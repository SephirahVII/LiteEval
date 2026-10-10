# 后端架构与结果口径

## 模块分工

| 模块 | 职责 |
| --- | --- |
| core | ContentPart、Event、EvalCase、AgentRun、Score 版本化契约 |
| storage / migrations | SQLite 事务、Case 修订、数据集快照、配置哈希、租约、Attempt、项目权限及迁移 |
| config / security | 提前校验配置、环境变量密钥引用、项目令牌哈希、结构化凭据脱敏 |
| api / cli | 认证后的 HTTP 入口和独立 API/Worker 进程生命周期 |
| transport / adapters / providers | 有界 HTTP、请求映射、Agent 执行、OpenAI 兼容 Judge |
| runner / harness | 并发调度、重试、心跳与写入租约校验、执行/快照/清理生命周期 |
| process / environments / codex | 有界子进程、独立工作目录、Docker 约束、Codex JSONL 标准化 |
| evaluators / reports | 显式证据要求、规则/Judge 评分、实验汇总、Case Compare |
| sdk | 同步/异步入口包装、嵌套 span、可选内容记录、默认 fail-open |

API 与 Worker 共享本机 SQLite（WAL 模式）和工件目录。多个 Worker 可处理不同实验，每个实验只允许一个有效租约持有者，实验内部并发处理 Case。当前部署面向单机；SQLite 文件不能放在网络共享盘上作为多主机队列。

## 数据与不可变性

一个 Case 可以有多个修订，修改只生成新修订。实验提交事务冻结 Case ID、修订号和内容哈希，并绑定不可变 Agent Version、Evaluator Revision 和运行配置。重新提交同一幂等键和相同请求返回原实验；相同键配不同请求返回 409。数据集后续修改不影响原实验，`rerun` 使用原快照。

Agent 配置冻结的是地址、模型和映射；外部部署代码和供应商模型仍可能变化。使用固定部署版本及固定镜像摘要，并在配置和 Trace metadata 中记录自己的代码版本。

每个实验/Case 修订只有一个稳定 Run；每次执行产生独立 Attempt。Agent 失败重试创建新 Attempt，Judge 重试复用已取得的输出。Attempt 保留执行状态、工件引用、usage 和成本，避免仅看到最后一次调用。

## 状态与恢复

实验：`queued → running → completed / failed / cancelled`。`failed` 表示调度或持久化等实验级错误，单个 Agent 失败不会直接使整个实验失败。

Run：`running → retrying → running` 或 `succeeded / failed / timed_out / cancelled`；恢复时旧的未完成 Attempt 标为 `abandoned`，保留 Run ID。已终结 Run 的结果与评分在同一个事务中提交，接管时不会重跑它。

默认租约 15 秒，每 5 秒心跳。取消请求禁止新结果提交并取消在途任务；失效 Worker 的取消请求由下一个 Worker 清理。结果提交检查租约 token、有效期、实验状态及取消标记，旧 Worker 即使稍后返回也不能覆盖新结果。普通退出会终止子进程并释放租约；强制崩溃后等待租约过期接管。Docker 容器带实验/租约/Attempt 标签，接管时清理该实验旧容器。

HTTP 线程中的请求无法保证立刻停止远端执行。租约与幂等键保障本地结果一致性，远端副作用依赖 Agent 自己的去重或补偿策略。

## 事件与证据

Event v2 包括 `schema_version`、`event_id`、`parent_id`、`sequence`、`kind`、`name`、开始/结束时间、状态、输入/输出 ContentPart 和 attributes。ContentPart 支持文本、JSON 以及媒体/文件工件引用；目前评分主要覆盖文本、JSON 和文件，尚无媒体专用评分器。

轨迹覆盖等级：`output_only`、`reported_spans`、`full_harness`。SDK 是显式 span 上报，不会自动捕获全部框架调用。HTTP `trajectory_complete=true` 是 Agent 声明完整性的契约；未完整上报时，未见工具调用应 abstain，不能断言没有调用。Codex 保留原始 JSONL，并将已知 item 转换为命令、消息、MCP 工具和文件操作；未知事件保留为 custom。完整等级仅描述 CLI 可观测的事件，并不意味着捕获内部模型思考。

评分器先判断所需证据是否可用。工具参数按预期对象顶层键子集精确匹配，返回相关 Event ID。文件与测试评分读取执行结束后的独立验证快照，验证命令来自管理员配置；Agent 的“完成了”文本不替代文件/测试证据。

## 汇总口径

| 字段 | 含义 |
| --- | --- |
| total_cases | 冻结快照 Case 数 |
| success_rate | 所有评分器通过的 Case 数 / total_cases |
| execution_success_rate | succeeded Run 数 / total_cases |
| scored_pass_rate | Case pass / (Case pass + Case fail) |
| scoring_coverage | (Case pass + Case fail) / total_cases |
| metrics[].pass_rate | 该评分器 pass / (pass + fail) |
| metrics[].mean_score | 该评分器有效 pass/fail 数值的均值 |
| latency | 成功 Run 的耗时均值及样本数，包含环境生命周期，未包含 Judge |
| cost_by_currency | 最终 Run 的已上报成本，分币种并附样本数 |
| attempt_cost_by_currency | 所有已持久化 Attempt 的已上报成本，包含重试 |
| agent_usage | 已持久化 Attempt 的 Agent token 数及样本数 |

已知任何一项 fail 即 Case fail；否则必须全部 pass 才是 Case pass；缺失、abstain、error 和执行失败为 unscored。未执行为 pending，取消项为 cancelled。分母为空返回 null。没有成本数据时不会假装为零；Judge 成本暂未统一计入 Agent 汇总，原始回复仍保存。

## Case Compare

只有两边均 completed、快照哈希相同、Evaluator Revision 集合相同才可比较。否则返回 `comparable=false` 及原因。

每个 Case 只计一次：任一指标回退为 regressed；没有回退但存在缺失/abstain/error 为 unscored；证据齐全且至少一项 fail→pass 为 improved；其余为 same。相反方向变化设置 `mixed=true` 并按 regressed 计。`metric_pair_counts` 单独统计评分器配对，`details` 提供两侧 Run ID、分值差和指标结果，用于查阅对应 Trace/工件。

连续评分的阈值决定 pass/fail；阈值未跨越时 Case 分类为 same，数值变化仍在 `value_delta` 展示。此版本不计算统计显著性、框架自动轨迹差异或自动发布门禁。
