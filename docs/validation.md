# 个人知识库第一版：整改与验证

验证日期：2026-10-22（Asia/Shanghai）。本轮承接阶段 0–3，完成阶段 4 的模型、配置、测试及文档整理，并按阶段 5 验证个人资料链路。当前结果不引用旧版企业功能验收作为通过依据。

## 实际变更

- 删除 Membership、KnowledgeBaseGrant、AuditEvent、OIDCIdentity、OIDCFlow、OperationalPolicy 的运行 ORM 定义。Organization 仅保留外键所需的 id；知识库兼容列保留并注释用途。
- 保留全部冻结历史迁移，Alembic head 仍为 `006_operations`。旧表与数据不删除，不自动变更组织资料归属或旧登录方式。
- 清除 Compose、生产配置样例的 OIDC 和平台管理员字段。生产模式、普通认证、静态限制、Worker 与存储适配保持。
- 按认证、文档版本、清理、静态限制、并发、流式、诊断、来源同步、迁移等职责迁移原混合测试。阶段 3 的 64 个测试函数全部保留；参数化展开后从 88 项增加到 94 项。
- README、架构、设计与部署说明改为个人资料定位。旧截图和 2026-10-15 至 18 日的企业说明、验证记录移动到 `docs/history/`，标注为历史资料；原始图片和 JSON 保持内容不变。
- 初始化脚本默认使用仓库内 `.conda-env`，仍允许显式指定环境。校验脚本不再使用过期的固定输出日期或覆盖旧版验收记录；Docker 构建上下文排除本机 Conda、私密配置和验收产物。

## 回归中修复的边界

删除单份文档时，原实现只检查其知识库是否仍属于当前账号，因此旧会话与历史复评可能再次使用该文档的已保存证据。新增失败用例复现后，统一检查保存引用的文档是否仍存在且属于当前个人范围，覆盖历史展示、后续对话上下文、评测结果、创建复评及排队评测执行。

仍存在且归当前用户所有的文档，其历史版本可继续下载和恢复；不把非活动版本一律认定为越权。检索算法、提示词、切块、向量模型适配和引用/数值核验策略未修改。Worker 只调整了历史复评的来源检查。

## 自动化与资料流程

| 检查 | 实际结果 |
| --- | --- |
| 后端全量 Pytest | 94 passed |
| Ruff | 通过 |
| 前端 TypeScript / Vite | 通过 |
| pip check | 无依赖冲突 |
| 30 问合成基准 | Hit@5、Recall@5 均为 0.8846153846；4 条不可回答问题全部拒答；与阶段 0、3 的逐题结果一致（排除耗时） |
| 5000 分块持久 BM25 | 40/40 首位命中 |
| 仓库真实文档，离线模式 | architecture.md 与 deployment.md 入库、去重、标准/Agent 问答、版本原件、历史复评、Dense/Hybrid 对照、SSE、design.md 替换、恢复原版本、删除及持久清理通过 |
| 阶段 3 / 修改后离线对照 | 同一备份恢复、同一配置、31 个分块、3 道问题：Dense/BM25/Hybrid 的片段、ID、排序一致，六次摘录答案一致；向量得分最大浮点差 1.49×10⁻⁸，低于 10⁻⁶ 容差 |
| 浏览器 | 注册、仅名称/描述的新建表单、实际文档同步、刷新恢复登录、两个个人库切换、带来源问答、退出及密码重新登录通过；请求审查未发现已删除接口 |
| 已删除模块 | 21 组旧方法/路径在匿名、登录两种状态均返回 404；OpenAPI 无企业、平台、OIDC 路由与旧请求结构 |
| 数据兼容 | 全新数据库、旧 v1 数据升级、协调备份恢复、兼容表存在且不进入运行元数据、最小外键解析通过 |
| 认证与资源保护 | 跨用户隔离、会话撤销、防跨站、限流、存储/待处理任务、并发、每日预算、幂等、停止生成与长调用后撤销检查通过 |

