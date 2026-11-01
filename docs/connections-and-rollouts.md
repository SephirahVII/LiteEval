# 模型连接与 Rollout 操作指南（0.3.0）

本轮完成用户 API Key / model endpoint 保存、执行接入、Rollout 统计与导出。当前身份体系仍是单 Owner + 项目访问 Token；连接归属于 Project，不扩展多用户组织成员体系，暂不实现 User Agent。

## 1. 升级和启动

停止 API、Worker，备份数据库与 artifacts 目录，再安装依赖：

```powershell
python -m pip install -e .
```

数据库启动时事务迁移到 schema 5，旧数据、环境变量凭据和实验保持兼容。新增三张表：`model_connections`、`usage_records`、`rollout_events`。

保存 API Key 前，生成一次加密主密钥。以下命令只创建新文件，已有文件会报错，避免覆盖旧主密钥：

```powershell
python -c "from pathlib import Path; from cryptography.fernet import Fernet; p=Path('data/secrets/master.key'); p.parent.mkdir(parents=True,exist_ok=True); p.open('xb').write(Fernet.generate_key())"
$env:LITEEVAL_SECRET_KEY = (Get-Content -Raw data/secrets/master.key).Trim()
```

API 和每个 Worker 都需要配置同一个 `LITEEVAL_SECRET_KEY`。主密钥不写入数据库、不放进 Git；由部署环境保存并妥善备份。丢失后不能解密已有 API Key，更换主密钥也不会自动重加密已有记录。实际部署可由系统密钥管理服务注入。旧环境变量模式不要求设置它。

```powershell
# 终端一，另需配置管理员 Token，或按旧指南使用 Owner 登录。
python -m liteeval.cli api
# 终端二，先加载同一个 LITEEVAL_SECRET_KEY。
python -m liteeval.cli worker
```

`.env.example` 是配置样例；CLI 不自动读取 `.env`。

## 2. 保存连接

`POST /v1/model-connections`。Owner 或本项目 Token 可管理本项目连接。示例只含占位值：

```json
{
  "project_id": "PROJECT_ID",
  "name": "我的评测模型",
  "kind": "openai_compatible",
  "endpoint": "https://YOUR_MODEL_SERVICE/v1",
  "model": "YOUR_MODEL_NAME",
  "api_key": "YOUR_API_KEY"
}
```

支持两种连接：

| kind | endpoint 含义 | model | 调用认证 |
| --- | --- | --- | --- |
| http_agent | 被测 Agent 的完整 POST 地址，保留路径末尾斜杠 | 可选 | 保存 Key 时使用 Bearer |
| openai_compatible | 模型服务基础地址，调用时追加 `/chat/completions` | 必填 | 保存 Key 时使用 Bearer |

无认证服务可省略 `api_key`。endpoint 不接受内嵌账号密码或 query 参数，凭据单独提交。接口不返回 Key 原文、密文或末尾字符，只返回 `has_api_key` 和 `key_version` 等元数据。

- `GET /v1/model-connections?project_id=ID&limit=50&offset=0&archived=exclude`：分页查询。
- `GET /v1/model-connections/{id}`：读取元数据。
- `PATCH /v1/model-connections/{id}`：修改 `name`、替换 `api_key` 或设置 `archived`。
- `{"api_key": null}`：移除凭据，之后按无认证服务调用；不是撤销上游供应商 Key。
- `{"archived": true}`：禁止该连接的新绑定和后续调用；恢复使用 false。

endpoint、model、kind 在创建后不变，修改它们应创建新连接和新的 Agent / Judge 修订。替换 Key 会递增 `key_version`，已引用该连接的后续调用使用新 Key；历史执行结果不改写。

## 3. 接入被测 Agent 与 Judge

HTTP Agent：

```json
{
  "project_id": "PROJECT_ID",
  "label": "agent-v1",
  "config": {"type": "http", "connection_id": "HTTP_CONNECTION_ID"}
}
```

提交到 `POST /v1/agent-versions`。默认发送 `{"input": case.input}`，默认读取 `answer`；可继续配置现有 `request_template`、`output_path`、`events_path`、`usage_path`。

如果 connection 是 `openai_compatible`，同样使用 `type=http`，平台默认发送保存的 model 与 `case.input.prompt` 组成的单条 user message，读取 `choices[0].message.content`。Case 使用其他输入字段时要明确配置 request_template。

Judge：

```json
{
  "project_id": "PROJECT_ID",
  "name": "答案正确性",
  "kind": "llm_judge",
  "config": {
    "connection_id": "MODEL_CONNECTION_ID",
    "rubric": "根据输入与参考答案判断被测回答是否正确。"
  }
}
```

提交到 `POST /v1/evaluators`，也适用于评分器 revisions 接口。绑定时自动固定 endpoint、model，不把 Key 写入配置。创建 Agent / Evaluator 仍要求现有 Owner/管理员权限；保存连接本身允许本项目 Token。不能混用 connection_id 与环境变量认证，也不能跨项目绑定连接。

