#!/usr/bin/env python3
"""
🎛️ flow_orchestrator.py
Core XI runtime. Manages live flow sessions, tracks active agents, stores thread data,
activates GGUF-backed agent runtimes, and triggers journal writing on flow completion.
"""

from abilities.common_abilities.run_daily_summary_promotions import run_summary_promotions
import json
from pathlib import Path
import datetime as _dt
import time
import subprocess
import psutil
import sys
import os
# Optional .env for fresh shells (safe if absent)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ---- Core runtime defaults (non-DB) ----
os.environ.setdefault("XI_EMBEDDER_MODEL_PATH", "/home/ghost/XI/embedders/bge-large-v1.5")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

# ---- DB env coalescer: pick any provided values, sync to XI_DB_*, POSTGRES_*, and PG* ----
def _pick_env(*names: str, default: str | None = None) -> str | None:
    for n in names:
        v = os.environ.get(n)
        if v is not None and v != "":
            return v
    return default

def _configure_db_env():
    # Prefer XI_DB_*, then POSTGRES_*, then PG*; fall back to sane defaults (no default password!)
    host = _pick_env("XI_DB_HOST","POSTGRES_HOST","PGHOST", default="localhost")
    port = _pick_env("XI_DB_PORT","POSTGRES_PORT","PGPORT", default="5432")
    name = _pick_env("XI_DB_NAME","POSTGRES_DB","PGDATABASE", default="xi_memory")
    user = _pick_env("XI_DB_USER","POSTGRES_USER","PGUSER", default="xi_user")
    pwd  = _pick_env("XI_DB_PASSWORD","POSTGRES_PASSWORD","PGPASSWORD", default=None)

    # Synchronize across families
    os.environ["XI_DB_HOST"] = host
    os.environ["XI_DB_PORT"] = port
    os.environ["XI_DB_NAME"] = name
    os.environ["XI_DB_USER"] = user
    if pwd is not None:
        os.environ["XI_DB_PASSWORD"] = pwd

    os.environ["POSTGRES_HOST"] = host
    os.environ["POSTGRES_PORT"] = port
    os.environ["POSTGRES_DB"]   = name
    os.environ["POSTGRES_USER"] = user
    if pwd is not None:
        os.environ["POSTGRES_PASSWORD"] = pwd

    os.environ["PGHOST"]     = host
    os.environ["PGPORT"]     = port
    os.environ["PGDATABASE"] = name
    os.environ["PGUSER"]     = user
    if pwd is not None:
        os.environ["PGPASSWORD"] = pwd
    else:
        # Ensure we don't leak a bogus default password to libpq
        if "PGPASSWORD" in os.environ:
            del os.environ["PGPASSWORD"]

    # Friendly summary without leaking the password
    print(f"🗄️ DB config → user={user} db={name} host={host} port={port} pw={'yes' if pwd else 'no'}")

_configure_db_env()

sys.path.append(str(Path(__file__).resolve().parent.parent))
import psycopg2
from abilities.common_abilities.inline_claim_guard import guard_claims_before_commit
from abilities.common_abilities.claims_insert import insert_claims
from abilities.common_abilities.situational_awareness_prompts import (
    build_gael_conflict_message,
    gael_claim_guard_header
)
from abilities.common_abilities.memory_search import search_memory_entries
from abilities.common_abilities.post_flow_hooks import index_on_write, sentinel_quick
from abilities.common_abilities.vector_controller import handle_vector_command
from abilities.common_abilities.memory_selection_utils import (
    should_vector_search,
    present_memory_choices,
    extract_selected_memory,
    agent_decides_memory_selection,
    is_valid_memory_chunk,
    parse_and_extract_text,
    extract_text_from_structured_memory
)
from abilities.common_abilities.search_controller import (
    search_controller,
    BudgetPolicy,
)
import re
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # puts XI/ on sys.path
from abilities.common_abilities.recency_search import recency_scoped_search
from pathlib import Path
import psycopg2

#from sandbox_runner.runner import Sandbox
def _env(*names, default=None):
    import os
    for n in names:
        v = os.getenv(n)
        if v not in (None, ""):
            return v
    return default
def _db_conn():
    import psycopg2
    return psycopg2.connect(
        dbname=_env("XI_DB_NAME","POSTGRES_DB","PGDATABASE", default="xi_memory"),
        user=_env("XI_DB_USER","POSTGRES_USER","PGUSER", default="xi_user"),
        # Pass None if no password set; lets peer/trust auth work cleanly
        password=_env("XI_DB_PASSWORD","POSTGRES_PASSWORD","PGPASSWORD", default=None),
        host=_env("XI_DB_HOST","POSTGRES_HOST","PGHOST", default="localhost"),
        port=_env("XI_DB_PORT","POSTGRES_PORT","PGPORT", default="5432"),
    )
#from abilities.common_abilities.sandbox_promotion_writer import (
#    PromotionInput, promote_and_record
#)
#from abilities.common_abilities.situational_awareness_prompts import sandbox_policy_block
from abilities.common_abilities.situational_awareness_prompts import gael_retrieval_selection

# Avoid circular import with flows.__init__ at import time.
# If you need registry ops, import/register lazily from the caller.

# Diver (optional; falls back if missing)
try:
    from diver.diver_controller import run_diver_search
except Exception:
    run_diver_search = None


TAU_LOCAL = 0.78   # accept normal vector
TAU_RECENT = 0.72  # accept recency fallback
# session cache disabled
# CACHE_PROMOTE_RE = re.compile(r'\[\[cache:\+\s*"(.*?)"\s*\]\]')

_SANDBOX_ROOT = Path("sandbox")
_REPO_ROOT = Path(".")  # repo root for writing journal files

# 📁 Paths
ROOT = Path(__file__).resolve().parent.parent  # /XI
THREADS_FOLDER = ROOT / "memory" / "threads"
FLOWS_FOLDER = ROOT / "flows"
TEMP_CONTEXT_FILE = FLOWS_FOLDER / "current_flow_context.json"
SEARCH_POLICY = BudgetPolicy(
    early_msgs=3,
    early_total_tokens=900,          # first 3 msgs total search-injection budget
    per_search_cap_after_early=500,  # per-search cap after early stage
    turn_total_cap=4000,             # per-turn search-injection cap
    default_snippet_target_tokens=180,
    hard_snippet_cap_tokens=240,
)

def _gael_recall_block(snippet: str, relevance: float | None, source: str | None) -> str:
    """Agent-facing GAEL block for manual recalls."""
    try:
        rel = f"{float(relevance):.2f}" if relevance is not None else "n/a"
    except Exception:
        rel = "n/a"
    src_line = f"Source: {source}\n" if source else ""
    return (
        "[Memory Recall]\n"
        f"Relevance (system-computed): {rel}\n"
        f"{src_line}"
        "Content:\n"
        f"{snippet}"
    )

