"""SQLAlchemy 模型 —— 与 DBD v1.0 DDL 一一对应（SQLite 方言：TEXT 时间、INTEGER 布尔、部分唯一索引）。"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Float,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .db.engine import now_str


class Base(DeclarativeBase):
    pass


def _ts():
    return datetime.now


class User(Base):
    __tablename__ = "user"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="user")
    pwd_ver: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_at: Mapped[str | None] = mapped_column(Text)
    last_login_at: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_str)


Index("uq_user_name", User.username, unique=True, sqlite_where=None)


class InviteCode(Base):
    __tablename__ = "invite_code"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    used_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    used_at: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_str)


class DailySetting(Base):
    __tablename__ = "daily_setting"
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    daily_new_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    daily_review_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    loose_match: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    typing_guide_on: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    tts_on: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    review_form: Mapped[str] = mapped_column(Text, nullable=False, default="typing")
    dictation_show_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    practice_group_size: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    game_difficulty: Mapped[str] = mapped_column(Text, nullable=False, default="normal")
    game_limited_mode: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    game_key_sound: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    exam_time_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    exam_pass_score: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    exam_loose_match: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_wrong_reshow: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class SystemSetting(Base):
    __tablename__ = "system_setting"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_str)


class WordBook(Base):
    __tablename__ = "word_book"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    is_public: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_at: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_str)


class Chapter(Base):
    __tablename__ = "chapter"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("word_book.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Word(Base):
    __tablename__ = "word"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapter.id"), nullable=False)
    book_id: Mapped[int] = mapped_column(ForeignKey("word_book.id"), nullable=False)
    spelling: Mapped[str] = mapped_column(Text, nullable=False)
    meaning: Mapped[str] = mapped_column(Text, nullable=False)
    phonetic: Mapped[str | None] = mapped_column(Text)
    example: Mapped[str | None] = mapped_column(Text)
    is_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_at: Mapped[str | None] = mapped_column(Text)
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_str)


Index("uq_word_book_spelling", Word.book_id, Word.spelling, unique=True, sqlite_where=Word.is_deleted == 0)
Index("idx_word_chapter", Word.chapter_id, Word.is_deleted, Word.id)


class UserWordStat(Base):
    __tablename__ = "user_word_stat"
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    word_id: Mapped[int] = mapped_column(ForeignKey("word.id"), primary_key=True)
    proficiency: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wrong_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    near_miss_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    streak_correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ease_factor: Mapped[float] = mapped_column(Float, nullable=False, default=2.5)
    interval_days: Mapped[int | None] = mapped_column(Integer)
    next_review_at: Mapped[str | None] = mapped_column(Text)
    last_wrong_at: Mapped[str | None] = mapped_column(Text)
    last_correct_at: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (CheckConstraint("ease_factor BETWEEN 1.3 AND 3.0"),)


Index("idx_stat_queue", UserWordStat.user_id, UserWordStat.next_review_at)
Index("idx_stat_prof", UserWordStat.user_id, UserWordStat.proficiency)


class StudyProgress(Base):
    __tablename__ = "study_progress"
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapter.id", ondelete="CASCADE"), primary_key=True)
    last_word_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[str | None] = mapped_column(Text)


class LetterStat(Base):
    __tablename__ = "letter_stat"
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    letter: Mapped[str] = mapped_column(Text, primary_key=True)
    avg_delay_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DailyActivity(Base):
    __tablename__ = "daily_activity"
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    date: Mapped[str] = mapped_column(Text, primary_key=True)
    new_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wrong_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TypingRecord(Base):
    __tablename__ = "typing_record"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    word_id: Mapped[int] = mapped_column(ForeignKey("word.id"), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[str] = mapped_column(Text, nullable=False)
    wpm: Mapped[float | None] = mapped_column(Float)
    accuracy: Mapped[float | None] = mapped_column(Float)
    request_id: Mapped[str | None] = mapped_column(Text)
    detail_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_str)


Index("idx_tr_user_time", TypingRecord.user_id, TypingRecord.created_at)
Index(
    "uq_tr_request",
    TypingRecord.user_id,
    TypingRecord.source,
    TypingRecord.request_id,
    unique=True,
    sqlite_where=TypingRecord.request_id.is_not(None),
)


class WrongBookItem(Base):
    __tablename__ = "wrong_book_item"
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    word_id: Mapped[int] = mapped_column(ForeignKey("word.id"), primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    conquer_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pinned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    added_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_str)
    resolved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resolved_at: Mapped[str | None] = mapped_column(Text)


class ExamRecord(Base):
    __tablename__ = "exam_record"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    paper_id: Mapped[str | None] = mapped_column(Text)
    chapter_id: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    duration: Mapped[int] = mapped_column(Integer, nullable=False)
    blur_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    detail_json: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_str)


Index("uq_exam_idem", ExamRecord.idempotency_key, unique=True, sqlite_where=ExamRecord.idempotency_key.is_not(None))
Index("idx_exam_user", ExamRecord.user_id, ExamRecord.created_at)


class GameRecord(Base):
    __tablename__ = "game_record"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    session_id: Mapped[str | None] = mapped_column(Text)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    max_combo: Mapped[int] = mapped_column(Integer, nullable=False)
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False)
    wpm: Mapped[float | None] = mapped_column(Float)
    difficulty: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    is_valid: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_str)


Index("uq_game_idem", GameRecord.idempotency_key, unique=True, sqlite_where=GameRecord.idempotency_key.is_not(None))


class ExamPaper(Base):
    __tablename__ = "exam_paper"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    chapter_id: Mapped[int] = mapped_column(Integer, nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    questions_json: Mapped[str] = mapped_column(Text, nullable=False)
    answer_key_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    blur_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_at: Mapped[str | None] = mapped_column(Text)


class ReviewQueue(Base):
    __tablename__ = "review_queue"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[str] = mapped_column(Text, nullable=False)
    word_id: Mapped[int] = mapped_column(ForeignKey("word.id"), nullable=False)
    tier: Mapped[int] = mapped_column(Integer, nullable=False)
    overdue_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_reshow: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_str)


Index("uq_rq", ReviewQueue.user_id, ReviewQueue.date, ReviewQueue.word_id, ReviewQueue.is_reshow, unique=True)
Index("idx_rq_read", ReviewQueue.user_id, ReviewQueue.date, ReviewQueue.status, ReviewQueue.tier)


class AdviceCache(Base):
    __tablename__ = "advice_cache"
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    date: Mapped[str] = mapped_column(Text, primary_key=True)
    items_json: Mapped[str] = mapped_column(Text, nullable=False)
    engine: Mapped[str] = mapped_column(Text, nullable=False, default="rule")
    refresh_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    generated_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_str)


class GameSession(Base):
    __tablename__ = "game_session"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    word_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    difficulty: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_str)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)


class ImportJob(Base):
    __tablename__ = "import_job"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    book_id: Mapped[int | None] = mapped_column(ForeignKey("word_book.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="uploaded")
    total_rows: Mapped[int | None] = mapped_column(Integer)
    ok_rows: Mapped[int | None] = mapped_column(Integer)
    dup_rows: Mapped[int | None] = mapped_column(Integer)
    error_rows: Mapped[int | None] = mapped_column(Integer)
    parsed_json: Mapped[str | None] = mapped_column(Text)
    error_report_path: Mapped[str | None] = mapped_column(Text)
    duplicate_strategy: Mapped[str | None] = mapped_column(Text)
    preview_token: Mapped[str | None] = mapped_column(Text, unique=True)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_str)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_record"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_str)


Index("idx_idem_created", IdempotencyRecord.created_at)
