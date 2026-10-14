# AI 建议引擎（D28）实测报告

> **结论先行（12:35 最终修订）**：AI 建议**已成功产出并落库**。在清除一个错误的系统代理环境变量、
> 并把模型名从被限流的 `deepseek-flash` 换成 `deepseek-v4-flash` 之后，
> 12:35 通过真实 HTTP 接口完成了端到端验证，`advice_cache` 出现**本项目历史上第一条 `engine='llm'` 记录**。
>
> 此前"产出 0 次"的结论**只对历史落库记录成立**，失败共叠加了三层原因：
>
> | 层 | 原因 | 状态 |
> |---|---|---|
> | ① | AES-GCM 密钥轮换 → 旧密文 `InvalidTag` | 用户 12:19 重新录入，**已修复** |
> | ② | **系统环境变量 `HTTP_PROXY=127.0.0.1:7892` 指向一个未监听的端口** | **本次根因**，清除后即通 |
> | ③ | 配置的 `deepseek-flash` 端点被限流，成功率仅 1/6 | 换 `deepseek-v4-flash` 后 5/6 |
>
> 但**"能连通"不等于"能用"**：实测发现 AI 产出的 `action.params` 用的是**单词拼写（`words`）而非 `word_id`**，
> 前端 `actionRoute()` 只认 `word_ids` → **「去执行」静默跳全量练习页**（详见 4.5.2，已立 P0）。
>
> 历史统计事实保留：`engine='llm'` 在 12:35 之前为 **0/51**。

本报告分五部分：① 落库事实取证；② 失败根因定位（三层）；③ AI 与规则引擎的差异对照；④ 附带缺陷；⑤ 首次真实产出实录。

---

## 〇、最终修订：为什么"连不通"（2026-10-04 12:25 完整根因）

这是本报告最重要的一节。前两版报告都把失败归因为"环境限制"，**这个判断是错的**——
真实原因是本机存在一个**指向死端口的代理环境变量**。

### 0.1 逐层实测证据

| 检查项 | 命令 | 结果 |
|---|---|---|
| 环境变量 | `env \| grep -i proxy` | `HTTP_PROXY=http://127.0.0.1:7892`（**四个变体全设**） |
| 代理端口是否在听 | `/dev/tcp/127.0.0.1/7892` | **closed**（`netstat` 只有 `SYN_SENT`/`CLOSE_WAIT` 残留） |
| 走代理访问目标 | `curl -x http://127.0.0.1:7892 https://token.sensenova.cn/...` | `http_code=000`，`exit=7`（连接被拒），2.0 s |
| **绕过代理直连** | `curl --noproxy '*' https://token.sensenova.cn/v1/models` | **`http_code=401`，0.24 s** ← 网络本身完全可达 |
| 对照：其他站点直连 | `curl --noproxy '*' https://www.baidu.com` | `200`，0.089 s |
| Python 复现（项目 venv） | `httpx.get(...)` 默认参数 | `ConnectError: [WinError 10061] 目标计算机积极拒绝` |
| Python 绕过代理 | `httpx.get(..., trust_env=False)` | **`HTTP 401 {"error":{"code":16,"message":"Forbidden"}}`** |
| 清除代理变量后跑真实 `generate()` | `env -u HTTP_PROXY -u HTTPS_PROXY ...` | **✅ 成功，5027 ms，产出 3 条建议** |

### 0.2 机理：为什么项目代码"看起来没问题"却连不上

`llm_advice._call` 第 90 行：

```python
async with httpx.AsyncClient(timeout=cfg.timeout_s) as client:
```

**没有传 `trust_env=False`**，而 httpx 的 `Client` 默认 `trust_env=True`，会**自动读取**
`HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` 环境变量并把所有外发请求导向该地址。
`.env` 里**没有任何 proxy 配置项**，所以项目的代理行为完全由操作系统/Shell 环境决定，
代码里无从感知也无从覆盖。

失败信息 `ConnectError: All connection attempts failed` 正是 httpx 依次尝试
`127.0.0.1:7892` 全部失败后的汇总措辞——**它看起来像"目标站点不可达"，实际是"本地代理没启动"**，
这是极具误导性的错误语义（详见第六节论文论点）。

### 0.3 第三个独立问题：配置的模型端点在限流

凭据可用后，`deepseek-flash`（当前配置值）实测成功率极低：

| 模型 | 成功/6 | 延迟 | 失败码 |
|---|---|---|---|
| `deepseek-flash`（当前配置） | **1/6** | 1058 ms | `RateLimitExceeded.EndpointTPMExceeded` ×5 |
| **`deepseek-v4-flash`** | **5/6** | 1453~1994 ms | `ConnectError` ×1（本地代理竞争） |