def _labeled_recall_block(*, date: str | None, session: str | None, relevance: float | None, content: str) -> str:
    """Human-friendly recall fence with beginning/end prompt markers (agent-facing)."""
    try:
        rel = f"{float(relevance):.2f}" if relevance is not None else "n/a"
    except Exception:
        rel = "n/a"
    d = date or ""
    s = session or ""
    meta = []
    if d: meta.append(f"Date: {d}")
    if s: meta.append(f"Session: {s}")
    meta.append(f"Relevance: {rel}")
    meta_hdr = "\n".join(meta)
    body = (content or "").strip()
    return (
        "[beginning prompt: memory recall]\n"
        f"{meta_hdr}\n"
        "---\n"
        f"{body}\n"
        "[end prompt: memory recall]"
    )

def _truncate_by_tokens(text: str, max_tokens: int = 325) -> str:
    """
    Very light, model-agnostic token-ish truncation.
    Splits on whitespace; good enough to bound length without extra deps.
    """
    if not isinstance(text, str) or not text:
        return ""
    parts = re.findall(r"\S+|\n", text)  # keep newlines as separate chunks
    if len(parts) <= max_tokens:
        return text
    clipped = "".join(parts[:max_tokens])
    # ensure we don't cut mid-word visually (best-effort)
    if not clipped.endswith((" ", "\n")):
        clipped += "…"
    return clipped

def _expand_inline_recalls_in_text(text: str, agent: str) -> str:
    """
    Replace any line that is exactly `/recall <topic>` with a labeled recall block.
    This is a post-processing step: it does not feed the recalled content back into the model
    mid-turn. It only alters the displayed/output text.
    Enable/disable via XI_RECALL_INLINE (default: "1" = on).
    """
    try:
        enabled = os.environ.get("XI_RECALL_INLINE", "1") == "1"
    except Exception:
        enabled = True
    if not enabled or not isinstance(text, str) or not text.strip():
        return text

    out_lines = []
    for line in text.splitlines():
        m = re.match(r"^\s*/recall\s+(.+?)\s*$", line)
        if not m:
            out_lines.append(line)
            continue

        topic = m.group(1).strip()
        blocks = []
        try:
            # Keep it light: top 2 matches scoped to agent
            results = search_memory_entries(query=topic, top_n=2, filters={"agent": agent})
        except Exception as e:
            results = []
            print(f"⚠️ inline /recall failed for '{topic}': {e}")

        for r in results or []:
            # distance→relevance ~ [0,1]
            rel = None
            try:
                d = float(r.get("distance"))
                rel = max(0.0, min(1.0, 1.0 - d))
            except Exception:
                pass
            ts_raw = r.get("timestamp")
            if ts_raw:
                if hasattr(ts_raw, "strftime"):
                    ts = ts_raw.strftime("%Y-%m-%d")
                else:
                    ts = str(ts_raw)[:10]
            else:
                ts = ""
            src_file = r.get("source_file") or ""
            try:
                fname = Path(src_file).name
            except Exception:
                fname = src_file or ""
            session_label = f"{r.get('agent','agent')}_session" if r.get("agent") else (fname or "")
            snippet = (r.get("content") or "").strip()
            if snippet:
                # cap verbatim snippet at ~325 token-ish units
                snippet = _truncate_by_tokens(snippet, max_tokens=325)
                blocks.append(_labeled_recall_block(
                    date=(ts or None),
                    session=(session_label or None),
                    relevance=rel,
                    content=snippet
                ))

        out_lines.append("\n\n".join(blocks) if blocks else
                         "[beginning prompt: memory recall]\n(No relevant memory found)\n[end prompt: memory recall]")

    return "\n".join(out_lines)


def _davinci_server_call(prompt: str, context_path: str, temp: float, max_tokens: int = 512, timeout: float = 120.0) -> str:
    """
    Talk to the persistent davinci_runtime server via JSON-lines TCP.
    XI_DAVINCI_SERVER format: "host:port" (e.g., "127.0.0.1:11435")
    """
    import socket, json, os, sys
    target = os.getenv("XI_DAVINCI_SERVER")
    if not target:
        raise RuntimeError("XI_DAVINCI_SERVER not set")
    host, port_s = target.split(":")
    port = int(port_s)
    req = {
        "input": prompt,
        "context_path": context_path,
        "temp": float(temp),
        "max_tokens": int(os.getenv("XI_DAVINCI_MAX_TOKENS", "512")),
        # no 'stop' here — runtime handles safe defaults; role-labels removed
        # NOTE: we'll add 'stream' below based on XI_LIVE_STREAM
    }
    # Toggle true live token streaming via env
    LIVE = os.environ.get("XI_LIVE_STREAM", "0") == "1"
    if LIVE:
        req["stream"] = True

    # Optional: console printer for deltas
    def _console_stream_writer(s: str):
        sys.stdout.write(s)
        sys.stdout.flush()

    with socket.create_connection((host, port), timeout=5.0) as sock:
        sock.settimeout(timeout)
        f = sock.makefile(mode="rwb", buffering=0)
        f.write((json.dumps(req) + "\n").encode("utf-8"))
        # Legacy single-shot behavior
        if not LIVE:
            line = f.readline()
            if not line:
                return ""
            resp = json.loads(line.decode("utf-8", "ignore"))
            if "error" in resp:
                raise RuntimeError(resp["error"])
            return resp.get("text", "") or ""

        # Streaming mode: read JSONL deltas as they arrive
        assembled: list[str] = []
        while True:
            line = f.readline()
            if not line:
                break
            try:
                msg = json.loads(line.decode("utf-8", "ignore"))
            except Exception:
                continue
            # server may send a small started event
            if "delta" in msg:
                delta = msg["delta"]
                _console_stream_writer(delta)
                assembled.append(delta)
            elif msg.get("done"):
                return (msg.get("text") or "".join(assembled)).strip()
            elif "error" in msg:
                raise RuntimeError(msg["error"])
        return "".join(assembled).strip()

# --- Public helper for GUIs/clients to stop the current generation -----------
def stop_generation(host_port: str | None = None) -> bool:
    """
    Sends a STOP control to the davinci_runtime sidecar (listening on port+1).
    Example: if XI_DAVINCI_SERVER=127.0.0.1:11435, control is 127.0.0.1:11436.
    Returns True on best-effort success, False otherwise.
    """
    import os, socket
    try:
        target = host_port or os.environ.get("XI_DAVINCI_SERVER", "127.0.0.1:11435")
        host, port_s = target.split(":")
        ctrl = (host, int(port_s) + 1)
        with socket.create_connection(ctrl, timeout=1.0) as s:
            s.sendall(b"STOP\n")
            try:
                s.recv(16)  # optional 'OK\n'
            except Exception:
                pass
        return True
    except Exception as e:
        print(f"⚠️ stop_generation failed: {e}")
        return False

