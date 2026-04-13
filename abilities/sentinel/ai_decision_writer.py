# abilities/sentinel/ai_decision_writer.py
import json
from pathlib import Path

def write_decisions(task_dir: str, decisions: list[dict]) -> None:
    """Write AI decisions into the task directory as ai_decisions.json."""
    out = Path(task_dir) / "ai_decisions.json"
    out.write_text(json.dumps(decisions, indent=2), encoding="utf-8")
    print(f"📝 Wrote AI decisions to {out}")

