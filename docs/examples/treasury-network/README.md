# Treasury network 真实校准记录

模型通过用户已有的 MiniMax API 连接调用。业务时间回放与真实 API 耗时分开记录；前五级最多160次动作，第六级起224次。
表中分数均为原始冻结版本的分数，环境失败保持未评分；版本之间不合并计算难度均值。
第一轮存在 repo 报价歧义；第三轮初次运行遇到 TLS 中断，第一次重跑遇到重试事件协议错误；第四轮初次运行遇到429。原记录全部保留。
第4–5级修正净缓冲和交接存在性评分；第6级发现消极不融资仍可获40分的漏洞；第7级改为联合资金可行性，0.7.1再修正未触发义务的分母与契约口径。旧分数保留，不能据此挑选低分宣称达标。

当前0.8.0使用联合资金40、付款25、缓冲15、偿债20，费用单独报告。以下先列同版、同Agent/Workflow/Evaluator摘要及参数的重复结果。

| 模型 | 重复次数 | 原始分数 /100 | 均分 | 样本标准差 |
| --- | ---: | --- | ---: | ---: |
| MiniMax-M3.1-Flash | 3 | 100.00, 100.00, 100.00 | 100.00 | 0.00 |
| MiniMax-M3 | 3 | 80.09, 52.04, 52.04 | 61.39 | 16.19 |

0.8.0校准未达到30–40分目标；保留变体没有用于补挑低分。下一轮原始证据与协议推导设计见 [作者侧草案](../../product/financial-source-risk-case-design.md)。

| 运行 | 题版 | 模型 | 分数 /100 | 动作 | API用量记录 | 实际秒数 | 轨迹 |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| round-01 | 0.1.0 | MiniMax-M3.1-Flash | 21.67 | 101 | 101 | 460.66 | [账本](traces/round-01.json) |
| round-02 | 0.2.0 | MiniMax-M3.1-Flash | 91.36 | 126 | 126 | 337.47 | [账本](traces/round-02.json) |
| round-03 | 0.3.0 | MiniMax-M3.1-Flash | 未评分 | 55 | 56 | 202.34 | 无完整产物 |
| round-03-retry | 0.3.0 | MiniMax-M3.1-Flash | 未评分 | 73 | 74 | 200.16 | 无完整产物 |
| round-03-retry-2 | 0.3.0 | MiniMax-M3.1-Flash | 83.59 | 116 | 116 | 307.91 | [账本](traces/round-03-retry-2.json) |
| round-04 | 0.4.0 | MiniMax-M3.1-Flash | 未评分 | 124 | 125 | 318.28 | 无完整产物 |
| round-04-retry | 0.4.0 | MiniMax-M3.1-Flash | 81.44 | 117 | 117 | 307.84 | [账本](traces/round-04-retry.json) |
| round-05 | 0.5.0 | MiniMax-M3.1-Flash | 未评分 | 140 | 140 | 625.94 | 无完整产物 |
| round-05-flash-full-history | 0.5.0 | MiniMax-M3.1-Flash | 79.55 | 111 | 112 | 231.73 | [账本](traces/round-05-flash-full-history.json) |
| round-05-m3 | 0.5.0 | MiniMax-M3 | 未评分 | 78 | 79 | 177.34 | 无完整产物 |
| round-05-m3-corrected | 0.5.0 | MiniMax-M3 | 80.00 | 121 | 122 | 260.16 | [账本](traces/round-05-m3-corrected.json) |
| round-05-retry | 0.5.0 | MiniMax-M3.1-Flash | 100.00 | 142 | 144 | 529.42 | [账本](traces/round-05-retry.json) |
| round-06 | 0.6.0 | MiniMax-M3.1-Flash | 52.50 | 116 | 117 | 270.03 | [账本](traces/round-06.json) |
| round-06-m3 | 0.6.0 | MiniMax-M3 | 52.50 | 111 | 112 | 245.33 | [账本](traces/round-06-m3.json) |
| round-07 | 0.7.0 | MiniMax-M3.1-Flash | 26.25 | 118 | 120 | 355.41 | [账本](traces/round-07.json) |
| round-07-1-flash-1 | 0.7.1 | MiniMax-M3.1-Flash | 30.72 | 116 | 118 | 357.20 | [账本](traces/round-07-1-flash-1.json) |
| round-07-1-m3-1 | 0.7.1 | MiniMax-M3 | 16.67 | 127 | 128 | 451.83 | [账本](traces/round-07-1-m3-1.json) |
| round-07-m3 | 0.7.0 | MiniMax-M3 | 60.00 | 117 | 119 | 448.64 | [账本](traces/round-07-m3.json) |
| round-08 | 0.8.0 | MiniMax-M3.1-Flash | 100.00 | 126 | 127 | 288.91 | [账本](traces/round-08.json) |
| round-08-flash-repeat-2 | 0.8.0 | MiniMax-M3.1-Flash | 100.00 | 107 | 109 | 260.58 | [账本](traces/round-08-flash-repeat-2.json) |
| round-08-flash-repeat-3 | 0.8.0 | MiniMax-M3.1-Flash | 100.00 | 102 | 103 | 246.02 | [账本](traces/round-08-flash-repeat-3.json) |
| round-08-m3 | 0.8.0 | MiniMax-M3 | 80.09 | 124 | 127 | 385.09 | [账本](traces/round-08-m3.json) |
| round-08-m3-repeat-2 | 0.8.0 | MiniMax-M3 | 52.04 | 111 | 112 | 412.80 | [账本](traces/round-08-m3-repeat-2.json) |
| round-08-m3-repeat-3 | 0.8.0 | MiniMax-M3 | 52.04 | 114 | 114 | 349.48 | [账本](traces/round-08-m3-repeat-3.json) |

动作数包含被拒的工具请求；API用量记录还可能包含未完成请求和传输重试。原始第五轮触及8MB进程输出上限，修复为工作区会话历史加摘要句柄后使用同一题包补跑。
完整请求/响应、模型返回的 reasoning、工具回执、发布快照及其摘要在本地 `data/treasury-calibration/<运行>/execution.zip`。本目录仅发布合成业务账本和脱敏统计。
input_tokens 包含缓存命中部分；output_tokens 包含供应商报告的 reasoning 部分。两者不能再次相加计算总量。供应商未报告 API 费用时保持未知；账本里的 GBP fee 是仿真融资成本。
所有级别均由作者侧参考路径验证100分。当前均为一个场景家族；只有最终同题包、同Harness摘要、同参数的重复及未参与改题的情景验证，才能支持30–40分难度声明。
