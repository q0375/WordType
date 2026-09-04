"""后端全链路冒烟测试脚本（本地开发用）。"""
import json
import uuid

import httpx

BASE = "http://127.0.0.1:8000/api/v1"


def main() -> None:
    c = httpx.Client(base_url=BASE, timeout=30)
    uname = f"smoke_{uuid.uuid4().hex[:6]}"

    r = c.post("/auth/register", json={"username": uname, "password": "test1234ab"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    uid = r.json()["user"]["id"]
    h = {"Authorization": f"Bearer {token}"}
    print("register ok, uid:", uid)

    # 未带 token
    assert c.get("/books").status_code == 401
    # user_id 红线
    r = c.post("/study/self-rate", headers=h, json={"user_id": 999, "word_id": 1, "rating": "know", "request_id": "x"})
    assert r.status_code == 400 and r.json()["code"] == "USER_ID_FORBIDDEN", r.text
    print("auth guard ok")

    books = c.get("/books?tab=public", headers=h).json()
    book_id = books["items"][0]["id"]
    chs = c.get(f"/books/{book_id}/chapters", headers=h).json()["items"]
    ch1 = chs[0]["id"]
    words = c.get(f"/words?book_id={book_id}&chapter_id={ch1}", headers=h).json()["items"]
    assert len(words) >= 20
    print("books/words ok:", len(words))

    # 学习：自评 + 默写
    r = c.post("/study/self-rate", headers=h, json={"word_id": words[0]["id"], "rating": "know", "request_id": str(uuid.uuid4())})
    assert r.json()["proficiency"] == 60 and r.json()["next_review_at"], r.text
    # 重放同 request_id
    rid = str(uuid.uuid4())
    c.post("/study/self-rate", headers=h, json={"word_id": words[1]["id"], "rating": "unknown", "request_id": rid})
    r2 = c.post("/study/self-rate", headers=h, json={"word_id": words[1]["id"], "rating": "know", "request_id": rid})
    assert r2.json()["proficiency"] == 0, r2.text  # 重放返回首次快照
    r = c.post("/study/dictation", headers=h, json={"word_id": words[2]["id"], "typed": words[2]["spelling"][:-1] + "x", "duration_ms": 3000, "request_id": str(uuid.uuid4())})
    assert r.json()["result"] == "near" and r.json()["proficiency"] == 30, r.text
    print("study ok (self-rate/dictation/replay)")

    # 练习
    r = c.post("/practice/session", headers=h, json={"chapter_ids": [ch1], "types": ["dictation", "choice", "listening", "cloze"], "group_size": 12})
    qs = r.json()["questions"]
    assert len(qs) == 12, len(qs)
    for q in qs[:3]:
        body = {"qid": q["qid"], "word_id": q["word_id"], "type": q["type"], "request_id": str(uuid.uuid4())}
        if q["type"] == "choice":
            body["choice_key"] = q["payload"]["options"][0]["key"]
        else:
            body["typed"] = "wrongguess"
        r = c.post("/practice/answer", headers=h, json=body)
        assert r.status_code == 200 and r.json()["result"] in ("correct", "near", "wrong"), r.text
    print("practice ok")

    # 复习：明天到期是自评+3d 词；先把一个词设为今天到期 → 造队列
    # 用 words[1]（unknown: next=+1d 明天）——物化只取 <= today。直接自评 know 的词是 +3d。
    # 这里验证空队列 + 额度返回即可
    r = c.get("/review/today", headers=h)
    assert "quota" in r.json() and "queue" in r.json()
    print("review today ok, queue:", len(r.json()["queue"]))

    # 考核
    r = c.post("/exam/start", headers=h, json={"chapter_id": ch1, "type_counts": {"dictation": 5, "choice": 3}, "time_limit_min": 10, "pass_score": 60, "loose_match": True})
    paper = r.json()
    assert "paper_id" in paper, r.text
    # 二次开卷 → 409
    r2 = c.post("/exam/start", headers=h, json={"chapter_id": ch1, "type_counts": {"dictation": 2}, "time_limit_min": 5, "pass_score": 60, "loose_match": True})
    assert r2.status_code == 409 and r2.json()["code"] == "EXAM_ALREADY_ACTIVE", r2.text
    answers = []
    for q in paper["questions"]:
        a = {"qid": q["qid"]}
        if q["type"] == "choice":
            a["choice_key"] = q["payload"]["options"][0]["key"]
        else:
            a["typed"] = "zzz"
        answers.append(a)
    key = str(uuid.uuid4())
    r = c.post("/exam/submit", headers={**h, "Idempotency-Key": key}, json={"paper_id": paper["paper_id"], "answers": answers, "duration_ms": 120000})
    assert r.status_code == 200, r.text
    score1 = r.json()["score"]
    r2 = c.post("/exam/submit", headers={**h, "Idempotency-Key": key}, json={"paper_id": paper["paper_id"], "answers": answers, "duration_ms": 120000})
    assert r2.status_code == 200 and r2.json()["score"] == score1
    assert r2.headers.get("x-idempotent-replay") == "true", dict(r2.headers)
    recs = c.get("/exam/records", headers=h).json()
    assert recs["total"] == 1
    print("exam ok (scoring/idempotent-replay/records), score:", score1)

    # 游戏：错词入错题本
    r = c.post("/game/start", headers=h, json={"chapter_ids": [ch1], "difficulty": "normal", "mode": "endless"})
    gs = r.json()
    w0 = gs["words"][0]
    seq = []
    for i, w in enumerate(gs["words"][:5]):
        destroyed = i % 2 == 0
        wl = len(w["spelling"])
        seq.append({
            "word_id": w["word_id"], "typed": w["spelling"] if destroyed else "xxx",
            "first_key_ms": 0, "last_key_ms": wl * 50 if destroyed else 0, "destroyed": destroyed,
        })
    correct = [s for s in seq if s["destroyed"]]
    # 提交 999 分制造偏差，验证服务端复核重算
    r = c.post("/game/submit", headers={**h, "Idempotency-Key": str(uuid.uuid4())}, json={
        "session_id": gs["session_id"], "difficulty": "normal", "mode": "endless",
        "duration_ms": 120000, "score": 999, "max_combo": 3, "correct_count": len(correct), "words": seq,
    })
    assert r.status_code == 200 and r.json()["valid"] is True, r.text
    # 偏差 >5% → 服务端复核分（999 vs 服务端重算）
    server_score = r.json()["score"]
    assert server_score != 999
    wb = c.get("/wrongbook", headers=h).json()
    assert wb["total"] >= 1, wb
    print("game ok (validated, server-rescored), score:", server_score, "| wrongbook:", wb["total"])

    # 错题本 pin/resolve
    wid = wb["items"][0]["word_id"]
    assert c.patch(f"/wrongbook/{wid}", headers=h, json={"pinned": 1}).status_code == 204
    wb = c.get("/wrongbook?resolved=0&pinned=1", headers=h).json()
    assert wb["total"] == 1
    print("wrongbook ops ok")

    # 设置 + 仪表盘 + 建议 + 导出
    s = c.get("/settings", headers=h).json()
    assert s["daily_new_limit"] == 20
    s2 = c.put("/settings", headers=h, json={"daily_new_limit": 30, "dictation_show_seconds": 2}).json()
    assert s2["daily_new_limit"] == 30 and s2["dictation_show_seconds"] == 2
    d = c.get("/stats/dashboard", headers=h).json()
    assert d["today"]["new_learned"] >= 2
    a = c.get("/advice", headers=h).json()
    assert len(a["items"]) <= 3
    z = c.get("/export/account", headers=h)
    assert z.status_code == 200 and z.content[:2] == b"PK"
    print("settings/dashboard/advice/export ok")

    # 改密 → pwd_ver 失效
    r = c.post("/auth/password", headers=h, json={"old_password": "test1234ab", "new_password": "newpass99x"})
    assert r.status_code == 200
    old_tok_401 = c.get("/settings", headers={"Authorization": f"Bearer {token}"})
    assert old_tok_401.status_code == 401 and old_tok_401.json()["code"] == "AUTH_PWD_CHANGED", old_tok_401.text
    r = c.post("/auth/login", json={"username": uname, "password": "newpass99x"})
    assert r.status_code == 200
    print("password/pwd_ver ok")

    print("\nALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
