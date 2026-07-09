import json, os, sys

# Enable/disable an intern in .copilot_interns.json so the keeper won't
# (re)build its tmux/poller — used to freeze an intern during a fresh restart
# so the old session can't be resurrected while we swap sessions.
name = sys.argv[1]
val = sys.argv[2].lower() in ("1", "true", "yes", "on")
WA = os.path.expanduser("~/work-agents")
ci = os.path.join(WA, ".copilot_interns.json")

d = json.load(open(ci))
items = d if isinstance(d, list) else d.get("interns", [])
hit = False
for it in items:
    if it.get("name") == name:
        it["enabled"] = val
        hit = True
json.dump(d, open(ci, "w"), ensure_ascii=False, indent=2)
print("ENABLED_%s %s -> %s" % ("OK" if hit else "MISS", name, val))
