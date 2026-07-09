#!/usr/bin/env bash
# Fresh-restart an intern's copilot session: drop an oversized / context-
# contaminated session and start a clean one, KEEPING the same intern, Feishu
# group, registration, git checkout and work branch.
#
#   fresh_restart.sh <intern_name> <old_sid> <project>
#   FORCE=1 fresh_restart.sh ...   # restart even if the intern is Working
#
# SAFE ORDERING (never two copilots in one working tree):
#   lock -> idle-check -> record work branches -> handoff -> DISABLE intern
#   (so the keeper can't resurrect it) -> KILL old tmux+poller + any stray
#   copilot in the tree, confirm dead -> restore work branches if moved ->
#   create the FRESH session (now the only copilot in the tree) -> repoint the
#   registry to the new SID -> RE-ENABLE. The keeper then rebuilds on the fresh
#   SID. Portable to bash 3.2 (macOS) and Linux. Companion scripts
#   (_make_handoff.py / _repoint_reg.py / _toggle_enabled.py) live in ~/work-agents.
set -uo pipefail
NAME="${1:?usage: fresh_restart.sh <intern_name> <old_sid> <project>}"
OLD_SID="${2:?old_sid required}"
PROJECT="${3:?project required}"
FORCE="${FORCE:-0}"
WA="$HOME/work-agents"
SS="$HOME/.copilot/session-state"
LOCK="$WA/.restart_${NAME}.lock"

pyget() { python3 -c "import json,os;d=json.load(open(os.path.expanduser('~/work-agents/.copilot_interns.json')));print(next((i.get('$1','') for i in (d if isinstance(d,list) else d.get('interns',[])) if i['name']=='$NAME'),''))" 2>/dev/null; }

# portable: print the cwd of a pid (Linux /proc, macOS lsof)
proc_cwd() {
  if [ -r "/proc/$1/cwd" ]; then readlink "/proc/$1/cwd" 2>/dev/null
  else lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1; fi
}

WS_CWD=$(grep '^cwd:' "$SS/$OLD_SID/workspace.yaml" 2>/dev/null | head -1 | cut -d: -f2- | sed 's/^ *//;s/ *$//')
REG_CWD=$(pyget cwd)
TREE="${REG_CWD:-$WS_CWD}"
LAUNCH="${WS_CWD:-$REG_CWD}"
TMUX_S=$(pyget tmux)
[ -z "$TREE" ] && { echo "ERR: no cwd for $NAME"; exit 1; }
[ -d "$LAUNCH" ] || { echo "ERR: launch cwd missing: $LAUNCH"; exit 1; }
echo "intern=$NAME old=${OLD_SID:0:8} project=$PROJECT tree=$TREE launch=$LAUNCH tmux=$TMUX_S"

# 0) lock (one restart per intern at a time)
if [ -e "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
  echo "ERR: restart already in progress ($LOCK, pid $(cat "$LOCK"))"; exit 1
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

# 1) idle-check: never interrupt an intern mid-work (unless FORCE=1)
if [ "$FORCE" != "1" ] && [ -n "$TMUX_S" ]; then
  if tmux capture-pane -t "$TMUX_S" -p 2>/dev/null | grep -qE "Working.*esc (interrupt|cancel)"; then
    echo "REFUSED: $NAME is Working — not restarting mid-task (retry when idle, or FORCE=1)"; exit 2
  fi
fi

# 2) record work branch of every git repo under the tree (repo<TAB>branch)
BR_F=$(mktemp 2>/dev/null || echo "/tmp/br_$NAME.$$")
: > "$BR_F"
find "$TREE" -maxdepth 2 -name .git -type d 2>/dev/null | while read -r gd; do
  r=$(dirname "$gd")
  b=$(git -C "$r" rev-parse --abbrev-ref HEAD 2>/dev/null)
  [ -n "$b" ] && [ "$b" != "HEAD" ] && printf '%s\t%s\n' "$r" "$b" >> "$BR_F" && echo "  branch: $(basename "$r") @ $b"
done

# 3) handoff safety-net (read-only from old events)
python3 "$WA/_make_handoff.py" "$OLD_SID" "$LAUNCH" 2>&1 | tail -1

# 4) DISABLE the intern so the keeper won't rebuild the old session in the gap
python3 "$WA/_toggle_enabled.py" "$NAME" false 2>&1 | tail -1

# 5) kill old tmux + poller + ANY copilot whose cwd is under this tree; confirm
[ -n "$TMUX_S" ] && tmux kill-session -t "$TMUX_S" 2>/dev/null
for p in $(pgrep -f "copstream:$NAME" 2>/dev/null); do kill "$p" 2>/dev/null; done
kill_in_tree() {  # $1 = signal (e.g. -TERM/-KILL/-0); echoes affected pids
  killed=""
  for p in $(pgrep -f "copilot.*--resume" 2>/dev/null); do
    pc=$(proc_cwd "$p")
    case "$pc" in "$TREE"*) kill "$1" "$p" 2>/dev/null && killed="$killed $p";; esac
  done
  echo "$killed"
}
k1=$(kill_in_tree -TERM); [ -n "$k1" ] && echo "  killed copilot in tree:$k1"
sleep 3
k2=$(kill_in_tree -KILL); [ -n "$k2" ] && echo "  force-killed lingering:$k2"
sleep 1
still=$(kill_in_tree -0); [ -n "$still" ] && echo "  WARN: copilot still present in tree:$still"

