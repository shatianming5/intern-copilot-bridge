#!/usr/bin/env python3
"""Extract a clean, size-bounded handoff transcript from a Claude Code .jsonl
session so a fresh Copilot-CLI intern can inherit the prior context.

Keeps: the FIRST real user message (the original task) + the most recent
dialogue (user text + assistant text), dropping tool I/O, thinking, sidechains.

Usage: claude_handoff.py <input.jsonl> <output.md> [tail_chars]
"""
import json
import sys


def blocks_text(content):
    """Return visible text from a message.content (str or list of blocks)."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    out = []
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            t = (b.get("text") or "").strip()
            if t:
                out.append(t)
    return "\n".join(out).strip()


def is_tool_result(content):
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                return True
    return False


def main():
    src, dst = sys.argv[1], sys.argv[2]
    tail_chars = int(sys.argv[3]) if len(sys.argv) > 3 else 140000

    turns = []  # (role, text)
    with open(src, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("isSidechain") or e.get("isMeta"):
                continue
            typ = e.get("type")
            msg = e.get("message") or {}
            content = msg.get("content")
            if typ == "user":
                if is_tool_result(content):
                    continue
                txt = blocks_text(content)
                # skip system-injected reminders / empty
                if not txt or txt.startswith("<"):
                    continue
                turns.append(("User", txt))
            elif typ == "assistant":
                txt = blocks_text(content)
                if txt:
                    turns.append(("Assistant", txt))

    if not turns:
        with open(dst, "w", encoding="utf-8") as o:
            o.write("(no recoverable dialogue found in transcript)\n")
        print(f"handoff: 0 turns -> {dst}")
        return

    first_user = None
    for role, txt in turns:
        if role == "User":
            first_user = txt
            break

    # Build the recent-dialogue tail within the char budget.
    rendered = []
    for role, txt in turns:
        rendered.append(f"### {role}\n{txt}\n")
    body = "\n".join(rendered)

    header = []
    header.append("# Handoff transcript (inherited from your prior Claude session)\n")
    if first_user:
        header.append("## Original task (first user message)\n")
        header.append(first_user.strip() + "\n")
    header.append(f"\n## Conversation ({len(turns)} turns; most recent shown)\n")
    head = "\n".join(header)

    if len(body) > tail_chars:
        body = "…[earlier turns omitted]…\n\n" + body[-tail_chars:]

    with open(dst, "w", encoding="utf-8") as o:
        o.write(head + "\n" + body + "\n")
    print(f"handoff: {len(turns)} turns, {len(head)+len(body)} chars -> {dst}")


if __name__ == "__main__":
    main()
