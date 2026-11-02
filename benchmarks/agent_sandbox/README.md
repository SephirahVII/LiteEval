# agent-sandbox

三道小型确定性任务，由可插拔的 agent harness 在加固 Docker 沙箱中执行：只读根文件系统、drop ALL capabilities、no-new-privileges、宿主编码 uid、/tmp tmpfs；仅 `bridge` 网络用于访问模型 API。

- `bugfix-001`：修复有 bug 的统计模块（隐藏用例判分 + 修复说明检查）
- `data-001`：CSV 销售数据聚合成精确 schema 的 JSON + 报告
- `parse-001`：解析应用日志，产出结构化统计 JSON

评分全部为离线 command judge（ground truth 在 agent 环境销毁后才物化）。题目与 agent 无关，同一 Release 可用不同 harness 复跑以横向比较。

可用 harness：

| `--harness` | 目录 | 必需环境变量 | 可选 |
|---|---|---|---|
| `claude` | `harnesses/claude_code` | `ANTHROPIC_API_KEY` | `ANTHROPIC_BASE_URL` / `ANTHROPIC_MODEL` / `CLAUDE_CODE_MAX_TURNS` |
| `dsh` | `harnesses/dsh` | `DEEPSEEK_API_KEY` | `DEEPSEEK_BASE_URL` / `DSH_MODEL` |

前置条件：
1. `images/agent-sandbox/build.sh` 构建并推送沙箱镜像，将输出的 digest 写入 `dataset.toml`
2. Worker 环境存在对应 harness 的 API Key

运行：`python examples/run_agent_sandbox_benchmark.py --harness claude`
