# 多轮 Benchmark 与实时轨迹

## 题包协议

多轮题在 `task.toml` 中使用 `[input] dialogue_file = "dialogue.toml"`。`dialogue.toml` 包含 `schema_version = 1` 和 2 至 32 个 `[[turns]]`，每轮声明 `instruction_file` 及可选 `timeout_seconds`。题包冻结指令文件的 SHA-256，保留单题总超时和独立评分超时。执行器在上一轮成功结束后读取下一轮文件。指令文件和 Judge 材料保存在题包资源中，未来轮次的内容不进入 Agent 请求或初始工作区。[三轮示例](../benchmarks/dialogue_session/dataset.toml) · [轮次声明](../benchmarks/dialogue_session/tasks/dialogue-001/dialogue.toml)

多轮 Release 选择实现 `liteeval.dialogue/v1` 的 Agent Harness。一次执行使用一种输入模式，原单 Prompt 题继续使用单轮协议。多轮执行当前使用固定顺序和离线 command checks。条件分支、异步事件调度、LLM User Agent 和中断后恢复留作后续扩展。

## Harness 与运行

每轮调用同一个已注册命令 Harness，保留一次 Rollout 的工作区。stdin 包含 `rollout_id`、`turn_index`、`session_id`、本轮 `instruction`、公开输入文件及交付规则。首轮 `session_id` 为 `null`。stdout 按 JSONL 输出 `schema_version=1` 的 `session`、`assistant`、`tool_call`、`tool_result` 和终止 `result`。终止记录包含 `session_id`、`success`、`output` 及可选 `usage`、`usage_scope`。普通日志写 stderr。会话变化、缺失终止记录或超时会结束本题并保留已落库事件。

Codex 适配器首轮运行 `codex exec --json`，后续用同一 Thread ID 执行 `codex exec resume --json`。它为每个 Rollout 使用独立会话目录，用进程中的 `MINIMAX_API_KEY` 调用配置的 MiniMax Responses 端点。随 CLI 流记录已出现的回复、工具调用与回执。验收使用一个受限的 `write_submission` 工具，允许写入题目声明的文件及大小范围，保留 Codex shell 的工作区权限。Codex 0.158.0 的 JSONL 用量是会话累计值，记录保留原值，逐轮统计使用相邻累计差，缺失值保持未知。[Codex CLI](https://learn.chatgpt.com/docs/non-interactive-mode) · [MiniMax 接入](https://platform.minimax.cn/docs/m-plan/codex) · [适配器](../harnesses/codex_dialogue/agent.py)

从仓库根目录运行。`--read-key` 使用隐藏输入，已经在执行进程配置 `MINIMAX_API_KEY` 时可省略。命令默认选择 `MiniMax-M3.1-Flash` 和中国区端点。若 Windows 的 `codex` 是 npm shim，可用 `LITEEVAL_CODEX_EXECUTABLE` 指向实际 `codex.exe`。

```powershell
python -m examples.run_dialogue_benchmark --db data/dialogue/liteeval.db --export data/dialogue/result.zip --trusted-local --read-key
```

启动后立即输出 Project ID 和 Execution ID。`--trusted-local` 显式使用本机工作目录。Docker 执行用 `--docker-image REGISTRY/IMAGE@sha256:DIGEST`，镜像须提供 Python 与 Codex CLI，执行机须可访问 MiniMax。Docker 配置作为不同的 Release 冻结。

## 实时轨迹与评分

API 和运行使用同一个 SQLite 数据库。按 [Owner 初始化](step-1-backend.md) 配置本机 API 后，启动 API、签发项目 Key 并启动控制台。将 `PROJECT_ID` 替换为运行输出中的实际值。

```powershell
python -m liteeval.interfaces.cli.main --db data/dialogue/liteeval.db api
python -m liteeval.interfaces.cli.main --db data/dialogue/liteeval.db key PROJECT_ID --label trajectory
node console/server.js --api-port 8765
```

打开 `http://127.0.0.1:7100/trajectory.html`，输入 Execution ID 与 LiteEval 项目 Key。此页从 `GET /v1/benchmark-executions/{id}/events?after=0` 分页读取已持久化事件，可用 `task_id` 筛选。浏览器按轮显示指令、会话、可见回复、工具调用、用量及状态。重新连接从事件序号零开始回放。原型其他页面继续使用各自的演示数据。[实时轨迹页面](../console/trajectory.html)

Judge 在独立环境下读取 `submission/` 和平台冻结的 `evidence/trajectory.jsonl`。重评使用原证据并要求原评分 Harness 摘要一致，ZIP 将相同轨迹保存到 `trajectories/<task-id>.jsonl`。示例 Judge 同时核对三轮开始与结束顺序、唯一会话 ID 和实际文件中的编号、币种、最终金额。

## 实测记录

2026-11-05，Codex CLI 0.158.0 与 `MiniMax-M3.1-Flash` 在 Windows 本地工作目录完成两次独立会话，运行 `edc41510cd5d478a82a1483bc352a325` 和 `b93f9c3e71664d84a14abd4b867dc70e`。每次三轮由各自的同一 Codex Thread 完成，第一轮提供 `DX-7d1a92a6`，第二轮延续该编号，第三轮写出 `{ "reference": "DX-7d1a92a6", "currency": "GBP", "amount": 47 }`。两次独立评分均为 1.0，耗时 14.327 秒和 13.406 秒。最新运行 ZIP 内含实际文件、18 条运行事件及由其中 14 条组成的逐轮证据。Edge 在首次成功运行中读取增量事件，刷新后还原相同三轮内容，并复核了最新运行的回放。导出与报告的凭据模式扫描为零。

离线测试覆盖并行会话隔离、后续指令隔离、流式观察、超时、会话 ID 变化、缺失终止事件、累计用量差分、轨迹重评与导出。此次真实验收使用本地工作目录。Docker Desktop 当前无法启动守护进程，Docker 多轮验收仍需在可用执行机完成。
