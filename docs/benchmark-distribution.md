# 跨设备设计与发布 Benchmark

平台和题包可以保存在不同仓库、不同设备。设计设备维护题目、场景控制器和评分材料，评测设备运行 LiteEval、保存模型连接与凭据，接收指定版本的包并执行。当前通过平台设备的 CLI 完成导入和执行。

## 文件与职责

| 位置 | 内容 |
| --- | --- |
| 平台仓库与运行设备 | LiteEval 代码、数据库、凭据、运行环境、事件与产物存储 |
| 独立 Benchmark 仓库 | `dataset.toml`、`tasks/`、初始工作区、私有场景、Judge、可选 `workflow/` |
| 独立 Harness 包 | `harness.toml` 和适配代码，可随 Bench 一并交付，也可由评测设备维护 |

题包只使用包内相对路径。控制器依赖的文件放进自己的包，执行需要的 Python 包、CLI 和服务由评测设备或固定 digest 的 Docker 镜像提供。模型凭据由评测设备绑定。`localhost` 镜像仓库和设备绝对路径须按目标设备准备。

题包包含 Ground Truth 和评分程序时，仓库权限按题目发布范围设置。发布记录保存来源仓库、commit SHA、包版本、Release digest、Harness 修订和 Workflow 修订。当前运行计划会冻结这些已选修订，题目中的 Workflow ID 仍需由调用方绑定实际修订。更改题目、场景或 Rubric 时发布新版本，并分别标注流程验收、难度校准和保留验收用途。[出题规范](../AGENTS.md)

## 当前可执行交付流程

1. 在设计设备完成本地目录题包，提交到独立仓库。使用相同换行规则或按原始字节打包，记录 commit SHA 和版本号。
2. 在评测设备检出固定提交，或解压传输的归档。先用 `benchmark validate` 检查资源引用和结构。
3. 向已存在的 Project 导入题包，注册需要的 Agent 和 Workflow 包，保存返回的 ID。
4. 选择评测设备上的模型连接或配置 Harness 声明的环境变量，运行预检和评测。
5. 保存运行 ID、事件、评分和 ZIP，后续执行和导出使用平台已保存的资源快照。

下面用独立存放的金融包和参考策略演示。`PROJECT_ID` 是评测设备上已创建的项目，目录按实际交付位置填写。规则策略仅用于流程验收。

```powershell
$projectId = 'PROJECT_ID'
python -m liteeval.interfaces.cli.main benchmark validate D:\Benchmarks\financial_stress
$release = python -m liteeval.interfaces.cli.main benchmark import D:\Benchmarks\financial_stress --project $projectId | ConvertFrom-Json
$workflow = python -m liteeval.interfaces.cli.main harness D:\Benchmarks\financial_stress\workflow --project $projectId | ConvertFrom-Json
$agent = python -m liteeval.interfaces.cli.main harness D:\Harnesses\financial_reference --project $projectId | ConvertFrom-Json
python -m liteeval.interfaces.cli.main benchmark run $release.id --project $projectId --agent-harness $agent.harness_revision_id --workflow-harness $workflow.harness_revision_id --workflow-options '{"clock":"replay"}' --trusted-local
```

普通任务使用对应的 Agent Harness；涉及 LLM 时绑定 `--target-connection`，评分需要 LLM 时绑定 `--judge-connection`。Docker 环境由题包的环境 profile 声明，评测设备需有可用的 Docker 和对应镜像。[目录包操作](benchmark-packages.md) · [多轮操作](dialogue-benchmarks.md)

## 已验证范围与后续接入

2026-11-05 在当前设备模拟两端交付。平台从独立构建的 wheel 加载，金融题包与参考 Harness 经过 ZIP 传输和字节摘要核对，导入和注册后删除接收源目录。平台使用保存的快照完成三道规则任务，3/3 通过并导出 ZIP。该验证覆盖文件位置、平台安装包和题包资源的分离。真实模型跨设备调用及目标设备的 Docker 环境按各自条件验收。

当前缺少通用 Git URL 导入、网页上传发布、Native Web 运行提交和跨设备 Job API。可以先由设计设备提交版本，再在平台设备手动拉取固定 SHA 并调用 CLI。后续可在这条已验证流程外增加导入任务，记录仓库、commit、路径与 digest，再补 Web 触发与后台执行。Git 更新后创建新的 Release，历史运行继续引用原快照。
