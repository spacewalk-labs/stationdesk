#!/usr/bin/env bash
# Official vendor installers; each failure leaves the other installations running.
set -uo pipefail
export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$PATH"
failed=()
for agent in claude codex opencode agy; do
    # Vendor installers can reject an existing installation (agy self-updates).
    # Keep working agents on Retry; retry the installer for missing/broken ones.
    if command -v "$agent" >/dev/null 2>&1 && "$agent" --version >/dev/null 2>&1; then
        continue
    fi
    case "$agent" in
        claude) url=https://claude.ai/install.sh; shell=bash ;;
        codex) url=https://chatgpt.com/codex/install.sh; shell="sh" ;;
        opencode) url=https://opencode.ai/install; shell=bash ;;
        agy) url=https://antigravity.google/cli/install.sh; shell=bash ;;
    esac
    if ! curl -fsSL "$url" | "$shell"; then
        failed+=("$agent")
    fi
done
if ((${#failed[@]})); then
    printf 'Agent installation failed: %s\n' "${failed[*]}" >&2
    exit 1
fi
