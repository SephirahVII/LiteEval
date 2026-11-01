# 前端原型对接后端接口

适用版本：LiteEval 0.2.2，数据库 schema 4，事件 schema 2。本文中的新接口均已实现；人工复核、Eval Spec 与 Deployment Gate 尚未实现。前端原型可继续独立开发，本轮后端没有修改前端目录。

## 1 启动与认证

日常账号、登录/退出、CSRF 和 PowerShell 完整操作见 [第一步指南](step-1-backend.md)。0.2.2 启动方式保持相同：

```powershell
python -m liteeval.interfaces.cli.main owner-init --username owner
python -m liteeval.interfaces.cli.main api
# 另一个终端
python -m liteeval.interfaces.cli.main worker
```

已有账号无需重复 owner-init；重复初始化会撤销旧会话。升级已有数据前停止所有旧 API/Worker 并备份数据库及工件，再用同一版本重新启动；首次打开自动事务迁移至 schema 4。

开发前端建议使用同源 `/v1` 代理。例如前端位于 `http://localhost:5173`、API 位于 `127.0.0.1:8765`，在 API 的启动环境中设置：

```powershell
$env:LITEEVAL_PUBLIC_ORIGIN = 'http://localhost:5173'
$env:LITEEVAL_COOKIE_SECURE = '0'
python -m liteeval.interfaces.cli.main api
```

前端将 `/v1` 请求代理到 API，通过相对地址请求并保留 Cookie。浏览器会自动发送 Origin，前端不要尝试手动设置该请求头。HTTPS 正式入口使用对应 PUBLIC_ORIGIN，并启用 Secure Cookie。

- POST `/v1/auth/session`：`{username,password}`，返回 csrf_token，并设置 HttpOnly Cookie。
- GET `/v1/auth/me`：读取身份和 csrf_token，用于页面刷新后恢复会话。
- POST/PATCH/DELETE 等写请求：发送 Cookie 和 `X-CSRF-Token`；不要在浏览器 localStorage 保存管理员 API Token。
- DELETE `/v1/auth/session`：撤销会话。401 时清除页面身份状态；403 显示权限或同源校验错误。
- 项目 API Key 继续使用 Bearer；配置评分器、创建预览和查询全局运行状态仍要求 Owner/管理员。

## 2 已有 Web 接口

| 页面 | 接口 | 返回与用法 |
| --- | --- | --- |
| 项目 | GET/POST `/v1/projects` | 项目列表、创建；创建仅 Owner/管理员 |
| 数据集 | GET/POST `/v1/datasets` | 创建与分页列表，可按 project_id 筛选 |
| Case | GET/POST `/v1/datasets/{id}/cases` | 输入、expected、metadata、revision |
| Case 编辑 | POST `/v1/cases/{id}/revisions` | input/expected/metadata 加 base_revision；冲突为 409 |
| 归档 | PATCH `/v1/datasets/{id}`、`/v1/cases/{id}`、`/v1/evaluators/{id}` | archived 布尔；Dataset 另支持 name/description |
| Trace | GET `/v1/traces`、`/v1/traces/{id}` | 基本分页/筛选和事件详情；高级时间检索尚未增加 |
| Agent | GET/POST `/v1/agent-versions` | 不可变执行配置；凭据只能使用环境变量引用 |
| 评分器 | GET/POST `/v1/evaluators` | 下文增加修订和 latest 查询 |
| 实验 | GET/POST `/v1/experiments` | 分页列表/创建；0.2.2 增加 name、description、diagnostic 和版本来源 |
| 进度 | GET `/v1/experiments/{id}/progress` | 状态、terminal/pending/running/retrying 等计数，约 2 秒轮询 |
| 分页结果 | GET `/v1/experiments/{id}/runs` | limit/offset、status/outcome；include_output=true 才附输出 |
| Run 详情 | GET `/v1/runs/{id}` | 输出、正式评分、Trace、Attempt 和工件引用 |
| 重跑/取消 | POST `/v1/experiments/{id}/rerun`、`/cancel` | 原快照重跑；取消是异步请求 |
| 对比 | GET `/v1/compare?left={id}&right={id}` | 相同快照和评分器修订才可比较 |
| 评分预览 | POST `/v1/evaluator-previews`、GET `/v1/evaluator-previews/{id}` | Worker 任务；支持真实 Run 或合成样本，不覆盖正式 Score |