即**即使解决了代理问题，当前配置的模型名本身也有 ~83% 的失败率**。
`generate()` 内部只重试 1 次，实测 3 次调用中 2 次仍以 `HTTP 429` 告终。
**建议把模型名改为 `deepseek-v4-flash`。**

### 0.4 立即可用的修复（按代价排序）

```bash
# 方案 A（推荐，一次性）：本机清掉死代理变量
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy

# 方案 B：把代理指向真实运行的代理端口（如 Clash 的 7890）
set HTTP_PROXY=http://127.0.0.1:7890

# 方案 C（代码级，最稳）：让 llm_advice 不受系统代理影响
#   httpx.AsyncClient(timeout=cfg.timeout_s, trust_env=False)
# 或按需显式注入：AsyncClient(proxy=cfg.proxy_url)
```

> ⚠️ **注意**：若目标部署环境**确实需要**代理访问外网（如境外模型 API），方案 C 的
> `trust_env=False` 会适得其反，正确做法是方案 B 或在 `user_ai_config` 增加显式 `proxy_url` 字段。
> 本项目生产服务器（腾讯云轻量，国内）访问 `token.sensenova.cn` **无需代理**，故方案 C 安全。

---

## 一、落库事实（第一手证据）

数据源：`backend/data/wordtype.db` 表 `advice_cache`（每用户每日一条，含 `engine` 字段标记产出方）。

> ⚠️ 复核说明：本报告初稿写于 12:15，彼时 `advice_cache` 为 49 条。**12:19 用户重新配置了
> uid=1 的 AI 凭据**（`user_ai_config.updated_at` 变为 `2026-10-04 12:19:19`），
> 复核时已增至 51 条。下表为**最新复核值**（`llm_success_records` 结论不变，仍为 0）。

| 指标 | 数值 |
|---|---|
| `advice_cache` 记录总数 | **51** |
| `engine = 'rule'`（规则引擎产出） | **51（100%）** |
| `engine = 'llm'`（AI 产出） | **0（0%）** |

`note` 字段（失败/降级原因）分类：

| note | 条数 | 含义 |
|---|---|---|
| （空） | 41 | 规则引擎正常路径，或未启用 AI |
| `InvalidTag: ` | 6 | **AI 调用失败，已回退规则引擎** —— 根因见第二节 |
| `AI 建议生成中，稍候自动更新…` | 2 | LLM 后台任务已调度，但结果从未落库（永远停在占位） |
| `AI 未配置完整（需要基地址、Key、模型名），已回退规则引擎` | 2 | 用户未填全凭据 |

**用户视角的实测记录**：uid=1（admin，学过 613 词）与 uid=2（学过 84 词）都曾配置过
`sensenova` 的 `deepseek-v4-flash` 并在设置页把建议引擎切到「AI」；两人的 `note` 均出现过
`AI 建议生成中，稍候自动更新…`，即**永远停留在占位状态，从未被 AI 结果覆盖**。

> 运行日志交叉印证（`uvicorn` stdout，非数据库推断）：
> ```
> {"ts":"2026-10-04 10:52:45,734","level":"WARNING","logger":"app.services.llm_advice",
>  "msg":"LLM advice attempt 1 failed: InvalidTag: "}
> {"ts":"2026-10-04 10:52:45,736","level":"WARNING","logger":"app.services.llm_advice",
>  "msg":"LLM advice attempt 2 failed: InvalidTag: "}
> ```
> 两条重试（`generate` 内部对瞬时故障重试一次）均以 `InvalidTag` 告终 —— **接口从未被真正调用**。

> 附：`user_ai_config` 现有 2 条密文（uid=1、uid=2），其中 uid=1 已于 12:19 重新录入。

---

## 二、失败根因：`InvalidTag` 不是接口错误，是密钥解密失败

`note` 里 6 次 `InvalidTag: ` 容易被误读为"第三方接口返回异常"。实际定位过程如下：

1. `InvalidTag` 检索命中 `cryptography/exceptions.py:35` → 它是 **AES-GCM 解密失败**异常，
   与 HTTP 响应无关（`_call` 里的 `httpx` 异常会被包成别的名字）。
2. 异常在 `llm_advice.generate` 的 `try` 内被吞成字符串，因此**接口从未被真正调用过**。
3. 用当前 `.env` 的 `AES_KEY` 直接解密库中两条 `api_key_enc` 密文：

```
uid=1 deepseek-v4-flash @ https://token.sensenova.cn/v1 updated=2026-09-04 -> FAIL InvalidTag
uid=2 deepseek-v4-flash @ https://token.sensenova.cn/v1 updated=2026-09-04 -> FAIL InvalidTag
```

****两条密文原本都无法用当前密钥解密**（uid=1 当时为 2026-09-04 的旧密文）。

