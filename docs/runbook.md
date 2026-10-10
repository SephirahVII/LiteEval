# 运行、接入与升级

## HTTP Agent 回归评测

1. 运行 README 的 API 和 Worker；凭据分别只配置给需要它的进程。
2. 管理员创建 Project 和项目密钥；项目密钥用于 SDK 和实验客户端。
3. 新建 Dataset，批量导入有 expected 的真实样本，或从生产 Trace 转 Case。
4. 管理员创建 HTTP Agent Version，配置自己的请求映射、输出路径和密钥环境变量引用。
5. 先建立 exact_match / JSON Schema / tool_parameter 等规则基线，按任务补充 Judge。
6. 提交 Experiment，客户端为同一次提交保留固定 Idempotency-Key。Worker 完成后查看 summary、失败 Run、对应 Trace 和工件。
7. 创建新版 Agent Version，通过旧实验 `rerun` 并传新版 agent_version_id，再 Compare。这样数据集后续编辑不会混入回归比较。

不要只看执行 completed；同时查看质量通过率、执行成功率、评分覆盖率、Judge error/abstain 数与回退 Case。

## 真实 Judge 联调

修改 `examples/judge_config.json` 中 base_url、model、rubric，再将 api_key_env 对应凭据配置到 Worker 环境。可在本机保留独立配置文件，避免提交私密服务信息。运行：

```bash
python -m liteeval.cli judge-check examples/judge_config.json
```

先确认结构化评分成功、模型名正确、供应商参数兼容，再将相同配置注册为 llm_judge。用少量人工标注样本验证评分尺度、阈值与理由，之后加入实际实验。失败的 Judge 不重跑 Agent，而是独立重试；每次返回的原始回复单独留档。

此版本没有实际供应商凭据，联调用本地 HTTP 模拟模型完成；真实供应商成功不应由模拟测试推断。Judge 一致性、偏差、重复采样和人工校准由实际数据验收。

## SDK 接入

```python
from liteeval.sdk import TraceClient
from liteeval.core import ContentPart

client = TraceClient("http://127.0.0.1:8765", "PROJECT_ID", "PROJECT_TOKEN")

@client.trace(agent_version="support-v1", model="your-model")
def agent(question):
    with client.span("tool", "lookup_policy", {"question": question}) as event:
        answer = lookup_policy(question)
        event.output_parts = [ContentPart("text", text=answer)]
    return answer
```

同一个装饰器支持 async def，嵌套 span 支持 parent_id 和时间信息。使用 input_mapper 将函数参数转换为目标输入。默认 fail_open=True，上报失败保留业务结果并在 client.last_error 记录错误类型。可用 log_content=False 关闭输入/输出内容记录；attributes 仍是调用者显式记录的元数据。SDK 不自动截获工具/模型调用，需要显式 span，或框架另写适配。

## Codex 执行

可信 Worker 安装并配置真实 Codex CLI；workspace 模式使用 Worker 环境的 codex，可通过 `LITEEVAL_CODEX_BINARY` 指定可信安装路径。model 填当前账户可用模型名；API 凭据通过 api_key_env 提供。Agent 调用 `codex exec --json --sandbox workspace-write --skip-git-repo-check -`，prompt 通过 stdin，保持 Codex 自身沙箱开启。

workspace 是新建目录，任务文件相互隔离，但本机进程不是强隔离。模型调用、CLI 沙箱和认证需要在目标平台实测；不能把替身 CLI 测试当作真实 Codex 验收。Windows 子进程终止不保证清除所有后代进程；运行不受信任代码时使用经验证的 Linux 隔离部署。

参考官方命令文档：https://developers.openai.com/codex/noninteractive 。此版本保存 stdout JSONL、stderr、usage 和最终文件/测试工件，CLI 输出变化仍可通过原始 JSONL 排查。

## Docker 环境与部署

Docker 任务环境需要 Worker 主机具有 Docker CLI、可访问的 Docker Engine，以及包含 Codex 和任务依赖的固定镜像。非 root 用户运行容器，根文件系统只读，仅 /workspace 与 /tmp 可写，镜像需将 Codex 认证/缓存目录设置到可写位置。不会把宿主机 HOME 注入容器。Codex 自身沙箱与 Docker 的兼容性需要针对内核、权限和镜像测试；失败会作为失败记录，不自动关闭沙箱。

环境固定设置 cap-drop ALL、no-new-privileges、pids/memory/cpu 限制，拒绝任意用户 mount/特权选项。仅绑定本 Attempt 目录，不把 Docker socket 挂进任务。Docker network=none 会阻止外部模型访问；bridge 允许网络但不代表出口域名过滤，网络策略由宿主部署设置。

仓库 Dockerfile/compose.yaml 提供 **HTTP Agent + Judge 服务**的单机部署示例：

```bash
cp .env.example .env
# 在 .env 中填写管理员密钥和 Worker 凭据
# Linux Docker Engine；在仓库目录运行
docker compose up --build -d
```

API 与 Worker 共享命名卷，使用 Linux host networking 保留 API loopback 绑定并访问主机上的 Agent。此镜像未预装 Codex 或 Docker CLI/Engine，不是 Docker 任务环境的完整 Worker 镜像。需要 Codex 隔离任务时，在可信宿主机部署 Worker 或另行构建固定依赖镜像，保留数据库/工件目录。

API 仅绑定 loopback；远程客户端通过带 TLS 的反向代理接入，并在部署层添加请求限速。项目客户端不能更改 Agent/Evaluator 配置，管理员需负责配置可信 endpoint、环境变量引用和验证命令。当前没有细粒度角色、审计日志或多机调度。

Docker 约束参考：https://docs.docker.com/reference/cli/docker/container/create/ 。本环境没有 Docker，尚未进行真实容器验收。

## 数据升级、恢复与备份

- schema v1 会自动增量迁移至 v2，保留既有项目、Trace、Case 和实验。升级前停止旧 API/Worker，备份数据库及其旁边的 artifacts 目录，再启动新版，不能混跑两个 schema 版本。
- 停机备份最简单；运行中备份使用 SQLite backup API，避免仅拷贝 WAL 模式下的单个 .db。数据库元数据和工件文件必须一起备份。
- API/Worker 使用同一 `LITEEVAL_DB`（或相同 --db 路径），工件默认位于数据库同目录 artifacts，临时工作目录位于 workspaces。
- Worker 崩溃后重启，租约过期时自动接管未完成实验，保留 Run ID 和已完成结果。恢复会重新调用未提交结果的远端 Agent，Agent 需要幂等支持。
- 正常任务退出清理工作目录；强制崩溃可能留下旧 workspaces 和未引用工件。当前没有自动保留周期或垃圾回收；确认所有 Worker 停止后可清理临时 workspaces，工件应按保留策略处理。
- 管理员密钥从环境读取；项目密钥只存 SHA-256，可撤销。结构化 JSON 工件与 SDK 有凭据字段/已知环境密钥脱敏，但不是通用个人信息识别。原始 JSONL/patch 等字节工件保存原内容，访问受项目鉴权限制。

## 仍需完成的验收

真实 Agent/Judge 账户联调；目标平台 Codex、Docker 和沙箱验证；真实 Agent/LLM 的大批量性能与长期存储压力；异常断电恢复演练；Judge 人工校准；生产备份/监控/留存和网络策略。当前测试证明了本机运行机制，不代表这些外部验收已经通过。
