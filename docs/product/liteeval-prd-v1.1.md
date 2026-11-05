# LiteEval 产品需求文档

文档版本 1.1 ｜ 2026-11-05 ｜ 后端核对基线 `main@90478fa`（软件 0.4.2）

LiteEval 面向个人开发者和小型 Agent 开发团队。用户可以导入一套题目，用不同的模型或 Agent Harness 执行，在同一份题目与评分口径下查看产物、证据、得分和用量。本版以目录式 Benchmark 为控制台的主流程。Dataset、Experiment 和 Trace API 继续用于各自的业务数据，接入界面时按资源来源区分。

## 1 用户入口

主流程为 **Benchmarks → New Evaluation → Evaluation Runs → Rollout → Results & Export**。从 Benchmark 详情进入新建流程时自动填入版本，从已有运行进入时预填原配置并要求再次确认。项目切换器位于导航顶部，所有列表、详情与创建操作均以当前项目为作用域。

| 中文入口 | English | 页面内容与操作 |
| --- | --- | --- |
| 概览 | Overview | 当前项目、最近运行、进行中的任务和需要处理的问题。可导入评测集、新建评测、打开最近一次运行。 |
| 评测集 | Benchmarks | 按评测集查看发布版本，再进入题目列表与详情。支持导入评测包、查看来源和校验结果，从指定版本发起评测。 |
| 评测运行 | Evaluation Runs | 查看运行列表、创建向导、运行详情和单题执行。运行详情提供结果、证据、用量与导出，已完成记录提供重跑和重评。运行列表提供对比入口。 |
| 运行配置 | Run Configuration | 子页为 Harnesses 和 Model Connections。按角色维护可用 Harness 修订，按项目保存 API 地址、模型 ID 和供应商凭据。向导也可打开对应配置页。 |

**项目设置 / Project Settings** 放在项目菜单，包含项目 API Key、Owner 会话和执行环境状态。**Trace 导入 / Import from Trace** 放在「评测集 → 导入来源」，用户可由 Trace 派生题目并保留来源。目录包转换接通前，既有 Trace 转 Case 入口标为 Dataset 来源。**环境与能力 / Environment & Capabilities** 放在题目、Harness 和预检详情中，展示本次实际会用到的配置。评分规则随评测集版本阅读，评分 Harness 在运行配置中管理。

页面区分空数据、加载失败、权限不足和能力暂未开放。演示数据有持久可见的「演示 / Demo」标识，真实模式仅以服务端结果更新记录。依赖 CLI 的动作提供命令和所需 ID，并标为「通过 CLI 执行 / Available via CLI」。页面刷新后通过项目和运行 ID 恢复详情。时间按用户时区展示，耗时带单位。

## 2 关键流程与页面需求

### BM 评测集与版本

- **BM-01 评测集、发布版本、题目。** 版本列表展示 ID、版本号、发布日期与摘要；详情补充题目数、来源、环境和评分概览。题目详情分为「题目与初始文件」「环境与能力要求」「预期交付」「评分规则」。题目使用发布版本和题目 ID 联合定位。参考答案存在时才显示，并限制为有权查看评分资源的用户。
- **BM-02 导入与发布。** 导入本地目录包或上传归档。GitHub 来源按仓库、路径及已解析的 commit SHA 固定；同版本同内容可复用，同版本内容不同给出冲突并引导更新版本号。预览展示待发布题目、校验错误及文件路径。发布后保存内容摘要和资源快照。来源信息依据实际记录填写，缺少来源时可标记为「本地包」或「来源未记录」。
- **BM-03 包约定。** `dataset.toml` 声明评测集、任务与环境配置。每题由 `task.toml` 声明输入、初始工作区、交付文件、超时和检查项。`judge/` 可包含测试、Rubric 和可选 `ground_truth.json`。校验同时核对题面依赖的实际输入文件。含 Docker 的评测固定镜像 digest。详细结构按目录包规范实现。[评测包规范](../benchmark-packages.md)

### RC Harness 和模型连接