### 2.0 复核：密钥修复后，暴露出**第二层**故障（重要）

2026-10-04 12:19，用户在设置页**重新录入了 uid=1 的凭据**。复核实测：

| 时点 | uid=1 密文 | 凭据探测 `llm_advice.test()` | `generate()` 实测 |
|---|---|---|---|
| 12:15（前） | `InvalidTag`（解不开） | — | — |
| **12:19（后）** | **OK `****jdMT`** | `ok=False`, `msg="All connection attempts failed"` | 耗时 4171 ms，`note="ConnectError: All connection attempts failed"` |

失败原因从 `InvalidTag` **变成了 `ConnectError`** —— 说明：

1. ✅ 密钥链路已修复，模型凭据（`api_base_url` / `api_key` / `model_name`）现可正常解密与使用；
2. ⚠️ **本条判断已于 12:25 被证伪**：当时记为"运行环境无法访问外网（环境限制）"，实为
   **本机 `HTTP_PROXY=127.0.0.1:7892` 指向一个未监听端口**。绕过代理后目标地址
   **0.24 s 返回 401**（完全可达）。完整证据见**第〇节**；
3. 🔴 **但这确实暴露了一个可观测性问题**：`generate()` 把
   「凭据失效（InvalidTag，用户必须重新录入）」和「网络不可达（ConnectError，用户什么都做不了）」
   **压成同一句话式的 `note` 字符串**返回，前端只把它当普通提示展示。
   两种失败的**用户处置动作完全相反**（一个要重新输 Key，一个只能重试/等网络），
   却被合并处理 —— 这是 2.1 之外第二个"降级机制掩盖了可操作信息"的例证。

> 因此，本项目 AI 建议链路当前的状态应表述为：
> **代码链路完整；密钥已修复；连通性障碍源于一个错误的系统代理环境变量（清除后即通）；
> 当前配置的 `deepseek-flash` 端点另有 ~83% 限流失败率。**
> 12:25 清除代理变量后已**首次成功产出真实 AI 建议**（见第五节）。

### 根因

`core/security.py` 的 `encrypt_secret` / `decrypt_secret` 用 `AES_KEY` 的**前 32 字节**作 AES-GCM 密钥
（`get_settings().aes_key.encode("utf-8")[:32]`）。密文生成于 2026-09-04，而当前 `.env` 的 `AES_KEY`
与之不匹配 —— 密钥在配置之后被**轮换或替换过**。AES-GCM 带认证标签，密钥一变，旧密文必然
`InvalidTag`。

### 这暴露了一个真实的设计缺陷（论文可写）

- **静默降级掩盖了故障**：`generate()` 捕获所有异常 → 返回 `(None, "InvalidTag: ")` →
  `_schedule_llm_advice` 把失败原因写进 `note` → 前端照常展示规则引擎建议。
  用户看到的是"AI 引擎已启用、建议正常显示"，**没有任何 UI 提示凭据已失效**。
- **`mask_secret` 同样吞异常**：密钥不匹配时 `decrypt_secret` 抛错被 `except` 吃掉，
  返回 `"****"`。于是设置页的「已配置 Key」掩码**看起来正常**（是 `****` 而非空），
  用户无法从界面察觉密钥已不可用 —— 实测 uid=1/2 的设置页正是这种状态。
- **`test()` 端点的探测同样失败**：`POST /misc/ai-config/test` 内部第一行就是 `decrypt_secret`，
  密钥失效时它会返回 `ok:false` + `InvalidTag`，而不是"凭据有效"。

**结论**：这是"密钥生命周期管理"缺失导致的**可复现故障**，不是模型能力问题。
修复方向：① 轮换 `AES_KEY` 时需保留旧密钥解密能力或提供重新录入引导；
② `decrypt_secret` 失败应**区分"凭据失效"与"接口失败"**并在前端显著提示；
③ `mask_secret` 应在解密失败时返回明确标记（如空串 + 状态位）而非 `"****"`。

### 2.1 附带发现：规则引擎的 `word_ids` 也会失效（已删除词未被过滤）

做幻觉实验时顺带发现对照组也非全对 —— 规则引擎给出的 16 个 `word_id` 里，
**有 2 个已失效**：

```
  706 -> affectionate   已删除(is_deleted=1)   <-- 失效
  861 -> marvelous      已删除(is_deleted=1)   <-- 失效
有效 14/16
```

根因在 `misc_service._generate_advice` 的 ① 号查询（错误率 >60%）：

```python
select(UserWordStat, Word).join(Word, Word.id == UserWordStat.word_id).where(
    UserWordStat.user_id == user.id,
    (UserWordStat.correct_count + UserWordStat.wrong_count) >= 5,
    UserWordStat.wrong_count * 1.0 / (...) > 0.6,
).limit(50)
```

