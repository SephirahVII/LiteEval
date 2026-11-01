# 0.2.1 第一步后端验证记录

验证日期：2026年11月1日。环境：Windows、Python 3.12.4。软件版本 0.2.1，数据库 schema 3，事件 schema 2。

## 自动化测试

执行 `python -m unittest discover -s tests -v`，共 54 项：53 项通过、0 失败、0 错误、1 项跳过；完整回归约 56.97 秒。

跳过项为 `test_snapshot_refuses_symlink_parent`：本机创建符号链接时报 WinError 1314，测试明确记录缺少权限。大文件快照上限现已单独测试并通过。跳过不代表符号链接防护已在本机完成验收；新增 Linux CI 应实际执行该项。

| 范围 | 验证内容 |
| --- | --- |
| 原有评测核心 | Trace→Case→实验→Compare、项目隔离、并发、重试、取消、租约接管与旧 Worker 写入拒绝 |
| 跨平台执行 | `.py` 受信任 Codex 启动器实际子进程、JSONL、文件和验证测试闭环；Windows 连接中断不再打印无关堆栈 |
| 浏览器会话 | Cookie 属性、CSRF 与 Origin、登录限速、退出、空闲/绝对过期、Owner 轮换撤销、HTTPS Secure Cookie |
| API Key 兼容 | 原 Bearer 作用域、禁止越权配置/预览、无效 Bearer 不回退 Cookie、原接口与旧修订调用 |
| 数据维护 | 原子导入、UTF-8 BOM CSV 回环、归档/恢复、历史快照与 rerun 保留、元数据校验 |
| 并发编辑 | 两个线程以相同 base_revision 保存，仅一个成功，另一个 409；显式 null 不允许绕过校验 |
| Web 查询 | 轻量进度、分页、状态/质量筛选、未执行和取消 Case、分页质量分类与原 summary 一致 |
| 评分预览 | 合成样本、历史冻结 Case、证据缺失 abstain、原正式 Score 不变、Judge 重试及每次原始回复 |
| 持久任务 | CLI Worker 实际子进程消费预览、预览进程重启后接管、拒绝旧租约提交 |
| 数据迁移 | v1/v2 升级到 v3、既有内容保留、重复打开、故障注入后结构和 user_version 回滚 |

全量回归后，还针对 HTTPS 来源大小写及显式 null 修订号进行了 HTTP 测试复核。原始全量摘要保存在 `data/step-1-tests.json`。

## 原有 API Demo

执行 `python examples/run_demo.py` 成功：两个 Case，v1 质量通过率 0.5，v2 为 1.0；Compare 为 improved=1、same=1、regressed=0、unscored=0。报告为 `data/demo/report.json`。

## 新增第一步演示

执行 `python examples/run_readiness_demo.py` 成功，实际经过本地 HTTP API、示例 Agent、Runner 和 PreviewRunner：

- Owner 登录与 CSRF 写入通过，退出后会话返回 401。
- 排队时 pending=2，运行后 terminal=2。
- 分页返回 total=2、单页 1 条，默认没有 output。
- 预览历史 Run 返回 pass，正式实验评分保持不变。
- 使用旧 base_revision 保存返回 409。
- 归档 Dataset 后，原快照 rerun 仍成功；Compare same=2。

报告为 `data/readiness-report.json`。演示使用临时数据与随机凭据，结束后关闭服务，不初始化日常账号、不调用真实模型。

## 持续验证与边界

新增 `.github/workflows/backend.yml`，定义 Ubuntu/Windows、Python 3.11/3.12/3.13 矩阵，运行测试与两个 Demo。本轮未推送代码、未触发远端 CI；不能声称 Linux 或其他 Python 版本已实测通过。

真实供应商 Judge、业务 Agent 副作用幂等、真实 Codex 认证/沙箱、Docker Engine 和生产部署仍需在目标环境验收。预览 Judge 的错误恢复采用至少一次调用，崩溃可能产生重复计费；当前已上报 Agent 成本与 Judge 原始回复仍分别保存。

本轮保留既有标准库 HTTP 服务，新增功能无第三方运行依赖。详细的安装、升级、登录、接口操作和启动步骤见 [第一步操作指南](step-1-backend.md)。
