# 第一步 后端稳定与 Web 接口准备

本文保留 0.2.1 第一步的操作记录；当前 0.2.2 的 schema 为 4，新增能力与前端代理设置见 [当前接口约定](frontend-backend-contract.md)。账号与 API/Worker 启动方式保持兼容。

本轮版本为 0.2.1，数据库 schema 为 3，事件 schema 仍为 2。目标是让后续前端可以安全登录、查看进度与分页结果、维护用例并预览评分。Web 页面和完整 0.3.0 工作台属于下一步。

## 1 本轮操作与交付

| 顺序 | 操作 | 已完成内容 |
| --- | --- | --- |
| 1 | 稳定原有回归基线 | Codex 的受信任 Python 启动器跨平台执行；拆分大文件与符号链接测试；取消/超时连接中断不再产生无关堆栈 |
| 2 | 增量升级数据 | schema v1/v2 事务迁移到 v3；迁移失败回滚；保留 Case 修订、快照、Run、Score 与工件 |
| 3 | 建立浏览器身份 | Owner 初始化/轮换，密码哈希，HttpOnly/SameSite Cookie，Origin/CSRF 校验，退出撤销、过期和登录限速 |
| 4 | 增加页面查询 | 实验轻量进度与分页 Run；状态/质量筛选；默认不返回输出和完整工件；保持旧 results 接口 |
| 5 | 完善数据维护 | Dataset 改名/描述/归档；Case、Evaluator 归档与恢复；base_revision 并发冲突；CSV 导出及 BOM 导入 |
| 6 | 增加评分预览 | API 提交持久预览任务；Worker 评分、心跳、崩溃接管；独立 Judge 重试与原始回复；不修改正式 scores |
| 7 | 验证与交接 | 新增存储/HTTP/会话/迁移/预览测试、一键演示与 Windows/Linux CI 工作流 |

本轮保留现有 Python 标准库 HTTP 服务及独立 Worker，没有引入新的运行依赖。PRD 中 FastAPI 是后续 API 层演进选项，本轮先以兼容接口完成上述交付。现有 /v1、CLI、SDK、规则评分、快照与 Compare 的语义继续保留。

## 2 先体验本轮功能

在项目根目录的 PowerShell 中执行：

```powershell
Set-Location 'D:\Data\Projects\GoldTrace'
python --version
python -m unittest discover -s tests -v
python -m examples.platform smoke
python -m examples.platform readiness
```

Python 要求 3.11+，不需要额外 pip 安装。第一个 Demo 验证原来的新旧 Agent Compare；第二个 Demo 验证登录/CSRF、实验进度、分页、评分预览、409 编辑冲突、归档后历史重跑和退出。均仅调用本地示例服务，不使用真实模型额度。

第二个 Demo 使用临时数据库和随机测试凭据，结束后关闭服务并清理临时目录，不设置日常 Owner 账号。结果保存在 `data/readiness-report.json`；原 Demo 报告仍在 `data/demo/report.json`。

Windows 没有符号链接权限时，该项测试明确标为 skipped；大文件上限测试仍独立执行。Linux CI 必须运行符号链接防护测试。CI 文件已加入仓库，本地验证不代表远端 CI 已执行。

## 3 升级已有数据

如果已有日常数据，先正常停止旧 API 和所有 Worker，并备份整个数据库目录（包含数据库、WAL/SHM 和 artifacts）。默认目录是 `data`；如果自定义了数据库或 artifact_root，必须同时备份这些实际路径。

以下只适用于已经停止所有进程、使用默认数据目录的情况；备份目标在仓库外，每次使用新目录：

```powershell
$backupTarget = 'D:\Data\Projects\GoldTrace-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
Copy-Item -LiteralPath 'D:\Data\Projects\GoldTrace\data' -Destination $backupTarget -Recurse
```

首次通过 0.2.1 的 Store/API/Worker/owner-init 打开数据库时会自动迁移到 schema 3。旧 0.2.0 程序不能混跑 schema 3。迁移失败会回滚数据库结构与版本；回退程序前需停止进程，并恢复匹配的升级前数据库和工件备份。

## 4 初始化 Owner 并启动服务

终端 A 初始化 Owner，密码交互输入，不放到命令行历史：

```powershell
python -m liteeval.interfaces.cli.main owner-init --username owner
python -m liteeval.interfaces.cli.main api
```

密码至少 12 个字符。再次运行 owner-init 会更新账号密码并撤销所有旧浏览器会话。Owner 已配置时，可以不设置管理员 API Token；如果保留 `LITEEVAL_ADMIN_TOKEN`，其仍须为至少 24 个字符的随机密钥，现有管理员脚本可继续使用。

终端 B 使用相同数据库启动 Worker：

```powershell
Set-Location 'D:\Data\Projects\GoldTrace'
python -m liteeval.interfaces.cli.main worker
```

