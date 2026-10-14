"""单词发音：微软 edge-tts 合成，磁盘缓存（带条目上限），无第三方未授权接口。"""

import asyncio
import uuid
from pathlib import Path

import edge_tts
from fastapi import Response

from ..core import ratelimit
from ..core.errors import AppError

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "tts_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 音色：美音/英音各一个高质量神经音色
VOICES = {
    "us": "en-US-AriaNeural",
    "uk": "en-GB-SoniaNeural",
}

# 缓存条目上限（超出后按 mtime 淘汰到 90%），防止匿名请求把磁盘填满
MAX_CACHE_FILES = 20000
# 合成锁表上限，超出后回收空闲锁，避免随唯一词无界增长
MAX_LOCKS = 4096

# 同一文件并发合成加锁，避免重复请求微软服务
_locks: dict[str, asyncio.Lock] = {}


def _cache_path(word: str, voice: str) -> Path:
    safe = "".join(c if c.isalnum() else "_" for c in word.lower())
    return CACHE_DIR / f"{safe}_{voice}.mp3"


def _prune_locks() -> None:
    """锁表超上限时回收当前未被持有的锁（tmp 唯一化后重复合成也不会写坏文件）。"""
    if len(_locks) < MAX_LOCKS:
        return
    for name, lock in list(_locks.items()):
        if not lock.locked():
            _locks.pop(name, None)


def _evict_cache() -> None:
    """缓存条目超上限时，按 mtime 从旧到新淘汰，保留最新 90%。"""
    try:
        files = [(f.stat().st_mtime, f) for f in CACHE_DIR.glob("*.mp3")]
    except OSError:
        return
    if len(files) <= MAX_CACHE_FILES:
        return
    files.sort()
    keep = int(MAX_CACHE_FILES * 0.9)
    for _, f in files[: len(files) - keep]:
        try:
            f.unlink(missing_ok=True)
            _locks.pop(f.name, None)
        except OSError:
            pass


# 淘汰检查节流：预热到数千条后，逐次 glob+stat 整个缓存目录会拖慢事件循环。
# 每 100 次新合成才做一次目录扫描（上限 20000 条，正常远达不到）。
_evict_tick = 0


def _evict_check() -> None:
    global _evict_tick
    _evict_tick += 1
    if _evict_tick % 100 == 0:
        _evict_cache()


async def _synthesize(word: str, voice: str, dest: Path) -> None:
    _prune_locks()
    lock = _locks.setdefault(dest.name, asyncio.Lock())
    async with lock:
        if dest.exists():
            return
        # tmp 文件名唯一化：即便锁表被回收导致并发合成，也不会互相写坏
        tmp = dest.with_name(f"{dest.name}.{uuid.uuid4().hex}.tmp")
        try:
            com = edge_tts.Communicate(word, voice)
            await com.save(str(tmp))
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)
    _evict_check()


async def speak(word: str, accent: str = "us", ip: str = "unknown") -> Response:
    word = (word or "").strip()
    if not word or len(word) > 100:
        raise AppError("VALIDATION_ERROR", "word 非法")
    voice = VOICES.get(accent, VOICES["us"])
    dest = _cache_path(word, voice)
    if not dest.exists():
        # 仅对真正触发上游合成的请求限流（缓存命中不消耗资源，也不占用配额）
        if not ratelimit.tts_allowed(ip):
            raise AppError("RATE_LIMITED", "语音请求过于频繁，请稍后再试")
        try:
            await _synthesize(word, voice, dest)
        except Exception as e:  # noqa: BLE001
            raise AppError("TTS_UNAVAILABLE", "语音合成暂不可用，请稍后重试") from e
    return Response(
        content=dest.read_bytes(),
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-TTS-Voice": voice,
        },
    )
