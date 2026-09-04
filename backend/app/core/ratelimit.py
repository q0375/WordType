"""内存限流（ADR-6）：登录失败锁定（用户名+IP，5 次/10min，不改变响应）；注册 IP 10 次/小时。"""

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


_limit = _SlidingWindow()


def login_failed(username: str, ip: str) -> bool:
    """记录登录失败；返回是否已达锁定（5 次/10 分钟）。"""
    return not _limit.hit(f"login:{username}:{ip}", limit=5, window_s=600)


def register_allowed(ip: str) -> bool:
    return _limit.hit(f"register:{ip}", limit=10, window_s=3600)
