# LiteEval

LiteEval 是本地优先的 Agent 回归评测后端。当前版本 **0.4.2**，使用 Python 3.11+、SQLite、本地工件目录，以及用于凭据加密的 cryptography。另附尚未接通后端的 [控制台交互原型](console/README.md)。首次运行先执行 `python -m pip install -e .`。

它实现以下闭环：生产 Trace → Case → 数据集不可变快照 → 实验 → Agent 执行与评分 → 指标汇总 → Case 级版本比较。

0.2.1 已补齐第一步的后端准备：浏览器 Owner 会话与 CSRF、轻量实验进度、分页结果、Dataset/Case/Evaluator 归档、Case 编辑冲突检查、独立 Worker 评分预览及 CSV 导出。数据库自动增量迁移至 schema 3；原有 API Key、CLI、SDK 和评测闭环继续支持。完整启动与 PowerShell 操作见 [第一步操作指南](docs/step-1-backend.md)。

0.2.2 进一步增加导入预览、评分器修订历史、命名数据集版本、实验预检哈希和 Worker 就绪上报，数据库升级到 schema 4。前端原型可按 [接口约定](docs/frontend-backend-contract.md) 对接；已有数据库升级前停止 API/Worker 并备份，再统一启动新版。预检不调用外部服务，Worker 状态是提示信息，尚未实现上线 Gate。

## 目录式 Benchmark（0.4.0）

后端已按 domain、ports、application、integrations、infrastructure、interfaces 分层；旧 Python 模块路径已删除，没有兼容转发层。新目录链路使用 Ground Truth、固定资源发布、独立 Agent/Evaluation Harness、真实文件采集和隔离评分。数据库 schema 6。

```bash
python -m examples.native_benchmark
```

这会运行两道离线计算题，产生实际子进程日志、JSON/Markdown/二进制产物、评分、重评记录与 ZIP。它是确定性程序的执行验收，模型调用数为 0，不是 LLM 或官方 FAB 成绩。配置和真实能力边界见 [目录 Benchmark 操作指南](docs/benchmark-packages.md)，新架构见 [架构说明](docs/architecture.md)。

## 数据与连接能力

0.4.1 将固定来源的 FAB v2 公开集转换为原生目录包：27 题、239 条逐项 rubric、原始 severity/must-pass、研究能力预检及 Partial Credit/All-Pass。`python -m examples.fab_v2 prepare` 仅准备题库；新增 `liteeval.qianwen-research/v1` 使用明确保存的 API 连接进行云端检索、提取和计算，通过显式提交工具生成真实文件。已用用户配置的千问平台 Key 对公开第 3、4 题完成 Qwen/GLM 共四次研究和双 Judge 诊断，未使用 Codex 登录。结果及解析修复见 [真实试跑报告](docs/fab-qianwen-trial.md)，接入步骤见 [原生 FAB 指南](docs/fab-v2-native.md)。

FAB 两种接入、历史实现归属及示例入口统一见 [Benchmark 索引](benchmarks/README.md) 和 [示例索引](examples/README.md)。旧 Dataset/Codex FAB 路径已删除。

0.3.0 新增项目级模型连接与 API Key 加密保存，可供 HTTP Agent、直接模型调用和 LLM Judge 使用；新增运行中本地日志的游标查询、Target/Judge/重试用量记录，以及实验或单条 Run 的 JSON/JSONL/CSV/ZIP 导出。数据库升级到 schema 5。完整配置、请求示例、统计口径和边界见 [连接与 Rollout 操作指南](docs/connections-and-rollouts.md)。User Agent 暂不纳入本轮。

| 目标 | 已实现 | 验证情况 |
| --- | --- | --- |
| 真实项目后端 MVP | 分页筛选、JSON/CSV 批量导入导出、HTTP 请求与响应映射、环境变量认证、规则与 LLM Judge、质量/执行指标、Case Compare | HTTP/API 闭环实测；Qianwen 研究 Harness 与两种真实 Judge 已完成公开两题诊断 |
| 可恢复评测服务 | Case 并发、Agent/Judge 独立重试、取消、租约心跳、崩溃接管、结果写入隔离、提交幂等、项目密钥、原始响应与工件 | 本地集成测试覆盖并发、重试、取消、恢复和鉴权 |
| 执行过程与任务结果评测 | 事件 v2、Python SDK、工具参数评分、Codex JSONL Adapter、独立工作目录、Docker 环境接口、文件/测试最终状态验证 | SDK、真实子进程/文件和 Linux CI 实际 Docker 已验证；Codex Adapter 仅替身 CLI 测试，本轮研究未使用它 |

