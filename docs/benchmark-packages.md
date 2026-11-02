# 目录式 Benchmark：当前操作与能力边界

2026-11-02；软件 0.4.1，SQLite schema 6。本文描述实际实现。旧 Python 顶层导入路径已删除；没有旧路径 facade、数据格式自动降级或运行环境回退。后续以本规范及可执行验收为准。

## 1. 直接运行完整离线闭环

```powershell
python -m pip install -e .
python examples/run_native_benchmark.py
```

脚本创建独立项目，导入 `benchmarks/native_finance`，注册 `harnesses/finance_command` 并执行两题。它真的启动 Agent 与 Judge 子进程，读取 CSV，生成数值结果和报告，复制并校验二进制文件。随后仅根据已存 Submission 重评，最后导出 ZIP。

结果保存在 `data/native-benchmark/report.json`、同目录 SQLite 和 `<execution-id>.zip`。报告明确标注 `kind=deterministic_subprocess_acceptance`、`model_calls=0`；分数只能证明这组执行与评分流程通过，不代表模型质量，更不是官方 FAB 成绩。

Workspace 是受信任本地执行：隔离工作目录，**不隔离宿主权限**。必须显式使用 `trusted_local=True` 或 CLI `--trusted-local`。执行程序能访问宿主自身权限允许的资源，不应在此模式运行不可信题库、Harness 或测试。Docker 模式没有到 Workspace 的 fallback。

## 2. 当前目录格式

```text
bench/
├── dataset.toml
├── README.md
├── .shared/                         # 可选的已声明资源
└── tasks/<task-id>/
    ├── task.toml
    ├── prompt.md
    ├── environment/initial_workspace/
    └── judge/
        ├── ground_truth.json       # 可选参考答案/事实/目标状态
        ├── rubric.json             # LLM 检查需要时提供
        └── test.py                 # command 检查需要时提供
```

`ground_truth` 不等于 rubric。前者是参考事实或目标；后者定义判断标准。新目录模式分别管理两者，不使用 expected 混装评分材料。Dataset 服务仍有自身 Case 字段；目录编译器不会自动读取或降级到旧 Case 格式。

dataset.toml 示例：

```toml
schema_version = 1
[benchmark]
id = "my-benchmark"
version = "1.0.0"
[tasks]
include = ["tasks/*/task.toml"]
[defaults]
environment_profile = "local"
target_timeout_seconds = 2400
judge_timeout_seconds = 180
[environment_profiles.local]
type = "workspace"
```

Task 的完整可执行示例见 `benchmarks/native_finance/tasks/growth-001/task.toml`。字段包含 task、input、environment、requirements、budget、artifacts、judge。操作字段未知、引用失效、任务 ID 重复、路径越界、权重/预算非法均阻止导入；不会静默忽略。

路径统一为包内相对 POSIX 路径。dataset 路径相对根目录，task 路径相对任务目录；初始工作区可含二进制文件。拒绝符号链接、硬链接与特殊文件。单文件 16 MB、包 64 MB/4000 文件，单产物上限默认 1 MB、最大 16 MB。

发布保存完整内容清单、资源 SHA-256、解析后的 TaskSpec 和 digest。资源进入独立的内容寻址存储；删除原始目录不影响执行。相同项目/id/version/内容幂等，内容改变而版本不变返回冲突。当前本地导入需由可信 CLI 执行，HTTP 不接受任意宿主路径。

## 3. Harness 选择与协议

Agent Harness 负责完成任务；Evaluation Harness 负责其评分执行/适配。平台 Orchestrator 统一拥有执行阶段、资源生命周期与持久化，Harness 不能重复执行平台已经拥有的阶段。

内置选项：

| 引用 | 角色 | 行为 |
| --- | --- | --- |
| liteeval.native/v1 | evaluation | 独立命令 Judge、LLM Judge、固定聚合 |
| liteeval.model-json/v1 | agent | 通过已保存连接运行 JSON action 工具循环，最多 32 回合 |
| 注册后的 revision ID | agent | 根据协议启动自建或公开来源的命令 Harness |
| 注册后的 revision ID | evaluation | 运行外部离线评分 Harness，不再执行 native checks |

自建包：

```text
harness/
├── harness.toml
└── agent.py
```

```toml
schema_version = 1
[harness]
id = "personal.my-agent"
role = "agent"
version = "1.0.0"
protocol = "liteeval.agent/v1"
[runtime]
command = ["{python}", "{harness}/agent.py"]
```

Agent 通过 stdin 接收 JSON：schema_version、task_id、prompt、公开 input_files 和 required_artifacts。其目录只物化公开输入与 Harness 自身资源，不物化 Ground Truth 或 Judge；宿主安全限制仍取决于运行环境。stdout 必须为一个包含 `schema_version=1`、`output` 的 JSON 对象；普通日志写 stderr。产物必须实际写入工作区，平台不从最终文字伪造文件。

命令支持 `{python}`、`{harness}`；Judge 还支持 `{submission}`、`{judge}`、`{result}`。源码随修订快照，调用方不能仅修改原目录来改变已注册实现。

0.4.1 起，任务能力要求冻结在 TaskSpec.capabilities；Harness 可通过 `[capabilities] provides=[...]` 声明扩展能力。预检检查两者兼容性，不会因声明而自动安装工具或授予权限。普通模型 JSON Harness 不宣称具有 web.search/web.fetch，不能直接用于 FAB 研究题。

