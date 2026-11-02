# 0.2.2 后端版本与预检验证记录

日期：2026年11月1日。环境：Windows、Python 3.12.4。软件 0.2.2，数据库 schema 4，事件 schema 2。

## 交付范围

在前端原型独立开发期间，新增导入预览、评分器不可变修订、命名数据集版本、静态实验预检与哈希核对，以及 CLI Worker 心跳/凭据就绪上报。本轮没有改动前端目录，没有实现人工复核、Eval Spec 或 Gate。

## 完整回归

执行 `python -m unittest discover -s tests -v`：**76 项，75 项通过，0 失败，0 错误，1 项跳过**，耗时约 53.50 秒。摘要保存在 `data/backend-0.2.2-tests.json`。

跳过的仍是本机符号链接权限不足的测试；Linux CI 应实际执行该防护测试。CI 矩阵和三个 Demo 已配置，本轮未推送代码或触发远端 CI。

| 范围 | 本次验证 |
| --- | --- |
| 导入 | 预览和正式导入共享校验；逐行/字段错误；UTF-8 BOM；长 CSV 单元格；错误上限与无部分入库 |
| 评分器 | 独立修订 id 与稳定 evaluator_id；旧配置/实验不变；latest 查询；并发编辑仅一个成功 |
| 数据集版本 | 发布固定 Case 修订；草稿编辑、Case 归档不影响发布版；同名并发发布无孤立快照 |
| 预检 | 不写数据库、不调用 HTTP；缺 expected/参数/最终状态、空集与诊断模式；调用上限与未知成本 |
| 提交 | 同事务核对预检 hash；过期 409；缺证据 400；旧客户端兼容；已成功请求幂等重试不受后来草稿修改影响 |
| Worker | Worker 上报而非 API 环境检测；不泄露值；心跳过期/退出；不同 Worker 的凭据不能拼接成就绪；HTTP 的 Docker 环境也检查 Docker CLI |
| 鉴权 | 项目作用域、Owner 配置权限、版本/历史资源读取、runtime 全局状态权限 |
| 迁移 | v1/v2/v3 到 v4；旧同名评分器不自动合并；旧会话和幂等请求保留；在 DDL 与回填后分别注入失败并验证回滚 |
| 原功能 | Trace、SDK、并发、重试、取消、恢复、工件、评分预览、会话、CSV 与 Compare 回归 |

## 真实本地 API 与 CLI Worker 演示

执行 `python -m examples.platform catalog` 成功。该脚本启动临时 API、示例 HTTP Agent，以及实际 CLI Worker 子进程，并通过 HTTP 接口完成：

- 非法导入预览准确返回 1 条非法记录，合法批次导入。
- 发布 2 个 Case 的 business-v1 版本。
- 建立评分器第 2 修订并读取历史。
- 查询到 1 个在线 Worker，预检状态 ready。
- 带预检哈希创建并运行基线和候选实验。
- 修改草稿后，发布版本的两次运行仍 same=2。
- 使用不同评分器修订的实验不可直接比较。
- 草稿再次修改后，旧预检提交返回 409/stale_preflight。

完整摘要为 `data/catalog-report.json`。脚本不修改日常数据库，不调用真实模型，结束时停止临时进程并清理临时数据。

## 原有演示

`python -m examples.platform readiness` 成功：登录/CSRF、进度、分页、评分预览、修订冲突、归档重跑和退出均通过。

`python -m examples.platform smoke` 成功：v1 success_rate=0.5、v2=1.0，improved=1、same=1、regressed=0、unscored=0。

`python -m compileall -q liteeval tests examples` 与 `git diff --check` 通过。

## 接入边界

Worker 就绪是提示信息，当前不保证定向路由；CLI 存在不等于 Docker Engine、镜像、认证或模型服务通过验收。预检成功不等于执行成功或质量通过。新前端应携带 preflight_hash；旧兼容客户端可以省略该字段，不因此获得“已预检”保证。

真实业务 Agent、供应商 Judge、Codex 与 Docker 仍需各自验收。本轮的前端请求和响应约定见 [接口对接文档](frontend-backend-contract.md)。
