# Finance Agent v2 — 公开题库

来自 [vals-ai/finance-agent-v2](https://github.com/vals-ai/finance-agent-v2)，固定 commit `03c7c432b645f42f81ef3c13957100b182217d7c`，上游文件 `data/public.csv`。本目录保留原始 CSV、上游 MIT LICENSE 和 source.json 来源清单。`.gitattributes` 禁止 CSV 换行转换，避免 Windows checkout 改变 SHA-256。

27 题，9 类各 3 题。每题包含 Question、Question Type、Expert time (mins)、Rubric；Rubric 保留 severity 与 must_pass。仅使用公开数据，没有拉取私有验证集或测试集。

准备题库，不调用模型：

```powershell
python examples/run_finance_benchmark.py
```

真实试跑（会调用你已登录的 Codex 或指定连接）：

```powershell
python examples/run_finance_benchmark.py --run --rows 3,4 --model gpt-6-sol
```

可按本机网络增加 `--proxy http://127.0.0.1:PORT`，不修改系统设置。模型必须是你账户可用的模型；可省略 --model 使用 CLI 默认模型。

完整说明及本次结果见 [接入报告](../../docs/finance-agent-v2.md)。本地单模型诊断分数不等于官方三模型评审成绩；替换了原版 Agent harness，不能直接对比官方榜单。
