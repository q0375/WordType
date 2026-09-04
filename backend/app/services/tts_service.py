"""单词发音：微软 edge-tts 合成，磁盘缓存，无第三方未授权接口。"""

import asyncio
from pathlib import Path

import edge_tts
from fastapi import Response

from ..core.errors import AppError

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "tts_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 音色：美音/英音各一个高质量神经音色
VOICES = {
    "us": "en-US-AriaNeural",
    "uk": "en-GB-SoniaNeural",
}

# 同一文件并发合成加锁，避免重复请求微软服务
_locks: dict[str, asyncio.Lock] = {}


def _cache_path(word: str, voice: str) -> Path:
    safe = "".join(c if c.isalnum() else "_" for c in word.lower())
    return CACHE_DIR / f"{safe}_{voice}.mp3"


async def _synthesize(word: str, voice: str, dest: Path) -> None:
    lock = _locks.setdefault(dest.name, asyncio.Lock())
    async with lock:
        if dest.exists():
            return
        tmp = dest.with_suffix(".tmp")
        com = edge_tts.Communicate(word, voice)
        await com.save(str(tmp))
        tmp.replace(dest)


async def speak(word: str, accent: str = "us") -> Response:
    word = (word or "").strip()
    if not word or len(word) > 100:
        raise AppError("VALIDATION_ERROR", "word 非法")
    voice = VOICES.get(accent, VOICES["us"])
    dest = _cache_path(word, voice)
    if not dest.exists():
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
