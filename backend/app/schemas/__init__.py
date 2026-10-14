"""pydantic 请求模型（响应直接用 dict 构造，字段名对齐接口规范 snake_case）。"""

import re
from typing import Literal

from pydantic import BaseModel, Field, StrictBool, field_validator

PASSWORD_RULE_MSG = "密码需至少 8 位且同时包含字母与数字"

# 考核每词限时档位（2026-09-21 恢复）：宽松 1.5 / 标准 1.0 / 紧凑 0.7，
# 倍数只作用于每题倒计时；计分权重恒用原始时限（档位不影响卷面分）。
TimePolicy = Literal["relaxed", "standard", "tight"]


def _check_password_strength(v: str) -> str:
    """共享密码强度校验：至少 8 位且同时含字母与数字（注册与改密同口径）。"""
    if not (re.search(r"[a-zA-Z]", v) and re.search(r"\d", v)):
        raise ValueError(PASSWORD_RULE_MSG)
    return v


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=8, max_length=64)
    invite_code: str | None = None

    @field_validator("username")
    @classmethod
    def username_rule(cls, v: str) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9_\u4e00-\u9fa5]{3,20}", v):
            raise ValueError("用户名需为 3-20 位字母/数字/下划线/中文")
        return v

    @field_validator("password")
    @classmethod
    def password_rule(cls, v: str) -> str:
        return _check_password_strength(v)


class LoginIn(BaseModel):
    username: str
    password: str


class PasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=64)

    @field_validator("new_password")
    @classmethod
    def new_password_rule(cls, v: str) -> str:
        return _check_password_strength(v)


class NicknameIn(BaseModel):
    """昵称（2026-09-22）：用户唯一可自行修改的展示名，用户名注册后不可改。"""

    nickname: str = Field(min_length=1, max_length=20)

    @field_validator("nickname")
    @classmethod
    def nickname_rule(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("昵称不能为空")
        return v


class SettingsIn(BaseModel):
    daily_review_limit: int | None = Field(default=None, ge=0, le=99999)
    typing_guide_on: int | None = Field(default=None, ge=0, le=1)
    tts_on: int | None = Field(default=None, ge=0, le=1)
    review_form: str | None = None
    dictation_show_seconds: int | None = Field(default=None, ge=2, le=5)
    practice_group_size: int | None = Field(default=None, ge=1, le=9999)
    game_difficulty: str | None = None
    game_limited_mode: int | None = Field(default=None, ge=0, le=1)
    game_key_sound: int | None = Field(default=None, ge=0, le=1)
    # 下坠时间已固化（5s + 0.75s/字母 × 难度系数），不再接受用户设置；旧字段保留以兼容旧前端
    advice_engine: str | None = Field(default=None, pattern="^(rule|llm)$")
    exam_time_limit: int | None = Field(default=None, ge=5, le=120)
    exam_pass_score: int | None = Field(default=None, ge=0, le=100)
    # exam_loose_match 已于 2026-09-19 取消（考核得分比=输入正确的字符数÷全部字符数，无开关可配）；旧前端若仍传该键会被忽略
    review_wrong_reshow: int | None = Field(default=None, ge=0, le=1)
    review_show_meaning: int | None = Field(default=None, ge=0, le=1)


class BookIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class BookPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_public: StrictBool | None = None  # 公开/私有（仅管理员可改，严格布尔）


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
    time_limit_s: int | None = Field(default=None, ge=3, le=600)  # 打字题基础秒数；NULL = 自动公式


class WordPatchIn(BaseModel):
    spelling: str | None = None
    meaning: str | None = None
    phonetic: str | None = None
    example: str | None = None
    time_limit_s: int | None = Field(default=None, ge=3, le=600)  # 传 null = 恢复自动公式


class GameFallMultIn(BaseModel):
    """游戏下坠倍数（全局，仅管理员）：出现 → 消失 = 时限 × mult，1~10。"""
    mult: float = Field(ge=1, le=10)


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
    group_size: int = Field(default=20, ge=1, le=9999)
    # 仪表盘「错误率>60% 专项练习」直达：按词组卷，与 chapter_ids 二选一（word_ids 优先）
    word_ids: list[int] = Field(default_factory=list, max_length=999)


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


class ReviewAdvanceIn(BaseModel):
    target_date: str


class ExamStartIn(BaseModel):
    # 多章节复选（2026-09-22）：chapter_ids 优先；chapter_id 保留兼容旧客户端（单章节）
    chapter_id: int | None = None
    chapter_ids: list[int] = Field(default_factory=list)
    type_counts: dict[str, int]
    time_limit_min: int = Field(default=20, ge=5, le=120)
    pass_score: int = Field(default=60, ge=0, le=100)
    time_policy: TimePolicy = "standard"
    # loose_match 已于 2026-09-19 取消；旧客户端传了也会被忽略


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
    # 排行榜分组维度：前端传入所选词库与章节（chapter_id 为空=整本词库）
    book_id: int | None = None
    chapter_id: int | None = None

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


class WrongBookBatchPatchIn(BaseModel):
    """错题本批量操作（2026-10-04 批量移出）：仅本人条目，不存在的 ID 静默跳过"""
    word_ids: list[int] = Field(min_length=1, max_length=200)
    pinned: int | None = Field(default=None, ge=0, le=1)
    resolved: int | None = Field(default=None, ge=0, le=1)


class InviteCodeIn(BaseModel):
    count: int = Field(ge=1, le=100)
    valid_days: int = Field(ge=1, le=365)


class InviteCodePatchIn(BaseModel):
    is_active: int = Field(ge=0, le=1)


class ResetPasswordIn(BaseModel):
    """管理员重置密码：new_password 缺省 = 随机生成临时密码（2026-09-21 新增指定密码方式）。"""

    new_password: str | None = None

    @field_validator("new_password")
    @classmethod
    def _pw_rule(cls, v: str | None) -> str | None:
        if v is not None and (len(v) < 8 or not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v)):
            raise ValueError(PASSWORD_RULE_MSG)
        return v


