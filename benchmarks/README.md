# Benchmark 示例

FAB v2 的两种接入形式共用固定的 27 道公开题、239 条 rubric 和同一套评分规则。金融压力工作流使用独立的仿真题包。

| 目录 | 用途 | 执行方式 |
| --- | --- | --- |
| [fab_v2_direct](fab_v2_direct/README.md) | 原始公开 CSV 直接运行，不使用平台数据库/Release/TaskSpec | `python -m examples.fab_v2 direct`；真实调用需加 `--run` |
| [fab_v2_public](fab_v2_public/README.md) | 平台 dataset.toml/tasks 包，经发布、预检、Target、采集、独立 Judge 与导出 | `python -m examples.fab_v2 prepare`；真实调用为 `python -m examples.fab_v2 native --run` |
| [financial_stress](financial_stress/README.md) | 同一 LDI 值班案例的两条冲击分支和平稳对照，含资金账本与定时交收 | `python -m examples.financial_stress` |
| [treasury_network](treasury_network/README.md) | 多账户、多币种、动态通知和融资到期链路的挑战题源；先冻结生成原生题包 | `python -m benchmarks.treasury_network.build data/treasury-package --level 5` |
| [source_treasury](source_treasury/README.md) | 从原始银行/持仓/CSA/市场资料重建风险，复用可信交收引擎 | `python -m benchmarks.source_treasury.build data/source-package` |

已完成的 Qwen/GLM 四次真实研究使用 **fab_v2_public 的第 3、4 题子集**，不是新 direct 入口的实测成绩。报告、产物和完整轨迹见 [试跑证据](../docs/examples/fab-v2/README.md)。两种方式均为适配后的公开题诊断，不是官方六工具/三 Judge 排行榜复现。

来源与目录整理：

- `8f8b7f6` 引入原始题源与旧 Dataset/Codex 脚本。原始题源现保存在 fab_v2_direct；旧导入器、旧评分公式与旧脚本已删除。
- `03d9a02` 引入 native_finance 及 finance_command/finance_evaluator。它们是离线计算验收，**不是 FAB 变种**，现整体移入 [examples/native_benchmark](../examples/native_benchmark/README.md)。
- `55f0cd1` 引入 fab_v2_public 及真实 Qianwen 研究 Harness，并完成上述试跑。

以上依据路径提交历史区分，不靠 Git author 推测具体 agent；不同 agent 依照要求都使用用户的 GitHub 身份。保留的原生包内容未因整理而改变，原发布 digest 仍有效。
