#!/usr/bin/env python3
"""daemon_watchdog.py - detect & auto-recover feishu_daemon inbound wedge.

Wedge signature (observed 2026-07-06 on .134): a
    "[RELAY_CLIENT] Feishu msg for 'X': text='...' atts=0"
log line with NO following log line for > STALL_SECONDS. A healthy daemon
ALWAYS logs a TMUX_SEND / "Sent to ... via tmux" / offline line within ~1s
after receiving a text message, so a lone trailing msg line means the inbound
handler is wedged (Feishu -> intern delivery is dead while the CLI keeps
running & streaming out).

On wedge: capture a SIGUSR1 faulthandler thread dump (root-cause evidence ->
feishu_daemon_faults.log), then TERM/KILL + relaunch the daemon with the
correct env (source ~/.relay_env + WORK_AGENTS_ROOT). Also relaunches if the
daemon process is dead. Self-discovers pid/log/script from the /tmp pidfile
and /proc, so it is portable across machines with the same architecture.

Modes: default = act; "--check" = detect only (no restart), print verdict.
"""
import os
import sys
import json
import time
import signal
import subprocess
import datetime
import fcntl

PIDFILE = "/tmp/feishu_daemon.json"
RELAY_ENV = os.path.expanduser("~/.relay_env")
WATCHLOG = os.path.expanduser("~/work-agents/.daemon_watchdog.log")
SELFLOCK = os.path.expanduser("~/work-agents/.daemon_watchdog.lock")
LOGPATH_CACHE = os.path.expanduser("~/work-agents/.daemon_logpath")
STALL_SECONDS = 90
MSG_MARK = "[RELAY_CLIENT] Feishu msg for"
EMPTY_TEXT = "text='' "


def logw(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "%s %s" % (ts, msg)
    print(line)
    try:
        with open(WATCHLOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def read_pidinfo():
    try:
        with open(PIDFILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def daemon_log_path(pid):
    try:
        return os.readlink("/proc/%d/fd/1" % pid)
    except OSError:
        return ""


def cache_logpath(path):
    if not path:
        return
    try:
        with open(LOGPATH_CACHE, "w") as f:
            f.write(path)
    except OSError:
        pass


def read_cached_logpath():
    try:
        with open(LOGPATH_CACHE) as f:
            return f.read().strip()
    except OSError:
        return ""


def daemon_script(pid, info):
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            for p in f.read().split(b"\0"):
                if p.endswith(b"feishu_daemon.py"):
                    return p.decode()
    except OSError:
        pass
    bd = info.get("bundle_dir", "")
    return os.path.join(bd, "scripts/daemon/feishu_daemon.py") if bd else ""


def tail(path, n=200):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 65536))
            data = f.read().decode("utf-8", "replace")
        return data.splitlines()[-n:]
    except OSError:
        return []


def parse_ts(line):
    try:
        return datetime.datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _text_msg(line):
    # Only atts=0 text messages get an immediate follow-up log line;
    # attachment-only / empty-text messages legitimately may not.
    return ("atts=0" in line) and (EMPTY_TEXT not in line)


def is_wedged(logpath):
    lines = tail(logpath, 200)
    last = -1
    for i, ln in enumerate(lines):
        if MSG_MARK in ln:
            last = i
    if last < 0:
        return False, None
    if not _text_msg(lines[last]):
        return False, None
    if any(ln.strip() for ln in lines[last + 1:]):
        return False, None  # handler logged something after -> healthy
    ts = parse_ts(lines[last])
    if ts is None:
        return False, None
    age = (datetime.datetime.now() - ts).total_seconds()
    return (age > STALL_SECONDS), lines[last]


def relaunch(script, logpath, war):
    if not script or not os.path.exists(script):
        logw("ERROR daemon script missing: %s" % script)
        return
    if not logpath:
        logpath = os.path.expanduser("~/work-agents/.daemon_relaunch.log")
    war = war or os.path.expanduser("~/work-agents")
    cmd = (
        '[ -f "%s" ] && . "%s"; '
        'export WORK_AGENTS_ROOT="%s"; '
        'echo "--- watchdog relaunch $(date +%%T) ---" >> "%s"; '
        'cd "%s"; setsid nohup /usr/bin/python3 "%s" >> "%s" 2>&1 &'
    ) % (RELAY_ENV, RELAY_ENV, war, logpath, war, script, logpath)
    subprocess.run(["bash", "-lc", cmd])
    logw("relaunch issued")


def recover(pid, script, logpath, war, reason):
    logw("RECOVER reason=%s pid=%s" % (reason, pid))
    if alive(pid):
        try:
            os.kill(pid, signal.SIGUSR1)  # faulthandler dump for root-cause
            logw("sent SIGUSR1 -> feishu_daemon_faults.log")
            time.sleep(3)
        except OSError as e:
            logw("SIGUSR1 failed: %s" % e)
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        for _ in range(6):
            if not alive(pid):
                break
            time.sleep(1)
        if alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
            time.sleep(2)
    relaunch(script, logpath, war)


def main():
    check_only = "--check" in sys.argv
    # self-lock so overlapping cron ticks never double-launch
    try:
        lf = open(SELFLOCK, "w")
        fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return
    info = read_pidinfo()
    pid = info.get("pid")
    war = info.get("work_agents_root")
    if not alive(pid):
        script = daemon_script(pid or 0, info)
        if check_only:
            print("VERDICT: daemon DEAD (pid=%s)" % pid)
            return
        logw("daemon dead (pid=%s); relaunching" % pid)
        recover(pid or 0, script, read_cached_logpath(), war, "process_dead")
        return
    logpath = daemon_log_path(pid)
    if not logpath or not os.path.exists(logpath):
        print("no log path")
        return
    cache_logpath(logpath)
    wedged, mark = is_wedged(logpath)
    if check_only:
        print("VERDICT: %s pid=%s" % ("WEDGED" if wedged else "healthy", pid))
        if mark:
            print("  last-msg: %s" % mark[:140])
        return
    if wedged:
        logw("WEDGE >%ds: %s" % (STALL_SECONDS, mark[:140]))
        recover(pid, daemon_script(pid, info), logpath, war, "inbound_wedge")


if __name__ == "__main__":
    main()
