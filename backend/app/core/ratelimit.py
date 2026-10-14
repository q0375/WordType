"""内存限流（ADR-6）：登录失败锁定（用户名+IP，5 次/10min，不改变响应）；注册 IP 120 次/小时。"""

import time
from collections import defaultdict, deque


class _SlidingWindow:
    def __init__(self) -> None:
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str, limit: int, window_s: float) -> bool:
        """记录一次并返回是否放行。"""
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True

    def peek(self, key: str, limit: int, window_s: float) -> bool:
        """不记录，仅判断当前窗口内是否仍在允许范围内。"""
        now = time.monotonic()
        q = self.hits.get(key)
        if not q:
            return True
        while q and now - q[0] > window_s:
            q.popleft()
        return len(q) < limit

    def clear(self, key: str) -> None:
        """清空某 key 的窗口（登录成功后调用）。"""
        self.hits.pop(key, None)


_limit = _SlidingWindow()

LOGIN_LIMIT = 5
LOGIN_WINDOW_S = 600


def _login_key(username: str, ip: str) -> str:
    return f"login:{username}:{ip}"


def login_allowed(username: str, ip: str) -> bool:
    """判定是否允许继续尝试登录（不计数）。已达锁定则返回 False。"""
    return _limit.peek(_login_key(username, ip), limit=LOGIN_LIMIT, window_s=LOGIN_WINDOW_S)


def login_failed(username: str, ip: str) -> bool:
    """记录登录失败；返回是否已达锁定（5 次/10 分钟）。"""
    return not _limit.hit(_login_key(username, ip), limit=LOGIN_LIMIT, window_s=LOGIN_WINDOW_S)


def login_succeeded(username: str, ip: str) -> None:
    """登录成功：清空该 用户名+IP 的失败计数。"""
    _limit.clear(_login_key(username, ip))


def register_allowed(ip: str) -> bool:
    # 2026-09-21 开放测试：10→120 次/小时/IP（原 10 次在开放测试时不够用）
    return _limit.hit(f"register:{ip}", limit=120, window_s=3600)


def tts_allowed(ip: str) -> bool:
    """匿名 TTS 合成限流：每 IP 每分钟 60 次（缓存命中不计上游，此处仍统一限流）。"""
    return _limit.hit(f"tts:{ip}", limit=60, window_s=60)
