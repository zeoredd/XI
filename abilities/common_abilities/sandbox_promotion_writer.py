# abilities/common_abilities/sandbox_promotion_writer.py
from __future__ import annotations
import os, json, uuid
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, Optional
from abilities.common_abilities.time_utils import get_current_quarter_and_period


# --- helpers -----------------------------------------------------------------

def _now_utc():
    # UTC with tzinfo
    return dt.datetime.now(dt.timezone.utc)

def _date_parts(now: dt.datetime | None = None):
    """
    Return (date_str, year, quarter, period) using your 4-4-5 helper.
    """
    now = now or _now_utc()
    d = now.date()
    year = d.year
    quarter, period = get_current_quarter_and_period(d)  # <- use your canonical helper
    date_str = d.isoformat()
    return date_str, year, quarter, period

def _ensure_dir(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)

def _minisign_placeholder():
    # wire in your real minisign call here
    return "minisign:UNSIGNED_PLACEHOLDER"

def _gen_uuid() -> str:
    return str(uuid.uuid4())

def _choose_model_meta(default_model: str | None = None, default_temp: float | None = None, default_ctx: int | None = None):
    # If you keep these in runtime, pass them in explicitly; otherwise use placeholders
    return default_model or "unknown-model", default_temp if default_temp is not None else 0.0, default_ctx or 0

@dataclass
class PromotionInput:
    agent: str
    session_id: str                    # e.g. "20250821_153201_ab12cd"
    promoted_paths: Sequence[str]      # relative to sandbox/{agent}/pinned/{kind}/ or absolute repo paths
    note: str = ""
    kind: str = "projects"             # pinned/{kind}
    source_thread: str = "sandbox"
    layer: str = "session"
    # optional model/context info for journal fields
    model: Optional[str] = None
    temp: Optional[float] = None
    context_length: Optional[int] = None
    # linkbacks (uuids/claim_ids/etc.) if you want traceability to upstream journals
    thread_refs: Optional[Sequence[str]] = None
    tags: Optional[Sequence[str]] = None

# --- 1) Write journal file (filesystem) --------------------------------------

def write_session_journal_file(
    repo_root: Path,
    promo: PromotionInput,
) -> Path:
    """
    Writes a full journal JSON under:
      /memory/{agent}/session/session/{agent}_session_journal_YYYYMMDD_N.json
    Returns the file path.
    """
    date_str, year, quarter, period = _date_parts()
    model, temp, ctx = _choose_model_meta(promo.model, promo.temp, promo.context_length)

    # Choose a sequential suffix N by counting existing files for today
    journal_dir = repo_root / "memory" / promo.agent / "session" / "session"
    journal_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(p for p in journal_dir.glob(f"{promo.agent}_session_journal_{date_str.replace('-','')}*.json"))
    suffix = len(existing) + 1

    journal_uuid = _gen_uuid()
    claim_id = f"sandbox:{promo.agent}:{promo.session_id}"
    signature = _minisign_placeholder()

    content = {
        "date": date_str,
        "year": year,
        "quarter": quarter,
        "period": period,
        "agent": promo.agent,
        "thread_type": promo.source_thread,
        "thread_refs": list(promo.thread_refs or []),
        "summary": f"Promoted artifacts from sandbox session {promo.session_id}: " +
                   (", ".join(Path(p).name for p in promo.promoted_paths) if promo.promoted_paths else "(none)"),
        "thoughts": promo.note or "",
        "decisions": "Pinned artifacts for reuse; future work continues from this baseline.",
        "uuid": journal_uuid,
        "temp": temp,
        "model": model,
        "context_length": ctx,
        "claim_id": claim_id,
        "signature": signature,

        "_meta": {
            "tags": list(promo.tags or ["sandbox", "promotion"]),
            "source_files": list(promo.promoted_paths),
            "corrupted": False,
            "sandbox": True,
            "session_id": promo.session_id,
            "kind": promo.kind
        }
    }

    out_path = journal_dir / f"{promo.agent}_session_journal_{date_str.replace('-','')}_{suffix}.json"
    _ensure_dir(out_path)
    out_path.write_text(json.dumps(content, indent=2))
    return out_path