- **RC-01 Harness 目录。** 按 Agent Harness、Evaluation Harness 分组，显示内置、公开或自建来源、版本与内容摘要、协议、声明能力、运行环境要求以及必需凭据的配置方式。公开实现先固定源码修订并适配协议，自建实现注册为独立修订。可选状态分别为「已注册」「声明兼容」「环境检查通过」「已验证运行」，验证记录带时间与条件。
- **RC-02 模型连接。** 创建时填连接名、API 地址、模型 ID、连接类型和供应商 API Key。已保存 Key 只显示是否配置和凭据版本。可更换、清除 Key 或归档连接。更换地址或模型 ID 时创建新连接，历史运行保留原连接绑定。被测与评审角色在新建评测时选择。
- **RC-03 命令 Harness 凭据。** 内置模型 Harness 与 LLM Judge 可以选择受支持的已保存连接。Claude Code、DeepSeek 等命令 Harness 采用执行进程的声明式环境变量。页面展示必需变量名、可选变量名及就绪状态；变量值留在配置该进程的环境中。当前缺少实测状态时显示「待验证」，主动联通性测试需说明会调用供应商。

### EV 新建评测

1. **选择评测内容 / Select Tasks**。选择发布版本及全量或指定题目，展示最终题数。尚无题目子集运行能力时，当前 CLI 引导使用单独准备的版本。
2. **配置被测对象 / Configure Target**。选择 Agent Harness 修订。需要模型连接时展示连接选择，需要命令环境变量时展示缺项与配置说明。运行环境来源于题目或版本配置，展示本地工作目录或 Docker、镜像摘要、网络策略和资源限制。
3. **确认评分方式 / Configure Evaluation**。展示当前版本规定的检查项、必过项和聚合规则。选择兼容的评分 Harness，涉及 LLM Judge 时选择评审模型连接。
4. **预检与提交 / Review & Start**。逐项给出兼容性、环境、凭据、超时和必需产物检查。检查结果区分已通过、阻塞、待运行验证。提交确认冻结版本、题目、两个 Harness 修订、运行环境、连接与参数。重复请求同一已确认的计划须返回同一运行记录；配置变化时重新预检。

Web 提交先持久化运行，再交给执行进程处理。排队、取消、异常中断、恢复和重试显示真实状态。超过心跳时限或断连时保留最后观测时间。Docker 不可用时提示修复环境。本地工作目录模式须明确授权受信任代码运行。

### ER 查看运行、重评与比较

- **ER-01 运行列表与进度。** 按评测集版本、运行状态和创建时间筛选，提供最近记录与运行 ID 查找。详情固定展示版本、Harness 修订、连接、计划题数、结束题数、评分覆盖与阶段耗时。运行中的日志使用增量读取，展开单题时按需加载。
- **ER-02 单题执行。** 题目行并列展示执行、产物和评分状态。详情标签为「概览 / Summary」「日志 / Logs」「轨迹 / Trajectory」「产物 / Artifacts」「评分 / Grading」「用量 / Usage」。日志显示来源、时间和输出限制。轨迹展示已记录的模型调用、工具事件及结果，并标注采集覆盖。产物列出预期和实际文件、缺失文件、大小及哈希。评分展示检查结果、Rubric criterion、理由及引用证据。用量区分 Target 和 Judge。
- **ER-03 导出。** 整次运行可下载 ZIP，包含执行计划、结果、事件、汇总表和实际提交文件；已采集的原始模型响应按证据权限提供。导出绑定项目与运行 ID。单文件下载核对项目、运行和产物归属。
- **ER-04 重跑与重评。** 「重新运行 / Rerun」复制原计划为新运行，可以在确认时更换被测配置。「重新评分 / Rejudge」复用已收集的提交，产生关联原运行的新记录，可按支持范围更换 Judge 连接。原运行及原始得分可回看。无法重新评分的题目显示原因。重评用量按此次评审计算，历史 Target 用量继续在原运行中查阅。
- **ER-05 运行对比。** 从运行列表选择两个已完成记录，先核对评测集摘要、题目集合、评分规则、评分 Harness、Judge 配置和环境。将选定的模型或 Agent Harness 变化显式标成对比变量。条件满足时展示通过率、覆盖率、耗时、用量及逐题变化。条件不一致时展示配置差异和对齐入口，质量变化结论保持不可用。

