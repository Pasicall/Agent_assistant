# 个人知识库整改：阶段 0–3 实施与验收记录

执行日期：2026-10-21（Asia/Shanghai）。范围以仓库根目录 `personal-learning-assistant-plan.md` 的阶段 0–3 为准。阶段 4–5 尚未整体实施。

## 完成情况

| 阶段 | 实际修改 | 验证结果 |
| --- | --- | --- |
| 0：确认基线 | 记录分支、提交、迁移版本、模型配置；备份原工作区；建立独立 Conda 环境与固定个人语料 | 原版后端 69 项测试、Ruff、前端构建通过；源码备份逐文件 SHA-256 校验通过 |
| 1：迁出通用接口 | 新增 `api/document_lifecycle.py` 与 `api/diagnostics.py`，迁出版本、下载、恢复、元数据、来源同步、ready、metrics、模型诊断 | 迁移后 OpenAPI 与原版完全一致；无重复路由；原混合模块 15 项测试全部通过 |
| 2：精简前端与访问模型 | 删除治理、平台组件与组织、SSO 请求；同步表单迁入知识空间；退出所有设备迁入账号区域；知识库只接收名称和描述；统一个人所有权范围 | 跨用户访问、历史组织资料隔离、引用与评测限制通过；前端构建和实际浏览器流程通过 |
| 3：移除企业后端 | 删除 `api/enterprise.py`、`api/platform.py`、`api/oidc.py`、`services/operations.py` 及注册；清理审计、平台身份、动态配额调用、企业设置和能力字段 | 19 个原 HTTP 方法/URL 组合从 OpenAPI 移除；不可达请求检查通过；上传、恢复、问答、评测使用静态限制 |

未改变检索算法、分块、提示词、模型适配、重排开关或 standard/agent 模式。核心算法文件、Worker、删除清理服务、DocumentVersions 组件、前端锁文件和全部历史迁移已与原备份逐文件比较，内容一致。

## 阶段 0：数据与回退基线

- 起始分支为 `main`，提交为 `45afb0d559a1e8141cf5e4c20e666e4e8f5e734e`；未创建提交或改动分支。
- 开始时已有 `.gitignore`、`AGENT.md` 修改和未跟踪的方案文件。原内容已备份；`AGENT.md`、方案文件保持原样，`.gitignore` 仅追加 `.conda-env/`。
- 初始仓库没有 `.env`、`data/`，进程环境也没有 `RAG_*` 配置。因此当前项目没有已配置的业务数据库，未发现需要转移的个人资料、组织资料、SSO 账户、动态配额或在途任务。此结论不覆盖仓库外其他项目或未配置的部署。
- 默认模型配置为 hash 向量、原文摘录、空生成模型、未配置重排；保留既有配置默认值。
- Alembic head 为 `006_operations`，整改后保持不变。没有新增 DDL、删除兼容表、重建数据库或重写历史迁移。
- 源码快照位于 `artifacts/phase0-baseline/source-before.tar.gz`，清单为同目录的 `source-sha256.json`；同时保留原工作区差异和状态。
- 固定个人语料包含事务、索引、课程安排三个合成资料文件及四个问题。原版正常入库后，停稳 API 内 Worker 并关闭本地向量客户端，通过现有维护接口协调保存 SQL、上传原件与 Qdrant local；SQLite 使用 backup API。备份位于 `artifacts/phase0-baseline/personal-backup/`，完整性验证通过。
- 新代码将该备份恢复到独立空目录，保留知识库、文档、版本和分块 ID，不重新索引，再执行对照。回退时应将源码快照解到另一个目录核对，匹配协调备份；不要直接覆盖当前开发工作区。已有部署仍应使用其自己的已验证备份。

## 访问与接口行为

当前统一范围为 `KnowledgeBase.user_id == 当前用户` 且 `organization_id IS NULL`。文档、任务、稠密/词项检索、聊天、历史引用和评测沿用这一入口。旧组织资料保留原归属，即使创建者、旧成员或旧授权仍存在，也不会自动出现在个人空间中。

知识库请求和响应已去掉组织、共享和角色字段；旧字段明确返回 422。普通用户信息不再包含平台管理员身份。组织、成员授权、审计、operations、platform 与 OIDC 接口不可调用，运行代码无空审计函数或恒假管理员函数占位。

通用文档与诊断 URL 保持原样。历史版本校验文档归属与版本所属关系，下载保持 uploads 路径边界；恢复仍保留容量预留和新版本任务；元数据调整继续更新知识库代次。来源同步保留配置目录、符号链接、大小和批次数限制，并保留延迟导入，避免循环依赖。metrics 继续使用独立凭证，模型诊断继续要求登录。

