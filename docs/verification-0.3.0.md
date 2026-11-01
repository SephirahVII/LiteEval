# 0.3.0 凭据与 Rollout 验证记录

日期：2026-11-01。本地 Windows，Python 3.12.4，软件 0.3.0，数据库 schema 5，事件 schema 2。

## 完成范围

- 项目级 HTTP Agent / 兼容模型连接，保存 endpoint、model 和 Fernet 加密 API Key；查询不返回明文/密文，支持 Key 替换/移除、归档/恢复。
- Agent/Evaluator 引用连接，非敏感参数固定入修订，执行时读取凭据；Worker 按连接报告解密就绪。
- 租约隔离的活动/日志持久化与游标读取；本地完整日志行可在执行中查询。
- Target/Judge 各次尝试的原始与标准用量、按角色/模型/币种汇总，含重试，缓存及 reasoning 未知为 null。
- Codex 累计各 turn.completed 的 usage，避免最后一回合覆盖。
- 实验和单 Run 的统计、JSON/JSONL/CSV/ZIP 导出，ZIP 工件 SHA-256 校验。
- 将执行开始阶段的数据库写入移出事件循环，修复 Windows 上可能阻碍 HTTP 并发启动的问题。

## 测试

执行 `python -m unittest discover -s tests -v`：**88 项，87 项通过，0 失败，0 错误，1 项跳过**，约 98.48 秒。日志在忽略目录 `data/backend-0.3.0-tests.txt`。

跳过项为本机 Windows 符号链接权限不足；Linux CI 应实际执行。

新增 12 项集成测试覆盖：加密持久化与重启、Key 替换/移除、错误主密钥、项目权限、不可变 endpoint/model、归档、Worker 解密就绪、真实本地 HTTP Target/Judge 调用、Judge 解析失败重试的用量累计、完整行运行中日志/跨分块凭据脱敏、租约/取消隔离、四种导出、HTTP 二进制下载、ZIP 工件哈希与完整性失败、schema 4→5 成功与失败回滚。

本地 Target/Judge 示例的断言：Target 输入 10、输出 4；Judge 两次尝试共输入 12、输出 4；合计 30 token、缓存读取 3、reasoning 3、上报费用 0.05 USD。输入别名不重复累加，缓存与 reasoning 不加到 total。

## 原有 Demo

- `python examples/run_demo.py` 通过：0.5→1.0，improved=1、same=1。
- `python examples/run_readiness_demo.py` 通过：会话/CSRF、分页、预览、冲突、归档历史重跑、退出。
- `python examples/run_catalog_demo.py` 通过：固定版本、评分器修订、Worker 预检、相同版本 Compare、过期预检 409。

## 远端验证

先前改动已作为 42dd901 推送，作者与提交者均由 GitHub 识别为 SephirahVII。该提交首次 CI 的 6 个矩阵中 5 个通过，Windows/Python 3.12 的并发重叠断言失败；本轮已修复数据库写入阻塞并在本地完整回归通过。0.3.0 推送后的远端结果以对应 Actions 为准。

## 边界

新增测试使用本地模型替身与真实 HTTP/子进程，未调用真实供应商。User Agent、GitHub benchmark 拉取、真实 Codex/Docker 环境验收、本地结果缓存、TTFT/生成速率和 CPU/内存实测不在本轮。reasoning 计量只记录上游公开数据，不还原隐藏 CoT。历史 Judge 用量不回溯补齐。
