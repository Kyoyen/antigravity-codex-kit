# Antigravity × Codex 轻量协作工具箱

> **核心思路：反重力（Antigravity）当总管陪你发散聊方案、定方向、做验收；本机 Codex 当“代码小员工”和严格审稿人。**

不用搭重型框架、不用折腾复杂 MCP，只用两个零第三方依赖的 Python 脚本 + 一份轻量 Skill，把 Antigravity 的沟通规划体验和 Codex 的代码执行能力串成一个顺手的本地工作流。

---

## 为什么这么做？

- **各干各最擅长的事**：在 Antigravity 里聊想法、拆方案、看人话汇报；具体写模块、大重构、修棘手 Bug 和做 Code Review 派给本机 Codex 干，不用自己直面 GPT 的冗长技术细节。
- **不裸调 `codex exec`，安全可控**：
  - 默认 `workspace-write` 沙箱（不会裸奔 `danger-full-access`），支持 `--read-only` 只读分析。
  - 思考强度默认 `medium`，难活加 `--hard` 切到 `high`（脚本层封顶 `high`，省额度不乱飙）。
  - 模型默认沿用你 `~/.codex/config.toml` 配置，也可按任务 `-m` 临时指定。
- **原生接入 Codex 桌面端**：默认走 `codex app-server --stdio` 官方协议发起会话，每笔派活都准确绑定对应项目目录，且原生以 `vscode` 来源落库——重开 Codex 桌面客户端就能在对应项目下看到历史会话，随时可点开或接管。
- **智能续聊 & 灵活不死板**：每次派活自动输出 `session_id`；同一功能的返工/追改用 `--resume <id>` 留在原会话，独立新任务则开新会话，由反重力自行判断。

---

## 目录结构

```text
.
├── bin/
│   ├── codex-worker                     # 派活写代码（默认 app-server 通道，--worktree 时走 codex exec）
│   └── codex-review                     # 合并前质检（输出简洁中文「阻断项 / 建议项」）
├── .agents/skills/codex-delegate/
│   └── SKILL.md                         # 轻量 Antigravity Skill，教反重力怎么派活与验收
├── AGENTS.md                            # 反重力与 Codex 共用的项目规范示例
└── install.sh                           # 一键软链到 ~/.local/bin 和 ~/.gemini/config/skills
```

---

## 前置条件

1. 已安装 [Antigravity](https://antigravity.google/)
2. 本机已安装并登录 [Codex CLI](https://github.com/openai/codex)（实测基于 `codex-cli 0.169.0`）
3. Python 3（macOS 自带即可，无任何第三方依赖）
4. `~/.local/bin` 在你的 `PATH` 中（如未配置，在 `~/.zshrc` 里加一句 `export PATH="$HOME/.local/bin:$PATH"`）

---

## 一键安装

```bash
git clone https://github.com/Zoeoetheroad/antigravity-codex-kit.git
cd antigravity-codex-kit
./install.sh
```

`install.sh` 会做两件事（都是软链，改仓库文件立即生效）：
- 把 `codex-worker` 和 `codex-review` 软链到 `~/.local/bin/`
- 把 `codex-delegate` Skill 软链到 `~/.gemini/config/skills/`，以后你在**任何项目**打开 Antigravity 都会自动识别

---

## 日常怎么用

### 1. 在 Antigravity 里直接说人话
聊完方案后，直接跟反重力说：
- “方案定了，交给 Codex 去实现吧”
- “让小员工顺手把这块重构一下”
- “跑一下 Codex Review 看看有没有坑”

Antigravity 会自动写好任务目标、约束和验收标准，调 `codex-worker` 派活，干完后帮你看 diff、跑验证，再用简洁中文向你汇报。

> 首次在 Antigravity 里运行 `codex-worker` / `codex-review` 时，因为需要联网及读取 `~/.codex` 登录态，放行一次命令即可。

### 2. 也可以在终端直接调用

```bash
# 常规任务（默认 medium，workspace-write 沙箱）
codex-worker -C /path/to/repo "实现 xxx 功能并补单元测试"

# 较难任务（切到 high，封顶 high）
codex-worker --hard -C /path/to/repo "排查并修复 xxx 并发竞争问题"

# 只读分析，不改代码
codex-worker --read-only -C /path/to/repo "梳理登录鉴权流程并指出潜在边界问题"

# 在独立 Git worktree 里隔离开发
codex-worker --worktree -C /path/to/repo "实验性重构 xxx 模块"

# 续聊返工（用上次末尾打印的 session_id，或传 last 续最近一次）
codex-worker --resume <session_id> -C /path/to/repo "把错误提示改成中文"

# 代码审查（默认审未提交改动，也支持 --base main / --commit <sha>）
codex-review -C /path/to/repo
codex-review --hard --base main -C /path/to/repo
```

---

## 小贴士

- **在 Codex 桌面客户端看历史会话**：由于 Codex 桌面客户端不会热监听外部写入，平时在 Antigravity 点开命令卡片看汇报即可；想在 Codex 客户端侧边栏看到新增的派活列表时，退出重开一下 Codex 客户端就会全部同步出来。
- **在终端接管某次会话**：随时可运行 `codex resume <session_id>` 进交互界面查看每一步细节或亲自继续聊。
