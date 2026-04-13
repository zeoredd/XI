#!/usr/bin/env python3
"""
🛡️ sudo_command_handler.py
Central router for all `sudo` commands during live XI flows.
Modularizes logic from flow_orchestrator.py.

This version:
- Adds flash/session cache controls (txt-based).
- Keeps journals sourced from GAEL-tagged thread (no cache ingestion).
- Avoids circular imports by inlining small helpers and importing heavy functions lazily.
"""

from abilities.common_abilities.result_codes import (
    END,
    REFLECT,
    SESSION_JOURNAL,
    START_PREFIX,
    NOOP,
)

import json
import re, os
from datetime import datetime, timezone
from pathlib import Path

from abilities.common_abilities.situational_awareness_prompts import (
    get_gael_summary_prompt,
    get_gael_thoughts_prompt,
    get_gael_decisions_prompt,
)

# ---- Runtime context (populated by flow_orchestrator at startup) ----
FLOW_CONTEXT = {}
VECTOR_SEARCH_STATE = {}
AGENT_PROFILES = {}
LAST_VECTOR_HITS = {}
FLOWS_FOLDER = Path("flows")    # may be overridden by orchestrator
MEMORY_ROOT = Path("memory")    # may be overridden by orchestrator

# XI repo root (assumes this file is in abilities/common_abilities/)
ROOT = Path(__file__).resolve().parents[2]

# === Single-pass GAEL helpers (quiet, non-stream) ============================
_FENCE_RE = re.compile(r"```(?:\s*)(SUMMARY|THOUGHTS|DECISIONS)\s*\n(.*?)\n```", re.IGNORECASE|re.DOTALL)

def _first_fenced(text: str, prefer: str) -> str:
    if not isinstance(text, str) or not text:
        return ""
    prefer = (prefer or "").upper()
    blocks = list(_FENCE_RE.finditer(text))
    for m in blocks:
        tag = (m.group(1) or "").upper()
        if tag == prefer:
            return (m.group(2) or "").strip()
    return (blocks[0].group(2).strip() if blocks else text.strip())

class _stream_off:
    """Temporarily force non-streaming model calls for GAEL."""
    def __enter__(self):
        self._prev = os.environ.get("XI_LIVE_STREAM")
        os.environ["XI_LIVE_STREAM"] = "0"
    def __exit__(self, *exc):
        if self._prev is None:
            os.environ.pop("XI_LIVE_STREAM", None)
        else:
            os.environ["XI_LIVE_STREAM"] = self._prev

def _prompt_summary(flow_context: dict) -> str:
    return get_gael_summary_prompt(flow_context)

def _prompt_thoughts(flow_context: dict) -> str:
    return get_gael_thoughts_prompt(flow_context)

def _prompt_decisions(flow_context: dict) -> str:
    return get_gael_decisions_prompt(flow_context)

def _emit_single_pass_gael(agent: str, flow_context: dict):
    """Quiet single-pass GAEL: Summary/Thoughts/Decisions, tagged once into thread."""
    from flows.flow_orchestrator import run_agent_with_runtime, run_agent
    with _stream_off():
        s_raw = run_agent_with_runtime(agent, _prompt_summary(flow_context))
        t_raw = run_agent_with_runtime(agent, _prompt_thoughts(flow_context))
        d_raw = run_agent_with_runtime(agent, _prompt_decisions(flow_context))
    # extract first fenced blocks
    summary   = _first_fenced(s_raw, "SUMMARY")
    thoughts  = _first_fenced(t_raw, "THOUGHTS")
    decisions = _first_fenced(d_raw, "DECISIONS")
    # tag once into thread so the journal parser can find them
    if summary:   run_agent(agent, "[GAEL Summary]",   f"[Summary]: {summary}")
    if thoughts:  run_agent(agent, "[GAEL Thoughts]",  f"[Thoughts]: {thoughts}")
    if decisions: run_agent(agent, "[GAEL Decisions]", f"[Decisions]: {decisions}")

