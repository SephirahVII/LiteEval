# 后端架构与运行口径

更新：2026-11-02，软件 0.4.0 / SQLite schema 6。

本轮直接重组后端，不保留旧 Python 模块导入路径或兼容 facade。先前提案中的 fallback、旧路径转发及以旧版本兼容为验收目标的安排取消。保留有用的项目、凭据、Trace、Dataset 服务实现，并迁入新模块；它们不是目录 Benchmark 的自动回退路径。

## 当前结构

```text
liteeval/
├── domain/                  # 契约、TaskSpec、评分规则、usage、路径、值对象
├── ports/                   # BenchmarkRepository/ResourceStore/Harness 协议
├── application/             # 发布、预检、执行、重评、导出、已有业务用例
├── integrations/
│   ├── agents/              # HTTP/Codex 接入
│   ├── models/              # 模型供应商接入
│   ├── harnesses/           # Agent/Evaluation Harness 与 registry
│   ├── judges/              # 已有规则/Judge 集成
│   └── benchmarks/          # FAB 格式转换
├── infrastructure/
│   ├── persistence/         # SQLite schema 6、Repository、查询
│   ├── artifacts/           # 内容寻址 blob
│   ├── execution/           # 进程、工作区、Docker、Worker
│   ├── sources/             # Benchmark/Harness 源码快照与解析
│   ├── network/             # HTTP
│   └── security/            # 凭据与脱敏
├── interfaces/              # HTTP、CLI、SDK
└── bootstrap.py             # 为目录用例注入具体依赖
```

Domain/Ports 不导入 I/O、Application 或集成框架。新目录 Benchmark 用例通过注入的 Repository、Loader、BlobStore、RuntimeFactory、Registry 和连接解析器使用部署能力。现有 Dataset/Trace 服务中的 SQL 与业务规则尚有集中实现，后续按新业务需要拆分，不设置保持旧模块路径的约束。

所有内部导入已改到新路径；JSON/hash/ID、错误、路径选择、Schema 和 usage 标准化已从存储/adapter 中提取。静态依赖扫描包含函数内导入；当前发现的模块依赖环为 0。架构测试限制 Domain/Ports 的依赖及顶层模块数量，防止重新添加兼容转发层。

## 目录 Benchmark 的执行链路

```text
DirectoryLoader → TaskSpec + 资源清单 → Benchmark Release
                                ↓
                 固定 Agent/Evaluation Harness 修订
                                ↓
               RunPlan（资源/代码 digest、连接、预算）
                                ↓
       Target Runtime → 真实 Submission → 独立 Evaluation Runtime
                                ↓
                  检查、聚合、持久化、导出/重评
```

Agent Harness 可以是注册的命令实现，或内置 JSON action 模型循环。Evaluation Harness 可以是内置 command/LLM Judge，或协议化的外部离线评分 runner。平台统一拥有工作区、阶段及持久化；外部评分 runner 不再触发 native 检查，不重复计分。角色、配置和资源 digest 固定到运行计划。

目录模式明确分开 ground_truth、rubric、test 和 submission；不会把评分资源传给 Target。初始化支持二进制文件，采集保存实际文件 bytes；工作区删除后仍可重评和导出。Workspace 是可信本地模式，Docker 是独立容器模式，二者不自动降级。

实际格式、命令、验收方式及未实现项见 [目录 Benchmark 指南](benchmark-packages.md)。离线两题验收是确定性进程执行，不是 LLM 或官方 FAB 质量成绩。本机 Docker 未实跑，独立 Linux CI 用于进一步验收。

## Dataset/Trace 服务的独立口径

下列内容描述现有 Dataset/Trace 服务；不要将其旧 Case 字段、快照比较规则或状态覆盖标签当成目录 Benchmark 已完整验收的依据。

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
