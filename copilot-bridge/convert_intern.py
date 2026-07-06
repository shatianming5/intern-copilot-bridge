#!/usr/bin/env python3
"""Convert one claude intern to a copilot-CLI intern on server3.

Steps: bootstrap a fresh copilot session in tmux (trust folder), capture its
SID, send the inheritance command (read HANDOFF_FROM_CLAUDE.md), register it in
.intern_sessions.json (external_managed + copilot), add it to the keeper config,
and start the outbound poller.

Usage: convert_intern.py <name> <project> <tmux> <cwd> <session_key>
"""
import json
import os
import subprocess
import sys
import time

NAME, PROJECT, TMUX, CWD, KEY = sys.argv[1:6]
HOME = os.path.expanduser("~")
WA = os.path.join(HOME, "work-agents")
ENV_SH = os.path.join(WA, "copilot_env.sh")
SSDIR = os.path.join(HOME, ".copilot", "session-state")


def sh(*a, **k):
    return subprocess.run(a, capture_output=True, text=True, **k)


def pane(t):
    return sh("tmux", "capture-pane", "-t", t, "-p").stdout


def main():
    os.makedirs(SSDIR, exist_ok=True)
    before = set(os.listdir(SSDIR))
    sh("tmux", "kill-session", "-t", TMUX)
    time.sleep(1)
    # bootstrap fresh copilot (single-shot; keeper takes over on first idle-exit)
    loop = f"source {ENV_SH}; copilot --allow-all"
    sh("tmux", "new-session", "-d", "-s", TMUX, "-x", "220", "-y", "50", "-c", CWD,
       "bash", "-lc", loop)
    print(f"[{NAME}] bootstrapping copilot in {TMUX} (cwd={CWD})")
    time.sleep(15)
    # handle folder-trust prompt (option 2 = trust + remember)
    if "trust" in pane(TMUX).lower():
        sh("tmux", "send-keys", "-t", TMUX, "2", "Enter")
        time.sleep(6)
        print(f"[{NAME}] folder trusted")
    # capture new SID
    sid = ""
    for _ in range(6):
        after = set(os.listdir(SSDIR))
        new = sorted(after - before)
        if new:
            sid = new[-1]
            break
        time.sleep(2)
    if not sid:
        print(f"[{NAME}] ERROR: could not capture SID")
        return 1
    print(f"[{NAME}] SID={sid}")
    # send inheritance command (bounded)
    msg = (
        f"你是研究实习生 {NAME}(项目 {PROJECT})。你的前一个会话是 Claude,"
        f"完整对话记录已保存在当前目录的 HANDOFF_FROM_CLAUDE.md。请先阅读它,"
        f"继承之前的任务与当前进度,然后用中文简要回复三点:①继承的核心任务 "
        f"②当前进度/在等什么 ③下一步。回复完这三点后先等待指示,不要擅自执行其他操作。"
    )
    sh("tmux", "send-keys", "-t", TMUX, msg)
    time.sleep(1)
    sh("tmux", "send-keys", "-t", TMUX, "Enter")
    print(f"[{NAME}] inheritance command sent")

    # register in .intern_sessions.json
    sp = os.path.join(WA, ".intern_sessions.json")
    d = json.load(open(sp))
    if KEY not in d:
        d[KEY] = {"type": "claude", "intern_name": NAME, "project": PROJECT}
    d[KEY]["tmux_session"] = TMUX
    d[KEY]["external_managed"] = True
    d[KEY]["provider"] = "copilot"
    d[KEY]["copilot_sid"] = sid
    json.dump(d, open(sp, "w"), indent=2, ensure_ascii=False)
    print(f"[{NAME}] registered {KEY} -> {TMUX} external_managed")

    # add to keeper config
    cfg = os.path.join(WA, ".copilot_interns.json")
    interns = []
    if os.path.exists(cfg):
        try:
            interns = json.load(open(cfg))
        except Exception:
            interns = []
    interns = [i for i in interns if i.get("name") != NAME]
    interns.append({"name": NAME, "project": PROJECT, "tmux": TMUX,
                    "sid": sid, "cwd": CWD, "enabled": True})
    json.dump(interns, open(cfg, "w"), indent=2, ensure_ascii=False)
    print(f"[{NAME}] keeper config updated ({len(interns)} interns)")

    # start poller
    env = dict(os.environ)
    env["PATH"] = f"{HOME}/.local/node-v22.11.0-linux-x64/bin:" + env.get("PATH", "")
    env["COP_SID"] = sid
    env["COP_INTERN"] = NAME
    env["COP_PROJECT"] = PROJECT
    env["COP_HOME"] = HOME
    if not sh("pgrep", "-f", f"copbridge:{NAME}").stdout.strip():
        subprocess.Popen(
            ["python3", os.path.join(WA, "copilot_bridge.py"), f"copbridge:{NAME}"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
        print(f"[{NAME}] poller started")
    print(f"[{NAME}] DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
