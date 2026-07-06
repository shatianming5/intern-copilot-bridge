#!/usr/bin/env bash
# ============================================================
# intern_start_codex.sh — 启动（或附着到）一个 intern 的 Codex CLI session
#
# 用法:
#   ./intern_start_codex.sh <intern_name> [project_name]
#
# 与 intern_start.sh（Claude 版）的差异：
#   - 启动 `codex` 而非 `claude`
#   - 跳过权限：`codex --dangerously-bypass-approvals-and-sandbox`（别名 --yolo）
#   - 配置：~/.codex/config.toml（用户级）+ <intern_dir>/.codex/config.toml（项目级 symlink）
#   - 项目 trust：必须在 ~/.codex/config.toml 中写 [projects."<intern_dir>"] trust_level="trusted"
#                 否则 intern 的项目级 hooks 配置不会被加载
#   - intern_sessions.json 中 type 写 'codex'
#   - 不写 ~/.claude.json hasCompletedOnboarding（Codex 无此概念）
# ============================================================

set -euo pipefail

# ── 常量 ──────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
WORK_ROOT="${WORK_AGENTS_ROOT:-/work-agents}"
RUNTIME_REPO="${INTERN_RUNTIME_REPO:-${REPO_ROOT}}"
PROJECT_NAME="${2:-axis_intern_agents}"
POLICY_ENV_FILE="${WORK_ROOT}/enterprise_policy/daemon/runtime/codex.env"

# Ensure common Codex install locations are in PATH before hook trust sync.
for candidate_bin in "${HOME}/.local/bin" "/usr/local/bin" "/usr/bin" "/bin"; do
    [[ ":${PATH}:" == *":${candidate_bin}:"* ]] || export PATH="${candidate_bin}:${PATH}"
done

# task205: 解析 Python 3 解释器 — 支持 conda-only 环境
_resolve_python() {
    if [[ -n "${PYTHON:-}" ]] && command -v "${PYTHON}" >/dev/null 2>&1; then
        echo "${PYTHON}"; return 0
    fi
    if [[ -n "${CONDA_PREFIX:-}" && -x "${CONDA_PREFIX}/bin/python" ]]; then
        echo "${CONDA_PREFIX}/bin/python"; return 0
    fi
    command -v python3 && return 0
    if command -v python >/dev/null 2>&1; then
        local ver
        ver="$(python --version 2>&1 | awk '{print $2}' | cut -d. -f1)"
        [[ "${ver}" == "3" ]] && { command -v python; return 0; }
    fi
    return 1
}
PYTHON="$(_resolve_python)" || { echo "[ERROR] No Python 3 interpreter found. Install python3 or activate a conda env first." >&2; exit 1; }
PYTHON_CANONICAL="$("${PYTHON}" -c 'import os, shutil, sys; raw=sys.argv[1]; print(os.path.realpath(shutil.which(raw) or raw))' "${PYTHON}" 2>/dev/null || true)"
[[ -n "${PYTHON_CANONICAL}" ]] && PYTHON="${PYTHON_CANONICAL}"
export PYTHON

# ── 颜色 ──────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

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

# bypass approval+sandbox 是高危 flag（OpenAI CLI 文档明确警告）。
# 与 Claude 的 --permission-mode bypassPermissions 同等级别 — 仅在 root + container 场景启用，
# 其他场景（非 root 或非容器）保留 codex 默认权限提示，避免在主管开发机上误删文件。
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
        --work-root "${WORK_ROOT}" --provider codex --session "${session_name}" >/dev/null 2>&1; then
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
    [[ "$(get_tmux_env_value "${session_name}" "INTERN_CODEX_DEFAULT_ARGS")" == "${CODEX_DEFAULT_ARGS}" ]] || return 1
    for key in $(launch_env_file_keys "${WORK_ROOT}/enterprise_policy/daemon/user.env"); do
        launch_env_key_matches_tmux "${session_name}" "${key}" || return 1
    done
    for key in $(launch_env_file_keys "${POLICY_ENV_FILE}"); do
        [[ "${key}" == "CODEX_POLICY_ENV_HASH" ]] && continue
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
    [[ "$(get_tmux_env_value "${session_name}" "INTERN_REAL_CODEX")" == "${REAL_CODEX_BIN}" ]] || return 1
    [[ "$(get_tmux_env_value "${session_name}" "INTERN_RUNTIME_REPO")" == "${RUNTIME_REPO}" ]] || return 1
    return 0
}

