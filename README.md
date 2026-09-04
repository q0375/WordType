# WordType · 打字背单词系统

单词学习与打字速度练习系统 —— Vue 3 + TypeScript 前端，FastAPI + SQLite 后端。

## 功能模块

| 模块 | 说明 |
|------|------|
| 认证 | JWT 7天滑动续期、pwd_ver 改密失效、登录锁定、邀请码注册 |
| 词库 | CRUD、三步导入（CSV/TXT/XLSX）、克隆、导出、章节管理 |
| 学习 | 自评卡（D8）+ 默写卡（D17）、每日新词额度、续学位置 |
| 练习 | 四种题型（默写/选择/听音/挖空）、断点续练（D12）、离线重放（D19） |
| 复习 | SM-2 间隔重复、D16 自评映射、D11 当日重现、每日额度 |
| 考核 | 限时组卷、D20 计分、失焦统计、幂等交卷、中断作废 |
| 游戏 | Canvas 60fps 下坠打字、Combo 阶梯、D27 服务端防作弊校验 |
| 错题本 | D10 攻克计数（连对3次移出）、置顶、手动移入/移出 |
| 统计 | 打卡热力图、熟练度分布、WPM/准确率趋势、未来7天复习量 |
| AI建议 | P3 规则引擎 + D28 可选 LLM 接入、每日3次刷新 |
| 管理端 | 用户管理、重置密码、邀请码批量生成、AI 配置 |

## 技术架构

```
前端：Vue 3 + TypeScript + Vite + Pinia + Vue Router + ECharts + Canvas
后端：FastAPI + SQLAlchemy(async) + SQLite(WAL) + APScheduler + JWT + bcrypt
部署：Nginx 反代 + systemd 守护 + 单机单进程（≤100并发）
```

## 快速开始

### 后端

```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env  # 修改 JWT_SECRET / AES_KEY
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

首次启动自动建表 + 创建管理员（admin/admin123456）+ 种子公共词库。

### 前端

```bash
cd frontend
npm install
npm run dev
```

访问 http://localhost:5173 ，API 自动代理到后端 8000 端口。

### 生产构建

```bash
cd frontend
npm run build
# 产物在 dist/，部署到 Nginx 静态目录
```

### 后端冒烟测试

```bash
cd backend
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
.venv/Scripts/python scripts/smoke_test.py   # 全链路用例：注册/幂等重放/考核/游戏/防作弊等
```

## 实现说明（相对设计文档的落定与偏差）

- 技术栈按根 README 口径为 **Vue 3 + Pinia + Vue Router + Element Plus**（需求 v3.2 中 React 条目不采纳，以架构文档 §2.2 目录结构与原型为准）。
- 后端导入解析当前为同步实现（≤20000 行内一次完成，接口契约保持三步制不变）；`≥1000 行异步` 预留 executor 挂点。
- 练习 choice 题的正确项 key 存进程内（单进程架构 ADR-1 下等价于服务端状态，重启仅影响未完成练习组）。
- 练习 listening 题型 payload 落定为 `{spelling}`（客户端 TTS 需要拼写才能合成读音，规范原文 `{}` 无法闭环）。
- 游戏结算复核中，服务端重算分不含高度加成（客户端数据不可得），与客户端分偏差 >5% 时以服务端值为准。
- 前端统计图表使用轻量 SVG/组件渲染，未引入 ECharts（后续可替换，不影响契约）。
- 预览产物（dist 快照、演示快捷登录、模板残留地图组件）已清除；`docs/快搭原型要点速览.md` 为原快搭产物需求要点的存档。

## 项目结构

```
encn/
├── backend/
│   ├── app/
│   │   ├── main.py              # 入口 + lifespan
│   │   ├── core/                # config/security/errors/idempotency/ratelimit/logging
│   │   ├── db/                  # engine/session/retry/init_db
│   │   ├── domain/              # judge/sm2/proficiency/exam_scoring/game_score/advice（纯函数）
│   │   ├── schemas/             # pydantic 请求/响应
│   │   ├── repositories/        # 隔离基类
│   │   ├── services/            # 业务编排
│   │   ├── api/v1/              # 全部路由
│   │   └── tasks/               # APScheduler 定时任务
│   ├── data/                    # SQLite 数据库
│   ├── backup/                  # 每日备份
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── main.ts / App.vue
│   │   ├── styles/tokens.css    # DS 设计变量（light+dark）
│   │   ├── router/              # 路由表 + 守卫
│   │   ├── stores/              # Pinia（auth/settings/quota）
│   │   ├── api/                 # Axios 实例 + 模块化 API
│   │   ├── core/                # judge/offline/stats/idempotency
│   │   ├── composables/         # useTypingInput/useTTS/useCountdown...
│   │   ├── game/                # Canvas 引擎（loop/entities/systems/renderer）
│   │   ├── components/          # base + biz
│   │   └── views/               # 全部页面
│   └── package.json
├── deploy/                      # Nginx/systemd/logrotate/部署脚本
└── docs/                        # 8 份设计文档（需求/架构/数据库/API/UI/流程）
```

## 设计决策

- **单进程后端**：Uvicorn 1 worker + asyncio，SQLite WAL 单写者，APScheduler 不重复调度
- **双端判定，服务端权威**：前端同算法预判定保证 0ms UX，计分一律以服务端结果为准
- **一切写操作幂等**：头幂等（exam/game）+ body 幂等（单题 request_id），24h 去重
- **增量熟练度**：`UPDATE ... SET proficiency = MIN(100, proficiency + delta)` 天然免读改写竞态
- **时间只有服务器一个源头**：TZ 锁定环境变量，前端日期字段一律服务端下发
- **隔离下沉到 Repository 层**：业务代码禁止裸写 user_id 过滤

## 默认账号

- 管理员：`admin` / `admin123456`（首次启动创建，请立即修改）
- 公共词库：CET-4 核心词汇（示例，30 词）

## API 文档

启动后端后访问 http://127.0.0.1:8000/docs 查看自动生成的 OpenAPI 文档。

完整接口契约见 `docs/接口规范与通信协议.md`。
