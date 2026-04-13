#!/usr/bin/env python3
"""
📡 vector_controller.py
Handles all `sudo vector` commands from flow_orchestrator.
Delegates semantic search using memory_search.py.
"""

from abilities.common_abilities.memory_search import search_memories
import re
from abilities.common_abilities.reverse_vector_search import rvs, format_report
from abilities.common_abilities.recency_search import recency_scoped_search
from abilities.common_abilities.situational_awareness_prompts import vector_result_prompt


# === 🧠 Entry Point ===
def handle_vector_command(command: str,
                          flow_context=None,
                          vector_state=None,
                          agent_profiles=None) -> str:
    if vector_state is None:
        return "❌ Vector system not initialized."

    command = command.strip()

    # ─── /rvs: Reverse Vector Search (claims history) ───────────────────────────
    if command.startswith("/rvs"):
        # Formats supported:
        #   /rvs subject=<subj> predicate=<pred> [value=<val>] [agents=a,b] [limit=100]
        #   /rvs subject_prefix=<prefix> [predicate=<pred>] [agents=a,b] [limit=100]
        #   /rvs <subject> <predicate> [value]                  (quick form)
        args = command.split()[1:]

        # defaults
        subject = predicate = value = subject_prefix = None
        agents = None
        limit = 100

        # quick form: /rvs subj pred [val]
        if args and "=" not in args[0]:
            if len(args) >= 1: subject = args[0]
            if len(args) >= 2: predicate = args[1]
            if len(args) >= 3: value = args[2]
            args = args[3:]  # remainder may still have key=val items

        # key=val items
        for tok in args:
            if "=" not in tok:
                continue
            k, v = tok.split("=", 1)
            k = k.strip().lower()
            v = v.strip()
            if k == "subject": subject = v
            elif k == "predicate": predicate = v
            elif k == "value": value = v
            elif k in ("subject_prefix", "subject-prefix", "prefix"): subject_prefix = v
            elif k == "agents": agents = [a.strip() for a in v.split(",") if a.strip()]
            elif k == "limit":
                try: limit = int(v)
                except ValueError: pass

        try:
            occ = rvs(
                subject=subject,
                predicate=predicate,
                value=value,
                agents=agents,
                limit=limit,
                subject_prefix=subject_prefix
            )
            report = format_report(occ)
            return "```\n" + report + "\n```"
        except Exception as e:
            return f"❌ /rvs failed: {e}"

    # ─── /recent "query" [hours=6] [agents=a,b] [limit=5] ──────────────────────
    m = re.match(r'^/recent\s+"([^"]+)"(?:\s+hours=(\d+))?(?:\s+agents=([\w,]+))?(?:\s+limit=(\d+))?$', command)
    if m:
        q = m.group(1)
        hours = int(m.group(2)) if m.group(2) else 6
        agents = [a.strip() for a in (m.group(3) or "davinci,hermes").split(",") if a.strip()]
        limit = int(m.group(4)) if m.group(4) else 5

        try:
            rr = recency_scoped_search(q, agents=agents, hours=hours, limit=limit)
            if rr.get("results"):
                hdr = "Recency Search"
                hint = vector_result_prompt(len(rr["results"]))
                lines = [f"- {r['agent']} :: {r['file']} :: score={float(r.get('score',0.0)):.2f}" for r in rr["results"]]
                return f"{hdr}\n{hint}\n" + "\n".join(lines)
            else:
                return "Recency Search\nNo recent matches."
        except Exception as e:
            return f"❌ /recent failed: {e}"



    if command == "sudo end vector":
        vector_state["active"] = False
        vector_state["agent"] = None
        return "🛑 Vector mode ended."

    if command.startswith("sudo vector status"):
        if not vector_state["active"]:
            return "🟡 No active vector session."
        agent = vector_state.get("agent")
        remaining = vector_state["agents"].get(agent, 0)
        return f"📊 Vector session active for `{agent}` — {remaining} remaining."

    # Parse: sudo vector [agent] [optional top_k]
    tokens = command.split()
    if len(tokens) == 2:
        return "❌ Specify an agent. Example: `sudo vector hermes`"
    elif len(tokens) == 3:
        agent = tokens[2]
        top_k = 3
    elif len(tokens) == 4:
        agent = tokens[2]
        try:
            top_k = int(tokens[3])
        except ValueError:
            return "❌ Invalid top_k value. Example: `sudo vector hermes 5`"
    else:
        return "❌ Invalid vector command format."

    if agent not in vector_state["agents"]:
        return f"❌ Unknown agent: {agent}"

    if not vector_state["infinite"].get(agent, False):
        remaining = vector_state["agents"].get(agent, 0)
        if remaining <= 0:
            return f"🔒 `{agent}` has exhausted vector search quota."
        vector_state["agents"][agent] = remaining - 1

    vector_state["active"] = True
    vector_state["agent"] = agent

    # Small GAEL hint to encourage proper selection + flash logging
    quota_line = f"{remaining} remaining." if not vector_state['infinite'].get(agent, False) else "infinite."
    gael_hint = (
        "\n\nYou will be shown small memory previews.\n"
        "Pick **at most 1–2**, and log:\n"
        "[Memory Considered]: <id> <why>\n"
        "[Memory Rejected]: <id> <why>\n"
        "[Memory Selected]: <id> <why>\n"
        "Only selected chunks will be injected."
    )
    return f"✅ Vector search activated for `{agent}` — {quota_line}{gael_hint}"

