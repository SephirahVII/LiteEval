# API 与配置

基础地址 `http://127.0.0.1:8765`。请求为 JSON 对象，最大 8 MB。除 health 外必须附 `Authorization: Bearer TOKEN`。通用失败：400 参数错误、401 未认证、403 权限错误、404 不存在、409 幂等冲突、500 内部错误。

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

LLM Judge 例见 `examples/judge_config.json`。请求 OpenAI 兼容 `/chat/completions`（base_url 应为 `/v1` 等前缀），要求模型返回 `{"score":0或1,"reason":"理由"}`。`scale=continuous` 时 score 为 0..1；`pass_threshold` 默认 1，`direction` 默认 higher（也支持 lower）。`include_trajectory=true` 可加入轨迹，`json_mode=true` 请求供应商 JSON 模式。generation_parameters 仅允许 temperature、top_p、max_tokens、max_completion_tokens、seed。供应商不支持的选项不要传入；Judge 输出不合法会记 error 并保存原回复。

## Codex 与最终状态

Agent 配置例见 `examples/codex_config.json`。environment.type 为 workspace 或 docker；seed_files 是相对路径→UTF-8 文本，也可由 case.input.files 提供。prompt 优先读取 case.input.prompt，再读取 question。

```json
{"type":"docker","image":"your-codex-image@sha256:...","network":"bridge","memory":"1g","cpus":1,
 "validation_tests":[{"name":"unit","command":["python","-m","unittest"],"timeout_seconds":30}]}
```

Docker 默认 network=none；需要访问模型时显式用 bridge 并在部署层配置网络策略。每个 Attempt 独立目录/容器；只挂载该目录至 /workspace；验证命令只能由管理员配置。最终状态包含文件哈希/文本、测试 stdout/stderr/退出码、patch_diff 和截断标记。最多 1000 个文件、5 MB 总量、单文件 1 MB；跳过 symlink 和 .git。超限缺少文件时 abstain，不能当作文件不存在。单工件最大 16 MB。