try:
    from runtime.routines_orchestrator import launch_routines_if_needed
except Exception:
    try:
        from abilities.common_abilities.routines_orchestrator import launch_routines_if_needed
    except Exception:
        def launch_routines_if_needed():
            print("ℹ️ Routines orchestrator not present; skipping background routines.")

# --- Optional summary promotions (guarded import) --- keep until fix routines orchestrator
try:
    from abilities.common_abilities.run_daily_summary_promotions import run_summary_promotions
except Exception:
    def run_summary_promotions(*args, **kwargs):
        print("ℹ️ Summary promotions module not present; skipping.")

def pull_memory_with_budget(token: str, flow_ctx: dict, agents: list[str]) -> dict:
    """
    Selects a memory snippet under budget. Prefers Diver (base → optional cascade),
    falls back to legacy search_controller if Diver is unavailable or returns nothing.
    Returns a dict with keys: selected, provenance, confidence, budget_used, gael_block, escalation_path
    """
    # Context for filtering
    agent_name  = (flow_ctx or {}).get("agent") or (flow_ctx or {}).get("current_agent")
    thread_type = (flow_ctx or {}).get("thread_type") or (flow_ctx or {}).get("thread")

    # ---- Try Diver first (optional, disabled by default) ----
    import os
    DIVER_ALLOWED = (os.getenv("XI_DISABLE_DIVER", "1") != "1")
    if not DIVER_ALLOWED:
        print("[diver] disabled (XI_DISABLE_DIVER=1 or unset)", file=sys.stderr)
    elif run_diver_search:
        print("[diver] enabled (XI_DISABLE_DIVER=0)", file=sys.stderr)
        try:
            query = (token or "").strip()

            # 1) Fast FTS (base)
            hits = run_diver_search(
                query=query,
                mode="base",
                topk=8,
                layer=None,
                agent=agent_name,
                thread=thread_type,
                after=None,
                before=None,
                use_vec=True,
                by_id=False,
            )

            # 2) Escalate to cascade if thin or user hints at linkage/why/how
            trigger_terms = ("why", "how", "trace", "links")
            needs_deep = (len(hits) < 2) or any(t in query.lower() for t in trigger_terms)
            if needs_deep:
                deep = run_diver_search(
                    query=query,
                    mode="cascade",
                    topk=max(12, 8),
                    layer=None,
                    agent=agent_name,
                    thread=thread_type,
                    after=None,
                    before=None,
                    use_vec=True,
                    by_id=False,
                    depth=2,
                    beam=80,
                )
                # merge unique ids (keep base order first)
                seen = {h["id"] for h in hits}
                hits += [h for h in deep if h["id"] not in seen]

            if hits:
                top = hits[0]  # dict with id, excerpt, score, display, etc.
                # Prefer Diver's own clean agent-facing block (no UUIDs)
                gael_block = (top.get("display") or "").strip()
                if not gael_block:
                    # Fallback: minimal clean block, still no provenance
                    snippet = (top.get("excerpt") or "").strip()
                    if len(snippet) > 1200:
                        snippet = snippet[:1197] + "…"
                    rel = None
                    try:
                        rel = float(top.get("score")) if top.get("score") is not None else None
                    except Exception:
                        rel = None
                    rel_txt = f"{rel:.2f}" if rel is not None else "n/a"
                    gael_block = (
                        "[Memory Recall]\n"
                        f"Relevance (system-computed): {rel_txt}\n"
                        "Content:\n"
                        f"{snippet}"
                    )

                return {
                    "selected": (top.get("excerpt") or ""),
                    "provenance": "diver (hidden)",  # keep UUIDs out of agent view
                    "confidence": float(top.get("score") or 0.0),
                    "budget_used": 0,  # Diver recall doesn't spend LLM tokens here
                    "gael_block": gael_block,
                    "escalation_path": ["diver_cascade" if needs_deep else "diver_base"],
                }
        except Exception as e:
            print(f"⚠️ Diver search failed, falling back to search_controller: {e}")

    # ---- Fallback: existing search_controller path ----
    # (keeps your previous behavior intact if Diver is missing or returns nothing)
    msg_idx = (flow_ctx or {}).get("msg_idx", 0)
    turn_tokens_used = (flow_ctx or {}).get("turn_tokens_used", 0)
    try:
        res = search_controller(
            token,
            agents=agents or ["davinci", "hermes"],
            msg_idx=msg_idx,
            turn_tokens_used=turn_tokens_used,
            policy=SEARCH_POLICY,
        )
        return res
    except Exception as e:
        # last-resort empty selection
        print(f"⚠️ search_controller failed: {e}")
        return {
            "selected": "",
            "provenance": "none",
            "confidence": 0.0,
            "budget_used": 0,
            "gael_block": "[Memory Selected]\nengine: none\nsnippet: (none)",
            "escalation_path": ["none"],
        }

# Session cache — disabled (keep stubs for compatibility)
def session_cache_txt_path(agent: str) -> Path:
    return ROOT / "memory" / agent / "session_cache.txt"
def append_session_cache_txt(agent: str, tag: str, text: str, ts: str = None):
    return None

# 🧠 Global flow context
LAST_VECTOR_HITS = {}
FLOW_CONTEXT = {}
THREAD_ENTRIES = []




# 🧬 Agent runtime & model profiles
AGENT_PROFILES = {
    "davinci": {
        "model_path": ROOT / "companions" / "davinci" / "davinci.gguf",
        "runtime": ROOT / "runtime" / "davinci_runtime.py",
        "memory_path": ROOT / "memory" / "davinci",
        "context_limit": 128000
    },
    "hermes": {
        "model_path": ROOT / "companions" / "hermes" / "hermes.gguf",
        "runtime": ROOT / "runtime" / "hermes_runtime.py",
        "memory_path": ROOT / "memory" / "hermes",
        "context_limit": 8192
    }
}

# 🧠 Vector Control State
VECTOR_SEARCH_STATE = {
    "agents": {agent: 3 for agent in AGENT_PROFILES},
    "infinite": {agent: False for agent in AGENT_PROFILES}
}

# 🧼 Vector Injection Cleaner
def strip_recursive_memory_blocks(text):
    if isinstance(text, str) and re.search(r'{\\?"entries\\?"\s*:', text):
        return "[Vector memory redacted due to recursive structure]"
    return text

# 🕓 Utility
def get_timestamp():
    # UTC-aware ISO with Z
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")