**缺少 `Word.is_deleted == 0` 条件** —— 与项目内其他模块（如 `review_service.review_today`
显式写 `EXISTS (SELECT 1 FROM word w2 WHERE w2.id = s.word_id AND w2.is_deleted = 0)`）不一致。

后果：被删除的词仍会计入「共 16 个」这个数字，并出现在「去执行」的 `word_ids` 里，
点进去组卷时被 `_pool_words` 的 `is_deleted=0` 过滤掉 → **用户看到建议说 16 个词，实际只练到 14 个**。

> 这条与 AI 无关，是规则引擎自身的正确性缺陷；但它恰好印证了 3.5 节的核心论点：
> **"可执行动作的正确性"必须由服务端代码保证，不能指望任何生成方（含 LLM）正确产出 ID。**

**修复建议**：`where(...)` 中补 `Word.is_deleted == 0`。

---

## 三、AI 建议 vs 规则引擎：差异对照（含可复现实验）

### 3.1 两者的设计契约

| 维度 | 规则引擎（`domain/advice.py` `build_items`） | AI 建议（`services/llm_advice.py`） |
|---|---|---|
| 触发 | 同步、纯函数、进程内 | HTTP 调 `/chat/completions`，`asyncio.create_task` 后台 |
| 时延 | ~0 ms（纯计算） | 首次数秒~数十秒（`timeout_s=30`），期间返回规则建议占位 |
| 确定性 | 完全确定，同输入必同输出（已实测幂等，见 3.3） | 不确定，`temperature=0.7` |
| 输出结构 | 强类型 `type`/`action.type` 由代码固定 | 靠 prompt 约束 + `_extract_items` 事后解析 |
| 失败行为 | 不存在失败 | 任何异常 → 静默回退规则引擎 |
| 凭据依赖 | 无 | 需 `api_base_url` + `api_key` + `model_name` 三项齐全且密钥可解密 |

### 3.2 真实 context 样本（这才是发给 LLM 的全部输入）

从生产口径查询中取出的 `uid=1` 实际 context（`high_error_words` 取前 10）：

```json
{
  "high_error_words": [
    {"spelling": "whirl", "wrong_count": 5, "total_count": 6},
    {"spelling": "disposable", "wrong_count": 7, "total_count": 8},
    {"spelling": "highlight", "wrong_count": 6, "total_count": 7},
    {"spelling": "luxury", "wrong_count": 9, "total_count": 10},
    {"spelling": "oxide", "wrong_count": 5, "total_count": 6},
    {"spelling": "affectionate", "wrong_count": 10, "total_count": 11},
    {"spelling": "clip", "wrong_count": 9, "total_count": 10},
    {"spelling": "buffet", "wrong_count": 9, "total_count": 10},
    {"spelling": "concrete", "wrong_count": 9, "total_count": 10},
    {"spelling": "marvelous", "wrong_count": 7, "total_count": 8}
  ],
  "danger_due_tomorrow": 12,
  "active_days_last_7": 1,
  "weak_bigrams": ["cg", "mr", "vh", "mt", "br"],
  "recent_7d": {"avg_wpm": null, "avg_accuracy": null}
}
```

同一份 context 下规则引擎的**确定性输出**：

```
[p1] high_error     action=practice      错误率超过 60% 的词共 16 个，建议立即专项练习巩固。
     params={"word_ids":[1529,1225,17634,3891,7679,706,3648,6610,16656,861,17983,17538,17888,17457,17989,24597]}
[p2] danger_due     action=review        有 12 个高危遗忘词即将到期，现在复习效果最佳。
[p4] typing_weak    action=typing_drill  指法专项：字母组合 cg、mr、vh、mt、br 最慢/最易错，值得针对性训练。
     params={"letters":["cg","mr","vh","mt","br"]}
```

注意几个**规则引擎做不到、而 LLM 的价值恰在这里**的地方：

- `context` 里给出了 `luxury 9/10`、`affectionate 10/11`、`clip 9/10` 这类**具体错词**，
  规则引擎只会说"共 16 个词错误率高"，**不会点名任何一个**；
- `active_days_last_7=1`（本周只学过 1 天）与 `danger_due_tomorrow=12`（明天 12 个高危到期）
  存在**优先级冲突**：明天就有 12 个词到期，今天却只学了 1 天。规则引擎按固定优先级
  `high_error → danger_due → inactive → typing_weak` 输出，**看不到这个冲突**；
- `recent_7d.avg_wpm` 为 `null`（无打字记录），规则引擎无话可说；LLM 可以提示"先做打字诊断"。

### 3.3 同一 context 下的 AI 建议（按 SYSTEM_PROMPT 契约实际生成）

