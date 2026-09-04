"""词库端点：books / chapters / words / 导入三步制 / 导出。"""

from fastapi import APIRouter, Depends, Request, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.errors import AppError
from ....db.engine import get_db
from ....models import User
from ....schemas import BookIn, BookPatchIn, ChapterIn, ChapterPatchIn, WordIn, WordPatchIn
from ....services import books_service
from ..deps import get_current_user

router = APIRouter()


@router.get("/books")
async def list_books(tab: str = "all", q: str = "", page: int = 1, page_size: int = 20,
                     user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await books_service.list_books(db, user, tab, q, page, page_size)


@router.post("/books", status_code=201)
async def create_book(payload: BookIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await books_service.create_book(db, user, payload.name)
    await db.commit()
    return data


@router.patch("/books/{book_id}", status_code=204)
async def patch_book(book_id: int, payload: BookPatchIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await books_service.patch_book(db, user, book_id, payload.name)
    await db.commit()
    return Response(status_code=204)


@router.delete("/books/{book_id}", status_code=204)
async def delete_book(book_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await books_service.delete_book(db, user, book_id)
    await db.commit()
    return Response(status_code=204)


@router.post("/books/{book_id}/clone", status_code=201)
async def clone_book(book_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await books_service.clone_book(db, user, book_id)
    await db.commit()
    return data


@router.get("/books/{book_id}/export")
async def export_book(book_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    book = await books_service.get_book_scoped(db, user, book_id)
    csv_text = await books_service.export_book_csv(db, book_id)
    await db.commit()
    filename = f"{book.name}.csv"
    return Response(
        content=csv_text.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename.encode('ascii', 'ignore').decode() or 'export.csv'}"},
    )


@router.get("/books/{book_id}/chapters")
async def list_chapters(book_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await books_service.get_book_scoped(db, user, book_id)
    return {"items": await books_service.list_chapters(db, book_id)}


@router.post("/chapters", status_code=201)
async def create_chapter(payload: ChapterIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await books_service.create_chapter(db, user, payload.book_id, payload.name, payload.sort_order)
    await db.commit()
    return data


@router.patch("/chapters/{chapter_id}", status_code=204)
async def patch_chapter(chapter_id: int, payload: ChapterPatchIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await books_service.patch_chapter(db, user, chapter_id, payload.name, payload.sort_order)
    await db.commit()
    return Response(status_code=204)


@router.delete("/books/{book_id}/chapters", status_code=200)
async def delete_all_chapters(book_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await books_service.delete_all_chapters(db, user, book_id)
    await db.commit()
    return data


@router.post("/books/{book_id}/resplit", status_code=200)
async def resplit_chapters(book_id: int, words_per_chapter: int | None = None, unit_count: int | None = None,
                           user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if words_per_chapter is not None and not (5 <= words_per_chapter <= 200):
        raise AppError("VALIDATION_ERROR", "每单元词数需在 5–200 之间")
    if unit_count is not None and not (2 <= unit_count <= 500):
        raise AppError("VALIDATION_ERROR", "单元数量需在 2–500 之间")
    if words_per_chapter and unit_count:
        raise AppError("VALIDATION_ERROR", "每单元词数与单元数量只能二选一")
    if not words_per_chapter and not unit_count:
        raise AppError("VALIDATION_ERROR", "请提供 words_per_chapter 或 unit_count")
    data = await books_service.resplit_chapters(db, user, book_id, words_per_chapter, unit_count)
    await db.commit()
    return data


@router.delete("/chapters/{chapter_id}", status_code=204)
async def delete_chapter(chapter_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await books_service.delete_chapter(db, user, chapter_id)
    await db.commit()
    return Response(status_code=204)


@router.get("/words")
async def list_words(book_id: int, chapter_id: int | None = None, q: str | None = None, page: int = 1, page_size: int = 50,
                     user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await books_service.get_book_scoped(db, user, book_id)
    return await books_service.list_words(db, book_id, chapter_id, q, page, page_size)


@router.post("/words", status_code=201)
async def create_word(payload: WordIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await books_service.create_word(db, user, payload)
    await db.commit()
    return data


@router.patch("/words/{word_id}", status_code=204)
async def patch_word(word_id: int, payload: WordPatchIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await books_service.patch_word(db, user, word_id, payload)
    await db.commit()
    return Response(status_code=204)


@router.delete("/words/{word_id}", status_code=204)
async def delete_word(word_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await books_service.delete_word(db, user, word_id)
    await db.commit()
    return Response(status_code=204)


# ---------- 导入三步制 ----------

@router.post("/books/import")
async def upload_import(request: Request, file: UploadFile, book_name: str | None = None, target_book_id: int | None = None,
                        user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = await books_service.upload_import(db, user, file, book_name, target_book_id)
    await db.commit()
    return data


@router.get("/books/import/{job_id}")
async def get_import_job(job_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await books_service.get_import_job(db, user, job_id)


@router.get("/books/import/preview")
async def import_preview(token: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await books_service.import_preview(db, user, token)


@router.post("/books/import/confirm", status_code=201)
async def import_confirm(token: str, duplicate_strategy: str = "skip", words_per_chapter: int | None = None,
                         unit_count: int | None = None,
                         user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if words_per_chapter is not None and not (5 <= words_per_chapter <= 200):
        raise AppError("VALIDATION_ERROR", "每单元词数需在 5–200 之间")
    if unit_count is not None and not (2 <= unit_count <= 500):
        raise AppError("VALIDATION_ERROR", "单元数量需在 2–500 之间")
    if words_per_chapter and unit_count:
        raise AppError("VALIDATION_ERROR", "每单元词数与单元数量只能二选一")
    data = await books_service.import_confirm(db, user, token, duplicate_strategy, words_per_chapter, unit_count)
    await db.commit()
    return data
