"""词库业务：CRUD、章节、单词、导出（公式注入转义）、克隆、导入三步制。"""

import csv
import io
import json
import uuid
from datetime import datetime, timedelta

from fastapi import UploadFile
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.errors import AppError
from ..models import Chapter, ImportJob, Word, WordBook

MAX_ROWS = 20000
ASYNC_THRESHOLD = 1000


# ---------- 可见性 ----------

async def get_book_scoped(db: AsyncSession, user, book_id: int) -> WordBook:
    """本人私有 或 公共库；否则 404（越权伪装，§1.4-3）。admin 对公共库有管理权。"""
    book = (await db.execute(select(WordBook).where(WordBook.id == book_id, WordBook.is_deleted == 0))).scalar_one_or_none()
    if book is None:
        raise AppError("NOT_FOUND", "词库不存在")
    if book.owner_id is not None and book.owner_id != user.id and not book.is_public:
        raise AppError("NOT_FOUND", "词库不存在")
    return book


# ---------- books ----------

def _card(book: WordBook, chapter_count: int, word_count: int) -> dict:
    return {
        "id": book.id,
        "name": book.name,
        "chapter_count": chapter_count,
        "word_count": word_count,
        "is_public": bool(book.is_public),
        "owner_id": book.owner_id,
    }


