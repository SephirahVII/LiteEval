# 后端架构与结果口径

0.2.2 新增 catalog（评分器修订/数据集命名版本）、imports（预览与正式导入共享校验）、planning（无外部调用的预检）和 runtime（Worker 上报）。数据库 schema 4；事件契约仍为 v2。预检与创建在同一事务核对快照摘要，发布版本复用不可变快照，旧幂等请求重试仍优先返回原实验。Worker 就绪信息不参与执行摘要，且仅为提示，不构成定向调度保证。接口约定见 [前端对接文档](frontend-backend-contract.md)。

0.2.1 在原评测核心之外增加 `sessions`（Owner 会话、Origin/CSRF 与撤销）、`web_queries`（轻量进度与分页结果）和 `previews`（独立持久评分预览）。数据库 schema 为 3，Event/ContentPart 契约仍为 v2。会话与预览不改变正式实验的 Score、快照和 Compare 语义。

Dataset/Case/Evaluator 归档不删除历史引用；新实验排除归档项，rerun 保留原快照。Case 修订通过 BEGIN IMMEDIATE 与 base_revision 校验避免并发覆盖；旧客户端省略该字段仍兼容。预览 Worker 采用独立租约、心跳与写入校验，恢复可能再次调用 Judge，原始回复以独立工件保存。具体边界和操作见 [第一步指南](step-1-backend.md)。

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

已知任何一项 fail 即 Case fail；否则必须全部 pass 才是 Case pass；缺失、abstain、error 和执行失败为 unscored。未执行为 pending，取消项为 cancelled。分母为空返回 null。没有成本数据时不会假装为零。0.3.0 新增独立 usage_records 与 rollout_usage，统一记录 Target/Judge 每次尝试的上报用量和费用；旧 Agent 汇总字段保持原行为。

## 连接与 Rollout（0.3.0）

model_connections 将不可变 endpoint/model 与可替换的加密凭据关联到 Project，使用部署环境提供的 Fernet 主密钥。Agent/Evaluator 配置固定连接的非敏感参数并保留 connection_id，Worker 调用时解密 Key。Worker 按连接检查能否解密，仅上报布尔值，不探测供应商服务。

rollout_events 按 attempt_id 记录活动和本地子进程完整日志行，通过 id 游标轮询读取。写入校验实验租约与当前 Attempt，拒绝过期 Worker。HTTP 内部轨迹仍依赖端点主动上报。usage_records 独立保留每次 Target/Judge 尝试的原始/标准用量、耗时与上报费用；别名不重复相加，缓存与 reasoning 不叠加到总量，未知为 null。

导出以数据库读取事务建立快照，包含固定 Case、配置、每次尝试的 Trace、Score、活动、用量与工件清单。ZIP 读取不可变工件时复核 SHA-256。连接表及凭据不进入导出；运行中导出标记部分覆盖。完整统计语义见 [操作指南](connections-and-rollouts.md)。

## Case Compare

只有两边均 completed、快照哈希相同、Evaluator Revision 集合相同才可比较。否则返回 `comparable=false` 及原因。

每个 Case 只计一次：任一指标回退为 regressed；没有回退但存在缺失/abstain/error 为 unscored；证据齐全且至少一项 fail→pass 为 improved；其余为 same。相反方向变化设置 `mixed=true` 并按 regressed 计。`metric_pair_counts` 单独统计评分器配对，`details` 提供两侧 Run ID、分值差和指标结果，用于查阅对应 Trace/工件。

连续评分的阈值决定 pass/fail；阈值未跨越时 Case 分类为 same，数值变化仍在 `value_delta` 展示。此版本不计算统计显著性、框架自动轨迹差异或自动发布门禁。
