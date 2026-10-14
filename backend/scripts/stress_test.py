"""并发与数据量压力测试（本地开发用，会自建自删测试词库）。

    cd backend && .venv/Scripts/python.exe scripts/stress_test.py
    .venv/Scripts/python.exe scripts/stress_test.py --threads 32 --requests 20 --rows 20000

只读接口走真实词库；导入场景会建临时词库并在结束后删除。
"""
import argparse
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx

BASE = "http://127.0.0.1:8000/api/v1"


class Runner:
    def __init__(self, threads, requests_):
        self.threads = threads
        self.n = requests_
        c = httpx.Client(base_url=BASE, timeout=120)
        tok = c.post("/auth/login", json={"username": "admin", "password": "admin123456"}).json()["token"]
        self.H = {"Authorization": f"Bearer {tok}"}
        self.c = c

    def run(self, label, fn, threads=None, per=None):
        threads = threads or self.threads
        per = per or self.n
        res = []

        def one(_):
            t0 = time.time()
            try:
                r = fn()
                ok = r.status_code
                err = "" if r.status_code < 400 else r.text[:100]
            except Exception as e:  # noqa: BLE001
                ok, err = 999, repr(e)[:100]
            return ok, time.time() - t0, err

        t0 = time.time()
        with ThreadPoolExecutor(threads) as ex:
            res = list(ex.map(one, range(threads * per)))
        wall = time.time() - t0
        codes = {}
        errs = []
        for ok, dt, err in res:
            codes[ok] = codes.get(ok, 0) + 1
            if err:
                errs.append(err)
        dts = sorted(dt for _, dt, _ in res)
        p95 = dts[max(0, int(len(dts) * 0.95) - 1)]
        print(f"[{label}] n={len(res)} wall={wall:.1f}s QPS={len(res)/wall:.0f} "
              f"avg={statistics.mean(dts)*1000:.0f}ms P95={p95*1000:.0f}ms status={codes}")
        for e in errs[:3]:
            print("     err:", e)
        return not any(ok >= 500 for ok, _, _ in res)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=12)
    ap.add_argument("--requests", type=int, default=10)
    ap.add_argument("--rows", type=int, default=10000, help="大文件导入行数（上限 20000）")
    args = ap.parse_args()

    r = Runner(args.threads, args.requests)
    ok = True

    # 取一个真实词库做读压测
    books = r.c.get("/books", headers=r.H, params={"page_size": 20}).json()["items"]
    bid = books[0]["id"]
    chs = r.c.get(f"/books/{bid}/chapters", headers=r.H, params={"page_size": 50}).json()["items"]
    ch1 = chs[0]["id"] if chs else None
    w = r.c.get("/words", headers=r.H, params={"book_id": bid, "page_size": 1}).json()["items"][0]["id"]

    print(f"压测目标：book={bid} chapter={ch1} word={w}\n")
    ok &= r.run("读 GET /books", lambda: r.c.get("/books", headers=r.H, params={"page_size": 50}))
    ok &= r.run("读 GET /chapters", lambda: r.c.get(f"/books/{bid}/chapters", headers=r.H, params={"page_size": 200}))
    ok &= r.run("读 GET /words", lambda: r.c.get("/words", headers=r.H, params={"book_id": bid, "page_size": 50}))
    ok &= r.run("写 PATCH /words", lambda: r.c.patch(f"/words/{w}", headers=r.H, json={"time_limit_s": 9}))

    # 并发导入
    names = [f"__stress_{uuid.uuid4().hex[:6]}" for _ in range(3)]
    n_rows = min(args.rows, 20000)

    def do_import(nm, rows):
        text = "章节,单词,释义,时限秒\n" + "\n".join(f"U{i % 50},s{i:06d},m{i},5" for i in range(rows))
        up = r.c.post("/books/import", headers=r.H, data={"book_name": nm},
                      files={"file": ("s.csv", ("\ufeff" + text).encode("utf-8"), "text/csv")})
        if up.status_code != 200:
            return up.status_code, up.text[:120]
        cf = r.c.post("/books/import/confirm", headers=r.H,
                      params={"token": up.json()["preview_token"], "duplicate_strategy": "skip", "unit_mode": "file"})
        return cf.status_code, ""

    t0 = time.time()
    with ThreadPoolExecutor(3) as ex:
        outs = list(ex.map(lambda nm: do_import(nm, n_rows), names))
    print(f"[并发导入 3×{n_rows} 行] wall={time.time()-t0:.1f}s status={[o[0] for o in outs]}")
    ok &= all(o[0] == 201 for o in outs)

    # 大文件单次
    t0 = time.time()
    nm = f"__stress_big_{uuid.uuid4().hex[:6]}"
    st, err = do_import(nm, n_rows)
    print(f"[单批 {n_rows} 行] wall={time.time()-t0:.1f}s status={st} {err}")

    for nm in names + [nm]:
        bl = r.c.get("/books", headers=r.H, params={"q": nm, "page_size": 200}).json()["items"]
        for b in bl:
            if b["name"] == nm:
                r.c.delete(f"/books/{b['id']}", headers=r.H)

    print("\nSTRESS OK" if ok else "\nSTRESS FAILED (存在 5xx)")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