列表一般返回 `{items,total,limit,offset}`，默认 50、最大 200。Dataset/Case/Evaluator 支持 `archived=exclude|include|only`，默认 exclude。旧 Agent/Evaluator 读取仍含 `config_json` 字符串；配置表单需解析为对象，再通过 config 字段提交。

进度 terminal 包含执行失败、超时与取消，不代表质量通过。Case outcome 的 pass/fail 与 Run status 的 succeeded/failed 分别展示。实验 details/list 的 diagnostic 为布尔值。

## 3 导入预览

POST `/v1/datasets/{id}/import-preview`，请求与正式 import 相同：

```json
{
  "records": [
    {"input": {"question": "退款方式"}, "expected": "在订单页申请", "metadata": {"critical": true}},
    {"input": "错误类型"}
  ]
}
```

也可发送 `{csv_text:"..."}`，两种来源不能同时提供。CSV 的 input/expected/metadata 单元格使用 JSON，支持 UTF-8 BOM。预览无数据库写入、不调用 Agent/Judge。

有非法行时 HTTP 仍为 200，返回 valid=false、total、valid_count、invalid_count、errors、errors_truncated 和最多 5 条有效记录 preview。errors 元素为 `{row,line,field,message}`：row 从 1 开始，line 为 CSV 记录结束的物理行号，JSON 导入时为 null。最多返回 100 条错误，但 invalid_count 统计整个批次。

格式本身错误、没有 input 列或批次不在 1–10000 条时返回 400。正式 POST `/v1/datasets/{id}/import` 会再次用相同规则校验；非法批次返回 400/invalid_import 和 validation，整批不入库。API 请求体最大 8 MB，CSV 单字段上限也为 8 MB，实际提交以序列化后的请求体大小为准。

## 4 评分器修订

每个评分器有稳定的 evaluator_id，以及每次修订独立的 id 与递增 revision。**实验的 evaluator_ids 必须填写所选修订的 id**；第一版的 id 恰好与 evaluator_id 相同，后续版本不相同。

GET `/v1/evaluators?project_id={id}&latest=true` 返回每个评分器的最高修订。默认不传 latest 时仍返回全部修订，保持旧接口兼容。归档按修订生效；latest 选中最高修订后再应用 archived 筛选。

POST `/v1/evaluators/{revision_id}/revisions`：

```json
{
  "base_revision": 1,
  "name": "答案正确性",
  "config": {"output_field": "$.answer"}
}
```

返回 201，新 id、evaluator_id、revision=2、config_json 和 config_hash。base_revision 必须等于该评分器当前最高修订，否则 409。name 可省略；kind 不可在同一评分器中改变，需要另建评分器。GET 同路径可分页读取修订历史，包括已归档修订。

修改评分器不改变旧实验。新旧实验使用不同修订时，即使 kind/config 内容相同，也不产生直接回归比较结论。

## 5 发布数据集版本

POST `/v1/datasets/{id}/versions`：

```json
{"name":"support-regression-2026-11","description":"退款和订单关键用例"}
```

返回 201：id、dataset_id、project_id、name、description、snapshot_id、snapshot_hash、case_count、created_at。名称在同一 Dataset 内唯一，重复返回 409。发布时冻结当前未归档 Case 的 id、revision、内容 hash 及顺序。

- GET `/v1/datasets/{id}/versions`：版本列表，支持 limit/offset。
- GET `/v1/dataset-versions/{id}`：版本详情。
- GET `/v1/dataset-versions/{id}/cases`：分页读取冻结 Case，不读取当前草稿。

已发布版本没有修改/删除接口。草稿编辑、Case 归档或新增 Case 都不改变已发布内容。Dataset 归档后仍能读取历史版本，但不能用于新实验；历史实验 rerun 保留原快照。

## 6 实验预检与提交

POST `/v1/experiments/preflight`，参数与新建实验相同。dataset_id 和 dataset_version_id **只能提供一个**。

```json
{
  "project_id": "PROJECT_ID",
  "dataset_version_id": "DATASET_VERSION_ID",
  "agent_version_id": "AGENT_VERSION_ID",
  "evaluator_ids": ["EVALUATOR_REVISION_ID"],
  "name": "候选版本评测",
  "diagnostic": false,
  "config": {"concurrency": 4, "max_retries": 1, "judge_retries": 1}
}
```

响应字段：

