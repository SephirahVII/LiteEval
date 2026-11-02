# API 与配置

当前软件版本 0.4.2，数据库 schema 6。模型连接、凭据加密、Rollout 统计与导出的完整请求示例见 [操作指南](connections-and-rollouts.md)。公开金融题库与真实执行结果见 [FAB v2 报告](fab-qianwen-trial.md)，两种示例入口见 [Benchmark 索引](../benchmarks/README.md)。导入、修订、版本、预检和 Worker 接口见 [前端后端接口约定](frontend-backend-contract.md)。下方保留 Dataset 服务接口说明。

## 0.3.0 新增接口

| 方法与路径 | 作用 |
| --- | --- |
| POST /v1/model-connections | 保存本项目 endpoint、model 和加密 API Key |
| GET /v1/model-connections?project_id=ID | 分页查询连接，仅返回元数据 |
| GET /v1/model-connections/{id} | 读取连接元数据 |
| PATCH /v1/model-connections/{id} | 更名、替换/移除 Key、归档/恢复 |
| GET /v1/runs/{id}/events?after=0&limit=200 | 持久化活动与本地运行日志的游标查询 |
| GET /v1/runs/{id}/statistics | Run 执行/轨迹/用量统计 |
| GET /v1/experiments/{id}/statistics | 实验统计，含 Target/Judge 重试用量 |
| GET /v1/runs/{id}/export?format=zip | 导出单条 Run，支持 json/jsonl/csv/zip |
| GET /v1/experiments/{id}/export?format=zip | 导出实验，支持相同四种格式 |

连接归属 Project；跨项目访问返回 403。创建 Agent 或 LLM Judge 时在 config 中引用 connection_id，自动固定 endpoint/model；密钥只在执行时解密。既有环境变量认证保留。新增 summary.rollout_usage，原有统计字段保留。

## 0.2.2 新增接口

| 方法与路径 | 请求/作用 |
| --- | --- |
| POST /v1/datasets/{id}/import-preview | 与 import 相同的 records/csv_text；返回逐行错误，不入库 |
| GET /v1/evaluators?latest=true | 每个稳定 evaluator_id 的最高修订，保留原分页结构 |
| GET/POST /v1/evaluators/{revision_id}/revisions | 修订历史；管理员用 config、base_revision、可选 name 创建新修订 |
| GET/POST /v1/datasets/{id}/versions | 命名不可变快照；name 在同一 Dataset 内唯一 |
| GET /v1/dataset-versions/{id} | 版本详情、hash 和 Case 数 |
| GET /v1/dataset-versions/{id}/cases | 冻结 Case 分页 |
| POST /v1/experiments/preflight | 静态证据/配置校验、调用上限和 Worker 状态；不调用外部服务 |
| GET /v1/runtime/status | 管理员读取 Worker 心跳、能力、引用凭据状态和队列摘要 |

新建实验支持 dataset_version_id（与 dataset_id 互斥）、name、description、diagnostic、preflight_hash。新前端携带哈希时，过期返回 409/stale_preflight，必需证据不足返回 400/preflight_failed；旧客户端不带哈希仍兼容。实验始终绑定评分器修订 id，而不是稳定 evaluator_id。

基础地址 `http://127.0.0.1:8765`。请求为 JSON 对象，最大 8 MB。除 health 和会话登录外需要 Bearer Token 或浏览器会话；Cookie 写请求还要求匹配 Origin 和 X-CSRF-Token。通用失败：400 参数错误、401 未认证、403 权限错误、404 不存在、409 幂等/修订/预检冲突、500 内部错误。当前数据库 schema 为 4，事件 schema 仍为 2。

## 0.2.1 新增接口

| 方法与路径 | 请求/作用 |
| --- | --- |
| POST /v1/auth/session | `{username,password}` 加同源 Origin；返回 HttpOnly Cookie 与 csrf_token，失败登录限速 |
| GET /v1/auth/me | 当前身份；会话模式可读取 csrf_token |
| DELETE /v1/auth/session | 带 Cookie/Origin/CSRF，撤销会话 |
| GET /v1/experiments/{id}/progress | 状态与计数，不返回输出与全部评分 |
| GET /v1/experiments/{id}/runs | limit/offset、status/outcome 筛选；默认不返回 output，include_output=true 可启用 |
| PATCH /v1/datasets/{id} | `{name?,description?,archived?}`，至少一个字段 |
| PATCH /v1/cases/{id} | `{archived:bool}` |
| PATCH /v1/evaluators/{id} | 管理员，`{archived:bool}` |
| POST /v1/evaluator-previews | 管理员，`{project_id,evaluator_id,run_id}` 或 `{project_id,evaluator_id,case,run}`；202 返回任务 ID |
| GET /v1/evaluator-previews/{id} | 经项目鉴权读取预览状态/结果，不暴露任务 payload |