class UserAiConfigIn(BaseModel):
    """用户自有 LLM 凭据（api_key 仅写入，永不回显；留空表示不修改）。"""

    api_base_url: str | None = None
    api_key: str | None = None
    model_name: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    timeout_s: int | None = Field(default=None, ge=1, le=300)


class DevDataSeedIn(BaseModel):
    """管理员测试数据注入参数（全部可省略，省略项不注入）。"""

    # 注意：字段名不可用 user_id —— 全局中间件红线禁止 body 携带 user_id
    target_user_id: int | None = None  # 缺省=管理员自己
    high_error_n: int | None = Field(default=None, ge=1, le=200)
    danger_due_n: int | None = Field(default=None, ge=1, le=200)
    inactive_days: int | None = Field(default=None, ge=1, le=30)
    weak_bigram_n: int | None = Field(default=None, ge=1, le=200)
    typing_count: int | None = Field(default=None, ge=1, le=200)
    typing_wpm: float | None = Field(default=None, ge=5, le=300)
    typing_accuracy: float | None = Field(default=None, ge=0.1, le=1)
    game_count: int | None = Field(default=None, ge=1, le=200)


# ==================== 班级（D34） ====================


class ClassIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)


class ClassPatchIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)


class UserClassIn(BaseModel):
    class_id: int | None = None  # NULL = 移回默认班级


# ==================== 考核卷（D36：管理员定义、全班同卷） ====================


class ExamTemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    # 多章节复选（2026-09-22）：chapter_ids 优先；chapter_id 保留兼容旧客户端（单章节）
    chapter_id: int | None = None
    chapter_ids: list[int] = Field(default_factory=list)
    type_counts: dict[str, int]
    # 2026-09-21 与个人端考核（ExamStartIn）对齐：5~120 分钟
    time_limit_min: int = Field(default=60, ge=5, le=120)
    pass_score: int = Field(default=60, ge=0, le=100)
    time_policy: TimePolicy = "standard"
    # loose_match 已于 2026-09-19 取消；旧客户端传了也会被忽略


class ExamTemplatePatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None


class PaperAssignIn(BaseModel):
    class_id: int
    available_at: str | None = None  # "YYYY-MM-DD HH:MM:SS"，NULL=立即启用
    expires_at: str | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=99)  # 每人最多可考次数，NULL=不限


class UserImportRow(BaseModel):
    username: str = Field(min_length=1, max_length=50)  # 格式校验在 service 内逐行做，坏行跳过而不是整批拒绝
    password: str | None = None  # 空/None → 自动生成临时密码
    class_name: str | None = None  # 班级名，不存在则自动创建；NULL = 默认班级


class UserImportIn(BaseModel):
    users: list[UserImportRow] = Field(min_length=1, max_length=1000)
