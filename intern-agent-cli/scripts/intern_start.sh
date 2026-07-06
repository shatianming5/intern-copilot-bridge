#!/usr/bin/env bash
# ============================================================
# intern_start.sh — 启动（或附着到）一个 intern 的 Claude Code session
#
# 用法:
#   ./intern_start.sh <intern_name> [project_name]
#
# 假定:
#   - WORK_AGENTS_ROOT 指向当前 runtime root
#   - intern 工作目录由 session map / state-v1 runtime 记录解析
#   - scoped tmux session 名由 internctl 注入 INTERN_TMUX_SESSION
#
# 流程:
#   1. 检查 repo 中存在该 intern 的信息
#   2. 检查 tmux session 是否已存在
#   3. 如果存在 → 直接 attach
#   4. 如果不存在 → 初始化目录 + 安装 hooks + 启动 claude
# ============================================================

set -euo pipefail

# ── 常量 ──────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
WORK_ROOT="${WORK_AGENTS_ROOT:-/work-agents}"
RUNTIME_REPO="${INTERN_RUNTIME_REPO:-${REPO_ROOT}}"
PROJECT_NAME="${2:-axis_intern_agents}"
POLICY_ENV_FILE="${WORK_ROOT}/enterprise_policy/daemon/runtime/claude.env"

# Ensure ~/.local/bin is in PATH (Claude CLI installs there)
[[ ":${PATH}:" == *":${HOME}/.local/bin:"* ]] || export PATH="${HOME}/.local/bin:${PATH}"

# task205: 解析 Python 3 解释器 — conda-only 环境可能只有 $CONDA_PREFIX/bin/python
# 探测顺序：$PYTHON env → $CONDA_PREFIX/bin/python → python3 → python（仅 Python 3.x）
_resolve_python() {
    if [[ -n "${PYTHON:-}" ]] && command -v "${PYTHON}" >/dev/null 2>&1; then
        echo "${PYTHON}"; return 0
    fi
    if [[ -n "${CONDA_PREFIX:-}" && -x "${CONDA_PREFIX}/bin/python" ]]; then
        echo "${CONDA_PREFIX}/bin/python"; return 0
    fi
    if command -v python3 >/dev/null 2>&1; then
        command -v python3; return 0
    fi
    if command -v python >/dev/null 2>&1; then
        local ver
        ver="$(python --version 2>&1 | awk '{print $2}' | cut -d. -f1)"
        if [[ "${ver}" == "3" ]]; then
            command -v python; return 0
        fi
    fi
    echo "" ; return 1
}
PYTHON="$(_resolve_python)" || true
if [[ -z "${PYTHON}" ]]; then
    echo -e "${RED:-}[ERROR]${NC:-} No Python 3 interpreter found. Install python3 or activate a conda env first." >&2
    exit 1
fi
PYTHON_CANONICAL="$("${PYTHON}" -c 'import os, shutil, sys; raw=sys.argv[1]; print(os.path.realpath(shutil.which(raw) or raw))' "${PYTHON}" 2>/dev/null || true)"
[[ -n "${PYTHON_CANONICAL}" ]] && PYTHON="${PYTHON_CANONICAL}"
export PYTHON

# ── 颜色 ──────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
die()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }
shell_quote() { printf '%q' "$1"; }

ensure_feishu_group() {
    local intern_name="$1"
    local intern_type="$2"
    local project_name="${3:-}"
    local workspace_id="${4:-}"
    local daemon_addr="${FEISHU_DAEMON_ADDR_FILE:-/tmp/feishu_daemon.json}"

    if [[ ! -f "${daemon_addr}" ]]; then
        die "Feishu daemon address file not found: ${daemon_addr}"
    fi

    "${PYTHON}" - "${daemon_addr}" "${intern_name}" "${intern_type}" "${project_name}" "${workspace_id}" <<'PYEOF'
import json
import socket
import sys
import time
import urllib.error
import urllib.request

GROUP_CREATE_TIMEOUT_SECONDS = 60
GROUP_CREATE_ATTEMPTS = 2

addr_path, intern_name, intern_type, project_name, workspace_id = sys.argv[1:6]
try:
    with open(addr_path, "r", encoding="utf-8") as f:
        addr = json.load(f)
    port = int(addr["http_port"])
except Exception as exc:
    print(f"invalid daemon address file {addr_path}: {exc}", file=sys.stderr)
    sys.exit(1)

payload_data = {"intern_name": intern_name, "type": intern_type}
if project_name:
    payload_data["project"] = project_name
if workspace_id:
    payload_data["workspace_id"] = workspace_id
payload = json.dumps(payload_data).encode("utf-8")
url = f"http://127.0.0.1:{port}/api/group/create"

result = None
for attempt in range(1, GROUP_CREATE_ATTEMPTS + 1):
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=GROUP_CREATE_TIMEOUT_SECONDS) as resp:
            result = json.loads(resp.read().decode("utf-8") or "{}")
            break
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"daemon /api/group/create HTTP {exc.code}: {body}", file=sys.stderr)
        sys.exit(1)
    except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
        if attempt >= GROUP_CREATE_ATTEMPTS:
            print(f"daemon /api/group/create failed after {GROUP_CREATE_ATTEMPTS} attempts: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"daemon /api/group/create timed out, retrying ({attempt + 1}/{GROUP_CREATE_ATTEMPTS})", file=sys.stderr)
        time.sleep(2)
    except Exception as exc:
        print(f"daemon /api/group/create failed: {exc}", file=sys.stderr)
        sys.exit(1)

chat_id = result.get("chat_id")
if not chat_id:
    print(f"daemon /api/group/create returned no chat_id: {result}", file=sys.stderr)
    sys.exit(1)

print(chat_id)
PYEOF
}