> 说明：项目内 AI 从未成功产出（见第一节），故此处由本报告作者**严格按 `llm_advice.SYSTEM_PROMPT`
> 的契约**（恰好 3 条、简体中文、每条 ≤80 字、引用 context 中的具体事实、输出指定 JSON），
> 对上述同一份 context 生成结果，用于与规则引擎做**结构性**对照。
> 这是**契约可行性验证**，不等于"项目内 AI 的历史输出"。

```json
{
  "items": [
    {
      "type": "high_error",
      "priority": 1,
      "message": "luxury 错 9 次、affectionate 错 10 次、clip 错 9 次，这三个词错误率超 85%，建议今天先专项练这 16 个高错词。",
      "action": {"type": "practice", "params": {"word_ids": [1529, 1225, 17634, 3891, 7679, 706, 3648, 6610, 16656, 861, 17983, 17538, 17888, 17457, 17989, 24597]}}
    },
    {
      "type": "danger_due",
      "priority": 2,
      "message": "明天有 12 个高危词到期，而你本周只学了 1 天；建议先清这 12 个，别让积累的逾期继续滚大。",
      "action": {"type": "review", "params": {}}
    },
    {
      "type": "typing_weak",
      "priority": 3,
      "message": "cg、mr、vh 这几个组合明显生疏（近 7 天无打字数据），每天 5 分钟指法练习补一下。",
      "action": {"type": "typing_drill", "params": {"letters": ["cg", "mr", "vh", "mt", "br"]}}
    }
  ]
}
```

### 3.4 差异对比表

| 维度 | 规则引擎实测 | AI 建议（契约生成） |
|---|---|---|
| **是否点名具体单词** | ❌ 只给总数「共 16 个」 | ✅ 点名 luxury / affectionate / clip 及各自错误次数 |
| **是否发现数据冲突** | ❌ 固定优先级，1 天活跃 vs 12 词到期不可见 | ✅ 明确指出"明天到期 12 个但本周只学 1 天" |
| **是否利用 null 值** | ❌ `avg_wpm=null` 直接无输出 | ✅ 把"无打字数据"转成可执行建议 |
| **措辞多样性** | ❌ 模板字符串，恒定文案 | ✅ 每条结合数据事实改写 |
| **可复现性** | ✅ 幂等（实测两次调用输出完全一致） | ❌ `temperature=0.7`，同输入多次调用措辞可能不同 |
| **时延** | ✅ ~0 ms | ❌ 数秒~30 s（`timeout_s`），首屏必须先返回占位 |
| **结构可靠性** | ✅ 强类型，代码保证 | ⚠️ 靠 prompt + 事后解析；`_extract_items` 失败即回退 |
| **能否离线/无凭据** | ✅ 永远可用 | ❌ 三项凭据齐全 + 密钥可解密才可用 |
| **成本** | 0 | 每次生成一次外部 API 调用（计费） |
| **建议条数** | `items[:3]` 硬截断，最多 3 条 | prompt 要求恰好 3 条，`_extract_items` 截 `raw_items[:3]` |
| **`word_ids` 正确性** | ⚠️ **实测 14/16**（2 个已删除词未过滤，见 2.1） | ❌ 必然为幻觉（context 无 `word_id`） |

### 3.5 一个必须记录的设计代价

规则引擎的 `action.params` 是**服务端算好的可执行参数**（如 `word_ids` 具体 ID 列表），
前端 `DashboardView.actionRoute()` 直接拿它跳转（`/practice?word_ids=...`）。
而 AI 路径下，模型要自己填 `params` —— 这里有一个**结构性不可能**：

**context 里根本没有 `word_id`。** 实际下发给模型的 `high_error_words` 每项只有三个字段：

```json
{"spelling": "whirl", "wrong_count": 5, "total_count": 6}
```

模型没有任何途径得知数据库主键。因此：

- 若 AI 仍按规则引擎的格式输出 `word_ids`，**只可能是幻觉值**；
- 实测幻觉后果（`llm_hallucination_test.py`）：

| 情形 | 后端组卷命中 | 前端行为 |
|---|---|---|
| 规则引擎填真实 id | 14/16 词 | 可用 |
| **AI 填幻觉 id** | **0/10 词** | `actionRoute` 仍生成 `/practice?word_ids=101529,...`，但词池全空 → **打开空白练习页** |
| AI 只填 type 不填 params | 全部词 | 退化为 `/practice`（无精准度） |
| AI 填 `type=none` | — | `actionRoute` 返回空串，模板 `v-if` 隐藏按钮 |

⚠️ 注意第 2 行是最坏情况：**前端不校验、照常跳转**，用户点「去执行」后看到的是
一个空白/无词的练习页 —— 这正是 R19 修过的"白屏"类问题的 AI 版本。