Dataset、Evaluator 和 Case 列表/导出新增 `archived=exclude|include|only`，默认 exclude。新实验仅冻结未归档 Case，不接受归档 Dataset/Evaluator；历史快照和 rerun 不受归档影响。

Case 修订新增 `base_revision`，不匹配返回 409。旧客户端省略该字段仍兼容，响应 Warning 提示升级。Dataset 导出支持 `format=csv`，默认 JSON；CSV 带 UTF-8 BOM，导入支持带 BOM 文件。

评分预览由 Worker 执行，使用不可变配置和冻结样本，既不调用 Agent 也不修改正式 Score。LLM Judge 会消耗额度并保存每次原始回复，错误时最多额外重试一次。完整字段、会话安全、启动和 PowerShell 示例见 [第一步操作指南](step-1-backend.md)。

## 接口

| 方法与路径 | 请求/作用 |
| --- | --- |
| GET /v1/health | 无需认证，返回服务及 schema 版本 |
| POST /v1/projects | 管理员，`{name}` |
| POST /v1/projects/{id}/api-keys | 管理员，`{label}`；明文 token 仅签发时返回 |
| POST /v1/api-keys/{id}/revoke | 管理员，撤销项目密钥 |
| POST /v1/datasets | `{project_id,name}` |
| POST /v1/traces | `{project_id,input,output?,events?,status?,capture_level?,metadata?}` |
| POST /v1/datasets/{id}/cases | `{input,expected?,metadata?}` |
| POST /v1/datasets/{id}/cases/from-trace | `{trace_id,expected?,metadata?}`，仅生产 Trace 可转 Case |
| POST /v1/cases/{id}/revisions | `{input,expected?,metadata?}`，生成新修订 |
| POST /v1/datasets/{id}/import | `{records:[...]}` 或 `{csv_text:"..."}`，完整批次校验后原子导入 |
| GET /v1/datasets/{id}/export | `{records:[...]}`，当前 Case 修订 |
| GET /v1/datasets/{id}/cases | Case 分页，可筛选 category、severity |
| POST /v1/agent-versions | 管理员，`{project_id,label,config}` |
| POST /v1/evaluators | 管理员，`{project_id,name,kind,config}` |
| POST /v1/experiments | `{project_id,dataset_id,agent_version_id,evaluator_ids,config?}`；可附 Idempotency-Key |
| POST /v1/experiments/{id}/cancel | 申请取消，终结实验不再变化 |
| POST /v1/experiments/{id}/rerun | `{agent_version_id?}`，原快照/评分器/运行配置，新 Run ID |
| GET /v1/experiments/{id}/results | summary、runs 和 scores |
| GET /v1/runs/{id} | 输出、最终 Trace、评分、Attempt 历史 |
| GET /v1/compare?left={id}&right={id} | 严格配对的 Case Compare |
| GET /v1/artifacts/{id}/content | 经项目认证下载工件，检查 SHA-256 |

下列资源支持 GET 列表及 GET `/v1/资源/{id}`：projects、datasets、traces、agent-versions、evaluators、experiments、artifacts。cases 支持单项读取，列表走所属 Dataset。

列表参数：`project_id`（项目密钥自动限制项目）、`limit=1..200`（默认 50）、`offset>=0`。返回 `{items,total,limit,offset}`，按 ID 固定排序。精确筛选：projects/datasets 的 name；traces 的 status/kind；agent-versions 的 label；evaluators 的 name/kind；experiments 的 status；artifacts 的 kind。Case 使用 metadata.category/severity 精确筛选。部分资源同时保留 `*_json` 原始序列化列供调试。

## 数据集导入

```json
{"records":[
  {"input":{"question":"如何退款？"},"expected":"退款请在订单页申请","metadata":{"category":"support","severity":"high"}}
]}
```

一次 1..10000 条，受请求体大小限制。input 和 metadata 必须为对象。`expected_output` 兼容作为 expected 的别名。CSV 列为 `input,expected,metadata`，每个单元格为 JSON（缺失 expected/metadata 可为空）；文本答案需要 JSON 字符串双引号，并按 CSV 规范转义。