# 🧠 Compile memory context for each agent in profile
def compile_agent_contexts():
    prepper_script = ROOT / "runtime" / "context_prepper.py"
    if not prepper_script.exists():
        print(f"⚠️ context_prepper.py missing at {prepper_script}")
        return
    print("🔄 Running context_prepper.py to refresh memory contexts...")
    subprocess.run(["python3", str(prepper_script)])

# 🚦 Start a flow
def start_flow(flow_type: str, temp: float, model: str):
    global FLOW_CONTEXT, THREAD_ENTRIES
    import os, time
    os.makedirs("logs", exist_ok=True)
    # ✅ log using the known args (don’t touch FLOW_CONTEXT yet)
    with open("logs/sandbox_retention.log","a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | start | flow={flow_type} | agents=[]\n")

    # now assign globals (mutate in-place so existing references stay valid)
    FLOW_CONTEXT.clear()
    FLOW_CONTEXT.update({
        "flow_type": flow_type,
        "start_time": get_timestamp(),
        "temp": temp,
        "model": model,
        # enable sudo: sudo router authorizes only this user name
        "user": os.environ.get("XI_USER", "Brandon"),
        "agents_active": [],
        "thread_refs": [],
        "gael_injections": {},
        "user": (os.getenv("XI_USER","") or "").strip(),
    })
    # Davinci-only emphasis (purely cosmetic; agents are added dynamically when they talk)
    # FLOW_CONTEXT["agents_active"] = ["davinci"]
    THREAD_ENTRIES = []
    compile_agent_contexts()  # 🔁 Load fresh memory contexts
    print(f"✅ Started flow: {flow_type} | temp={temp} | model={model}")
    try:
        who = (FLOW_CONTEXT.get("user") or "").strip() or "(none)"
        print(f"🪪 sudo user: {who}")
    except Exception:
        pass
    save_temp_context()

    # Reset vector use counts
    for agent in AGENT_PROFILES:
        VECTOR_SEARCH_STATE["agents"][agent] = 3
        VECTOR_SEARCH_STATE["infinite"][agent] = False
    # session cache reset removed

# 🧠 Check if agent can use vector search
def vector_allowed(agent: str) -> bool:
    if VECTOR_SEARCH_STATE["infinite"].get(agent, False):
        return True
    uses_left = VECTOR_SEARCH_STATE["agents"].get(agent, 0)
    if uses_left > 0:
        VECTOR_SEARCH_STATE["agents"][agent] -= 1
        return True
    return False