- 实际可用的 `action.type` 只能是 `review` / `study_plan` / `books` 这类**无需 ID 的跳转**；
- `_extract_items` **直接信任模型输出的 `action.params`，没有回查校验** —— 这是一条已知功能缺口。

> 这是"规则引擎与 LLM 混合架构"的核心矛盾：**LLM 擅长把数据讲成人话，不擅长产出可执行的 ID 引用**。
> 正确的分工应是：规则引擎负责**计算与产出可执行 action**（保证正确性），
> LLM 只负责**把 action 改写成自然语言**（保证表达质量）。当前实现把两者混在一次生成里，
> 是这条结论的直接反证。顺带说明：即便规则引擎，其 ID 正确性也需服务端保证（见 2.1 的 14/16）。

---

## 四、复现方式

```bash
cd backend
./.venv/Scripts/python.exe ../.workbuddy/tmp/llm_evidence.py        # A/B/C 三节全部证据
./.venv/Scripts/python.exe ../.workbuddy/tmp/llm_hallucination_test.py # 3.5 节幻觉实验
```

输出：`.workbuddy/tmp/llm_evidence.json`，含

- `cache_total` / `engine_dist` / `llm_success_records`：第一节落库事实
- `ai_config_decrypt`：第二节密钥可解密性验证
- `samples[]`：42 个用户的真实 context + 规则引擎输出 + 最近落库 engine/note

**断言口径**（论文引用建议直接用这些数字）：

- `llm_success_records == 0`（51 条记录中 AI 产出 0 条）
- `engine_dist == {"rule": 51}`
- `ai_config_decrypt`：uid=2 为 `decryptable=false`（`InvalidTag`，旧密文）；
  uid=1 于 12:19 重新录入后已变为 `decryptable=true`
- 幻觉实验：对照组 14/16 有效，实验组 0/10 命中

---

## 四点五、首次真实产出实录（12:25，AI 首次跑通）

清除代理变量后，用 **uid=1 的真实 context**（含真实错词 `luxury` 错 9/10、`annually`/`previously` 全错等）
调用 `llm_advice.generate()`，**首次拿到结构完全合法的 3 条 AI 建议**：

```
耗时 5027ms    note = ''      ← 空 note = 成功，未降级

[1] type=typing_weak  action=typing_drill  params={"bigrams":["cg","mr","vh"],"minutes":5}
    弱键组合 cg、mr、vh 反复出错，建议每天做5分钟专项打字练习，重点突破这三组字母。
[2] type=high_error   action=practice      params={"words":["annually","previously","luxury",
                                                       "clip","buffet","concrete"]}
    annually、previously 12次全错，luxury、clip、buffet、concrete 也错9次，
    建议今天逐个跟读拼写并复习。
[3] type=danger_due   action=review        params={"due_count":12,"review_today":6}
    明天有12个单词到期复习，但近7天只学习1天，建议今天先复习6个，避免遗忘堆积。
```

### 4.5.3 完整链路端到端验证（12:35，`engine='llm'` 首次落库）

在清代理环境启动 uvicorn，用探针账号（复制 uid=1 的 AI 凭据 + `advice_engine='llm'` + 30 词学习数据）
走真实 HTTP 接口验证，**全链路打通**：

```
POST /api/v1/ai-config/test   → {"ok":true,"latency_ms":2592,"message":"HTTP 200"}   ← 凭据探测通过
GET  /api/v1/advice（第1次）  → engine="rule", note="AI 建议生成中，稍候自动更新…"
                                （规则引擎先出，同时后台调度 LLM 任务）
GET  /api/v1/advice（第2次，8秒后）
                             → ✅ engine="llm"，note=""（空 = 成功，无降级）
```

数据库确认（**这是本项目历史上第一条 AI 产出的建议记录**）：

```
engine 分布: [('llm', 1), ('rule', 51)]
新记录: (user_id=129, date='2026-10-04', engine='llm', note='', generated_at='2026-10-04 12:35:13')
```

AI 实际产出的三条建议：

| # | type | action.params | 文案 |
|---|---|---|---|
| 1 | `high_error` | `{"words":["alternative","accommodate","haul","excess","disposable","stride"]}` | 单词 alternative、accommodate、haul、excess、disposable、stride 错误率高达90%，建议集中专项练习，先准确再提速。 |
| 2 | `danger_due` | `{"count":30}` | 明天有30个单词到期，请安排一次复习，优先巩固高错误词，避免遗忘曲线陡降。 |
| 3 | `inactive` | `{"days_per_week":5}` | 最近7天仅活跃3天，建议将学习频率提升至每周至少5天，每次15分钟，保持持续性。 |

**与规则引擎的同题对比**（同一份 context）：

