# antigravity-codex-kit

轻量级本地多 Agent 协作工具箱：使用 **Antigravity** 负责需求讨论、方案规划与结果验收，将具体的编码、重构与代码审查任务委托给本机 **Codex CLI** 执行。

无需配置额外的 MCP 服务或第三方依赖，仅包含两个基于 Python 标准库的 CLI 封装脚本与一份 Antigravity Skill。

## 设计特点

- **职责分离**：在 Antigravity 中完成需求对齐、架构讨论和最终验收；将边界清晰的实现、重构、问题排查和 Code Review 交给本机 Codex 执行。
- **可控的默认边界**：
  - `codex-worker` 默认使用 `workspace-write` 沙箱，支持 `--read-only` 只读模式和 `--worktree` Git 隔离模式。
  - 推理强度默认 `medium`，复杂任务可通过 `--hard` 切换至 `high`（脚本层限制上限为 `high`）。
  - 模型默认继承 `~/.codex/config.toml`，也支持通过 `-m` 按任务覆盖。
- **兼容 Codex 桌面客户端**：`codex-worker` 默认通过 `codex app-server --stdio` 协议发起与恢复会话，会话会绑定到对应的项目目录并以 `vscode` 来源记录，可在 Codex 桌面客户端中查看历史记录。
- **灵活的会话管理**：每次执行自动输出 `session_id`；同一任务的连续修改可通过 `--resume <session_id>` 延续上下文，独立任务则开启新会话。

## 目录结构

```text
.
├── bin/
│   ├── codex-worker                     # 封装 Codex 任务执行（默认 app-server，--worktree 时回退 codex exec）
│   └── codex-review                     # 封装 Codex 代码审查，输出「阻断项 / 建议项」结构化结论
├── .agents/skills/codex-delegate/
│   └── SKILL.md                         # Antigravity Skill，定义任务委托与验收流程
├── AGENTS.md                            # Antigravity 与 Codex 共用的项目约定
└── install.sh                           # 将命令与 Skill 软链至全局目录
```

## 环境要求

- [Antigravity](https://antigravity.google/)
- 本机已安装并登录 [Codex CLI](https://github.com/openai/codex)（基于 `codex-cli 0.169.0` 验证）
- Python 3（标准库，无额外依赖）
- `~/.local/bin` 已加入 `PATH`

## 安装

```bash
git clone https://github.com/Zoeoetheroad/antigravity-codex-kit.git
cd antigravity-codex-kit
./install.sh
```

`install.sh` 会创建以下软链接（修改本仓库文件即可直接生效）：

- `~/.local/bin/codex-worker` → `bin/codex-worker`
- `~/.local/bin/codex-review` → `bin/codex-review`
- `~/.gemini/config/skills/codex-delegate` → `.agents/skills/codex-delegate`

安装完成后，在任意项目中打开 Antigravity 均可自动识别 `codex-delegate` Skill。

## 使用方式

### 在 Antigravity 中调用

确认方案后，可直接指示 Antigravity 将具体开发或审查任务交由 Codex 执行，例如：

- “按这个方案交给 Codex 实现。”
- “让 Codex 重构这部分逻辑并补充测试。”
- “用 Codex 对当前改动做一次 Code Review。”

> 首次在 Antigravity 中执行 `codex-worker` / `codex-review` 时，由于需要访问网络及 `~/.codex` 凭证，需允许该命令执行。

### 命令行直接调用

```bash
# 常规任务（默认 medium，workspace-write 沙箱）
codex-worker -C /path/to/repo "实现 xxx 功能并补充单元测试"

# 复杂任务（切换至 high，上限 high）
codex-worker --hard -C /path/to/repo "排查并修复 xxx 并发问题"

# 只读分析（不修改文件）
codex-worker --read-only -C /path/to/repo "梳理鉴权流程并列出潜在边界问题"

# 在独立 Git worktree 中执行
codex-worker --worktree -C /path/to/repo "重构 xxx 模块"

# 延续已有会话（传入 session_id，或使用 last 延续当前目录最近一次会话）
codex-worker --resume <session_id> -C /path/to/repo "补充异常处理逻辑"

# 代码审查（默认审查未提交改动，也支持 --base <branch> / --commit <sha>）
codex-review -C /path/to/repo
codex-review --hard --base main -C /path/to/repo
```

## 说明

- **在 Codex 桌面客户端中查看会话**：Codex 桌面端与外部 `codex-worker` 各自持有独立 `app-server` 进程，且同一会话受单写者文件锁（`thread-writer-locks`）保护。实测桌面端一旦通过 `codex://threads/<session_id>` 或手动点击加载某会话，会长期持有该会话写锁（切到其他会话也不会立即释放），导致后续 `codex-worker --resume <session_id>` 报 `already has an active writer`。因此 `codex-worker` **默认不自动在桌面端打开会话**（`CODEX_WORKER_REVEAL=never`），以保证连续 `--resume` 追改不受影响；确认任务收尾、需要直接交接给已运行的 macOS 桌面端查看或继续对话时，可显式加 `--reveal`（或设置 `CODEX_WORKER_REVEAL=auto|always`），在不抢焦点的前提下通过 `open -g -b com.openai.codex codex://threads/<session_id>` 后台刷出该会话。
- **在终端接管会话**：可通过 `codex resume <session_id>` 在终端交互界面中查看完整过程或继续对话。
- **Codex 配置解析失败**：如果旧版 CLI 不识别当前配置（例如 `web_search = "indexed"`），报错会显示实际调用的 CLI 路径和可取得的版本。默认沿用 `PATH`；可用 `CODEX_BIN="/absolute/path/to/codex" codex-worker --read-only -C /path/to/project "只回复 OK"` 为单次调用指定兼容的可执行文件。工作树模式与 `codex-review` 同样接受 `CODEX_BIN`；显式指定无效路径时不会回退到 `PATH`。无需为此更改现有 Codex 配置。
