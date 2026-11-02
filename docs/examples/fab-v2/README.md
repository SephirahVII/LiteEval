# FAB v2 已完成的原生包试跑

[下载完整证据包](trial-2026-11-02.zip)；[中文方法与结果](../../fab-qianwen-trial.md)。

2026-11-02 使用用户提供的千问平台 API Key，对原生 fab_v2_public 第 3、4 题进行 Qwen/GLM 共四次真实研究，不使用 Codex 登录。Target 耗时分别为 24分41秒、12分48秒、5分54秒、4分23秒；两位 Judge 诊断分均为 1.0。

GLM 首次响应的 Markdown JSON fence 引发解析失败；修复后对同一原始响应离线重算，原 unscored 记录保留。归档含原始响应哈希、恢复判定、真实答案、来源、工具轨迹、token/缓存/reasoning 用量及各执行 ZIP。该包在目录整理前生成，内部路径和代码 digest 保留当时事实。

这是两题公开诊断，不是官方 FAB 排行榜复现，也不是新增 direct CSV 入口的真实模型成绩。新增 direct 入口本轮只通过协议 fixture 验证；没有重复调用模型。

归档不含凭据、主密钥或连接数据库；只包含公开题的研究与评分材料。ZIP 内 integrity.json 列出所有文件哈希。

归档 SHA-256：`42970dd1374306f4be07186bdab8c560faba45188bb54605629bacf5145e6c9c`。
