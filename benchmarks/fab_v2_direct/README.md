# FAB v2 原始公开题直接运行

保留上游固定 CSV、source.json、MIT LICENSE，另提供直接执行入口。它绕过平台数据库、Release 和 TaskSpec；使用同一个千问研究 Harness、原始 criterion、权重及 must-pass 规则。不是官方 Vals runner，不调用 Codex CLI 或使用登录态。

```powershell
# 只检查来源与题目选择，不调用模型
python -m examples.fab_v2 direct --rows 3,4

# 在进程环境中配置 QIANWEN_API_KEY 后，用全新输出目录执行真实请求
python -m examples.fab_v2 direct --run --rows 3,4 --output data/fab-v2-direct-new
```

默认模型及 Judge 为 qwen3.8-max-0902、glm-5.3；可用 --models / --judges 指定明确型号，不自动降级。使用用户指定的千问平台 endpoint，不保存明文凭据。每题研究上限 7200 秒，每位 Judge 180 秒；错误、缺失提交、未评分均保留。

输出包含实际 response.md/sources.json、provider events、原始 Target/Judge 响应、逐项判定、输入/输出/缓存/reasoning token 和耗时。目录存在则拒绝覆盖。平台版评测使用相邻 [fab_v2_public](../fab_v2_public/README.md)。

此前四次真实模型结果来自平台原生包；这个直接入口在目录整理时新增，并通过协议 fixture 验证，本轮未另行消耗额度重跑。参见 [真实运行证据](../../docs/examples/fab-v2/README.md)。