## HTTP Agent

```json
{
  "type":"http",
  "endpoint":"https://your-agent.example/run",
  "timeout_seconds":30,
  "auth":{"type":"bearer","token_env":"AGENT_TOKEN"},
  "headers_env":{"X-API-Key":"OTHER_AGENT_KEY"},
  "headers":{"X-Eval-Mode":"regression"},
  "request_template":{"query":"$.input.question","context":"$.metadata"},
  "output_path":"$.result.answer",
  "events_path":"$.events",
  "usage_path":"$.usage",
  "cost_path":"$.cost",
  "currency":"CNY"
}
```

默认为发送 `{"input":case.input}`，读取 `answer`，可选读取 events/usage/cost/currency。模板中 `$` 开头的字符串是字段引用，可用 `$.input`、`$.expected`、`$.metadata`、`$.case`；支持点路径与数字数组下标，不支持过滤表达式或字符串插值。传输固定为 JSON POST，HTTP 响应对象和事件数组有显式校验。

密钥只能使用环境变量引用，禁止配置中直接写 Authorization/Cookie/API key。Worker 自动添加稳定 Run ID 的 `Idempotency-Key`。请求和响应各限制 4 MB，不携带凭据跟随重定向。响应 `trajectory_complete=true` 表示 Agent 声明事件完整。

## 实验配置

```json
{"concurrency":4,"max_retries":1,"judge_retries":1,"retry_delay_seconds":0.5}
```

concurrency 范围 1..32；两种 retries 为 0..3；retry_delay_seconds 为 0..30。默认 Agent 不重试，Judge 最多额外重试一次。HTTP 429、5xx、网络错误、超时可触发 Agent 重试；映射/认证缺失等不会自动重试。每次重试新建执行环境。默认心跳周期 5 秒，取消通常在下一次心跳触发；外部 HTTP 请求可能继续至超时。

## Evaluator

| kind | config | 所需证据 |
| --- | --- | --- |
| exact_match / contains | 可选 output_field、expected_field | expected |
| regex | pattern | 输出 |
| json_schema | schema | JSON 输出 |
| tool_called | tool_name | 轨迹 |
| tool_parameter | tool_name、parameters（缺省使用 case.expected） | 工具 arguments 或 JSON input_parts |
| latency | max_ms | 耗时 |
| cost | max_cost、currency | 已上报成本且币种一致 |
| file_exists | path | 最终文件清单 |
| file_content | path、expected（缺省使用 case.expected） | 最终文本文件 |
| test_passed | test_name | 配置验证命令的退出码与超时状态 |
| llm_judge | base_url、model、rubric 等 | 输出；可选轨迹 |

JSON Schema 仅支持 type、properties、required、additionalProperties（布尔）、items、enum、minimum、maximum、title、description；递归校验配置，未知关键字报错，不宣称支持完整标准。

LLM Judge 例见 `examples/config/judge.json`。请求 OpenAI 兼容 `/chat/completions`（base_url 应为 `/v1` 等前缀），要求模型返回 `{"score":0或1,"reason":"理由"}`。`scale=continuous` 时 score 为 0..1；`pass_threshold` 默认 1，`direction` 默认 higher（也支持 lower）。`include_trajectory=true` 可加入轨迹，`json_mode=true` 请求供应商 JSON 模式。generation_parameters 仅允许 temperature、top_p、max_tokens、max_completion_tokens、seed。供应商不支持的选项不要传入；Judge 输出不合法会记 error 并保存原回复。

## Codex 与最终状态

Agent 配置例见 `examples/config/codex.json`。environment.type 为 workspace 或 docker；seed_files 是相对路径→UTF-8 文本，也可由 case.input.files 提供。prompt 优先读取 case.input.prompt，再读取 question。

```json
{"type":"docker","image":"your-codex-image@sha256:...","network":"bridge","memory":"1g","cpus":1,
 "validation_tests":[{"name":"unit","command":["python","-m","unittest"],"timeout_seconds":30}]}
```

Docker 默认 network=none；需要访问模型时显式用 bridge 并在部署层配置网络策略。每个 Attempt 独立目录/容器；只挂载该目录至 /workspace；验证命令只能由管理员配置。最终状态包含文件哈希/文本、测试 stdout/stderr/退出码、patch_diff 和截断标记。最多 1000 个文件、5 MB 总量、单文件 1 MB；跳过 symlink 和 .git。超限缺少文件时 abstain，不能当作文件不存在。单工件最大 16 MB。
