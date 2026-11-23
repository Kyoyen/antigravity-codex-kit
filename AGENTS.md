# AGENTS.md

本项目是本地多 Agent 协作工具箱（Antigravity 主控 + Codex 执行/审查）。

## 通用约定

- **保持轻量**：脚本只依赖 macOS 自带工具和 Python 3 标准库，不引入多余第三方依赖。
- **安全边界**：`codex-worker` 默认使用 `workspace-write` 沙箱，禁止默认开启 `danger-full-access`；思考强度默认 `medium`，上限 `high`。
- **代码风格**：脚本参数清晰、报错可读、输出简洁，方便 Agent 和人类直接阅读。
- **变更验证**：修改 `bin/` 下的脚本后，至少执行 `--help` 和语法检查（`python3 -m py_compile`）确认无误。
