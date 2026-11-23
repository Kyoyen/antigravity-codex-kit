#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_TARGET="${HOME}/.local/bin"
SKILL_TARGET="${HOME}/.gemini/config/skills"

chmod +x "${ROOT_DIR}/bin/codex-worker" "${ROOT_DIR}/bin/codex-review"

mkdir -p "${BIN_TARGET}" "${SKILL_TARGET}"

ln -sfn "${ROOT_DIR}/bin/codex-worker" "${BIN_TARGET}/codex-worker"
ln -sfn "${ROOT_DIR}/bin/codex-review" "${BIN_TARGET}/codex-review"
ln -sfn "${ROOT_DIR}/.agents/skills/codex-delegate" "${SKILL_TARGET}/codex-delegate"

echo "已安装全局命令："
echo "  - ${BIN_TARGET}/codex-worker -> ${ROOT_DIR}/bin/codex-worker"
echo "  - ${BIN_TARGET}/codex-review -> ${ROOT_DIR}/bin/codex-review"
echo "已安装全局 Antigravity Skill："
echo "  - ${SKILL_TARGET}/codex-delegate -> ${ROOT_DIR}/.agents/skills/codex-delegate"
