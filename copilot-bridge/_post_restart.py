import json, os, sys
sys.path.insert(0, os.path.expanduser("~/work-agents/vendor"))
import feishu_api as fa

new_sid = sys.argv[1]
chat = sys.argv[2]

EV = os.path.expanduser("~/.copilot/session-state/%s/events.jsonl" % new_sid)
summary = ""
for l in open(EV):
    try:
        d = json.loads(l)
        if d.get("type") == "assistant.message":
            c = (d.get("data") or {}).get("content", "")
            if c and len(c.strip()) > 40:
                summary = c.strip()
    except Exception:
        pass

pol = json.load(open(os.path.expanduser("~/work-agents/enterprise_policy/daemon/policy.json")))["feishu"]
tok = fa.get_tenant_token(pol["app_id"], pol["app_secret"])
msg = "🔄 **已重启为全新会话**（原会话上下文过大、出现幻觉污染，已重置）。\n新会话已读项目文档重新定向，摘要如下：\n\n" + summary[:1500]
mid, err = fa.send_message(tok, chat, msg)
print("POSTED" if mid else ("ERR " + str(err)))
