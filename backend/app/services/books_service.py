"""词库业务：CRUD、章节、单词、导出（公式注入转义）、克隆、导入三步制。"""

import asyncio
import csv
import io
import json
import re
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
    """本人私有 或 公共库；否则 404（越权伪装，§1.4-3）。admin 对公共库有管理权；
    admin 额外放行他人私有库（清理注销用户遗留库、删除违规内容），普通用户视角不变。"""
    book = (await db.execute(select(WordBook).where(WordBook.id == book_id, WordBook.is_deleted == 0))).scalar_one_or_none()
    if book is None:
        raise AppError("NOT_FOUND", "词库不存在")
    if book.owner_id is not None and book.owner_id != user.id and not book.is_public and user.role != "admin":
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

    # 批量统计，避免每个词库各查两次（N+1）
    ids = [b.id for b in rows]
    cc_map: dict[int, int] = {}
    wc_map: dict[int, int] = {}
    if ids:
        cc_map = dict(
            (
                await db.execute(
                    select(Chapter.book_id, func.count())
                    .where(Chapter.is_deleted == 0, Chapter.book_id.in_(ids))
                    .group_by(Chapter.book_id)
                )
            ).all()
        )
        wc_map = dict(
            (
                await db.execute(
                    select(Word.book_id, func.count())
                    .where(Word.is_deleted == 0, Word.book_id.in_(ids))
                    .group_by(Word.book_id)
                )
            ).all()
        )
    items = [_card(b, cc_map.get(b.id, 0), wc_map.get(b.id, 0)) for b in rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def create_book(db: AsyncSession, user, name: str) -> dict:
    book = WordBook(name=name, owner_id=user.id, is_public=0)
    db.add(book)
    await db.flush()
    return {"id": book.id, "name": book.name}


async def patch_book(db: AsyncSession, user, book_id: int, name: str | None = None, is_public: bool | None = None) -> None:
    book = await get_book_managed(db, user, book_id)
    if book.owner_id != user.id and user.role != "admin":
        raise AppError("NOT_FOUND", "词库不存在")
    if name is not None:
        book.name = name
    if is_public is not None:
        if user.role != "admin":
            raise AppError("FORBIDDEN", "仅管理员可设置词库公开/私有")
        book.is_public = 1 if is_public else 0


async def delete_book(db: AsyncSession, user, book_id: int) -> None:
    book = await get_book_managed(db, user, book_id)
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
        # 全字段克隆（含 time_limit_s 自定义限时；2026-09-20 补：此前漏带，克隆副本与原库考核/听写限时口径不一致）
        db.add(Word(chapter_id=ch_map[w.chapter_id], book_id=copy.id, spelling=w.spelling, meaning=w.meaning, phonetic=w.phonetic, example=w.example, time_limit_s=w.time_limit_s))
    await db.flush()
    return {"book_id": copy.id}


# ---------- export / chapters / words ----------

def _csv_escape(v: str) -> str:
    if v and v[0] in ("=", "+", "-", "@"):
        return "'" + v
    return v


async def export_book_csv(db: AsyncSession, book_id: int) -> str:
    # 导出带上「章节 / 时限秒」：导出的 CSV 可直接再导入，单元结构与词序原样还原
    rows = (
        await db.execute(
            select(Chapter.name, Word)
            .outerjoin(Chapter, Chapter.id == Word.chapter_id)
            .where(Word.book_id == book_id, Word.is_deleted == 0)
            .order_by(Chapter.sort_order, Chapter.id, Word.id)
        )
    ).all()
    buf = io.StringIO()
    buf.write("\ufeff")  # UTF-8 BOM
    writer = csv.writer(buf)
    writer.writerow(["章节", "单词", "释义", "音标", "例句", "时限秒"])
    for ch_name, w in rows:
        writer.writerow([
            _csv_escape(ch_name or ""),
            _csv_escape(w.spelling),
            _csv_escape(w.meaning),
            _csv_escape(w.phonetic or ""),
            _csv_escape(w.example or ""),
            w.time_limit_s or "",
        ])
    return buf.getvalue()


async def list_chapters(db: AsyncSession, book_id: int) -> list[dict]:
    rows = (
        await db.execute(select(Chapter).where(Chapter.book_id == book_id, Chapter.is_deleted == 0).order_by(Chapter.sort_order, Chapter.id))
    ).scalars().all()
    # 批量统计各章节词数，避免逐章节查询（N+1）
    ids = [ch.id for ch in rows]
    wc_map: dict[int, int] = {}
    if ids:
        wc_map = dict(
            (
                await db.execute(
                    select(Word.chapter_id, func.count())
                    .where(Word.is_deleted == 0, Word.chapter_id.in_(ids))
                    .group_by(Word.chapter_id)
                )
            ).all()
        )
    out = [
        {"id": ch.id, "book_id": ch.book_id, "name": ch.name, "sort_order": ch.sort_order, "word_count": wc_map.get(ch.id, 0)}
        for ch in rows
    ]
    return out


async def create_chapter(db: AsyncSession, user, book_id: int, name: str, sort_order: int | None) -> dict:
    await get_book_managed(db, user, book_id)
    ch = Chapter(book_id=book_id, name=name, sort_order=sort_order or 0)
    db.add(ch)
    await db.flush()
    return {"id": ch.id, "name": ch.name, "sort_order": ch.sort_order}


async def patch_chapter(db: AsyncSession, user, chapter_id: int, name: str | None, sort_order: int | None) -> None:
    ch = (await db.execute(select(Chapter).where(Chapter.id == chapter_id, Chapter.is_deleted == 0))).scalar_one_or_none()
    if ch is None:
        raise AppError("NOT_FOUND", "章节不存在")
    await get_book_managed(db, user, ch.book_id)
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
        # 按单元数量：尽量均分
        units = max(1, min(unit_count, n))
        base, extra = divmod(n, units)
        sizes = [base + (1 if u < extra else 0) for u in range(units)]
    else:
        # 按每单元词数：每单元装满 cap 个，余数单独成单元
        cap = words_per_chapter or 30
        full, rest = divmod(n, cap)
        sizes = [cap] * full + ([rest] if rest else [])
    if not sizes:
        raise AppError("EMPTY_WORD_POOL", "词库没有可划分的单词")

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
            {"id": w.id, "chapter_id": w.chapter_id, "spelling": w.spelling, "meaning": w.meaning, "phonetic": w.phonetic, "example": w.example, "time_limit_s": w.time_limit_s}
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
    await get_book_managed(db, user, ch.book_id)
    if await word_duplicate(db, ch.book_id, payload.spelling):
        raise AppError("WORD_DUPLICATE", "库内已存在该单词（忽略大小写）")
    w = Word(chapter_id=ch.id, book_id=ch.book_id, spelling=payload.spelling.strip(), meaning=payload.meaning, phonetic=payload.phonetic, example=payload.example, time_limit_s=payload.time_limit_s)
    db.add(w)
    await db.flush()
    return {"id": w.id}


async def patch_word(db: AsyncSession, user, word_id: int, payload) -> None:
    w = (await db.execute(select(Word).where(Word.id == word_id, Word.is_deleted == 0))).scalar_one_or_none()
    if w is None:
        raise AppError("NOT_FOUND", "单词不存在")
    await get_book_managed(db, user, w.book_id)
    data = payload.model_dump(exclude_unset=True)
    if "spelling" in data and data["spelling"] and data["spelling"].strip().lower() != w.spelling.lower():
        if await word_duplicate(db, w.book_id, data["spelling"], exclude_id=w.id):
            raise AppError("WORD_DUPLICATE", "库内已存在该单词（忽略大小写）")
    for k, v in data.items():
        if v is not None:
            setattr(w, k, v.strip() if isinstance(v, str) and k == "spelling" else v)
    if "time_limit_s" in data and data["time_limit_s"] is None:
        w.time_limit_s = None  # 显式传 null = 恢复自动公式


async def delete_word(db: AsyncSession, user, word_id: int) -> None:
    w = (await db.execute(select(Word).where(Word.id == word_id, Word.is_deleted == 0))).scalar_one_or_none()
    if w is None:
        raise AppError("NOT_FOUND", "单词不存在")
    await get_book_managed(db, user, w.book_id)
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


# ---------- 表头别名（支持中文/英文，随意列顺序） ----------
_HDR_UNIT = {"章节", "单元", "单元名", "组", "组别", "unit", "chapter", "group"}
_HDR_SPELL = {"单词", "拼写", "英文", "词条", "word", "spelling", "term"}
_HDR_MEAN = {"释义", "翻译", "中文", "意思", "meaning", "translation", "definition"}
_HDR_PHON = {"音标", "读音", "phonetic", "ipa", "pronunciation"}
_HDR_EX = {"例句", "例子", "example", "sentence"}
_HDR_TIME = {"时限秒", "时限", "限时", "时长", "答题时间", "秒", "秒数", "time_limit", "time_limit_s", "limit_s", "duration", "seconds"}


def _norm_header(s: str) -> str:
    s = (s or "").strip().lower()
    for ch in (" ", "\t", "（", "）", "(", ")", "：", ":", "《", "》", "[", "]"):
        s = s.replace(ch, "")
    return s


def _map_header(cells: list[str]) -> dict[str, int] | None:
    """把表头行映射成 {字段: 列索引}；不是表头（认不出单词列）时返回 None。"""
    m: dict[str, int] = {}
    for i, c in enumerate(cells):
        k = _norm_header(c)
        if not k:
            continue
        for field, aliases in (
            ("unit", _HDR_UNIT), ("spelling", _HDR_SPELL), ("meaning", _HDR_MEAN),
            ("phonetic", _HDR_PHON), ("example", _HDR_EX), ("time_limit_s", _HDR_TIME),
        ):
            if k in aliases and field not in m:
                m[field] = i
                break
    return m if "spelling" in m else None


def _parse_time_s(raw: str) -> int | None:
    """'8' / '8秒' / '8s' → 8；无效或留空 → None（自动）。"""
    import re

    s = (raw or "").strip()
    if not s:
        return None
    m = re.search(r"\d+", s)
    if not m:
        return None
    v = int(m.group())
    return v if 3 <= v <= 600 else None


_TIME_STRICT_RE = re.compile(r"^(\d{1,3})\s*(?:秒|s|S)?$")

_FIELD_CN = {"unit": "章节", "spelling": "单词", "meaning": "释义", "phonetic": "音标", "example": "例句", "time_limit_s": "时限秒"}


def _time_cell_s(raw: str) -> int | None:
    """严格口径：单元格剥掉首尾空白后是纯数字（可带 秒/s 后缀）才认定为时限。"""
    m = _TIME_STRICT_RE.match((raw or "").strip())
    if not m:
        return None
    v = int(m.group(1))
    return v if 3 <= v <= 600 else None


def _repair_valid(merged: list[str], colmap: dict[str, int]) -> bool:
    """修复结果校验：音标列不含中文/空格、单词释义非空、时限列能严格解出数字。"""
    p_idx = colmap.get("phonetic")
    if p_idx is not None and (_has_cjk(merged[p_idx]) or " " in merged[p_idx]):
        return False
    s_idx = colmap.get("spelling")
    if s_idx is not None and not merged[s_idx].strip():
        return False
    m_idx = colmap.get("meaning")
    if m_idx is not None and not merged[m_idx].strip():
        return False
    t_idx = colmap.get("time_limit_s")
    if t_idx is not None and _time_cell_s(merged[t_idx]) is None:
        return False
    return True


def _try_repair_row(cells: list[str], colmap: dict[str, int]) -> tuple[list[str], str] | None:
    """某字段里的英文逗号没加引号（如例句 `示例：gs.fit(X, y)`）会把该行切成比表头更多列，
    后面的「时限秒」整体右移读不到 → 导入成自动。这里把多出的单元格并回最合适的字段。

    一行里多个字段都有未加引号逗号（如释义一个、例句两个）也能修：枚举全部分配方案，
    按（改动字段数少 → 靠右字段优先）排序，第一个通过类型校验的即为解；修不了返回 None。
    """
    h = max(colmap.values()) + 1
    n = len(cells)
    if n <= h:
        return None
    extra = n - h
    if extra > 8:
        return None
    t_idx = colmap.get("time_limit_s")
    if t_idx is None:
        return None  # 没有时限列就没有可靠信号，不修
    if t_idx == h - 1 and _time_cell_s(cells[t_idx] if t_idx < n else "") is not None:
        return None  # 时限在末列且已正确读出 → 行没坏

    if t_idx == h - 1 and len(colmap) == h:
        # 常规布局（时限在末列、表头各列均可识别）：多字段分配
        cand = [(f, i) for f, i in sorted(colmap.items(), key=lambda kv: kv[1]) if f != "time_limit_s"]
        plans: list[tuple] = []

        def gen(k: int, rem: int, cur: list[int]) -> None:
            if k == len(cand):
                if rem == 0:
                    plans.append(tuple(cur))
                return
            for m in range(rem, -1, -1):
                cur.append(m)
                gen(k + 1, rem - m, cur)
                cur.pop()

        gen(0, extra, [])
        plans.sort(key=lambda t: (sum(1 for m in t if m), tuple(-m for m in reversed(t))))
        for tpl in plans:
            merged: list[str] = []
            shift = 0
            ok = True
            for (f, idx), m in zip(cand, tpl):
                start = idx + shift
                end = start + 1 + m
                if end > n:
                    ok = False
                    break
                merged.append(",".join(cells[start:end]))
                shift += m
            if not ok:
                continue
            tstart = t_idx + shift
            merged.append(cells[tstart] if tstart < n else "")
            if len(merged) == h and _repair_valid(merged, colmap):
                touched = [_FIELD_CN.get(f, f) for (f, _), m in zip(cand, tpl) if m]
                return merged, "、".join(touched)
        return None

    # 非典型布局（时限不在末列等）：退回单字段吸收
    for field, idx in sorted(colmap.items(), key=lambda kv: -kv[1]):
        if field == "time_limit_s":
            continue
        merged = cells[:idx] + [",".join(cells[idx: idx + extra + 1])] + cells[idx + extra + 1:]
        if len(merged) == h and _repair_valid(merged, colmap):
            return merged, _FIELD_CN.get(field, field)
    return None


def _try_pad_short_row(cells: list[str], colmap: dict[str, int]) -> list[str] | None:
    """行比表头少列（手写时漏了空列，如 `英语单词,cat,猫,5`），且末列是干净的数字：
    按时限列右对齐补齐空列，而不是把 '5' 塞进音标/例句。不满足条件返回 None。"""
    h = max(colmap.values()) + 1
    n = len(cells)
    t_idx = colmap.get("time_limit_s")
    if n >= h or t_idx != h - 1 or n < 2:
        return None
    if _time_cell_s(cells[-1]) is None:
        return None
    merged = cells[: n - 1] + [""] * (h - n) + [cells[-1]]
    p_idx = colmap.get("phonetic")
    if p_idx is not None and _has_cjk(merged[p_idx]):
        return None
    s_idx = colmap.get("spelling")
    if s_idx is not None and not merged[s_idx].strip():
        return None
    return merged


def _has_cjk(v: str | None) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", v or ""))


def _parse_warnings(ok: list[dict], header: bool, repairs: list | None = None) -> list[str]:
    """解析体检：只提示不改数据。无表头是最常见的错位源头（单元名被当成单词）。"""
    if not ok:
        return []
    ws: list[str] = []
    if not header:
        ws.append(
            "未在首行识别到表头，已按「拼写,释义,音标,例句」顺序读取。若首列其实是单元名，请补上表头行再重传，否则单元名会被当成单词。"
        )
        # 强错位信号：释义位置是纯英文，而音标/例句位置出现中文（音标不会是中文）
        if all(not _has_cjk(r.get("meaning")) for r in ok) and any(
            _has_cjk(r.get("phonetic")) or _has_cjk(r.get("example")) for r in ok
        ):
            ws.append(
                "疑似列错位：释义列是英文、音标/例句列却是中文——很可能是「单元,单词,释义」三列但没有表头行。请在首行加上：章节,单词,释义"
            )
    if repairs:
        extra = [(ln, f) for ln, k, f in repairs if k == "extra"]
        short = [ln for ln, k, _ in repairs if k == "short"]
        if extra:
            lines_txt = "、".join(f"第 {ln} 行" for ln, _ in extra[:5]) + (" 等行" if len(extra) > 5 else "")
            flds = "、".join(sorted({f for _, f in extra}))
            ws.append(
                f"{lines_txt}的「{flds}」列里有未加引号的英文逗号，已自动按列位置还原；"
                '建议给含逗号的字段加上英文双引号（如 "gs.fit(X, y)"），否则其它软件可能解析错位。'
            )
        if short:
            lines_txt = "、".join(f"第 {ln} 行" for ln in short[:5]) + (" 等行" if len(short) > 5 else "")
            ws.append(f"{lines_txt}比表头少列且末列是数字，已按时限列对齐补齐空列。")
    return ws


def _blank_row(line: int) -> dict:
    return {"line": line, "unit": "", "spelling": "", "meaning": "", "phonetic": "", "example": "", "time_limit_s": None}


def _parse_rows(filename: str, raw: bytes) -> tuple[list[dict], bool]:
    """解析为 (rows, header_detected)。row = {line, unit, spelling, meaning, phonetic, example, time_limit_s}。

    支持：csv / txt / xlsx；中英文表头（含「章节」「时限秒」两列，列顺序随意）；
    无表头时退回位置解析（拼写,释义,音标,例句）；带逗号的引号字段按 CSV 规则处理。
    """
    rows: list[dict] = []
    colmap: dict[str, int] | None = None
    repairs: list[tuple[int, str, str]] = []  # (行号, extra=多列/short=少列, 字段)

    def build(i: int, cells: list[str], cm: dict[str, int] | None) -> dict:
        r = _blank_row(i)
        if cm:
            for field, idx in cm.items():
                raw = cells[idx] if idx < len(cells) else ""
                if field == "time_limit_s":
                    r["time_limit_s"] = _parse_time_s(raw)
                else:
                    r[field] = (raw or "").strip()
        else:
            vals = [c.strip() for c in (list(cells) + ["", "", "", ""])[:4]]
            r["spelling"], r["meaning"], r["phonetic"], r["example"] = vals
        return r

    if filename.lower().endswith(".xlsx"):
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True)
        ws = wb.active
        for i, row in enumerate(ws.iter_rows(values_only=True), start=1):
            cells = [str(v).strip() if v is not None else "" for v in row]
            if not any(cells):
                continue
            if i == 1:
                cm0 = _map_header(cells)
                if cm0:
                    colmap = cm0
                    continue
            rows.append(build(i, cells, colmap))
    else:
        text_data = _detect_decode(raw)
        if '"' in text_data or "," in text_data:
            # 整体交给 csv 模块：支持引号字段里内嵌换行/逗号（逐行 read 会把这种字段截断）
            parsed_rows = [list(r) for r in csv.reader(io.StringIO(text_data))]
        else:
            # 无逗号：按 tab / 分号 / 整行兜底
            parsed_rows = []
            for line in text_data.splitlines():
                if not line.strip():
                    continue
                if "\t" in line:
                    parsed_rows.append([c.strip() for c in line.split("\t")])
                elif ";" in line or "；" in line:
                    parsed_rows.append([p.strip() for p in re.split(r"[;；]", line)])
                else:
                    parsed_rows.append([line.strip()])
        for i, cells in enumerate(parsed_rows, start=1):
            if not any(c.strip() for c in cells):
                continue
            if i == 1:
                cm0 = _map_header(cells)
                if cm0:
                    colmap = cm0
                    continue
            if colmap:
                h_max = max(colmap.values()) + 1
                if len(cells) > h_max:
                    fixed = _try_repair_row(cells, colmap)
                    if fixed:
                        cells, fld = fixed
                        repairs.append((i, "extra", fld))
                elif len(cells) < h_max:
                    padded = _try_pad_short_row(cells, colmap)
                    if padded is not None:
                        cells = padded
                        repairs.append((i, "short", "时限"))
            rows.append(build(i, cells, colmap))
    return rows, colmap is not None, repairs


