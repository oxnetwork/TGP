"""
Phoenix quality helpers:
- identifier / pattern blacklist
- per-source hourly NPVT cap (fixed + smart)
- file-hash cooldown for repeated NPVT/NPVS files
"""
from __future__ import annotations

import hashlib
import re
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

_npvt_hourly: Dict[str, List[float]] = defaultdict(list)
_file_hash_seen: Dict[str, float] = {}
_source_outcomes: Dict[str, List[Tuple[float, int, int, int]]] = defaultdict(list)


def normalize_pattern(p: str) -> str:
    return (p or "").strip().lower()


def parse_blacklist(raw: str) -> List[str]:
    if not raw:
        return []
    parts = re.split(r"[\n,;]+", raw)
    out = []
    for p in parts:
        p = normalize_pattern(p)
        if p and p not in out:
            out.append(p)
    return out


def link_hits_blacklist(link: str, patterns: List[str]) -> Optional[str]:
    if not link or not patterns:
        return None
    low = link.lower()
    name = host = userinfo = ""
    try:
        if "#" in link:
            name = link.split("#", 1)[1]
        if "://" in link:
            rest = link.split("://", 1)[1]
            main = rest.split("#")[0].split("?")[0]
            if "@" in main:
                userinfo, hostport = main.rsplit("@", 1)
            else:
                hostport = main
            host = hostport.split(":")[0]
    except Exception:
        pass
    hay = " ".join([low, name.lower(), host.lower(), userinfo.lower()])
    for p in patterns:
        if not p:
            continue
        if p in hay:
            return p
        compact_p = re.sub(r"[^a-z0-9]", "", p)
        compact_h = re.sub(r"[^a-z0-9]", "", hay)
        if compact_p and len(compact_p) >= 4 and compact_p in compact_h:
            return p
    return None


def source_npvt_allowed(source_id: str, max_per_hour: int) -> bool:
    if max_per_hour <= 0:
        return True
    key = str(source_id)
    now = time.time()
    _npvt_hourly[key] = [t for t in _npvt_hourly[key] if now - t < 3600]
    return len(_npvt_hourly[key]) < max_per_hour


def source_npvt_record(source_id: str) -> None:
    _npvt_hourly[str(source_id)].append(time.time())


def source_npvt_count(source_id: str) -> int:
    now = time.time()
    key = str(source_id)
    _npvt_hourly[key] = [t for t in _npvt_hourly[key] if now - t < 3600]
    return len(_npvt_hourly[key])


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data or b"").hexdigest()


def file_hash_allowed(data: bytes, cooldown_sec: int) -> Tuple[bool, str]:
    h = file_sha256(data)
    if cooldown_sec <= 0:
        return True, h
    now = time.time()
    dead = [k for k, ts in _file_hash_seen.items() if now - ts > max(cooldown_sec * 3, 7200)]
    for k in dead:
        _file_hash_seen.pop(k, None)
    last = _file_hash_seen.get(h)
    if last is not None and (now - last) < cooldown_sec:
        return False, h
    return True, h


def file_hash_record(data_or_hash) -> None:
    if isinstance(data_or_hash, bytes):
        h = file_sha256(data_or_hash)
    else:
        h = str(data_or_hash)
    _file_hash_seen[h] = time.time()


def record_source_outcome(source_id: str, sent: int, dup: int, offline: int) -> None:
    key = str(source_id)
    now = time.time()
    _source_outcomes[key].append((now, int(sent or 0), int(dup or 0), int(offline or 0)))
    _source_outcomes[key] = [x for x in _source_outcomes[key] if now - x[0] < 3600]


def smart_source_cap(source_id: str, base_cap: int) -> int:
    if base_cap <= 0:
        return base_cap
    key = str(source_id)
    now = time.time()
    rows = [x for x in _source_outcomes.get(key, []) if now - x[0] < 3600]
    if not rows:
        return base_cap
    sent = sum(r[1] for r in rows)
    dup = sum(r[2] for r in rows)
    offline = sum(r[3] for r in rows)
    total = sent + dup + offline
    if total < 5:
        return base_cap
    dup_ratio = dup / total
    sent_ratio = sent / total
    cap = base_cap
    if sent == 0 and dup_ratio >= 0.75:
        cap = max(2, base_cap // 3)
    elif sent_ratio < 0.05 and dup_ratio >= 0.6:
        cap = max(3, base_cap // 2)
    elif sent_ratio >= 0.25 and sent >= 3:
        cap = min(50, base_cap + 8)
    elif sent_ratio >= 0.12:
        cap = min(40, base_cap + 3)
    return int(cap)
