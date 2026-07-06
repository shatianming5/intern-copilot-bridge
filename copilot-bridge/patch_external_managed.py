#!/usr/bin/env python3
"""Make the daemon's provider-restart iterator skip externally-managed /
copilot-converted interns, so it never respawns claude over a running copilot
session (cop_*). Idempotent."""
import sys

OLD = (
    '    for key, entry in data.items():\n'
    '        if not isinstance(entry, dict) or entry.get("type") != provider:\n'
    '            continue\n'
    '        name = str(entry.get("intern_name") or str(key).split(":", 1)[-1])'
)
NEW = (
    '    for key, entry in data.items():\n'
    '        if not isinstance(entry, dict) or entry.get("type") != provider:\n'
    '            continue\n'
    '        # copilot-CLI interns are externally managed (keeper owns the tmux);\n'
    '        # never respawn a provider CLI over them.\n'
    '        if entry.get("external_managed") or entry.get("provider") == "copilot":\n'
    '            continue\n'
    '        name = str(entry.get("intern_name") or str(key).split(":", 1)[-1])'
)
MARK = "never respawn a provider CLI over them"

for p in sys.argv[1:]:
    s = open(p, encoding="utf-8").read()
    if MARK in s:
        print("already patched:", p)
        continue
    if OLD in s:
        open(p + ".bak.extmgd", "w").write(s)
        open(p, "w", encoding="utf-8").write(s.replace(OLD, NEW, 1))
        print("patched:", p)
    else:
        print("PATTERN NOT FOUND:", p)