| | 规则引擎（第1次响应） | AI（第2次响应） |
|---|---|---|
| 高错误率词 | 「共 **6** 个，建议立即专项练习」 | **点名 6 个词** + 「错误率高达 90%」 |
| 到期提醒 | 「有 30 个高危遗忘词即将到期」 | 「明天有30个单词到期，**优先巩固高错误词**」 |
| 第三条 | **无**（只有 2 条） | **指出活跃天数问题**：「最近7天仅活跃3天，建议提升至每周至少5天」 |
| 可执行性 | ✅ `word_ids` 真实 ID，前端可直接组卷 | ❌ `words` 拼写，前端**静默忽略** |

> 结论：AI 在**表达与数据洞察**上明显强于规则引擎（能点名、能发现规则引擎完全看不到的第三条建议），
> 但**可执行性仍需服务端兜底**（见 4.5.2）。

### 4.5.1 与规则引擎的实测差异（同一份 context）

| 维度 | 规则引擎输出 | 本次 AI 真实输出 |
|---|---|---|
| 条数 | 3 条 | 3 条 |
| 第 1 条 | 「共 16 个高错误率词，建议练习」（只给总数） | **点名 6 个具体词** + 各自的错误次数 |
| 第 2 条 | 「明天 12 个高危词到期」 | **指出数据冲突**：明天 12 个到期 vs 近 7 天只学 1 天，并给出可执行的「今天先复习 6 个」 |
| 措辞 | 模板字符串，跨用户恒定 | 逐条结合数据事实改写 |
| 数据引用 | 仅用 count | 用到错误次数（9/10、12 次全错）与活跃天数 |
| 延迟 | ~0 ms | **5027 ms**（单次），失败重试再 +8.7 s |
| 确定性 | 幂等，两次调用完全一致 | `temperature=0.7`，实测同输入 3 次调用中 2 次因 429 失败、1 次输出的 `action.params` 键名都不同（`spellings` vs `words`） |

### 4.5.2 🔴 首次成功即暴露的新缺陷：AI 产出的 params 前端无法消费

本次 AI 对 `high_error` 给的是 `params={"words": ["annually", ...]}`（**单词拼写**），
另一次调用给的是 `params={"spellings": ["luxury"]}`（**换了键名**）。而前端 `DashboardView.actionRoute()`
只认 `word_ids`：

```ts
if (t === 'practice') {
  const ids = Array.isArray(item?.action?.params?.word_ids) ? item.action.params.word_ids : [];
  return ids.length ? `/practice?word_ids=${ids.join(',')}` : '/practice';
}
```

结果：**AI 生成的"去执行"按钮只会跳到全量练习页**，`words` / `spellings` 字段被静默忽略，
用户点了以为在练那 6 个高错误率词，实际练的是整本词库。这比"AI 编造 ID"更隐蔽 ——
模型其实答对了词，但**接口契约缺失导致信息在最后一公里丢失**。

> 补充：`SYSTEM_PROMPT` 里 `"params":{}` 没有任何字段约束，也没说明 `word_id` 从哪来
> （context 的 `high_error_words` 只有 `{spelling, wrong_count, total_count}`）。
> 所以这是**契约设计的必然结果**，不是模型"笨"。修复方向：
> ① 在 context 中补 `word_id`；② 在 prompt 中显式规定 `practice` 必须输出 `params.word_ids`；
> ③ 服务端按 `spellings` 反查真实 `word_id` 后再下发（最稳，且能顺带过滤已删除词）。

---

## 五、论文可用结论（五条）

1. **在真实使用中，AI 建议引擎的可用性为 0（历史事实，已于 12:35 终结）**。尽管实现了完整的双引擎架构（用户可自选、
   失败自动回退、凭据用户自持、AES 加密存储），12:35 之前 51 条产出记录全部来自规则引擎，
   `engine='llm'` 为 0。运行日志交叉印证（`attempt 1/2 failed: InvalidTag`）。
   → 论证"**降级机制掩盖了功能失效**"：可用性故障对用户不可见。
   ✅ **12:35 修正三层根因后已打通全链路**，产出本项目第一条 `engine='llm'` 记录（见 4.5.3）。
   本条转为"**该系统曾长期处于完全不可用状态，且用户与开发者均无法察觉**"的历史结论。

2. **根因是密钥生命周期管理缺失，而非模型能力**。`InvalidTag` 表明 AES-GCM 密钥在配置后被轮换，
   旧密文永久不可解；系统将其与"接口故障"混为一谈，并因 `mask_secret` 吞异常而使设置页
   仍显示 `****` 掩码，用户无法自查。
   → 论证"**加密凭据的密钥轮换需要配套失效检测与重新录入路径**"。