Codex 自定义模型 endpoint 暂未接入该连接机制，保留原有 Codex 配置和环境变量凭据。

Worker 心跳按连接逐一报告能否解密，仅返回布尔值；预检据此提示就绪。能解密不代表上游 Key 有效、额度充足或模型可访问，实际调用结果以 Run 为准。依旧不保证按能力定向路由。

## 4. 查看执行内容和统计

原有 `GET /v1/runs/{id}` 提供最终输出、Trace、Score、尝试记录。

新增接口：

| 接口 | 内容 |
| --- | --- |
| GET /v1/runs/{id}/events?after=0&limit=200 | 持久化活动日志、调用完成信息，返回 next_cursor / has_more |
| GET /v1/runs/{id}/statistics | 单个 Run 的执行、轨迹、输出、用量统计 |
| GET /v1/experiments/{id}/statistics | 整个实验统计 |

`after` 使用上次返回的 next_cursor；limit 为 1..1000。不同尝试通过 attempt_id 区分。本地子进程完整日志行在运行中持续写入，未完成的行在换行或进程结束时写入。HTTP Agent 的内部轨迹在响应后才能取得，必须由 Agent 主动返回 events。当前提供游标轮询，不提供 SSE/WebSocket。

统计包含：Run 状态、尝试与重试次数、工具调用数与名称分布、来源提供的工具耗时、Run 耗时均值/P50/P95、最新 Run 输出字符及 UTF-8 字节数、采集日志字节、工件数量及大小。

用量按实际记录的 Target / Judge 调用和重试累计，可按角色与模型查看：

- input_tokens / output_tokens / total_tokens。
- cached_input_tokens / cache_write_tokens / reasoning_tokens。
- 上游返回的费用按币种分别累计，不自动推算模型价格。
- 每项给出 known_samples，未提供的计量返回 null。不同别名不会相加；缓存、reasoning 不再叠加到输入/输出总量。
- 保留原始 usage，并报告无效计数、别名冲突、多轮采样不完整等告警。Codex 改为累计各 turn.completed，记录每个字段的已知回合数。

`GET /v1/experiments/{id}/results` 的 summary 新增 `rollout_usage`；旧 agent_usage 等字段保留原行为。

覆盖边界：旧 Target 用量可从 attempts 回退读取；旧 Judge 历史与重试无法完整重建。运行中统计标记 partial。缓存统计依赖供应商字段，不表示平台已实现结果缓存。reasoning token 是计量，不是隐藏 CoT 正文。TTFT、生成速率、CPU/内存实测尚未实现。文件快照仍有大小限制，不是完整文件系统备份。

## 5. 导出

```text
GET /v1/experiments/{id}/export?format=json
GET /v1/experiments/{id}/export?format=jsonl
GET /v1/experiments/{id}/export?format=csv
GET /v1/experiments/{id}/export?format=zip
GET /v1/runs/{id}/export?format=zip
```

| 格式 | 内容 |
| --- | --- |
| JSON | 固定 Case、实验与配置、Run、每次尝试及 Trace/trajectory、评分、原始与标准用量、活动日志、工件元数据及统计 |
| JSONL | 上述内容按 record_type 拆分为记录，适合脚本处理 |
| CSV | 每 Run 一行，汇总各角色用量、耗时和结果；UTF-8 BOM，兼容 Excel，防公式注入 |
| ZIP | rollout.json、records.jsonl、manifest.json 和已捕获工件文件；工件读取时核对 SHA-256 |

JSON/JSONL 只引用工件元数据；ZIP 包含实际已捕获的内容。CSV 是摘要，不包含完整轨迹。导出不包含连接表和凭据密文；调用中已知的凭据在响应、Trace、日志与工件保存时脱敏。导出权限沿用项目隔离。

元数据和 ZIP 打包有 128 MB 上限，超过时明确报错，应按单条 Run 分批导出。运行中可导出已保存的部分内容；本次导出以数据库读取快照为准。

PowerShell 下载例子（headers 使用你已有的 Owner/管理员或项目 Token）：

```powershell
Invoke-WebRequest -Uri "$base/v1/experiments/$experimentId/export?format=zip" -Headers $headers -OutFile "rollout.zip"
```

## 6. 验证

```powershell
python -m unittest discover -s tests -v
python examples/run_demo.py
python examples/run_readiness_demo.py
python examples/run_catalog_demo.py
```

新增集成测试覆盖加密持久化/替换/删除、错误主密钥、项目权限、连接绑定、真实本地 HTTP Target/Judge、Judge 重试用量、运行中日志读取、租约隔离、四种导出、ZIP 工件哈希与凭据脱敏。真实供应商、业务 Agent、Codex/Docker 执行环境仍需使用你自己的配置进行验收。

加密实现使用 cryptography 的 [Fernet](https://cryptography.io/en/latest/fernet/)，主密钥由部署环境单独保管。
