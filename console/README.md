# 控制台交互原型

```powershell
cd console
npm run dev
npm test
```

默认监听 http://127.0.0.1:7100，零 npm 依赖。页面数据、登录、导入、任务执行、导出及进度均为内存 Mock，刷新后重置，尚未接通后端。不要在演示表单中输入真实凭据。

`/trajectory.html` 是独立的真实轨迹入口。它通过本机 API 读取已有 Native Execution 的增量事件，使用 LiteEval 项目 Key 或已有 Owner 会话。运行步骤见 [多轮 Benchmark](../docs/dialogue-benchmarks.md#实时轨迹与评分)。

## 信息架构（PRD 1.1）

一级入口为 **概览 → 评测集（Benchmarks）→ 评测运行（Evaluation Runs）→ 运行配置（Harnesses / Model Connections）**，项目设置（项目 API Key、Owner 会话、执行环境状态）位于侧边栏底部。项目是凭据与数据的权限边界而非组织噱头：**仅当存在多个项目时**侧边栏才显示切换器，单项目直接默认进入；切换静默生效，以选中高亮和 URL 项目 ID 表达状态。Trace 导入收在「评测集 → 导入来源」，由 Trace 派生的内容标为 Dataset 来源。原型对齐 `docs/product/liteeval-prd-v1.1.md`（后端核对基线 `main@90478fa`，软件 0.4.2 · schema 6）。

与后端能力的对应以「能力徽标」常驻标注：**HTTP 可接 / 部分可接 / 通过 CLI 执行 / 待实现**，口径见 PRD §4 对照表。演示数据带持久「演示 · Demo」标识；指标按「已评分通过率 / 评分覆盖率」口径展示并标明分母。中英文界面可切换，Benchmark、Harness、Rollout、Trace 等稳定术语保留原文。新建评测为四步向导（选题 → 被测 → 评分 → 预检与提交），阻塞项 / 待运行验证 / 已通过分别展示。

历史版本（c7b8596 / 83c082f / a3b8ece）为 Dataset 主流程原型与设计参考图，已被 PRD 1.1 结构取代。测试验证静态服务边界、模拟 DOM 下的路由 / 标签渲染与表单转义，不代替浏览器视觉验收或真实 API 集成测试。
