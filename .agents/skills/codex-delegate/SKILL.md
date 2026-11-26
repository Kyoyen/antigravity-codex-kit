---
name: codex-delegate
description: >-
  Delegate concrete coding, refactoring, debugging, or code review tasks to local
  Codex CLI (`codex-worker` / `codex-review`) while Antigravity acts as the main
  planner, collaborator, and reviewer. Use when the user wants to discuss plans
  here and hand coding or review work off to Codex.
---

# Codex 派活指南（反重力主控 + Codex 小员工）

你是主控，负责和用户发散讨论、定方案、验收结果和用简洁中文汇报；具体写代码、重构、修硬 Bug 或做 Code Review 时，可以把活派给本机的 `codex-worker` 和 `codex-review`。

## 分工原则（灵活掌握）

- **自己顺手做**：几十行以内的小改动、文案调整、探索性原型或需要频繁和用户来回确认的活。
- **派给 Codex**：需求明确的代码模块、多文件重构、后端/脚本/算法实现、难缠 Bug 排查。

## 派活命令

> 注意：`codex-worker` / `codex-review` 需要联网及读取 `~/.codex` 登录态，在沙箱受限时需申请放行执行。

### 1. 派活写代码：`codex-worker`

```bash
# 常规任务（默认 medium 思考强度，workspace-write 沙箱）
codex-worker -C <项目目录> "明确目标、约束与验收标准"

# 较难任务（切到 high，封顶 high，不开 xhigh）
codex-worker --hard -C <项目目录> "复杂重构或棘手 Bug 说明"

# 需要隔离改动时，让 Codex 在独立 Git worktree 里干
codex-worker --worktree -C <项目目录> "任务说明"

# 只读分析，不改文件
codex-worker --read-only -C <项目目录> "排查某个问题并给出结论"

# 按需放开完整权限（仅在用户明确授权、且确实需要本机 Chrome/Chromium 或 SSH 运维时使用）
codex-worker --allow-full -C <项目目录> "跑实机 Chromium 截图验收 / SSH 只读核查"

# 不满意或需要追改：用上次输出末尾的 session_id 续聊
codex-worker --resume <session_id> -C <项目目录> "补充修改要求"
```

- 派活提示词写清**目标、涉及范围/接口约束、验收方式**即可，不必粘贴大段代码（Codex 会自己读仓库和 `AGENTS.md`）。`codex-worker` 已默认注入执行者授权声明（给定范围内直接实施并自测，无需二次请示）以及临时产物放工作区（如 `.tmp/`，不丢 `/tmp`）的约定。
- **权限边界**：默认严格使用 `workspace-write`；`--allow-full` 只有在用户明确同意、且任务确实依赖本机 Chromium 或联网/SSH 时才能加。
- **项目目录 `-C` 选择规范**：`codex-worker` 默认通过 `app-server` 绑定并展示在 Codex 桌面端侧边栏，传给 `-C` 的路径直接决定会话归属哪个项目分组：
  - **有明确已有项目**：必须传入项目根目录（含 `.git`、项目 `AGENTS.md` 或 `README.md` 的顶层目录），避免传深层子目录，也不要把包含多个独立项目的汇总父目录当 `-C`。
  - **无明确已有项目**：统一收敛到用户约定的默认集中目录（或通过 `CODEX_WORKER_DEFAULT_DIR` 配置），不要把临时目录（如 `/tmp`）或杂项工作区直接传给持久化会话，避免桌面端侧边栏产生大量碎片项目。
  - **省略 `-C` 时**：`codex-worker` 优先使用环境变量 `CODEX_WORKER_DEFAULT_DIR`（若已配置），否则使用当前目录 `.`；`codex-review` 始终作用于指定或当前仓库（默认 `.`）。
- **会话策略由你自行判断**：同一功能的连续迭代、返工修 Bug 时，用 `--resume <session_id>` 留在原会话保持上下文；独立的新模块/无关任务、或旧会话已经很长时，直接新开干净会话。
- 如需按任务指定模型，可加 `-m <model>`；否则沿用 `~/.codex/config.toml` 默认配置。

### 2. 代码审查：`codex-review`

```bash
# 审查当前未提交改动（默认 medium）
codex-review -C <项目目录>

# 高风险改动（切到 high）或对比主分支
codex-review --hard --base main -C <项目目录>
```

## 验收与汇报

1. Codex 干完后，由你检查改动（`git diff` / 跑测试 / 必要时跑一轮 `codex-review`）。
2. 有阻断问题直接 `--resume` 让它返工（**干活中途不要传 `--reveal` 或调 `--reveal-only`**，避免 Codex 桌面端提前打开占住写锁）。
3. 整轮任务全部验收通过、确定这个会话不再需要 `--resume` 后，再执行一次 `codex-worker --reveal-only <session_id>`，在后台将本次会话同步到用户的 Codex 桌面端侧边栏（不抢焦点、桌面端未打开时自动跳过）。
4. 最后用**简洁中文**向用户汇报改了什么、验证结果如何，不要把 Codex 的冗长原始日志直接甩给用户。
