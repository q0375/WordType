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
    class_id: Mapped[int | None] = mapped_column(Integer)  # 所属班级；NULL 表示未分配（语义上归默认班级）
    pwd_ver: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_at: Mapped[str | None] = mapped_column(Text)
    last_login_at: Mapped[str | None] = mapped_column(Text)
    # 昵称（2026-09-22）：用户唯一可自行修改的展示名，NULL 时回落显示用户名；用户名注册后不可改
    nickname: Mapped[str | None] = mapped_column(Text)
    # 最后活跃时间（2026-09-22）：认证请求节流（≥60s）刷新，管理端据此显示在线状态（5 分钟内视为在线）
    last_seen_at: Mapped[str | None] = mapped_column(Text)
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
    daily_new_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=20)  # 历史列：学习已取消每日新词上限，保留只为免重建线上表
    daily_review_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=100)  # 历史列：复习已取消每日上限（2026-09-23），队列全量物化，保留只为免重建线上表
    loose_match: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    typing_guide_on: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    tts_on: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    review_form: Mapped[str] = mapped_column(Text, nullable=False, default="typing")
    dictation_show_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    practice_group_size: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    game_difficulty: Mapped[str] = mapped_column(Text, nullable=False, default="normal")
    game_limited_mode: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    game_key_sound: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    game_fall_mode: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # 1=按词长加时 0=跟随难度固定速度
    game_fall_base: Mapped[int] = mapped_column(Integer, nullable=False, default=8)  # 按词长加时：基准秒
    game_fall_per_char: Mapped[int] = mapped_column(Integer, nullable=False, default=6)  # 每字母加时，0.1 秒为单位（6=0.6s）
    advice_engine: Mapped[str] = mapped_column(Text, nullable=False, default="rule")  # rule=规则引擎 / llm=AI 模型（用户自选）
    exam_time_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    exam_pass_score: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    exam_loose_match: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_wrong_reshow: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    review_show_meaning: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # 复习时是否显示释义提示


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
    time_limit_s: Mapped[int | None] = mapped_column(Integer)  # 管理员设置的打字题基础秒数；NULL = 自动（5+0.75×词长）
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
    # 排行榜分组维度：词库 + 章节（NULL=整本词库）+ 章节集合键（2026-09-23：多选章节按集合分组）
    book_id: Mapped[int | None] = mapped_column(Integer)
    chapter_id: Mapped[int | None] = mapped_column(Integer)
    # 排行榜分组键：所选章节集合规范化（升序去重逗号串，如 "501,502"）；NULL=整本词库（含全选/不选）
    chapter_key: Mapped[str | None] = mapped_column(Text)
    is_valid: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_str)


Index("uq_game_idem", GameRecord.idempotency_key, unique=True, sqlite_where=GameRecord.idempotency_key.is_not(None))


class ExamPaper(Base):
    __tablename__ = "exam_paper"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    chapter_id: Mapped[int] = mapped_column(Integer, nullable=False)
    template_id: Mapped[int | None] = mapped_column(Integer)  # 来源考核卷模板（自组卷为 NULL）
    source: Mapped[str] = mapped_column(Text, nullable=False, default="self")  # self=自组卷 / template=考核卷
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    questions_json: Mapped[str] = mapped_column(Text, nullable=False)
    answer_key_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    blur_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_at: Mapped[str | None] = mapped_column(Text)


class StudyClass(Base):
    """班级。默认班级固定存在（is_default=1），未分配用户语义上归默认班级。"""

    __tablename__ = "study_class"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    is_default: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[int | None] = mapped_column(Integer)  # 创建管理员
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_str)


class ExamTemplate(Base):
    """考核卷（管理员定义）：创建时固定组卷，所有参与者题目完全一致。"""

    __tablename__ = "exam_template"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    chapter_id: Mapped[int] = mapped_column(Integer, nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    questions_json: Mapped[str] = mapped_column(Text, nullable=False)
    answer_key_json: Mapped[str] = mapped_column(Text, nullable=False)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_str)


class ClassPaperAssign(Base):
    """班级-考核卷分配：available_at 到时自动对该班启用，expires_at 过后截止（可空=不截止）。"""

    __tablename__ = "class_paper_assign"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("exam_template.id", ondelete="CASCADE"), nullable=False)
    class_id: Mapped[int] = mapped_column(ForeignKey("study_class.id", ondelete="CASCADE"), nullable=False)
    available_at: Mapped[str | None] = mapped_column(Text)  # NULL = 立即生效
    expires_at: Mapped[str | None] = mapped_column(Text)  # NULL = 不截止
    max_attempts: Mapped[int | None] = mapped_column(Integer)  # NULL = 不限次数；否则每人最多可交卷 N 次
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_str)


Index("idx_assign_read", ClassPaperAssign.class_id, ClassPaperAssign.template_id, unique=True)


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
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")  # engine=rule 且选了 AI 时记录回退原因
    refresh_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    generated_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_str)


class UserAiConfig(Base):
    """每用户自有 LLM 凭据（AES-GCM 加密存储 Key，仅本人可读写）。"""

    __tablename__ = "user_ai_config"
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    api_base_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    api_key_enc: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    timeout_s: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    updated_at: Mapped[str | None] = mapped_column(Text)


class GameSession(Base):
    __tablename__ = "game_session"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    word_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    difficulty: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    # 结算时回写进 GameRecord，供排行榜分组（不可由客户端在 submit 时伪造）
    book_id: Mapped[int | None] = mapped_column(Integer)
    chapter_id: Mapped[int | None] = mapped_column(Integer)
    # 开局快照的章节集合键（规范化同 GameRecord.chapter_key；NULL=整本）
    chapter_key: Mapped[str | None] = mapped_column(Text)
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
