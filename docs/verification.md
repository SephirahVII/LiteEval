# 验证记录（0.2.0）

验证日期：2026-10-10；本地 Python 3.12.14，无第三方运行依赖。

## 自动化测试

命令：`python -m unittest discover -s tests -v`

结果：**28 项全部通过**，本次用时 15.200 秒。

| 范围 | 验证内容 |
| --- | --- |
| 核心闭环 | 生产 Trace → Case → 冻结快照 → 实验 → 汇总 → Compare；跨项目隔离 |
| 导入与版本 | CSV/JSON 批次原子性、筛选/导出、幂等冲突、数据集变化后仍可使用原快照 rerun |
| HTTP | 映射、Bearer 环境变量、完整 ContentPart 事件、usage/成本、Case 并发上限 |
| 恢复机制 | 瞬时失败重试与固定 Run 幂等键、单 Case 超时、队列/在途取消、失效租约接管、旧 Worker 写入拒绝、取消后崩溃清理、心跳及重复 claim |
| 评分与汇总 | 缺失证据处理、模拟 Judge 非法输出重试、原始回复留档、Agent 不重复调用、混合改善/回退时 Case 只计一次 |
| SDK | 同步/异步入口、嵌套与并发 span 的正确父子关系、上报失败不破坏业务结果 |
| 执行环境 | 真实子进程、种子文件/产物/验证测试、patch、退出清理、路径遍历拒绝、快照 symlink 防护、子进程超时保留部分输出 |
| Adapter | Codex JSONL started/completed 合并、未知事件保留、替身 CLI 生成文件并通过最终状态评分；Docker 命令约束构造 |
| 鉴权/持久化 | 实际 HTTP API 的管理员/项目作用域、密钥撤销、工件访问与完整性、v1 数据库增量迁移、配置及事件校验 |

## 完整 API Demo

命令：`python examples/run_demo.py`

通过真实本地 HTTP 请求创建项目、录入生产 Trace、转 Case、批量导入、配置两个示例 Agent、创建实验、运行 Worker，再通过 API Compare。

结果：两个 Case；v1 success_rate=0.5，v2 success_rate=1.0；improved=1、same=1、regressed=0、unscored=0。报告保存至 `data/demo/report.json`。这是示例数据上的确定性回归结果，不是实际业务质量测量。

## 千条 Case 本地容量检查

命令：`python examples/load_check.py --cases 1000 --concurrency 8`

本次 import → freeze → HTTP Agent → 规则评分 → 汇总共 **5.909 秒**；1000 个 Run succeeded，success_rate=1.0。数据库和工件使用独立临时目录，检查结束删除；摘要保存在 `data/load-check.json`。

此检查使用零模型推理延迟的小响应本地模拟 Agent，证明千条 Case 的数据与执行路径可完成，不代表真实网络/LLM 吞吐率或长时间压力测试结果。命令可在目标硬件重跑。

## 尚未验收的外部能力

- 真实供应商 Judge：未提供账户凭据；仅通过本地模拟服务验证协议、解析、重试与留档。真实模型需运行 judge-check 和人工标注集校准。
- 真实 Codex CLI：环境未安装 CLI；已使用可执行替身验证 subprocess/JSONL/文件/验证闭环，不能代替真实账户及沙箱验证。
- Docker Engine / Compose：环境没有 Docker；测试验证命令构造和限制，未实际 build/run 容器。
- 真实 Agent 副作用幂等、系统断电演练、长期压力、生产代理/TLS/限速、备份恢复及多用户运营流程，需部署环境验收。
