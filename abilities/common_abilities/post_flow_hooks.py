# abilities/common_abilities/post_flow_hooks.py
import subprocess, sys
from pathlib import Path

XI_ROOT = Path(__file__).resolve().parents[2]

def index_on_write(journal_path: Path):
    try:
        subprocess.run(
            [sys.executable, "abilities/common_abilities/memory_indexer.py", "--file", str(journal_path)],
            cwd=str(XI_ROOT),
            check=True
        )
    except Exception as e:
        print(f"⚠️ Index-on-write failed: {e}")

def sentinel_quick(agent: str, window_hours: int = 2):
    try:
        subprocess.run(
            [sys.executable, "abilities/sentinel/sentinel_routine_audit.py", "--quick", "--window-hours", str(window_hours), "--agents", agent],
            cwd=str(XI_ROOT),
            check=True
        )
    except Exception as e:
        print(f"⚠️ Sentinel quick audit failed: {e}")

