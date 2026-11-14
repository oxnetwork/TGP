#!/usr/bin/env python3
"""Download Pantegnos CLI (NPVS/NPVT Gen-2 decryptor) into /data/pantegnos."""
from __future__ import annotations
import json
import os
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

API = "https://api.github.com/repos/FrontierTM/Pantegnos/releases/latest"
DEFAULT_OUT = os.environ.get("PANTEGNOS_BIN") or "/data/pantegnos"


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT)
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"Fetching Pantegnos release info...")
    req = urllib.request.Request(API, headers={"User-Agent": "phoenix-bot"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode())
    asset = None
    for a in data.get("assets") or []:
        name = (a.get("name") or "").lower()
        if "linux-amd64" in name and name.endswith(".tar.gz"):
            asset = a
            break
    if not asset:
        print("No linux-amd64 asset found", file=sys.stderr)
        return 1
    url = asset["browser_download_url"]
    print(f"Downloading: {url}")
    with tempfile.TemporaryDirectory() as td:
        zpath = Path(td) / "p.tgz"
        urllib.request.urlretrieve(url, zpath)
        with tarfile.open(zpath, "r:gz") as tar:
            tar.extractall(td)
        binary = None
        for p in Path(td).rglob("*"):
            if p.is_file() and "pantegnos" in p.name.lower() and not p.name.endswith((".txt", ".md")):
                binary = p
                break
        if not binary:
            print("Binary not found in archive", file=sys.stderr)
            return 1
        data_b = binary.read_bytes()
        out.write_bytes(data_b)
        out.chmod(0o755)
    print(f"OK -> {out} ({out.stat().st_size/1024/1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