# 🤖 Run an agent via its runtime and GGUF
def run_agent_with_runtime(agent_name: str, user_input: str, model_path: str = None) -> str:
    import os

    from abilities.common_abilities.situational_awareness_prompts import vector_search_intro
    from abilities.common_abilities.sudo_command_handler import handle_sudo_command

    # 🧠 Manual Recall Command: `/recall [context]`
    # Intercept BEFORE sudo/auto-recall so it's deliberate and clean.
    query = None
    if isinstance(user_input, str):
        raw = user_input.strip()
        # Strict mode (default): only trigger if the whole message is a single /recall line
        STRICT = os.environ.get("XI_RECALL_STRICT", "1") == "1"
        if STRICT:
            m = re.match(r"^/recall\s+(.+)$", raw)
            if m:
                query = m.group(1).strip()
        else:
            if raw.startswith("/recall"):
                query = raw[len("/recall"):].strip()

    if query is not None:
        if not query:
            recall_text = "[beginning prompt: memory recall]\n(No query provided)\n[end prompt: memory recall]"
        else:
            # Pull up to 2 vector matches for this agent only (simple & predictable).
            try:
                matches = search_memory_entries(query=query, top_n=2, filters={"agent": agent_name})
            except Exception as e:
                matches = []
                print(f"⚠️ /recall vector search failed: {e}")

            blocks: list[str] = []
            for m in matches or []:
                # distance→relevance: smaller distance = better (clip to [0,1])
                rel = None
                try:
                    d = float(m.get("distance"))
                    rel = max(0.0, min(1.0, 1.0 - d))
                except Exception:
                    pass
                # Friendly source (no UUIDs): use agent_session + date (YYYY-MM-DD) as "Session" and "Date"
                ts_raw = m.get("timestamp")
                if ts_raw:
                    # Handle datetime objects safely
                    if hasattr(ts_raw, "strftime"):
                        ts = ts_raw.strftime("%Y-%m-%d")
                    else:
                        ts = str(ts_raw)[:10]
                else:
                    ts = ""
                src_file = m.get("source_file") or ""
                try:
                    fname = Path(src_file).name
                except Exception:
                    fname = src_file or ""
                session_label = f"{m.get('agent','agent')}_session" if (m.get('agent')) else (fname or "")
                snippet = (m.get("content") or "").strip()
                if snippet:
                    # cap verbatim snippet at ~325 token-ish units
                    snippet = _truncate_by_tokens(snippet, max_tokens=325)
                    blocks.append(_labeled_recall_block(
                        date=(ts or None),
                        session=session_label or None,
                        relevance=rel,
                        content=snippet
                    ))

            recall_text = "\n\n".join(blocks) if blocks else (
                "[beginning prompt: memory recall]\n(No relevant memory found)\n[end prompt: memory recall]"
            )

        # Log to thread and return early (no model invocation)
        run_agent(agent_name, user_input, recall_text)
        return recall_text

    # 👮 Sudo command intercept (returns early if handled)
    maybe = handle_sudo_command(agent_name, user_input)
    if maybe is not None:
        # also log into the thread to keep transcripts coherent
        run_agent(agent_name, user_input, str(maybe))
        return str(maybe)

    # 🔁 Prepend any GAEL injection destined for this agent
    inj = FLOW_CONTEXT.get("gael_injections", {}).pop(agent_name, None)
    if inj:
        user_input = f"{inj}\n\n{user_input}"

    enriched_input = user_input  # ✅ define early to avoid unbound error
    profile = AGENT_PROFILES.get(agent_name)

    # 🚫 Skip vector search for GAEL prompts (must be after the prepend above)
    is_gael = isinstance(user_input, str) and user_input.strip().startswith("[GAEL")

    # 🔕 Auto recall disabled by default. Set XI_AUTO_RECALL=1 to re-enable.
    AUTO_RECALL_ALLOWED = os.environ.get("XI_AUTO_RECALL", "0") == "1"
    memory_already_injected = False
    if not is_gael and AUTO_RECALL_ALLOWED:
        agents_for_search = [agent_name] if agent_name else (FLOW_CONTEXT.get("agents_active") or ["davinci","hermes"])
        mem = pull_memory_with_budget(user_input, FLOW_CONTEXT, agents_for_search)
        def _gael_block(x):
            if x is None: return ""
            if isinstance(x, dict): return x.get("gael_block", "")
            if hasattr(x, "get"):
                try: return x.get("gael_block", "")
                except Exception: pass
            if hasattr(x, "gael_block"):
                try: return getattr(x, "gael_block") or ""
                except Exception: pass
            return ""
        gb = _gael_block(mem)
        if gb:
            user_input = f"{gb}\n\n{user_input}"
            enriched_input = user_input
            memory_already_injected = True

    # 🧠 Vector Search Logic (only if auto recall is enabled AND nothing injected yet)
    if AUTO_RECALL_ALLOWED and (not is_gael) and (not memory_already_injected) and should_vector_search(agent_name, user_input) and vector_allowed(agent_name):
        print("\n🧠 GAEL Vector Awareness Hint:")
        print(vector_search_intro())

        hits = search_agent_memory(agent_name, user_input, top_k=5)
        if hits:
            # GAEL selection scaffold (log decisions; pick at most 1–2)
            try:
                print(gael_retrieval_selection(min(5, len(hits))))
            except Exception:
                pass
            print("\n🔍 Top memory snippets found:")
            print(present_memory_choices(agent_name, hits))
            try:
                import os, sys
                non_interactive = os.environ.get("XI_NON_INTERACTIVE") == "1"
                if non_interactive or not sys.stdin.isatty():
                    selected = 0
                else:
                    selected = int(input("🔢 Select memory item (1–5, or 0 for agent to decide): ").strip())

                if selected == 0:
                    selected = agent_decides_memory_selection(agent_name, hits)

                raw_chunk = extract_selected_memory(selected, hits)
                # try to pull an ID for flash logging (dict hits only)
                selected_id = None
                try:
                    if isinstance(hits, list) and 1 <= selected <= len(hits) and isinstance(hits[selected-1], dict):
                        selected_id = hits[selected-1].get("id")
                except Exception:
                    selected_id = None
                selected_content = parse_and_extract_text(raw_chunk)

                # 🧼 Sanitize selected memory to remove recursive structures
                selected_content = strip_recursive_memory_blocks(selected_content)

                if is_valid_memory_chunk(selected_content):
                    # Wrap as labeled recall block and inject ONCE.
                    wrapped = _labeled_recall_block(
                        date=get_timestamp()[:10],
                        session=f"{agent_name}_session",
                        relevance=None,
                        content=selected_content
                    )
                    enriched_input = f"{wrapped}\n\n{user_input}"
                    memory_already_injected = True
                else:
                    print("⚠️ Skipping invalid or malformed memory chunk.")
            except Exception as e:
                print(f"⚠️ Memory selection failed: {e}")

        # 🧯 Recency fallback (only if auto recall is enabled AND STILL nothing was injected)
        try:
            if AUTO_RECALL_ALLOWED and (not memory_already_injected) and (not is_gael):
                agents_for_recent = [agent_name] if agent_name else (FLOW_CONTEXT.get("agents_active") or ["davinci","hermes"])
                recent = recency_scoped_search(user_input, agents=agents_for_recent, hours=6, max_files_per_agent=6, limit=5)
                if recent and recent.get("results"):
                    top = recent["results"][0]
                    selected_content = (top.get("snippet") or "").strip()
                    if selected_content:
                        wrapped = _labeled_recall_block(
                            date=(top.get("date") or "")[:10] if top.get("date") else get_timestamp()[:10],
                            session=top.get("file") or None,
                            relevance=float(top.get("score",0.0)),
                            content=selected_content
                        )
                        enriched_input = f"{wrapped}\n\n{user_input}"
                        memory_already_injected = True
                        print("🧩 Recency-Scoped Search injected one cohesive snippet.")
        except Exception as e:
            print(f"⚠️ Recency fallback failed: {e}")


    # 🚦 Safety checks for agent runtime
    if not profile:
        print(f"⚠️ No profile found for agent '{agent_name}'")
        output = f"(No response — profile missing for {agent_name})"
    else:
        runtime_path = profile["runtime"]
        memory_path = profile["memory_path"]
        context_file = memory_path / "compiled_context.txt"
        model_path = Path(model_path) if model_path else profile["model_path"]

        if not runtime_path.exists():
            print(f"⚠️ Runtime script missing for {agent_name} at {runtime_path}")
            output = f"(No response — missing runtime script)"

        elif not model_path or not model_path.exists():
            print(f"⚠️ GGUF model missing for {agent_name} at {model_path}")
            output = f"(No response — missing model)"

        else:
            try:
                temp = FLOW_CONTEXT.get("temp", 0.8)
                # 🔥 Prefer persistent server if configured
                server = os.environ.get("XI_DAVINCI_SERVER") if agent_name == "davinci" else None
                agent_timeout = float(os.environ.get("XI_AGENT_TIMEOUT", "180"))
                if server:
                    try:
                        thought = _davinci_server_call(
                            prompt=enriched_input,
                            context_path=str(context_file),
                            temp=float(temp),
                            max_tokens=int(os.environ.get("XI_DAVINCI_MAX_TOKENS", "512")),
                            timeout=agent_timeout,
                        )
                    except (ConnectionResetError, TimeoutError, OSError) as e:
                        print(f"⚠️ Davinci server issue ({e}). Retrying once...")
                        try:
                            thought = _davinci_server_call(
                                prompt=enriched_input,
                                context_path=str(context_file),
                                temp=float(temp),
                                max_tokens=int(os.environ.get("XI_DAVINCI_MAX_TOKENS", "512")),
                                timeout=agent_timeout,
                            )
                        except Exception as e2:
                            print(f"⚠️ Davinci server second attempt failed ({e2}). Falling back to subprocess.")
                            result = subprocess.run(
                                [
                                    "python3", str(runtime_path),
                                    "--input", enriched_input,
                                    "--model", str(model_path),
                                    "--context", str(context_file),
                                    "--temp", str(temp)
                                ],
                                capture_output=True, text=True, timeout=int(agent_timeout)
                            )
                            thought = result.stdout.strip()
                else:
                    timeout_s = int(agent_timeout)
                    result = subprocess.run(
                        [
                            "python3", str(runtime_path),
                            "--input", enriched_input,
                            "--model", str(model_path),
                            "--context", str(context_file),
                            "--temp", str(temp)
                        ],
                        capture_output=True, text=True, timeout=timeout_s
                    )
                    thought = result.stdout.strip()

                # Take the raw model output, then expand any inline /recall lines (display-time only)
                output = _expand_inline_recalls_in_text(thought, agent_name)

            except Exception as e:
                print(f"❌ Error running {agent_name}: {e}")
                output = f"(Error during execution: {e})"

    # session cache promotion disabled; output already expanded for inline recalls

    # 🧼 Final response display (flash cache removed)
    run_agent(agent_name, user_input, output)

    # Suppress final echo when we already streamed live tokens to the console.
    SUPPRESS = os.environ.get("XI_SUPPRESS_FINAL_ECHO_WHEN_STREAMING", "1") == "1"
    LIVE = os.environ.get("XI_LIVE_STREAM", "0") == "1"
    return "" if (LIVE and SUPPRESS) else output

