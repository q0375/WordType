Read "D:\test\encn\docs\数据库设计文档.md":

     1	以下为**数据库设计文档（DBD v1.0）**，将 v3.1 第六章的模型细化为可直接建库的 DDL，并给出完整数据流转逻辑（事件→字段变更矩阵、事务边界、聚合流水线、读路径 SQL）。过程中发现 3 处必须落库才能闭环的缺口（复习队列、考核卷、建议缓存），已补表并标注 〔DBD新增〕；发现 4 处字段级细化，标注 〔DBD细化〕；全部差异在第十节汇总，需回写评审。
     2	---
     3	# 数据库设计文档 v1.0
     4	### WordType · 配套《需求规格说明书 v3.1》与《架构设计 AD v1.0》
     5	---
     6	## 〇、设计约定（全库强制）
     7	| # | 约定 | 内容 |
     8	|---|------|------|
     9	| C1 | 方言 | SQLite 3.35+（部分索引/ON CONFLICT 均依赖）；命名 snake_case 单数；主键一律 `INTEGER PRIMARY KEY`（rowid 别名）自增；外部生成的 token 类主键用 `TEXT`（uuid） |
    10	| C2 | 时间 | 时间戳 `TEXT` ISO8601 `'YYYY-MM-DD HH:MM:SS'`，**服务器本地时区**（D13，`datetime('now','localtime')`）；纯日期 `TEXT` `'YYYY-MM-DD'`。迁移 PG 时时间戳换 `timestamptz`、日期换 `date`（见第九节） |
    11	| C3 | 布尔 | `INTEGER 0/1`，不用真布尔类型（PG 迁移时映射 boolean） |
    12	| C4 | 枚举 | `TEXT + CHECK` 约束（不用 int 枚举，可读性优先）；枚举值扩充=一次 Alembic 迁移 |
    13	| C5 | JSON | `TEXT` 存 JSON，pydantic 在应用层校验；不入库索引；大小上限：detail_json ≤ 16KB、parsed_json ≤ 8MB（应用层截断报错） |
    14	| C6 | 软删 | 业务表 `is_deleted + deleted_at(+deleted_by)`；**一切业务查询必须过滤 is_deleted=0**（Repository 基类）；物理删除仅发生在账号清理任务 |
    15	| C7 | 删除策略 | 所有 `user_id` 外键声明 `ON DELETE CASCADE`（账号物理清理依赖）；`word_id` 外键不级联（词只软删，物理删除随清理任务显式处理） |
    16	| C8 | 规范化 | `spelling` 按用户输入原样存储（展示用），判重/judgment 统一在查询与引擎层 normalize（trim+lower），判重靠 NOCASE 索引 |
    17	---
    18	## 一、ER 总览
    19	```mermaid
    20	erDiagram
    21	    USER ||--o{ WORD_BOOK : "owner(私有)"
    22	    USER ||--o| DAILY_SETTING : "1:1"
    23	    USER ||--o{ USER_WORD_STAT : ""
    24	    USER ||--o{ TYPING_RECORD : ""
    25	    USER ||--o{ WRONG_BOOK_ITEM : ""
    26	    USER ||--o{ DAILY_ACTIVITY : ""
    27	    USER ||--o{ REVIEW_QUEUE : ""
    28	    USER ||--o{ EXAM_PAPER : ""
    29	    USER ||--o{ GAME_SESSION : ""
    30	    USER ||--o{ GAME_RECORD : ""
    31	    USER ||--o{ EXAM_RECORD : ""
    32	    USER ||--o{ IMPORT_JOB : ""
    33	    USER ||--o{ ADVICE_CACHE : ""
    34	    USER ||--o{ LETTER_STAT : ""
    35	    USER ||--o{ STUDY_PROGRESS : ""
    36	    WORD_BOOK ||--o{ CHAPTER : ""
    37	    CHAPTER ||--o{ WORD : ""
    38	    WORD ||--o{ USER_WORD_STAT : ""
    39	    WORD ||--o{ TYPING_RECORD : ""
    40	    WORD ||--o{ WRONG_BOOK_ITEM : ""
    41	    WORD ||--o{ REVIEW_QUEUE : ""
    42	    EXAM_PAPER ||--o| EXAM_RECORD : "交卷生成"
    43	    GAME_SESSION ||--o| GAME_RECORD : "结算生成"
    44	    INVITE_CODE }o--|| USER : "created_by/used_by"
    45	```
    46	---
    47	## 二、DDL（分域）
    48	### 2.1 账户域
    49	```sql
    50	CREATE TABLE user (
    51	  id            INTEGER PRIMARY KEY,
    52	  username      TEXT    NOT NULL,
    53	  password_hash TEXT    NOT NULL,                       -- bcrypt
    54	  role          TEXT    NOT NULL DEFAULT 'user'  CHECK(role IN ('user','admin')),
    55	  pwd_ver       INTEGER NOT NULL DEFAULT 0,             -- 口径16：改密/重置+1
    56	  is_deleted    INTEGER NOT NULL DEFAULT 0,
    57	  deleted_at    TEXT,
    58	  last_login_at TEXT,                                   -- DBD细化：admin用户列表用
    59	  created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
    60	);
    61	-- 用户名唯一【含软删行】：注销后30天内名字不可被重新注册（保证可恢复）
    62	CREATE UNIQUE INDEX uq_user_name ON user(username COLLATE NOCASE);
    63	CREATE TABLE invite_code (                              -- D25
    64	  id         INTEGER PRIMARY KEY,
    65	  code       TEXT    NOT NULL,
    66	  created_by INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
    67	  used_by    INTEGER REFERENCES user(id) ON DELETE SET NULL,
    68	  used_at    TEXT,
    69	  expires_at TEXT    NOT NULL,
    70	  is_active  INTEGER NOT NULL DEFAULT 1,
    71	  created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
    72	);
    73	CREATE UNIQUE INDEX uq_invite_code ON invite_code(code);
    74	CREATE INDEX idx_invite_created ON invite_code(created_by);
    75	CREATE TABLE daily_setting (
    76	  user_id                INTEGER PRIMARY KEY REFERENCES user(id) ON DELETE CASCADE,
    77	  daily_new_limit        INTEGER NOT NULL DEFAULT 20   CHECK(daily_new_limit BETWEEN 0 AND 100),
    78	  daily_review_limit     INTEGER NOT NULL DEFAULT 100  CHECK(daily_review_limit BETWEEN 0 AND 500),  -- 范围为DBD建议值
    79	  loose_match            INTEGER NOT NULL DEFAULT 1,
    80	  typing_guide_on        INTEGER NOT NULL DEFAULT 1,
    81	  tts_on                 INTEGER NOT NULL DEFAULT 1,
    82	  review_form            TEXT    NOT NULL DEFAULT 'typing' CHECK(review_form IN ('typing','choice','self')),
    83	  dictation_show_seconds INTEGER NOT NULL DEFAULT 3    CHECK(dictation_show_seconds BETWEEN 2 AND 5),
    84	  practice_group_size    INTEGER NOT NULL DEFAULT 20   CHECK(practice_group_size BETWEEN 10 AND 50),
    85	  game_difficulty        TEXT    NOT NULL DEFAULT 'normal' CHECK(game_difficulty IN ('easy','normal','hard')),
    86	  game_limited_mode      INTEGER NOT NULL DEFAULT 0,
    87	  exam_time_limit        INTEGER NOT NULL DEFAULT 20   CHECK(exam_time_limit BETWEEN 5 AND 120),
    88	  exam_pass_score        INTEGER NOT NULL DEFAULT 60   CHECK(exam_pass_score BETWEEN 0 AND 100),
    89	  exam_loose_match       INTEGER NOT NULL DEFAULT 0,
    90	  review_wrong_reshow    INTEGER NOT NULL DEFAULT 1
    91	);
    92	CREATE TABLE system_setting (
    93	  key        TEXT PRIMARY KEY,       -- ai_model_config / invite_required / letterstat_watermark / ...
    94	  value_json TEXT NOT NULL,
    95	  updated_at TEXT NOT NULL
    96	);
    97	```
    98	### 2.2 内容域（词库）
    99	```sql
   100	CREATE TABLE word_book (
   101	  id          INTEGER PRIMARY KEY,
   102	  name        TEXT    NOT NULL,
   103	  owner_id    INTEGER REFERENCES user(id) ON DELETE CASCADE,   -- NULL=公共词库
   104	  is_public   INTEGER NOT NULL DEFAULT 0,
   105	  is_deleted  INTEGER NOT NULL DEFAULT 0,                      -- DBD细化：级联软删落点
   106	  deleted_at  TEXT,
   107	  created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
   108	);
   109	CREATE INDEX idx_book_owner ON word_book(owner_id);
   110	CREATE TABLE chapter (
   111	  id         INTEGER PRIMARY KEY,
   112	  book_id    INTEGER NOT NULL REFERENCES word_book(id) ON DELETE CASCADE,
   113	  name       TEXT    NOT NULL,
   114	  sort_order INTEGER NOT NULL DEFAULT 0,
   115	  is_deleted INTEGER NOT NULL DEFAULT 0
   116	);
   117	CREATE INDEX idx_chapter_book ON chapter(book_id, sort_order);
   118	CREATE TABLE word (
   119	  id         INTEGER PRIMARY KEY,
   120	  chapter_id INTEGER NOT NULL REFERENCES chapter(id),
   121	  book_id    INTEGER NOT NULL REFERENCES word_book(id),  -- 冗余列：库级判重（克隆/导入时维护）
   122	  spelling   TEXT    NOT NULL,
   123	  meaning    TEXT    NOT NULL,
   124	  phonetic   TEXT,
   125	  example    TEXT,
   126	  is_deleted INTEGER NOT NULL DEFAULT 0,
   127	  deleted_at TEXT,
   128	  deleted_by INTEGER REFERENCES user(id) ON DELETE SET NULL,
   129	  created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
   130	);
   131	-- 并发导入判重竞态兜底（应用层校验之外的第二道闸）；软删行让位，允许重建同词
   132	CREATE UNIQUE INDEX uq_word_book_spelling
   133	  ON word(book_id, spelling COLLATE NOCASE) WHERE is_deleted = 0;
   134	-- 词列表：章节内按 id 升序（备忘1）
   135	CREATE INDEX idx_word_chapter ON word(chapter_id, is_deleted, id);
   136	```
   137	### 2.3 学习统计域
   138	```sql
   139	CREATE TABLE user_word_stat (
   140	  user_id         INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
   141	  word_id         INTEGER NOT NULL REFERENCES word(id),
   142	  proficiency     INTEGER NOT NULL DEFAULT 0 CHECK(proficiency BETWEEN 0 AND 100),
   143	  correct_count   INTEGER NOT NULL DEFAULT 0,
   144	  wrong_count     INTEGER NOT NULL DEFAULT 0,
   145	  near_miss_count INTEGER NOT NULL DEFAULT 0,
   146	  streak_correct  INTEGER NOT NULL DEFAULT 0,
   147	  ease_factor     REAL    NOT NULL DEFAULT 2.5 CHECK(ease_factor BETWEEN 1.3 AND 3.0),
   148	  interval_days   INTEGER,        -- NULL=未入循环（仅练习触达过的词）
   149	  next_review_at  TEXT,           -- 'YYYY-MM-DD'；NULL=未入队 或 已掌握退出
   150	  last_wrong_at   TEXT,
   151	  last_correct_at TEXT,
   152	  PRIMARY KEY (user_id, word_id)
   153	);
   154	CREATE INDEX idx_stat_queue ON user_word_stat(user_id, next_review_at);   -- 复习队列
   155	CREATE INDEX idx_stat_prof  ON user_word_stat(user_id, proficiency);      -- 高危筛选
   156	CREATE TABLE study_progress (
   157	  user_id         INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
   158	  chapter_id      INTEGER NOT NULL REFERENCES chapter(id) ON DELETE CASCADE,
   159	  last_word_index INTEGER NOT NULL DEFAULT 0,     -- 「重来」仅归零此字段
   160	  updated_at      TEXT,
   161	  PRIMARY KEY (user_id, chapter_id)
   162	);
   163	CREATE TABLE letter_stat (
   164	  user_id        INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
   165	  letter         TEXT    NOT NULL CHECK(length(letter) BETWEEN 1 AND 2),  -- D24：单字母+bigram
   166	  avg_delay_ms   REAL    NOT NULL DEFAULT 0,
   167	  total_count    INTEGER NOT NULL DEFAULT 0,
   168	  error_count    INTEGER NOT NULL DEFAULT 0,
   169	  PRIMARY KEY (user_id, letter)
   170	);
   171	CREATE TABLE daily_activity (
   172	  user_id       INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
   173	  date          TEXT    NOT NULL,                 -- 'YYYY-MM-DD' 服务器本地（D13）
   174	  new_count     INTEGER NOT NULL DEFAULT 0,
   175	  review_count  INTEGER NOT NULL DEFAULT 0,
   176	  correct_count INTEGER NOT NULL DEFAULT 0,
   177	  wrong_count   INTEGER NOT NULL DEFAULT 0,       -- near 归入此列（注记N2）
   178	  PRIMARY KEY (user_id, date)
   179	);
   180	```
   181	### 2.4 记录域
   182	```sql
   183	CREATE TABLE typing_record (
   184	  id          INTEGER PRIMARY KEY,
   185	  user_id     INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
   186	  word_id     INTEGER NOT NULL REFERENCES word(id),
   187	  source      TEXT    NOT NULL CHECK(source IN ('study','practice','review','exam','game')),
   188	  result      TEXT    NOT NULL CHECK(result IN ('correct','near','wrong')),  -- DBD细化R1：替代布尔is_correct
   189	  wpm         REAL,
   190	  accuracy    REAL,
   191	  request_id  TEXT,                                 -- DBD细化R2：D19单题幂等的库级兜底
   192	  detail_json TEXT,                                 -- 结构见 §4.1
   193	  created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
   194	);
   195	CREATE INDEX idx_tr_user_time ON typing_record(user_id, created_at);
   196	CREATE UNIQUE INDEX uq_tr_request ON typing_record(user_id, source, request_id)
   197	  WHERE request_id IS NOT NULL;
   198	CREATE TABLE wrong_book_item (
   199	  user_id       INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
   200	  word_id       INTEGER NOT NULL REFERENCES word(id),
   201	  source        TEXT    NOT NULL CHECK(source IN ('study','practice','review','exam','game')),
   202	  conquer_count INTEGER NOT NULL DEFAULT 0,
   203	  pinned        INTEGER NOT NULL DEFAULT 0,
   204	  added_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
   205	  resolved      INTEGER NOT NULL DEFAULT 0,
   206	  resolved_at   TEXT,
   207	  PRIMARY KEY (user_id, word_id)                    -- 口径15：同词仅一行upsert
   208	);
   209	CREATE INDEX idx_wb_user ON wrong_book_item(user_id, resolved, pinned);
   210	CREATE TABLE exam_record (
   211	  id              INTEGER PRIMARY KEY,
   212	  user_id         INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
   213	  paper_id        TEXT REFERENCES exam_paper(id),
   214	  chapter_id      INTEGER,
   215	  score           INTEGER NOT NULL,                 -- D20 round后整数；0.5仅在明细层
   216	  duration        INTEGER NOT NULL,                 -- 秒
   217	  blur_count      INTEGER NOT NULL DEFAULT 0,
   218	  config_json     TEXT    NOT NULL,                 -- 组卷+计分+池策略快照
   219	  detail_json     TEXT    NOT NULL,                 -- 逐题{qid,word_id,type,result,raw_score,used_ms}
   220	  idempotency_key TEXT,
   221	  created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
   222	);
   223	CREATE UNIQUE INDEX uq_exam_idem ON exam_record(idempotency_key) WHERE idempotency_key IS NOT NULL;
   224	CREATE INDEX idx_exam_user ON exam_record(user_id, created_at);
   225	CREATE TABLE game_record (
   226	  id              INTEGER PRIMARY KEY,
   227	  user_id         INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
   228	  session_id      TEXT REFERENCES game_session(id),
   229	  score           INTEGER NOT NULL,
   230	  max_combo       INTEGER NOT NULL,
   231	  correct_count   INTEGER NOT NULL,
   232	  wpm             REAL,
   233	  difficulty      TEXT    NOT NULL CHECK(difficulty IN ('easy','normal','hard')),
   234	  mode            TEXT    NOT NULL CHECK(mode IN ('endless','timed')),
   235	  is_valid        INTEGER NOT NULL DEFAULT 1,
   236	  idempotency_key TEXT,
   237	  created_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
   238	);
   239	CREATE UNIQUE INDEX uq_game_idem ON game_record(idempotency_key) WHERE idempotency_key IS NOT NULL;
   240	CREATE INDEX idx_game_rank ON game_record(difficulty, mode, score DESC) WHERE is_valid = 1;
   241	```
   242	### 2.5 会话与幂等域（含 DBD 新增表）
   243	```sql
   244	-- 〔DBD新增 T1〕考核卷面：交卷判分的依据（AD §6.2 exam_token 落库）
   245	CREATE TABLE exam_paper (
   246	  id              TEXT PRIMARY KEY,                 -- exam_token (uuid)
   247	  user_id         INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
   248	  chapter_id      INTEGER NOT NULL,
   249	  config_json     TEXT    NOT NULL,                 -- 快照（与成绩单口径一致）
   250	  questions_json  TEXT    NOT NULL,                 -- [{qid,word_id,type,payload}] 下发内容
   251	  answer_key_json TEXT    NOT NULL,                 -- 服务端答案快照（防公共词库中途被改，不下发）
   252	  status          TEXT    NOT NULL DEFAULT 'active'
   253	                       CHECK(status IN ('active','submitted','void')),
   254	  blur_count      INTEGER NOT NULL DEFAULT 0,       -- 失焦实时增量上报累计
   255	  started_at      TEXT    NOT NULL,
   256	  expires_at      TEXT    NOT NULL,
   257	  submitted_at    TEXT
   258	);
   259	CREATE INDEX idx_paper_user ON exam_paper(user_id, status);
   260	-- 〔DBD新增 T2〕当日复习队列物化：D11重现一次、额度截断、溢出排序的落库依据
   261	CREATE TABLE review_queue (
   262	  id           INTEGER PRIMARY KEY,
   263	  user_id      INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
   264	  date         TEXT    NOT NULL,
   265	  word_id      INTEGER NOT NULL REFERENCES word(id),
   266	  tier         INTEGER NOT NULL,                    -- 0=逾期 1=高危 2=巩固中
   267	  overdue_days INTEGER NOT NULL DEFAULT 0,
   268	  is_reshow    INTEGER NOT NULL DEFAULT 0,          -- D11重现行
   269	  status       TEXT    NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','done')),
   270	  created_at   TEXT    NOT NULL
   271	);
   272	-- 同词当日：基础行(0)最多1条 + 重现行(1)最多1条 → 「重现一次且仅一次」的库级保证（P1验收④）
   273	CREATE UNIQUE INDEX uq_rq ON review_queue(user_id, date, word_id, is_reshow);
   274	CREATE INDEX idx_rq_read ON review_queue(user_id, date, status, tier);
   275	-- 〔DBD新增 T3〕AI 建议缓存 + 手动刷新计数（D28）
   276	CREATE TABLE advice_cache (
   277	  user_id       INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
   278	  date          TEXT    NOT NULL,
   279	  items_json    TEXT    NOT NULL,
   280	  engine        TEXT    NOT NULL DEFAULT 'rule' CHECK(engine IN ('rule','llm')),
   281	  refresh_count INTEGER NOT NULL DEFAULT 0,
   282	  generated_at  TEXT    NOT NULL,
   283	  PRIMARY KEY (user_id, date)
   284	);
   285	CREATE TABLE game_session (                         -- D27
   286	  id            TEXT PRIMARY KEY,
   287	  user_id       INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
   288	  word_ids_json TEXT    NOT NULL,
   289	  difficulty    TEXT    NOT NULL,
   290	  mode          TEXT    NOT NULL,
   291	  status        TEXT    NOT NULL DEFAULT 'active' CHECK(status IN ('active','settled','void')),
   292	  created_at    TEXT    NOT NULL,
   293	  expires_at    TEXT    NOT NULL                    -- 30分钟
   294	);
   295	CREATE INDEX idx_gs_user ON game_session(user_id, status);
   296	CREATE TABLE import_job (
   297	  id                 INTEGER PRIMARY KEY,
   298	  user_id            INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
   299	  book_id            INTEGER REFERENCES word_book(id) ON DELETE CASCADE,
   300	  filename           TEXT    NOT NULL,
   301	  status             TEXT    NOT NULL DEFAULT 'uploaded'
   302	                       CHECK(status IN ('uploaded','parsing','preview_ready','confirmed','failed','expired')),
   303	  total_rows INTEGER, ok_rows INTEGER, dup_rows INTEGER, error_rows INTEGER,
   304	  parsed_json        TEXT,
   305	  error_report_path  TEXT,
   306	  duplicate_strategy TEXT CHECK(duplicate_strategy IN ('skip','overwrite')),
   307	  preview_token      TEXT UNIQUE,
   308	  expires_at         TEXT NOT NULL,
   309	  created_at         TEXT NOT NULL DEFAULT (datetime('now','localtime'))
   310	);
   311	CREATE INDEX idx_job_created ON import_job(status, expires_at);
   312	CREATE TABLE idempotency_record (
   313	  key               TEXT PRIMARY KEY,               -- Idempotency-Key 或 单题request_id
   314	  endpoint          TEXT NOT NULL,
   315	  user_id           INTEGER NOT NULL,
   316	  status_code       INTEGER NOT NULL,
   317	  response_snapshot TEXT NOT NULL,                  -- 首次响应快照（口径24重放返回）
   318	  created_at        TEXT NOT NULL DEFAULT (datetime('now','localtime'))
   319	);
   320	CREATE INDEX idx_idem_created ON idempotency_record(created_at);   -- 03:00清理
   321	```
   322	---
   323	## 三、关键语义细化
   324	### 3.1 detail_json 结构（双端契约，LetterStat 数据源）
   325	```json
   326	{
   327	  "v": 1,
   328	  "duration_ms": 3800,
   329	  "keys": [
   330	    {"i": 0, "expected": "a", "typed": "a", "t": 120, "ok": true},
   331	    {"i": 1, "expected": "b", "typed": "x", "t": 305, "ok": false}
   332	  ]
   333	}
   334	```
   335	规则：`t` 为相对该题首键的 ms；IME 组合帧不记录；`ok = (typed == expected)`；退格不产生条目（分母口径一致）；`delay[i] = t[i] − t[i−1]`。
   336	### 3.2 UserWordStat 字段语义与「状态即派生」
   337	- **状态不落库**：已掌握/巩固中/高危由 `proficiency` + `next_review_at` 派生（`p≥80 且 next IS NULL`→已掌握；`next IS NOT NULL`→循环中，按 p 分高危/巩固）。杜绝状态列与数值列的双写不一致。
   338	- `interval_days IS NULL` 且无 stat 行 = 从未触达；`interval_days IS NOT NULL 且 next IS NULL` = 曾入循环、现已掌握（D18 回退判定依据）。
   339	- `streak_correct` 语义见 D21；自评设值与学习入队时归零。
   340	### 3.3 生命周期同步函数 sync_lifecycle（任何模式写 stat 后必须调用）
   341	```
   342	输入：本事件 result 与更新后的 stat 行
   343	① p ≥ 80 且 next_review_at IS NOT NULL      → next_review_at = NULL        （掌握退出）
   344	② result = wrong 且 next_review_at IS NULL
   345	   且 interval_days IS NOT NULL              → next_review_at = 明日,
   346	                                              interval_days = 1             （D18 回退）
   347	③ 其余不动
   348	```
   349	---
   350	## 四、核心数据流转
   351	### 4.1 事件 × 表变更矩阵（最核心的一张表）
   352	> 图例：＋=插入/U，Δ=增量更新，S=设值，—=不动。stat 列内 p=integrity 序列见 4.2。
   353	| 事件 | user_word_stat | typing_record | wrong_book_item | daily_activity | review_queue | 其他 |
   354	|---|---|---|---|---|---|---|
   355	| 学习·自评（认识/模糊/不认识） | S: p=60/30/0，interval=3/1/1，next=+3d/+1d/+1d，streak=0 | —（无打字） | —（自评不入本） | ＋1 行或 Δ：new_count+1 | — | study_progress Δ |
   356	| 学习·默写 对/近似/错 | 同上三档（D17 映射），streak=0 | ＋ | 错→入本/归零；近似→不动（N1） | new+1；correct+1 或 wrong+1 | — | TypingRecord(source=study) |
   357	| 练习·答题 对/近似/错 | Δ p 与计数（§4.2）＋sync_lifecycle | ＋ | 对→conquer+1；错→入本/归零；近似→不动（N1） | correct/wrong+1 | — | 30s 写 localStorage（D12）不落库 |
   358	| 复习·打字/选择 对/近似/错 | Δ 全量 SM-2（§4.2）＋sync_lifecycle | ＋ | 同练习行 | review+1；correct/wrong+1 | 当前行 status=done；错且 D11 开→＋重现行 | 额度=review_count 校验 |
   359	| 复习·自评（D16） | 按映射三态同上 | —（无打字） | 同对应判定态 | review+1（打卡） | 同上 | — |
   360	| 考核·交卷（批量 N 题） | Δ 每题增量（循环内批量）＋sync_lifecycle | ＋×N（source=exam，result=逐题） | 同练习行（批量） | 批量 correct/wrong 累加 | — | exam_paper：status=submitted、blur_count 定格 → exam_record ＋；idempotency_record ＋ |
   361	| 游戏·结算（批量逐词） | Δ 每词：销毁(含宽松)→对；弹回/落地→错；＋sync_lifecycle | —（逐词日志不落库，v3.1） | 错词批量入本/归零 | 批量 correct/wrong 累加 | — | game_session：status=settled → game_record ＋；idempotency_record ＋ |
   362	| 导入·确认 | — | — | — | — | — | word 批量＋；import_job status=confirmed |
   363	| 改密/管理员重置 | — | — | — | — | — | user：pwd_ver+1（口径16） |
   364	| 注册（邀请码开启） | — | — | — | — | — | invite_code：used_by/used_at 原子核销（§4.4） |
   365	| 注销/恢复/物理清理 | — | — | — | — | — | user.is_deleted 翻转；清理任务硬删（§五） |
   366	### 4.2 SM-2 与熟练度的字段级规则（复习路径全量版；练习/考核/游戏仅取 p/计数部分）
   367	| 判定 | interval_days | ease_factor | proficiency | streak | next_review_at | D11 重现 |
   368	|---|---|---|---|---|---|---|
   369	| 对 | 1→3；≥3→round(iv×ease) | +0.05（≤3.0） | +10×MIN(streak+1, 3) | +1 | 今日+新 interval | — |
   370	| 近似 | max(1, iv÷2 取整) | −0.2→−0.1？否：**−0.1**（≥1.3） | −12（≥0） | 不变 | 今日+新 interval | 否 |
   371	| 错 | 1 | −0.2（≥1.3） | −25（≥0） | 0 | 明日 | 开关开→重现行×1 |
   372	增量 SQL（SQLite 同条 UPDATE 内右值取旧值，天然防读改写竞态，D2）：
   373	```sql
   374	-- 对
   375	UPDATE user_word_stat SET
   376	  correct_count   = correct_count + 1,
   377	  streak_correct  = streak_correct + 1,
   378	  proficiency     = MIN(100, proficiency + 10 * MIN(streak_correct + 1, 3)),
   379	  interval_days   = CASE WHEN interval_days <= 1 THEN 3
   380	                         ELSE CAST(ROUND(interval_days * ease_factor) AS INTEGER) END,
   381	  ease_factor     = MIN(3.0, ease_factor + 0.05),
   382	  next_review_at  = :today_plus_new_interval,   -- 应用层先算日期串
   383	  last_correct_at = :now
   384	WHERE user_id = :u AND word_id = :w;
   385	-- 近似 / 错 两条同构，略（公式如上表）
   386	```
   387	### 4.3 写路径全景
   388	```mermaid
   389	flowchart LR
   390	    subgraph EVENTS["写事件"]
   391	        E1["学习自评/默写"]
   392	        E2["练习答题"]
   393	        E3["复习作答"]
   394	        E4["考核交卷"]
   395	        E5["游戏结算"]
   396	        E6["导入确认"]
   397	    end
   398	    subgraph TX["单事务（Service 层边界）"]
   399	        T1[("user_word_stat<br/>增量+sync_lifecycle")]
   400	        T2[("typing_record")]
   401	        T3[("wrong_book_item")]
   402	        T4[("daily_activity<br/>upsert计数")]
   403	        T5[("review_queue<br/>done/重现行")]
   404	        T6[("exam_record / game_record<br/>+ 会话状态翻转")]
   405	    end
   406	    subgraph ASYNC["异步/批量"]
   407	        A1[("letter_stat<br/>04:00水位线聚合")]
   408	        A2[("TypingRecord→归档<br/>P4")]
   409	    end
   410	    E1 --> T1 & T2 & T4
   411	    E2 --> T1 & T2 & T3 & T4
   412	    E3 --> T1 & T2 & T3 & T4 & T5
   413	    E4 --> T6 & T1 & T2 & T3 & T4
   414	    E5 --> T6 & T1 & T3 & T4
   415	    E6 --> W[("word 批量插入<br/>NOCASE判重")]
   416	    T2 -.->|id > 水位线| A1
   417	```
   418	### 4.4 六个事务的关键机制
   419	| 事务 | 边界内动作（全部单事务） | 幂等/并发手段 |
   420	|---|---|---|
   421	| 单题提交 | stat 增量＋sync_lifecycle → typing_record → wrongbook → daily upsert →（复习）queue done/重现行 | 应用层查 idempotency_record(request_id)；`uq_tr_request` 兜底；409 返回快照 |
   422	| 考核交卷 | 校验头幂等 → 载入 paper → 逐题 judge → D20 计分 → exam_record → stat 批量 → wrongbook 批量 → daily 批量 → paper.status=submitted | `Idempotency-Key` 唯一索引；重复请求返回快照；paper 状态机防二次判分 |
   423	| 游戏结算 | 校验 session（未 settled、word∈池、耗时）→ game_record → stat 批量 → wrongbook 批量 → daily 批量 → session.status=settled | 同上；session 单向状态机 |
   424	| 导入确认 | 载入 parsed_json → 分批 INSERT word（每批 500，整事务）→ 跳过/覆盖 → 报告文件 → job.status=confirmed | `uq_word_book_spelling` 兜底竞态；覆盖仅 UPDATE meaning/phonetic/example（口径17） |
   425	| 注册核销 | `UPDATE invite_code SET used_by=?, used_at=? WHERE code=? AND is_active=1 AND used_by IS NULL AND expires_at>:now` → rowcount=1 才建用户 | 单语句原子核销，防一码多用 |
   426	| 队列物化 | BEGIN IMMEDIATE → 额度计算 → INSERT …（幂等，NOT EXISTS 防重复）→ 提交 | `uq_rq` 唯一索引防双端同时物化 |
   427	### 4.5 DailyActivity 计数触发细则（口径21 落地）
   428	| 计数 | 触发条件 | 说明 |
   429	|---|---|---|
   430	| new_count | 学习自评/默写完成且**本次新建了 stat 行** | 重学/复习不计数；额度校验读此列 |
   431	| review_count | 复习每完成 1 题（含 D11 重现题） | 额度校验：limit − review_count − 队列 pending 数 |
   432	| correct_count | 任意模式 result=correct（含考核/游戏批量折算） | 自评不计（非判定） |
   433	| wrong_count | 任意模式 result∈{near, wrong}（N2） | 自评不计 |
   434	| 打卡 | 上表任一 upsert 发生即当日行存在 | 04:00 对账仅校准（§4.6） |
   435	### 4.6 LetterStat 聚合流水线（T+1 水位线）
   436	```mermaid
   437	flowchart LR
   438	    W["04:00 任务启动"] --> R["读 system_setting.letterstat_watermark<br/>（已处理的最大 typing_record.id）"]
   439	    R --> S["SELECT * FROM typing_record<br/>WHERE id > 水位线 AND detail_json NOT NULL"]
   440	    S --> P["逐条解析 detail_json：<br/>单字母(a-z) + 相邻bigram<br/>error=ok=false 条目"]
   441	    P --> U["letter_stat upsert：<br/>total+1；avg=(avg*total+x)/(total+1)"]
   442	    U --> M["回写水位线 = MAX(id)"]
   443	    M --> D["剔除超龄数据随 03:00 清理"]
   444	```
   445	**决策记录**：不实时聚合。理由：单题会放大为 ~20 行更新挤占写事务；LetterStat 仅服务每日 AI 建议（口径20/§4.7），T+1 新鲜度足够；结果页热力图使用前端本地数据，不依赖本表。
   446	### 4.7 读路径（高频 SQL 骨架）
   447	**① 复习队列物化（GET /review/today，幂等可重入）**
   448	```sql
   449	-- 剩余额度 = limit − 今日review_count − 队列pending数（应用层算）
   450	INSERT INTO review_queue (user_id, date, word_id, tier, overdue_days)
   451	SELECT :uid, :today, s.word_id,
   452	       CASE WHEN s.next_review_at < :today THEN 0
   453	            WHEN s.proficiency < 40 THEN 1 ELSE 2 END,
   454	       MAX(0, CAST(julianday(:today) - julianday(s.next_review_at) AS INTEGER))
   455	FROM user_word_stat s
   456	WHERE s.user_id = :uid AND s.next_review_at IS NOT NULL
   457	  AND s.next_review_at <= :today
   458	  AND NOT EXISTS (SELECT 1 FROM review_queue q
   459	                  WHERE q.user_id = s.user_id AND q.date = :today
   460	                    AND q.word_id = s.word_id AND q.is_reshow = 0)
   461	ORDER BY (s.next_review_at < :today) DESC,          -- 逾期优先
   462	         CASE WHEN s.next_review_at < :today
   463	              THEN -julianday(:today) + julianday(s.next_review_at)  -- 逾期降序(负号反转)
   464	              ELSE 0 END DESC,
   465	         s.proficiency ASC,                          -- 同层高危优先
   466	         s.word_id ASC
   467	LIMIT :remaining;
   468	-- 取题：SELECT ... FROM review_queue WHERE status='pending' ORDER BY tier, overdue_days DESC
   469	```
   470	**② 仪表盘**：热力图/连续天数 = `daily_activity` 按日期存在性与计数；熟练度分布 = `CASE proficiency` 三桶 GROUP BY；未来 7 天 = `SELECT next_review_at, COUNT(*) … WHERE next_review_at BETWEEN 明日 AND +7d GROUP BY next_review_at`；WPM 趋势 = `typing_record` 按日 AVG（可按 source）。
   471	**③ AI 规则建议（口径20 依序取前 3）**：错误率>60% → `WHERE correct_count+wrong_count>=5 AND wrong_count*1.0/(correct_count+wrong_count)>0.6`；高危明日到期 → `proficiency<40 AND next_review_at=:tomorrow`；连续 7 天未学 → `daily_activity` 最近 7 日无行；指法专项 → `letter_stat WHERE length(letter)=2 ORDER BY avg_delay_ms DESC / error_count DESC`。
   472	---
   473	## 五、一致性保障汇总
   474	| 机制 | 规则 |
   475	|---|---|
   476	| 增量纪律 | 一切计数/熟练度=单条自引用 UPDATE（§4.2）；**禁止 SELECT→计算→绝对值回写**（D2） |
   477	| upsert 规范 | 统一 `INSERT … ON CONFLICT DO UPDATE` 自引用 excluded（daily_activity / letter_stat / advice_cache / wrong_book_item） |
   478	| 幂等双层 | 应用层查 idempotency_record（返回快照）+ 业务表唯一索引兜底（`uq_tr_request` / `uq_exam_idem` / `uq_game_idem`）；并发穿透由唯一冲突捕获 |
   479	| 判分权威 | 前端结果仅展示；`result` 一律服务端 judge 产出后落库 |
   480	| 软删纪律 | 业务查询强制 `is_deleted=0`（Repository 基类）；stat/wrongbook 保留不随词删（v3.1） |
   481	| 写冲突 | `@write_retry` 指数退避×3 → 503 DB_BUSY（AD §3.3）；事务一律短事务，禁止跨请求 |
   482	---
   483	## 六、数据生命周期与清理映射（03:00 任务统一执行）
   484	| 数据 | 保留策略 | 清理动作 |
   485	|---|---|---|
   486	| idempotency_record | 24h | DELETE created_at < now−24h |
   487	| review_queue | date < 今日−7 | DELETE |
   488	| advice_cache | date < 今日 | DELETE |
   489	| game_session | settled/void 后 7d；过期未结算 → void | 状态翻转 + DELETE |
   490	| exam_paper | submitted 保留 30d；void 保留 7d | DELETE（exam_record 独立留存） |
   491	| import_job | confirmed/failed/expired 后 24h | DELETE + 删错误报告文件 |
   492	| typing_record | 热 180d | P4：按月聚合入 LetterStat 后删除明细 |
   493	| 已注销账号 | 软删 30d | **单事务硬删**：user 行（user_id FK 级联带走全部私有表）+ 其私有词库的 word 及这些词的 stat/wrongbook/record 残留 + 私有 book/chapter |
   494	| 备份文件 | 30 份 | 02:00 任务自清理 |
   495	---
   496	## 七、反向决策记录（有意不建的表）
   497	| 候选表 | 不建理由 |
   498	|---|---|
   499	| PracticeSession / 练习组表 | 练习逐题权威判定不依赖组上下文；组内 50% 重现是纯前端编排（D23）；服务端无状态即可满足全部验收 |
   500	| ExamAnswer 逐题明细表 | 数据量小、只读快照、无独立查询需求 → exam_record.detail_json |
   501	| LoginAttempt / 锁定计数表 | 内存 LRU（AD ADR-6），重启清零可接受 |
   502	| AdviceLog 历史表 | advice_cache 按日覆盖已满足「每日生成+刷新计数」，历史无产品需求 |
   503	| Word 全文索引表 | 词量 ≤2 万/库，LIKE + 分页足够；FTS5 列 P4 备选 |
   504	---
   505	## 八、PostgreSQL 迁移对照（P4，仅类型/索引层改动）
   506	| SQLite | PostgreSQL | 备注 |
   507	|---|---|---|
   508	| `INTEGER PRIMARY KEY` | `BIGSERIAL` / `IDENTITY` | — |
   509	| TEXT 时间戳（本地时） | `timestamptz`（存 UTC，读时转 D13 时区） | 读写边缘函数集中收口 |
   510	| TEXT 日期 | `date` | — |
   511	| `INTEGER 0/1` | `boolean` | — |
   512	| `COLLATE NOCASE` 唯一索引 | `UNIQUE INDEX ON lower(spelling)` | — |
   513	| 部分索引 `WHERE …` | 原生支持 | 语法一致 |
   514	| JSON TEXT | `jsonb` | pydantic 校验不变 |
   515	---
   516	## 九、DBD 相对 v3.1 第六章的差异与口径注记（需回评审）
   517	**新增/细化对照**：
   518	| # | 项 | 类型 | 说明 |
   519	|---|---|---|---|
   520	| T1 | exam_paper 表 | 新增 | AD §6.2 exam_token 落库；answer_key 快照防公共词库中途被改；中断作废=置 void |
   521	| T2 | review_queue 表 | 新增 | D11「一次且仅一次」、额度截断、溢出排序的库级保证（P1 验收④） |
   522	| T3 | advice_cache 表 | 新增 | 建议缓存 + D28 刷新计数（3 次/日）落库 |
   523	| R1 | typing_record.result | 细化 | 三态枚举替代 is_correct 布尔（近似态需独立落库） |
   524	| R2 | typing_record.request_id + 唯一索引 | 细化 | D19 幂等的库级兜底层 |
   525	| R3 | word_book/chapter.is_deleted | 补齐 | 级联软删的载体 |
   526	| R4 | 时间/日期类型约定 | 新增 | 见 C2；D13 的库层落点 |
   527	**口径注记（评审确认项）**：
   528	- **N1**：近似错误不改变 conquer_count（不清零、不+1），独立计入 near_miss_count——与「间隔减半、不重现」的第三态定位一致；
   529	- **N2**：DailyActivity.wrong_count 含近似（无独立列）；
   530	- **N3**：游戏内宽松匹配销毁=correct 全权处理（附录 B 原文口径）；
   531	- **N4**：考核/游戏答对**不推进 SM-2 间隔**（仅熟练度与 sync_lifecycle）→ 逾期词考核答对后仍留当日复习队列；
   532	- **N5**：D11 重现题按普通复习题全权计分（SM-2 同权二次结算）；
   533	- **N6**：导入未指定章节时落入该词库「默认章节」（sort_order=0）。
   534	---
   535	**收尾**：本 DBD 与 v3.1/AD 完全对齐，新增三表均有决策依据且不与既有口径冲突。建议下一步：① 把 T1–T3/R1–R4 + N1–N6 回写为 **v3.2 变更提案**（沿用既有格式）；② 输出 **Alembic 初始迁移脚本**（含全部索引，可直接 `alembic upgrade head`）；③ 输出**双端判定同源测试向量 JSON**（§4.2 公式 + judge 规则的共享用例）。需要哪个说一声。