## 3 中英文界面与指标口径

中文和英文可独立切换。稳定术语如 Benchmark、Harness、Rollout、Trace 可在中文界面保留，例如「评分 Harness」和「单题执行（Rollout）」。同一页面内标签保持一致。模型名、技术 ID、版本、路径和 digest 原样保留。语言切换不影响 URL 中的资源身份、筛选或评测配置。

| 中文 | English | 界面含义 |
| --- | --- | --- |
| 评测集 / 评测集版本 / 题目 | Benchmark / Benchmark Release / Task | 一个评测集可有多个发布版本，每个版本包含固定题目与资源。区分评测集版本、Harness 修订和模型 ID。 |
| 评测运行 / 单题执行 | Evaluation Run / Rollout | 一次运行覆盖所选题目。单题执行关联该运行中的题目与结果。Attempt 用于已有实际重试记录时。 |
| 被测对象 / Agent Harness | Target / Agent Harness | 被测对象是运行选定的 Agent 配置。Agent Harness 提供任务执行、工具调用和提交接口。 |
| 评分 Harness / 评分规则 / 参考答案 | Evaluation Harness / Rubric / Ground Truth | Harness 负责运行检查，Rubric 描述评分项，Ground Truth 是可选参考结果或参考数据。离线测试同样可独立评分。 |
| 预期交付 / 实际提交 / 运行产物 | Expected Deliverables / Submission / Artifacts | 预期交付列明要求。实际提交是该次 Agent 交付的文件。运行产物还包括已采集的执行证据。 |
| 供应商 API Key / 项目 API Key | Provider API Key / Project API Key | 前者用于访问供应商，后者用于访问 LiteEval API。凭据版本和项目权限分别管理。 |

| 指标或状态 | 展示规则 |
| --- | --- |
| 运行已结束 / Run Completed | 表示运行处理完毕。单题可以是「执行成功 · 缺少必需产物 · 评分未通过」或「执行失败 · 已收集部分产物 · 未评分」。 |
| 未评分 / Unscored | 用于执行失败、评分错误等未产生有效评分的题目，保留原因。缺必需产物若产生有效失败判定，按未通过计入。 |
| 已评分通过率 / Pass Rate Among Scored | 通过题数 ÷ 有效评分题数。另可展示全量通过率（通过题数 ÷ 计划题数），两者标明分母。分母为零时显示暂无有效评分。 |
| 评分覆盖率 / Scoring Coverage | 有效评分题数 ÷ 计划题数。示例「已结束 20/20，已评分 18/20，已评分题通过 14/18」。FAB 的 Partial Credit 与 All-Pass 各自展示，附判定范围。 |
| 耗时 / Duration | 显示实际采集的阶段和总耗时，并区分超时上限。速度比较使用同口径和同样本范围。 |
| Token 用量 / Token Usage | 按模型及 Target、Judge 分组。展示供应商报告的输入、输出、总量、缓存读写和推理计数。子项不重复相加，缺失显示「未报告」。推理 Token 是计数，轨迹只展示已采集证据。费用仅在有账单或明确的估算依据时显示，并标出来源和覆盖率。 |

## 4 当前后端与入口对照

下表核对目录式 Benchmark（Native）。**HTTP 可接**表示已有接口，页面仍需真实联调。**部分可接**表示现有数据只能支持部分页面。**CLI / 服务**表示可从对应运行入口使用。**待实现**表示目标页面依赖的新能力。控制台当前使用 Mock 数据，登录、端点检查、运行与导出尚未接后端。[控制台原型说明](../../console/README.md)