# 🧾 Log output
def run_agent(agent_name: str, user_input: str, agent_output: str):
    timestamp = get_timestamp()

    # ✅ Only track known agents (avoid adding "user" by accident)
    if agent_name in AGENT_PROFILES and agent_name not in FLOW_CONTEXT["agents_active"]:
        FLOW_CONTEXT["agents_active"].append(agent_name)

    THREAD_ENTRIES.append({
        "timestamp": timestamp,
        "agent": agent_name,
        "input": user_input,
        "output": agent_output
    })

    save_temp_context()

# 🔍 Search vector memory for agent
def search_agent_memory(agent: str, query: str, top_k: int = 3) -> list:
    global LAST_VECTOR_HITS
    try:
        results = search_memory_entries(query=query, top_n=top_k, filters={"agent": agent})
        LAST_VECTOR_HITS[agent] = results
        return results
    except Exception as e:
        print(f"⚠️ Memory search failed for {agent}: {e}")
        LAST_VECTOR_HITS[agent] = []
        return []

# 💾 Save flow thread to disk
def save_flow_thread():
    flow_type = FLOW_CONTEXT["flow_type"]
    date_str = _dt.datetime.now().strftime("%Y%m%d")
    year = _dt.datetime.now().strftime("%Y")
    thread_file = THREADS_FOLDER / flow_type / "session" / year / f"{flow_type}_session_{date_str}.json"
    thread_file.parent.mkdir(parents=True, exist_ok=True)

    with open(thread_file, "w") as f:
        json.dump({"flow_context": FLOW_CONTEXT, "thread": THREAD_ENTRIES}, f, indent=2)

    FLOW_CONTEXT["thread_refs"].append(str(thread_file.relative_to(ROOT)))
    print(f"💾 Saved thread to: {thread_file}")
    return thread_file

# 🧠 GAEL Reflection Prompts
# flash cache removed

def _clip(s: str, max_chars=700, max_lines=None) -> str:
    if not isinstance(s, str): return ""
    s = s.strip()
    if max_chars and len(s) > max_chars: s = s[:max_chars]
    if max_lines: s = "\n".join(s.splitlines()[:max_lines])
    return s.strip()

def _strip_leading_thought_prefix(s: str) -> str:
    s = (s or "").strip()
    return s[len("[Thought]:"):].lstrip() if s.startswith("[Thought]:") else s

# 🧱 GAEL fence + sanitize helpers
import re

PROMPTS_FENCED = [
    ("Summary",
     "[GAEL] Return ONLY the summary fenced as:\n"
     "```SUMMARY\n<80-word max summary>\n```\n"
     "No signatures, no quotations."),
    ("Thoughts",
     "[GAEL] Return ONLY the thoughts fenced as:\n"
     "```THOUGHTS\n- <bullet 1>\n- <bullet 2>\n- <bullet 3>\n```\n"),
    ("Decisions",
     "[GAEL] Return ONLY the decisions fenced as:\n"
     "```DECISIONS\n- Action — Owner — When\n- ...\n```\n"
     "No background restatements."),
]

def _extract_fence(kind: str, text: str) -> str:
    if not text:
        return ""
    start = f"```{kind.upper()}"
    idx = text.find(start)
    if idx == -1:
        idx = text.lower().find(f"```{kind.lower()}")
        if idx == -1:
            return text.strip()
    body = text[idx + len(start):]
    end = body.find("```")
    if end != -1:
        return body[:end].strip()
    return body.strip()

def _sanitize_summary(s: str) -> str:
    s = s.strip().strip('"').strip("“”\"'")
    return re.sub(r"\s*[–—-]\s*[A-Za-z][\w\- ]{1,40}$", "", s).strip()

def _ensure_bullets(s: str, max_bullets=3) -> str:
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    if any(ln.startswith(("-", "•", "*")) for ln in lines):
        norm = []
        for ln in lines[:max_bullets]:
            ln = re.sub(r'^[•*]\s*', '- ', ln)
            if not ln.startswith('- '):
                ln = f"- {ln}"
            norm.append(ln)
        return "\n".join(norm)
    parts = re.split(r"(?<=[.!?])\s+", " ".join(lines))
    parts = [p.strip().rstrip(':;') for p in parts if p.strip()]
    return "\n".join(f"- {p}" for p in parts[:max_bullets])

def _emit_gael_in_thread(agent: str):
    # allow longer sections; we’ll normalize at index time, not in-agent
    clips = {
        "Summary":   {"max_chars": 1200, "max_lines": 10},  # ~150–200 words
        "Thoughts":  {"max_chars": 1800, "max_lines": 12},  # short paragraphs ok
        "Decisions": {"max_chars": 1800, "max_lines": 12},
    }

    for label, ptxt in PROMPTS_FENCED:
        # log the GAEL prompt used for this section (no flash cache)
        run_agent(agent, f"[{label} Prompt]", ptxt)

        # 1) draft (fenced)
        draft = run_agent_with_runtime(agent, ptxt)
        payload = _extract_fence(label.upper(), draft)

        # 2) self-edit (no new info, enforce brevity/cleanliness)
        edit_prompt = (
            "[GAEL] Self-edit the following. Trim repetition, obey constraints, keep meaning. "
            "Do NOT add new information. Return only the cleaned text.\n\n" + payload
        )
        edited = run_agent_with_runtime(agent, edit_prompt)

        # 3) Sanitize + clip
        cleaned = _strip_leading_thought_prefix(edited)
        if label == "Summary":
            cleaned = _sanitize_summary(cleaned)

        cleaned = _clip(cleaned, **clips[label])
        # 4) Emit parser-friendly tag for writers (no “User:”/“Agent:” role prefixes)
        run_agent(agent, f"{label} Result", f"[{label}]: {cleaned}")