def handle_sudo_command(agent: str, text: str):
    """
    Returns a string (response to show) if a sudo command is handled,
    or None if it's not a sudo command.
    """
    import shlex
    if not isinstance(text, str) or not text.strip().lower().startswith("sudo "):
        return None
    # Best-effort tokenize; if it fails due to quotes, ignore as non-sudo to avoid crashing flows
    try:
        toks = shlex.split(text.strip())
    except Exception:
        return None
    if len(toks) < 2 or toks[0].lower() != "sudo":
        return None
    if toks[1].lower() == "sandbox":
        return "🚫 sandbox disabled by configuration"
    else:
        return None

    # subcommands: new | run | promote
    sub = toks[2].lower() if len(toks) > 2 else ""

SESSION_CACHE_MAX_ENTRIES = 10
SESSION_CACHE_MAX_BYTES = 4096


def session_cache_txt_path(agent: str) -> Path:
    return ROOT / "memory" / agent / "session_cache.txt"

def _materialize_blocks(blocks):
    return ("\n--- entry ---\n" + "\n--- entry ---\n".join(blocks)) if blocks else ""

def append_session_cache_txt(agent: str, tag: str, text: str, ts: str | None = None) -> None:
    # session cache disabled
    return None

# ==============================
# GAEL prompt builders (inline)
# ==============================

def _get_summary_prompt(agent: str, flow_context: dict) -> str:
    return get_gael_summary_prompt(flow_context)

def _get_thoughts_prompt(agent: str, flow_context: dict) -> str:
    return get_gael_thoughts_prompt(flow_context)

def _get_decisions_prompt(agent: str, flow_context: dict) -> str:
    return get_gael_decisions_prompt(flow_context)

def _run_in_thread_gael_for_agent(agent: str):
    # Lazy import to avoid circulars at module load
    from flows.flow_orchestrator import run_agent_with_runtime, run_agent, FLOW_CONTEXT

    prompts = [
        ("Summary",   _get_summary_prompt),
        ("Thoughts",  _get_thoughts_prompt),
        ("Decisions", _get_decisions_prompt),
    ]

    # Extract first fenced block from model output (fallback to raw)
    FENCE_RE = re.compile(r"```(?:\s*)(SUMMARY|THOUGHTS|DECISIONS)\s*\n(.*?)\n```",
                          re.IGNORECASE | re.DOTALL)
    def first_fenced(text: str, prefer: str) -> str:
        if not isinstance(text, str) or not text:
            return ""
        prefer = (prefer or "").upper()
        blocks = list(FENCE_RE.finditer(text))
        for m in blocks:
            tag = (m.group(1) or "").upper()
            if tag == prefer:
                return m.group(2).strip()
        return (blocks[0].group(2).strip() if blocks else text.strip())

    # Prevent partial fence deltas while GAEL is running
    with _stream_off():
        for label, prompt_fn in prompts:
            agent_prompt = prompt_fn(agent, FLOW_CONTEXT)
            # Log GAEL prompt under agent identity (not "user")
            run_agent(agent, f"[GAEL {label} Prompt]", agent_prompt)
            output = run_agent_with_runtime(agent, agent_prompt)
        # Normalize to a single bracketed line the parser can pick up
        prefer = {"Summary":"SUMMARY","Thoughts":"THOUGHTS","Decisions":"DECISIONS"}[label]
        body = first_fenced(output, prefer)
        tagged = f"[{label}]: {body}"
        run_agent(agent, agent_prompt, tagged)

# ==============================
# Command Router
# ==============================

def handle_start_flow(command: str):
    cmd_lo = command.strip().lower()
    parts = command.strip().split()
    # sudo_command_handler.py (inside command router)
    if cmd_lo == "sudo checkpoints on":
        FLOW_CONTEXT["allow_checkpoint_journals"] = True
        print("✅ Checkpoint journals enabled for this flow.")
        return NOOP
    if cmd_lo == "sudo checkpoints off":
        FLOW_CONTEXT["allow_checkpoint_journals"] = False
        print("🚫 Checkpoint journals disabled for this flow.")
        return NOOP

    if len(parts) == 3:
        print("📜 Available flows:")
        for path in sorted(FLOWS_FOLDER.glob("*.py")):
            name = path.stem
            if name not in ["__init__", "flow_orchestrator", "routines_orchestrator"]:
                print(f"  - {name}")
        return NOOP

    elif len(parts) == 4:
        flow_name = parts[3]
        flow_file = FLOWS_FOLDER / f"{flow_name}.py"
        if flow_file.exists():
            print(f"✅ Flow '{flow_name}' is available. You can now start it manually.")
            return START_PREFIX + flow_name
        else:
            print(f"❌ Flow '{flow_name}' not found.")
            return NOOP

    else:
        print("⚠️ Invalid command format. Use: sudo start flow OR sudo start flow <name>")
        return NOOP