这是可以接入自己的 HTTP Agent 和目录研究 Harness 的后端 MVP。真实 Qianwen 模型试跑见上述报告，Docker 已通过 Linux CI 的实际容器验收；正式生产部署、更多题目的质量校准与其他框架接入仍需继续完成。

## 一键体验

```bash
python -m examples.platform smoke
python -m examples.platform readiness
python -m examples.platform catalog
python -m unittest discover -s tests -v
```

Demo 自动启动本地 API 和两个示例 Agent，执行 Trace 转 Case、批量导入、实验与比较，然后关闭服务。预期输出：v1 通过率 0.5，v2 通过率 1.0，1 个 Case 改善、1 个不变。完整结果写入 `data/demo/report.json`。

## 启动常驻服务

浏览器 Owner 模式可先执行 `python -m liteeval.interfaces.cli.main owner-init` 交互设置密码，再执行下面的 API/Worker 命令。API Key 管理员模式仍按下述环境变量启动。Owner 登录接口与会话用法见 [第一步操作指南](docs/step-1-backend.md)；Web 页面将在下一步实现。

生成管理员密钥，只在本机保存：

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
export LITEEVAL_ADMIN_TOKEN='替换为生成的密钥'
python -m liteeval.interfaces.cli.main api
```

另一个终端启动 Worker，凭据必须配置在 Worker 的环境中：

```bash
export AGENT_TOKEN='你的 Agent 密钥'
export LITEEVAL_JUDGE_API_KEY='你的 Judge 密钥'
python -m liteeval.interfaces.cli.main worker
```

API 默认 `127.0.0.1:8765`，数据库默认 `data/liteeval.db`。所有接口除 `GET /v1/health` 外都要求 `Authorization: Bearer TOKEN`。管理员创建项目、Agent/Evaluator 配置及项目密钥；项目密钥可管理本项目的 Trace、数据集和实验。密钥签发时只返回一次明文。

```bash
curl http://127.0.0.1:8765/v1/projects \
  -H "Authorization: Bearer $LITEEVAL_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' -d '{"name":"Support"}'
```

完整请求及配置见 [API 文档](docs/api.md)。接入实际服务、密钥、升级备份、Docker 和联调流程见 [运行指南](docs/runbook.md)。设计及指标口径见 [架构说明](docs/architecture.md)。测试范围见 [验证记录](docs/verification.md)。

## CLI

```bash
python -m liteeval.interfaces.cli.main --db data/liteeval.db worker --once
python -m liteeval.interfaces.cli.main run EXPERIMENT_ID
python -m liteeval.interfaces.cli.main cancel EXPERIMENT_ID
python -m liteeval.interfaces.cli.main compare LEFT_ID RIGHT_ID
python -m liteeval.interfaces.cli.main key PROJECT_ID --label sdk
python -m liteeval.interfaces.cli.main judge-check examples/config/judge.json
```

`--db` 位于子命令之前；也可以使用 `LITEEVAL_DB`。`judge-check` 会请求配置指定的真实服务并消耗其额度，先修改示例中的供应商地址和模型名。

## 重要语义

- `completed` 表示实验处理完毕；质量通过率使用 `summary.success_rate`，执行成功率使用 `summary.execution_success_rate`。
- 缺少预期答案、轨迹或最终状态会产生 `abstain`；Judge/配置错误产生 `error`，均不冒充质量通过。
- Compare 必须具有相同 Case 修订和 Evaluator 修订。通过 `rerun` 保留原实验快照，仅更换 Agent。
- HTTP 重试及崩溃恢复采用至少一次调用，始终携带同一 Run 的 `Idempotency-Key`。外部 Agent 必须实现去重才能避免重复副作用。
- 工作目录隔离便于重现任务；Docker 提供额外进程/资源限制。真实环境与当前模型需要单独验收，详细边界见运行指南。
