from __future__ import annotations
import os, json, re
from pathlib import Path
import datetime as _dt
from typing import Tuple, Dict, Any, List

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")

def _iso_ok(s: str) -> bool:
    if not isinstance(s, str) or not ISO_RE.match(s):
        return False
    try:
        _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return True
    except Exception:
        return False

def validate_and_fix_timestamp(doc: Dict[str, Any], path: str, *,
                               allow_fix: bool = True,
                               future_slack_min: int = 5,
                               mtime_slack_min: int = 30) -> Tuple[bool, List[str], Dict[str, Any]]:
    reasons: List[str] = []
    d = doc
    meta = d.get("_meta") or {}
    ts = meta.get("timestamp")
    date = d.get("date")
    now = _dt.datetime.now(_dt.timezone.utc)

    # 1) presence
    if not ts:
        reasons.append("timestamp:missing")

    # 2) format
    if ts and not _iso_ok(ts):
        reasons.append("timestamp:invalid_format")

    # 3) parse or synthesize
    ts_dt = None
    if ts and _iso_ok(ts):
        ts_dt = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    elif allow_fix:
        # synthesize from date or file mtime
        if isinstance(date, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            ts_dt = _dt.datetime.fromisoformat(date + "T00:00:00+00:00")
        else:
            try:
                mtime = _dt.datetime.fromtimestamp(os.path.getmtime(path), tz=_dt.timezone.utc)
                ts_dt = mtime
            except Exception:
                pass
        if ts_dt:
            meta.setdefault("timestamp", ts_dt.isoformat(timespec="seconds").replace("+00:00", "Z"))
            d["_meta"] = meta

    # 4) future sanity
    if ts_dt and (ts_dt - now) > _dt.timedelta(minutes=future_slack_min):
        reasons.append(f"timestamp:in_future(>{future_slack_min}m)")

    # 5) filename/year sanity (best-effort)
    token = Path(path).name
    m = re.search(r'_(\d{8}|\d{4}P\d{2}|\d{4}Q[1-4])\.json$', token)
    if m and ts_dt:
        year_in_file = m.group(1)[:4]
        if str(ts_dt.year) != year_in_file:
            reasons.append(f"filename_year:mismatch({year_in_file}!={ts_dt.year})")

    # 6) date alignment (if date present)
    if ts_dt and isinstance(date, str):
        if ts_dt.date().isoformat() != date:
            reasons.append("date:mismatch_with_timestamp")

    ok = len(reasons) == 0
    return ok, reasons, d

def flag_to_sentinel(kind: str, file_path: str, reasons: List[str]) -> None:
    rec = {
        "kind": kind,
        "file": file_path,
        "reasons": reasons,
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    }
    logdir = Path("logs"); logdir.mkdir(parents=True, exist_ok=True)
    with open(logdir / "sentinel_flags.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

