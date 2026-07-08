#!/usr/bin/env bash
# Fresh-restart an intern's copilot session: drop an oversized / context-
# contaminated session and start a clean one, while KEEPING the same intern,
# Feishu group, registration and git checkout. Same intern, brand-new SID.
#
#   fresh_restart.sh <intern_name> <old_sid> <project>
#
# Steps: export a HANDOFF safety-net -> create a fresh `copilot -p` session in
# the repo cwd -> repoint the registry to the new SID -> kill the old tmux +
# poller so the keeper rebuilds on the fresh SID. Companion scripts
# (_make_handoff.py / _repoint_reg.py) must sit in $HOME/work-agents.
set -uo pipefail
NAME="${1:?usage: fresh_restart.sh <intern_name> <old_sid> <project>}"
OLD_SID="${2:?old_sid required}"
PROJECT="${3:?project required}"
WA="$HOME/work-agents"
SS="$HOME/.copilot/session-state"

CWD=$(grep '^cwd:' "$SS/$OLD_SID/workspace.yaml" 2>/dev/null | head -1 | cut -d: -f2- | sed 's/^ *//;s/ *$//')
[ -z "$CWD" ] && { echo "ERR: no cwd for $OLD_SID"; exit 1; }
[ -d "$CWD" ] || { echo "ERR: cwd missing: $CWD"; exit 1; }
echo "intern=$NAME old=${OLD_SID:0:8} project=$PROJECT cwd=$CWD"

# 1) handoff safety net (recent conclusions -> HANDOFF_FRESH_RESTART.md in cwd)
python3 "$WA/_make_handoff.py" "$OLD_SID" "$CWD" 2>&1 | tail -1

# 2) create a FRESH session (no --resume) in the repo cwd
before=$(ls -d "$SS"/*/ 2>/dev/null | xargs -n1 basename 2>/dev/null | sort)
ORIENT="Your previous session was reset because its context grew too large and began confabulating polluted tool output. You are the research intern \"$NAME\" on project \"$PROJECT\", working in this repository. Re-orient: read GOAL*.md, README.md, docs/ and HANDOFF_FRESH_RESTART.md, then reply with a concise (<=8 lines) summary of the project goal and where things currently stand. Do NOT make any code or git changes yet — just re-orient and report."
# shellcheck disable=SC1090
source "$WA/copilot_env.sh" 2>/dev/null || true
cd "$CWD" || exit 1
echo "creating fresh session (copilot -p, up to 180s)..."
if command -v timeout >/dev/null 2>&1; then TO="timeout 180";
elif command -v gtimeout >/dev/null 2>&1; then TO="gtimeout 180";
else TO=""; fi
$TO copilot -p "$ORIENT" --allow-all >"/tmp/fresh_${NAME}.out" 2>&1
rc=$?
after=$(ls -d "$SS"/*/ 2>/dev/null | xargs -n1 basename 2>/dev/null | sort)
NEW_SID=$(comm -13 <(printf '%s\n' "$before") <(printf '%s\n' "$after") | grep -E '^[0-9a-f-]{36}$' | head -1)
if [ -z "$NEW_SID" ]; then
  echo "ERR: no new session created (rc=$rc). tail:"; tail -8 "/tmp/fresh_${NAME}.out"; exit 1
fi
echo "NEW_SID=$NEW_SID"

# 3) repoint registration to the fresh SID + clear stream state
python3 "$WA/_repoint_reg.py" "$NAME" "$NEW_SID" 2>&1 | tail -1

# 4) swap: kill the old tmux + poller so the keeper rebuilds on the fresh SID
TMUX_S=$(python3 -c "import json,os;d=json.load(open(os.path.expanduser('~/work-agents/.copilot_interns.json')));print(next((i['tmux'] for i in (d if isinstance(d,list) else d.get('interns',[])) if i['name']=='$NAME'),''))" 2>/dev/null)
[ -n "$TMUX_S" ] && tmux kill-session -t "$TMUX_S" 2>/dev/null
for p in $(pgrep -f "copstream:$NAME" 2>/dev/null); do kill "$p" 2>/dev/null; done
echo "FRESH_DONE $NAME $NEW_SID (keeper will rebuild ${TMUX_S:-tmux} on the fresh SID within ~30s)"