Evaluation 包使用 `role="evaluation"` 和 `protocol="liteeval.evaluation/v1"`。stdin 包含任务、声明 checks、Submission 清单及 ground_truth_file；Submission 位于 `submission/`，Judge 私有资源位于 `judge/`。stdout 返回 `{"schema_version":1,"checks":[...]}`；每个声明的 check 必须恰好返回一次。参见 `harnesses/finance_evaluator`。

公共 Harness 的接入方法是：检出明确上游 commit，加入/实现上述协议包装层，再注册该目录。当前不会自动克隆任意 Git URL，也不自动执行上游安装脚本。未适配的一体化官方 runner、需要自管 Docker 的框架、远程任务协议必须另建集成器，不能因为注册了目录就宣称已兼容。命令式 Evaluation Harness 当前仅接受离线检查；其 LLM 凭据/工具代理尚未实现。

Harness 修订固定资源 digest；builtin 也记录平台代码树 digest，不仅是版本标签。模型身份分别记录请求模型与供应商返回模型；未返回的信息保持未知。

## 4. 手动 CLI 流程

通过现有项目管理入口创建 project 后，使用其 ID：

```powershell
liteeval --db data/liteeval.db benchmark validate benchmarks/native_finance
liteeval --db data/liteeval.db benchmark import benchmarks/native_finance --project PROJECT_ID
liteeval --db data/liteeval.db harness harnesses/finance_command --project PROJECT_ID
liteeval --db data/liteeval.db benchmark run RELEASE_ID --project PROJECT_ID --agent-harness HARNESS_REVISION_ID --trusted-local
liteeval --db data/liteeval.db benchmark rejudge EXECUTION_ID --project PROJECT_ID
liteeval --db data/liteeval.db benchmark export EXECUTION_ID --project PROJECT_ID --output result.zip
```

切换自建 Evaluation Harness：先注册 `harnesses/finance_evaluator`，再向 run 增加 `--evaluation-harness EVALUATION_REVISION_ID`。两种 Evaluation Harness 都有真实子进程测试，额外验收会使 native test.py 故意崩溃，确认选用外部实现时不会重复执行 native 检查。

模型模式使用 `--agent-harness liteeval.model-json/v1 --target-connection CONNECTION_ID`。需要 LLM Judge 时再绑定 `--judge-connection CONNECTION_ID`。连接必须属于本项目且为 openai_compatible；Key 沿用加密保存和可信调用层解析，不复制进 Agent 文件。Qianwen 研究模式使用 `liteeval.qianwen-research/v1` 和千问平台连接，已完成两题真实试跑，见 [证据及复现方式](fab-qianwen-trial.md)。`benchmark rejudge` 支持显式 `--judge-connection` 切换 Judge，不重复运行 Target。

目录执行当前使用 CLI/应用服务入口；不是现有 Dataset Worker 队列的回退路径。读取接口已有 `GET /v1/benchmark-releases` 和 `GET /v1/benchmark-executions/{id}`，遵循项目鉴权。Web 提交、后台队列、远程取消/恢复及多机调度仍需后续实现。

## 5. 评分、产物和日志

Target 结束后先采集并保存实际产物 bytes/hash，再销毁 Agent 环境，然后在独立运行环境中物化 Submission 与 Judge。Docker 时为不同容器；本地时为不同临时目录与进程。评分完成清理工作目录，日志、Submission 和结果已持久化。

command Judge 根据 argv 写入 `{result}` 指定的 JSON 文件，包含 schema_version、check_id、outcome=pass/fail、score=[0,1]、reason。退出 0 只表示评分程序完成，不自动表示通过。LLM Judge 接收 prompt、指定产物、rubric 与可选 Ground Truth，按同一结果 schema 校验。`llm` 是单 check 数值协议；`llm_rubric` 则批量返回全部 criterion 的布尔判定，由程序校验并计算权重及 must-pass gate。多模型 jury 尚未实现，FAB 使用方式见 [专用指南](fab-v2-native.md)。

按正权重加权平均，must_pass 失败最终分数为 0，同时保留 gate 前分数；达到 threshold 才通过。Judge 崩溃或证据错误记 unscored，不把其余 check 重新归一化。缺少 required 文件记任务失败。执行状态、采集状态和评分结论分别保存；completed 不等于全部任务通过。

事件包含本地 stdout/stderr、模型调用、工具输入输出、Submission 清单、评分与完成状态。研究模式还保存实时供应商状态及原始 SSE。用量只汇总供应商返回的计数，缓存/reasoning 不重复叠加；未知为 null。ZIP 包含执行计划、事件 JSONL、CSV、真正的产物文件和 raw-responses，读取时校验 SHA。原始 Judge 请求可能含 rubric/Ground Truth；导出用于有权查看评分材料的项目所有者。导出不包含连接表、API Key 或加密主密钥。

## 6. Docker 和待验收范围

Docker profile 使用 `type="docker"` 与 `image="仓库@sha256:64位digest"`，可指定 network=none/bridge、cpus、memory。当前不直接构建 Dockerfile，须预先准备镜像并写入实际 digest；缺少 Docker CLI 会阻止执行，不降级为本地运行。

本机缺少 Docker CLI/WSL；[Linux CI 实际容器作业](https://github.com/SephirahVII/LiteEval/actions/runs/41340058156) 已通过：准备 Python 镜像、解析实际 RepoDigest，再运行容器与独立 Judge。千问研究工具由供应商云端执行，不代表运行在这个 Docker 中。

当前还未交付自动 Git 同步、通用服务图、skills 注入、任意公开框架直接兼容、远程长任务 Job 协议、User Agent、官方 FAB 六工具/三 Judge/重复试验方法复刻，以及全公开集质量验收。双 Judge 诊断不等于官方 jury。尚不支持的声明明确拒绝；不会由旧实现兜底。