def end_flow(
    journal_only: bool = False,
    write_journals: bool = False,
    emit_gael: bool = False,
    minimal: bool = True,
):
    from abilities.common_abilities.situational_awareness_prompts import flow_completion_message

    FLOW_CONTEXT["end_time"] = get_timestamp()
    save_temp_context()

    # Allow env overrides for convenience
    import os
    if os.getenv("XI_END_FLOW_EMIT_GAEL") == "1":
        emit_gael = True
    if os.getenv("XI_MINIMAL_JOURNAL") == "0":
        minimal = False

    # Optionally emit GAEL sections into the thread (default: off)
    if emit_gael:
        for agent in FLOW_CONTEXT.get("agents_active", []):
            if agent in AGENT_PROFILES:
                _emit_gael_in_thread(agent)

    thread_path = save_flow_thread()

    # ⛔ Do not write journals unless explicitly allowed
    if not write_journals:
        print("📝 Journaling disabled for this end_flow; skipping writers.")
        if not journal_only and not minimal:
            print("\n🧘 Final Reflection:")
            print(flow_completion_message())
        return

    # call per-agent writers
    for agent in FLOW_CONTEXT.get("agents_active", []):
        if agent not in AGENT_PROFILES:
            continue
        script_path = ROOT / "abilities" / agent / f"{agent}_write_session_journal.py"
        if not script_path.exists():
            print(f"⚠️ No journal writer found for {agent} at {script_path}")
            continue

        print(f"\n🧠 GAEL Hint for {agent}:")
        # flash cache disabled
        print(f"✍️ Calling journal writer for {agent}...")

        try:
            before_ts = time.time()
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT)
            # journaling should never stream
            env["XI_LIVE_STREAM"] = "0"
            res = subprocess.run(
                [sys.executable, "-m", f"abilities.{agent}.{agent}_write_session_journal",
                 "--flow_context", json.dumps(FLOW_CONTEXT)],
                cwd=str(ROOT),
                capture_output=True, text=True, env=env
            )

            if res.returncode == 0:
                print(f"✅ {agent} journal writer exited OK")
                if res.stdout and res.stdout.strip():
                    print(res.stdout.strip())

                # 1) Parse JOURNAL_PATH from writer stdout (fallback to newest if missing)
                m = re.search(r"^JOURNAL_PATH=(.+)$", res.stdout or "", flags=re.MULTILINE)
                journal_path = m.group(1).strip() if m else None
                if not journal_path:
                    new_path = newest_session_journal_for(agent, since_ts=before_ts)
                    journal_path = str(new_path) if new_path else None

                # Minimal mode: stop here after file is written/located (no claims/index/sentinel)
                if minimal:
                    continue

                # 2) If last turn already flagged a conflict, skip inserts this turn
                if FLOW_CONTEXT.get("skip_claim_insert"):
                    FLOW_CONTEXT["skip_claim_insert"] = False
                    print("⏭️ Skipping claim insertion this turn (awaiting agent correction).")
                else:
                    # 3) Run inline guard — insert then index
                    if journal_path:
                        pending_claims = extract_pending_claims_from_journal(journal_path, agent)
                        if pending_claims:
                            with _db_conn() as conn:
                                guard = guard_claims_before_commit(conn, pending_claims)
                                if guard["conflicts"]:
                                    FLOW_CONTEXT["skip_claim_insert"] = True
                                    FLOW_CONTEXT.setdefault("gael_injections", {})[agent] = build_gael_conflict_message(guard["conflicts"])
                                    print("⚠️ Conflict detected — prompting agent to correct before commit. Skipping claim insert this turn.")
                                    journal_path = None
                                else:
                                    insert_claims(conn, guard["ok"])
                        else:
                            print("ℹ️ No structured claims found in journal; skipping claim insert.")
                    else:
                        print(f"⚠️ Could not locate new session journal for {agent} after writer ran.")

                # 4) If we *did* produce a journal and didn’t hit a conflict:
                #    Index it → run inline Sentinel amendments for this file → re-index
                if journal_path:
                    env = os.environ.copy()
                    env["PYTHONPATH"] = str(ROOT)

                    # First index via DB indexer (module-safe)
                    try:
                        r1 = subprocess.run(
                            [sys.executable, "-m", "abilities.common_abilities.memory_indexer",
                             "--file", journal_path, "--agent", agent],
                            cwd=str(ROOT), env=env, check=False
                        )
                        print(f"🔍 Indexed (pre-amend): {Path(journal_path).name} rc={r1.returncode}")
                    except Exception as e:
                        print(f"⚠️ Pre-amend index failed for {agent}: {e}")

                    # Inline Sentinel amendment scoped to this file (fast path supports --file --apply)
                    try:
                        r2 = subprocess.run(
                            [sys.executable, "-m", "abilities.sentinel.sentinel_routine_audit",
                             "--file", journal_path, "--apply"],
                            cwd=str(ROOT), env=env, check=False
                        )
                        print(f"🛡️ Sentinel inline amend rc={r2.returncode} (file={Path(journal_path).name})")
                    except Exception as e:
                        print(f"⚠️ Inline Sentinel amend failed for {agent}: {e}")

                    # Re-index to capture overlays/flags immediately
                    try:
                        r3 = subprocess.run(
                            [sys.executable, "-m", "abilities.common_abilities.memory_indexer",
                             "--file", journal_path, "--agent", agent],
                            cwd=str(ROOT), env=env, check=False
                        )
                        print(f"🔁 Re-indexed (post-amend): {Path(journal_path).name} rc={r3.returncode}")
                    except Exception as e:
                        print(f"⚠️ Post-amend re-index failed for {agent}: {e}")

                    # Opportunistic light routines (indexer once, ensure daemon, sweep amendments)
                    try:
                        launch_routines_if_needed()
                    except Exception:
                        pass

            else:
                print(f"❌ {agent} journal writer failed (code {res.returncode})")
                if res.stderr and res.stderr.strip():
                    print(res.stderr.strip())
                elif res.stdout and res.stdout.strip():
                    print(res.stdout.strip())

        except Exception as e:
            print(f"❌ {agent} journal writer crashed: {e}")


    # only print final reflection for full end (not journal-only)
    if not journal_only and not minimal:
        print("\n🧘 Final Reflection:")
        print(flow_completion_message())

def newest_session_journal_for(agent: str, since_ts: float) -> Path | None:
    base = ROOT / "memory" / agent
    candidates = []
    if base.exists():
        for d in base.iterdir():
            if d.is_dir() and d.name.endswith("_journal"):
                sess = d / "session"
                if sess.exists():
                    for p in sess.glob(f"{agent}_session_journal_*.json"):
                        try:
                            if p.stat().st_mtime >= since_ts:
                                candidates.append(p)
                        except Exception:
                            pass
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


# 💽 Save flow context temp file
def save_temp_context():
    FLOWS_FOLDER.mkdir(parents=True, exist_ok=True)
    with open(TEMP_CONTEXT_FILE, "w") as f:
        json.dump(FLOW_CONTEXT, f, indent=2)

# ✅ Keep this helper standalone (no code after the return!)
from pathlib import Path
import json