# --- 2) Insert compact promotion note into memory_entries (and optional memories) ---

def record_sandbox_promotion_db(
    conn,
    promo: PromotionInput,
    *,
    mirror_to_memories: bool = False,  # set True if you still use the old FTS table
    embedder_fingerprint: str | None = None,
    content_prefix: str = "[SANDBOX PROMOTION]",
):
    """
    Inserts one compact row in memory_entries (canonical).
    - Puts sandbox flags inside validator_flags JSONB (safe with your spec).
    - Does not require schema changes.
    Optionally mirrors a lightweight row into 'memories' for older FTS paths.
    """
    dt = _now_utc()
    date_str, year, quarter, period = _date_parts(dt)
    model, temp, ctx = _choose_model_meta(promo.model, promo.temp, promo.context_length)
    mem_uuid = _gen_uuid()
    claim_id = f"sandbox:{promo.agent}:{promo.session_id}"

    # Compact, human-friendly text for quick grepping; full detail stays in journal file
    text_lines = [
        f"{content_prefix}",
        f"agent: {promo.agent}",
        f"session: {promo.session_id}",
        f"kind: {promo.kind}",
        f"note: {promo.note or '(none)'}",
        "artifacts:" if promo.promoted_paths else "artifacts: (none)"
    ]
    text_lines += [f"  - {p}" for p in promo.promoted_paths]
    compact_text = "\n".join(text_lines)

    validator_flags = {
        "sandbox": True,
        "ephemeral": False,            # promotions persist; non-promoted runs remain file-only in sandbox/
        "session_id": promo.session_id,
        "kind": promo.kind,
        "tags": list(promo.tags or ["sandbox", "promotion"])
    }

    # Insert into memory_entries
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO memory_entries
                (uuid, agent, "timestamp", content, source_file, thread_type, period,
                 content_fingerprint, embedder_fingerprint, validator_flags, claim_id, status)
            VALUES
                (%s,   %s,    %s,          %s,      %s,          %s,         %s,
                 %s,                 %s,                   %s::jsonb,     %s,      %s)
            RETURNING id
            """,
            (
                mem_uuid,
                promo.agent,
                dt,
                compact_text,
                # Point to the journal file path (or leave None). If you call write_session_journal_file first, pass its relative path here.
                None,
                promo.source_thread,
                str(period),
                # lightweight fingerprint placeholders; your indexer can overwrite on embed
                f"cfp:{mem_uuid[:8]}",
                embedder_fingerprint or "bge-large-v1.5",
                json.dumps(validator_flags),
                claim_id,
                "current",
            ),
        )
        row_id = cur.fetchone()[0]

        if mirror_to_memories:
            # Old flat FTS table mirror (optional)
            # Schema: memories(id text PK, layer text, agent text, thread text, ts bigint, text text, text_fts tsvector generated)
            # We use id=uuid for convenience; ts in milliseconds since epoch to match bigint
            ts_ms = int(dt.timestamp() * 1000)
            cur.execute(
                """
                INSERT INTO memories (id, layer, agent, thread, ts, text)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (mem_uuid, promo.layer, promo.agent, promo.source_thread, ts_ms, compact_text),
            )

    conn.commit()
    return {"entry_id": row_id, "uuid": mem_uuid, "claim_id": claim_id, "timestamp": dt.isoformat()}

# --- convenience: all-in-one flow hook ---------------------------------------

def promote_and_record(
    conn,
    repo_root: Path,
    promo: PromotionInput,
    *,
    write_file: bool = True,
    mirror_to_memories: bool = False,
    embedder_fingerprint: str | None = None
):
    """
    Typical flow call from sandbox_promote(...):
      1) write the full journal file (spec-compliant)
      2) insert compact row in memory_entries (and optional memories mirror)
    """
    journal_path = None
    if write_file:
        journal_path = write_session_journal_file(repo_root, promo)

    res = record_sandbox_promotion_db(
        conn,
        promo,
        mirror_to_memories=mirror_to_memories,
        embedder_fingerprint=embedder_fingerprint,
    )

    return {
        "journal_file": str(journal_path) if journal_path else None,
        **res,
    }