问答与评测直接使用应用静态设置，摄取使用传入设置；登录/问答限流、存储统计、所有者锁、待处理任务上限、并发槽位、每日模型预算、幂等、会话租约及长模型调用后的会话校验均保留。

## 实际验证

| 检查 | 结果 |
| --- | --- |
| 原版后端全量测试 | 69 passed |
| 阶段 1 原混合模块回归 | 15 passed，OpenAPI 完全一致、无重复路由 |
| 修改后后端全量测试 | 88 passed，无警告 |
| `python -m ruff check backend scripts` | 通过 |
| `npm run build` | Vue/TypeScript 检查与 Vite 构建通过 |
| `python -m pip check` | 无依赖冲突 |
| 原有 30 问基准 | 修改前后逐问结果一致，仅排除耗时；Hit@5、Recall@5 均为 0.8846153846，不可回答问题拒答率为 1 |
| 5000 分块、40 次持久 BM25 检索 | Top-1 命中 40/40；合成功能门槛通过 |
| 恢复的固定个人语料 | 四问题 × dense/BM25/hybrid，及 standard/agent 两种问答模式通过；ID、排序、正文、引用和回答一致 |
| 备份恢复后的分数精度 | 仅 dense_score 出现浮点末位差异，最大绝对差约 2.18×10⁻⁸；专门采用绝对容差 10⁻⁶，其余字段逐项精确比较 |
| 浏览器实际流程 | 注册、仅名称/描述的知识库创建、来源同步、刷新恢复登录、流式引用问答、历史答案复评完成、系统能力展示、退出所有设备、刷新后要求登录、密码重新登录后资料保留，全部通过 |
| 浏览器请求记录 | 验证时检查了 62 条 API 请求，未请求任何已删除的企业接口 |

新增或调整的回归包括：另一用户无法读取/恢复版本或修改元数据；版本不得跨文档下载或恢复；历史文件下载禁止越过 uploads；旧组织引用不能通过缓存、幂等重放、历史或评测泄漏；静态待处理任务、存储和共享模型预算不受旧动态策略影响；没有新的审计写入；来源同步拒绝越界符号链接；metrics 凭证与普通登录独立。

本轮只调整因删除业务而失效的测试，并补充阶段验收用例。原 `test_enterprise.py` 与 `test_platform.py` 内的通用认证、版本、任务和删除回归仍保留；测试文件按职责的全面整理留待阶段 4。

可复核的原始日志、XML、前后 OpenAPI、语料对照 JSON、基准、页面截图和汇总位于 `artifacts/phase0-baseline/` 与 `artifacts/phase0-3-validation/`。这些是本机验收产物，沿用仓库现有规则不提交到 Git。

## 已创建的开发环境

独立前缀：`/Users/pasical/Code/Agent_assistant/.conda-env`。使用 Python 3.12.15、Node.js 22.23.2，已安装后端必需依赖及 dev、ml、evaluation 可选依赖，并按现有 `package-lock.json` 完成前端安装。没有下载模型权重，也未更改已有的其他 Conda 环境。

仓库新增 `environment.yml` 用于重建环境。Conda 精确包清单和 Python 包清单位于基线产物目录。Tesseract 系统程序和模型服务仍按需要单独配置；安装 Python 适配库不代表 OCR 或真实模型已经就绪。

日常运行可使用两个终端：

```bash
# 终端一
cd /Users/pasical/Code/Agent_assistant
conda activate /Users/pasical/Code/Agent_assistant/.conda-env
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

```bash
# 终端二
cd /Users/pasical/Code/Agent_assistant
conda activate /Users/pasical/Code/Agent_assistant/.conda-env
cd frontend
npm run dev
```

当前没有创建真实模型 `.env`；不配置时沿用离线演示默认值。来源同步入口仅在设置 `RAG_SOURCE_SYNC_ROOT` 后展示。浏览器验收使用了隔离的测试数据和文档源，其临时服务在验收后停止；不会把这些测试账号或资料作为日常默认数据。

## 留待阶段 4–5

企业 ORM 定义尚待阶段 4 解除；现有兼容列、表、外键与历史数据保持不动，应用已无企业业务查询。当前仅为仍需保留的知识库兼容字段添加说明。配置样例、Compose、脚本说明、README 与架构文档的全面清理，以及混合测试的按职责拆分，属于阶段 4，尚未整体实施；其中旧企业功能介绍不能作为当前能力依据。

本地没有可用的真实生成/语义向量模型，也未配置 PostgreSQL、Redis 和 Qdrant server，未执行这些外部服务的集成验证、真实模型 Ragas 评分或真实个人资料的最终全流程验收。本轮 hash/原文摘录对照验证链路与整改一致性，不代表真实语义质量；阶段 5 仍需按方案完成。