# 6) restore work branches if something moved them (safe now: no copilot in tree)
while IFS="$(printf '\t')" read -r r b; do
  [ -z "$r" ] && continue
  cur=$(git -C "$r" rev-parse --abbrev-ref HEAD 2>/dev/null)
  if [ "$cur" != "$b" ]; then
    if [ -z "$(git -C "$r" status --porcelain 2>/dev/null)" ]; then
      git -C "$r" checkout "$b" 2>/dev/null && echo "  restored $(basename "$r"): $cur -> $b"
    else
      echo "  WARN: $(basename "$r") on $cur (want $b) but has uncommitted changes — left as-is"
    fi
  fi
done < "$BR_F"
rm -f "$BR_F"

# 7) create the FRESH session (only copilot in the tree now)
before=$(ls -d "$SS"/*/ 2>/dev/null | xargs -n1 basename 2>/dev/null | sort)
ORIENT="Your previous session was reset because its context grew too large and began confabulating polluted tool output. You are the research intern \"$NAME\" on project \"$PROJECT\", working in this repository (stay on the CURRENT git branch; do NOT checkout another branch). Re-orient: read GOAL*.md, README.md, docs/ and HANDOFF_FRESH_RESTART.md, then reply with a concise (<=8 lines) summary of the project goal and where things currently stand. Do NOT make any code or git changes yet — just re-orient and report."
# shellcheck disable=SC1090
source "$WA/copilot_env.sh" 2>/dev/null || true
cd "$LAUNCH" || { echo "ERR: cd $LAUNCH failed"; exit 1; }
echo "creating fresh session (copilot -p, up to 180s)..."
if command -v timeout >/dev/null 2>&1; then TO="timeout 180";
elif command -v gtimeout >/dev/null 2>&1; then TO="gtimeout 180";
else TO=""; fi
$TO copilot -p "$ORIENT" --allow-all >"/tmp/fresh_${NAME}.out" 2>&1
rc=$?
after=$(ls -d "$SS"/*/ 2>/dev/null | xargs -n1 basename 2>/dev/null | sort)
NEW_SID=$(comm -13 <(printf '%s\n' "$before") <(printf '%s\n' "$after") | grep -E '^[0-9a-f-]{36}$' | head -1)
if [ -z "$NEW_SID" ]; then
  echo "ERR: no new session created (rc=$rc). tail:"; tail -8 "/tmp/fresh_${NAME}.out"
  python3 "$WA/_toggle_enabled.py" "$NAME" true 2>&1 | tail -1   # re-enable so keeper recovers
  exit 1
fi
echo "NEW_SID=$NEW_SID"

# 8) repoint the registry to the fresh SID + clear stream state + RE-ENABLE
python3 "$WA/_repoint_reg.py" "$NAME" "$NEW_SID" 2>&1 | tail -1
python3 "$WA/_toggle_enabled.py" "$NAME" true 2>&1 | tail -1
echo "FRESH_DONE $NAME $NEW_SID (keeper rebuilds ${TMUX_S:-tmux} on the fresh SID within ~30s)"