3. **失败分类缺失导致用户无法处置**。实测共出现三类失败——`InvalidTag`（凭据失效，需重新录入）、
   `ConnectError`（网络/代理不可达，只能重试）、`HTTP 429`（模型端点限流，需换模型或等配额）——
   被压成同一种 `note` 字符串返回，而**三者用户处置动作完全不同**。
   → 论证"**可观测性设计应区分'用户可修复'与'用户不可修复'与'需等待'的失败**"，
   这是双引擎降级架构中降级本身也会损失信息的一个具体例证。

3b. **⚠️ 最有工程价值的发现：`ConnectError` 的语义误导**。httpx 默认 `trust_env=True`，
   会自动采用系统 `HTTP_PROXY`；本机该变量指向**未监听端口** `7892`，于是所有外发请求
   都被导向死地址。实测对照：走代理 `curl exit=7 / http_code=000`（2.0 s 超时），
   绕过代理 `401 / 0.24 s`（可达）。
   → 论证"**基础设施故障会被包装成业务层错误码**，错误分类必须基于根因而非表象"。
   这解释了为何前两版报告都误判为"环境限制、外网不可达"。

4. **LLM 与规则引擎的差异集中在"表达"与"洞察"而非"计算"**。同一份 context 的端到端实测对照：

   | 能力 | 规则引擎 | LLM（实测产出） |
   |---|---|---|
   | 点名具体单词 | ❌ 只说"共 6 个" | ✅ 点名 6 个 + "错误率高达 90%" |
   | 发现规则外的洞察 | ❌ **只输出 2 条** | ✅ **输出第 3 条**："最近7天仅活跃3天，建议提升至每周5天" |
   | 引用精确数值 | 仅用 count | 错误次数、活跃天数、到期天数 |
   | 零延迟 / 幂等 | ✅ ~0 ms，两次一致 | ❌ 2.5~8.7 s，且 429 限流下不稳定 |
   | 产出可执行 ID | ✅ 真实 `word_ids` | ❌ 只给拼写（见 4.5.2） |

   → 论证"**混合架构应让规则引擎负责计算与可执行动作、LLM 只负责自然语言改写与补充洞察**"，
   这也是本项目下一步的正确演进方向。

5. **"可执行动作的正确性"必须由服务端保证，不能外包给任何生成方**。这条被三方证据支持：
   - AI 编造 ID：实测 0/10 命中，前端不校验照常跳转 → 空白页；
   - **AI 用拼写代替 ID**（首次成功落库时实测，键名还在 `words` / `spellings` 间摇摆）：
     前端只认 `word_ids` → **静默跳全量练习页**，用户以为在练高错误率词；
   - 规则引擎漏 `is_deleted=0`：实测 14/16 有效。
   → 论证"**生成式组件的输出必须经过服务端校验/回查，不能直接驱动 UI 跳转**"。
   ⚠️ 特别注意第二例：模型**答对了词**，是接口契约缺失导致正确信息在最后一公里丢失 ——
   这比"模型答错"更隐蔽，因为**表面上一切正常**。

---

## 六、待办（按优先级）

| 优先级 | 事项 | 位置 |
|---|---|---|
| **P0** | **清除/修正本机死代理变量**（`HTTP_PROXY=127.0.0.1:7892` 无监听），否则 AI 必连不通 | 操作系统 / Shell 环境 |
| **P0** | **模型名从 `deepseek-flash` 改为 `deepseek-v4-flash`**（前者实测成功率 1/6，后者 5/6） | 设置页 `user_ai_config.model_name` |
| **P0** | **`high_error` 查询补 `Word.is_deleted == 0`** | `misc_service._generate_advice` ① 号查询 |
| **P0** | **AI/规则引擎产出后统一回查 `action.params.word_ids`**，按 `spellings` 反查真实 ID 并过滤已删词 | `llm_advice._extract_items` + `domain/advice.py` |
| P1 | `llm_advice` 的 `AsyncClient` 显式处理代理（生产无需代理，建议 `trust_env=False`；若需代理则加 `user_ai_config.proxy_url` 字段） | `llm_advice.py:90,148` |
| P1 | 失败原因**分类**为「凭据失效 / 网络不可达 / 端点限流(429)」三类，各自给出对应处置建议；`mask_secret` 解密失败返回空而非 `****` | `llm_advice.py` + `core/security.py` |
| P1 | 429 限流应触发**指数退避重试**（当前仅重试 1 次，实测 3 次调用 2 次仍失败） | `llm_advice.generate` |
| P2 | AI 路径改为"规则算 action + LLM 只改写文案"的两段式 | `llm_advice.py` + `misc_service` |
| P2 | `SYSTEM_PROMPT` 显式规定 `practice` 动作必须输出 `params.word_ids`（当前 `params:{}` 无任何字段约束） | `llm_advice.py:SYSTEM_PROMPT` |
| P2 | 设置页展示 AI 引擎健康状态（上次成功/失败时间与原因） | `SettingsView.vue` |
