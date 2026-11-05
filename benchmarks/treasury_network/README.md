# Treasury network stress challenge

金融压力流程验收题之后新增的题源，当前生成版本为0.8.0。同版各三次实测，Flash均分100，M3均分61.39，尚未达到30–40/100目标；本版保留作运营能力基线。
旧 `financial_stress` 的题包和原始成绩保留。这里的八级是同一工作流家族的八次设计迭代，不是八个独立业务场景。

## 生成与执行

```powershell
python -m benchmarks.treasury_network.build data/my-treasury-package --level 8
python -m examples.treasury_network data/my-treasury-package --db data/liteeval.db --project PROJECT_ID --target-connection CONNECTION_ID --parameters '{"max_tokens":8192,"reasoning_split":true}'
```

第二条使用MiniMax的思考内容分离参数。其他供应商按自身接口设置 `--parameters`，默认仅 `max_tokens=8192`。
模型连接由平台加密保存并绑定；API Key不写入题包、命令行或执行ZIP。

生成器拒绝覆盖目录。输出为标准原生包：

```text
dataset.toml
workflow/{harness.toml, controller.py, engine.py, resources.py}
tasks/treasury-duty/
  task.toml
  prompt.md
  environment/initial_workspace/{desk,bank,funding,collateral,operations,risk,news,archive}/
  workflow-fixtures/scenario.json
  judge/{ground_truth.json,verify.py}
freeze.json
```

`freeze.json`记录文件SHA256；发布另有release digest、Workflow及Agent修订。作者解法在题源的 `reference.py`，不进入模型工作区。
被测instruction只有岗位、目标、权限、交付物和资料入口。工具描述只说明参数和操作语义。未来材料发布前不能读取或搜索。

## 迭代范围

| 级别 | 新增或调整的业务内容 |
| --- | --- |
| 1 | 多实体、多币种、受限现金、三笔付款、融资到期与抵押品释放 |
| 2 | 正式通知改版和旧估计迟到；修正首轮repo报价歧义 |
| 3 | FX匹配失败与更自然的市场旁置信息；金额、时点同时提供可读值 |
| 4 | 银行未分配来款；删除交接存在性分，净缓冲扣除全部到期未付负债 |
| 5 | SSI改版、现金抵押品维护义务、独立Dealer B交易窗口 |
| 6 | 融资共享总限额、两窗口共用MMF持仓、正式通知晚于部分融资截止 |
| 7 | 逐lot选择抵押品，抵押与出售共用持仓；联合资金可行性评分，消极不融资不获免费合规分；正式通知09:12 |
| 8 | 银行10:35指令批次早于融资到期；偿还全额本金在到期日检查现金，支持对尚未到账现金提前排队；补全相关接口语义 |

0.7.1另修正契约口径、未触发义务的计分和迟到分配来款后的到期事件调度。0.7.0及更早快照、分数保持不变。

## 当前评分

| 项目 | 权重 | 证据 |
| --- | ---: | --- |
| 联合资金组合 | 40 | 已接受的资金安排能否同时满足融资总限额、M1及现金抵押品支付后的GBP缓冲、USD义务保障；三项取最低连续分 |
| 到期付款 | 25 | 已发布义务的可信交收记录；C1仅在实际触发时计入分母 |
| 实际净缓冲 | 15 | 两次GBP付款节点现金扣除全部仍欠的到期GBP义务，线性部分分 |
| 当日本金偿还 | 20 | 按融资本金金额加权的准时偿还比例 |

联合资金项审查资金安排，付款和缓冲项审查真实到账及执行。计划有资金而漏排付款、资金仍在suspense等情况可以分别定位。
无全题归零门槛。费用和交接继续导出，但不以简单确认、文本格式或存在性占据挑战分。
Judge逐笔复算现金、融资本金峰值和期末余额。权重、公式、权限及预算随题包冻结。

前五级最多160次动作，第六级起224次。读取/查询15个业务秒，报价/审批/提交等30秒；等待推进到下一事件或更早指定时间。
值班08:30–11:30，使用业务时间回放；模型真实API耗时另记，不据此声称真实紧急反应速度。每条平台执行限时2400秒。

## Harness与验证

`harnesses/json_action`保留完整模型消息及reasoning字段，工作区保存历史，轮次间传递SHA256句柄，避免累计输出触及8MB上限。
瞬时错误有限重试；错误用量保持未知。原始请求/响应、供应商token统计、重试和控制器流水进入平台证据。
这是固定JSON动作Agent基准，跨成熟第三方Agent/Harness的难度需要另行验证。

```powershell
python -m unittest tests.test_treasury_network tests.test_json_action_transport -q
python -m examples.treasury_network.summarize
```

作者路径验证各级及保留金额变体可完整成功；负例覆盖空跑0分、错币种/错实体/未交收资产、重复抵押与出售、未触发义务，以及迟到入账的时钟单调性。
真实校准和失败尝试均保存在本地 `data/treasury-calibration/`，脱敏账本和汇总见 [校准记录](../../docs/examples/treasury-network/README.md)。
最终验收使用同版、同Harness、同参数的重复和未参与改题的保留变体，不得合并不同版本的分数宣称达标。
本版校准已失败，未消耗保留变体选择低分。[下一轮作者草案](../../docs/product/financial-source-risk-case-design.md)尚未实现。
