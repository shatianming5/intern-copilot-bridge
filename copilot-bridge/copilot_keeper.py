#!/usr/bin/env python3
"""Multi-intern keeper for Copilot-CLI Feishu bridges on server3.

Reads ~/work-agents/.copilot_interns.json:
  [ {"name":"intern_dd","project":"causal-field-two-layer",
     "tmux":"cop_dd","sid":"<uuid>","cwd":"/abs/intern_dir"}, ... ]

For each enabled intern it guarantees:
  1. a tmux session running `copilot --resume=<sid>` under an auto-resume loop
     (copilot idle-shuts-down ~7 min -> loop resumes within a few seconds);
  2. an outbound poller (copilot_bridge.py) posting replies to Feishu.

Idempotent + safe to run detached forever.
"""
import json
import glob
import os
import subprocess
import time

HOME = os.path.expanduser("~")
WA = os.path.join(HOME, "work-agents")
CONFIG = os.path.join(WA, ".copilot_interns.json")
SESSIONS_JSON = os.path.join(WA, ".intern_sessions.json")
SESSION_STATE = os.path.join(HOME, ".copilot", "session-state")
ENV_SH = os.path.join(WA, "copilot_env.sh")
BRIDGE = os.path.join(WA, "copilot_stream.py")
LOG = os.path.join(WA, ".copilot_keeper.log")


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def load_cfg():
    try:
        return json.load(open(CONFIG))
    except Exception as ex:
        log(f"config load error: {ex}")
        return []


# ── fork self-heal ──────────────────────────────────────────────────────────
# `copilot --resume=<big session>` can spawn a NEW same-cwd session and write
# there, leaving the registered SID stale and the poller capturing nothing. The
# session copilot is ACTUALLY using is the one carrying a live `inuse.<pid>.lock`.
def _pane_pids(name):
    """pane pid + all descendant pids for a tmux session (empty if none)."""
    r = run(["tmux", "list-panes", "-t", name, "-F", "#{pane_pid}"])
    pane_pid = (r.stdout.strip().splitlines() or [""])[0]
    if not pane_pid:
        return set()
    pids = {pane_pid}
    frontier = [pane_pid]
    while frontier:
        p = frontier.pop()
        kids = run(["pgrep", "-P", p]).stdout.split()
        for k in kids:
            if k not in pids:
                pids.add(k)
                frontier.append(k)
    return pids


