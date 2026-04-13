# ===============================
# file: scripts/bootstrap_memdb.py
# ===============================
"""
Creates a minimal SQLite database with memories, links, and FTS for smoke testing.
Run: python scripts/bootstrap_memdb.py
"""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "mem.db")

schema = [
    # memories table
    """
    CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY,
        layer TEXT,
        agent TEXT,
        thread TEXT,
        ts INTEGER,
        text TEXT
    );
    """,
    # standalone FTS (so we can store TEXT ids alongside text)
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5(
        text,
        id UNINDEXED
    );
    """,
    # links graph
    """
    CREATE TABLE IF NOT EXISTS links (
        src TEXT,
        dst TEXT,
        link_type TEXT,
        confidence REAL
    );
    """
]

sample_memories = [
    ("uuid-1", "session", "xi", "search", 1755700000, "We planned the search engine and sentinel."),
    ("uuid-2", "weekly", "xi", "search", 1755800000, "Cascade diver expands links through time."),
    ("uuid-3", "period", "allen", "infra", 1755900000, "Allen brainstormed about ruby rigs."),
]

sample_links = [
    ("uuid-1", "uuid-2", "propagates_to", 0.9),
    ("uuid-2", "uuid-3", "related", 0.7)
]

if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    for stmt in schema:
        cur.executescript(stmt)

    cur.executemany("INSERT OR REPLACE INTO memories VALUES (?,?,?,?,?,?)", sample_memories)
    cur.executemany("INSERT INTO links VALUES (?,?,?,?)", sample_links)

    # rebuild standalone FTS from memories
    cur.execute("DELETE FROM mem_fts")
    cur.executemany(
        "INSERT INTO mem_fts(text, id) VALUES (?, ?)",
        [(m[5], m[0]) for m in sample_memories]
    )

    conn.commit()
    conn.close()
    print(f"Database initialized at {DB_PATH}")

