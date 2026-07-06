"""
飞书 API 封装 — 纯 HTTP 调用，无状态。
所有 API 调用返回 (result, error_msg) 元组，调用方可以选择记录 error_msg。
"""
import json
import re
import time
import urllib.request
import urllib.error
from urllib.parse import unquote, urlparse

BASE_URL = "https://open.feishu.cn/open-apis"

_MARKDOWN_LINK_RE = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
_LINE_SUFFIX_RE = re.compile(r"^(?P<path>.+?)(?P<suffix>:\d+(?::\d+)?)?$")
_GENERIC_FILE_LINK_LABELS = {
    "click here",
    "file",
    "here",
    "link",
    "source",
    "this",
}


def _strip_markdown_link_target(href):
    href = str(href or "").strip()
    if href.startswith("<") and href.endswith(">"):
        href = href[1:-1].strip()
    return href


def _split_local_file_target(href):
    href = _strip_markdown_link_target(href)
    if not href or href.startswith(("http://", "https://")):
        return None
    if href.startswith("file://"):
        parsed = urlparse(href)
        if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
            return None
        target = unquote(parsed.path or "")
    elif "://" in href:
        return None
    else:
        target = unquote(href)

    match = _LINE_SUFFIX_RE.match(target)
    if not match:
        return None
    path = match.group("path") or ""
    suffix = match.group("suffix") or ""
    path = path.strip()
    if not _looks_like_local_file_path(path):
        return None
    return path, suffix


def _looks_like_local_file_path(path):
    path = str(path or "")
    if path.startswith(("/", "~/", "./", "../")):
        return True
    basename = path.rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1]
    return "/" in path and "." in basename


def _display_label_for_local_file(label, path):
    label = str(label or "").strip()
    if not label or len(label) > 80:
        return None
    if label.startswith(("/", "~/", "./", "../")) or "://" in label:
        return None
    basename = label.rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1]
    if not basename or basename.lower() in _GENERIC_FILE_LINK_LABELS:
        return None
    path_basename = path.rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1]
    if basename == path_basename or "." in basename:
        return label
    return None


def _compact_local_file_link(label, href):
    target = _split_local_file_target(href)
    if not target:
        return None
    path, suffix = target
    display = _display_label_for_local_file(label, path)
    if not display:
        display = path.rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1]
    if not display:
        return None
    if suffix and not re.search(r":\d+(?::\d+)?$", display):
        display += suffix
    return display


def compact_local_file_markdown_links(text):
    """Shrink local file markdown links before Feishu wrapping/rendering.

    HTTP(S) links remain markdown links so the post renderer can keep them as
    Feishu anchors. Local paths stay plain text because Feishu rejects them as
    href values.
    """
    raw = "" if text is None else str(text)
    lines = raw.split("\n")
    compacted = []
    in_code_block = False

    def replace_link(match):
        replacement = _compact_local_file_link(match.group(1), match.group(2))
        return replacement if replacement is not None else match.group(0)

    for line in lines:
        if line.strip().startswith("```"):
            compacted.append(line)
            in_code_block = not in_code_block
            continue
        if in_code_block:
            compacted.append(line)
        else:
            compacted.append(_MARKDOWN_LINK_RE.sub(replace_link, line))
    return "\n".join(compacted)


def get_tenant_token(app_id, app_secret, state=None):
    """获取 tenant_access_token。优先从 state 缓存读取，过期后重新请求。"""
    now = time.time()

    # Try cached token from state (survives across hook processes)
    if state:
        cached = state.get("feishu", {}).get("_token_cache", {})
        if cached.get("token") and now < cached.get("expires_at", 0) - 300:
            return cached["token"]

    url = f"{BASE_URL}/auth/v3/tenant_access_token/internal"
    payload = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        if result.get("code") == 0:
            token = result["tenant_access_token"]
            expire = result.get("expire", 7200)
            # Save to state for next hook process
            if state:
                fs = state.setdefault("feishu", {})
                fs["_token_cache"] = {
                    "token": token,
                    "expires_at": now + expire,
                }
            return token
    except Exception:
        pass
    return None


def _parse_inline(text):
    """将一行文本中的 Markdown 内联标记转换为飞书 post tag 数组。
    
    支持：**bold**、*italic*、`code`、[text](url)
    """
    tags = []
    # 正则匹配：**bold** | *italic* | `code` | [text](url) | 普通文本
    pattern = re.compile(
        r'\*\*(.+?)\*\*'         # **bold**
        r'|\*(.+?)\*'            # *italic*
        r'|`(.+?)`'             # `code`
        r'|\[([^\]]+)\]\(([^)]+)\)'  # [text](url)
    )
    last = 0
    for m in pattern.finditer(text):
        # 前面的普通文本
        if m.start() > last:
            tags.append({"tag": "text", "text": text[last:m.start()]})
        if m.group(1) is not None:  # **bold**
            tags.append({"tag": "text", "text": m.group(1), "style": ["bold"]})
        elif m.group(2) is not None:  # *italic*
            tags.append({"tag": "text", "text": m.group(2), "style": ["italic"]})
        elif m.group(3) is not None:  # `code`
            tags.append({"tag": "text", "text": m.group(3), "style": ["code_inline"]})
        elif m.group(4) is not None:  # [text](url)
            href = m.group(5)
            # 飞书要求 href 是合法 URL（http/https），非法 href 会导致 230001 错误
            if href.startswith(("http://", "https://")):
                tags.append({"tag": "a", "text": m.group(4), "href": href})
            else:
                # 本地文件路径压缩成可读短引用；其他非法 URL 保持旧的纯文本降级。
                compacted = _compact_local_file_link(m.group(4), href)
                display_text = compacted if compacted is not None else f"{m.group(4)}({href})"
                tags.append({"tag": "text", "text": display_text})
        last = m.end()
    # 尾部普通文本
    if last < len(text):
        tags.append({"tag": "text", "text": text[last:]})
    if not tags:
        tags.append({"tag": "text", "text": text})
    return tags