wait_for_codex_prompt() {
    local session_name="$1"
    local timeout_seconds="${2:-30}"
    local deadline=$((SECONDS + timeout_seconds))
    local capture=""

    while (( SECONDS < deadline )); do
        capture="$(tmux capture-pane -p -J -t "=${session_name}:" -S -80 2>/dev/null || true)"
        if grep -q "› " <<<"${capture}" && grep -qi "codex" <<<"${capture}"; then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

codex_auth_mode() {
    "${PYTHON}" - <<'PYEOF'
import json
import os

auth_path = os.path.expanduser("~/.codex/auth.json")
try:
    with open(auth_path, "r", encoding="utf-8") as f:
        print(json.load(f).get("auth_mode") or "")
except Exception:
    print("")
PYEOF
}

resolve_codex_command() {
    local hook_feature_arg=""
    local features=""
    local codex_runtime="codex"
    local codex_bin="${REAL_CODEX_BIN:-codex}"
    features="$("${codex_bin}" features list 2>/dev/null || true)"
    if grep -Eq '^hooks[[:space:]]' <<< "${features}"; then
        hook_feature_arg="--enable hooks"
    elif grep -Eq '^codex_hooks[[:space:]]' <<< "${features}"; then
        hook_feature_arg="--enable codex_hooks"
    fi
    if [[ -n "${CODEX_PROFILE:-}" ]]; then
        codex_runtime="codex --profile ${CODEX_PROFILE}"
    fi

    # --dangerously-bypass-approvals-and-sandbox（别名 --yolo）整体放开权限+沙箱。
    # 与 Claude 对齐：仅在 root + container 时启用 bypass；其他场景使用默认 codex（带权限提示）。
    # OpenAI Codex CLI 文档：https://developers.openai.com/codex/cli/reference 中该 flag 标注为 dangerously。
    if should_enable_root_bypass; then
        echo "${codex_runtime} ${hook_feature_arg} --dangerously-bypass-approvals-and-sandbox"
        return
    fi
    if is_root_user; then
        warn "Detected root without sandbox/container markers; running plain 'codex' (approvals required for write/exec)." >&2
        echo "${codex_runtime} ${hook_feature_arg}"
        return
    fi
    # 非 root 用户：仍允许 bypass（与 Claude 的非 root 行为一致 — 用户对自己 home 的内容负责）
    echo "${codex_runtime} ${hook_feature_arg} --dangerously-bypass-approvals-and-sandbox"
}

resolve_codex_default_args() {
    local command_line=""
    if [[ -n "${INTERN_CODEX_POLICY_ARGS:-}" ]]; then
        echo "${INTERN_CODEX_POLICY_ARGS}"
        return
    fi
    command_line="$(resolve_codex_command)"
    if [[ "${command_line}" == "codex" ]]; then
        echo ""
        return
    fi
    echo "${command_line#codex }"
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

resolve_real_codex_binary() {
    local wrapper_dir="${INTERN_DIR}/.intern/bin"
    local real_codex=""
    local search_path=""

    search_path="$(strip_path_entry "${wrapper_dir}" "${PATH}")"
    real_codex="$(PATH="${search_path}" command -v codex || true)"
    if [[ -z "${real_codex}" ]]; then
        die "codex executable not found in PATH"
    fi
    if [[ "${real_codex}" == "${wrapper_dir}/codex" ]]; then
        die "resolved codex points to managed wrapper; cannot determine real Codex binary"
    fi
    REAL_CODEX_BIN="${real_codex}"
    ok "Resolved real Codex binary: ${REAL_CODEX_BIN}"
}

# ── 参数检查 ──────────────────────────────────
if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <intern_name> [project_name]"
    echo ""
    echo "  启动（或附着到）一个 intern 的 Codex CLI session。"
    echo ""
    echo "  project_name 默认为 axis_intern_agents。"
    echo "  代码仓库路径由企业 state-v1 resolver 通过 INTERN_CODE_REPO_PATH 提供。"
    exit 1
fi

INTERN_NAME="$1"

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
        info "tmux session '${TMUX_SESSION}' exists and Codex is running. Checking runtime config..."
    fi
    if [[ "${PROCESS_RUNNING}" -eq 0 ]]; then
        warn "tmux session '${TMUX_SESSION}' exists, but Codex is not running. Reusing the session..."
    fi
else
    info "tmux session '${TMUX_SESSION}' not found. Creating new session..."
fi

# ============================================================
# Step 3: 初始化 intern 工作目录
# ============================================================
info "Step 3: 初始化 intern 工作目录 ${INTERN_DIR}..."

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

# 写入 .hook_state.json（hooks 依赖 project 字段）
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
if [[ "${INTERN_START_SKIP_GROUP_CREATE:-0}" == "1" ]]; then
    CHAT_ID="${FEISHU_CHAT_ID:-}"
    info "Skipping Feishu group creation by request."
else
    CHAT_ID="$(ensure_feishu_group "${INTERN_NAME}" "codex" "${PROJECT_NAME}" "${INTERN_WORKSPACE_ID:-}")"
fi
ok "Feishu group ready: ${CHAT_ID:-skipped}"

# ============================================================
# Step 3.5: 用户级 ~/.codex/config.toml 配置项目 trust
# ============================================================
# Codex 项目级 .codex/config.toml 仅在项目被 trusted 时加载（包括 hooks）。
# trust grant 必须写在用户级 ~/.codex/config.toml 中：
#   [projects."<absolute-intern-dir>"]
#   trust_level = "trusted"
USER_CODEX_DIR="${HOME}/.codex"
USER_CODEX_CONFIG="${USER_CODEX_DIR}/config.toml"
mkdir -p "${USER_CODEX_DIR}"

info "Step 3.5: 在 ~/.codex/config.toml 中授信 intern 工作目录..."
"${PYTHON}" - "${USER_CODEX_CONFIG}" "${INTERN_DIR}" <<'PYEOF'
"""幂等地写入 Codex user config。

策略：先用 regex 删掉任何形态的旧 entry（含 header 和 trust_level 同行的 malformed 写法），
再追加一段干净的。避免被原文件状态污染。Python 标准库 tomllib 只读不写，不引入 tomli_w 依赖。
"""
import sys, os, re

config_path, intern_dir = sys.argv[1], sys.argv[2]
escaped_dir = re.escape(intern_dir)

text = ''
if os.path.exists(config_path):
    with open(config_path, 'r', encoding='utf-8') as f:
        text = f.read()

def upsert_top_level_bool(src, key, value):
    lines = src.splitlines(keepends=True)
    first_table = next((i for i, line in enumerate(lines) if line.lstrip().startswith('[')), len(lines))
    pattern = re.compile(r'^(\s*)' + re.escape(key) + r'\s*=')
    value_line = f'{key} = {str(value).lower()}\n'
    for i in range(first_table):
        if pattern.match(lines[i]):
            lines[i] = value_line
            return ''.join(lines)
    lines.insert(first_table, value_line)
    return ''.join(lines)

text = upsert_top_level_bool(text, 'suppress_unstable_features_warning', True)

# 删除任何形态（well-formed / inline header+key / header-only）的本 intern 旧 entry
pattern = re.compile(
    r'\n*\[projects\."' + escaped_dir + r'"\][^\n]*\n?'   # header 行（可含 inline trust_level）
    r'(?:[ \t]*trust_level\s*=\s*"[^"]*"\n?)*',           # 后续独立 trust_level 行
)
text = pattern.sub('\n', text)

# 追加干净 section（保留尾部空行作为 section 间分隔）
if text.strip():
    text = text.rstrip() + '\n\n'
else:
    text = ''
text += f'[projects."{intern_dir}"]\ntrust_level = "trusted"\n'

with open(config_path + '.tmp', 'w', encoding='utf-8') as f:
    f.write(text)
os.rename(config_path + '.tmp', config_path)
print('trust granted for ' + intern_dir)
print('suppressed unstable feature warning')
PYEOF
ok "User-level codex trust configured at ${USER_CODEX_CONFIG}"

# ============================================================
# Step 4: 确保 .codex/config.toml symlink 到共享模板
# ============================================================
info "Step 4: 确保 Codex 项目级 hooks 配置存在..."

CODEX_DIR="${INTERN_DIR}/.codex"
mkdir -p "${CODEX_DIR}"

CODEX_SETTINGS_TEMPLATE="${WORK_ROOT}/.github/codex_settings.toml"
if [[ ! -f "${CODEX_SETTINGS_TEMPLATE}" ]]; then
    die "Codex settings template not found: ${CODEX_SETTINGS_TEMPLATE}"
fi

source_enterprise_user_env
source_policy_runtime_env
if policy_env_has_managed_keys; then
    ACTIVE_POLICY_ENV_HASH="${CODEX_POLICY_ENV_HASH:-}"
else
    ACTIVE_POLICY_ENV_HASH=""
fi

# 替换旧文件为 symlink
if [[ -f "${CODEX_DIR}/config.toml" && ! -L "${CODEX_DIR}/config.toml" ]]; then
    warn "  Replacing plain config.toml with symlink to template..."
    rm -f "${CODEX_DIR}/config.toml"
fi
ln -sf "${CODEX_SETTINGS_TEMPLATE}" "${CODEX_DIR}/config.toml"

ok "Hooks config ready at ${CODEX_DIR}/config.toml"

# ============================================================
# Step 4.5: 同步 Codex hook review/trust state
# ============================================================
info "Step 4.5: 同步 Codex hook trust state..."
TRUST_STATE_CHANGED=0
TRUST_SYNC_OUTPUT="$("${PYTHON}" "${SCRIPT_DIR}/codex_trust_hooks.py" --config "${USER_CODEX_CONFIG}" --intern-dir "${INTERN_DIR}" --work-root "${WORK_ROOT}" 2>&1)" || die "Codex hook trust sync failed: ${TRUST_SYNC_OUTPUT}"
while IFS= read -r line; do
    [[ -n "${line}" ]] && info "  ${line}"
done <<< "${TRUST_SYNC_OUTPUT}"
if [[ "${TRUST_SYNC_OUTPUT}" == *"changed=1"* ]]; then
    TRUST_STATE_CHANGED=1
fi

# ============================================================
# Step 4.6: 注册 intern 类型为 codex（.intern_sessions.json）
# ============================================================
info "Step 4.6: 注册 intern 类型为 codex..."

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
    entry['type'] = 'codex'
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

ok "Registered type=codex for ${INTERN_NAME}"

SETTINGS_MTIME="$(stat -c %Y "${CODEX_DIR}/config.toml" 2>/dev/null || echo 0)"
CODEX_PROFILE_FILE="${CODEX_DIR}/profile"
ACTIVE_CODEX_PROFILE="${CODEX_PROFILE:-}"
if [[ -z "${ACTIVE_CODEX_PROFILE}" && -f "${CODEX_PROFILE_FILE}" ]]; then
    ACTIVE_CODEX_PROFILE="$(tr -d '[:space:]' < "${CODEX_PROFILE_FILE}")"
fi
if [[ -n "${ACTIVE_CODEX_PROFILE}" ]]; then
    if ! [[ "${ACTIVE_CODEX_PROFILE}" =~ ^[a-zA-Z0-9_.-]+$ ]]; then
        die "Invalid Codex profile '${ACTIVE_CODEX_PROFILE}' in ${CODEX_PROFILE_FILE}"
    fi
    export CODEX_PROFILE="${ACTIVE_CODEX_PROFILE}"
fi

resolve_real_codex_binary
CODEX_DEFAULT_ARGS="$(resolve_codex_default_args)"

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
    APPLIED_SETTINGS_MTIME="$(get_tmux_env_value "${TMUX_SESSION}" "CODEX_SETTINGS_MTIME")"
    APPLIED_RUNTIME_REPO="$(get_tmux_env_value "${TMUX_SESSION}" "INTERN_RUNTIME_REPO")"
    APPLIED_CODEX_PROFILE="$(get_tmux_env_value "${TMUX_SESSION}" "CODEX_PROFILE")"
    if [[ "${INTERN_SESSION_FORCE_RESTART:-0}" != "1" && -n "${APPLIED_SETTINGS_MTIME}" && "${APPLIED_SETTINGS_MTIME}" == "${SETTINGS_MTIME}" && "${APPLIED_RUNTIME_REPO}" == "${RUNTIME_REPO}" && "${APPLIED_CODEX_PROFILE}" == "${ACTIVE_CODEX_PROFILE}" && "${TRUST_STATE_CHANGED}" -eq 0 ]] && policy_runtime_matches_tmux "${TMUX_SESSION}"; then
        ok "tmux session '${TMUX_SESSION}' exists and Codex is already running with current hook/runtime config. Attaching..."
        attach_or_detach_session
    fi
    if [[ "${INTERN_SESSION_FORCE_RESTART:-0}" == "1" ]]; then
        warn "tmux session '${TMUX_SESSION}' exists; force restart requested."
    else
        warn "tmux session '${TMUX_SESSION}' exists, but Codex is using stale hook/runtime config. Restarting in place..."
    fi
fi

# ============================================================
# Step 4.7: 同步 Skill 农场（task220）
# ============================================================
# 重建 ${INTERN_DIR}/.agents/skills/ 以反映 .intern_skill.json 的最新启用列表。
# 失败不阻断启动（保留上一次成功状态），错误写到 stderr 进 log。
SKILL_SYNC_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/internctl.py"
CODEX_RESUME_COMMAND="$(printf '%q ' "${PYTHON}" "${SKILL_SYNC_SCRIPT}" session resume "${INTERN_NAME}" --project "${PROJECT_NAME}" --type codex)"
if [[ -f "${SKILL_SYNC_SCRIPT}" ]]; then
    info "Step 4.7: 同步 Codex Skill 农场..."
    WORK_AGENTS_ROOT="${WORK_ROOT}" "${PYTHON}" "${SKILL_SYNC_SCRIPT}" skill sync "${INTERN_NAME}" --project "${PROJECT_NAME}" >/dev/null 2>&1 || \
        warn "skill sync 失败（不阻断启动），可手动执行 'internctl skill sync ${INTERN_NAME} --project ${PROJECT_NAME}' 排查"
fi

# ============================================================
# Step 5: 创建 tmux session 并启动 Codex
# ============================================================
CODEX_RUN_ARGS="${CODEX_DEFAULT_ARGS}"
if [[ "${CODEX_RESUME_ON_START:-0}" == "1" ]]; then
    CODEX_RUN_ARGS="${CODEX_RUN_ARGS} resume --last"
fi

if [[ "${SESSION_EXISTS}" -eq 1 ]]; then
    info "Step 5: 在现有 tmux session '${TMUX_SESSION}' 中重启 Codex..."
    # 动态解析实际 pane target——历史 session 的唯一 window 可能不是 0（task253）
    PANE_INDEX="$(tmux list-panes -s -t "=${TMUX_SESSION}" -F '#{window_index}.#{pane_index}' 2>/dev/null | head -n1)"
    if [[ -z "${PANE_INDEX}" ]]; then
        die "无法解析 tmux session '${TMUX_SESSION}' 的 pane target（list-panes 返回空）"
    fi
    # Use a fast, RC-free bash so the pane shell is ready immediately; a slow
    # interactive zsh/conda init would race the send-keys below and garble them.
    tmux respawn-pane -k -t "=${TMUX_SESSION}:${PANE_INDEX}" -c "${INTERN_DIR}" bash --noprofile --norc
else
    info "Step 5: 创建 tmux session '${TMUX_SESSION}' 并启动 Codex..."
    # Use a fast, RC-free bash so the pane shell is ready immediately; a slow
    # interactive zsh/conda init would race the send-keys below and garble them.
    tmux new-session -d -s "${TMUX_SESSION}" -c "${INTERN_DIR}" bash --noprofile --norc
fi

# 通知 VS Code 插件 tmux session 已就绪
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
tmux send-keys -t "=${TMUX_SESSION}:" "export INTERN_REAL_CODEX=\"${REAL_CODEX_BIN}\"" Enter
tmux send-keys -t "=${TMUX_SESSION}:" "export INTERN_CODEX_DEFAULT_ARGS=$(shell_quote "${CODEX_DEFAULT_ARGS}")" Enter
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
tmux set-environment -t "=${TMUX_SESSION}" INTERN_REAL_CODEX "${REAL_CODEX_BIN}"
tmux set-environment -t "=${TMUX_SESSION}" INTERN_CODEX_DEFAULT_ARGS "${CODEX_DEFAULT_ARGS}"
tmux set-environment -t "=${TMUX_SESSION}" INTERN_CTL_PYTHON "${PYTHON}"
tmux set-environment -t "=${TMUX_SESSION}" INTERN_CTL_PATH "${SKILL_SYNC_SCRIPT}"
tmux set-environment -t "=${TMUX_SESSION}" INTERN_RUNTIME_REPO "${RUNTIME_REPO}"
tmux set-environment -t "=${TMUX_SESSION}" CODEX_SETTINGS_MTIME "${SETTINGS_MTIME}"
tmux set-environment -t "=${TMUX_SESSION}" CODEX_POLICY_ENV_HASH "${ACTIVE_POLICY_ENV_HASH:-}"
if [[ -n "${ACTIVE_CODEX_PROFILE}" ]]; then
    tmux send-keys -t "=${TMUX_SESSION}:" "export CODEX_PROFILE=\"${ACTIVE_CODEX_PROFILE}\"" Enter
    tmux set-environment -t "=${TMUX_SESSION}" CODEX_PROFILE "${ACTIVE_CODEX_PROFILE}"
else
    tmux set-environment -u -t "=${TMUX_SESSION}" CODEX_PROFILE 2>/dev/null || true
fi

CODEX_AUTH_MODE="$(codex_auth_mode)"
OPENAI_API_KEY_POLICY_MANAGED=0
if [[ "${CODEX_AUTH_MODE}" == "chatgpt" ]]; then
    if policy_env_manages_key "OPENAI_API_KEY"; then
        info "Codex auth cache is ChatGPT, but OPENAI_API_KEY is managed by enterprise policy; keeping policy runtime env."
        OPENAI_API_KEY_POLICY_MANAGED=1
    else
        info "Codex auth cache is ChatGPT; unsetting OPENAI_API_KEY for the Codex process so ChatGPT auth is not shadowed."
        tmux send-keys -t "=${TMUX_SESSION}:" "unset OPENAI_API_KEY" Enter
        tmux set-environment -u -t "=${TMUX_SESSION}" OPENAI_API_KEY 2>/dev/null || true
    fi
fi
tmux send-keys -t "=${TMUX_SESSION}:" "export INTERN_CODEX_AUTH_MODE=\"${CODEX_AUTH_MODE}\"" Enter
tmux send-keys -t "=${TMUX_SESSION}:" "export INTERN_CODEX_OPENAI_API_KEY_POLICY_MANAGED=\"${OPENAI_API_KEY_POLICY_MANAGED}\"" Enter
tmux set-environment -t "=${TMUX_SESSION}" INTERN_CODEX_AUTH_MODE "${CODEX_AUTH_MODE}"
tmux set-environment -t "=${TMUX_SESSION}" INTERN_CODEX_OPENAI_API_KEY_POLICY_MANAGED "${OPENAI_API_KEY_POLICY_MANAGED}"

if should_enable_root_bypass; then
    tmux send-keys -t "=${TMUX_SESSION}:" "export IS_SANDBOX=1" Enter
fi

# 启动 Codex CLI
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
CODEX_COMMAND="$(shell_quote "${REAL_CODEX_BIN}") ${CODEX_RUN_ARGS}; status=\$?; ${OFFLINE_NOTIFY}; echo; echo \"[intern] Codex exited with status \$status.\"; echo \"[intern] Resume this intern:\"; echo $(shell_quote "  ${CODEX_RESUME_COMMAND}"); exec bash -l"
info "Using Codex launch command: ${REAL_CODEX_BIN} ${CODEX_RUN_ARGS}"
tmux send-keys -t "=${TMUX_SESSION}:" "${CODEX_COMMAND}" Enter

ok "Codex session started in tmux '${TMUX_SESSION}'."
if wait_for_codex_prompt "${TMUX_SESSION}" 30; then
    ok "Codex prompt is ready."
else
    warn "Codex prompt was not confirmed within 30s; session is running but first delivery may need retry."
fi
request_light_refresh
attach_or_detach_session
