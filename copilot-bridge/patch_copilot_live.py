#!/usr/bin/env python3
"""Patch _is_claude_process_running in all feishu_daemon.py copies so that
copilot-CLI interns (registered as claude-type + external_managed, running as
`node .../copilot`) are detected as online. Idempotent."""
import sys

OLD = (
    '    if _is_tmux_cli_process_running(intern_name, "claude", project=project):\n'
    '        return True\n'
    '    return _is_tmux_cli_child_process_running(intern_name, "claude", project=project)'
)
NEW = (
    '    if _is_tmux_cli_process_running(intern_name, "claude", project=project):\n'
    '        return True\n'
    '    if _is_tmux_cli_child_process_running(intern_name, "claude", project=project):\n'
    '        return True\n'
    '    # copilot-CLI interns (registered as claude-type + external_managed) run as node .../copilot\n'
    '    if _is_tmux_cli_process_running(intern_name, "copilot", project=project):\n'
    '        return True\n'
    '    return _is_tmux_cli_child_process_running(intern_name, "copilot", project=project)'
)
MARK = "registered as claude-type + external_managed"

for p in sys.argv[1:]:
    s = open(p, encoding="utf-8").read()
    if MARK in s:
        print("already patched:", p)
        continue
    if OLD in s:
        open(p + ".bak.copilotlive", "w").write(s)
        open(p, "w", encoding="utf-8").write(s.replace(OLD, NEW, 1))
        print("patched:", p)
    else:
        print("PATTERN NOT FOUND:", p)
