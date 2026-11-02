# 0.4.0 架构与目录执行验收

日期：2026-11-02。本轮验收对象是目录式 Benchmark 的完整执行链路，不以旧 Python 导入路径兼容或历史设计保留作为完成条件。

## 实际执行

执行命令：`python -m examples.native_benchmark`。

- 题库：`examples/native_benchmark/benchmark`，2 道题，正增长与负增长。
- Agent Harness：`examples/native_benchmark/harnesses/agent`，从公开 CSV 读取数据并计算，没有读取 Ground Truth。
- Evaluation Harness：内置 `liteeval.native/v1` 和独立注册的 `examples/native_benchmark/harnesses/evaluation` 分别执行，两组各 2/2 通过。
- 真实操作：Agent/Judge 子进程、CSV 读取、JSON/Markdown 输出、256 字节二进制复制与 SHA-256 检查。
- 每题采集 3 个产物，原始文件进入内容寻址存储；ZIP 共 6 个产物文件及计划/事件/汇总，共 9 个成员。
- 重评只读取已持久化 Submission，不调用 Target，不覆盖原结果。
- 模型调用数为 0。这是确定性执行验收，不是模型能力成绩或官方 FAB 结果。

最新本机证据：`data/native-benchmark/report.json`、其中指定的 ZIP、SQLite 和测试日志。数据文件不提交到 Git；其他机器可按相同命令生成自己的证据。

## 测试覆盖

新增目录测试覆盖真实子进程、二进制产物、错误答案、缺失文件、Judge 崩溃、取消、超时、资源篡改、版本冲突、删除来源后运行、重评不重跑、项目权限、模型 JSON 工具循环和用量协议、外部 Evaluation Harness 不重复评分，以及架构边界。

模型协议测试使用本地 HTTP fixture 返回脚本化 action 和模拟 usage，不能将这些计数当作真实供应商用量。

完整回归初次运行暴露一项旧测试的 100ms Python 启动假设，并发现超时会取消 reader、未排空已有日志的问题。已修复：超时先终止进程树，日志 reader 保持运行并有界排空；测试为解释器启动留出合理时间，仍验证进程提前终止与 partial 输出保留。

最终本地全量结果：112 项，110 通过、2 跳过，101.516 秒。跳过项为 Docker 实机运行和 Windows 无符号链接权限；不将其计为已验收。日志为 `data/native-benchmark/verified-tests.txt`。Dataset、readiness、catalog 与新目录四个示例均已实际运行通过；可编辑包 0.4.0 构建、安装和新 CLI 校验通过。

额外将暂存的 Git tree 导出到全新临时目录执行 native 示例，确认题库 CSV/二进制输入、Harness 和新模块均包含在 Git 内容中，不依赖工作区未跟踪文件。源码与样例使用固定 LF 字节，原始 FAB CSV 继续保留上游字节。

静态检查发现 47 个实现模块、0 个依赖环。Domain/Ports 禁止导入部署层，顶层只有 __init__.py 与 bootstrap.py，没有旧路径兼容模块。

## Docker 与发布条件

当前 Windows 主机没有 Docker CLI/WSL，因此本地容器测试明确跳过，不将跳过算作通过。

GitHub Actions 增加独立 Linux Docker 作业：拉取 Python 镜像、读取 RepoDigest、以实际 digest 固定环境后执行同一题库与独立容器 Judge。结果见 [后端 CI](https://github.com/SephirahVII/LiteEval/actions/workflows/backend.yml)，应以对应提交的作业状态判断，不能仅凭此文档认定已通过。

Workspace 只验证受信任本地程序的目录隔离，不证明宿主安全隔离。真实模型长任务、官方 Harness 方法一致性、服务图、远程队列/恢复及自动 Git 同步仍未验收；完整能力列表见 [操作指南](benchmark-packages.md)。