自定义数据库时，所有命令均在子命令之前传同一个 `--db`：

```powershell
python -m liteeval.interfaces.cli.main --db 'D:\EvalData\liteeval.db' owner-init
python -m liteeval.interfaces.cli.main --db 'D:\EvalData\liteeval.db' api
python -m liteeval.interfaces.cli.main --db 'D:\EvalData\liteeval.db' worker
```

健康检查返回软件 0.2.1、schema 3：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8765/v1/health'
```

当前服务提供 API，浏览器登录页面由下一步前端提供。以下 PowerShell 请求可直接演示现有登录和维护接口。

## 5 登录与浏览器会话

在终端 C 中输入刚配置的账号密码：

```powershell
$base = 'http://127.0.0.1:8765'
$credential = Get-Credential -UserName 'owner' -Message 'LiteEval Owner 登录'
$loginBody = @{
    username = $credential.UserName
    password = $credential.GetNetworkCredential().Password
} | ConvertTo-Json
$login = Invoke-RestMethod -Method Post -Uri "$base/v1/auth/session" `
    -ContentType 'application/json' -Body $loginBody `
    -Headers @{ Origin = $base } -SessionVariable evalSession
$loginBody = $null
$credential = $null
$browserHeaders = @{ Origin = $base; 'X-CSRF-Token' = $login.csrf_token }
Invoke-RestMethod -Uri "$base/v1/auth/me" -WebSession $evalSession
```

会话使用 HttpOnly、SameSite=Strict Cookie；数据库只存会话 Token 哈希。空闲 8 小时或最长 7 天后失效。写请求必须同时发送 Cookie、匹配的 Origin 与 X-CSRF-Token。重新打开前端时可以通过 auth/me 获取当前 CSRF Token。

同一来源 10 分钟内连续 5 次登录失败后，后续登录返回 429，直到窗口结束。登录接口也要求匹配 Origin。项目 API Key 和管理员 Bearer 请求无需 CSRF，仍按原来的作用域鉴权；无效 Bearer 不会退回使用 Cookie 权限。

部署到 HTTPS 反向代理时，在 API 环境中配置公开来源，不能只信任请求中的 X-Forwarded-*：

```powershell
$env:LITEEVAL_PUBLIC_ORIGIN = 'https://eval.example.com'
$env:LITEEVAL_COOKIE_SECURE = '1'
python -m liteeval.interfaces.cli.main api
```

以上域名是待替换示例，平台不会自动部署或申请证书。本机 HTTP 体验时不设置 PUBLIC_ORIGIN，COOKIE_SECURE 保持 0。API 仍绑定 loopback；公开来源不改变监听地址。

## 6 维护数据与处理编辑冲突

创建项目、Dataset 和一条 Case：

```powershell
$projectBody = @{ name = '个人 Agent 评测' } | ConvertTo-Json
$project = Invoke-RestMethod -Method Post -Uri "$base/v1/projects" -WebSession $evalSession `
    -Headers $browserHeaders -ContentType 'application/json' -Body ([Text.Encoding]::UTF8.GetBytes($projectBody))
$datasetBody = @{ project_id = $project.id; name = '业务回归集' } | ConvertTo-Json
$dataset = Invoke-RestMethod -Method Post -Uri "$base/v1/datasets" -WebSession $evalSession `
    -Headers $browserHeaders -ContentType 'application/json' -Body ([Text.Encoding]::UTF8.GetBytes($datasetBody))
$caseBody = @{ input = @{ question = '如何退款' }; expected = '请在订单页申请退款'; metadata = @{ critical = $true } } | ConvertTo-Json -Depth 10
$case = Invoke-RestMethod -Method Post -Uri "$base/v1/datasets/$($dataset.id)/cases" -WebSession $evalSession `
    -Headers $browserHeaders -ContentType 'application/json' -Body ([Text.Encoding]::UTF8.GetBytes($caseBody))
```

编辑时发送自己读取的 revision；重复用旧 revision 保存会返回 409，应重新读取并合并内容：

```powershell
$editBody = @{ input = $case.input; expected = '退款请在订单页申请'; metadata = $case.metadata; base_revision = $case.revision } | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method Post -Uri "$base/v1/cases/$($case.id)/revisions" -WebSession $evalSession `
    -Headers $browserHeaders -ContentType 'application/json' -Body ([Text.Encoding]::UTF8.GetBytes($editBody))