def _session_cwd(sdir):
    """cwd recorded in a session-state dir's workspace.yaml (cheap), or None."""
    try:
        with open(os.path.join(sdir, "workspace.yaml")) as f:
            for line in f:
                if line.startswith("cwd:"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return None


def _live_lock_pid(sdir):
    """pid of a live `inuse.<pid>.lock` in this session dir, or None."""
    for lk in glob.glob(os.path.join(sdir, "inuse.*.lock")):
        pid = os.path.basename(lk).split(".")[1]
        if run(["ps", "-p", pid]).returncode == 0:
            return pid
    return None


def _events_mtime(sid):
    """mtime of a session's events.jsonl if non-empty, else 0."""
    f = os.path.join(SESSION_STATE, sid, "events.jsonl")
    try:
        if os.path.getsize(f) > 0:
            return os.path.getmtime(f)
    except Exception:
        pass
    return 0


def resolve_effective_sid(itn):
    """Return the SID copilot is REALLY writing for this intern.

    A fork = a *different* same-cwd session whose events.jsonl is being actively
    appended (newer than the registered one) AND that carries a live inuse-lock
    held by a process under this intern's tmux pane. Empty stub sessions copilot
    merely locks (no events) are ignored via the mtime gate. Returns the
    registered SID unchanged when there is no clearly-newer active fork."""
    reg = itn["sid"]
    cwd = itn.get("cwd", "")
    tmux = itn["tmux"]
    if not cwd or not tmux_alive(tmux):
        return reg
    reg_m = _events_mtime(reg)
    now = time.time()
    # cheap prefilter: only sessions whose events are newer than the registered
    # one (by a margin) and recently active are fork candidates.
    cands = []
    for f in glob.glob(os.path.join(SESSION_STATE, "*", "events.jsonl")):
        try:
            m = os.path.getmtime(f)
        except Exception:
            continue
        if m <= reg_m + 60 or (now - m) > 1800:
            continue
        sid = os.path.basename(os.path.dirname(f))
        if sid != reg:
            cands.append((m, sid, os.path.dirname(f)))
    if not cands:
        return reg
    pane_pids = _pane_pids(tmux)
    if not pane_pids:
        return reg
    for _m, sid, sdir in sorted(cands, reverse=True):   # newest first
        wc = _session_cwd(sdir)
        if not wc or not wc.startswith(cwd):
            continue
        lp = _live_lock_pid(sdir)
        if lp and lp in pane_pids:
            return sid
    return reg


def kill_poller(name):
    for pid in run(["pgrep", "-f", f"copstream:{name}"]).stdout.split():
        run(["kill", pid])


def persist_sid(name, new_sid):
    """Update the registered SID in both registry files (fork self-heal)."""
    try:
        d = json.load(open(CONFIG))
        for it in (d if isinstance(d, list) else d.get("interns", [])):
            if it.get("name") == name:
                it["sid"] = new_sid
        json.dump(d, open(CONFIG, "w"), ensure_ascii=False, indent=2)
    except Exception as ex:
        log(f"persist_sid CONFIG err: {ex}")
    try:
        s = json.load(open(SESSIONS_JSON))

        def fix(o):
            if isinstance(o, dict):
                if o.get("intern_name") == name and "copilot_sid" in o:
                    o["copilot_sid"] = new_sid
                for v in o.values():
                    fix(v)
            elif isinstance(o, list):
                for v in o:
                    fix(v)
        fix(s)
        json.dump(s, open(SESSIONS_JSON, "w"), ensure_ascii=False, indent=2)
    except Exception as ex:
        log(f"persist_sid SESSIONS err: {ex}")


def tmux_alive(name):
    return run(["tmux", "has-session", "-t", name]).returncode == 0


def tmux_has_correct_sid(name, sid):
    """True if the tmux session is legitimately producing the EXPECTED sid.

    Passes when the pane chain resumes `--resume=<sid>` OR when session <sid>
    carries a live inuse-lock held by a process under this pane (i.e. copilot
    forked into <sid> and is actively writing it — after a repoint this is the
    correct steady state, so we must NOT rebuild). Guards against malformed/stale
    sessions (no --resume, wrong session) which match neither and get rebuilt.
    """
    r = run(["tmux", "list-panes", "-t", name, "-F", "#{pane_pid}"])
    pane_pid = (r.stdout.strip().splitlines() or [""])[0]
    if not pane_pid:
        return False
    # walk pane_pid + its descendants, look for --resume=<sid> in any cmdline
    seen = run(["pgrep", "-P", pane_pid]).stdout.split() + [pane_pid]
    for pid in seen:
        cl = run(["ps", "-o", "args=", "-p", pid]).stdout
        if f"--resume={sid}" in cl:
            return True
    # also check the wrapper (pane_pid) cmdline directly
    wrapper = run(["ps", "-o", "args=", "-p", pane_pid]).stdout
    if f"--resume={sid}" in wrapper:
        return True
    # fork case: this pane's copilot holds the live lock on session <sid>
    sdir = os.path.join(SESSION_STATE, sid)
    lock_pid = _live_lock_pid(sdir)
    return bool(lock_pid and lock_pid in _pane_pids(name))



def kill_tmux(name):
    run(["tmux", "kill-session", "-t", name])


def _model_flags(itn):
    """Optional per-intern model flags from config: model / effort / context."""
    flags = []
    if itn.get("model"):
        flags += ["--model", str(itn["model"])]
    if itn.get("effort"):
        flags += ["--effort", str(itn["effort"])]
    if itn.get("context"):
        flags += ["--context", str(itn["context"])]
    return " ".join(flags)


def start_tmux(itn):
    sid, cwd, name = itn["sid"], itn["cwd"], itn["tmux"]
    mflags = _model_flags(itn)
    mpart = f" {mflags}" if mflags else ""
    loop = (
        f'source {ENV_SH}; '
        f'while true; do '
        f'copilot --resume={sid} --allow-all{mpart}; '
        f'echo "[keeper] copilot exited $(date), resuming in 3s"; sleep 3; '
        f'done'
    )
    run(["tmux", "new-session", "-d", "-s", name, "-x", "220", "-y", "50", "-c", cwd,
         "bash", "-lc", loop])
    log(f"(re)created tmux {name} resume={sid}{mpart}")


def poller_alive(name):
    r = run(["pgrep", "-f", f"copilot_stream.py.*{name}"])
    if r.stdout.strip():
        return True
    r = run(["pgrep", "-f", f"copstream:{name}"])
    return bool(r.stdout.strip())


def start_poller(itn):
    env = dict(os.environ)
    env["PATH"] = f"{HOME}/.local/node-v22.11.0-linux-x64/bin:" + env.get("PATH", "")
    env["COP_SID"] = itn["sid"]
    env["COP_INTERN"] = itn["name"]
    env["COP_PROJECT"] = itn["project"]
    env["COP_HOME"] = HOME
    env["COP_TMUX"] = itn["tmux"]
    # marker in argv so pgrep can find it
    subprocess.Popen(
        ["python3", BRIDGE, f"copstream:{itn['name']}"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True,
    )
    log(f"(re)started stream {itn['name']} sid={itn['sid']}")


def main():
    log("keeper start")
    while True:
        try:
            for itn in load_cfg():
                if not itn.get("enabled", True):
                    continue
                if not itn.get("sid"):
                    continue
                # fork self-heal: if copilot forked into a new same-cwd session
                # (live lock under this tmux), follow it — repoint registry +
                # restart the poller so capture never silently stalls.
                eff = resolve_effective_sid(itn)
                if eff != itn["sid"]:
                    log(f"fork detected {itn['name']}: {itn['sid']} -> {eff}; repointing")
                    persist_sid(itn["name"], eff)
                    itn["sid"] = eff
                    kill_poller(itn["name"])
                if not tmux_alive(itn["tmux"]):
                    start_tmux(itn)
                    time.sleep(10)
                elif not tmux_has_correct_sid(itn["tmux"], itn["sid"]):
                    # tmux exists but is resuming the wrong/no sid -> rebuild it
                    log(f"tmux {itn['tmux']} has wrong sid (expected {itn['sid']}); rebuilding")
                    kill_tmux(itn["tmux"])
                    time.sleep(2)
                    start_tmux(itn)
                    time.sleep(10)
                if not poller_alive(itn["name"]):
                    start_poller(itn)
        except Exception as ex:
            log(f"loop error: {ex}")
        time.sleep(20)


if __name__ == "__main__":
    main()
