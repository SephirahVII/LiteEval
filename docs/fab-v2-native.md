# FAB v2 公开集的原生目录接入

2026-11-02，LiteEval 0.4.1。已交付题库转换、固定发布、研究能力预检、逐 criterion Judge 协议与确定性评分。**现已用用户配置的千问平台 API 连接，完成公开第 3、4 题的 Qwen/GLM 四次真实研究及双 Judge 诊断**；详见 [真实试跑报告](fab-qianwen-trial.md)。不是官方 FAB 排行榜成绩。

## 1. 范围与来源

原始来源保留在 `benchmarks/finance_agent_v2`，原生包为 `benchmarks/fab_v2_public`。

- 公开题 27 道、9 类、239 条检查。
- 仓库：[vals-ai/finance-agent-v2](https://github.com/vals-ai/finance-agent-v2)。固定 commit `03c7c432b645f42f81ef3c13957100b182217d7c`。
- 原文件 `data/public.csv`，SHA-256 `27b48c08a6099bc076b4194cac7cefe295082b9aedcbc67f4fedfa70468b427e`。
- MIT LICENSE 完整保留。每条 criterion 的 source 保存完整上游 rubric 条目，不丢弃 operator 或 modifiers。
- 450 道 Private Validation 和 450 道 Test 没有导入，也未请求其访问权限。

官方 v2 方法包括共享研究 Harness、六种工具、每题两小时上限、三模型 jury、三次重复运行及测试集。该方法与公开题的单模型诊断不同，依据见 [官方方法](https://www.vals.ai/benchmarks/fabv2)。公开仓库 [固定修订 README](https://github.com/vals-ai/finance-agent-v2/blob/03c7c432b645f42f81ef3c13957100b182217d7c/README.md) 还要求模型/工具凭据，并说明 Vals 平台访问受限；不能简单复制官方命令就假定所有依赖已经具备。

## 2. 新结构

```text
benchmarks/fab_v2_public/
├── dataset.toml
├── README.md
├── LICENSE
├── source.json
└── tasks/fabv2-public-001/ ... fabv2-public-027/
    ├── task.toml
    ├── prompt.md
    ├── environment/initial_workspace/question.txt
    └── judge/rubric.json
```

没有制造 ground_truth.json。公开文件给出的是 rubric 检查材料，适配器把它们放在 Judge 私有域；初始工作区只包含原文问题，Target 不接收 rubric 或上游答案引用。

映射关系：Question → question.txt 与 prompt；Question Type → category；Expert time → metadata.expert_time_minutes；Rubric → criteria 列表。每条 criterion 的 description、weight、must_pass 分别对应 criteria、modifiers.severity、category=must_pass。原文不替换公司、财年或日期，问题的 SHA-256 与来源修订随任务保存。

每题必须真实写入 `response.md`。sources.json、calculations.json 是可选产物。平台不从最终消息补造 response.md。问题中的日期若不明确，指令要求说明使用的时间基准，不能自动换成当前财年。

Target 上限为 7200 秒，不是要求必须运行两小时。Expert time 是人工估时元数据，不自动作为超时。Judge 当前每题最多 180 秒、一个请求批量判断全部 criterion。

## 3. 转换与发布：不调用模型

```powershell
python -m liteeval.interfaces.cli.main benchmark convert-fab benchmarks/finance_agent_v2 benchmarks/fab_v2_public
python -m liteeval.interfaces.cli.main benchmark validate benchmarks/fab_v2_public
python examples/prepare_fab_package.py
```

转换验证原 CSV 的 hash、题量、operator 和 modifiers。目标内容相同则幂等；不同时拒绝覆盖，使用新目录/版本。转换与静态校验不需要项目、数据库或模型凭据。prepare 示例才创建独立数据库、项目和原生 Release，并将报告写入 `data/fab-v2-native/catalog.json`。

报告包含 release_id、digest、27/239 计数和预检阻塞原因，明确 `mode=prepared_only`、`model_calls=0`、`graded_case_count=0`，不写任何模型准确率。

准备两题子集：

```powershell
python -m liteeval.interfaces.cli.main benchmark convert-fab benchmarks/finance_agent_v2 data/fab-v2-native/rows-3-4 --rows 3,4
```

指定 Docker 时增加 `--docker-image 仓库@sha256:真实digest`。转换后的 profile 使用 network=bridge、2 CPU、2g 内存；须预先构建/准备实际包含研究 Agent 及工具依赖的镜像。仅有 Python 的镜像不意味着具备金融研究能力。不同环境生成不同发布版本，不在运行时自动降级。

## 4. 研究 Harness 就绪条件

包声明 workspace.read、workspace.write、process.execute、web.search、web.fetch。普通 `liteeval.model-json/v1` 只有文件与命令能力，预检会拒绝，不会因为能够输出文本就认为能跑 FAB。

符合协议的研究 Harness 可在 harness.toml 声明：

```toml
[capabilities]
provides = ["web.search", "web.fetch"]
```

这是声明兼容性，不会自动安装工具或获得联网权限。还需要实测检索/下载、计算、日志和写文件，并准备相应的模型、搜索、SEC/行情等凭据。当前命令 Harness 不自动获得平台保存的 Target API Key；接入使用这些 Key 的公开研究框架，还须实现受控模型/工具调用通道或明确的独立认证集成。不能把 Key 写进包，也不能擅自复用本机 CLI 登录态。

当前已有 `liteeval.qianwen-research/v1`：使用用户显式配置的千问平台 endpoint/model/Key，调用 Responses 的 web_search、web_extractor、code_interpreter，实时保存供应商事件，并执行 submit_answer 写入本地产物。它不使用本机模型 CLI 登录；代码计算发生在供应商云端，本地工作区只用于初始材料与提交采集。普通命令 Harness 的独立认证限制仍然成立。原始官方 runner 尚未原样接入。使用已适配的自建 Harness 时：

```powershell
liteeval --db data/fab-v2-native/liteeval.db harness PATH_TO_ADAPTED_RESEARCH_HARNESS --project PROJECT_ID
liteeval --db data/fab-v2-native/liteeval.db benchmark run RELEASE_ID --project PROJECT_ID --agent-harness RESEARCH_HARNESS_REVISION_ID --judge-connection JUDGE_CONNECTION_ID --trusted-local
```

以上大写项需替换为实际配置。千问平台试跑可直接使用 `examples/run_fab_qianwen.py --run`，配置方式见专用报告；不会默认调用模型。Workspace 仅适用于受信任本地代码；Docker 包运行不使用 --trusted-local。公开/自建 Harness 需要满足协议，不支持时预检拒绝。

## 5. 评分协议

`type="llm_rubric"` 一次提交问题、response.md 和完整 rubric。Judge 必须逐条返回：

```json
{
  "schema_version": 1,
  "check_id": "fab-v2-rubric",
  "verdicts": [
    {"id": "criterion-001", "passed": true, "reason": "相应证据支持该检查"}
  ]
}
```

例子只演示单条形状，实际必须覆盖全部 criterion，不能缺失、重复、添加未知 ID 或改写权重。passed 必须是布尔值。程序计算：

```text
before_gate = sum(weight of passed criteria) / sum(all criterion weights)
Partial Credit = before_gate if every must-pass passed else 0
All-Pass = every criterion passed
```

每条理由、证据、原始权重、must_pass、gate 前分数和最终结果都会保存。汇总另外给出已评分题的 Partial Credit 均值、All-Pass、总题量与覆盖率。Judge JSON 不完整/崩溃是 unscored，不冒充答错或重新归一化剩余权重。

每次评分执行是**单 Judge 的公开集诊断**，可用不同 Judge 重评同一批产物。本轮分别使用 Qwen 与 GLM，汇总为双 Judge 诊断；官方三模型裁决、重复统计和超时计零方法没有在此被自动复刻。平台保留执行错误与未评分状态，报告必须连同覆盖率阅读。

## 6. 验证与后续验收

自动测试覆盖原文、239 条原始检查、severity/must-pass、无伪造 Ground Truth、转换幂等/篡改拒绝、两小时预算、研究能力预检，以及批量 Judge 协议、缺失判定的 unscored。

协议集成测试使用占位 Target 和本地脚本化 Judge，验证一题一个请求、私有评分资源不传 Target；这些是 fixture，不作为 FAB 成绩保存。CI 运行转换一致性与 prepare，不调用真实模型。

两题真实试跑已经完成，实际检索/计算、来源、文件、错误、用量和评分响应全部保留。GLM 的 Markdown JSON fence 曾引发首次解析失败，修复后用原始响应离线重算，原失败记录保留，没有重跑研究或再次调用 Judge。后续应扩展至 27 题、重复运行并校准 Judge；正式官方可比模式另行适配和验收。