| 用户入口 | 当前后端支持 | Web 接通所需工作 |
| --- | --- | --- |
| 概览、最近运行 | **待实现**。Native 没有运行列表和项目汇总接口。 | 增加项目内运行列表和进度摘要，再据此展示最近运行与待处理事项。 |
| 评测集版本列表 | **HTTP 可接**。`GET /v1/benchmark-releases` 返回项目内 ID、名称、版本、digest 和创建时间。 | 先可按名称聚合展示版本。题数、来源详情和文件需另取详情数据。 |
| 版本详情与题目 | **CLI / 服务**。仓储可按 ID 读取发布 manifest，保存题目、资源与摘要。 | 增加版本、题目及公共资源查询，控制评分私有资源的访问。 |
| 包校验、发布与 GitHub 来源 | **CLI / 服务**。`benchmark validate/import` 支持本地包。可手动 checkout 固定 commit 再导入。FAB 有专用转换命令。 | 增加上传与校验预览、固定 Git 来源导入及来源元数据。通用 GitHub 拉取、版本差异和自动同步待实现。 |
| Agent / Evaluation Harness | **CLI / 服务**。内置 Harness 按 ID 解析，自建或已适配的公开目录用 `harness` 注册并快照。 | 增加 Harness 列表、详情、兼容性与注册接口。页面需要区分声明和实测结果。 |
| 模型连接与供应商 Key | **HTTP 可接**。`/v1/model-connections` 支持创建、列表、详情、更新名称与密钥、归档和恢复。密钥加密保存，依赖 `LITEEVAL_SECRET_KEY`。 | 对接真实表单。端点健康探测和项目共享密钥库尚无对应 API，当前展示配置状态。 |
| Claude Code / DeepSeek 凭据与环境 | **CLI / 服务**。Agent Harness 的必需和可选变量经执行环境透传；缺少必需变量时预检拒绝。Docker 预检检查 CLI，daemon、镜像、CLI 和供应商可用性需要执行验证。 | 增加 Native 环境与变量就绪报告。已保存 Model Connection 的 Key 不会注入命令 Harness。 |
| 新建、预检与运行 | **CLI / 服务**。`BenchmarkService.preflight()` 在 `benchmark run` 前检查资源、能力、凭据与运行要求；前台逐题跑整个 Release。 | 增加 Web 预检及提交、选题、提交幂等、独立任务队列和运行状态 API。Native 当前没有网页取消、租约接管和独立任务重试。 |
| 运行详情、日志与轨迹 | **部分可接**。`GET /v1/benchmark-executions/{id}` 需要已知 ID，返回 plan、result 与全部已持久化事件。单题以事件的 `task_id` 归集。 | 可先做按 ID 查询。增量事件、分题查询、阶段进度和大证据按需加载需要新 API。轨迹深度取决于 Harness 上报，命令封装当前主要保留日志、输出和文件。 |
| 产物与评分 | **部分可接**。详情可读已收集文件的元数据、缺失清单、评分检查与理由；文件字节由 BlobStore 保存。 | 评分与产物清单可先展示，Native 文件下载与证据预览需要授权接口。 |
| Token、缓存、推理与效率 | **部分可接**。执行结果或事件有已采集的模型用量、耗时和阶段记录。命令 Harness 未统一采集供应商用量；重评结果结构与普通运行也有差异。 | 按角色展示已有数据，归一普通运行与重评的摘要，缺失显示未知。账单、全量资源监控与完整轨迹覆盖待独立建设。 |
| 导出与重新评分 | **CLI / 服务**。`benchmark export` 导出 ZIP；`benchmark rejudge` 对已完成运行复用 Submission，可切换 Judge 连接，并保持评分 Harness 修订。 | 增加 Web 下载与重评触发接口，保留原运行链接并显示被跳过的题目。 |
| 重新运行与对比 | **部分可用**。可用 CLI 对原 Release 另发一次运行。Native 未提供从历史计划发起重跑或 Compare 的接口。 | 实现计划复制与对比资格检查，基于真实两侧证据出结果。 |
| 项目、登录及项目 Key | **HTTP 可接**。已有 Project、Owner 会话、项目 Key 签发及撤销；运行状态 API 可查询现有 Worker。 | 接通真实认证。项目 Key 列表和 Native 执行环境的状态展示需要单独补充。 |
| Trace 导入、Dataset 对比 | **既有 Dataset HTTP 可接**。Trace 转 Case、Dataset 版本、Experiment 预检、队列、重试、取消和 Compare 归属既有服务。 | 目录包接入需增加 Trace 到包题目的转换与验证。两条执行路径各自使用实际资源 ID 与评分口径。 |