`docs/artifacts/personal-v1-validation.json` 是本轮精简汇总。原始日志、XML、完整语料/引用与比较结果保存在忽略提交的 `artifacts/phase4-5/`。本轮网页截图为 README 中的个人版截图，使用隔离验收账号与仓库架构文档。

## 真实模型对照：按用户要求停止

2026-10-22 已按用户要求停止本地模型验收，并关闭本轮临时启动的 Ollama 服务。未下载新权重，未改动日常 `.env`。2026-10-24 用户配置了远程 DeepSeek API，并授权仅验证不依赖真实 Embedding 的部分，结果见下节。

停止前，阶段 3 基线使用已有 `qwen3:4b` 与 `qwen3-embedding:0.6b`（实际 1024 维）完成了三道问题的标准/Agent 共六次问答。六次生成均未通过现有数值核验，最终返回原文摘录；摘录中的关键事实与引用下载有效。这只能证明模型适配及回退链路可运行，不能作为生成答案质量通过的依据。

修改后的真实模型运行在完成第一道问题后中断，未形成完整前后对照。真实语义检索与生成质量验收保留为未完成；未执行真实 Ragas 评分。下方离线检查仅用于验证工程流程和固定配置下的行为一致性。

## 2026-10-24 补充：远程 DeepSeek 生成链路

使用用户在本机 `.env` 配置的 DeepSeek `deepseek-flash` API，保留哈希向量作为流程支撑，关闭本地重排、视觉模型与 OCR；不调用真实 Embedding，不运行本地模型。全部资料来自仓库架构和部署文档，使用临时 SQLite / Qdrant local，不打开已有业务数据库，不改写用户 `.env`。

| 检查 | 本次实际结果 |
| --- | --- |
| 项目实际模型适配器 | DeepSeek API 连通，一次真实生成返回有效答案 |
| 三题 × 标准/Agent | 6/6 返回 DeepSeek 生成答案，关键事实 50、800/120、600 与引用核对通过；最终摘录回退 0 次 |
| 引用与原件 | 生成答案引用的文档、版本原件下载通过 |
| 请求重放与答案缓存 | 同一请求重放保持消息 ID；答案缓存复用时生成调用数为 0 |
| 真实流式输出 | 关闭验收进程中的答案缓存后，收到 42 个模型草稿事件及最终结果；经一次有界修订后数值核验通过 |
| 普通评测 / 历史复评 | 均完成；历史复评保留原答案与引用，未开启 Ragas |
| 空库问答 | 拒答且无引用，未调用生成模型 |
| 文档生命周期 | 上传、去重、替换、恢复原版本、删除、旧历史隐藏和持久清理通过 |
| 后端回归 / 前端构建 / Ruff | 94 项后端测试通过；TypeScript / Vite 构建与 Ruff 通过 |

数值核验是局部检查，本次未执行 LLM 语义事实核验。模型调用中出现过有界修订，最终答案通过现有核验；没有为通过验收修改提示词、核验阈值或检索算法。这三道问题只能说明生成链路与资料操作可运行，不能代表普遍回答质量，也不构成真实语义检索前后对照。

精简结果见 [DeepSeek 验证汇总](artifacts/deepseek-generation-validation.json)；完整输出及日志位于忽略提交的 `artifacts/deepseek-2026-10-24/`。真实语义 Embedding、Ragas、真实重排和更大题集的质量验收继续保留为未完成。

只验证远程生成链路时，从仓库根目录执行：

```bash
conda activate ./.conda-env
python scripts/validate-personal.py --mode generation --lifecycle --output artifacts/deepseek-generation.json
```

此模式读取已配置的远程生成接口，仅在验收进程中显式使用哈希向量、数值核验，并禁用本地重排与视觉推理。它会产生真实 API 请求；`--with-ragas` 仅允许与完整语义配置的 `--mode configured` 一起使用。

## 可复现检查

从仓库根目录执行：

