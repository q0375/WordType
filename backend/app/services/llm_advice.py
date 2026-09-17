"""LLM 建议引擎（D28）：OpenAI 兼容 /chat/completions，基于真实学习数据生成个性化建议。

凭据为每用户自有（user_ai_config 表，Key AES-GCM 加密），管理员不参与配置。
任何失败（未配置/超时/报错/输出不合法）一律返回 None，由调用方回退规则引擎。
"""

import json
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.security import decrypt_secret
from ..models import UserAiConfig

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "你是英语打字学习应用的学习顾问。请根据用户的学习数据，生成恰好 3 条个性化、可执行的学习建议。"
    "建议必须与数据中的具体事实对应（引用具体单词、数字或字母组合），用简体中文，每条不超过 80 字。"
    "只输出 JSON，不要任何解释或代码块标记，格式："
    '{"items":[{"type":"high_error|danger_due|inactive|typing_weak|custom","priority":1,'
    '"message":"建议内容","action":{"type":"practice|review|study_plan|typing_drill|none","params":{}}}]}，'
    "priority 从 1 开始按重要程度递增。"
)


async def load_user_config(db: AsyncSession, user_id: int) -> UserAiConfig | None:
    return (await db.execute(select(UserAiConfig).where(UserAiConfig.user_id == user_id))).scalar_one_or_none()


def config_ready(cfg: UserAiConfig | None) -> bool:
    """凭据齐全（基地址/Key/模型名）即视为可用。"""
    return bool(cfg and cfg.api_base_url and cfg.api_key_enc and cfg.model_name)


def mask(cfg: UserAiConfig | None) -> dict:
    from ..core.security import mask_secret

    if cfg is None:
        return {"api_base_url": "", "api_key_masked": "", "model_name": "", "temperature": 0.7, "timeout_s": 30}
    return {
        "api_base_url": cfg.api_base_url,
        "api_key_masked": mask_secret(cfg.api_key_enc) if cfg.api_key_enc else "",
        "model_name": cfg.model_name,
        "temperature": cfg.temperature,
        "timeout_s": cfg.timeout_s,
    }


def _extract_items(content: str) -> list[dict] | None:
    """解析模型输出为合法建议列表；不合法返回 None。"""
    text = content.strip()
    if text.startswith("```"):  # 剥离 markdown 代码块
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 兜底：部分推理模型会在 JSON 前后带说明文字，截取首个 { 到末个 } 之间的内容
        l, r = text.find("{"), text.rfind("}")
        if l == -1 or r <= l:
            return None
        try:
            data = json.loads(text[l : r + 1])
        except json.JSONDecodeError:
            return None
    raw_items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(raw_items, list) or not raw_items:
        return None
    items: list[dict] = []
    for i, it in enumerate(raw_items[:3]):
        if not isinstance(it, dict) or not str(it.get("message", "")).strip():
            continue
        action = it.get("action") if isinstance(it.get("action"), dict) else {"type": "none", "params": {}}
        items.append({
            "type": str(it.get("type") or "custom"),
            "priority": it.get("priority") if isinstance(it.get("priority"), int) else i + 1,
            "message": str(it["message"]).strip()[:200],
            "action": {"type": str(action.get("type") or "none"), "params": action.get("params") or {}},
        })
    return items or None


async def _call(cfg: UserAiConfig, context: dict) -> tuple[list[dict] | None, str]:
    """单次调用。返回 (items, 失败原因)；成功时原因为空串。"""
    key = decrypt_secret(cfg.api_key_enc)
    async with httpx.AsyncClient(timeout=cfg.timeout_s) as client:
        resp = await client.post(
            cfg.api_base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": cfg.model_name,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                ],
                "temperature": cfg.temperature,
            },
        )
    if resp.status_code != 200:
        logger.warning("LLM advice HTTP %s: %s", resp.status_code, resp.text[:200])
        return None, f"接口返回 HTTP {resp.status_code}"
    msg = resp.json()["choices"][0]["message"]
    content = msg.get("content") or ""
    if not content.strip():
        # 推理模型兜底：content 为空时尝试从 reasoning_content 中提取 JSON
        content = msg.get("reasoning_content") or ""
    items = _extract_items(content)
    if items is None:
        logger.warning("LLM advice output unparsable: %.200s", content)
        return None, "模型输出无法解析为建议（可能是不兼容的模型）"
    return items, ""


async def generate(db: AsyncSession, user_id: int, context: dict) -> tuple[list[dict] | None, str]:
    """用用户自有凭据调用 LLM 生成建议；失败自动重试一次。

    返回 (items, 失败原因)：items 为 None 时原因为非空字符串，由调用方回退规则引擎并展示原因。
    """
    cfg = await load_user_config(db, user_id)
    if not config_ready(cfg):
        return None, "未配置完整（需要基地址、Key、模型名）"
    reason = "未知错误"
    for attempt in (1, 2):  # 瞬时超时/偶发 5xx 重试一次
        try:
            items, reason = await _call(cfg, context)
            if items is not None:
                return items, ""
        except Exception as e:  # noqa: BLE001 — 网络超时、JSON 结构异常等一律回退
            reason = f"{type(e).__name__}: {e}"[:160]
            logger.warning("LLM advice attempt %s failed: %s", attempt, reason)
    return None, reason


async def test(db: AsyncSession, user_id: int) -> dict:
    """1-token 探测用户自有凭据；无论成败均 200（ok:false 带原因）。"""
    import time

    cfg = await load_user_config(db, user_id)
    if not config_ready(cfg):
        return {"ok": False, "latency_ms": 0, "message": "未配置完整（需要基地址、Key、模型名）"}
    try:
        key = decrypt_secret(cfg.api_key_enc)
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=cfg.timeout_s) as client:
            resp = await client.post(
                cfg.api_base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": cfg.model_name, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
            )
        latency = int((time.monotonic() - start) * 1000)
        return {"ok": resp.status_code == 200, "latency_ms": latency, "message": f"HTTP {resp.status_code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "latency_ms": 0, "message": str(e)[:200]}