源码依据见 [HTTP 路由](../../liteeval/interfaces/http/api.py)、[CLI 入口](../../liteeval/interfaces/cli/main.py)、[Native 执行服务](../../liteeval/application/benchmarks.py)、[版本与运行仓储](../../liteeval/infrastructure/persistence/benchmarks.py)、[凭据管理](../../liteeval/application/connections.py)和 [ZIP 导出](../../liteeval/application/benchmark_exports.py)。

### 0.4.2 示例复核

- **离线与研究链路。** 确定性示例已有执行、产物、离线评分及导出验收，Linux CI 有 Native Docker 记录。FAB v2 已有 Qwen 和 GLM 在两道公开题上的研究试跑与双 Judge 诊断，覆盖范围按原报告标注。[0.4.0 验证](../verification-0.4.0.md) · [FAB 试跑](../fab-qianwen-trial.md)
- **新编码沙箱示例。** 当前 checkout 运行 `python -m liteeval.interfaces.cli.main benchmark validate benchmarks/agent_sandbox` 返回 `Empty or missing initial_workspace`。`parse-001` 声明初始工作区，题面要求 `logs/app.log`，当前 Git 树缺该输入文件。`git check-ignore` 确认目标路径匹配全局 `*.log` 规则。临时副本移除声明可通过三题结构校验，正式修复应将真实日志输入纳入版本并核对隐藏测试。镜像指向 `localhost:5000` 的 digest，目标执行机需要可获取该镜像及对应供应商 Key。此次复核到包校验阶段，历史 macOS 验收以其原始环境记录为准。[题目配置](../../benchmarks/agent_sandbox/tasks/parse-001/task.toml) · [题面](../../benchmarks/agent_sandbox/tasks/parse-001/prompt.md) · [示例要求](../../benchmarks/agent_sandbox/README.md)
- **本轮回归。** Windows Python 3.12.4 下运行 `python -m unittest discover -s tests -v`，125 项中 123 项通过，2 项因本地 Docker 与符号链接条件跳过。Harness 环境变量相关的 5 项通过，覆盖声明、缺项预检及本地工作目录子进程透传。本轮未发起供应商请求或 Docker 执行。
- **接口复核。** 用临时数据库实际运行两道确定性题，均通过。经后端 HTTP 路由分发验证，版本列表与已知运行 ID 的查询返回 `200`，运行详情含两题结果与 14 条事件；版本详情、运行列表、创建运行返回 `404`，其他项目无法读取该运行。

## 5 交付顺序与验收

| 阶段 | 交付范围 | 验收条件 |
| --- | --- | --- |
| M1 真实数据可读 | 四个一级入口、项目切换、中英术语、版本列表、模型连接、已知运行 ID 详情和 CLI 引导。 | 刷新后真实记录仍可访问，真实与演示来源明确，跨项目访问被拒绝，密钥不回显。Native 结果显示阶段、覆盖率和实际用量。 |
| M2 浏览器评测闭环 | 版本及题目详情、目录包和固定 GitHub commit 导入、Harness 查询与注册、选题、预检、提交与异步运行、进度、下载和重评。 | 从新项目通过浏览器完成导入、发布、真实执行、查看提交与评分、下载 ZIP。验证重复提交、缺凭据、缺镜像、进程异常和重评复用。公开与自建 Harness 各有可复现接入样例。 |
| M3 回归分析与运营 | 计划重跑、严格比较、Trace 转目录题目、备份恢复。团队权限、人工复核和质量 Gate 按业务验收推进。 | 同题同评分配置下更换模型或 Harness 可逐题比较；评分标准或题目集合改变时有差异说明。数据库与产物从备份恢复后可校验。 |

首轮主路径验收使用离线确定性包，独立验证文件生成、收集和评分。真实模型、研究工具与 Docker 通过各自的受控运行验收。编码沙箱示例通过干净 checkout 校验、镜像可获取及实际三题执行后，再作为可运行示例展示。正式发布范围以已验收能力为准，User Agent、通用 MCP 管理、定时 Git 同步和共享凭据库留作后续需求。