```bash
conda activate ./.conda-env
python -m ruff check backend scripts
python -m pytest -q backend/tests
python scripts/benchmark.py --load-chunks 5000 --queries 40 --output artifacts/benchmark.json
python scripts/validate-personal.py --lifecycle --output artifacts/personal-validation.json
cd frontend
npm run build
```

`validate-personal.py` 每次新建隔离 SQLite / Qdrant local，默认使用真实仓库文档和明确标注的 hash/摘录模式。可用 `--mode generation` 仅验证远程生成，或用 `--mode configured` 读取完整语义模型配置；`--snapshot` 先停稳 Worker 和关闭向量客户端再做协调备份，`--restore` 仅写入新的空目标，`--compare` 校验模式、ID、排序、证据和关键事实。`--with-ragas` 在 configured 模式中执行可选真实模型评分。CI 新增离线实际文档流程，外部服务任务继续覆盖 PostgreSQL、Redis、Qdrant server。

## 验证边界

当前没有本机 Docker/PostgreSQL/Redis/Qdrant server 配置，本轮没有运行容器构建、完整 Compose 或真实服务集成；Compose 已做 YAML 与应用字段契约检查，原有 CI 服务集成任务保留。PowerShell 脚本在本机仅做静态核对，未在 Windows 执行。

可选重排保留适配与单次执行的模拟回归，Ragas 保留预检和历史回放等路径回归；本轮未加载真实 CrossEncoder 权重，也未执行真实 Ragas 评分。

hash 模式只验证工程链路，真实模型小题集也不代表普遍质量。试探题“RRF 融合排名公式中的常数是多少”在离线模式下因现有证据过滤而保守拒答；本轮按方案保持检索策略，不为提高验收分数修改阈值或提示词。该限制与数值、公式类问题的效果应在后续独立评测中观察。

## 专题学习 v0.2.0（2026-10-31）

本轮在当前工作区执行，未调用外部模型，未修改个人业务数据库：

| 检查 | 结果 |
| --- | --- |
| 后端全量 `python -m pytest -q` | **114 passed**，约 12 秒；含 20 项新增学习测试 |
| `python -m ruff check backend scripts` | 通过 |
| 前端 `vue-tsc --noEmit` + `vite build` | 通过 |
| 新增迁移 | SQLite 新库、旧库保留数据、重复启动测试通过，head 为 `007_learning` |
| 学习闭环 | 固定模型响应夹具下：目标确认、提交、疑似/重复证据、提示、任务分流、纠正、完成路径通过 |
| 可靠性 | 跨用户隔离、幂等、过期/删除来源、在途删除、旧租约隔离、失败重试、预算上限测试通过 |
| 实际浏览器 | 隔离服务中验证学习入口、任务、第二级原文帮助、提示记录、提交保存和无模型失败提示；截图见更新说明 |

固定响应测试验证服务端如何处理模型输出，不能证明真实模型诊断准确。真实模型语义案例集、教学专家题库审定、实际 PostgreSQL/Redis/Qdrant server 和多 Worker 组合未在本轮验收，见[更新说明与未完善项](learning-workbench-v0.2.0-release.md)。

## 专题学习 v2 补强（2026-11-14）

| 检查 | 结果 |
| --- | --- |
| 后端全量 `pytest -q` | **135 passed**，约 30 秒；含 3 项新增：续题批次与序号、续题失败回退与重试、v2 障碍派生 |
| `python -m ruff check backend scripts` | 通过 |
| 前端 `vue-tsc --noEmit` + `vite build` | 通过 |
| 续题接口 | 批次用尽后 `POST /workspaces/{id}/practice` 生成新题并回到 `active`；重复请求键返回同一作业；有当前任务时拒绝 |
| 障碍线索 | 由评分标准的 `focus` 派生，只在“未满足或部分满足且能定位原句”时产生；单次作答仅记为疑似，两题家族后才确认 |

本轮仍使用固定模型响应夹具，未调用真实模型；真实语义质量、人工标注案例集与 PostgreSQL/Redis/Qdrant 多 Worker 组合仍未验收。
