# LiteEval 0.2.0 当前版本基线

核对日期：2026-11-01（Asia/Shanghai）。

- 上游仓库：https://github.com/SephirahVII/LiteEval
- 分支与提交：`main`，`cb262045d90d99047b7188cb01c49206c8d97977`。
- 提交日期：2026-10-10；版本号来自 `pyproject.toml`，为 `0.2.0`。
- 本地工作区：`D:\Data\Projects\GoldTrace`；代码已从上游克隆。
- 本文依据仓库源码、既有文档及本次本机复测，作为后续需求迭代的起点。建议路线尚不代表已确认的产品需求。

## 产品定位与用户流程

当前版本是本地优先的 Agent 回归评测后端 MVP。它使用 Python 3.11+ 标准库、SQLite 和本地工件目录，提供 HTTP API、CLI 和 Python Trace SDK，尚无 Web 前端。

已有业务闭环：

```text
业务 Agent 上报 Trace
  → 将生产 Trace 转为 Case，或直接导入 Case
  → 创建实验并冻结用例修订、Agent 配置、评分器修订
  → Worker 执行 Agent 并评分
  → 汇总质量、执行、耗时和成本
  → 使用原实验快照运行新版 Agent
  → 逐 Case 比较改善、回退与未评分项
```

例如，个人业务 Agent 修改提示词或工具实现后，可以用相同用例和相同评分规则运行新旧版本，检查哪些用例改善、哪些用例回退，并回查输出、轨迹和文件证据。

## 已实现的能力

| 领域 | 当前实现 | 主要实现位置 |
| --- | --- | --- |
| 项目与访问控制 | 项目隔离；管理员令牌和项目令牌；项目令牌哈希存储、撤销；工件按项目鉴权 | `api.py`、`security.py`、`storage.py` |
| Trace 采集 | 输入、输出、事件、状态和元数据；事件 v2 的父子关系、顺序、时间及内容片段；生产 Trace 转 Case | `core.py`、`storage.py` |
| Python SDK | 同步/异步函数装饰器、显式嵌套 span；可关闭内容记录；默认上报失败保留业务结果 | `sdk.py` |
| 数据集与用例 | Case 修订；JSON/CSV 批量原子导入；JSON 导出；分页和部分字段筛选 | `storage.py`、`api.py` |
| 实验与版本 | 不可变用例快照；绑定 Agent 配置和评分器修订；提交幂等；基于原快照 rerun | `storage.py` |
| HTTP Agent | JSON POST；请求模板和响应路径映射；环境变量凭据；读取轨迹、usage、成本；稳定 Run 幂等键 | `adapters.py`、`transport.py` |
| Codex Adapter | 子进程执行、JSONL 事件归一化；命令、消息、MCP 工具、文件操作；原始输出和未知事件保留 | `codex.py`、`process.py` |
| 执行环境 | 每次 Attempt 独立工作目录；种子文件；最终文件快照、patch 和验证命令；Docker 环境及资源约束接口 | `environments.py`、`harness.py` |
| 执行可靠性 | Case 并发 1–32；Agent/Judge 分开重试；取消；租约、心跳和崩溃接管；拒绝旧 Worker 提交；Attempt 历史 | `runner.py`、`storage.py` |
| 指标和对比 | 质量通过率、执行成功率、评分覆盖率、指标分数、平均耗时、token、分币种成本；Case 级版本对比 | `reports.py` |
| 部署和升级 | API/Worker 独立进程；SQLite WAL；v1→v2 增量迁移；Dockerfile、Linux Compose 示例和备份运行文档 | `cli.py`、`migrations.py`、`docs/runbook.md` |

当前内置 12 类评分器：

| 类型 | 评分对象 |
| --- | --- |
| `exact_match`、`contains`、`regex` | 输出的精确匹配、包含关系和正则匹配 |
| `json_schema` | JSON 结构，仅支持文档列明的 Schema 子集 |
| `tool_called`、`tool_parameter` | 工具调用及预期参数顶层键子集匹配 |
| `latency`、`cost` | 耗时上限和同币种成本上限 |
| `file_exists`、`file_content`、`test_passed` | 执行结束后的文件、文本内容和独立验证命令结果 |
| `llm_judge` | OpenAI 兼容 Chat Completions 服务；二值或 0–1 连续评分、阈值、理由和可选轨迹 |

## 使用时必须保留的结果语义

- 实验 `completed` 表示处理完成，不表示用例全部通过。
- `success_rate` 是全部评分器通过的 Case 数除以快照 Case 总数；`execution_success_rate` 是执行成功率；`scoring_coverage` 单独反映有效评分覆盖率。
- 缺少必要证据为 `abstain`，评分器或 Judge 出错为 `error`，不能作为质量通过。评分器结果包括 `pass/fail/abstain/error`。
- Compare 要求两边实验完成、Case 快照相同、评分器修订集合相同。任一指标回退即该 Case 回退；证据不足不能报告为改善。连续分数变化会保留，但未跨阈值时 Case 分类仍可能为不变。
- 已冻结的是配置和用例内容；远端 Agent 代码及模型服务仍可能变化，需要业务部署版本或固定镜像配合复现。
- HTTP 重试和崩溃恢复可能再次调用远端 Agent，外部服务需要识别稳定的 Run 幂等键以避免重复副作用。

