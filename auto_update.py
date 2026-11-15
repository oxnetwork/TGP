# -*- coding: utf-8 -*-
"""Auto-update helpers: GitHub source upload + Railway check/redeploy."""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import time
import urllib.error
import urllib.request
import zipfile
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("auto_update")

GH_API = "https://api.github.com"
RW_GQL = "https://backboard.railway.app/graphql/v2"


def _gh_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "phoenix-auto-update",
        "Content-Type": "application/json",
    }


def _rw_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "phoenix-auto-update",
    }


def _http_json(method: str, url: str, headers: dict, data: Optional[dict] = None, timeout: float = 60) -> Tuple[int, dict]:
    body = None if data is None else json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "ignore")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", "ignore")
        try:
            parsed = json.loads(err) if err else {}
        except Exception:
            parsed = {"message": err}
        return e.code, parsed
    except Exception as e:
        return 0, {"message": str(e)}


def github_latest_commit(token: str, owner: str, repo: str, branch: str = "main") -> Tuple[bool, dict]:
    code, data = _http_json(
        "GET",
        f"{GH_API}/repos/{owner}/{repo}/commits/{branch}",
        _gh_headers(token),
    )
    if code != 200:
        return False, {"error": data.get("message") or str(data), "code": code}
    commit = (data.get("commit") or {})
    return True, {
        "sha": data.get("sha") or "",
        "message": (commit.get("message") or "").split("\n")[0][:200],
        "date": ((commit.get("author") or {}).get("date") or ""),
        "url": data.get("html_url") or f"https://github.com/{owner}/{repo}/commit/{data.get('sha','')}",
        "author": ((commit.get("author") or {}).get("name") or ""),
    }


def github_put_file(token: str, owner: str, repo: str, path: str, content_bytes: bytes, message: str, branch: str = "main") -> Tuple[bool, str]:
    """Create or update a single file via Contents API."""
    # get existing sha
    code, existing = _http_json(
        "GET",
        f"{GH_API}/repos/{owner}/{repo}/contents/{path}?ref={branch}",
        _gh_headers(token),
    )
    sha = existing.get("sha") if code == 200 else None
    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha
    code2, data = _http_json(
        "PUT",
        f"{GH_API}/repos/{owner}/{repo}/contents/{path}",
        _gh_headers(token),
        payload,
    )
    if code2 in (200, 201):
        return True, (data.get("content") or {}).get("html_url") or "ok"
    return False, data.get("message") or str(data)[:300]


def upload_source_zip_to_github(
    token: str,
    owner: str,
    repo: str,
    zip_bytes: bytes,
    branch: str = "main",
    commit_message: str = "chore: source update via bot",
    max_files: int = 80,
) -> dict:
    """
    Extract zip and push each text/source file to GitHub.
    Skips huge binaries, venv, .git, __pycache__.
    """
    result = {"ok": False, "uploaded": 0, "skipped": 0, "errors": [], "files": []}
    skip_parts = (".git/", "__pycache__/", ".venv/", "venv/", "node_modules/", ".pyc")
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except Exception as e:
        result["errors"].append(f"invalid zip: {e}")
        return result

    names = zf.namelist()
    # strip single top-level folder if present
    tops = set()
    for n in names:
        if n.endswith("/"):
            continue
        tops.add(n.split("/")[0] if "/" in n else "")
    prefix = ""
    if len(tops) == 1:
        only = next(iter(tops))
        if only and all(n.startswith(only + "/") or n == only for n in names if not n.endswith("/")):
            prefix = only + "/"

    count = 0
    for name in names:
        if name.endswith("/"):
            continue
        rel = name[len(prefix):] if prefix and name.startswith(prefix) else name
        rel = rel.lstrip("/")
        if not rel:
            continue
        low = rel.replace("\\", "/")
        if any(s in low for s in skip_parts):
            result["skipped"] += 1
            continue
        if low.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".zip", ".7z", ".rar", ".db", ".sqlite")):
            result["skipped"] += 1
            continue
        try:
            data = zf.read(name)
        except Exception as e:
            result["errors"].append(f"{rel}: read {e}")
            continue
        if len(data) > 2_000_000:
            result["skipped"] += 1
            continue
        ok, msg = github_put_file(token, owner, repo, low, data, commit_message, branch=branch)
        if ok:
            result["uploaded"] += 1
            result["files"].append(low)
            count += 1
        else:
            result["errors"].append(f"{rel}: {msg}")
        if count >= max_files:
            result["errors"].append(f"stopped at max_files={max_files}")
            break
        time.sleep(0.35)  # gentle rate-limit

    result["ok"] = result["uploaded"] > 0 and not any("invalid zip" in e for e in result["errors"])
    return result


def railway_gql(token: str, query: str, variables: Optional[dict] = None) -> Tuple[bool, dict]:
    code, data = _http_json(
        "POST",
        RW_GQL,
        _rw_headers(token),
        {"query": query, "variables": variables or {}},
    )
    if code != 200:
        return False, {"error": data.get("message") or str(data), "code": code}
    if data.get("errors"):
        return False, {"error": data["errors"][0].get("message") if data["errors"] else str(data["errors"])}
    return True, data.get("data") or {}


def railway_service_info(token: str, service_id: str) -> Tuple[bool, dict]:
    q = """
    query($id: String!) {
      service(id: $id) {
        id
        name
        deployments(first: 3) {
          edges {
            node {
              id
              status
              createdAt
              meta
            }
          }
        }
      }
    }
    """
    ok, data = railway_gql(token, q, {"id": service_id})
    if not ok:
        return False, data
    svc = data.get("service") or {}
    edges = ((svc.get("deployments") or {}).get("edges")) or []
    deploys = []
    for e in edges:
        n = e.get("node") or {}
        deploys.append({
            "id": n.get("id"),
            "status": n.get("status"),
            "createdAt": n.get("createdAt"),
            "meta": n.get("meta"),
        })
    return True, {"id": svc.get("id"), "name": svc.get("name"), "deployments": deploys}


def railway_redeploy(token: str, service_id: str, environment_id: str = "") -> Tuple[bool, dict]:
    """Try serviceInstanceRedeploy; fall back to deploymentTrigger if env set."""
    # Prefer redeploy latest instance
    if environment_id:
        q = """
        mutation($serviceId: String!, $environmentId: String!) {
          serviceInstanceRedeploy(serviceId: $serviceId, environmentId: $environmentId)
        }
        """
        ok, data = railway_gql(token, q, {"serviceId": service_id, "environmentId": environment_id})
        if ok:
            return True, {"result": data, "method": "serviceInstanceRedeploy"}
        # keep error, try alternate
        err1 = data
    else:
        err1 = {"error": "no environment_id"}

    # Alternate: deploymentRestart on latest deployment
    ok_info, info = railway_service_info(token, service_id)
    if ok_info and info.get("deployments"):
        dep_id = info["deployments"][0].get("id")
        if dep_id:
            q2 = """
            mutation($id: String!) {
              deploymentRestart(id: $id)
            }
            """
            ok2, data2 = railway_gql(token, q2, {"id": dep_id})
            if ok2:
                return True, {"result": data2, "method": "deploymentRestart", "deploymentId": dep_id}
            return False, {"error": data2.get("error") or data2, "prev": err1}
    return False, err1 if isinstance(err1, dict) else {"error": str(err1)}
