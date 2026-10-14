"""导入链路自动化测试（含单元策略 / 编码 / 边界 / 权限 / 回环）。

用法（后端需已启动）：
    cd backend && .venv/Scripts/python.exe scripts/test_import.py
可选：WT_BASE=http://127.0.0.1:8000/api/v1 指定地址。
"""
import io
import os
import uuid

import httpx

BASE = os.environ.get("WT_BASE", "http://127.0.0.1:8000/api/v1")

PASS, FAIL = [], []


def check(label, cond, extra=""):
    if cond:
        PASS.append(label)
        print(f"  ✓ {label} {extra}")
    else:
        FAIL.append(f"{label} {extra}")
        print(f"  ✗ {label} {extra}")


def upload_csv(c, tok, text, book_name=None, target_book_id=None, filename="t.csv", encoding="utf-8"):
    data = {}
    if book_name:
        data["book_name"] = book_name
    if target_book_id:
        data["target_book_id"] = str(target_book_id)
    payload = ("\ufeff" + text).encode(encoding) if encoding != "gbk" else text.encode("gbk")
    return c.post(
        "/books/import",
        headers={"Authorization": f"Bearer {tok}"},
        data=data,
        files={"file": (filename, payload, "text/csv")},
    )


def confirm(c, tok, token, **kw):
    params = {"token": token, "duplicate_strategy": kw.pop("duplicate_strategy", "skip")}
    params.update(kw)
    return c.post("/books/import/confirm", headers={"Authorization": f"Bearer {tok}"}, params=params)


def find_book(c, tok, name):
    r = c.get("/books", headers={"Authorization": f"Bearer {tok}"}, params={"q": name, "page_size": 200})
    for b in r.json()["items"]:
        if b["name"] == name:
            return b
    return None


def chapters(c, tok, bid):
    r = c.get(f"/books/{bid}/chapters", headers={"Authorization": f"Bearer {tok}"}, params={"page_size": 500})
    return r.json()["items"]


def words(c, tok, bid, chapter_id=None, page_size=500):
    p = {"book_id": bid, "page_size": page_size}
    if chapter_id:
        p["chapter_id"] = chapter_id
    r = c.get("/words", headers={"Authorization": f"Bearer {tok}"}, params=p)
    return r.json()["items"]


def structure(c, tok, bid):
    """→ [(单元名, [(拼写, 时限秒), ...]), ...]"""
    out = []
    for ch in chapters(c, tok, bid):
        ws = [(w["spelling"], w.get("time_limit_s")) for w in words(c, tok, bid, ch["id"])]
        out.append((ch["name"], ws))
    return out


