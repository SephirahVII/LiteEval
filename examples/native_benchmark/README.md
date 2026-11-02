# 离线目录 Benchmark 验收样例

这是两道 CSV 收入增长计算题，不是 FAB v2。运行 `python -m examples.native_benchmark`，无需 API Key。

```text
native_benchmark/
├── __main__.py
├── benchmark/                 # dataset.toml、两题、初始文件、Ground Truth、测试
└── harnesses/
    ├── agent/                 # role=agent：读 CSV，计算并提交文件
    └── evaluation/            # role=evaluation：独立读取提交与 Ground Truth
```

两个 Harness 是可注册的业务示例，分别扮演被测 Agent 与评分器；它们不定义工作区或 Docker 环境。环境由题包 profile 声明、平台 Runtime 执行。平台内置 Harness/协议在 `liteeval/integrations/harnesses/`，环境实现在 `liteeval/infrastructure/execution/`。

原根目录 harnesses/finance_command 与 finance_evaluator 现已移到这里。两者职责不同，保留为独立协议验收。平台测试和 Linux Docker CI 使用同一份样例，避免重复维护。
