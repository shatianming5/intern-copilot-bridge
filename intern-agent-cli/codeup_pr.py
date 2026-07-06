#!/usr/bin/env python3
"""
codeup_pr.py — Codeup MR CLI，与 `gh pr` 对等的命令行接口。

封装阿里云 Codeup OpenAPI（openapi-rdc.aliyuncs.com），供 LLM intern 在终端直接调用。

用法:
    python3 codeup_pr.py create --title "..." --base master [--body "..."]
    python3 codeup_pr.py merge <local_id> [--merge-type no-fast-forward|squash|rebase|ff-only]
    python3 codeup_pr.py view <local_id> [--json state,mergedAt]
    python3 codeup_pr.py list [--head <branch>] [--state opened|merged|closed]

认证:
    环境变量 CODEUP_ACCESS_TOKEN（~/.bashrc 中 export）
    通过 x-yunxiao-token 请求头认证，不需要 AK/SK

organizationId 自动获取:
    环境变量 CODEUP_ORG_ID，或通过 ListOrganizations API 自动解析

仓库信息:
    从当前 git remote URL 提取路径，通过 ListRepositories API 自动获取 repositoryId
    或从环境变量 CODEUP_REPOSITORY_ID
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ============================================================
# 配置 & 常量
# ============================================================

CODEUP_API_BASE = "https://openapi-rdc.aliyuncs.com"
# ============================================================
# 认证 & 自动解析
# ============================================================

def load_auth():
    """加载 Codeup 认证信息，返回 (access_token, org_id)。

    优先级：CODEUP_ACCESS_TOKEN / CODEUP_ORG_ID 环境变量 > API 自动获取 orgId。
    """
    token = os.environ.get("CODEUP_ACCESS_TOKEN", "")
    if not token:
        print("错误: 未找到 Codeup access token。请在 ~/.bashrc 中添加:", file=sys.stderr)
        print('  export CODEUP_ACCESS_TOKEN="pt-xxxxxx"', file=sys.stderr)
        sys.exit(1)

    org_id = os.environ.get("CODEUP_ORG_ID", "")
    if not org_id:
        org_id = _resolve_org_id(token)

    return token, org_id


def _resolve_org_id(token):
    """通过 ListOrganizations API 自动获取 organizationId。"""
    url = f"{CODEUP_API_BASE}/oapi/v1/platform/organizations"
    headers = {
        "Content-Type": "application/json",
        "x-yunxiao-token": token,
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            orgs = json.loads(resp.read().decode())
        if not isinstance(orgs, list) or not orgs:
            print("错误: ListOrganizations 返回空，请确认 Token 有效。", file=sys.stderr)
            sys.exit(1)
        if len(orgs) == 1:
            return orgs[0].get("id", "")
        # 多企业：取第一个
        return orgs[0].get("id", "")
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode()
        except Exception:
            pass
        print(f"错误: 获取 organizationId 失败 ({e.code}): {error_body or e.reason}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"错误: 网络异常: {e.reason}", file=sys.stderr)
        sys.exit(1)


def get_repository_id():
    """从 git remote URL 自动获取 Codeup repositoryId。

    优先级：环境变量 > ListRepositories API 匹配路径
    """
    repo_id = os.environ.get("CODEUP_REPOSITORY_ID", "")
    if repo_id:
        return int(repo_id)

    # 从 git remote URL 提取路径
    remote_url = ""
    try:
        remote_url = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    if not remote_url or "codeup.aliyun.com" not in remote_url:
        print("错误: 当前目录不是 Codeup 仓库 (remote URL 不含 codeup.aliyun.com)。", file=sys.stderr)
        print("请在 Codeup 仓库目录下执行，或设置环境变量 CODEUP_REPOSITORY_ID。", file=sys.stderr)
        sys.exit(1)

    repo_path = _extract_repo_path_from_url(remote_url)
    if not repo_path:
        print(f"错误: 无法从 remote URL 提取路径: {remote_url}", file=sys.stderr)
        sys.exit(1)

    resolved = _resolve_repository_id(repo_path)
    if resolved:
        return resolved

    print(f"错误: 无法通过 API 解析 repositoryId (路径: {repo_path})。", file=sys.stderr)
    print("请设置环境变量 CODEUP_REPOSITORY_ID。", file=sys.stderr)
    sys.exit(1)


def _extract_repo_path_from_url(url):
    """从 Codeup remote URL 提取完整路径 (org/group/repo)。

    支持格式:
        https://codeup.aliyun.com/<org>/<group>/<repo>.git
        git@codeup.aliyun.com:<org>/<group>/<repo>.git
    返回: "org/group/repo" 格式（pathWithNamespace）
    """
    # HTTPS
    m = re.search(r'codeup\.aliyun\.com/(.+?)(?:\.git)?$', url)
    if m:
        return m.group(1)
    # SSH
    m = re.search(r'codeup\.aliyun\.com:(.+?)(?:\.git)?$', url)
    if m:
        return m.group(1)
    return None


def _resolve_repository_id(repo_path):
    """通过 ListRepositories API 匹配 pathWithNamespace 获取 repositoryId。"""
    token, org_id = load_auth()
    page = 1
    per_page = 50
    while True:
        url = (f"{CODEUP_API_BASE}/oapi/v1/codeup/organizations/{org_id}/repositories"
               f"?page={page}&perPage={per_page}")
        headers = {
            "Content-Type": "application/json",
            "x-yunxiao-token": token,
        }
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except (urllib.error.HTTPError, urllib.error.URLError):
            return None

        repos = data if isinstance(data, list) else data.get("result", [])
        if not repos:
            return None

        for repo in repos:
            pwn = repo.get("pathWithNamespace", "")
            if pwn == repo_path:
                return repo.get("id")
            # 也尝试不含 org 前缀的匹配
            if "/" in repo_path and pwn.endswith(repo_path):
                return repo.get("id")

        if len(repos) < per_page:
            return None
        page += 1


def get_current_branch():
    """获取当前 git 分支名。"""
    try:
        return subprocess.check_output(
            ["git", "branch", "--show-current"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


# ============================================================
# API 调用
# ============================================================

def api_request(method, path, token, body=None, params=None, retries=0):
    """发送 Codeup API 请求。

    Args:
        method: HTTP 方法
        path: API 路径 (如 /oapi/v1/codeup/organizations/{orgId}/...)
        token: 个人访问令牌
        body: 请求 body (dict)
        params: URL query params (dict)
        retries: 失败时重试次数 (针对已知瞬时错误)

    Returns:
        解析后的 JSON 响应
    """
    url = f"{CODEUP_API_BASE}{path}"
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)

    headers = {
        "Content-Type": "application/json",
        "x-yunxiao-token": token,
    }

    data = json.dumps(body).encode() if body else None

    for attempt in range(1 + retries):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                response_body = resp.read().decode()
                if response_body:
                    return json.loads(response_body)
                return {}
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode()
            except Exception:
                pass
            # Codeup 有瞬时错误 "source commit can not be null"，重试可恢复
            if attempt < retries and "source commit can not be null" in error_body:
                print(f"API 瞬时错误，{attempt + 1}/{retries} 次重试...", file=sys.stderr)
                time.sleep(2 * (attempt + 1))
                continue
            print(f"API 错误 ({e.code}): {error_body or e.reason}", file=sys.stderr)
            sys.exit(1)
        except urllib.error.URLError as e:
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            print(f"网络错误: {e.reason}", file=sys.stderr)
            sys.exit(1)


def _mr_base_path(org_id, repo_id):
    """返回 MR API 的基础路径 (repo 级别)。"""
    return f"/oapi/v1/codeup/organizations/{org_id}/repositories/{repo_id}/changeRequests"


# ============================================================
# 子命令实现
# ============================================================

def cmd_create(args):
    """创建 Merge Request (changeRequest)。"""
    token, org_id = load_auth()
    repo_id = get_repository_id()
    source_branch = args.head or get_current_branch()
    if not source_branch:
        print("错误: 无法确定 source branch，请用 --head 指定。", file=sys.stderr)
        sys.exit(1)

    body = {
        "title": args.title,
        "sourceBranch": source_branch,
        "sourceProjectId": repo_id,
        "targetBranch": args.base,
        "targetProjectId": repo_id,
        "createFrom": "WEB",
    }
    if args.body:
        body["description"] = args.body

    result = api_request("POST", _mr_base_path(org_id, repo_id), token, body=body, retries=2)

    local_id = result.get("localId", "?")
    status = result.get("status", result.get("state", "UNDER_DEV"))
    detail_url = result.get("detailUrl", "")
    source = result.get("sourceBranch", source_branch)
    target = result.get("targetBranch", args.base)

    print(f"MR #{local_id} 创建成功")
    print(f"  状态: {status}")
    print(f"  分支: {source} → {target}")
    if detail_url:
        print(f"  链接: {detail_url}")


def cmd_merge(args):
    """合并 Merge Request。"""
    token, org_id = load_auth()
    repo_id = get_repository_id()

    # 映射用户友好名称到 API 值
    merge_type_map = {
        "merge": "no-fast-forward",
        "no-fast-forward": "no-fast-forward",
        "ff-only": "ff-only",
        "squash": "squash",
        "rebase": "rebase",
    }
    merge_type = merge_type_map.get(args.merge_type, "no-fast-forward")

    body = {"mergeType": merge_type}

    path = f"{_mr_base_path(org_id, repo_id)}/{args.local_id}/merge"
    result = api_request("POST", path, token, body=body)

    status = result.get("status", result.get("state", "MERGED"))
    merged_rev = result.get("mergedRevision", "")
    print(f"MR #{args.local_id} 合并成功 (status: {status})")
    if merged_rev:
        print(f"  mergedRevision: {merged_rev[:12]}")


def cmd_view(args):
    """查看 Merge Request 详情。"""
    token, org_id = load_auth()
    repo_id = get_repository_id()

    path = f"{_mr_base_path(org_id, repo_id)}/{args.local_id}"
    mr = api_request("GET", path, token)

    if args.json:
        fields = [f.strip() for f in args.json.split(",")]
        filtered = {k: mr.get(k) for k in fields}
        print(json.dumps(filtered, ensure_ascii=False, indent=2))
    else:
        local_id = mr.get("localId", args.local_id)
        title = mr.get("title", "")
        status = mr.get("status", mr.get("state", "unknown"))
        author = mr.get("author", {}).get("name", "unknown")
        source = mr.get("sourceBranch", "")
        target = mr.get("targetBranch", "")
        detail_url = mr.get("detailUrl", "")
        created = mr.get("createdAt", mr.get("createTime", ""))
        merged_rev = mr.get("mergedRevision", "")

        print(f"MR #{local_id}: {title}")
        print(f"  状态: {status}")
        print(f"  作者: {author}")
        print(f"  分支: {source} → {target}")
        if detail_url:
            print(f"  链接: {detail_url}")
        if created:
            print(f"  创建: {created}")
        if merged_rev:
            print(f"  合并 revision: {merged_rev[:12]}")


def cmd_list(args):
    """列出 Merge Requests（组织级别 API，用 projectIds 过滤）。"""
    token, org_id = load_auth()
    repo_id = get_repository_id()

    # ListChangeRequests 是组织级别的端点
    path = f"/oapi/v1/codeup/organizations/{org_id}/changeRequests"
    params = {
        "projectIds": str(repo_id),
        "perPage": "20",
    }
    if args.state:
        params["state"] = args.state

    result = api_request("GET", path, token, params=params)

    mrs = result if isinstance(result, list) else []
    if not mrs:
        print("没有找到匹配的 MR", file=sys.stderr)
        return

    # 如果指定了 --head，在客户端过滤 sourceBranch（支持前缀匹配，如 "intern_name/" 匹配所有以此开头的分支）
    if args.head:
        prefix = args.head
        mrs = [mr for mr in mrs if mr.get("sourceBranch", "").startswith(prefix)]
        if not mrs:
            print(f"没有找到 sourceBranch 以 {prefix} 开头的 MR", file=sys.stderr)
            return

    for mr in mrs:
        local_id = mr.get("localId", "?")
        title = mr.get("title", "")
        state = mr.get("state", "")
        source = mr.get("sourceBranch", "")
        print(f"  #{local_id}\t{state}\t{source}\t{title}")


def cmd_close(args):
    """关闭 Merge Request。"""
    token, org_id = load_auth()
    repo_id = get_repository_id()

    path = f"{_mr_base_path(org_id, repo_id)}/{args.local_id}/close"
    result = api_request("POST", path, token)

    status = result.get("status", result.get("state", "CLOSED"))
    print(f"MR #{args.local_id} 已关闭 (status: {status})")


# ============================================================
# CLI 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Codeup MR CLI — 与 gh pr 对等的命令行工具",
        prog="codeup_pr",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # create
    p_create = subparsers.add_parser("create", help="创建 Merge Request")
    p_create.add_argument("--title", required=True, help="MR 标题")
    p_create.add_argument("--base", required=True, help="目标分支")
    p_create.add_argument("--head", help="源分支 (默认当前分支)")
    p_create.add_argument("--body", help="MR 描述")
    p_create.set_defaults(func=cmd_create)

    # merge
    p_merge = subparsers.add_parser("merge", help="合并 Merge Request")
    p_merge.add_argument("local_id", help="MR localId (库内序号)")
    p_merge.add_argument("--merge-type",
                         choices=["merge", "no-fast-forward", "ff-only", "squash", "rebase"],
                         default="squash", help="合并方式 (默认 squash —— 分支多次 commit 会被压成单一 commit)")
    p_merge.set_defaults(func=cmd_merge)

    # view
    p_view = subparsers.add_parser("view", help="查看 MR 详情")
    p_view.add_argument("local_id", help="MR localId (库内序号)")
    p_view.add_argument("--json", help="输出指定字段 (逗号分隔)")
    p_view.set_defaults(func=cmd_view)

    # list
    p_list = subparsers.add_parser("list", help="列出 Merge Requests")
    p_list.add_argument("--head", help="按源分支过滤")
    p_list.add_argument("--state", choices=["opened", "merged", "closed"],
                        help="按状态过滤")
    p_list.set_defaults(func=cmd_list)

    # close
    p_close = subparsers.add_parser("close", help="关闭 Merge Request")
    p_close.add_argument("local_id", help="MR localId (库内序号)")
    p_close.set_defaults(func=cmd_close)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
