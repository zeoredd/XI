# abilities/sentinel/resolve_uuid_to_source.py
from pathlib import Path
import json
from typing import List
import glob

def resolve_uuids_to_files(agent: str, uuids: list[str]) -> list[Path]:
    """Return all journal files for agent that contain any of the uuids
    (either in 'uuid' or in 'compiled_uuids'). Scans all tiers."""
    base_path = Path("memory") / agent
    journal_types = [
        "idea_generator_journal",
        "daily_briefing_and_strategy_journal",
        "personal_and_group_conversation_journal",
        "sentinel_audit_journal" if agent == "sentinel" else None,
    ]
    journal_types = [jt for jt in journal_types if jt]
    tiers = ["session", "weekly", "period", "quarterly"]

    hits: list[Path] = []
    uuid_set = set(uuids or [])
    for jt in journal_types:
        for tier in tiers:
            # Look in the tier *and* its archive fallback
            tier_dir = base_path / jt / tier
            cand_dirs = [tier_dir, tier_dir / "archive"]
            for d in cand_dirs:
                if not d.exists():
                    continue
                for file in d.glob("*.json"):
                    try:
                        data = json.loads(file.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    if not isinstance(data, dict):
                        continue
                    file_uuid = data.get("uuid")
                    compiled = data.get("compiled_uuids", []) or []
                    if (file_uuid in uuid_set) or (uuid_set.intersection(compiled)):
                        hits.append(file)

    return sorted(set(hits))


