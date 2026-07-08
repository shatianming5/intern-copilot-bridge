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
import os
import subprocess
import time

HOME = os.path.expanduser("~")
WA = os.path.join(HOME, "work-agents")
CONFIG = os.path.join(WA, ".copilot_interns.json")
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


def tmux_alive(name):
    return run(["tmux", "has-session", "-t", name]).returncode == 0


def tmux_has_correct_sid(name, sid):
    """True if the tmux session's wrapper is resuming the EXPECTED sid.

    Guards against a malformed/stale session (e.g. `copilot --allow-all` with no
    --resume, or resuming a wrong sid) that tmux_alive would wrongly accept,
    leaving the intern stuck in a detached context forever. Inspects the pane's
    command chain for `--resume=<sid>`.
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
    return f"--resume={sid}" in wrapper


def kill_tmux(name):
    run(["tmux", "kill-session", "-t", name])


def start_tmux(itn):
    sid, cwd, name = itn["sid"], itn["cwd"], itn["tmux"]
    loop = (
        f'source {ENV_SH}; '
        f'while true; do '
        f'copilot --resume={sid} --allow-all; '
        f'echo "[keeper] copilot exited $(date), resuming in 3s"; sleep 3; '
        f'done'
    )
    run(["tmux", "new-session", "-d", "-s", name, "-x", "220", "-y", "50", "-c", cwd,
         "bash", "-lc", loop])
    log(f"(re)created tmux {name} resume={sid}")


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