is_root_user() {
    [[ "$(id -u)" -eq 0 ]]
}

is_container_environment() {
    if [[ "${IS_SANDBOX:-}" == "1" ]]; then
        return 0
    fi

    if command -v systemd-detect-virt >/dev/null 2>&1 && systemd-detect-virt -c >/dev/null 2>&1; then
        return 0
    fi

    if [[ -f "/.dockerenv" || -f "/run/.containerenv" ]]; then
        return 0
    fi

    if [[ -n "${container:-}" || -n "${KUBERNETES_SERVICE_HOST:-}" ]]; then
        return 0
    fi

    grep -qaE '(docker|containerd|kubepods|podman|lxc)' /proc/1/cgroup 2>/dev/null
}

should_enable_root_bypass() {
    is_root_user && is_container_environment
}

session_has_live_process() {
    local session_name="$1"
    local current_command=""

    current_command="$(tmux list-panes -t "=${session_name}" -F '#{pane_current_command}' 2>/dev/null | head -n 1 | tr '[:upper:]' '[:lower:]' | tr -d '\r')"

    case "${current_command}" in
        ""|bash|sh|zsh|fish|tmux)
            return 1
            ;;
        *)
            return 0
            ;;
    esac
}

get_tmux_env_value() {
    local session_name="$1"
    local var_name="$2"
    tmux show-environment -t "=${session_name}" 2>/dev/null | sed -n "s/^${var_name}=//p" | tail -n 1
}

source_enterprise_user_env() {
    local user_env_file="${WORK_ROOT}/enterprise_policy/daemon/user.env"
    if [[ -f "${user_env_file}" ]]; then
        set -a
        # shellcheck source=/dev/null
        . "${user_env_file}"
        set +a
    fi
}

source_policy_runtime_env() {
    if [[ -f "${POLICY_ENV_FILE}" ]]; then
        # shellcheck source=/dev/null
        . "${POLICY_ENV_FILE}"
    fi
}

policy_env_manages_key() {
    local key="$1"
    [[ -f "${POLICY_ENV_FILE}" ]] || return 1
    grep -E '^# managed_env_keys:' "${POLICY_ENV_FILE}" 2>/dev/null | tr ' ' '\n' | grep -Fxq "${key}"
}

policy_env_has_managed_keys() {
    local managed=""
    [[ -f "${POLICY_ENV_FILE}" ]] || return 1
    managed="$(sed -n 's/^# managed_env_keys:[[:space:]]*//p' "${POLICY_ENV_FILE}" 2>/dev/null | head -n 1 || true)"
    [[ -n "$(tr -d '[:space:]' <<<"${managed}")" ]]
}

