# Lightweight JSONL logger for search controller runs.
from __future__ import annotations
import json, time
from pathlib import Path
from typing import Any, Dict

LOG_DIR = Path("logs"); LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "search_runs.jsonl"

def write_run(payload: Dict[str, Any]) -> None:
    payload = dict(payload or {})
    payload.setdefault("ts", int(time.time()))
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass  # never break search on logging
