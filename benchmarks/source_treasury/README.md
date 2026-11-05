# Source treasury

这是在运营基线之后新增的原始证据评测题源，当前生成0.3.0。首批固定12次真实运行已完成，难度未达到30–40分目标，仍是校准候选。
模型不再直接得到内部可用现金或更新后的保证金预测，需要从公开银行记录、交易注册表、合成CSA协议和市场资料建立风险台账，再操作资金与抵押品。

```powershell
python -m benchmarks.source_treasury.build data/source-treasury-package
python -m examples.treasury_network data/source-treasury-package --db data/liteeval.db --project PROJECT_ID --target-connection CONNECTION_ID --parameters '{"max_tokens":8192,"reasoning_split":true}'
```

输出仍是原生 `dataset.toml + tasks/treasury-duty/`，包含初始工作区、私有fixture、独立Workflow和命令Judge。拒绝覆盖已冻结目录。
复用 `treasury_network` 已验证的交收、占用、共享额度和到期引擎；额外接口及数据属于这个题包，不放入平台核心。

只读SQL仅装载当前公开CSV，整数数字列有INTEGER类型，标识及文本保持TEXT。禁止写入、外部文件、ATTACH和扩展；结果最多100行并注明截断，查询计算量有上限。
`risk_report`只确认记录，不能充当判断答案是否正确的oracle。缺省的CSA键表示零追加VM，现金根据报告的业务as-of核对。
`calculate(expression)`提供有界十进制计算；允许数字、括号及四则运算，不允许代码、函数或文件访问。模型可以选择SQL、计算器或直接分析。

| 冻结维度 | 权重 |
| --- | ---: |
| 前置风险重建：各CSA追加VM及可用现金 | 40 |
| 联合资金可行性 | 30 |
| 实际付款 | 15 |
| 实际净缓冲 | 5 |
| 资金控制：压力题偿债；对照兼顾避免无谓融资 | 10 |

风险项为 `时间系数 × (0.8 × max(0, 1−整本CSA金额绝对误差之和/GBP5m) + 0.2 × 现金准确度)`。
整本台账共享一个业务现金缓冲，不能给每个CSA单独使用GBP5m误差额度。现金准确度也按GBP5m连续计分。
来源重建数据全部公开后、正式M1发布前获得完整时间分，发布后至到期前准确报告有一半时间分。引用来源单独导出，不作为整题格式门槛。
权重在真实执行前冻结。0.1.1修正零金额CSA省略及晚提交报告的现金as-of核算；0.2.0增加平静和已对冲对照；0.3.0提供计算器并采用共享误差预算。所有旧版本的原始成绩保留。

作者侧独立Python重建与完整资金策略已验证压力、平静、已对冲三种情景及各自保留金额变体均可获得100分。模型重复试验安排见 [固定协议](CALIBRATION.md)。
平静情景市场因子不变；已对冲情景与压力题收到相同市场变化，但组合增加抵销头寸。两类对照分别检查消息与自身敞口是否被正确结合，不与压力成绩混算。

```powershell
python -m benchmarks.source_treasury.build data/source-quiet --scenario quiet
python -m benchmarks.source_treasury.build data/source-hedged --scenario hedged
python -m benchmarks.source_treasury.build data/source-retained --variant retained
python -m examples.treasury_network.summarize --family source --input data/source-treasury-calibration --output docs/examples/source-treasury
```

真实统计和合成业务轨迹见 [运行报告](../../docs/examples/source-treasury/README.md)。完整模型请求/响应、工具回执和发布快照保存在本地导出ZIP；公开报告不收录API Key或原始reasoning。

```powershell
python -m unittest tests.test_source_treasury -v
```

协议、数据与时钟均为合成定义，不用于真实金融业务。真实API耗时和业务时钟分别记录。
