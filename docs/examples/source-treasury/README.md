# Source treasury 真实校准记录

这是原始证据风险重建题的真实 API 运行记录。当前为校准候选，尚未声明达到30–40分难度目标。
调用用户已有的 MiniMax API 连接；未使用 Codex 登录。一个场景家族，不代表所有强模型或成熟 Agent 的能力。
业务时间采用回放，真实调用耗时单独记录；每题最多224次动作、2400秒墙钟上限。
各次分数采用当时冻结的 rubric；不同题版、参数、Harness或校准/保留阶段分别统计。
0.1.0为初始原始资料题；0.1.1修正零额CSA省略和现金as-of；0.2.0增加平静与已对冲对照；0.3.0提供计算器并改为整本风险台账共享GBP5m误差预算。历史分数不重写。
业务错误、台账记录错误及已识别的评分/元数据局限见 [轨迹分析](ANALYSIS.md)。

| 题版/情景 | 模型 | 重复次数 | 原始分数 /100 | 均分 | 样本标准差 |
| --- | --- | ---: | --- | ---: | ---: |
| 0.3.0/stress | MiniMax-M3.1-Flash | 3 | 60.00, 58.48, 80.80 | 66.43 | 12.47 |
| 0.3.0/stress | MiniMax-M3 | 3 | 100.00, 100.00, 100.00 | 100.00 | 0.00 |

相同压力题包的重复试验、保留金额变体和对照分别报告；对照不能用来补挑低分。详细分析见 [设计与分析](../../product/financial-source-risk-case-design.md)。

| 运行 | 题版 | 阶段/情景 | 模型 | 分数 /100 | 动作 | API用量记录 | 实际秒数 | 轨迹 |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| source-01-flash | 0.1.0 | calibration/stress | MiniMax-M3.1-Flash | 41.29 | 174 | 176 | 563.09 | [账本](traces/source-01-flash.json) |
| source-01-m3 | 0.1.0 | calibration/stress | MiniMax-M3 | 58.31 | 145 | 147 | 805.05 | [账本](traces/source-01-m3.json) |
| source-011-flash-1 | 0.1.1 | calibration/stress | MiniMax-M3.1-Flash | 48.95 | 138 | 138 | 790.53 | [账本](traces/source-011-flash-1.json) |
| source-02-flash-1 | 0.2.0 | calibration/stress | MiniMax-M3.1-Flash | 59.90 | 126 | 127 | 476.45 | [账本](traces/source-02-flash-1.json) |
| source-02-m3-1 | 0.2.0 | calibration/stress | MiniMax-M3 | 38.05 | 117 | 119 | 498.61 | [账本](traces/source-02-m3-1.json) |
| source-03-flash-1 | 0.3.0 | calibration/stress | MiniMax-M3.1-Flash | 60.00 | 123 | 125 | 382.94 | [账本](traces/source-03-flash-1.json) |
| source-03-flash-2 | 0.3.0 | calibration/stress | MiniMax-M3.1-Flash | 58.48 | 145 | 160 | 828.23 | [账本](traces/source-03-flash-2.json) |
| source-03-flash-3 | 0.3.0 | calibration/stress | MiniMax-M3.1-Flash | 80.80 | 134 | 139 | 388.78 | [账本](traces/source-03-flash-3.json) |
| source-03-flash-hedged | 0.3.0-hedged | calibration/hedged | MiniMax-M3.1-Flash | 92.00 | 100 | 103 | 246.94 | [账本](traces/source-03-flash-hedged.json) |
| source-03-flash-quiet | 0.3.0-quiet | calibration/quiet | MiniMax-M3.1-Flash | 100.00 | 103 | 117 | 576.09 | [账本](traces/source-03-flash-quiet.json) |
| source-03-flash-retained | 0.3.0-retained | retained/stress | MiniMax-M3.1-Flash | 29.25 | 126 | 128 | 396.84 | [账本](traces/source-03-flash-retained.json) |
| source-03-m3-1 | 0.3.0 | calibration/stress | MiniMax-M3 | 100.00 | 139 | 141 | 393.69 | [账本](traces/source-03-m3-1.json) |
| source-03-m3-2 | 0.3.0 | calibration/stress | MiniMax-M3 | 100.00 | 134 | 149 | 664.58 | [账本](traces/source-03-m3-2.json) |
| source-03-m3-3 | 0.3.0 | calibration/stress | MiniMax-M3 | 100.00 | 118 | 123 | 316.56 | [账本](traces/source-03-m3-3.json) |
| source-03-m3-hedged | 0.3.0-hedged | calibration/hedged | MiniMax-M3 | 100.00 | 113 | 117 | 334.94 | [账本](traces/source-03-m3-hedged.json) |
| source-03-m3-quiet | 0.3.0-quiet | calibration/quiet | MiniMax-M3 | 80.00 | 106 | 119 | 484.23 | [账本](traces/source-03-m3-quiet.json) |
| source-03-m3-retained | 0.3.0-retained | retained/stress | MiniMax-M3 | 61.25 | 138 | 140 | 433.97 | [账本](traces/source-03-m3-retained.json) |

动作数包含被拒的工具请求；API用量记录还可能包含未完成请求和传输重试。
完整请求/响应、模型返回的 reasoning、工具回执、发布快照及其摘要在本地 `data/source-treasury-calibration/<运行>/execution.zip`。本目录仅发布合成业务账本和脱敏统计。
input_tokens 包含缓存命中部分；output_tokens 包含供应商报告的 reasoning 部分。两者不能再次相加计算总量。供应商未报告 API 费用时保持未知；账本里的 GBP fee 是仿真融资成本。
所有级别均由作者侧参考路径验证100分。当前均为一个场景家族；只有最终同题包、同Harness摘要、同参数的重复及未参与改题的情景验证，才能支持30–40分难度声明。

完整性、同版离线重判及证据包摘要见 [核验记录](audit.json)。