## 2026-11-01 本机复测

环境：Windows、Python 3.12.4。未修改业务代码或测试代码。

### API Demo

命令：`python -m examples.platform smoke`

执行成功。通过真实本地 HTTP 请求完成项目、Trace、Case、批量导入、两个 Agent 版本、实验执行和 Compare。

| 项目 | 结果 |
| --- | --- |
| 用例数量 | 2 |
| v1 质量通过率 | 0.5 |
| v2 质量通过率 | 1.0 |
| Case 比较 | improved=1、same=1、regressed=0、unscored=0 |
| 报告 | `data/demo/report.json`，该目录按现有规则不纳入 Git |

这是示例数据验证，不代表真实业务质量。

### 自动化测试

命令：`python -m unittest discover -s tests -v`

28 项测试执行 47.013 秒：26 项通过，1 项失败，1 项错误，命令退出码为 1。

| 未通过项 | 本次证据与判断 |
| --- | --- |
| `test_codex_adapter_fixture_end_to_end_final_state` | 测试直接执行带 POSIX shebang 的无扩展名 `fake-codex` 脚本，Windows 无法将其作为 Win32 程序启动。单项诊断确认 Run 错误为 `OSError: [WinError 193]`。这是测试替身的跨平台兼容问题，不能据此断言真实 Codex CLI 已通过或不可用。 |
| `test_snapshot_refuses_symlink_parent_and_large_files` | 创建符号链接时报 `OSError: [WinError 1314]`，当前进程缺少相应权限；测试未到达目标断言。本次未完成这项防护验收。 |

超时/取消相关测试期间，模拟 HTTP 服务还输出了 `ConnectionAbortedError: [WinError 10053]`，相关测试自身通过。

仓库 `docs/verification.md` 记录了 2026-10-10 在 Python 3.12.14 下 28 项全通过，以及一次 1000 Case 模拟负载检查。本次结果必须与历史记录分开理解；本次未重跑千条容量检查。

## 已有代码但仍需真实环境验收的能力

- **真实 HTTP Agent**：本次 Demo 使用本地示例 Agent；自己的业务 endpoint、认证、响应映射、副作用幂等仍需接入验证。
- **真实 LLM Judge**：仓库测试覆盖模拟协议、解析、错误重试及留档；本次未调用供应商模型。正式使用需要真实服务联调与人工标注集校准。
- **真实 Codex CLI**：JSONL 解析及 Adapter 已有实现；仓库原验收使用替身，本次替身在 Windows 启动失败。本次未发起真实 Codex 任务。
- **Docker**：已有环境实现和命令约束测试；本次未 build/run 容器。Compose 示例面向 Linux host networking，且不包含完整 Codex/Docker 任务 Worker 依赖。
- **生产运行**：长期压力、断电恢复、备份还原、监控告警、TLS/限速和存储留存策略仍需目标部署环境验收。

## 正式版本的主要缺口

1. **用户界面**：没有可视化项目、数据集、用例、实验、轨迹、工件和版本比较页面。
2. **评测工作流**：尚无人工标注/复核工作台、Judge 校准流程、通用框架自动埋点或更丰富的报告分析。
3. **真实接入与平台适配**：真实 Agent/Judge/Codex/Docker 需要验收；Windows 测试存在已复现问题。
4. **日常运行保障**：无自动工件保留周期或垃圾回收；生产监控、备份恢复和长期可靠性流程待建立。
5. **高级能力**：无统计显著性计算、自动轨迹差异分析和自动发布门禁；媒体内容只有契约/引用表达，尚无媒体专用评分器。
6. **规模与协作**：当前为单机 SQLite 设计，不能把网络共享盘作为多机队列；只有管理员/项目令牌，没有细粒度用户角色或审计日志。是否扩展由个人平台的实际使用需求决定。

## 后续迭代建议

| 顺序 | 目标 | 建议验收标准 |
| --- | --- | --- |
| 1 | 明确正式版首批场景并稳定当前基线 | 选定实际 Agent 与代表性用例；处理目标平台兼容问题；保留 Demo 和关键回归测试 |
| 2 | 完成可日常使用的 Web 工作台 | 用户可以导入/修订用例、配置评分、运行与取消实验、查阅失败证据、对比版本 |
| 3 | 用真实数据校准评测 | 实际 endpoint/凭据接入；人工标注集检验 Judge；结果和成本口径可解释 |
| 4 | 完成正式部署验收 | 按选定部署方式验证恢复、备份、监控、留存和运行权限；需要时验收真实 Codex/Docker |
| 5 | 按需求扩展 | 再决定定时回归、CI 门禁、统计分析、更多 Adapter、多用户或多机能力 |

后续以用户逐次提出的具体需求为准，在此基线之上实施、验证并更新相关文档。
