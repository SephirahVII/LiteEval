# Financial Stress Workflow v0.2.0

LDI 流动性值班的首个可执行题包。三个任务使用同一份 08:30 工作区和岗位指令，分别覆盖主回购拒单、主回购正常和平稳值班。所有金额、合同与交收均在仿真器内处理。

| Case | 情景 | 规则策略参考结果 |
| --- | --- | --- |
| `ldi-margin-001` | 09:12 回购被拒，须在备用授信截止前筹资 | 两笔付款准时，期末现金 £5m，费用 £91,700 |
| `ldi-margin-002` | 回购按时交收，同样有两笔追缴 | 两笔付款准时，期末现金 £5m，费用 £21,700 |
| `ldi-margin-003` | 平稳对照，仅 £10m 正常追缴 | 一笔付款准时，期末现金 £10m，费用 £0 |

从仓库根目录运行。

```powershell
python -m liteeval.interfaces.cli.main benchmark validate benchmarks/financial_stress
python -m examples.financial_stress
```

默认使用规则策略与受控业务时钟。程序发布题包、运行三个任务、调用独立命令 Judge，并将报告和 ZIP 写入 `data/financial-stress/`。规则策略的模拟业务延迟和实测响应时间分别记录。

使用已有的 LiteEval 模型连接时，将数据库、项目 ID、连接 ID 指向实际配置。

```powershell
python -m examples.financial_stress --mode model --db data/liteeval.db --project PROJECT_ID --target-connection CONNECTION_ID
python -m examples.financial_stress --mode live --db data/liteeval.db --project PROJECT_ID --target-connection CONNECTION_ID
```

`model` 让模型在固定决策预算内使用受控回放。模型每次返回一个 JSON 动作，通过工具读取期初文件、查询状态、等待、申请授权、融资、付款和提交交接。`live` 使用 6 倍业务时钟，每题约 25 分钟，三题顺序执行。两种模型模式使用项目内 OpenAI 兼容模型连接，启用后会产生模型请求。

示例分别注册题包的 `workflow/` 和 Agent Harness。手动运行时，以 `--workflow-harness` 选择控制器修订，以 `--agent-harness` 选择 Agent 修订。模型策略通过 `--target-connection` 绑定连接。业务时钟通过 `--workflow-options '{"clock":"live"}'` 选择。

| 模式 | Agent 包 | 控制器时钟 |
| --- | --- | --- |
| 规则回放 | `harnesses/financial_reference/` | `replay` |
| 模型回放 | `harnesses/json_action/` | `replay` |
| 模型限时 | `harnesses/json_action/` | `live` |

Agent 获取期初快照与分时事件。控制器在独立进程中读取 `workflow-fixtures/` 的场景，验证权限、截止、抵押品、额度与幂等指令，在付款时点按已到账现金结算。Agent 提交结构化交接动作后，控制器生成 `handover.md` 和 `evidence/financial_stress.json`，平台从控制器工作区采集文件。ZIP 保存已发布事件、读取时点、动作、回执及期末账本，重评复用相同证据。

独立 Judge 复算现金和费用。付款与授权是必过项，处置评分另计响应、纠偏、缓冲成本及交接。预测期发现与平稳误报作为独立指标记录。当前已验收三条 L0 情景的规则轨迹，并用替身模型验证了工具协议和限时截止。L1/L2、新闻和从通知开始的专项诊断保留在设计稿中。[设计稿](../../docs/product/financial-stress-benchmark-design.md)
