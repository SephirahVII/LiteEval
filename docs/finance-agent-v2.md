# Finance Agent v2 接入与真实试跑

本文保留 0.3.1 的历史试跑记录。0.4.1 的原生目录包、逐项评分与研究 Harness 就绪要求见 [新接入指南](fab-v2-native.md)；本篇旧记录不是新目录包的执行验收。

日期：2026-11-01。后端版本 0.3.1，数据库 schema 5。此次验证的是公开题库导入、固定版本、真实模型执行、诊断评分、轨迹/用量采集和导出。

## 来源与版本

来源：[Vals 官方 v2 说明](https://www.vals.ai/benchmarks/fabv2)、[官方仓库](https://github.com/vals-ai/finance-agent-v2)。v2 官方页面当前标记已归档，由 v2.1 取代；本项目按指定的 v2 仓库固定来源，不自动替换版本。

- 上游 commit：`03c7c432b645f42f81ef3c13957100b182217d7c`。
- 上游路径：`data/public.csv`，27 道公开题，9 类各 3 题。
- 原始文件 SHA-256：`27b48c08a6099bc076b4194cac7cefe295082b9aedcbc67f4fedfa70468b427e`。
- MIT 许可与版权声明随文件保留于 `benchmarks/finance_agent_v2/LICENSE`。
- CSV 与来源清单随代码保存；导入时先验哈希。Case metadata 保留来源 commit、路径、行号、问题哈希、分类、预计人工时间和稳定 external_case_id。
- 原始 rubric、severity、must_pass 保存在 Case.expected；Target 只收到问题和研究指令，不收到 rubric/标准答案。
- 完整 27 题和试跑子集各自发布不可变 Dataset Version，后续草稿修改不会改变已经发布的版本。重复准备会复用原固定版本，不会选择用户后来新建的草稿版本。

## 本次实际执行

使用本机已登录的 Codex CLI 0.158.0，Target 与独立 Judge 都显式选择 `gpt-6-sol`。Target reasoning effort 为 medium，Judge 为 low。每题 Target 超时 240 秒，无自动重试，串行执行；Judge 使用单独上下文，只评判提供的答案和 rubric，不执行研究工具。

本机原环境代理已失效。实际运行仅为这个进程配置 `http://127.0.0.1:8890`；没有修改系统代理、登录凭据或全局 Codex 配置。初次连通性测试中，直连超时，用户配置里的 gpt-6.1-sol 也被当前 CLI 账户拒绝；后续按本机模型列表选择 gpt-6-sol，并成功完成真实调用。

| 公开行号 | 任务 | Agent 执行 | Target 总耗时 | 本地 rubric 检查 | 本地加权分数 |
| --- | --- | --- | --- | --- | --- |
| 4 | CRWD 与 PANW FY2023–FY2025 收入 CAGR 比较 | succeeded | 68.562 秒 | 4/4；must-pass 通过 | 1.00 |
| 3 | EA 与 TTWO 竞争对手披露比较 | succeeded | 89.969 秒 | 5/7；must-pass 通过 | 0.75 |

本地诊断平均加权分数 0.875，All-Pass 为 1/2，执行成功为 2/2。耗时包含 CLI 启动、检索和执行收尾，不包括 Judge。它不是纯模型生成耗时。

定性题未通过的两项：答案没有明确说明大型竞争对手可以比 EA 投入更多资源；也没有明确表达 TTWO 面对已建立和新兴互动娱乐公司的竞争。原题没有指定财年，本次 Agent 引用了 FY2026 的披露，因此与公开 rubric 的时间基准也需要人工核对。这里保留真实判定，不通过补答案或调整 rubric 改分。

## 评分方法与可比性

本地 Judge 对每条 rubric 返回 index、passed、reason，程序验证条目齐全且不重复，再确定性计算 severity 加权比例；任一 must_pass 失败，整题部分分数归零；全部检查通过才算 All-Pass。

官方方法使用三模型评审、原版金融研究工具 harness、独立测试集和多次运行。本次使用当前项目的 Codex research Agent、公开的两道样题、同模型独立上下文的单次诊断评分，未部署原版六工具 harness，也未做三次重复。因此分数只验证本地链路和检查差异，不能作为正式 FAB 成绩、模型排名或泛化能力结论。依据见 [官方评分方法](https://www.vals.ai/benchmarks/fabv2)。

## 实际采集的内容

- 2 条 Run、2 次 Target 调用、2 次 Judge 调用，无重试。
- 7 次公开 web_search 工具事件；29 条本地日志记录。
- 10 个工件，145,594 字节：Agent 原始响应、Codex JSONL、trajectory、最终状态快照和 Judge 原始结果。
- Judge 工件包含逐条判定、权重汇总、must-pass gate、All-Pass，以及 Judge 的原始 CLI rollout。
- 事件记录是 CLI 对外输出的事件。网页正文与所有内部调用没有完整归档，不能视为完整浏览器录制。

| 角色 | 输入 token | 输出 token | 合计 | 缓存读取 | reasoning token |
| --- | ---: | ---: | ---: | ---: | ---: |
| Target | 368,215 | 3,337 | 371,552 | 302,464 | 650 |
| Judge | 32,187 | 326 | 32,513 | 14,336 | 0 |
| 总计 | 400,402 | 3,663 | 404,065 | 316,800 | 650 |

计数取自 CLI 的 turn.completed；缓存与 reasoning 是子项，不再叠加到合计。此处包含多步研究中重复发送的上下文，不等于最终答案长度。CLI 没有返回费用，费用保持未知。此次补充识别实际 CLI 的 cache_write_input_tokens、reasoning_output_tokens 字段；都已保留原始 JSONL。

## 本机沙箱限制

尽管启动参数为 workspace-write，两个 Target 均报告 response.md 写入被只读环境拒绝；最终文件快照为空。此次完成了网络研究与答案返回，没有通过 Agent 自行落盘文件的验收。导出的 answer-*.md 是平台从最终输出生成的文件，不能当作 Agent 自行创建文件的证据。

没有关闭沙箱、使用全访问模式或修改系统权限来绕过这个限制。需要进一步核对本机 Windows sandbox 的有效权限，再单独验证文件写入、命令执行和计算器工具。本次结果仍保留该限制原文，避免把“模型回答成功”误报为“全部沙箱能力通过”。[OpenAI Docs 的 Windows sandbox 说明](https://developers.openai.com/codex/windows)解释了配置实现和权限边界。

## 数据位置

本次使用独立的 `data/finance-agent-v2/liteeval.db`，保留在本机。原始结果不提交 Git。

- Project：`4a42e54ac9184920a44b886460b2d02d`。
- 完整 Dataset：`a5c36207731c4d88b47ba64044677afc`；Version：`6e1f6a9ed6ec4fc08f78ace27c6043f3`。
- 试跑 Dataset：`fd00234ab80b4fcabb5707269bb66b13`；Version：`a1327d14d4434334bb15756a66ecbc7d`。
- Experiment：`ec359146a64c4606bf163ccc3f124bc9`。
- 汇总：`data/finance-agent-v2/results/benchmark.json`。
- 四种导出：`data/finance-agent-v2/results/rollout-ec359146a64c4606bf163ccc3f124bc9.{json,jsonl,csv,zip}`。
- 两份答案：同目录的 `answer-*.md`。

ZIP 包含工件清单和 SHA-256；每次导出读取工件时校验。工作目录执行结束后销毁，已保存内容仍可查询和再次导出。

## 复现

只准备题库，不调用模型：

```powershell
python -m pip install -e .
python examples/run_finance_benchmark.py --rows 3,4
```

默认导入完整 27 题，并建立选定行号的子集。重复准备不覆盖已经保存的 benchmark.json 实验报告；准备信息单独写 catalog.json。

真实调用本机已登录 CLI：

```powershell
python examples/run_finance_benchmark.py --run --rows 3,4 --model gpt-6-sol --proxy http://127.0.0.1:8890
```

代理端口和模型应按你自己的环境调整；没有代理需求时省略 --proxy，省略 --model 则使用 CLI 默认模型。脚本不会自动降级到其他模型或改成模拟答案。--run 会真实调用账户模型。再次执行生成新实验，不覆盖已导出的其他实验文件。

也可以传 --connection-id 和 --judge-connection-id 使用该数据库中本项目已保存的连接。使用 API Key 连接时需按连接指南配置相同的 LITEEVAL_SECRET_KEY。--no-judge 仅检查 response.md 是否存在，不评价答案质量，适用于支持工作目录写入的 Target；当前这台机器该检查预期不能通过。

默认本地 Judge 是仅在脚本运行期间存在的 HTTP 服务，服务关闭后连接归档。因此不要直接重跑旧实验依赖的临时 Judge 地址；重新执行脚本会创建新服务和实验。持久部署应传入真实的 Judge connection。

通过现有 API 查看这些数据：

```powershell
python -m liteeval.interfaces.cli.main --db data/finance-agent-v2/liteeval.db owner-init
python -m liteeval.interfaces.cli.main --db data/finance-agent-v2/liteeval.db api
```

Owner 登录后可访问原有 dataset/version、experiment/results、run、statistics、events 和 export 接口。这里是独立数据库，不会自动出现在其他 --db 指向的服务中。

## 回归验证

本地 `python -m unittest discover -s tests -v`：94 项，93 项通过、0 失败、0 错误，1 项因 Windows 符号链接权限跳过，约 68.31 秒。日志位于 `data/finance-agent-v2/backend-0.3.1-tests.txt`。

新增 6 项覆盖原始文件哈希、9 类公开题、评分数据与 Target 输入分离、固定版本复用/草稿变更、非法选题、severity/must-pass 汇总、缺失或重复判定、CLI 代理/环境变量边界、真实 CLI token 别名与可恢复错误解析。另已实际重复执行准备命令，确认复用数据集且保留已有实验报告。

CI 会运行后端回归和原有 Demo，并执行金融题库准备命令；CI 不调用真实模型。真实模型记录是上面的本机两题实验，不会把测试替身结果混入其中。
