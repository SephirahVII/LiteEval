# 多轮 Benchmark 与实时轨迹

## 题包协议

多轮题在 `task.toml` 中使用 `[input] dialogue_file = "dialogue.toml"`。`dialogue.toml` 包含 `schema_version = 1` 和 2 至 32 个 `[[turns]]`，每轮声明 `instruction_file` 及可选 `timeout_seconds`。题包冻结指令文件的 SHA-256，保留单题总超时和独立评分超时。执行器在上一轮成功结束后读取下一轮文件。指令文件和 Judge 材料保存在题包资源中，未来轮次的内容不进入 Agent 请求或初始工作区。[三轮示例](../tests/fixtures/dialogue_session/dataset.toml) · [轮次声明](../tests/fixtures/dialogue_session/tasks/dialogue-001/dialogue.toml)

多轮 Release 选择实现 `liteeval.dialogue/v1` 的 Agent Harness。一次执行使用一种输入模式，原单 Prompt 题继续使用单轮协议。多轮执行当前使用固定顺序和离线 command checks。条件分支、异步事件调度、LLM User Agent 和中断后恢复留作后续扩展。

## Harness 与运行

每轮调用同一个已注册命令 Harness，保留一次 Rollout 的工作区。stdin 包含 `rollout_id`、`turn_index`、`session_id`、本轮 `instruction`、公开输入文件及交付规则。首轮 `session_id` 为 `null`。stdout 按 JSONL 输出 `schema_version=1` 的 `session`、`assistant`、`tool_call`、`tool_result` 和终止 `result`。终止记录包含 `session_id`、`success`、`output` 及可选 `usage`、`usage_scope`。普通日志写 stderr。会话变化、缺失终止记录或超时会结束本题并保留已落库事件。

自建多轮 Harness 声明 `role="agent"` 和 `protocol="liteeval.dialogue/v1"`，负责以同一会话执行连续指令。支持已保存模型连接的适配器通过 `[runtime.connection]` 声明凭据映射。第三方适配器、复现脚本和历史真实验收记录随独立题库维护。[交付位置](benchmark-distribution.md) · [模型连接](connections-and-rollouts.md#本机-owner-资源管理)

使用以下命令绑定已导入题包、已注册 Harness 和本项目连接。先填写 ID 并加载与 API 相同的主密钥。受信任目录包可显式选择 `--trusted-local`，Docker 运行条件由该版本的环境配置决定。

```powershell
python -m liteeval.interfaces.cli.main benchmark run RELEASE_ID --project PROJECT_ID --agent-harness HARNESS_REVISION_ID --target-connection CONNECTION_ID --trusted-local
```

## 实时轨迹与评分

API 和运行使用同一个 SQLite 数据库。按 [Owner 初始化](step-1-backend.md) 配置本机 API 后，启动 API、签发项目 Key 并启动控制台。将 `PROJECT_ID` 替换为运行输出中的实际值。

```powershell
python -m liteeval.interfaces.cli.main --db data/dialogue/liteeval.db api
python -m liteeval.interfaces.cli.main --db data/dialogue/liteeval.db key PROJECT_ID --label trajectory
node console/server.js --api-port 8765
```

打开 `http://127.0.0.1:7100/trajectory.html`，输入 Execution ID 与 LiteEval 项目 Key。此页从 `GET /v1/benchmark-executions/{id}/events?after=0` 分页读取已持久化事件，可用 `task_id` 筛选。浏览器按轮显示指令、会话、可见回复、工具调用、用量及状态。重新连接从事件序号零开始回放。原型其他页面继续使用各自的演示数据。[实时轨迹页面](../console/trajectory.html)

Judge 在独立环境下读取 `submission/` 和平台冻结的 `evidence/trajectory.jsonl`。重评使用原证据并要求原评分 Harness 摘要一致，ZIP 将相同轨迹保存到 `trajectories/<task-id>.jsonl`。示例 Judge 同时核对三轮开始与结束顺序、唯一会话 ID 和实际文件中的编号、币种、最终金额。
