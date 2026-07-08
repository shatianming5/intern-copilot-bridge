import json, os, sys, subprocess, time

name, new_sid = sys.argv[1], sys.argv[2]
WA = os.path.expanduser("~/work-agents")
ci = os.path.join(WA, ".copilot_interns.json")
isj = os.path.join(WA, ".intern_sessions.json")
ts = time.strftime("%H%M%S")
for f in (ci, isj):
    if os.path.exists(f):
        subprocess.run(["cp", f, f + f".bak.freshrestart{ts}"])

# .copilot_interns.json
d = json.load(open(ci))
items = d if isinstance(d, list) else d.get("interns", [])
old = None
for it in items:
    if it["name"] == name:
        old = it["sid"]; it["sid"] = new_sid
json.dump(d, open(ci, "w"), ensure_ascii=False, indent=2)

# .intern_sessions.json copilot_sid
s = json.load(open(isj))
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
json.dump(s, open(isj, "w"), ensure_ascii=False, indent=2)

# clear stream state so poller starts clean (no orphan-close of old msg)
st = os.path.join(WA, f".copstream_{name}.state")
if os.path.exists(st):
    os.rename(st, st + f".bak{ts}")

print("REG_OK %s: %s -> %s" % (name, (old or "?")[:8], new_sid[:8]))
