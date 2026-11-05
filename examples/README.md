# 示例入口

示例按场景分组，根目录不再堆放独立运行脚本。

| 命令 / 目录 | 内容 | 真实模型 |
| --- | --- | --- |
| `python -m examples.platform smoke` | HTTP Agent → Worker → Compare | 否 |
| `python -m examples.platform readiness` | Owner 会话、预览、分页、归档验收 | 否 |
| `python -m examples.platform catalog` | 固定版本、预检与 Compare | 否 |
| `python -m examples.platform load --cases 1000 --concurrency 8` | 本地吞吐验收 | 否 |
| `python -m examples.native_benchmark` | 两题、命令 Agent、自建评分 Harness、实际文件 | 否 |
| `config/` | Judge、Codex Adapter 配置样例，无真实凭据 | 配置本身不调用 |

平台 Harness 实现在 `liteeval/integrations/harnesses/`；示例目录中的 Harness 是协议使用者。外部题库、专属脚本和结果的归属见 [Benchmark 交付](../docs/benchmark-distribution.md)。