async def _analyze_rows(db: AsyncSession, book_id: int, rows: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """→ (ok, dup, errors)。ok 为本库新增候选；dup 为与库内现存重复。"""
    ok, dup, errors = [], [], []
    seen: set[str] = set()
    existing = {
        r.lower()
        for r in (
            await db.execute(
                select(func.lower(Word.spelling)).where(Word.book_id == book_id, Word.is_deleted == 0)
            )
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
        book = await get_book_managed(db, user, target_book_id)
    else:
        book = WordBook(name=book_name or filename.rsplit(".", 1)[0], owner_id=user.id, is_public=0)
        db.add(book)
        await db.flush()
        # 章节延后到确认入库时按所选单元策略创建，避免留下无单词的空壳章节

    if len(raw) > 0:
        try:
            rows, header_detected, repairs = _parse_rows(filename, raw)
        except Exception as e:  # noqa: BLE001
            raise AppError("FILE_TYPE_FORBIDDEN", f"文件解析失败: {e}") from None
    else:
        rows = []
        header_detected = False
        repairs: list = []
    if len(rows) > MAX_ROWS:
        raise AppError("ROW_LIMIT_EXCEEDED", f"行数超过上限 {MAX_ROWS}")

    ok, dup, errors = await _analyze_rows(db, book.id, rows)
    job = ImportJob(
        user_id=user.id,
        book_id=book.id,
        filename=filename,
        status="preview_ready",
        total_rows=len(rows),
        ok_rows=len(ok),
        dup_rows=len(dup),
        error_rows=len(errors),
        parsed_json=json.dumps(
            {"ok": ok, "dup": dup, "errors": errors, "meta": {"header": header_detected, "repairs": repairs}},
            ensure_ascii=False,
        )[: 8 * 1024 * 1024],
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
        "warnings": _parse_warnings(ok, header_detected, repairs),
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
    ok = parsed.get("ok", [])
    # 文件自带的单元结构 preview（按首次出现顺序，含词数）
    unit_preview: list[dict] = []
    for r in ok:
        u = (r.get("unit") or "").strip()
        if not unit_preview or unit_preview[-1]["name"] != u:
            unit_preview.append({"name": u or "默认章节", "count": 1})
        else:
            unit_preview[-1]["count"] += 1
    return {
        "job_id": job.id,
        "book_id": job.book_id,
        "preview_token": job.preview_token,
        "ok_rows": job.ok_rows,
        "dup_rows": job.dup_rows,
        "error_rows": job.error_rows,
        "sample_ok": ok[:20],
        "units": unit_preview,
        "has_unit_column": any((r.get("unit") or "").strip() for r in ok),
        "has_time_column": any(r.get("time_limit_s") for r in ok),
        "dup_words": parsed.get("dup", []),
        "errors": parsed.get("errors", []),
        "warnings": _parse_warnings(ok, (parsed.get("meta") or {}).get("header", True), (parsed.get("meta") or {}).get("repairs") or []),
    }


_IMPORT_SEM = asyncio.Semaphore(1)  # SQLite 单写者：多批导入串行化，避免 database is locked


async def import_confirm(db: AsyncSession, user, token: str, duplicate_strategy: str, words_per_chapter: int | None = None, unit_count: int | None = None, unit_mode: str | None = None) -> dict:
    """外层串行化：并发导入排队执行，而不是互相抢占写锁后报错。"""
    async with _IMPORT_SEM:
        return await _import_confirm_inner(db, user, token, duplicate_strategy, words_per_chapter, unit_count, unit_mode)


async def _import_confirm_inner(db: AsyncSession, user, token: str, duplicate_strategy: str, words_per_chapter: int | None = None, unit_count: int | None = None, unit_mode: str | None = None) -> dict:
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
    ch_by_name: dict[str, Chapter] = {}

    # 单元策略优先级：显式 unit_mode > 每单元词数 > 单元数量 > 文件自带单元列 > 默认单章节
    has_unit_col = any((r.get("unit") or "").strip() for r in ok_rows)
    if unit_mode in ("file", "size", "count", "none"):
        mode = unit_mode
    elif words_per_chapter:
        mode = "size"
    elif unit_count:
        mode = "count"
    elif has_unit_col:
        mode = "file"
    else:
        mode = "none"

    if mode == "file":
        # 按文件里的「章节」列分组：单元结构、单词归属与顺序完全照搬文件，不打散
        existing = (await db.execute(select(Chapter).where(Chapter.book_id == job.book_id, Chapter.is_deleted == 0))).scalars().all()
        ch_by_name = {c.name: c for c in existing}
        sort_no = max([c.sort_order or 0 for c in existing] + [0])
        order: list[str] = []
        for r in ok_rows:
            u = (r.get("unit") or "").strip() or "默认章节"
            if u not in order:
                order.append(u)
        for name in order:
            ch = ch_by_name.get(name)
            if ch is None:
                sort_no += 1
                ch = Chapter(book_id=job.book_id, name=name, sort_order=sort_no)
                db.add(ch)
                await db.flush()
                ch_by_name[name] = ch
                chapter_count += 1
        for r in ok_rows:
            u = (r.get("unit") or "").strip() or "默认章节"
            ch = ch_by_name[u]
            db.add(Word(chapter_id=ch.id, book_id=job.book_id, spelling=r["spelling"], meaning=r["meaning"], phonetic=r.get("phonetic") or None, example=r.get("example") or None, time_limit_s=r.get("time_limit_s")))
            inserted += 1
            if inserted % 500 == 0:
                await db.commit()  # 分批提交，缩短事务持锁时间
    elif mode == "count":
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
                db.add(Word(chapter_id=ch.id, book_id=job.book_id, spelling=r["spelling"], meaning=r["meaning"], phonetic=r.get("phonetic") or None, example=r.get("example") or None, time_limit_s=r.get("time_limit_s")))
                inserted += 1
                if inserted % 500 == 0:
                    await db.commit()  # 分批提交，缩短事务持锁时间
    elif mode == "size":
        # 自选单元词数：随机打散后按固定容量切分单元
        import random

        shuffled = list(ok_rows)
        random.shuffle(shuffled)
        for idx in range(0, len(shuffled), words_per_chapter or 20):
            part = shuffled[idx : idx + (words_per_chapter or 20)]
            ch = Chapter(
                book_id=job.book_id,
                name=f"Unit {idx // (words_per_chapter or 20) + 1}",
                sort_order=idx // (words_per_chapter or 20) + 1,
            )
            db.add(ch)
            await db.flush()
            chapter_count += 1
            for r in part:
                db.add(Word(chapter_id=ch.id, book_id=job.book_id, spelling=r["spelling"], meaning=r["meaning"], phonetic=r.get("phonetic") or None, example=r.get("example") or None, time_limit_s=r.get("time_limit_s")))
                inserted += 1
                if inserted % 500 == 0:
                    await db.commit()  # 分批提交，缩短事务持锁时间
    else:
        # 默认章节（N6）；已有词库可能没有任何章节，缺失时自动补建
        ch = (
            await db.execute(select(Chapter).where(Chapter.book_id == job.book_id, Chapter.is_deleted == 0).order_by(Chapter.sort_order, Chapter.id).limit(1))
        ).scalar_one_or_none()
        if ch is None:
            ch = Chapter(book_id=job.book_id, name="默认章节", sort_order=0)
            db.add(ch)
            await db.flush()
        for r in ok_rows:
            db.add(Word(chapter_id=ch.id, book_id=job.book_id, spelling=r["spelling"], meaning=r["meaning"], phonetic=r.get("phonetic") or None, example=r.get("example") or None, time_limit_s=r.get("time_limit_s")))
            inserted += 1
            if inserted % 500 == 0:
                await db.commit()  # 分批提交，缩短事务持锁时间

    overwritten = 0
    if duplicate_strategy == "overwrite":
        for r in parsed.get("dup", []):
            values = {
                "meaning": r["meaning"],
                "phonetic": r.get("phonetic") or None,
                "example": r.get("example") or None,
            }  # 口径17
            if r.get("time_limit_s"):
                values["time_limit_s"] = r["time_limit_s"]
            # 已有单词按文件的单元列重新归属（若该文件带单元列且对应章节已存在）
            unit_name = (r.get("unit") or "").strip()
            if unit_name and mode == "file":
                target = ch_by_name.get(unit_name)
                if target is not None:
                    values["chapter_id"] = target.id
            await db.execute(
                update(Word)
                .where(
                    Word.book_id == job.book_id,
                    Word.is_deleted == 0,
                    func.lower(Word.spelling) == r["spelling"].strip().lower(),
                )
                .values(**values)
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
        "unit_mode": mode,
    }
