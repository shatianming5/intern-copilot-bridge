import json, os, sys, time

old_sid = sys.argv[1]
cwd = sys.argv[2]
EV = os.path.expanduser("~/.copilot/session-state/%s/events.jsonl" % old_sid)

msgs, users = [], []
if os.path.exists(EV):
    for l in open(EV):
        try:
            d = json.loads(l)
            t = d.get("type")
            if t == "assistant.message":
                c = (d.get("data") or {}).get("content", "")
                if c and len(c.strip()) > 40:
                    msgs.append((d.get("timestamp", "")[:19], c.strip()))
            elif t == "user.message":
                c = (d.get("data") or {}).get("content", "")
                if c and "please continue" not in c.lower():
                    users.append((d.get("timestamp", "")[:19], c.strip()[:200]))
        except Exception:
            pass

out = os.path.join(cwd, "HANDOFF_FRESH_RESTART.md")
try:
    with open(out, "w") as f:
        f.write("# Handoff — fresh session restart (%s)\n\n" % time.strftime("%Y-%m-%d %H:%M"))
        f.write("The previous copilot session (`%s`) grew too large and began confabulating\n" % old_sid[:8])
        f.write("'polluted' tool output. It was reset to a fresh session. Re-orient from the\n")
        f.write("repo docs (GOAL*.md, README.md, docs/) plus the notes below.\n\n")
        f.write("## Recent user intents\n")
        for ts, c in users[-6:]:
            f.write("- [%s] %s\n" % (ts, c.replace("\n", " ")))
        f.write("\n## Last substantive conclusions (most recent first)\n\n")
        for ts, c in list(reversed(msgs))[:8]:
            f.write("### %s\n%s\n\n" % (ts, c[:1200]))
    print("HANDOFF_OK %s (asst=%d user=%d)" % (out, len(msgs), len(users)))
except Exception as e:
    print("HANDOFF_WARN", e)