async def list_books(db: AsyncSession, user, tab: str, q: str, page: int, page_size: int = 20) -> dict:
    stmt = select(WordBook).where(WordBook.is_deleted == 0)
    if tab == "mine":
        stmt = stmt.where(WordBook.owner_id == user.id)
    elif tab == "public":
        stmt = stmt.where(WordBook.is_public == 1)
    else:
        stmt = stmt.where((WordBook.owner_id == user.id) | (WordBook.is_public == 1))
    if q:
        stmt = stmt.where(WordBook.name.contains(q))
    stmt = stmt.order_by(WordBook.is_public.desc(), WordBook.id)
    rows = (await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()

    items = []
    for b in rows:
        cc = (
            await db.execute(
                select(func.count()).select_from(Chapter).where(Chapter.book_id == b.id, Chapter.is_deleted == 0)
            )
        ).scalar_one()
        wc = (
            await db.execute(
                select(func.count()).select_from(Word).where(Word.book_id == b.id, Word.is_deleted == 0)
            )
        ).scalar_one()
        items.append(_card(b, cc, wc))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def create_book(db: AsyncSession, user, name: str) -> dict:
    book = WordBook(name=name, owner_id=user.id, is_public=0)
    db.add(book)
    await db.flush()
    return {"id": book.id, "name": book.name}


async def patch_book(db: AsyncSession, user, book_id: int, name: str) -> None:
    book = await get_book_scoped(db, user, book_id)
    if book.owner_id != user.id and user.role != "admin":
        raise AppError("NOT_FOUND", "词库不存在")
    book.name = name


async def delete_book(db: AsyncSession, user, book_id: int) -> None:
    book = await get_book_scoped(db, user, book_id)
    if book.owner_id != user.id and user.role != "admin":
        raise AppError("NOT_FOUND", "词库不存在")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 级联软删（R3）
    await db.execute(update(Word).where(Word.book_id == book_id).values(is_deleted=1, deleted_at=now, deleted_by=user.id))
    await db.execute(update(Chapter).where(Chapter.book_id == book_id).values(is_deleted=1))
    book.is_deleted = 1
    book.deleted_at = now


async def clone_book(db: AsyncSession, user, book_id: int) -> dict:
    book = await get_book_scoped(db, user, book_id)
    if book.owner_id == user.id:
        raise AppError("NOT_FOUND", "仅可克隆公共词库")
    copy = WordBook(name=f"{book.name}-副本", owner_id=user.id, is_public=0)
    db.add(copy)
    await db.flush()
    chapters = (await db.execute(select(Chapter).where(Chapter.book_id == book.id, Chapter.is_deleted == 0).order_by(Chapter.sort_order))).scalars().all()
    words = (await db.execute(select(Word).where(Word.book_id == book.id, Word.is_deleted == 0).order_by(Word.id))).scalars().all()
    ch_map: dict[int, int] = {}
    for ch in chapters:
        nc = Chapter(book_id=copy.id, name=ch.name, sort_order=ch.sort_order)
        db.add(nc)
        await db.flush()
        ch_map[ch.id] = nc.id
    for w in words:
        db.add(Word(chapter_id=ch_map[w.chapter_id], book_id=copy.id, spelling=w.spelling, meaning=w.meaning, phonetic=w.phonetic, example=w.example))
    await db.flush()
    return {"book_id": copy.id}


# ---------- export / chapters / words ----------

def _csv_escape(v: str) -> str:
    if v and v[0] in ("=", "+", "-", "@"):
        return "'" + v
    return v


async def export_book_csv(db: AsyncSession, book_id: int) -> str:
    words = (
        await db.execute(select(Word).where(Word.book_id == book_id, Word.is_deleted == 0).order_by(Word.id))
    ).scalars().all()
    buf = io.StringIO()
    buf.write("\ufeff")  # UTF-8 BOM
    writer = csv.writer(buf)
    writer.writerow(["单词", "释义", "音标", "例句"])
    for w in words:
        writer.writerow([_csv_escape(w.spelling), _csv_escape(w.meaning), _csv_escape(w.phonetic or ""), _csv_escape(w.example or "")])
    return buf.getvalue()


async def list_chapters(db: AsyncSession, book_id: int) -> list[dict]:
    rows = (
        await db.execute(select(Chapter).where(Chapter.book_id == book_id, Chapter.is_deleted == 0).order_by(Chapter.sort_order, Chapter.id))
    ).scalars().all()
    out = []
    for ch in rows:
        wc = (await db.execute(select(func.count()).select_from(Word).where(Word.chapter_id == ch.id, Word.is_deleted == 0))).scalar_one()
        out.append({"id": ch.id, "book_id": ch.book_id, "name": ch.name, "sort_order": ch.sort_order, "word_count": wc})
    return out


async def create_chapter(db: AsyncSession, user, book_id: int, name: str, sort_order: int | None) -> dict:
    await get_book_scoped(db, user, book_id)
    ch = Chapter(book_id=book_id, name=name, sort_order=sort_order or 0)
    db.add(ch)
    await db.flush()
    return {"id": ch.id, "name": ch.name, "sort_order": ch.sort_order}


async def patch_chapter(db: AsyncSession, user, chapter_id: int, name: str | None, sort_order: int | None) -> None:
    ch = (await db.execute(select(Chapter).where(Chapter.id == chapter_id, Chapter.is_deleted == 0))).scalar_one_or_none()
    if ch is None:
        raise AppError("NOT_FOUND", "章节不存在")
    await get_book_scoped(db, user, ch.book_id)
    if name is not None:
        ch.name = name
    if sort_order is not None:
        ch.sort_order = sort_order


async def get_book_managed(db: AsyncSession, user, book_id: int) -> WordBook:
    """管理操作（删除章节/重新划分）仅 owner 或 admin；公开库对普通用户只读。"""
    book = await get_book_scoped(db, user, book_id)
    if book.owner_id != user.id and (user.role or "user") != "admin":
        raise AppError("FORBIDDEN", "公开词库不可修改，请先克隆为自己的词库")
    return book


async def delete_chapter(db: AsyncSession, user, chapter_id: int) -> None:
    ch = (await db.execute(select(Chapter).where(Chapter.id == chapter_id, Chapter.is_deleted == 0))).scalar_one_or_none()
    if ch is None:
        raise AppError("NOT_FOUND", "章节不存在")
    await get_book_managed(db, user, ch.book_id)
    ch.is_deleted = 1  # 仅删章节，单词保留（可重新划分单元）


async def delete_all_chapters(db: AsyncSession, user, book_id: int) -> dict:
    """一键清空词库全部章节。单词保留（仍属于词库），可通过"重新划分单元"再次分组。"""
    book = await get_book_managed(db, user, book_id)
    chapters = (await db.execute(select(Chapter).where(Chapter.book_id == book_id, Chapter.is_deleted == 0))).scalars().all()
    for ch in chapters:
        ch.is_deleted = 1
    kept_words = (
        await db.execute(select(func.count()).select_from(Word).where(Word.book_id == book_id, Word.is_deleted == 0))
    ).scalar_one()
    return {"deleted_chapters": len(chapters), "kept_words": kept_words}


async def resplit_chapters(db: AsyncSession, user, book_id: int, words_per_chapter: int | None = None, unit_count: int | None = None) -> dict:
    """重新划分单元：保留词，删除现有章节，随机打散重新分入 Unit 1..N。"""
    import random

    book = await get_book_managed(db, user, book_id)
    words = (await db.execute(select(Word).where(Word.book_id == book_id, Word.is_deleted == 0))).scalars().all()
    if not words:
        raise AppError("EMPTY_WORD_POOL", "词库没有可划分的单词")

    n = len(words)
    if unit_count:
        units = min(unit_count, n)
        base, extra = divmod(n, units)
        sizes = [base + (1 if u < extra else 0) for u in range(units)]
    else:
        cap = words_per_chapter or 30
        units = -(-n // cap)
        base, extra = divmod(n, units)
        sizes = [base + (1 if u < extra else 0) for u in range(units)]

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    old_chapters = (await db.execute(select(Chapter).where(Chapter.book_id == book_id, Chapter.is_deleted == 0))).scalars().all()
    for ch in old_chapters:
        ch.is_deleted = 1

    shuffled = list(words)
    random.shuffle(shuffled)
    pos = 0
    for u, size in enumerate(sizes):
        ch = Chapter(book_id=book_id, name=f"Unit {u + 1}", sort_order=u + 1)
        db.add(ch)
        await db.flush()
        for w in shuffled[pos : pos + size]:
            w.chapter_id = ch.id
        pos += size
    return {"book_id": book_id, "word_count": n, "chapter_count": len(sizes)}


async def list_words(db: AsyncSession, book_id: int, chapter_id: int | None, q: str | None, page: int, page_size: int = 50) -> dict:
    stmt = select(Word).where(Word.book_id == book_id, Word.is_deleted == 0)
    if chapter_id:
        stmt = stmt.where(Word.chapter_id == chapter_id)
    if q:
        stmt = stmt.where((Word.spelling.contains(q)) | (Word.meaning.contains(q)))
    stmt = stmt.order_by(Word.id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {
        "items": [
            {"id": w.id, "chapter_id": w.chapter_id, "spelling": w.spelling, "meaning": w.meaning, "phonetic": w.phonetic, "example": w.example}
            for w in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def word_duplicate(db: AsyncSession, book_id: int, spelling: str, exclude_id: int | None = None) -> Word | None:
    stmt = select(Word).where(Word.book_id == book_id, Word.is_deleted == 0, func.lower(Word.spelling) == spelling.strip().lower())
    if exclude_id:
        stmt = stmt.where(Word.id != exclude_id)
    return (await db.execute(stmt.limit(1))).scalar_one_or_none()


async def create_word(db: AsyncSession, user, payload) -> dict:
    ch = (await db.execute(select(Chapter).where(Chapter.id == payload.chapter_id, Chapter.is_deleted == 0))).scalar_one_or_none()
    if ch is None:
        raise AppError("NOT_FOUND", "章节不存在")
    await get_book_scoped(db, user, ch.book_id)
    if await word_duplicate(db, ch.book_id, payload.spelling):
        raise AppError("WORD_DUPLICATE", "库内已存在该单词（忽略大小写）")
    w = Word(chapter_id=ch.id, book_id=ch.book_id, spelling=payload.spelling.strip(), meaning=payload.meaning, phonetic=payload.phonetic, example=payload.example)
    db.add(w)
    await db.flush()
    return {"id": w.id}


async def patch_word(db: AsyncSession, user, word_id: int, payload) -> None:
    w = (await db.execute(select(Word).where(Word.id == word_id, Word.is_deleted == 0))).scalar_one_or_none()
    if w is None:
        raise AppError("NOT_FOUND", "单词不存在")
    await get_book_scoped(db, user, w.book_id)
    data = payload.model_dump(exclude_unset=True)
    if "spelling" in data and data["spelling"] and data["spelling"].strip().lower() != w.spelling.lower():
        if await word_duplicate(db, w.book_id, data["spelling"], exclude_id=w.id):
            raise AppError("WORD_DUPLICATE", "库内已存在该单词（忽略大小写）")
    for k, v in data.items():
        if v is not None:
            setattr(w, k, v.strip() if isinstance(v, str) and k == "spelling" else v)


async def delete_word(db: AsyncSession, user, word_id: int) -> None:
    w = (await db.execute(select(Word).where(Word.id == word_id, Word.is_deleted == 0))).scalar_one_or_none()
    if w is None:
        raise AppError("NOT_FOUND", "单词不存在")
    await get_book_scoped(db, user, w.book_id)
    w.is_deleted = 1
    w.deleted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    w.deleted_by = user.id


# ---------- 导入三步制 ----------

def _detect_decode(raw: bytes) -> str:
    import chardet

    enc = chardet.detect(raw[:65536]).get("encoding") or "utf-8"
    try:
        return raw.decode(enc)
    except (UnicodeDecodeError, LookupError):
        return raw.decode("utf-8", errors="replace")


def _parse_rows(filename: str, raw: bytes) -> list[dict]:
    """解析为 [{line, spelling, meaning, phonetic, example}]，首行表头跳过。"""
    rows: list[dict] = []
    if filename.lower().endswith(".xlsx"):
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True)
        ws = wb.active
        for i, row in enumerate(ws.iter_rows(values_only=True), start=1):
            vals = [str(v).strip() if v is not None else "" for v in (list(row) + ["", "", "", ""])[:4]]
            if i == 1 and (vals[0] in ("单词", "word", "spelling")):
                continue
            rows.append({"line": i, "spelling": vals[0], "meaning": vals[1], "phonetic": vals[2], "example": vals[3]})
    else:
        text_data = _detect_decode(raw)
        for i, line in enumerate(text_data.splitlines(), start=1):
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 1:
                parts = [p.strip() for p in line.split("\t")]
            parts = (parts + ["", "", "", ""])[:4]
            if i == 1 and parts[0] in ("单词", "word", "spelling"):
                continue
            rows.append({"line": i, "spelling": parts[0], "meaning": parts[1], "phonetic": parts[2], "example": parts[3]})
    return rows


def _analyze_rows(db: AsyncSession, book_id: int, rows: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """→ (ok, dup, errors)。ok 为本库新增候选；dup 为与库内现存重复。"""
    ok, dup, errors = [], [], []
    seen: set[str] = set()
    existing = {
        r.lower()
        for r in db.execute(
            select(func.lower(Word.spelling)).where(Word.book_id == book_id, Word.is_deleted == 0)
        ).scalars()
    }
    for r in rows:
        sp = (r.get("spelling") or "").strip()
        mn = (r.get("meaning") or "").strip()
        if not sp or not mn:
            errors.append({"line": r["line"], "reason": "单词或释义为空"})
            continue
        if len(sp) > 100 or len(mn) > 500:
            errors.append({"line": r["line"], "reason": "字段超长"})
            continue
        key = sp.lower()
        if key in seen:
            errors.append({"line": r["line"], "reason": "文件内重复"})
            continue
        if key in existing:
            dup.append({**r, "spelling": sp, "meaning": mn})
            continue
        seen.add(key)
        ok.append({**r, "spelling": sp, "meaning": mn})
    return ok, dup, errors


async def upload_import(
    db: AsyncSession, user, file: UploadFile, book_name: str | None, target_book_id: int | None
) -> dict:
    s = get_settings()
    raw = await file.read()
    if len(raw) > 10 * 1024 * 1024:
        raise AppError("FILE_TOO_LARGE", "文件超过 10MB")
    filename = file.filename or "upload.csv"
    if not filename.lower().endswith((".csv", ".txt", ".xlsx")):
        raise AppError("FILE_TYPE_FORBIDDEN", "仅支持 csv/txt/xlsx")

    if target_book_id:
        book = await get_book_scoped(db, user, target_book_id)
        if book.owner_id != user.id and user.role != "admin":
            raise AppError("NOT_FOUND", "词库不存在")
    else:
        book = WordBook(name=book_name or filename.rsplit(".", 1)[0], owner_id=user.id)
        db.add(book)
        await db.flush()
        db.add(Chapter(book_id=book.id, name="默认章节", sort_order=0))  # N6
        await db.flush()

    if len(raw) > 0:
        try:
            rows = _parse_rows(filename, raw)
        except Exception as e:  # noqa: BLE001
            raise AppError("FILE_TYPE_FORBIDDEN", f"文件解析失败: {e}") from None
    else:
        rows = []
    if len(rows) > MAX_ROWS:
        raise AppError("ROW_LIMIT_EXCEEDED", f"行数超过上限 {MAX_ROWS}")

    ok, dup, errors = _analyze_rows(db, book.id, rows)
    job = ImportJob(
        user_id=user.id,
        book_id=book.id,
        filename=filename,
        status="preview_ready",
        total_rows=len(rows),
        ok_rows=len(ok),
        dup_rows=len(dup),
        error_rows=len(errors),
        parsed_json=json.dumps({"ok": ok, "dup": dup, "errors": errors}, ensure_ascii=False)[: 8 * 1024 * 1024],
        preview_token=uuid.uuid4().hex,
        expires_at=(datetime.now() + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S"),
    )
    db.add(job)
    await db.flush()
    return {
        "job_id": job.id,
        "status": "preview_ready",
        "preview_token": job.preview_token,
        "expires_at": job.expires_at,
        "ok_rows": len(ok),
        "dup_rows": len(dup),
        "error_rows": len(errors),
        "book_id": book.id,
    }


async def get_import_job(db: AsyncSession, user, job_id: int) -> dict:
    job = (await db.execute(select(ImportJob).where(ImportJob.id == job_id, ImportJob.user_id == user.id))).scalar_one_or_none()
    if job is None:
        raise AppError("NOT_FOUND", "导入任务不存在")
    return {
        "job_id": job.id,
        "status": job.status,
        "ok_rows": job.ok_rows,
        "dup_rows": job.dup_rows,
        "error_rows": job.error_rows,
        "preview_token": job.preview_token,
        "expires_at": job.expires_at,
    }


async def import_preview(db: AsyncSession, user, token: str) -> dict:
    job = (await db.execute(select(ImportJob).where(ImportJob.preview_token == token, ImportJob.user_id == user.id))).scalar_one_or_none()
    if job is None:
        raise AppError("NOT_FOUND", "预览不存在")
    if job.expires_at < datetime.now().strftime("%Y-%m-%d %H:%M:%S"):
        raise AppError("PREVIEW_EXPIRED", "预览已过期")
    parsed = json.loads(job.parsed_json or "{}")
    return {
        "job_id": job.id,
        "book_id": job.book_id,
        "ok_rows": job.ok_rows,
        "dup_rows": job.dup_rows,
        "error_rows": job.error_rows,
        "sample_ok": parsed.get("ok", [])[:20],
        "dup_words": parsed.get("dup", []),
        "errors": parsed.get("errors", []),
    }


async def import_confirm(db: AsyncSession, user, token: str, duplicate_strategy: str, words_per_chapter: int | None = None, unit_count: int | None = None) -> dict:
    job = (await db.execute(select(ImportJob).where(ImportJob.preview_token == token, ImportJob.user_id == user.id))).scalar_one_or_none()
    if job is None:
        raise AppError("NOT_FOUND", "预览不存在")
    if job.status == "confirmed":
        raise AppError("SESSION_ALREADY_SETTLED", "该批次已确认入库")
    if job.expires_at < datetime.now().strftime("%Y-%m-%d %H:%M:%S"):
        raise AppError("PREVIEW_EXPIRED", "预览已过期")

    parsed = json.loads(job.parsed_json or "{}")
    ok_rows = parsed.get("ok", [])
    inserted = 0
    chapter_count = 0

    if unit_count:
        # 自选单元数量：随机打散后尽量均分成 N 个单元
        import random

        shuffled = list(ok_rows)
        random.shuffle(shuffled)
        n = len(shuffled)
        units = min(unit_count, n)
        base, extra = divmod(n, units)
        pos = 0
        for u in range(units):
            size = base + (1 if u < extra else 0)
            part = shuffled[pos : pos + size]
            pos += size
            ch = Chapter(book_id=job.book_id, name=f"Unit {u + 1}", sort_order=u + 1)
            db.add(ch)
            await db.flush()
            chapter_count += 1
            for r in part:
                db.add(Word(chapter_id=ch.id, book_id=job.book_id, spelling=r["spelling"], meaning=r["meaning"], phonetic=r.get("phonetic") or None, example=r.get("example") or None))
                inserted += 1
    elif words_per_chapter:
        # 自选单元词数：随机打散后按固定容量切分单元
        import random

        shuffled = list(ok_rows)
        random.shuffle(shuffled)
        for idx in range(0, len(shuffled), words_per_chapter):
            part = shuffled[idx : idx + words_per_chapter]
            ch = Chapter(
                book_id=job.book_id,
                name=f"Unit {idx // words_per_chapter + 1}",
                sort_order=idx // words_per_chapter + 1,
            )
            db.add(ch)
            await db.flush()
            chapter_count += 1
            for r in part:
                db.add(Word(chapter_id=ch.id, book_id=job.book_id, spelling=r["spelling"], meaning=r["meaning"], phonetic=r.get("phonetic") or None, example=r.get("example") or None))
                inserted += 1
    else:
        # 默认章节（N6）
        ch = (
            await db.execute(select(Chapter).where(Chapter.book_id == job.book_id, Chapter.is_deleted == 0).order_by(Chapter.sort_order, Chapter.id).limit(1))
        ).scalar_one()
        for r in ok_rows:
            db.add(Word(chapter_id=ch.id, book_id=job.book_id, spelling=r["spelling"], meaning=r["meaning"], phonetic=r.get("phonetic") or None, example=r.get("example") or None))
            inserted += 1

    overwritten = 0
    if duplicate_strategy == "overwrite":
        for r in parsed.get("dup", []):
            await db.execute(
                update(Word)
                .where(
                    Word.book_id == job.book_id,
                    Word.is_deleted == 0,
                    func.lower(Word.spelling) == r["spelling"].strip().lower(),
                )
                .values(meaning=r["meaning"], phonetic=r.get("phonetic") or None, example=r.get("example") or None)  # 口径17
            )
            overwritten += 1

    job.status = "confirmed"
    job.duplicate_strategy = duplicate_strategy
    await db.flush()
    return {
        "book_id": job.book_id,
        "ok_rows": inserted,
        "chapter_count": chapter_count,
        "dup_rows": job.dup_rows or 0,
        "overwritten": overwritten,
        "error_rows": job.error_rows or 0,
    }
