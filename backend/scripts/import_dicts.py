"""从 kajweb/dict 词库 zip 导入真实词书：随机打散 → 按 30 词/单元建 Unit 章节。"""
import json
import random
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio

from sqlalchemy import select

from app.db.engine import get_sessionmaker
from app.models import User, WordBook
from app.services.books_service import create_book

DICTS_DIR = Path(__file__).resolve().parents[1] / "data" / "dicts"
UNIT_SIZE = 30  # 每单元词数

BOOKS = {
    "1521164649209_CET4_1.zip": "CET-4 核心词汇",
    "1521164633851_CET6_3.zip": "CET-6 核心词汇",
    "1521164654696_KaoYan_2.zip": "考研核心词汇",
}


def parse_zip(path: Path) -> list[dict]:
    rows: list[dict] = []
    with zipfile.ZipFile(path) as zf:
        name = [n for n in zf.namelist() if n.endswith(".json")][0]
        raw = zf.read(name).decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
    except json.JSONDecodeError:
        data = [json.loads(line) for line in raw.splitlines() if line.strip()]
    for item in data:
        head = (item.get("headWord") or "").strip()
        content = (item.get("content") or {}).get("word", {}).get("content", {}) or {}
        trans = content.get("trans") or []
        meaning = "；".join(
            f"{t.get('pos', '')}. {t.get('tranCn', '')}".strip(". ").strip()
            for t in trans
            if t.get("tranCn")
        ).strip("； ") or (trans[0].get("tranCn", "") if trans else "")
        if not head or not meaning:
            continue
        phonetic = content.get("usphone") or content.get("ukphone") or ""
        sentences = (content.get("sentence") or {}).get("sentences") or []
        example = ""
        if sentences:
            s0 = sentences[0]
            example = s0.get("sContent", "")
            if s0.get("sCn"):
                example += f" {s0['sCn']}"
        rows.append({"line": len(rows) + 1, "spelling": head, "meaning": meaning, "phonetic": phonetic, "example": example})
    return rows


async def main() -> None:
    async with get_sessionmaker()() as db:
        admin = (await db.execute(select(User).where(User.username == "admin"))).scalar_one()
        for zip_name, book_name in BOOKS.items():
            path = DICTS_DIR / zip_name
            rows = parse_zip(path)
            random.shuffle(rows)  # 词随机
            created = await create_book(db, admin, book_name)
            book_id = created["id"]
            from app.models import WordBook

            bk = (await db.execute(select(WordBook).where(WordBook.id == book_id))).scalar_one()
            bk.is_public = 1  # 真实词库公开，全部账号可见
            n_units = 0
            for idx in range(0, len(rows), UNIT_SIZE):
                part = rows[idx : idx + UNIT_SIZE]
                from app.models import Chapter, Word

                ch = Chapter(book_id=book_id, name=f"Unit {idx // UNIT_SIZE + 1}", sort_order=idx // UNIT_SIZE + 1)
                db.add(ch)
                await db.flush()
                n_units += 1
                for r in part:
                    db.add(
                        Word(
                            chapter_id=ch.id,
                            book_id=book_id,
                            spelling=r["spelling"],
                            meaning=r["meaning"],
                            phonetic=r["phonetic"] or None,
                            example=r["example"] or None,
                        )
                    )
            await db.commit()
            print(f"{book_name}: {len(rows)} 词 → {n_units} 个单元（每单元 {UNIT_SIZE} 词，随机分配）")


asyncio.run(main())