def check_for_sudo_commands(
    user_input: str,
    flow_context: dict,
    vector_state: dict,
    last_hits: dict,
    agent_profiles: dict,
    flows_folder: Path,
    memory_root: Path,
) -> str:
    """Return:
       - None  → not handled
       - NOOP  → handled, nothing further
       - START_PREFIX+name / END / REFLECT / SESSION_JOURNAL → control signals
    """
    # Make local context available
    global FLOW_CONTEXT, VECTOR_SEARCH_STATE, AGENT_PROFILES, LAST_VECTOR_HITS, FLOWS_FOLDER, MEMORY_ROOT
    FLOW_CONTEXT = flow_context
    VECTOR_SEARCH_STATE = vector_state
    AGENT_PROFILES = agent_profiles
    LAST_VECTOR_HITS = last_hits
    FLOWS_FOLDER = flows_folder
    MEMORY_ROOT = memory_root

    # === 🔐 Access control ===
    AUTHORIZED_SUDO_USER = "Brandon"
    user = flow_context.get("user", "").strip().lower()
    if user != AUTHORIZED_SUDO_USER.lower():
        if user_input.strip().lower().startswith("sudo"):
            print("⚠️ Unauthorized sudo attempt.")
            return NOOP
        return None

    cmd = user_input.strip()
    cmd_lo = cmd.lower()
    parts = cmd.split()

    # Fast exit: not a sudo command we handle
    SUDO_COMMANDS = (
        "sudo clear cache",
        "sudo end flow",
        "sudo start flow",
        "sudo switch flow",
        "sudo vector",
        "sudo write journals",
        "sudo reflect",
        "sudo inspect vector",
        "sudo memory hits",
        "sudo flow status",
        # removed: session cache disabled
        "sudo promote memory",
        "sudo amend",
        "sudo session flow",
        # flash commands disabled:
        "sudo flash view",
        "sudo flash clear",
        "sudo flash add",
        # removed: session cache disabled
        "sudo cache add",
    )
    if not any(cmd_lo.startswith(prefix) for prefix in SUDO_COMMANDS):
        return None

    # --- Flash controls (disabled) ---
    if cmd_lo.startswith("sudo flash view"):
        print("🚫 flash cache is disabled.")
        return NOOP

    if cmd_lo.startswith("sudo flash clear"):
        print("🚫 flash cache is disabled.")
        return NOOP

    if cmd_lo.startswith("sudo flash add"):
        print("🚫 flash cache is disabled.")
        return NOOP

    # --- Session cache controls (TXT) ---
    # (removed) cache view — session cache disabled

    # (removed) cache view — session cache disabled

    # (removed) cache view — session cache disabled

    # === 🔁 Flow control ===
    if cmd_lo == "sudo end flow":
        print("🧼 Ending current flow...")
        from flows.flow_orchestrator import FLOW_CONTEXT as FCX, end_flow
        # Decide whether to fetch fresh GAEL now (single pass, non-stream)
        want_gael = os.getenv("XI_SUDO_END_FLOW_GAEL", "1") == "1"
        if want_gael:
            for agent in FCX.get("agents_active", []):
                if agent in (agent_profiles or {}):
                    try:
                        _emit_single_pass_gael(agent, FCX)
                    except Exception as e:
                        print(f"⚠️ GAEL single-pass failed for {agent}: {e}")
        # Minimal journal write; no post indexing/sentinel
        end_flow(journal_only=False, write_journals=True, emit_gael=False, minimal=False)
        return END

    if cmd_lo.startswith("sudo start flow"):
        return handle_start_flow(cmd)

    if cmd_lo.startswith("sudo switch flow"):
        if len(parts) == 4:
            new_flow = parts[3]
            print(f"🔁 Switching flow to {new_flow} and journaling current state...")
            from flows.flow_orchestrator import end_flow, start_flow, FLOW_CONTEXT as FCX
            end_flow(journal_only=True)
            model = FCX.get("model")
            if not model:
                print(f"🛑 Cannot switch to flow '{new_flow}' — no model path in FLOW_CONTEXT.")
                return NOOP
            start_flow(new_flow, temp=FCX.get("temp", 0.6), model=model)
            print(f"✅ Flow switched to {new_flow}.")
            return START_PREFIX + new_flow
        else:
            print("⚠️ Usage: sudo switch flow <name>")
            return NOOP

    # === 🧠 Journaling & Reflection ===
    if cmd_lo in ("sudo reflect", "sudo write journals"):
        print("📝 Reflecting mid-session and writing journals...")
        from flows.flow_orchestrator import end_flow, reflect_response, FLOW_CONTEXT as FCX
        end_flow(journal_only=True, write_journals=True)
        for agent in FCX.get("agents_active", []):
            reflect_response(agent)
        return REFLECT

    if cmd_lo == "sudo session flow":
        print("📘 Writing session journals without ending flow...")
        from flows.flow_orchestrator import end_flow
        end_flow(journal_only=True, write_journals=True)
        return SESSION_JOURNAL

    # === 🧬 Vector Controls (non-breaking) ===
    if cmd_lo == "sudo memory hits":
        for agent, hits in last_hits.items():
            print(f"\n🧠 Top memory hits for {agent}:")
            if not hits:
                print("  (No results)")
                continue
            for i, hit in enumerate(hits, 1):
                snippet = hit.get("content", "")[:100].replace("\n", " ")
                score = hit.get("score", hit.get("distance", 0))
                try:
                    print(f"  {i}. Score: {float(score):.4f} — {snippet}...")
                except Exception:
                    print(f"  {i}. Score: {score} — {snippet}...")
        return NOOP

    if cmd_lo == "sudo inspect vector":
        print(json.dumps(last_hits, indent=2))
        return NOOP

    if cmd_lo.startswith("sudo vector"):
        if len(parts) == 2:
            print("📊 Usage: sudo vector <agent> <count> — or 'sudo vector status'")
            return NOOP
        if len(parts) == 3 and parts[2].lower() == "status":
            print("📈 Vector search remaining per agent:")
            for agent, count in vector_state.get("agents", {}).items():
                print(f"  - {agent}: {count} remaining")
            return NOOP
        if len(parts) == 3:
            agent = parts[2]
            if agent in vector_state.get("agents", {}):
                vector_state["agents"][agent] += 1
                print(f"✅ Granted 1 additional vector search to {agent}. Now: {vector_state['agents'][agent]}")
            else:
                print(f"⚠️ Unknown agent: {agent}")
            return NOOP
        if len(parts) == 4:
            agent = parts[2]
            try:
                count = int(parts[3])
                if agent in vector_state.get("agents", {}):
                    vector_state["agents"][agent] += count
                    print(f"✅ Granted {count} vector searches to {agent}. Now: {vector_state['agents'][agent]}")
                else:
                    print(f"⚠️ Unknown agent: {agent}")
            except ValueError:
                print("⚠️ Invalid count format.")
            return NOOP

    # === 🧠 System Meta (non-breaking) ===
    if cmd_lo == "sudo flow status":
        print(f"🌀 Current Flow: {flow_context.get('flow_type', 'Unknown')}")
        print(f"Temp: {flow_context.get('temp')} — Model: {flow_context.get('model')}")
        print("Vector Limits:")
        for agent, count in vector_state.get("agents", {}).items():
            print(f"  - {agent}: {count} remaining")
        return NOOP

    if cmd_lo == "sudo view journal cache":
        print("🚫 session cache is disabled.")
        return NOOP

    # === 📈 Summary Promotion (non-breaking) ===
    if cmd_lo == "sudo promote memory":
        print("📈 Promoting all summaries and archiving layers...")
        from abilities.common_abilities.write_all_summaries_then_archive import promote_all_summaries
        promote_all_summaries()
        return NOOP

    # === ✍️ Amend Memory (non-breaking) ===
    if cmd_lo == "sudo amend":
        from abilities.common_abilities.amend_memory_entry import run_amend_flow
        run_amend_flow()
        return NOOP

    # === 🧼 Clear Caches (non-breaking) (disabled)===
    if cmd_lo == "sudo clear cache":
        print("🧼 Session cache disabled (nothing to clear).")
        return NOOP

    return NOOP

