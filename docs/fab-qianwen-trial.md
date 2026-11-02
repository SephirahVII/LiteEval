# FAB v2：Qwen / GLM 真实研究试跑

2026-11-02，LiteEval 0.4.1。使用用户提供的千问 AI 平台 API Key，endpoint 为 `https://maas.qianwenaiapi.com/compatible-mode/v1`。未使用 Codex 登录或模型 CLI。通过该 endpoint 的 `/models` 查询及实际请求验证，本轮选择 `qwen3.8-max-0902` 和 `glm-5.3`；记录请求模型和供应商返回模型，但不能据此独立验证供应商后台权重。

## 实际结果

固定公开集原始第 3、4 题，分别是 EA/TTWO 年报竞争披露比较，以及 CRWD/PANW FY2023→FY2025 收入 CAGR 比较。每个模型每题运行一次，共 4 次 Target。Judge 对每题全部 criterion 逐项判断，平台计算 severity 加权、must-pass gate 和 All-Pass。

| Target | 题目 | Target 耗时 | Qwen Judge | GLM Judge（离线解析恢复） |
| --- | --- | ---: | ---: | ---: |
| qwen3.8-max-0902 | 003：EA / TTWO | 24分40.89秒 | 1.0 | 1.0 |
| qwen3.8-max-0902 | 004：CRWD / PANW | 12分47.52秒 | 1.0 | 1.0 |
| glm-5.3 | 003：EA / TTWO | 5分54.48秒 | 1.0 | 1.0 |
| glm-5.3 | 004：CRWD / PANW | 4分23.20秒 | 1.0 | 1.0 |

共 44 条真实 Judge criterion 判定，全部通过；不代表对答案的独立人工审计。第 003 题未指定财年，两模型在答案中说明使用 FY2026、同时核对 FY2025，原始题面没有被修改。时间是观测结果，不以人工估时作为最短运行时间。不同模型研究路径不同，不能单凭较短耗时断言虚假；也不能据此推断全题库表现。

GLM 首次返回的完整 JSON 被包在 Markdown 代码块中，原解析器将四份响应记为 unscored。修复只接受一个完整 JSON 对象或单个完整 JSON 代码块，仍拒绝额外文字、缺项、重复 criterion 和权重篡改。报告用同一批保存的 GLM 原始响应离线计算 1.0，没有新模型请求、没有修改答案/rubric，也没有覆盖原 unscored 数据库记录。`offline-recovery.json` 保存来源 execution/blob/rubric 哈希、原错误、修复代码 digest 和完整逐项判定。

## 实际研究与用量

Harness `liteeval.qianwen-research/v1` 调用千问平台 `/responses`，启用 web_search、web_extractor、code_interpreter。模型必须显式调用 submit_answer，本地 Harness 才写 response.md、sources.json；最终聊天文本不会自动充当文件。Target 看不到 Judge 的 rubric。研究计算发生在供应商云端；平台只能记录其返回的输入、输出和状态，无法观测云端完整操作系统、CPU、内存或未返回的内部步骤。

| Target / 题目 | 输入 token | 其中缓存 | 输出 token | 其中 reasoning | 搜索 / 提取 / 代码调用 |
| --- | ---: | ---: | ---: | ---: | --- |
| Qwen / 003 | 1,462,718 | 1,295,744 | 48,198 | 15,397 | 4 / 8 / 70 |
| Qwen / 004 | 363,131 | 279,552 | 21,773 | 10,167 | 8 / 12 / 6 |
| GLM / 003 | 766,442 | 719,872 | 18,380 | 9,198 | 2 / 4 / 36 |
| GLM / 004 | 216,017 | 185,856 | 14,218 | 7,904 | 5 / 3 / 7 |

上述计数来自供应商 usage，包含研究内部多步调用及 Qwen 003 的续接请求；不是单个上下文窗口长度。缓存属于输入、reasoning 属于输出，不再额外相加。表中不含 Judge 与运行前联通/搜索探针；Judge 用量保留在各执行结果中，探针独立保存。价格和实际账单未报告，保持未知。

工具执行中出现过代码错误，之后仍完成提交；原始错误保留在工具输出和 SSE 中。调用次数不等于搜索查询条数，不把 SSE 的 added/done 事件重复计为两次调用。每份产物的实际 bytes、大小和 SHA-256 都经过复核。

## 记录与复现

本机 `data/fab-qianwen/` 包含：

- `report.md`、`report.json`：可读报告及原始执行/评分结果。
- `evidence.json`、`offline-recovery.json`：文件哈希、耗时、工具及 token、恢复前后评分来源。
- `answers/`：四份真正提交的 Markdown 答案；sources.json 也保存在各执行 ZIP。
- `*-trajectory.md`：按时间排列的工具输入输出、模型用量与逐项 Judge 判定。
- `*-primary.zip`、`*-judge-*.zip`：冻结运行计划、事件 JSONL、原始模型响应/SSE、CSV 与实际产物。
- `fab-qianwen-evidence.zip`、`integrity.json`：汇总证据包及内部文件哈希。

原始 Judge 响应含公开 rubric，仅向有权查看评分材料的人分享。汇总包不包含连接数据库、API Key 或主密钥。凭据在本机使用 DPAPI/应用加密保存，不进入 Git。

本轮本地后端回归运行 121 项，119 项通过、2 项跳过（Windows 符号链接权限及本机未配置 Docker）。解析器的完整对象/代码块接受、非法内容拒绝及切换 Judge 重评都有协议测试；这些测试使用明确标注的 HTTP fixture，不作为模型成绩。实际 API 响应、落盘产物、ZIP 内容与哈希另行核验通过，凭据扫描未发现明文 Key。

在执行进程中配置 `QIANWEN_API_KEY` 和 `LITEEVAL_SECRET_KEY`（Fernet 主密钥），然后：

```powershell
python examples/run_fab_qianwen.py --run --rows 3,4 --output data/fab-qianwen
python examples/export_fab_trial_report.py
```

第一条会实际消耗供应商额度，默认不传 --run 时拒绝调用。复跑使用新的输出目录保存独立实验，避免覆盖本次报告。导出脚本默认读取本次 `data/fab-qianwen`，纯本地运行，不需要模型凭据。模型不存在时脚本拒绝降级；运行中可读取 progress.json、live-console.txt 或 benchmark execution 查询接口查看持久化事件。

固定题库 revision 为 `03c7c432b645f42f81ef3c13957100b182217d7c`；两题 release digest 为 `c44778e02583783e1e528356bfad4815e892cf68d26d78ad34da032390b15b51`。Qwen 主执行 `5985ddfe010d4289a5287788394a8353`，GLM 主执行 `81211ea6d0aa4705bfe98f40e806491f`。

这是两题、单次重复、双 Judge 的诊断，未复刻官方六工具、三模型 jury、三次重复或私有测试集，不能与 [官方 FAB v2 方法](https://www.vals.ai/benchmarks/fabv2) 的榜单分数直接比较。供应商工具协议依据 [千问平台文档](https://platform.qianwenai.com/docs/developer-guides/tool-calling/web-search)；上述运行结论以本机保存的实际 API 响应为依据。