def extract_pending_claims_from_journal(journal_path: str, agent: str) -> list[dict]:
    """
    Looks for top-level 'claims': [{"subject","predicate","value"}]
    Returns [] if not present.
    """
    try:
        data = json.loads(Path(journal_path).read_text(encoding="utf-8"))
    except Exception:
        return []
    claims = data.get("claims") or []
    out = []
    for c in claims:
        subj, pred, val = c.get("subject"), c.get("predicate"), c.get("value")
        if subj and pred and val:
            out.append({
                "subject": subj,
                "predicate": pred,
                "value": val,
                "origin_file": str(journal_path),
                "agent": agent,
            })
    return out

ALLOW_CHECKPOINT_JOURNALS = False  # can be flipped via FLOW_CONTEXT or env
def checkpoint_journal_for(agent: str):
    if not (FLOW_CONTEXT.get("allow_checkpoint_journals")
            or ALLOW_CHECKPOINT_JOURNALS
            or os.getenv("XI_ALLOW_CHECKPOINT_JOURNALS") == "1"):
        print("⏭️ Checkpoint journaling disabled; skipping.")
        return
    """Write a session journal mid-flow for one agent, run the inline guard, then (if clean) index + quick sentinel."""
    # Call the same per-agent writer you use in end_flow
    script_path = ROOT / "abilities" / agent / f"{agent}_write_session_journal.py"
    if not script_path.exists():
        print(f"⚠️ No journal writer found for {agent} at {script_path}")
        return

    print(f"\n✍️ (checkpoint) Calling journal writer for {agent}...")
    before_ts = time.time()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    res = subprocess.run(
        [sys.executable, "-m", f"abilities.{agent}.{agent}_write_session_journal",
         "--flow_context", json.dumps(FLOW_CONTEXT)],
        cwd=str(ROOT),
        capture_output=True, text=True, env=env
    )

    if res.stdout and res.stdout.strip():
        print(res.stdout.strip())

    if res.returncode != 0:
        print(f"❌ {agent} journal writer failed (code {res.returncode})")
        if res.stderr and res.stderr.strip():
            print(res.stderr.strip())
        return

    # Parse JOURNAL_PATH from writer stdout; fallback to newest-on-disk if absent
    m = re.search(r"^JOURNAL_PATH=(.+)$", res.stdout or "", flags=re.MULTILINE)
    journal_path = m.group(1).strip() if m else None
    if not journal_path:
        p = newest_session_journal_for(agent, since_ts=before_ts)
        journal_path = str(p) if p else None

    # If prior turn already flagged a conflict, skip inserts this turn
    if FLOW_CONTEXT.get("skip_claim_insert"):
        FLOW_CONTEXT["skip_claim_insert"] = False
        print("⏭️ Skipping claim insertion this turn (awaiting agent correction).")
        return

    if not journal_path:
        print(f"⚠️ Could not locate new session journal for {agent} after writer ran.")
        return

    # If the writer didn't include claims, patch them in now (smoke-test helper, unconditional if missing)
    try:
        raw = json.loads(Path(journal_path).read_text(encoding="utf-8"))
        existing = raw.get("claims") or []
        override = FLOW_CONTEXT.get("claims_override") or []
        # Fallback default for the smoke test if override isn't present
        if not override:
            override = [{"subject": "order:smoke-test", "predicate": "status", "value": "cancelled"}]

        # Only inject if claims are absent
        if not existing:
            normalized = []
            for c in override:
                subj, pred, val = c.get("subject"), c.get("predicate"), c.get("value")
                if subj and pred and val:
                    normalized.append({"subject": subj, "predicate": pred, "value": val})
            if normalized:
                raw["claims"] = normalized
                Path(journal_path).write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"🧩 Patched {len(normalized)} claim(s) into journal for guard. (override_seen={bool(FLOW_CONTEXT.get('claims_override'))})")
    except Exception as e:
        print(f"⚠️ Could not patch claims into journal: {e}")



    # Inline guard → GAEL injection or insert
    pending_claims = extract_pending_claims_from_journal(journal_path, agent)
    if pending_claims:
        with _db_conn() as conn:
            guard = guard_claims_before_commit(conn, pending_claims)
            if guard["conflicts"]:
                FLOW_CONTEXT["skip_claim_insert"] = True
                FLOW_CONTEXT.setdefault("gael_injections", {})[agent] = build_gael_conflict_message(guard["conflicts"])
                print("⚠️ Conflict detected — prompting agent to correct before commit. Skipping claim insert this turn.")
                return
            else:
                insert_claims(conn, guard["ok"])
    else:
        print("ℹ️ No structured claims found in journal; skipping claim insert.")

    # Clean → index + quick sentinel
    try:
        index_on_write(Path(journal_path))
        print(f"🔍 Indexed: {Path(journal_path).name}")
    except Exception as e:
        print(f"⚠️ Index-on-write failed for {agent}: {e}")
    try:
        sentinel_quick(agent, window_hours=2)
    except Exception as e:
        print(f"⚠️ Sentinel quick audit failed for {agent}: {e}")



# 📁 Folder containing flow scripts
FLOWS_DIR = Path("flows")

# 🧭 Check if today's summary run already occurred
def has_summary_run_today() -> bool:
    today = _dt.datetime.now().strftime("%Y-%m-%d")
    return (Path(".flags") / f"summary_run_{today}").exists()

# 🧪 Manual test
if __name__ == "__main__":
    def main():
########from abilities.common_abilities.routines_orchestrator import launch_routines_if_needed
        
        # Step 1: Start background routines if needed
        launch_routines_if_needed()

        # Step 1.1: Sweep sandbox (delete sessions >21 days old with no promotions)
        sweep_sandbox(days=21)

        # ✅ Step 2: Promote daily summaries (once per day)
        if not has_summary_run_today():
            print("🧠 Running daily summary promotions from flow orchestrator...")
            run_summary_promotions()

            flag_dir = Path(".flags")
            flag_dir.mkdir(exist_ok=True)
            today = _dt.datetime.now().strftime("%Y-%m-%d")
            (flag_dir / f"summary_run_{today}").touch()

        # Step 3: Print flow startup notice
        print("🚀 Starting XI Flow Orchestrator...")

        # Step 4: (Optional test flow execution)
        # start_flow("idea_generator", temp=0.6, model="companions/davinci/davinci.gguf")
        # run_agent_with_runtime("davinci", "Hey do you remember what I said about encrypted memories last time?")
        # run_agent_with_runtime("hermes", "I love Allen!")
        # end_flow()

    main()


def reflect_response(agent: str, prompt: str = "[GAEL] Briefly reflect on this session."):
    """Compatibility shim for sudo handler; invokes the agent to reflect and logs to the thread."""
    try:
        return run_agent_with_runtime(agent, prompt)
    except Exception as e:
        print(f"⚠️ reflect_response failed for {agent}: {e}")
        return ""