| 字段 | 含义 | 前端处理 |
| --- | --- | --- |
| ready | 静态必需证据校验是否通过 | 不是 Agent 质量分数或上线结论 |
| submission_allowed | 当前标准/诊断模式是否允许提交 | false 时阻止页面提交并显示原因 |
| blocking_errors | 缺 expected、工具参数类型错误、最终状态不可用、空集等 | 展示数量、修订与必要的 Case ID |
| warnings | 轨迹需运行验证、Worker 状态未确认等 | 展示风险与排队状态，不隐去未知信息 |
| source | Dataset、命名版本、snapshot_hash、Case/critical 数 | 提交摘要展示固定口径 |
| evidence_coverage | 每个评分器的静态 expected 证据计数 | 不代表实际评分覆盖率 |
| credential_readiness | Worker 上报的凭据/能力状态 | 见下文，属于提示信息 |
| estimated_call_count | agent_max 和 judge_max | 按重试上限估算，不保证实际调用这些次数 |
| estimated_cost | 当前为 null | 没有定价依据，显示未知 |
| preflight_hash | 不可变执行输入的摘要 | 随创建请求提交，不在页面自行生成 |

预检不写快照、不创建实验、不调用外部服务。ready 只表示已能静态判断的必需证据通过，实际输出、轨迹、文件、模型服务和执行结果仍在运行后验证。

前端确认后 POST `/v1/experiments`，发送同样的执行参数并增加 preflight_hash。服务器在冻结快照的同一个事务中重新核对：

- 输入变化：409/stale_preflight，响应带当前 preflight，要求重新展示确认。
- 证据阻塞：400/preflight_failed，响应带 preflight。
- 允许提交：201，返回实验 id、快照 hash、case_count、diagnostic 和 name。

哈希绑定用例内容与修订、来源版本、Agent 配置、评分器修订、运行配置和 diagnostic；名称/描述不影响执行摘要。已发布版本不因草稿编辑而变化。Worker 心跳不进入哈希，避免每次心跳使已确认请求过期。

一次用户提交生成固定 Idempotency-Key，网络重试复用该 key 和完全相同的请求体。成功提交后即使草稿又改动，相同请求重试仍返回原实验并带 reused=true；不能因为响应丢失又生成一个新 key。

为兼容旧客户端，不带 preflight_hash 的旧创建请求仍按原规则接受。**新前端应始终携带哈希**，不能把旧兼容入口当作静态校验已通过。需要缺证据诊断时，将 diagnostic=true 后重新预检；诊断标记随 rerun 保留，未来不能用作正式 Gate 通过依据。空集、权限、归档和无效配置不能通过诊断模式绕过。

## 7 Worker 与凭据状态

GET `/v1/runtime/status` 仅 Owner/管理员可用，返回 Worker 列表、在线数、实验/预览排队数。CLI Worker 启动时注册，约每 5 秒心跳；15 秒没有心跳或已 stop 时视为离线。run 单实验命令不是常驻 Worker，不会登记在线状态。

Worker 仅上报当前配置引用的环境变量名和是否存在，不上报变量值、主机路径或完整进程环境。codex_cli/docker_cli 只检查命令可用性，不代表账号、Engine、镜像和沙箱已验收。

预检的 credential_readiness 根据同一个在线 Worker 是否同时具备所需凭据与能力计算：ready 表示全部在线 Worker 满足，partial 表示只有部分满足，missing 表示在线 Worker 均不满足，unknown 表示没有在线上报。不能把两个 Worker 各自一半的凭据合并成就绪。

当前调度器未按能力定向路由，因此返回 advisory=true、routing_guaranteed=false。凭据或配置变化可能在下一次心跳才反映；unknown/partial/missing 显示警告，不当作服务端已验证的强保证。没有 Worker 时，页面应明确提示任务将排队。

## 8 一键联调与当前范围

```powershell
python -m unittest discover -s tests -v
python examples/run_catalog_demo.py
```

新 Demo 自动启动本地 API、示例 Agent 和真实 CLI Worker 子进程，验证导入预览、版本发布、评分器修订、Worker 心跳、带哈希提交、版本 Compare 与过期预检。报告为 `data/catalog-report.json`。不使用日常数据库，不调用真实模型。

Overview 聚合、Trace 高级查询、人工复核、Eval Spec、Gate 和 CI 发布阻断仍是后续工作，不能在原型中显示为已接通的真实能力。本轮测试与边界见 [0.2.2 验证记录](verification-0.2.2.md)。
