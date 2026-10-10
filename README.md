# LiteEval

LiteEval 是本地优先的 Agent 回归评测后端。当前版本 **0.2.0**，使用 Python 3.11+ 标准库、SQLite 和本地工件目录，不包含前端。

它实现以下闭环：生产 Trace → Case → 数据集不可变快照 → 实验 → Agent 执行与评分 → 指标汇总 → Case 级版本比较。

## 当前能力

| 目标 | 已实现 | 验证情况 |
| --- | --- | --- |
| 真实项目后端 MVP | 分页筛选、JSON/CSV 批量导入导出、HTTP 请求与响应映射、环境变量认证、规则与 LLM Judge、质量/执行指标、Case Compare | HTTP/API 闭环实测；Judge 用本地模拟服务验证协议，真实模型需要配置后联调 |
| 可恢复评测服务 | Case 并发、Agent/Judge 独立重试、取消、租约心跳、崩溃接管、结果写入隔离、提交幂等、项目密钥、原始响应与工件 | 本地集成测试覆盖并发、重试、取消、恢复和鉴权 |
| 执行过程与任务结果评测 | 事件 v2、Python SDK、工具参数评分、Codex JSONL Adapter、独立工作目录、Docker 环境接口、文件/测试最终状态验证 | SDK、真实子进程/文件操作已验证；Codex 使用替身 CLI，Docker 尚未启动真实容器 |

这是可以接入自己的 HTTP Agent 的后端 MVP。实际生产服务、真实模型质量校准和 Codex/Docker 环境验收仍需按下面文档完成。

## 一键体验

```bash
python examples/run_demo.py
python -m unittest discover -s tests -v
```

Demo 自动启动本地 API 和两个示例 Agent，执行 Trace 转 Case、批量导入、实验与比较，然后关闭服务。预期输出：v1 通过率 0.5，v2 通过率 1.0，1 个 Case 改善、1 个不变。完整结果写入 `data/demo/report.json`。

## 启动常驻服务

生成管理员密钥，只在本机保存：

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
export LITEEVAL_ADMIN_TOKEN='替换为生成的密钥'
python -m liteeval.cli api
```

另一个终端启动 Worker，凭据必须配置在 Worker 的环境中：

```bash
export AGENT_TOKEN='你的 Agent 密钥'
export LITEEVAL_JUDGE_API_KEY='你的 Judge 密钥'
python -m liteeval.cli worker
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
python -m liteeval.cli --db data/liteeval.db worker --once
python -m liteeval.cli run EXPERIMENT_ID
python -m liteeval.cli cancel EXPERIMENT_ID
python -m liteeval.cli compare LEFT_ID RIGHT_ID
python -m liteeval.cli key PROJECT_ID --label sdk
python -m liteeval.cli judge-check examples/judge_config.json
```

`--db` 位于子命令之前；也可以使用 `LITEEVAL_DB`。`judge-check` 会请求配置指定的真实服务并消耗其额度，先修改示例中的供应商地址和模型名。

## 重要语义

- `completed` 表示实验处理完毕；质量通过率使用 `summary.success_rate`，执行成功率使用 `summary.execution_success_rate`。
- 缺少预期答案、轨迹或最终状态会产生 `abstain`；Judge/配置错误产生 `error`，均不冒充质量通过。
- Compare 必须具有相同 Case 修订和 Evaluator 修订。通过 `rerun` 保留原实验快照，仅更换 Agent。
- HTTP 重试及崩溃恢复采用至少一次调用，始终携带同一 Run 的 `Idempotency-Key`。外部 Agent 必须实现去重才能避免重复副作用。
- 工作目录隔离便于重现任务；Docker 提供额外进程/资源限制。真实环境与当前模型需要单独验收，详细边界见运行指南。
