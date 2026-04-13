# abilities/common_abilities/db_write.py
import psycopg2

UPSERT_SQL = """
INSERT INTO memories (id, agent, layer, thread, ts, text)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE SET
  agent=EXCLUDED.agent, layer=EXCLUDED.layer, thread=EXCLUDED.thread,
  ts=EXCLUDED.ts, text=EXCLUDED.text;
"""

def upsert_memory(conn, mem_id, agent, layer, thread, ts, text):
    with conn.cursor() as cur:
        cur.execute(UPSERT_SQL, (mem_id, agent, layer, thread, ts, text))

# abilities/common_abilities/db_write.py (add)
def upsert_link(conn, parent_uuid, child_uuid, relation="summarizes", weight=1.0):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO links (parent_uuid, child_uuid, relation, weight)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT DO NOTHING;
        """, (parent_uuid, child_uuid, relation, weight))