```

旧客户端未传 base_revision 时仍可新增修订，HTTP Warning 会提示迁移；新前端必须携带。Case 的评测字段随修订冻结，后续修改不改变旧实验。

归档 Dataset；同样的 PATCH 可用于 `/v1/cases/{id}` 和 `/v1/evaluators/{id}`：

```powershell
Invoke-RestMethod -Method Patch -Uri "$base/v1/datasets/$($dataset.id)" -WebSession $evalSession `
    -Headers $browserHeaders -ContentType 'application/json' -Body '{"archived":true}'
Invoke-RestMethod -Uri "$base/v1/datasets?archived=only" -WebSession $evalSession
Invoke-RestMethod -Method Patch -Uri "$base/v1/datasets/$($dataset.id)" -WebSession $evalSession `
    -Headers $browserHeaders -ContentType 'application/json' -Body '{"archived":false}'
```

默认列表和导出只含未归档数据，`archived=include` 返回全部，`archived=only` 返回归档项。归档 Case 不进入新快照；归档 Dataset/Evaluator 不能用于新的实验提交，但历史结果和原快照 rerun 仍可使用。项目密钥可维护本项目 Dataset/Case，评分器归档仍要求管理员权限。

## 7 查看实验进度和分页结果

通过原来的实验创建接口得到 experiment_id 后，使用以下接口。`$experimentId` 为实际实验 ID：

```powershell
Invoke-RestMethod "$base/v1/experiments/$experimentId/progress" -WebSession $evalSession
Invoke-RestMethod "$base/v1/experiments/$experimentId/runs?limit=50&offset=0" -WebSession $evalSession
Invoke-RestMethod "$base/v1/experiments/$experimentId/runs?outcome=fail&limit=50" -WebSession $evalSession
Invoke-RestMethod "$base/v1/experiments/$experimentId/runs?status=failed&limit=50" -WebSession $evalSession
```

progress 返回冻结 Case 总数、终结数量、等待/执行/重试/成功/失败/超时/取消计数和 Worker 租约状态。terminal 包括失败与超时，不能理解成质量通过数。已取消的未执行 Case 计为取消；实验失败后尚未执行的 Case 仍显示 pending，客户端以实验终态停止轮询。

runs 返回冻结 Case 的分页结果，包含尚未启动的 Case、评分和 metadata。`outcome` 为 pass/fail/unscored/pending/cancelled，`status` 为执行状态；`include_output=true` 才附输出，完整 Trace/Attempt 仍使用原有 Run 详情接口。页面建议每 2 秒轮询 progress，实验终结后停止。

## 8 预览评分器

先创建一个评分器；预览只引用已经注册的不可变评分器配置：

```powershell
$evaluatorBody = @{ project_id = $project.id; name = '答案精确匹配'; kind = 'exact_match'; config = @{} } | ConvertTo-Json -Depth 10
$evaluator = Invoke-RestMethod -Method Post -Uri "$base/v1/evaluators" -WebSession $evalSession `
    -Headers $browserHeaders -ContentType 'application/json' -Body ([Text.Encoding]::UTF8.GetBytes($evaluatorBody))
$previewBody = @{
    project_id = $project.id
    evaluator_id = $evaluator.id
    case = @{ input = @{ question = '2+2' }; expected = '4' }
    run = @{ output = '4'; status = 'succeeded' }
} | ConvertTo-Json -Depth 10
$preview = Invoke-RestMethod -Method Post -Uri "$base/v1/evaluator-previews" -WebSession $evalSession `
    -Headers $browserHeaders -ContentType 'application/json' -Body $previewBody
Invoke-RestMethod "$base/v1/evaluator-previews/$($preview.id)" -WebSession $evalSession
```

返回 202/queued 后由终端 B 的 Worker 处理，再次 GET 即可查看 completed/failed 和 score。也可在独立终端运行一次 `python -m liteeval.interfaces.cli.main worker --once`；已有实验优先，该命令每次只处理一个实验或预览。

预览历史真实 Run 时，只发送 project_id、evaluator_id、run_id；服务端固定使用该 Run 对应的 Case 修订和已留存证据，不读取后来编辑的 Case。合成样本标记 synthetic=true，不能冒充真实执行结果。两种模式不能混用。

LLM Judge 凭据只配置给 Worker，参照原运行指南。Judge 预览会真实调用模型并消耗额度，错误时最多额外重试一次；每次原始回复作为鉴权工件保留。进程崩溃接管可能再次调用 Judge，采用至少一次调用语义。预览只写独立结果，不覆盖任何正式 Score，也不调用业务 Agent。

## 9 退出与后续交接

```powershell
Invoke-RestMethod -Method Delete -Uri "$base/v1/auth/session" -WebSession $evalSession -Headers $browserHeaders
```

后续前端可以直接使用本轮新增接口。总览聚合、Trace 可视化、预检、命名版本、人工复核和 Gate 按 [PRD](product/liteeval-prd-v1.0.md) 分期实现。本轮实际验证结果见 [0.2.1 验证记录](verification-0.2.1.md)。

CI 采用 [checkout](https://github.com/actions/checkout) 与 [setup-python](https://github.com/actions/setup-python) 官方工作流组件；远端运行结果需在代码推送后查看，本轮未推送代码。
