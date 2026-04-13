# abilities/sentinel/sentinel_ai_task_runner.py
import json, os, sys
from pathlib import Path
import datetime as _dt
import subprocess

XI_ROOT = Path(__file__).resolve().parents[2]  # /home/.../XI
AI_TASKS_DIR = XI_ROOT / "memory/sentinel/ai_tasks"
OVERLAYS_DIR = XI_ROOT / "memory/sentinel/overlays"

def _latest_task_dir() -> Path | None:
    if not AI_TASKS_DIR.exists():
        return None
    dirs = [p for p in AI_TASKS_DIR.iterdir() if p.is_dir() and p.name.startswith("sentinel_ai_task_")]
    return max(dirs, default=None, key=lambda p: p.stat().st_mtime)

def _load_payload(task_dir: Path) -> dict:
    payload = task_dir / "ai_payload.json"
    if not payload.exists():
        raise FileNotFoundError(f"Missing {payload}")
    return json.loads(payload.read_text(encoding="utf-8"))

def _emit_prompt(task_dir: Path, payload: dict) -> str:
    # Minimal GAEL-ish instruction for sentinel agent
    prompt = [
        "# AI-Assisted Sentinel Review",
        "",
        "You are the Sentinel agent. Review these conflicts and summary issues.",
        "For each conflict, decide one of:",
        "- keep (no action)",
        "- overlay_fix (subject, predicate, object, and reason required)",
        "- escalate (explain why).",
        "",
        "Output JSON list named ai_decisions.json with objects like:",
        '{"action":"overlay_fix","subject":"entry.uuid","predicate":"verified","object":true,"reason":"Validation matched"}',
        "",
        "Be precise. Prefer overlays. Avoid destructive changes.",
        "",
        "Payload follows:",
        json.dumps(payload, indent=2),
        "",
        "## How to Output",
        "Import the helper and call it with this task dir:",
        "```python",
        "from abilities.sentinel.ai_decision_writer import write_decisions",
        f"write_decisions({str(task_dir)!r}, DECISIONS_LIST)",
        "```",
        "Where DECISIONS_LIST is your list of JSON decisions.",
        "This will save them directly to:",
        f"{str(task_dir)}/ai_decisions.json"
    ]
    text = "\n".join(prompt) + "\n"
    (task_dir / "ai_task_compiled.md").write_text(text, encoding="utf-8")
    return text

def _invoke_agent(task_dir: Path) -> Path:
    """
    Use the flows harness to ask the 'sentinel' agent to produce ai_decisions.json in the task dir.
    This uses a single-shot flow: start → run → end.
    """
    md = (task_dir / "ai_task_compiled.md").read_text(encoding="utf-8")
    code = (
        "from flows.flow_orchestrator import start_flow, run_agent_with_runtime, end_flow, AGENT_PROFILES\n"
        "start_flow('sentinel_ai_audit', temp=0.2, model=str(AGENT_PROFILES['sentinel']['model_path']))\n"
        f"run_agent_with_runtime('sentinel', {md!r})\n"
        "end_flow()\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(XI_ROOT)
    # Let the agent write a decisions file; we’ll also accept text and parse if needed later.
    subprocess.run([sys.executable, "-c", code], cwd=str(XI_ROOT), env=env, check=False)
    # Expect the agent to write ai_decisions.json (or we could extract from latest journal if needed)
    decisions = task_dir / "ai_decisions.json"
    return decisions

def _apply_decisions(task_dir: Path, decisions_path: Path) -> int:
    if not decisions_path.exists():
        print(f"⚠️ No ai_decisions.json found at {decisions_path}")
        return 0
    try:
        decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"⚠️ Could not parse ai_decisions.json: {e}")
        return 0

    OVERLAYS_DIR.mkdir(parents=True, exist_ok=True)
    applied = 0
    for d in decisions if isinstance(decisions, list) else []:
        if (d.get("action") or "").lower() != "overlay_fix":
            continue
        subj = (d.get("subject") or "").strip()
        pred = (d.get("predicate") or "").strip()
        obj  = d.get("object", None)
        reason = (d.get("reason") or "").strip()
        if not subj or not pred:
            continue
        key = f"{subj}|{pred}|{'' if obj is None else obj}"
        hid = __import__("hashlib").sha1(key.encode("utf-8")).hexdigest()[:10]
        payload = {
            "subject": subj,
            "predicate": pred,
            "object": obj,
            "status": "applied",
            "source": f"{task_dir.name}",
            "timestamp": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z"),
            "details": {"resolution": "overlay", "reason": reason},
        }
        (OVERLAYS_DIR / f"overlay_{hid}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        applied += 1
    return applied

def _reindex_overlays():
    # Index all overlays; cheap enough for the small overlay set
    subprocess.run([sys.executable, "-m", "abilities.common_abilities.memory_indexer", "--agent", "sentinel",
                    "--all-overlays"], cwd=str(XI_ROOT), check=False)

def main():
    latest = _latest_task_dir()
    if not latest:
        print("ℹ️ No AI task directories found.")
        return 0
    payload = _load_payload(latest)
    _emit_prompt(latest, payload)
    print(f"🤖 Invoking sentinel agent for: {latest.name}")
    decisions = _invoke_agent(latest)
    applied = _apply_decisions(latest, decisions)
    print(f"🧷 AI overlay fixes applied: {applied}")
    if applied:
        _reindex_overlays()
    print("✅ AI-assisted review complete.")
    return 0

if __name__ == "__main__":
    sys.exit(main())

