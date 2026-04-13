# abilities/common_abilities/db_touchpoints.py
import uuid, json, datetime
from typing import Optional, Sequence

def record_sandbox_promotion(conn, *,
    agent: str,
    session_id: str,
    note: str,
    promoted_paths: Sequence[str],
    source_thread: str = "sandbox",
    layer: str = "session",
    persistent: bool = True,
    links: list[tuple[str, str, str, float]] | None = None,  # (parent_uuid, child_uuid, relation, weight)
):
    """
    Writes a compact memory entry for a promotion and optional links.

    Returns: inserted memory UUID (str)
    """
    mem_id = str(uuid.uuid4())
    ts = datetime.datetime.utcnow()
    text = (
        f"[SANDBOX PROMOTION]\n"
        f"agent: {agent}\n"
        f"session: {session_id}\n"
        f"note: {note}\n"
        f"artifacts:\n  - " + "\n  - ".join(promoted_paths)
    )
    meta = {
        "sandbox": True,
        "session_id": session_id,
        "promoted_paths": list(promoted_paths)
    }
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO memory_entries (id, agent, layer, thread, ts, text, meta, ephemeral)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
        """, (mem_id, agent, layer, source_thread, ts, text, json.dumps(meta), not persistent))
        if links:
            for parent, child, relation, weight in links:
                cur.execute("""
                    INSERT INTO memory_links (parent_uuid, child_uuid, relation, weight, ts)
                    VALUES (%s, %s, %s, %s, %s)
                """, (parent, child, relation, weight, ts))
    conn.commit()
    return mem_id

