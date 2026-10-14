# AI 建议引擎（LLM）与规则引擎实测对比

> 用途：论文佐证材料。数据来源 = 本地库 `backend/data/wordtype.db` 的 `advice_cache` 表原始记录（user_id=1），代码位置均标 file:line。
> 采样日期：2026-10-04。样本量限制见文末。

## 1. 机制（代码事实）

| 维度 | 规则引擎 | LLM 引擎 |
|---|---|---|
| 定义 | `backend/app/domain/advice.py:1-57` 固定优先级模板：错误率>60% → 高危明日到期 → 连续7天未学 → 指法专项 → 兜底鼓励 | `backend/app/services/llm_advice.py` OpenAI 兼容 `/chat/completions`，SYSTEM_PROMPT 约束"恰好 3 条、引用具体单词/数字、中文、≤80字、只输出 JSON"（llm_advice.py:19-26） |
| 输入 | 四类聚合量（高错词表/到期数/未学天数/弱 bigram） | 同一份 context JSON 原样喂给模型（misc_service.py:294-315） |
| 确定性 | **同数据必同输出**（模板+计数） | temperature 0.7（用户可配），逐次可变 |
| 选择方式 | 默认 | 用户自选 `advice_engine`，凭据每用户自有（Key AES-GCM 加密存储） |
| 失败行为 | — | 任何失败（HTTP/超时/输出不可解析）→ 重试 1 次 → **回退规则引擎并在 note 记录原因**（llm_advice.py:118-135，misc_service.py:337-347） |
| 结果结构 | 两者共享同一 JSON schema：`{type, priority, message, action{type, params}}`，type 枚举一致（high_error/danger_due/inactive/typing_weak/custom）→ **前端渲染同一路径** | |
| 成本控制 | 零 | 每日一缓存（advice_cache 按 date 主键），LLM 在后台任务生成替换，不阻塞请求（misc_service.py:327-362） |

## 2. 失败与回退的实测证据（2026-09-19 ~ 09-23，连续 5 天）

`advice_cache` 每日行 `engine='rule'` 且 `note='AI 调用失败，已回退规则引擎：InvalidTag: '`。
`InvalidTag` 是 cryptography AES-GCM 解密校验失败异常（`decrypt_secret`，llm_advice.py:89）——即存储的 API Key 与当前 `AES_KEY` 环境不符（推断成因为部署侧更换过 AES_KEY）。**回退链路按设计工作：用户每天仍拿到了建议，只是来自规则引擎。**

## 3. 首次成功生成对比（同一用户、相近数据）

### 3.1 规则引擎输出（2026-09-20 00:05，engine=rule 回退态）

| # | type | message | action |
|---|---|---|---|
| p1 | high_error | 错误率超过 60% 的词共 22 个，建议立即专项练习巩固。 | practice，params.word_ids=[22 个数字 ID] |
| p2 | danger_due | 有 2 个高危遗忘词即将到期，现在复习效果最佳。 | review，params={} |
| p4 | typing_weak | 指法专项：字母组合 cg、mr、vh、mt、br 最慢/最易错，值得针对性训练。 | typing_drill，params.letters=[cg,mr,vh,mt,br] |

### 3.2 LLM 输出（2026-10-04 12:41:45，engine=llm，模型 agnes-3.0-flash）

| # | type | message | action |
|---|---|---|---|
| p1 | high_error | luxury 和 affectionate 错率极高，建议今日重点复习这5个高频错词。 | review，params.words=[5 个拼写字符串] |
| p2 | inactive | 近7天仅活跃1天，且明日有12个单词到期，需立即制定每日打卡计划。 | study_plan，params.frequency="daily" |
| p3 | typing_weak | cg、mr、vh、mt、br 音位组合较弱，请完成专项打字训练以提升准确率。 | typing_drill，params.bigrams=[同规则引擎的 5 组合] |

（配置写入 12:41:37 → 生成落库 12:41:45，端到端约 8 秒，含后台任务调度。）

## 4. 差异结论（论文表述可用）

1. **结构同构、语义分化**：两者输出同一 schema、同一 type 枚举，前端零改动共用；但 LLM 的 message 引用具体单词（luxury/affectionate）、**融合多信号成一条因果句**（p2 把"活跃 1 天"与"明日到期 12 词"合成"需立即制定打卡计划"），规则引擎只做单信号计数模板。
2. **规则引擎独有语义 LLM 未复现**：p1 中 LLM 说"5 个高频错词"但 action 从 practice 变成了 review、且丢弃了精确的 22 词 ID 清单——规则引擎的"可执行清单"能力（word_ids 直达组卷）LLM 输出不稳定。
3. **⚠ 已发现的契约缺陷（本对比的直接产出）**：LLM 生成的 `action.params` 键名/类型与前端"去执行"契约不符——前端只认 `practice + params.word_ids: number[]`（DashboardView.vue:80-87），LLM 给的 `words:[拼写]`、`bigrams`、`frequency` 均不被消费，该按钮退化为无参跳转。修复方向：SYSTEM_PROMPT 注入机器契约（明确 params 必须用 word_ids 整数数组）或服务端归一化映射。
4. **可靠性设计有实测支撑**：5 天连续失败期间回退机制保证功能零中断，且 note 字段留存了可审计的失败原因——可作为"LLM 功能的降级设计"案例。

## 5. 样本局限（论文必须声明）

- LLM 成功样本仅 1 天 3 条（2026-10-04），无跨模型/多次重复采样；temperature=0.7 下同数据输出会波动，本例不代表稳定质量。
- 9 月失败样本与 10 月成功样本**不同日、数据不同**，对比是"形态对比"而非"同输入对照实验"。做严格论证需：固定同一份 context JSON，对 rule/LLM 各采样 N 次，评审维度（事实正确性、可执行性、流畅度）盲评。
- 建议补充实验：同一 context 重复生成 5 次测一致性；故意给空数据测 LLM 是否幻觉编造单词（规则引擎可保证不幻觉——这是两者的本质差异点）。