launch_env_file_keys() {
    local file_path="$1"
    local line=""
    [[ -f "${file_path}" ]] || return 0
    while IFS= read -r line; do
        line="${line#"${line%%[![:space:]]*}"}"
        [[ -z "${line}" ]] && continue
        if [[ "${line}" =~ ^#[[:space:]]managed_env_keys:[[:space:]]*(.*)$ ]]; then
            tr ' ' '\n' <<<"${BASH_REMATCH[1]}" | grep -E '^[A-Za-z_][A-Za-z0-9_]*$' | grep -vx 'PATH' || true
            continue
        fi
        [[ "${line}" == \#* ]] && continue
        if [[ "${line}" =~ ^unset[[:space:]]+([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*$ ]]; then
            [[ "${BASH_REMATCH[1]}" == "PATH" ]] && continue
            printf '%s\n' "${BASH_REMATCH[1]}"
            continue
        fi
        [[ "${line}" =~ ^export[[:space:]]+(.+)$ ]] && line="${BASH_REMATCH[1]}"
        if [[ "${line}" =~ ^([A-Za-z_][A-Za-z0-9_]*)= ]]; then
            [[ "${BASH_REMATCH[1]}" == "PATH" ]] && continue
            printf '%s\n' "${BASH_REMATCH[1]}"
        fi
    done < "${file_path}"
}

launch_env_key_matches_tmux() {
    local session_name="$1"
    local key="$2"
    local expected="${!key-}"
    local actual=""
    actual="$(get_tmux_env_value "${session_name}" "${key}")"
    [[ "${actual}" == "${expected}" ]]
}

sync_launch_env_files_to_tmux() {
    local session_name="$1"
    local key=""
    local cli_root=""
    cli_root="$(cd "${SCRIPT_DIR}/.." && pwd)"
    if WORK_AGENTS_ROOT="${WORK_ROOT}" PYTHONPATH="${cli_root}:${PYTHONPATH:-}" \
        "${PYTHON}" -m lib.session_launch_spec tmux-sync \
        --work-root "${WORK_ROOT}" --provider claude --session "${session_name}" >/dev/null 2>&1; then
        return 0
    fi
    for key in $(launch_env_file_keys "${WORK_ROOT}/enterprise_policy/daemon/user.env"); do
        if [[ -n "${!key+x}" ]]; then
            tmux set-environment -t "=${session_name}" "${key}" "${!key-}"
        else
            tmux set-environment -u -t "=${session_name}" "${key}" 2>/dev/null || true
        fi
    done
    for key in $(launch_env_file_keys "${POLICY_ENV_FILE}"); do
        if [[ -n "${!key+x}" ]]; then
            tmux set-environment -t "=${session_name}" "${key}" "${!key-}"
        else
            tmux set-environment -u -t "=${session_name}" "${key}" 2>/dev/null || true
        fi
    done
}

policy_runtime_matches_tmux() {
    local session_name="$1"
    local key=""
    [[ "$(get_tmux_env_value "${session_name}" "INTERN_CLAUDE_DEFAULT_ARGS")" == "${CLAUDE_DEFAULT_ARGS}" ]] || return 1
    for key in $(launch_env_file_keys "${WORK_ROOT}/enterprise_policy/daemon/user.env"); do
        launch_env_key_matches_tmux "${session_name}" "${key}" || return 1
    done
    for key in $(launch_env_file_keys "${POLICY_ENV_FILE}"); do
        [[ "${key}" == "CLAUDE_POLICY_ENV_HASH" ]] && continue
        launch_env_key_matches_tmux "${session_name}" "${key}" || return 1
    done
    [[ "$(get_tmux_env_value "${session_name}" "INTERN_DIR")" == "${INTERN_DIR}" ]] || return 1
    [[ "$(get_tmux_env_value "${session_name}" "PROJECT_REPO")" == "${INTERN_REPO}" ]] || return 1
    [[ "$(get_tmux_env_value "${session_name}" "WORK_AGENTS_ROOT")" == "${WORK_ROOT}" ]] || return 1
    [[ "$(get_tmux_env_value "${session_name}" "PROJECT_NAME")" == "${PROJECT_NAME}" ]] || return 1
    [[ "$(get_tmux_env_value "${session_name}" "INTERN_WORKSPACE_ID")" == "${INTERN_WORKSPACE_ID:-}" ]] || return 1
    [[ "$(get_tmux_env_value "${session_name}" "FEISHU_DAEMON_ADDR_FILE")" == "${FEISHU_DAEMON_ADDR_FILE:-/tmp/feishu_daemon.json}" ]] || return 1
    [[ "$(get_tmux_env_value "${session_name}" "INTERN_NAME")" == "${INTERN_NAME}" ]] || return 1
    [[ "$(get_tmux_env_value "${session_name}" "INTERN_TMUX_SESSION")" == "${TMUX_SESSION}" ]] || return 1
    [[ "$(get_tmux_env_value "${session_name}" "INTERN_TMUX_READY_CHANNEL")" == "${TMUX_READY_CHANNEL}" ]] || return 1
    [[ "$(get_tmux_env_value "${session_name}" "INTERN_REAL_CLAUDE")" == "${REAL_CLAUDE_BIN}" ]] || return 1
    [[ "$(get_tmux_env_value "${session_name}" "INTERN_RUNTIME_REPO")" == "${RUNTIME_REPO}" ]] || return 1
    return 0
}

resolve_claude_command() {
    local help_output=""
    local claude_bin="${REAL_CLAUDE_BIN:-claude}"

    help_output="$("${claude_bin}" --help 2>/dev/null || true)"
    if grep -q -- '--permission-mode' <<<"${help_output}"; then
        if is_root_user && ! should_enable_root_bypass; then
            echo -e "${YELLOW}[WARN]${NC} Detected root without sandbox/container markers; falling back to acceptEdits because bypassPermissions is rejected by Claude in this mode." >&2
            echo "claude --permission-mode acceptEdits"
            return
        fi
        echo "claude --permission-mode bypassPermissions"
        return
    fi
    if ! is_root_user && grep -q -- '--dangerously-skip-permissions' <<<"${help_output}"; then
        echo "claude --dangerously-skip-permissions"
        return
    fi
    echo "claude"
}

resolve_claude_default_args() {
    local command_line=""
    if [[ -n "${INTERN_CLAUDE_POLICY_ARGS:-}" ]]; then
        echo "${INTERN_CLAUDE_POLICY_ARGS}"
        return
    fi
    command_line="$(resolve_claude_command)"
    if [[ "${command_line}" == "claude" ]]; then
        echo ""
        return
    fi
    echo "${command_line#claude }"
}

strip_path_entry() {
    local remove="$1"
    local path_value="$2"
    local result=""
    local part=""
    IFS=':' read -r -a _path_parts <<< "${path_value}"
    for part in "${_path_parts[@]}"; do
        [[ -z "${part}" || "${part}" == "${remove}" ]] && continue
        if [[ -z "${result}" ]]; then
            result="${part}"
        else
            result="${result}:${part}"
        fi
    done
    echo "${result}"
}

resolve_real_claude_binary() {
    local wrapper_dir="${INTERN_DIR}/.intern/bin"
    local real_claude=""
    local search_path=""

    search_path="$(strip_path_entry "${wrapper_dir}" "${PATH}")"
    real_claude="$(PATH="${search_path}" command -v claude || true)"
    if [[ -z "${real_claude}" ]]; then
        die "claude executable not found in PATH"
    fi
    if [[ "${real_claude}" == "${wrapper_dir}/claude" ]]; then
        die "resolved claude points to managed wrapper; cannot determine real Claude binary"
    fi
    REAL_CLAUDE_BIN="${real_claude}"
    ok "Resolved real Claude binary: ${REAL_CLAUDE_BIN}"
}

# ── 参数检查 ──────────────────────────────────
if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <intern_name> [project_name]"
    echo ""
    echo "  启动（或附着到）一个 intern 的 Claude Code session。"
    echo ""
    echo "  project_name 默认为 axis_intern_agents。"
    echo "  代码仓库路径由企业 state-v1 resolver 通过 INTERN_CODE_REPO_PATH 提供。"
    echo ""
    echo "Examples:"
    echo "  $0 intern_rule_alice"
    echo "  $0 intern_rule_bob axis_vla"
    exit 1
fi

INTERN_NAME="$1"

# 安全检查：intern name 白名单
if ! [[ "$INTERN_NAME" =~ ^[a-zA-Z0-9_-]+$ ]]; then
    die "Invalid intern name: '$INTERN_NAME' (must be [a-zA-Z0-9_-]+)"
fi
TMUX_SESSION="${INTERN_TMUX_SESSION:-}"
if [[ -z "${TMUX_SESSION}" ]]; then
    die "INTERN_TMUX_SESSION is required; start sessions through internctl session start"
fi
TMUX_READY_CHANNEL="${INTERN_TMUX_READY_CHANNEL:-session_ready_${TMUX_SESSION}}"

if [[ -z "${INTERN_DIR:-}" ]]; then
    die "INTERN_DIR is required; start sessions through the state-v1 resolver"
fi
if [[ -z "${INTERN_CODE_REPO_PATH:-}" ]]; then
    die "INTERN_CODE_REPO_PATH is required; start sessions through the metadata resolver"
fi
if [[ -z "${INTERN_METADATA_INTERN_DIR:-}" ]]; then
    die "INTERN_METADATA_INTERN_DIR is required; start sessions through the enterprise metadata resolver"
fi
if [[ -z "${INTERN_SESSION_REGISTRY_KEY:-}" ]]; then
    die "INTERN_SESSION_REGISTRY_KEY is required; start sessions through internctl session start"
fi
if [[ -z "${INTERN_WORKSPACE_ID:-}" ]]; then
    die "INTERN_WORKSPACE_ID is required; start sessions through enterprise workspace state"
fi
INTERN_REPO="${INTERN_CODE_REPO_PATH}"
INTERN_WS="${INTERN_METADATA_INTERN_DIR}"

# ============================================================
# Step 1: 检查 intern 在 repo 中是否存在
# ============================================================
info "Step 1: 检查 intern '${INTERN_NAME}' 是否存在于 repo..."

if [[ ! -d "${RUNTIME_REPO}" ]]; then
    die "Runtime repo not found: ${RUNTIME_REPO}"
fi

MASTER_INTERN_DIR="${INTERN_METADATA_INTERN_DIR}"
if [[ ! -d "${MASTER_INTERN_DIR}" ]]; then
    die "Intern '${INTERN_NAME}' not found in project '${PROJECT_NAME}' (missing ${MASTER_INTERN_DIR})"
fi

# 检查 status.md 存在
if [[ ! -f "${MASTER_INTERN_DIR}/status.md" ]]; then
    die "Intern '${INTERN_NAME}' has no status.md (${MASTER_INTERN_DIR}/status.md)"
fi

ok "Intern '${INTERN_NAME}' found in repo."

# ============================================================
# Step 2: 检查 tmux session 是否存在
# ============================================================
info "Step 2: 检查 tmux session '${TMUX_SESSION}'..."

SESSION_EXISTS=0
PROCESS_RUNNING=0

if tmux has-session -t "=${TMUX_SESSION}" 2>/dev/null; then
    SESSION_EXISTS=1
    if session_has_live_process "${TMUX_SESSION}"; then
        PROCESS_RUNNING=1
        info "tmux session '${TMUX_SESSION}' exists and Claude is running. Checking runtime config..."
    fi
    if [[ "${PROCESS_RUNNING}" -eq 0 ]]; then
        warn "tmux session '${TMUX_SESSION}' exists, but Claude is not running. Reusing the session..."
    fi
else
    info "tmux session '${TMUX_SESSION}' not found. Creating new session..."
fi

# ============================================================
# Step 3: 初始化 intern 工作目录
# ============================================================
info "Step 3: 初始化 intern 工作目录 ${INTERN_DIR}..."

# 创建基本目录
mkdir -p "${INTERN_DIR}"
mkdir -p "${INTERN_DIR}/debug"
mkdir -p "${INTERN_DIR}/outputs"

# 使用企业 resolver 指定的代码仓库；session 启动不再创建本地 clone。
if [[ ! -d "${INTERN_REPO}" ]]; then
    die "Workspace code repo not found: ${INTERN_REPO}"
fi
info "  Using workspace code repo: ${INTERN_REPO}"
if [[ -d "${INTERN_REPO}/.git" ]]; then
    info "  Repo already exists, running PR-aware checkout..."
    bash "${SCRIPT_DIR}/intern_checkout_pr.sh" "${INTERN_NAME}" "${INTERN_REPO}"
    if [[ -f "${INTERN_REPO}/.gitmodules" ]]; then
        info "  Updating submodules..."
        (cd "${INTERN_REPO}" && git submodule update --init --recursive)
    fi
fi

ok "Repo ready at ${INTERN_REPO}"

# 写入 .hook_state.json（hooks 依赖 project 字段定位代码仓库）
_HOOK_STATE="${INTERN_DIR}/.hook_state.json"
if [[ ! -f "${_HOOK_STATE}" ]] || ! "${PYTHON}" -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d.get('project') else 1)" "${_HOOK_STATE}" 2>/dev/null; then
    "${PYTHON}" -c "
import json, sys, os
p = sys.argv[1]; proj = sys.argv[2]
d = {}
if os.path.exists(p):
    try: d = json.load(open(p))
    except: pass
d['project'] = proj
with open(p + '.tmp', 'w') as f: json.dump(d, f, ensure_ascii=False, indent=2)
os.rename(p + '.tmp', p)
" "${_HOOK_STATE}" "${PROJECT_NAME}"
    info "  wrote .hook_state.json (project=${PROJECT_NAME})"
fi

# ============================================================
# Step 3.4: 确保飞书群存在
# ============================================================
info "Step 3.4: 确保飞书群存在..."
CHAT_ID="$(ensure_feishu_group "${INTERN_NAME}" "claude" "${PROJECT_NAME}" "${INTERN_WORKSPACE_ID:-}")"
ok "Feishu group ready: ${CHAT_ID}"

# ============================================================
# Step 3.5: 预填充 Claude 用户配置（仅首次）
# ============================================================
# hasCompletedOnboarding 必须写入 ~/.claude.json（Claude Code 读取的位置）
# model / skipDangerousModePermissionPrompt 写入 ~/.claude/settings.json
CLAUDE_CONFIG="${HOME}/.claude.json"
if ! "${PYTHON}" -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d.get('hasCompletedOnboarding') else 1)" "${CLAUDE_CONFIG}" 2>/dev/null; then
    info "Step 3.5: 写入 hasCompletedOnboarding 到 ~/.claude.json..."
    if [[ -f "${CLAUDE_CONFIG}" ]]; then
        "${PYTHON}" -c "
import json, sys
p = sys.argv[1]
try:
    d = json.load(open(p))
except:
    d = {}
d['hasCompletedOnboarding'] = True
json.dump(d, open(p, 'w'), indent=2)
print()
" "${CLAUDE_CONFIG}"
    else
        echo '{"hasCompletedOnboarding": true}' > "${CLAUDE_CONFIG}"
    fi
    ok "hasCompletedOnboarding written to ${CLAUDE_CONFIG}"
else
    info "Step 3.5: ~/.claude.json already has hasCompletedOnboarding, skipping."
fi
USER_CLAUDE_SETTINGS="${HOME}/.claude/settings.json"
# Merge mode: always ensure required fields exist (don't skip if file exists)
info "Step 3.5: 确保 ~/.claude/settings.json 包含必要字段..."
mkdir -p "${HOME}/.claude"
"${PYTHON}" -c "
import json, sys, os
p = sys.argv[1]
try:
    d = json.load(open(p))
except:
    d = {}
changed = False
if not d.get('skipDangerousModePermissionPrompt'):
    d['skipDangerousModePermissionPrompt'] = True
    changed = True
if not d.get('model'):
    d['model'] = 'opus'
    changed = True
if changed:
    with open(p, 'w') as f:
        json.dump(d, f, indent=2)
    print('updated: ' + ', '.join(k for k in ('skipDangerousModePermissionPrompt', 'model') if k in d))
else:
    print('all fields present')
" "${USER_CLAUDE_SETTINGS}"
ok "User-level settings ready at ${USER_CLAUDE_SETTINGS}"

# Pre-accept workspace trust dialog for the intern directory
# Claude stores per-directory trust in ~/.claude.json under projects.<path>.hasTrustDialogAccepted
"${PYTHON}" -c "
import json, sys, os
config_path = sys.argv[1]
intern_dir = sys.argv[2]
try:
    d = json.load(open(config_path))
except:
    d = {}
projects = d.setdefault('projects', {})
entry = projects.setdefault(intern_dir, {})
if not entry.get('hasTrustDialogAccepted'):
    entry['hasTrustDialogAccepted'] = True
    with open(config_path, 'w') as f:
        json.dump(d, f, indent=2)
    print('trust accepted for ' + intern_dir)
" "${CLAUDE_CONFIG}" "${INTERN_DIR}" && ok "Workspace trust pre-accepted for ${INTERN_DIR}" || true

# ============================================================
# Step 4: 确保 .claude/settings.json 存在
# ============================================================
info "Step 4: 确保 Claude Code hooks 配置存在..."

CLAUDE_DIR="${INTERN_DIR}/.claude"
mkdir -p "${CLAUDE_DIR}"

# settings.json 是通用静态文件（不含 per-intern 数据），软链接到企业 hooks 模板。
# hook 命令通过 $WORK_AGENTS_ROOT 环境变量定位 hooks 脚本（Step 5 中 export）。
# hooks Python 代码通过 os.environ.get() 读取 INTERN_DIR/PROJECT_REPO/WORK_AGENTS_ROOT。
# 使用软链接确保 hook 配置随代码更新自动同步。
CLAUDE_SETTINGS_TEMPLATE="${WORK_ROOT}/.github/claude_settings.json"
if [[ ! -f "${CLAUDE_SETTINGS_TEMPLATE}" ]]; then
    die "Claude settings template not found: ${CLAUDE_SETTINGS_TEMPLATE}"
fi
# 如果已存在普通文件（旧版遗留），先删除再建软链接
if [[ -f "${CLAUDE_DIR}/settings.json" && ! -L "${CLAUDE_DIR}/settings.json" ]]; then
    warn "  Replacing plain settings.json with symlink to template..."
    rm -f "${CLAUDE_DIR}/settings.json"
fi
ln -sf "${CLAUDE_SETTINGS_TEMPLATE}" "${CLAUDE_DIR}/settings.json"

ok "Hooks config ready at ${CLAUDE_DIR}/settings.json"
source_enterprise_user_env
source_policy_runtime_env
if policy_env_has_managed_keys; then
    ACTIVE_POLICY_ENV_HASH="${CLAUDE_POLICY_ENV_HASH:-}"
else
    ACTIVE_POLICY_ENV_HASH=""
fi

# ============================================================
# Step 4.5: 注册 intern 类型为 claude（.intern_sessions.json）
# ============================================================
info "Step 4.5: 注册 intern 类型为 claude..."

"${PYTHON}" -c "
import json, os, fcntl
map_file = '${WORK_ROOT}/.intern_sessions.json'
lock_file = '${WORK_ROOT}/.intern_sessions.lock'
fd = open(lock_file, 'w')
fcntl.flock(fd, fcntl.LOCK_EX)
try:
    data = json.load(open(map_file)) if os.path.exists(map_file) else {}
    key = os.environ['INTERN_SESSION_REGISTRY_KEY']
    entry = data.get(key, {})
    if not isinstance(entry, dict):
        entry = {}
    entry['type'] = 'claude'
    entry['intern_name'] = '${INTERN_NAME}'
    entry['project'] = '${PROJECT_NAME}'
    if os.environ.get('INTERN_DIR'):
        entry['intern_dir'] = os.environ['INTERN_DIR']
    entry['workspace_id'] = os.environ['INTERN_WORKSPACE_ID']
    entry['tmux_session'] = os.environ['INTERN_TMUX_SESSION']
    data[key] = entry
    tmp = map_file + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    os.rename(tmp, map_file)
finally:
    fcntl.flock(fd, fcntl.LOCK_UN)
    fd.close()
"

ok "Registered type=claude for ${INTERN_NAME}"

SETTINGS_MTIME="$(stat -c %Y "${CLAUDE_DIR}/settings.json" 2>/dev/null || echo 0)"

resolve_real_claude_binary
CLAUDE_DEFAULT_ARGS="$(resolve_claude_default_args)"

attach_or_detach_session() {
    if [[ "${INTERN_START_NO_ATTACH:-0}" == "1" ]]; then
        info "INTERN_START_NO_ATTACH=1; leaving session detached."
        exit 0
    fi
    info ""
    info "Attaching to tmux session..."
    exec tmux attach-session -t "=${TMUX_SESSION}"
}

if [[ "${SESSION_EXISTS}" -eq 1 && "${PROCESS_RUNNING}" -eq 1 ]]; then
    APPLIED_SETTINGS_MTIME="$(get_tmux_env_value "${TMUX_SESSION}" "CLAUDE_SETTINGS_MTIME")"
    APPLIED_RUNTIME_REPO="$(get_tmux_env_value "${TMUX_SESSION}" "INTERN_RUNTIME_REPO")"
    if [[ "${INTERN_SESSION_FORCE_RESTART:-0}" != "1" && -n "${APPLIED_SETTINGS_MTIME}" && "${APPLIED_SETTINGS_MTIME}" == "${SETTINGS_MTIME}" && "${APPLIED_RUNTIME_REPO}" == "${RUNTIME_REPO}" ]] && policy_runtime_matches_tmux "${TMUX_SESSION}"; then
        ok "tmux session '${TMUX_SESSION}' exists and Claude is already running with current hook/runtime config. Attaching..."
        attach_or_detach_session
    fi
    if [[ "${INTERN_SESSION_FORCE_RESTART:-0}" == "1" ]]; then
        warn "tmux session '${TMUX_SESSION}' exists; force restart requested."
    else
        warn "tmux session '${TMUX_SESSION}' exists, but Claude is using stale hook/runtime config. Restarting in place..."
    fi
fi

# ============================================================
# Step 4.7: 同步 Skill 农场（task186）
# ============================================================
# 重建 ${INTERN_DIR}/.claude/skills/ 以反映 .intern_skill.json 的最新启用列表。
# 失败不阻断启动（保留上一次成功状态），错误写到 stderr 进 log。
SKILL_SYNC_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/internctl.py"
CLAUDE_RESUME_COMMAND="$(printf '%q ' "${PYTHON}" "${SKILL_SYNC_SCRIPT}" session resume "${INTERN_NAME}" --project "${PROJECT_NAME}" --type claude)"
clear_claude_session_uuid() {
    if [[ -f "${SKILL_SYNC_SCRIPT}" ]]; then
        WORK_AGENTS_ROOT="${WORK_ROOT}" "${PYTHON}" "${SKILL_SYNC_SCRIPT}" \
            session clear-claude-session "${INTERN_NAME}" --project "${PROJECT_NAME}" \
            >/dev/null 2>&1 || true
    fi
}
capture_claude_session_uuid() {
    local since="${1:-0}"
    if [[ -f "${SKILL_SYNC_SCRIPT}" ]]; then
        WORK_AGENTS_ROOT="${WORK_ROOT}" "${PYTHON}" "${SKILL_SYNC_SCRIPT}" \
            session capture-claude-session "${INTERN_NAME}" --project "${PROJECT_NAME}" --timeout 90 --since "${since}" \
            >/dev/null 2>&1 &
    fi
}
if [[ -f "${SKILL_SYNC_SCRIPT}" ]]; then
    info "Step 4.7: 同步 Skill 农场..."
    WORK_AGENTS_ROOT="${WORK_ROOT}" "${PYTHON}" "${SKILL_SYNC_SCRIPT}" skill sync "${INTERN_NAME}" --project "${PROJECT_NAME}" >/dev/null 2>&1 || \
        warn "skill sync 失败（不阻断启动），可手动执行 'internctl skill sync ${INTERN_NAME} --project ${PROJECT_NAME}' 排查"
fi

# ============================================================
# Step 5: 创建 tmux session 并启动 Claude
# ============================================================
CLAUDE_COMMAND_ARGS="${CLAUDE_DEFAULT_ARGS}"

if [[ "${SESSION_EXISTS}" -eq 1 ]]; then
    info "Step 5: 在现有 tmux session '${TMUX_SESSION}' 中重启 Claude..."
    # 动态解析实际 pane target——历史 session 的唯一 window 可能不是 0（task253）
    PANE_INDEX="$(tmux list-panes -s -t "=${TMUX_SESSION}" -F '#{window_index}.#{pane_index}' 2>/dev/null | head -n1)"
    if [[ -z "${PANE_INDEX}" ]]; then
        die "无法解析 tmux session '${TMUX_SESSION}' 的 pane target（list-panes 返回空）"
    fi
    # Use a fast, RC-free bash so the pane shell is ready immediately; a slow
    # interactive zsh/conda init would race the send-keys below and garble them.
    tmux respawn-pane -k -t "=${TMUX_SESSION}:${PANE_INDEX}" -c "${INTERN_DIR}" bash --noprofile --norc
else
    info "Step 5: 创建 tmux session '${TMUX_SESSION}' 并启动 Claude..."
    # Use a fast, RC-free bash so the pane shell is ready immediately; a slow
    # interactive zsh/conda init would race the send-keys below and garble them.
    tmux new-session -d -s "${TMUX_SESSION}" -c "${INTERN_DIR}" bash --noprofile --norc
fi

# Signal VS Code extension that tmux session is ready (event-driven startup)
tmux wait-for -S "${TMUX_READY_CHANNEL}" 2>/dev/null || true

# 设置环境变量（在 tmux session 内）
tmux send-keys -t "=${TMUX_SESSION}:" "source ~/.bashrc 2>/dev/null" Enter
tmux send-keys -t "=${TMUX_SESSION}:" "set -a; [ -f \"${WORK_ROOT}/enterprise_policy/daemon/user.env\" ] && . \"${WORK_ROOT}/enterprise_policy/daemon/user.env\"; [ -f \"${POLICY_ENV_FILE}\" ] && . \"${POLICY_ENV_FILE}\"; set +a" Enter
sync_launch_env_files_to_tmux "${TMUX_SESSION}"
tmux send-keys -t "=${TMUX_SESSION}:" "export INTERN_DIR=\"${INTERN_DIR}\"" Enter
tmux send-keys -t "=${TMUX_SESSION}:" "export PROJECT_REPO=\"${INTERN_REPO}\"" Enter
tmux send-keys -t "=${TMUX_SESSION}:" "export WORK_AGENTS_ROOT=\"${WORK_ROOT}\"" Enter
tmux send-keys -t "=${TMUX_SESSION}:" "export PROJECT_NAME=\"${PROJECT_NAME}\"" Enter
tmux send-keys -t "=${TMUX_SESSION}:" "export INTERN_WORKSPACE_ID=\"${INTERN_WORKSPACE_ID:-}\"" Enter
tmux send-keys -t "=${TMUX_SESSION}:" "export FEISHU_DAEMON_ADDR_FILE=\"${FEISHU_DAEMON_ADDR_FILE:-/tmp/feishu_daemon.json}\"" Enter
tmux send-keys -t "=${TMUX_SESSION}:" "export INTERN_NAME=\"${INTERN_NAME}\"" Enter
tmux send-keys -t "=${TMUX_SESSION}:" "export INTERN_TMUX_SESSION=\"${TMUX_SESSION}\"" Enter
tmux send-keys -t "=${TMUX_SESSION}:" "export INTERN_TMUX_READY_CHANNEL=\"${TMUX_READY_CHANNEL}\"" Enter
tmux send-keys -t "=${TMUX_SESSION}:" "export INTERN_REAL_CLAUDE=\"${REAL_CLAUDE_BIN}\"" Enter
tmux send-keys -t "=${TMUX_SESSION}:" "export INTERN_CLAUDE_DEFAULT_ARGS=$(shell_quote "${CLAUDE_DEFAULT_ARGS}")" Enter
tmux send-keys -t "=${TMUX_SESSION}:" "export INTERN_CTL_PYTHON=\"${PYTHON}\"" Enter
tmux send-keys -t "=${TMUX_SESSION}:" "export INTERN_CTL_PATH=\"${SKILL_SYNC_SCRIPT}\"" Enter
tmux set-environment -t "=${TMUX_SESSION}" INTERN_DIR "${INTERN_DIR}"
tmux set-environment -t "=${TMUX_SESSION}" PROJECT_REPO "${INTERN_REPO}"
tmux set-environment -t "=${TMUX_SESSION}" WORK_AGENTS_ROOT "${WORK_ROOT}"
tmux set-environment -t "=${TMUX_SESSION}" PROJECT_NAME "${PROJECT_NAME}"
tmux set-environment -t "=${TMUX_SESSION}" INTERN_WORKSPACE_ID "${INTERN_WORKSPACE_ID:-}"
tmux set-environment -t "=${TMUX_SESSION}" FEISHU_DAEMON_ADDR_FILE "${FEISHU_DAEMON_ADDR_FILE:-/tmp/feishu_daemon.json}"
tmux set-environment -t "=${TMUX_SESSION}" INTERN_NAME "${INTERN_NAME}"
tmux set-environment -t "=${TMUX_SESSION}" INTERN_TMUX_SESSION "${TMUX_SESSION}"
tmux set-environment -t "=${TMUX_SESSION}" INTERN_TMUX_READY_CHANNEL "${TMUX_READY_CHANNEL}"
tmux set-environment -t "=${TMUX_SESSION}" INTERN_REAL_CLAUDE "${REAL_CLAUDE_BIN}"
tmux set-environment -t "=${TMUX_SESSION}" INTERN_CLAUDE_DEFAULT_ARGS "${CLAUDE_DEFAULT_ARGS}"
tmux set-environment -t "=${TMUX_SESSION}" INTERN_CTL_PYTHON "${PYTHON}"
tmux set-environment -t "=${TMUX_SESSION}" INTERN_CTL_PATH "${SKILL_SYNC_SCRIPT}"
tmux set-environment -t "=${TMUX_SESSION}" INTERN_RUNTIME_REPO "${RUNTIME_REPO}"
tmux set-environment -t "=${TMUX_SESSION}" CLAUDE_SETTINGS_MTIME "${SETTINGS_MTIME}"
tmux set-environment -t "=${TMUX_SESSION}" CLAUDE_POLICY_ENV_HASH "${ACTIVE_POLICY_ENV_HASH:-}"

if should_enable_root_bypass; then
    tmux send-keys -t "=${TMUX_SESSION}:" "export IS_SANDBOX=1" Enter
fi

# 禁用 Claude CLI 的 experimental beta headers（Bedrock 代理不支持）
if policy_env_manages_key "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"; then
    info "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS is managed by enterprise policy."
else
    tmux send-keys -t "=${TMUX_SESSION}:" "export CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1" Enter
    tmux send-keys -t "=${TMUX_SESSION}:" "export INTERN_CLAUDE_DISABLE_EXPERIMENTAL_BETAS=1" Enter
    tmux set-environment -t "=${TMUX_SESSION}" INTERN_CLAUDE_DISABLE_EXPERIMENTAL_BETAS "1"
fi

# 启动 Claude Code CLI
# root/sudo 下只有在容器/沙箱环境显式带 IS_SANDBOX=1 时，Claude 才允许 bypassPermissions。
# 命令链：Claude 退出后自动通知 daemon → relay 下线（正常 /exit 场景）并打印 internctl resume 命令
# Daemon 端口动态：从 /tmp/feishu_daemon.json 读，daemon 不在则跳过 offline 通知（无所谓，daemon 自己会感知）
DAEMON_HTTP_PORT="$("${PYTHON}" -c 'import json; print(json.load(open("/tmp/feishu_daemon.json"))["http_port"])' 2>/dev/null || echo "")"
if [ -n "${DAEMON_HTTP_PORT}" ]; then
    OFFLINE_NOTIFY="curl -s -X POST http://localhost:${DAEMON_HTTP_PORT}/api/intern/offline -H 'Content-Type: application/json' -d '{\"intern_name\":\"${INTERN_NAME}\",\"project\":\"${PROJECT_NAME}\",\"workspace_id\":\"${INTERN_WORKSPACE_ID:-}\"}' > /dev/null 2>&1"
else
    OFFLINE_NOTIFY="true"
fi
request_light_refresh() {
    if [ -n "${DAEMON_HTTP_PORT}" ]; then
        curl -s -X POST "http://localhost:${DAEMON_HTTP_PORT}/api/intern/request_refresh" \
            -H 'Content-Type: application/json' \
            -d "{\"intern_name\":\"${INTERN_NAME}\",\"project\":\"${PROJECT_NAME}\",\"workspace_id\":\"${INTERN_WORKSPACE_ID:-}\"}" > /dev/null 2>&1 || true
    fi
}
CLAUDE_COMMAND="$(shell_quote "${REAL_CLAUDE_BIN}") ${CLAUDE_COMMAND_ARGS}; status=\$?; ${OFFLINE_NOTIFY}; echo; echo \"[intern] Claude exited with status \$status.\"; echo \"[intern] Resume this intern:\"; echo $(shell_quote "  ${CLAUDE_RESUME_COMMAND}"); exec bash -l"
info "Using Claude launch command: ${REAL_CLAUDE_BIN} ${CLAUDE_COMMAND_ARGS}"
clear_claude_session_uuid
CLAUDE_CAPTURE_SINCE="$(date +%s.%N 2>/dev/null || date +%s)"
tmux send-keys -t "=${TMUX_SESSION}:" "${CLAUDE_COMMAND}" Enter
capture_claude_session_uuid "${CLAUDE_CAPTURE_SINCE}"

ok "Claude session started in tmux '${TMUX_SESSION}'."
request_light_refresh
attach_or_detach_session