def main():
    c = httpx.Client(base_url=BASE, timeout=60)
    garbage = []

    # ---------- 登录 ----------
    r = c.post("/auth/login", json={"username": "admin", "password": "admin123456"})
    assert r.status_code == 200, r.text
    tok = r.json()["token"]
    H = {"Authorization": f"Bearer {tok}"}

    def newbook(prefix):
        nm = f"__it_{prefix}_{uuid.uuid4().hex[:6]}"
        garbage.append(nm)
        return nm

    print("\n[1] 表头识别 / 缺列 / 空值")
    nm = newbook("cols")
    csv_text = "章节,单词,释义,音标,例句,时限秒\nU1,chair,椅子,,The chair is comfortable.,3\nU1,apple,苹果,/ˈæpl/,,\n"
    r = upload_csv(c, tok, csv_text, book_name=nm)
    check("上传返回 200", r.status_code == 200, r.text[:120])
    up = r.json()
    check("识别到单元列", up.get("ok_rows") == 2)
    check("有表头时无体检告警", not up.get("warnings"), str(up.get("warnings")))
    confirm(c, tok, up["preview_token"], unit_mode="file")
    bid = find_book(c, tok, nm)["id"]
    st = structure(c, tok, bid)
    check("单元名与词序一致", st == [("U1", [("chair", 3), ("apple", None)])], str(st))

    print("\n[2] 只给「单词,释义」两列（无音标/例句/章节）")
    nm = newbook("two")
    r = upload_csv(c, tok, "单词,释义\nzebra,斑马\n", book_name=nm)
    up = r.json()
    confirm(c, tok, up["preview_token"], unit_mode="file")
    bid = find_book(c, tok, nm)["id"]
    st = structure(c, tok, bid)
    check("落默认章节且字段为空", st == [("默认章节", [("zebra", None)])], str(st))

    print("\n[3] 无表头 + 疑似错位 → 体检告警")
    nm = newbook("noheader")
    r = upload_csv(c, tok, "Unit1,egg,鸡蛋\n", book_name=nm)
    up = r.json()
    ws = up.get("warnings") or []
    check("无表头告警", any("未在首行识别到表头" in w for w in ws), str(ws))
    check("错位告警", any("疑似列错位" in w for w in ws), str(ws))
    # 有表头的同一份数据不应告警
    r2 = upload_csv(c, tok, "章节,单词,释义\nUnit1,egg,鸡蛋\n", book_name=newbook("hdr"))
    check("补表头后无告警", not (r2.json().get("warnings") or []), str(r2.json().get("warnings")))

    print("\n[4] 引号字段（含逗号 / 换行 / 引号）保真")
    nm = newbook("quote")
    tricky = '单词,释义\n"a,b",含逗号\n"line1\nline2",含换行\n"quote""s",含引号\n'
    r = upload_csv(c, tok, tricky, book_name=nm)
    up = r.json()
    confirm(c, tok, up["preview_token"], unit_mode="file")
    bid = find_book(c, tok, nm)["id"]
    sp = [w["spelling"] for w in words(c, tok, bid)]
    check("含逗号字段", "a,b" in sp, str(sp))
    check("含换行字段", "line1\nline2" in sp, str(sp))
    check("转义引号字段", 'quote"s' in sp, str(sp))

    print("\n[5] 编码：GBK / UTF-8 无 BOM")
    for enc, label in (("gbk", "GBK"), ("utf-8", "UTF-8")):
        nm = newbook("enc")
        r = upload_csv(c, tok, "单词,释义\napple,苹果\n", book_name=nm, encoding=enc)
        up = r.json()
        confirm(c, tok, up["preview_token"], unit_mode="file")
        bid = find_book(c, tok, nm)["id"]
        mn = words(c, tok, bid)[0]["meaning"]
        check(f"{label} 正确解码", mn == "苹果", mn)

    print("\n[6] 单元策略：file 保序 / size 打散 / count 数量")
    base = "章节,单词,释义\n" + "\n".join(f"U{i // 5},w{i},释义{i}" for i in range(20))
    nm = newbook("modefile")
    up = upload_csv(c, tok, base, book_name=nm).json()
    confirm(c, tok, up["preview_token"], unit_mode="file")
    bid = find_book(c, tok, nm)["id"]
    st = structure(c, tok, bid)
    check("file: 4 个单元且顺序为 U0..U3", [n for n, _ in st] == ["U0", "U1", "U2", "U3"], str([n for n, _ in st]))
    check("file: 单元内词序与文件一致", st[0][1] == [("w0", None), ("w1", None), ("w2", None), ("w3", None), ("w4", None)], str(st[0][1]))
    nm = newbook("modecount")
    up = upload_csv(c, tok, base, book_name=nm).json()
    confirm(c, tok, up["preview_token"], unit_count=4)
    bid = find_book(c, tok, nm)["id"]
    check("count: 单元数为 4", len(chapters(c, tok, bid)) == 4, str(len(chapters(c, tok, bid))))

    print("\n[7] 重复策略：skip / overwrite")
    nm = newbook("dup")
    up = upload_csv(c, tok, "单词,释义,时限秒\nbeta,测试,5\n", book_name=nm).json()
    confirm(c, tok, up["preview_token"], unit_mode="file")
    bid = find_book(c, tok, nm)["id"]
    up2 = upload_csv(c, tok, "单词,释义,时限秒\nbeta,新释义,9\ngamma,伽马,\n", target_book_id=bid).json()
    check("同库重复被识别为 dup", up2.get("dup_rows") == 1 and up2.get("ok_rows") == 1, str(up2))
    # skip：beta 保持旧值
    confirm(c, tok, up2["preview_token"], duplicate_strategy="skip", unit_mode="none")
    ws = {w["spelling"]: w for w in words(c, tok, bid)}
    check("skip: 旧释义保留", ws["beta"]["meaning"] == "测试" and ws["beta"]["time_limit_s"] == 5, str(ws.get("beta")))
    check("skip: 新词仍入库", "gamma" in ws)
    # overwrite：beta 更新
    up3 = upload_csv(c, tok, "单词,释义,时限秒\nbeta,新释义,9\n", target_book_id=bid).json()
    confirm(c, tok, up3["preview_token"], duplicate_strategy="overwrite", unit_mode="none")
    ws = {w["spelling"]: w for w in words(c, tok, bid)}
    check("overwrite: 释义与时限被更新", ws["beta"]["meaning"] == "新释义" and ws["beta"]["time_limit_s"] == 9, str(ws.get("beta")))

    print("\n[8] 错误行与上限")
    nm = newbook("err")
    up = upload_csv(c, tok, "单词,释义\nok1,好\n,缺单词\n缺释义,\n" + "x" * 101 + ",超长\nok2,好\nok2,重复\n", book_name=nm).json()
    # 4 个错误行：缺单词 / 缺释义 / 超长 / 文件内重复（ok2）
    check("错误行数=4", up.get("error_rows") == 4, str(up.get("error_rows")))
    check("有效行=2", up.get("ok_rows") == 2, str(up.get("ok_rows")))
    nm = newbook("limit")
    big = "单词,释义\n" + "\n".join(f"w{i},m{i}" for i in range(20001))
    r = upload_csv(c, tok, big, book_name=nm)
    check("20001 行被拒", r.status_code == 422 and r.json().get("code") == "ROW_LIMIT_EXCEEDED", r.text[:120])
    garbage.remove(nm)  # 未建库

    print("\n[9] 导出 → 再导入 回环")
    nm = newbook("rt")
    src = "章节,单词,释义,音标,例句,时限秒\nA,alpha,阿尔法,,,5\nA,beta,贝塔,/ˈbeɪtə/,,3\nB,gamma,伽马,,Gamma.,4\n"
    up = upload_csv(c, tok, src, book_name=nm).json()
    confirm(c, tok, up["preview_token"], unit_mode="file")
    bid = find_book(c, tok, nm)["id"]
    before = structure(c, tok, bid)
    csv_out = c.get(f"/books/{bid}/export", headers=H).text.lstrip("\ufeff")
    check("导出含章节列", csv_out.splitlines()[0].startswith("章节,单词,释义"), csv_out.splitlines()[0])
    nm2 = newbook("rt2")
    up2 = upload_csv(c, tok, csv_out, book_name=nm2).json()
    confirm(c, tok, up2["preview_token"], unit_mode="file")
    bid2 = find_book(c, tok, nm2)["id"]
    check("回环后结构一致", structure(c, tok, bid2) == before, f"{structure(c, tok, bid2)} != {before}")

    print("\n[10] 权限：普通用户不能导入他人词库")
    uname = f"it_{uuid.uuid4().hex[:6]}"
    ru = c.post("/auth/register", json={"username": uname, "password": "test1234ab"})
    assert ru.status_code == 200, ru.text
    utok = ru.json()["token"]
    UH = {"Authorization": f"Bearer {utok}"}
    r = c.post("/books/import", headers=UH, data={"target_book_id": str(bid)},
               files={"file": ("t.csv", "单词,释义\nxx,yy\n".encode("utf-8"), "text/csv")})
    check("他人词库导入被拒", r.status_code in (403, 404), f"{r.status_code} {r.text[:80]}")

    print("\n[11] 大文件：10000 行 / 500 单元")
    import time
    nm = newbook("big")
    rows = ["章节,单词,释义,时限秒"]
    rows += [f"U{i % 500},w{i:05d},m{i},5" for i in range(10000)]
    t0 = time.time()
    up = upload_csv(c, tok, "\n".join(rows), book_name=nm).json()
    t1 = time.time()
    r = confirm(c, tok, up["preview_token"], unit_mode="file")
    t2 = time.time()
    bid = find_book(c, tok, nm)["id"]
    n_ch = len(chapters(c, tok, bid))
    r2 = c.get("/words", headers=H, params={"book_id": bid, "page_size": 1})
    check("1万行入库", r2.json()["total"] == 10000 and r.status_code == 201, str(r2.json()["total"]))
    check("500 个单元", n_ch == 500, str(n_ch))
    print(f"    解析 {t1 - t0:.2f}s / 入库 {t2 - t1:.2f}s / 单元 {n_ch}")

    print("\n[12] 分页一致性（与总数对账）")
    r1 = c.get("/words", headers=H, params={"book_id": bid, "page": 1, "page_size": 50})
    r2 = c.get("/words", headers=H, params={"book_id": bid, "page": 200, "page_size": 50})
    check("末页仍有数据", r1.json()["total"] == 10000 and len(r2.json()["items"]) == 50, str(r2.json().get("total")))

    print("\n[13] 未加引号逗号的列修复（时限秒不再丢失/显示自动）")
    nm = newbook("rp")
    csv_txt = (
        "章节,单词,释义,音标,例句,时限秒\n"
        'Python代码,"gs.fit(X, Y)",用网格搜索对象gs拟合特征X与目标Y,,示例：gs.fit(X, y),8\n'
        "英语单词,dog,犬,狗,/dɒɡ/,a dog,7\n"
        "英语单词,sun,太阳,,The sun, the moon, and the stars,9\n"
        "英语单词,dog2,犬,犬科,/d/,A dog, loyal,6\n"
        "英语单词,cat,猫,,5\n"
    )
    up = upload_csv(c, tok, csv_txt, book_name=nm).json()
    check("体检提示逗号已修复", any("未加引号的英文逗号" in w for w in up.get("warnings", [])), str(up.get("warnings")))
    check("体检提示少列对齐", any("比表头少列" in w for w in up.get("warnings", [])), str(up.get("warnings")))
    confirm(c, tok, up["preview_token"], unit_mode="file")
    bid = find_book(c, tok, nm)["id"]
    ws = {w["spelling"]: w for w in words(c, tok, bid)}
    g = ws.get("gs.fit(X, Y)", {})
    check("例句1逗号: 时限=8 例句逐字还原", g.get("time_limit_s") == 8 and g.get("example") == "示例：gs.fit(X, y)", str(g))
    d = ws.get("dog", {})
    check("释义1逗号: 释义=犬,狗 时限=7", d.get("meaning") == "犬,狗" and d.get("time_limit_s") == 7, str(d))
    sn = ws.get("sun", {})
    check("例句2逗号: 还原 时限=9", sn.get("example") == "The sun, the moon, and the stars" and sn.get("time_limit_s") == 9, str(sn))
    d2 = ws.get("dog2", {})
    check("释义+例句各有逗号: 同时还原 时限=6", d2.get("meaning") == "犬,犬科" and d2.get("example") == "A dog, loyal" and d2.get("time_limit_s") == 6, str(d2))
    ct = ws.get("cat", {})
    check("少列行按时限对齐: 时限=5", ct.get("time_limit_s") == 5, str(ct))

    # ---------- 清理 ----------
    print("\n清理测试词库…")
    for nm in garbage:
        b = find_book(c, tok, nm)
        if b:
            c.delete(f"/books/{b['id']}", headers=H)
    # 注销测试账号
    r = c.post("/account/deactivate", headers=UH)
    check("测试账号已注销", r.status_code == 204, f"{r.status_code} {r.text[:80]}")

    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print("  FAILED:", f)
    if FAIL:
        raise SystemExit(1)
    print("ALL IMPORT TESTS PASSED")


if __name__ == "__main__":
    main()
