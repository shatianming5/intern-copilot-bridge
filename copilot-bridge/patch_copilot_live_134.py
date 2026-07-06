#!/usr/bin/env python3
"""Patch .134's (older) feishu_daemon.py so copilot-CLI interns are detected as
online. .134's version lacks _is_tmux_cli_child_process_running entirely and
_is_claude_process_running only checks pane_current_command. Idempotent."""
import sys

OLD = (
    'def _is_claude_process_running(intern_name, project=None):\n'
    '    """Check if Claude CLI is actually running in the tmux pane (not just bash).\n'
    '\n'
    "    Returns True if the current command in the pane is 'claude', False otherwise.\n"
    "    A tmux session can exist but Claude may have /exit'd, leaving only bash.\n"
    '    """\n'
    '    return _is_tmux_cli_process_running(intern_name, "claude", project=project)'
)
NEW = (
    'def _is_tmux_cli_child_process_running(intern_name, needle, project=None):\n'
    '    """Detect a CLI running as a child of the pane\'s shell. Resumed providers\n'
    "    run under a bash wrapper so pane_current_command reports 'bash'; enumerate\n"
    '    children of pane_pid via `ps --ppid` and match on the needle."""\n'
    '    session_name = _resolve_tmux_session_name(intern_name, project=project)\n'
    '    if not _check_tmux_session(intern_name, project=project):\n'
    '        return False\n'
    '    try:\n'
    '        result = subprocess.run(\n'
    '            ["tmux", "list-panes", "-t", f"={session_name}", "-F", "#{pane_pid}"],\n'
    '            capture_output=True, text=True\n'
    '        )\n'
    '        pane_pid = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""\n'
    '        if not pane_pid:\n'
    '            return False\n'
    '        children = subprocess.run(\n'
    '            ["ps", "--ppid", pane_pid, "-o", "args="],\n'
    '            capture_output=True, text=True\n'
    '        )\n'
    '        return needle in children.stdout.lower()\n'
    '    except (subprocess.CalledProcessError, FileNotFoundError, IndexError):\n'
    '        return False\n'
    '\n'
    '\n'
    'def _is_claude_process_running(intern_name, project=None):\n'
    '    """Check if Claude CLI is actually running in the tmux pane (not just bash).\n'
    '\n'
    "    Returns True if the current command in the pane is 'claude', False otherwise.\n"
    "    A tmux session can exist but Claude may have /exit'd, leaving only bash.\n"
    '    """\n'
    '    if _is_tmux_cli_process_running(intern_name, "claude", project=project):\n'
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
        open(p + ".bak.copilotlive134", "w").write(s)
        open(p, "w", encoding="utf-8").write(s.replace(OLD, NEW, 1))
        print("patched:", p)
    else:
        print("PATTERN NOT FOUND:", p)
