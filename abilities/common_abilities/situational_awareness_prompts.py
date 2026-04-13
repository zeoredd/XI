# 🧠 situational_awareness_prompts.py
# Context-aware prompt scaffolding for GAEL (Guided Agent Experience Layer)

# --- GAEL helper API (small, reusable) ---
def gael_preamble(flow_name: str, agent: str, purpose: str) -> str:
    """
    Short pre-turn scaffold: identity scope, retrieval options, and trio format.
    """
    return (
        f"You are **{agent}** running the **{flow_name}** flow.\n"
        f"Purpose: {purpose}\n\n"
        "Identity scope: **self only**.\n"
        "Use retrieval only if needed:\n"
        "• `/recent \"<query>\" hours=N` for fresh context\n"
        "• `/rvs subject:predicate:value` for structured facts\n\n"
        "When you finish, return ONLY three fenced blocks:\n\n"
        "```SUMMARY\n"
        "One concise paragraph (≤240 words). No bullets. Plain prose.\n"
        "```\n\n"
        "```THOUGHTS\n"
        "3–5 short bullets (≤160 characters each). Insights, risks, or open questions.\n"
        "```\n\n"
        "```DECISIONS\n"
        "2–4 concrete action lines in the form:\n"
        "- Action — Owner — When\n"
        "Each line ≤200 characters.\n"
        "```\n"
    )

def gael_retrieval_selection(n_hits: int) -> str:
    """
    Pair a compact 'how to choose' instruction with your existing result header.
    """
    try:
        hdr = vector_result_prompt(n_hits)  # existing helper
    except Exception:
        hdr = f"Top {n_hits} memory previews."
    return (
        f"{hdr}\n"
        "Pick **at most 1–2** that are essential.\n"
        "Briefly justify your choice internally; only the selected chunk will be injected this turn.\n"
    )

def gael_inline_amend_guidance(conflicts: list) -> str:
    """
    Wrap your conflict-message builder so callers just pass conflict dicts.
    """
    try:
        return build_gael_conflict_message(conflicts)
    except Exception:
        return "A memory conflict was detected. Propose a non-destructive amendment; history will be preserved."

def gael_rollup_guidance(level: str, source_files: list) -> str:
    """
    Level-aware rollup guidance with traceability reminder.
    """
    try:
        base = summary_promotion_intro(level, source_files)  # existing helper
    except Exception:
        base = f"Produce a {level} rollup."
    return (
        f"{base}\n"
        "Produce an objective rollup with file/uuid references.\n"
        "Deduplicate decisions; keep it concise.\n"
    )


def flash_cache_intro():
    # flash cache disabled — keep callable to avoid import errors, but say nothing
    return ""

def journal_intro(session_cache_summary, thread_file_path):
    return (
        "✍️ **Session Journal Writing Mode**\n"
        "You are now reflecting on a completed session.\n\n"
        "**Please include:**\n"
        "- A concise **summary** of what occurred\n"
        "- Your **thoughts**, interpretations, or realizations\n"
        "- Any **decisions** made or actions planned\n\n"
        f"📎 **Thread reference**: `{thread_file_path}`\n"
        f"🧠 **Session Cache Summary**:\n{session_cache_summary.strip()}\n\n"
        "⚠️ **Important Reminder**:\n"
        "Do **not** copy raw memory blocks (e.g., `{ \"entries\": [...] }`) into your journal.\n"
        "Instead, summarize recalled memory in your own words.\n"
        "Avoid recursive nesting or re-embedding prior journal structures inside `thoughts` or `decisions`.\n"
    )

def summary_promotion_intro(level, source_files):
    return (
        f"📈 Promoting to **{level} Summary**\n"
        f"Please read and integrate the key themes, ideas, and progress from the following source entries:\n"
        f"📂 Files: {', '.join(source_files)}\n"
    )

def vector_result_prompt(results_count):
    return (
        f"🔍 **{results_count} vector memory hits found**\n"
        "You may select one to inject into your prompt context.\n"
        "This can help reconnect you with relevant background or past logic.\n"
    )

def vector_injection_notice():
    return (
        "✅ **Selected memory injected.**\n"
        "Treat this as a natural recall — you just remembered something meaningful.\n"
    )

