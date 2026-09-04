"""pydantic 请求模型（响应直接用 dict 构造，字段名对齐接口规范 snake_case）。"""

from pydantic import BaseModel, Field, field_validator


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=8, max_length=64)
    invite_code: str | None = None

    @field_validator("username")
    @classmethod
    def username_rule(cls, v: str) -> str:
        import re

        if not re.fullmatch(r"[a-zA-Z0-9_\u4e00-\u9fa5]{3,20}", v):
            raise ValueError("用户名需为 3-20 位字母/数字/下划线/中文")
        return v

    @field_validator("password")
    @classmethod
    def password_rule(cls, v: str) -> str:
        import re

        if not (re.search(r"[a-zA-Z]", v) and re.search(r"\d", v)):
            raise ValueError("密码需至少 8 位且同时包含字母与数字")
        return v


class LoginIn(BaseModel):
    username: str
    password: str


class PasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=64)


class SettingsIn(BaseModel):
    daily_new_limit: int | None = Field(default=None, ge=0, le=100)
    daily_review_limit: int | None = Field(default=None, ge=0, le=500)
    loose_match: int | None = Field(default=None, ge=0, le=1)
    typing_guide_on: int | None = Field(default=None, ge=0, le=1)
    tts_on: int | None = Field(default=None, ge=0, le=1)
    review_form: str | None = None
    dictation_show_seconds: int | None = Field(default=None, ge=2, le=5)
    practice_group_size: int | None = Field(default=None, ge=10, le=50)
    game_difficulty: str | None = None
    game_limited_mode: int | None = Field(default=None, ge=0, le=1)
    game_key_sound: int | None = Field(default=None, ge=0, le=1)
    exam_time_limit: int | None = Field(default=None, ge=5, le=120)
    exam_pass_score: int | None = Field(default=None, ge=0, le=100)
    exam_loose_match: int | None = Field(default=None, ge=0, le=1)
    review_wrong_reshow: int | None = Field(default=None, ge=0, le=1)


class BookIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class BookPatchIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ChapterIn(BaseModel):
    book_id: int
    name: str = Field(min_length=1, max_length=100)
    sort_order: int | None = None


class ChapterPatchIn(BaseModel):
    name: str | None = None
    sort_order: int | None = None


class WordIn(BaseModel):
    chapter_id: int
    spelling: str = Field(min_length=1, max_length=100)
    meaning: str = Field(min_length=1, max_length=500)
    phonetic: str | None = None
    example: str | None = None


class WordPatchIn(BaseModel):
    spelling: str | None = None
    meaning: str | None = None
    phonetic: str | None = None
    example: str | None = None


class StudyScope(BaseModel):
    scope: str | None = None  # "chapter:23" | "book:5"


class SelfRateIn(BaseModel):
    word_id: int
    rating: str
    request_id: str

    @field_validator("rating")
    @classmethod
    def rating_rule(cls, v: str) -> str:
        if v not in ("know", "vague", "unknown"):
            raise ValueError("rating 必须为 know/vague/unknown")
        return v


class DictationIn(BaseModel):
    word_id: int
    typed: str
    duration_ms: int = Field(ge=0)
    detail: dict | None = None
    request_id: str


class PracticeSessionIn(BaseModel):
    chapter_ids: list[int] = Field(default_factory=list)
    types: list[str] = Field(min_length=1)
    group_size: int = Field(default=20, ge=10, le=50)


class PracticeAnswerIn(BaseModel):
    qid: str
    word_id: int
    type: str
    typed: str | None = None
    choice_key: str | None = None
    duration_ms: int = Field(default=0, ge=0)
    detail: dict | None = None
    request_id: str


class ReviewAnswerIn(BaseModel):
    word_id: int
    form: str
    typed: str | None = None
    choice_key: str | None = None
    rating: str | None = None
    reshow: int = 0
    duration_ms: int = Field(default=0, ge=0)
    detail: dict | None = None
    request_id: str


class ExamStartIn(BaseModel):
    chapter_id: int
    type_counts: dict[str, int]
    time_limit_min: int = Field(default=20, ge=5, le=120)
    pass_score: int = Field(default=60, ge=0, le=100)
    loose_match: bool = False


class BlurIn(BaseModel):
    count: int = Field(default=1, ge=1)


class ExamSubmitIn(BaseModel):
    paper_id: str
    answers: list[dict] = Field(default_factory=list)
    duration_ms: int = Field(default=0, ge=0)


class GameStartIn(BaseModel):
    chapter_ids: list[int] = Field(default_factory=list)
    difficulty: str
    mode: str

    @field_validator("difficulty")
    @classmethod
    def d(cls, v):
        if v not in ("easy", "normal", "hard"):
            raise ValueError("difficulty 必须为 easy/normal/hard")
        return v

    @field_validator("mode")
    @classmethod
    def m(cls, v):
        if v not in ("endless", "timed"):
            raise ValueError("mode 必须为 endless/timed")
        return v


class GameSubmitIn(BaseModel):
    session_id: str
    difficulty: str
    mode: str
    duration_ms: int = Field(ge=0)
    score: int = Field(ge=0)
    max_combo: int = Field(ge=0)
    correct_count: int = Field(ge=0)
    words: list[dict] = Field(default_factory=list)


class WrongBookIn(BaseModel):
    word_id: int


class WrongBookPatchIn(BaseModel):
    pinned: int | None = Field(default=None, ge=0, le=1)
    resolved: int | None = Field(default=None, ge=0, le=1)


class InviteCodeIn(BaseModel):
    count: int = Field(ge=1, le=100)
    valid_days: int = Field(ge=1, le=365)


class InviteCodePatchIn(BaseModel):
    is_active: int = Field(ge=0, le=1)


class AIConfigIn(BaseModel):
    engine: str | None = None
    api_base_url: str | None = None
    api_key: str | None = None
    model_name: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    timeout_s: int | None = Field(default=None, ge=1, le=300)
    enabled: bool | None = None