def build_post_content(text):
    """构建飞书 post 消息的 content JSON 字符串。
    
    支持特殊标记：
    - --- → hr 分割线
    - ```...``` → code_block 代码块
    - **bold** → 加粗
    - *italic* → 斜体
    - `code` → 行内代码
    - [text](url) → 链接（仅 http/https）
    """
    lines = text.split("\n")
    content_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # 代码块开始标记
        if line.strip().startswith("```"):
            # 收集代码块内容
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1  # 跳过结束的 ```
            code_text = "\n".join(code_lines)
            content_lines.append([{"tag": "code_block", "language": "PLAINTEXT", "text": code_text}])
        elif line.strip() == "---":
            content_lines.append([{"tag": "hr"}])
            i += 1
        else:
            content_lines.append(_parse_inline(line))
            i += 1
    post = {"zh_cn": {"title": "", "content": content_lines}}
    return json.dumps(post)


def estimate_post_body_size(content_text):
    """估算 post 消息请求体大小（字节），用于 content-length 溢出预判。

    飞书 post 消息限制 30KB 请求体。此函数模拟 update_message 的请求体结构
    来估算实际大小。
    """
    content_json = build_post_content(content_text)
    body = json.dumps({"msg_type": "post", "content": content_json})
    return len(body.encode("utf-8"))


def send_message(token, chat_id, content_text):
    """POST 创建新消息，返回 (message_id, error_msg)。"""
    url = f"{BASE_URL}/im/v1/messages?receive_id_type=chat_id"
    body = json.dumps({
        "receive_id": chat_id,
        "msg_type": "post",
        "content": build_post_content(content_text),
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        if result.get("code") == 0:
            return result["data"]["message_id"], None
        else:
            return None, f"feishu code={result.get('code')} msg={result.get('msg')} content_len={len(content_text)}"
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")[:500]
        return None, f"HTTP {e.code}: {body_text} content_len={len(content_text)}"
    except Exception as e:
        return None, f"Exception: {e} content_len={len(content_text)}"


def update_message(token, msg_id, content_text):
    """PUT 更新已有消息，返回 (success, error_msg)。"""
    url = f"{BASE_URL}/im/v1/messages/{msg_id}"
    body = json.dumps({
        "msg_type": "post",
        "content": build_post_content(content_text),
    }).encode()
    req = urllib.request.Request(url, data=body, method="PUT", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        if result.get("code") == 0:
            return True, None
        else:
            return False, f"feishu code={result.get('code')} msg={result.get('msg')} msg_id={msg_id} content_len={len(content_text)}"
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")[:500]
        return False, f"HTTP {e.code}: {body_text} msg_id={msg_id} content_len={len(content_text)}"
    except Exception as e:
        return False, f"Exception: {e} msg_id={msg_id} content_len={len(content_text)}"


# ── extensions for copilot bridge: images + interactive cards ──────────────
import os as _os


def upload_image(token, image_path):
    """Upload an image to Feishu, return (image_key, error)."""
    try:
        boundary = "----copilotbridge" + str(int(time.time() * 1000))
        with open(image_path, "rb") as f:
            img = f.read()
        parts = []
        parts.append(("--" + boundary).encode())
        parts.append(b'Content-Disposition: form-data; name="image_type"')
        parts.append(b"")
        parts.append(b"message")
        parts.append(("--" + boundary).encode())
        fn = _os.path.basename(image_path)
        parts.append(
            f'Content-Disposition: form-data; name="image"; filename="{fn}"'.encode())
        parts.append(b"Content-Type: application/octet-stream")
        parts.append(b"")
        parts.append(img)
        parts.append(("--" + boundary + "--").encode())
        body = b"\r\n".join(parts)
        req = urllib.request.Request(
            f"{BASE_URL}/im/v1/images", data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                     "Authorization": f"Bearer {token}"})
        resp = urllib.request.urlopen(req, timeout=20)
        result = json.loads(resp.read())
        if result.get("code") == 0:
            return result["data"]["image_key"], None
        return None, f"feishu code={result.get('code')} msg={result.get('msg')}"
    except Exception as e:
        return None, f"upload_image exc: {e}"


def send_image(token, chat_id, image_key):
    """Send an image message, return (message_id, error)."""
    url = f"{BASE_URL}/im/v1/messages?receive_id_type=chat_id"
    body = json.dumps({"receive_id": chat_id, "msg_type": "image",
                       "content": json.dumps({"image_key": image_key})}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        if result.get("code") == 0:
            return result["data"]["message_id"], None
        return None, f"feishu code={result.get('code')} msg={result.get('msg')}"
    except Exception as e:
        return None, f"send_image exc: {e}"


def send_interactive(token, chat_id, card):
    """Send an interactive card (dict), return (message_id, error)."""
    url = f"{BASE_URL}/im/v1/messages?receive_id_type=chat_id"
    body = json.dumps({"receive_id": chat_id, "msg_type": "interactive",
                       "content": json.dumps(card)}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        if result.get("code") == 0:
            return result["data"]["message_id"], None
        return None, f"feishu code={result.get('code')} msg={result.get('msg')}"
    except Exception as e:
        return None, f"send_interactive exc: {e}"