def flow_completion_message():
    return (
        "✅ **Flow Completed**\n"
        "Your journal entry has been archived, and flash cache wiped.\n"
        "You may now idle or await further instruction.\n"
    )

def vector_search_intro():
    return (
        "You're being offered vector memory — relevant past context the user or system believes may help you reason better.\n"
        "Please scan the memory snippet carefully, consider how it might relate to the current prompt, and integrate it into your response only if appropriate."
    )

def build_gael_conflict_message(conflicts):
    """
    conflicts: list of dicts like
      { "subject": str, "predicate": str, "new_value": Any,
        "existing": [ { "value": Any, "time_start": "...", "weight": 1.0, "claim_id": "..." }, ... ] }
    """
    lines = []
    for item in conflicts or []:
        subj = item.get("subject","?")
        pred = item.get("predicate","?")
        newv = item.get("new_value")
        existing_vals = [e.get("value") for e in (item.get("existing") or [])]
        lines.append(f"- {subj}.{pred} : new={repr(newv)} vs existing={existing_vals}")
    detail = "\n".join(lines) or "- (no details available)"
    return (
        "[GAEL]\n"
        "A potential memory conflict was detected just before commit.\n"
        "Review and choose the safest path. If uncertain, prefer a non-destructive overlay.\n\n"
        "Conflicts:\n"
        f"{detail}\n\n"
        "Respond ONLY with one of:\n"
        "- keep (explain why)\n"
        "- overlay_fix (explain the minimal correction)\n"
        "- escalate (explain why)\n"
    )

def gael_selection_hint():
    """Short reminder used just before showing vector previews."""
    return "Pick at most 1–2 essential snippets; prefer precision over volume."

def gael_claim_guard_header():
    """One-liner header when surfacing pre-commit claim conflicts inline."""
    return "🛡️ **Claim Guard** found conflicts. Propose a minimal, reversible overlay if appropriate."

# --- Central GAEL Journal Prompt Specs ---

GAEL_SUMMARY_PROMPT = """[GAEL] Return ONLY the summary fenced as:
```SUMMARY
One concise paragraph (≤240 words). No bullets. Plain prose.
```"""

GAEL_THOUGHTS_PROMPT = """[GAEL] Return ONLY the thoughts fenced as:
```THOUGHTS
3–5 short bullets (≤160 characters each).
Stick to observations from THIS session. No roleplay or fiction.
Each bullet is an insight, a risk, or an open question grounded in the thread.
```"""

GAEL_DECISIONS_PROMPT = """[GAEL] Return ONLY the decisions fenced as:
```DECISIONS
2–4 lines capturing ONLY decisions already made or next actions we explicitly agreed to in THIS session.
Do NOT propose new actions. Do NOT speculate.
If none were decided, return exactly: None

Format each decided item as:
- Action — Owner — When
(Each line ≤200 characters.)
```"""

def get_gael_summary_prompt(flow_context: dict) -> str:
    return GAEL_SUMMARY_PROMPT

def get_gael_thoughts_prompt(flow_context: dict) -> str:
    return GAEL_THOUGHTS_PROMPT

def get_gael_decisions_prompt(flow_context: dict) -> str:
    return GAEL_DECISIONS_PROMPT

# 🛠 GAEL correction hint for claims
correction_hint = """
If you discover a conflict or incorrect memory:
- Write the corrected claims in a [CLAIMS] ... [/CLAIMS] block.
- Keep them accurate and minimal (subject, predicate, value).
- Example:
[CLAIMS]
gather:launch_date = 2025-09-01
gather:status = pending
[/CLAIMS]
"""

#def sandbox_policy_block():
#    return (
#        "[GAEL]\n"
#        "Sandbox Policy:\n"
#        "• Use the sandbox for code experiments. Start with sandbox.new_session(label).\n"
#        "• Write code to ./code and results to ./results.\n"
#        "• Review logs after each run; promote only good artifacts:\n"
#        "  sandbox.promote(session_id, [\"results/...\",\"code/...\"], note, kind=\"projects\")\n"
#        "• Promotions persist to memory (thread_type=\"sandbox\"); unpromoted sessions may be auto-cleaned.\n"
#        "• Prefer summaries/metrics over raw dumps; avoid spamming memory.\n"
#    )






