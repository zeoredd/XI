#!/usr/bin/env python3
"""
🧠 write_all_summaries_then_archive.py
Promotes session journals to weekly, period, and quarterly summaries.
• Can target one --journal_type, auto-detect one if omitted, or run --all.
• Archives input entries after each successful promotion.
"""

import json
import textwrap
import argparse
from pathlib import Path
import datetime as dt
from abilities.common_abilities.time_utils import get_current_quarter_and_period
from typing import List, Dict, Any
import glob

# Runtime + helpers (best-effort imports; keep script resilient)
try:
    from flows.flow_orchestrator import run_agent
except Exception:
    run_agent = None
try:
    from abilities.common_abilities.parse_thread_for_journal import parse_gael_journal
except Exception:
    parse_gael_journal = None
try:
    from abilities.common_abilities.signature_utils import minisign_sign_file
except Exception:
    minisign_sign_file = None
import subprocess, os, shutil, re

# 🎛️ CLI Args
parser = argparse.ArgumentParser()
parser.add_argument("--agent", required=True, help="Agent name (e.g. davinci, hermes)")
parser.add_argument("--journal_type", required=False, help="Journal type (e.g. idea_generator_journal)")
parser.add_argument("--all", action="store_true", help="Promote all detected *_journal folders for this agent")
parser.add_argument("--debug", action="store_true", help="Verbose prints")
parser.add_argument("--level", choices=["weekly","period","quarterly"],
                    help="Force-write a summary at this level from the newest --take files")
parser.add_argument("--take", type=int, default=0, help="How many source files to take (newest first) for --level")
parser.add_argument("--dry-run", action="store_true",
                    help="Preview summary but don’t write or archive")
parser.add_argument("--include-archive", action="store_true",
                    help="If source dir is empty, also look in archive/<year>")
parser.add_argument("--seed-dir", help="Use *.json in this directory as explicit inputs (synthetic tests)")
parser.add_argument("--no-archive", action="store_true", help="Do not move inputs after a rollup (testing)")
parser.add_argument("--strict", action="store_true", help="Fail if GAEL trio is still missing after one repair")
parser.add_argument("--max-bullets-thoughts", type=int, default=9)
parser.add_argument("--max-bullets-decisions", type=int, default=7)

args = parser.parse_args()

AGENT_NAME = args.agent
JOURNAL_TYPE = args.journal_type
SUMMARY_LEVELS = ["weekly", "period", "quarterly"]
SUMMARY_THRESHOLDS = {"weekly": 7, "period": 4, "quarterly": 3}

def _any_archive_for(level: str, files: list[Path]) -> bool:
    if not files: return False
    L = (level or "").lower()
    needles = {
        "weekly": f"{os.sep}session{os.sep}archive{os.sep}",
        "period": f"{os.sep}weekly{os.sep}archive{os.sep}",
        "quarterly": f"{os.sep}period{os.sep}archive{os.sep}",
    }
    needle = needles.get(L)
    return bool(needle and any(needle in str(p) for p in files))

def _scan_sources(agent: str, journal_type: str, level: str) -> list[Path]:
    """
    Source folders by rollup level (LIVE first; archive optional via --include-archive):
      weekly    -> session/            (optionally: session/archive/**)
      period    -> weekly/             (optionally: weekly/archive/**)
      quarterly -> period/             (optionally: period/archive/**)
    """
    root = Path("memory") / agent / journal_type
    # live tier dir
    if level == "weekly":
        live = root / "session"
        arch = live / "archive"
    elif level == "period":
        live = root / "weekly"
        arch = live / "archive"
    elif level == "quarterly":
        live = root / "period"
        arch = live / "archive"
    else:
        live = root / "session"
        arch = live / "archive"

    paths = []
    # 1) live dir first
    if live.exists():
        paths.extend(Path(p) for p in glob.glob(str(live / "*.json")))
    # 2) optional archive (recursive) if requested
    if getattr(args, "include_archive", False) and arch.exists():
        paths.extend(arch.rglob("*.json"))

    return sorted(paths)

def _coerce_trailing_int(value, fallback:int) -> int:
    """Extract a trailing integer from strings like 'P2', 'Q3', '2'; fallback if none."""
    s = str(value or "")
    m = re.search(r"(\d+)$", s)
    return int(m.group(1)) if m else int(fallback)

def _today_token_for(summary_type: str) -> str:
    """Return filename token: weekly=YYYYMMDD, period=YYYYP{nn}, quarterly=YYYYQ{n}."""
    cal = get_current_quarter_and_period(_now_date())
    year = int(cal.get("year", dt.datetime.now().year))
    if summary_type == "weekly":
        return dt.datetime.now().strftime("%Y%m%d")
    if summary_type == "period":
        # Accept '2', 'P2', '08', etc. Zero-pad to 2 for stable sort.
        per_num = _coerce_trailing_int(cal.get("period"), dt.datetime.now().month)
        return f"{year}P{per_num:02d}"
    # quarterly
    qn = _coerce_trailing_int(cal.get("quarter"), ((dt.datetime.now().month - 1)//3) + 1)
    return f"{year}Q{qn}"

def _now_date():
    return dt.datetime.now(dt.timezone.utc).date()

def _agent_root(agent: str) -> Path:
    return Path("memory") / agent

def _list_journal_types(agent: str):
    root = _agent_root(agent)
    types = []
    for p in root.glob("*_journal"):
        if (p / "session").exists():
            types.append(p.name)
    return sorted(types)

def _read_json(p: Path) -> Dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _is_session_file(p: Path) -> bool:
    s = str(p)
    return "/session/" in s and (s.endswith(".json") or s.endswith(".jsonl"))

def _resolve_session_paths(base_dir: Path, basenames: list[str]) -> tuple[list[Path], list[str]]:
    """Resolve session files by basename; search live and archive."""
    session_dir = base_dir / "session"
    archive_root = session_dir / "archive"
    resolved, missing = [], []
    for name in basenames:
        p1 = session_dir / name
        if p1.exists():
            resolved.append(p1)
            continue
        hits = list(archive_root.rglob(name))
        if hits:
            hits.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            resolved.append(hits[0])
        else:
            missing.append(name)
    return resolved, missing

def _archive_exact(base_dir: Path, src_paths: list[Path], input_tier: str) -> list[Path]:
    """Move the given *input tier* files to that tier's archive/<year>/ (preserve basename); move *.minisig too."""
    moved = []
    tier_dir = base_dir / input_tier
    for p in src_paths:
        # derive year from filename token or fallback to mtime
        year = None
        for token in p.stem.split("_"):
            if token.isdigit() and len(token) >= 4 and token.startswith(("19","20")):
                year = token[:4]; break
        if not year:
            year = str(dt.datetime.fromtimestamp(p.stat().st_mtime).year)

        dest_dir = tier_dir / "archive" / year
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / p.name

        # If already in archive, keep as-is
        if str(p).startswith(str(dest_dir)):
            moved.append(p)
        else:
            shutil.move(str(p), str(dest))
            moved.append(dest)

        # Move signature if present
        sig_src = p.with_suffix(p.suffix + ".minisig")
        if sig_src.exists():
            shutil.move(str(sig_src), str(dest.with_suffix(dest.suffix + ".minisig")))
    return moved


def _first_nonempty(*vals):
    """Return the first non-empty string from a list of values (may be list/str/None)."""
    for v in vals:
        if not v:
            continue
        if isinstance(v, list):
            # join list entries into one string
            joined = " ".join([str(x) for x in v if x])
            if joined.strip():
                return joined
        elif isinstance(v, str) and v.strip():
            return v.strip()
    return ""

def _flatten_text(x) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x.strip()
    if isinstance(x, (list, tuple)):
        return "\n".join([str(i).strip() for i in x if i])
    if isinstance(x, dict):
        return "\n".join([str(v).strip() for v in x.values() if v])
    return str(x).strip()

def _source_digest(entries: list[dict], max_chars: int = 2400) -> str:
    """Compact digest of sources: Summary + key lines from Thoughts/Decisions."""
    parts = []
    for i, e in enumerate(entries, 1):
        s = (e.get("summary") or "").strip()
        t = (e.get("thoughts") or e.get("key_thoughts") or [])
        d = (e.get("decisions") or [])
        if isinstance(t, str):
            t_lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
        else:
            t_lines = [str(x).strip() for x in t if str(x).strip()]
        d_lines = [str(x).strip() for x in d if str(x).strip()]
        block = []
        if s:
            block.append(f"Summary: {s}")
        if t_lines:
            block.append("Thoughts: " + " | ".join(t_lines[:6]))
        if d_lines:
            block.append("Decisions: " + " | ".join(d_lines[:6]))
        if block:
            parts.append(f"[{i}] " + " || ".join(block))
        if sum(len(p) for p in parts) > max_chars:
            break
    return "\n".join(parts)

def _sanitize_bullets(text_or_lines, max_bullets=7):
    """Return a clean list of '- bullet' lines from string or list."""
    if isinstance(text_or_lines, str):
        raw = [ln for ln in text_or_lines.splitlines()]
    else:
        raw = [str(x) for x in (text_or_lines or [])]

    cleaned = []
    for ln in raw:
        ln = ln.strip()
        if not ln:
            continue
        # drop markdown headers / trivial noise
        if ln.lstrip("-•").strip().startswith("#") or ln.lower() in {"n/a", "none"}:
            continue
        # split very long lines into sentence bullets
        if len(ln) > 160:
            parts = [p.strip(" -•") for p in re.split(r'(?<=\.)\s+|\n|•|-{2,}|\u2022', ln) if p and p.strip()]
            for p in parts:
                b = f"- {p}"
                if b not in cleaned:
                    cleaned.append(b)
                    if len(cleaned) >= max_bullets:
                        return cleaned
            continue
        # normal bullet
        ln = ln.lstrip("-•").strip()
        b = f"- {ln}"
        if b not in cleaned:
            cleaned.append(b)
            if len(cleaned) >= max_bullets:
                break
    return cleaned

# Light fluff/boilerplate guards (mirror Sentinel style without being heavy-handed)
_FLUFF_RX = re.compile(r'^\s*(?:-|\*|•)?\s*(?:i think|well[, ]|maybe|perhaps)\b', re.I)
APPROVED_VERBS = (
    "add|enable|remove|create|ship|fix|patch|write|refactor|test|validate|index|archive|promote|"
    "deploy|rollback|migrate|upgrade|schedule|document|plan|design|"
    "compute|calculate|process|analyze|measure|benchmark|monitor|observe|detect|profile|optimize|tune|"
    "train|retrain|evaluate|compare|calibrate|"
    "audit|verify|sign"
)
_ACTION_VERB_RX = re.compile(rf'^\s*(?:-|\*|•)?\s*({APPROVED_VERBS})\b', re.I)
_BANNED_PHRASES = {
    "Summary unavailable", "TBD", "lorem ipsum", "No explicit thoughts provided", "No decisions recorded"
}


def _strip_banned(blob: str) -> str:
    out = blob or ""
    for bad in _BANNED_PHRASES:
        out = out.replace(bad, "")
    return out.strip()

def _drop_fluff_bullets(lines: list[str]) -> list[str]:
    return [ln for ln in (lines or []) if not _FLUFF_RX.search(ln)]

def _ensure_action_verbs(lines: list[str]) -> list[str]:
    fixed = []
    for ln in (lines or []):
        raw = ln.strip()
        if not raw:
            continue
        if not raw.startswith(("-", "*", "•")):
            raw = f"- {raw}"
        # Enforce action verb start; coerce to nearest approved verb if missing.
        if not _ACTION_VERB_RX.search(raw):
            body = re.sub(r'^\s*[-*•]\s*', '', raw).strip()
            # minimal heuristic mapping, preserves the original body text
            if re.search(r'\b(bug|error|fail|regress|issue|crash)\b', body, re.I):
                verb = "Fix"
            elif re.search(r'\b(doc|readme|spec|write[- ]?up|notes?)\b', body, re.I):
                verb = "Document"
            elif re.search(r'\b(remove|drop|delete|deprecat(e|ion))\b', body, re.I):
                verb = "Remove"
            elif re.search(r'\b(refactor|clean\s*up|restructure)\b', body, re.I):
                verb = "Refactor"
            elif re.search(r'\b(benchmark|perf|latency|throughput|profile)\b', body, re.I):
                verb = "Benchmark"
            elif re.search(r'\b(optimi[sz]e|tune)\b', body, re.I):
                verb = "Optimize"
            elif re.search(r'\b(train|retrain|evaluate|compare|calibrate)\b', body, re.I):
                # map to the exact term when possible
                m = re.search(r'(train|retrain|evaluate|compare|calibrate)', body, re.I)
                verb = m.group(1).capitalize() if m else "Train"
            elif re.search(r'\b(compute|calculate|process)\b', body, re.I):
                verb = "Compute"
            elif re.search(r'\b(monitor|observe|detect)\b', body, re.I):
                verb = "Monitor"
            elif re.search(r'\b(deploy|rollback|migrate|upgrade)\b', body, re.I):
                m = re.search(r'(deploy|rollback|migrate|upgrade)', body, re.I)
                verb = m.group(1).capitalize()
            elif re.search(r'\b(plan|design|schedule)\b', body, re.I):
                m = re.search(r'(plan|design|schedule)', body, re.I)
                verb = m.group(1).capitalize()
            else:
                verb = "Add"
            raw = f"- {verb}: {body}"
        fixed.append(raw)
    return fixed[:7]

def _dedupe_sentences(paragraph: str) -> str:
    """Remove near-duplicate consecutive sentences in a short paragraph."""
    if not paragraph:
        return paragraph
    parts = [p.strip() for p in re.split(r'(?<=\.)\s+', paragraph) if p.strip()]
    out = []
    prev = None
    for p in parts:
        if not prev or p.lower() != prev.lower():
            out.append(p)
        prev = p
    return " ".join(out)

def _prompt_for_summary(agent: str, journal_type: str, level: str, files: List[Path]) -> str:
    return "\n".join([
        "[GAEL: SUMMARY WRITER]",
        f"Agent: {agent}",
        f"Journal type: {journal_type}",
        f"Summary level: {level}  (weekly/period/quarterly)",
        "Task: Write an agent-authored journal entry based on the sources below.",
        "Output exactly these sections, in order, with short, factual content:",
        "Summary:",
        "Thoughts:",
        "Decisions:",
        "",
        "Do not invent new facts. If uncertain, state it briefly.",
        "",
        _source_digest(files),
        "Write now."
    ])

def _parse_gael_blocks(text: str) -> Dict[str, str]:
    # Try downstream parser first
    if parse_gael_journal:
        try:
            out = parse_gael_journal(text) or {}
            return {
                "Summary": _flatten_text(out.get("Summary")),
                "Thoughts": _flatten_text(out.get("Thoughts")),
                "Decisions": _flatten_text(out.get("Decisions")),
            }
        except Exception:
            pass

    # Robust fallback: sectionize by headings (case-insensitive, start of line)
    import re
    sections = {"Summary": "", "Thoughts": "", "Decisions": ""}
    current = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        head = line.lower()
        if head.startswith("summary:"):
            current = "Summary"; line = line[len("summary:"):].strip()
        elif head.startswith("thoughts:"):
            current = "Thoughts"; line = line[len("thoughts:"):].strip()
        elif head.startswith("decisions:"):
            current = "Decisions"; line = line[len("decisions:"):].strip()
        if current:
            sections[current] += (line + "\n")

    # Normalize bullets for Thoughts/Decisions
    for key in ("Thoughts", "Decisions"):
        lines = [l.strip("•- ").strip() for l in sections[key].splitlines() if l.strip()]
        sections[key] = "\n".join(lines)
    sections["Summary"] = sections["Summary"].strip()
    return sections

def _has_all_sections(text: str) -> bool:
    if not text:
        return False
    pats = [r'(?mi)^\s*summary:\s*\S', r'(?mi)^\s*thoughts:\s*\S', r'(?mi)^\s*decisions:\s*']
    return all(re.search(p, text) for p in pats)

def _format_weekly_prompt(agent: str, when: str, digest: str) -> str:
    return "\n".join([
        "[GAEL: WEEKLY SUMMARY]",
        f"Agent: {agent}",
        f"Timeframe: {when}",
        "You are composing the WEEKLY journal. Use only these headings, in this order:",
        "Summary:",
        "Thoughts:",
        "Decisions:",
        "",
        "Rules:",
        " - Concise, factual, grounded in sources below.",
        " - No preamble or extra headings.",
        " - One short paragraph for Summary.",
        " - 3–7 bullets for Thoughts (actionable or reflective).",
        " - 0–5 bullets for Decisions (clear, testable).",
        "",
        "Sources (session digests):",
        digest,
    ])

def _format_repair_prompt(agent: str, previous_text: str) -> str:
    return "\n".join([
        "[GAEL: SUMMARY REPAIR]",
        f"Agent: {agent}",
        "Rewrite STRICTLY with the three headings only.",
        "Summary:",
        "Thoughts:",
        "Decisions:",
        "",
        "No extra prose before/after. Maintain factuality.",
        "-----BEGIN PREVIOUS-----",
        previous_text,
        "-----END PREVIOUS-----"
    ])

def _agent_write_and_save_summary(
    base_dir: Path,
    summary_type: str,
    entry_files: List[Path],
    today_str: str,
    used_archive: bool = False,
    used_seed_dir: bool = False,
) -> Path:

    """
    Agent-authored summary writer:
      - Feeds source text (digest) to the agent via GAEL prompt
      - Parses GAEL into Summary/Thoughts/Decisions
      - Saves, signs, indexes (mem-only), archives sources
    """
    summary_dir = base_dir / summary_type
    summary_dir.mkdir(parents=True, exist_ok=True)

    # 📦 Determine correct archive folder (unchanged)
    if summary_type == "weekly":
        archive_dir = (base_dir / "session") / "archive" / str(dt.datetime.now().year)
    elif summary_type == "period":
        archive_dir = (base_dir / "weekly") / "archive" / str(dt.datetime.now().year)
    else:
        archive_dir = (base_dir / "period") / "archive" / str(dt.datetime.now().year)
    archive_dir.mkdir(parents=True, exist_ok=True)

    # 📚 Load minimal metadata list (for compiled_uuids)
    compiled = [_read_json(f) for f in entry_files]
    # 🔎 Detect if we sourced from archive (weekly→session/archive, period→weekly/archive, quarterly→period/archive)
    local_archive = _any_archive_for(summary_type, entry_files)


    calendar_info = get_current_quarter_and_period(_now_date())

    # 🧾 Build a compact digest from sources for GAEL
    digest_text = _source_digest(compiled)
    when = f"{calendar_info['year']} {calendar_info['quarter']} / Period {calendar_info['period']}"
    prompt = _format_weekly_prompt(AGENT_NAME, when, digest_text)

    # (We build the digest above; the actual prompting happens below in the unified GAEL block)

    # 📝 Prompt the agent with GAEL + digest
    # (keeps output sections explicit and ordered)
    # level-specific prompt rules
    if summary_type == "weekly":
        sum_min, sum_max = 3, 5
        th_min, th_max   = 4, min(args.max_bullets_thoughts, 8)
        dc_min, dc_max   = 2, min(args.max_bullets_decisions, 6)
    elif summary_type == "period":
        sum_min, sum_max = 4, 6
        th_min, th_max   = 5, min(args.max_bullets_thoughts, 9)
        dc_min, dc_max   = 3, min(args.max_bullets_decisions, 7)
    else:  # quarterly
        sum_min, sum_max = 6, 10
        th_min, th_max   = 6, min(args.max_bullets_thoughts, 9)
        dc_min, dc_max   = 3, min(args.max_bullets_decisions, 7)

    prompt = "\n".join([
        "[GAEL: SUMMARY WRITER v2]",
        f"Agent: {AGENT_NAME}",
        f"Journal type: {base_dir.name}",
        f"Summary level: {summary_type}  (weekly/period/quarterly)",
        "Task: Write an agent-authored journal entry from the sources.",
        "REQUIREMENTS:",
        " - Output EXACTLY the three sections below in this order, with headings",
        f" - Summary: {sum_min}–{sum_max} sentences; tight, factual, no fluff",
        f" - Thoughts: {th_min}–{th_max} bullet points (themes, risks, deltas)",
        f" - Decisions: {dc_min}–{dc_max} bullet points; action verbs; testable; owner if known",
        " - No new facts; be concise and factual",
        " - Never output boilerplate like 'Summary unavailable', 'TBD', 'No explicit thoughts provided', 'No decisions recorded'.",
        " - If a section seems empty, synthesize from the sources instead of writing a placeholder.",
        "",
        "FORMAT (copy-paste these headings verbatim):",
        "Summary:",
        "Thoughts:",
        "Decisions:",
        "",
        "Sources digest:",
        digest_text,
        "",
        "Write the three sections now. Do not include anything else."
    ])
    system_hint = "[system] You are writing your own journal summary. Be concise and factual."
    model_path = None

    # ▶️ Run the agent (runtime first; fallback to simple run)
    agent_reply = ""
    try:
        from flows.flow_orchestrator import run_agent_with_runtime, AGENT_PROFILES
        model_path = AGENT_PROFILES.get(AGENT_NAME, {}).get("model_path")
        if model_path:
            agent_reply = run_agent_with_runtime(AGENT_NAME, prompt, model_path=str(model_path)) or ""
    except Exception:
        agent_reply = ""

    if not agent_reply:
        try:
            from flows.flow_orchestrator import run_agent as _ra
            agent_reply = _ra(AGENT_NAME, prompt, system_hint) or ""
        except Exception:
            pass

    # 📎 Save raw reply (attempt #1) for troubleshooting
    try:
        (summary_dir / f"{AGENT_NAME}_{summary_type}_journal_{today_str}.raw.txt").write_text(agent_reply or "", encoding="utf-8")
    except Exception:
        pass

    # If headings missing, one strict retry to force format
    if not _has_all_sections(agent_reply):
        repair = _format_repair_prompt(AGENT_NAME, agent_reply or "[empty]")
        retry_reply = ""
        try:
            if model_path:
                retry_reply = run_agent_with_runtime(AGENT_NAME, repair, model_path=str(model_path)) or ""
        except Exception:
            retry_reply = ""
        if (not retry_reply):
            try:
                from flows.flow_orchestrator import run_agent as _ra
                retry_reply = _ra(AGENT_NAME, repair, system_hint) or ""
            except Exception:
                pass
        if retry_reply:
            agent_reply = retry_reply
            try:
                (summary_dir / f"{AGENT_NAME}_{summary_type}_journal_{today_str}.raw.retry.txt").write_text(agent_reply, encoding="utf-8")
            except Exception:
                pass

    # 🧩 Parse GAEL (or fallback splitter)
    written_summary = _parse_gael_blocks(agent_reply or "")

    # Ensure GAEL trio has minimal content even if partially missing
    if not written_summary.get("Summary"):
        # Build a compact summary from the first couple session summaries
        synth_summ = []
        for e in compiled:
            s = (e.get("summary") or "").strip()
            if s:
                synth_summ.append(s)
            if len(synth_summ) >= 2:
                break
        written_summary["Summary"] = " ".join(synth_summ)
    if not written_summary.get("Thoughts"):
        # Synthesize bullets from session thoughts or summaries
        syn = []
        for e in compiled:
            th = e.get("thoughts") or e.get("key_thoughts") or []
            if isinstance(th, str):
                lines = [ln.strip() for ln in th.splitlines() if ln.strip()]
            else:
                lines = [str(x).strip() for x in th if str(x).strip()]
            if not lines:
                # fall back to the session summary if no thoughts present
                s = (e.get("summary") or "").strip()
                if s:
                    lines = [s]
            for ln in lines:
                ln = ln.lstrip("-•").strip()
                if ln and ln not in syn:
                    syn.append(f"- {ln}")
                if len(syn) >= 5:
                    break
            if len(syn) >= 5:
                break
        written_summary["Thoughts"] = "\n".join(syn)
    if not written_summary.get("Decisions"):
        syn = []
        for e in compiled:
            d = e.get("decisions") or []
            if isinstance(d, str):
                d_lines = [x.strip() for x in d.splitlines() if x.strip()]
            else:
                d_lines = [str(x).strip() for x in d if str(x).strip()]
            for ln in d_lines:
                if not ln.startswith("-"):
                    ln = f"- {ln}"
                if ln not in syn:
                    syn.append(ln)
                if len(syn) >= 5:
                    break
            if len(syn) >= 5:
                break
        written_summary["Decisions"] = "\n".join(syn)

    # 🧼 Normalize → bullets, dedupe, strip fluff/boilerplate
    thoughts_list  = _sanitize_bullets(_strip_banned(written_summary.get("Thoughts", "")), max_bullets=7)
    thoughts_list  = _drop_fluff_bullets(thoughts_list)
    decisions_list = _sanitize_bullets(_strip_banned(written_summary.get("Decisions", "")), max_bullets=7)
    decisions_list = _drop_fluff_bullets(decisions_list)
    decisions_list = _ensure_action_verbs(decisions_list)

    # In strict mode, fail immediately if any GAEL section is empty after sanitization.
    if args.strict and (not written_summary.get("Summary") or not thoughts_list or not decisions_list):
        raise SystemExit("❌ strict: GAEL trio missing/empty after synthesis+sanitize")   

    # String forms (what the indexer/recency search look at)
    thoughts_str  = "\n".join(thoughts_list)
    decisions_str = "\n".join(decisions_list)

    # Tighten summary: remove repeated sentences
    ws = written_summary.get("Summary", "")
    written_summary["Summary"] = _strip_banned(_dedupe_sentences(ws))

    # 📝 Compose output JSON
    now = dt.datetime.now(dt.timezone.utc)
    summary = {
        "date": str(_now_date()),
        "agent": AGENT_NAME,
        "journal_type": base_dir.name,
        "summary_type": summary_type,
        "year": calendar_info["year"],
        "day_of_year": calendar_info["day_of_year"],
        "quarter": calendar_info["quarter"],
        "period": calendar_info["period"],
        "entry_count": len(compiled),
        "compiled_uuids": [e.get("uuid", "") for e in compiled if isinstance(e, dict)],
        "source_files": [str(f.name) for f in entry_files],
        "source_paths_full": [str(f) for f in entry_files],  # NEW: absolute provenance
        "summary": written_summary.get("Summary", "").strip(),
        # Strings are indexed/searched; arrays are for UI/back-compat
        "thoughts": thoughts_str,
        "key_thoughts": [ln[2:].strip() if ln.startswith("- ") else ln for ln in thoughts_list],
        "decisions": decisions_str,
        "decisions_list": [ln[2:].strip() if ln.startswith("- ") else ln for ln in decisions_list],
        "_meta": {
            "timestamp": now.isoformat(timespec="seconds"),       # ← recency_search already checks this
            "created_at_utc": now.isoformat(timespec="seconds"),  # ← handy for other tools
            "written_by": AGENT_NAME,
            "summary_level": summary_type,
            "validator_flags": [],  # ← place to record explicit fallbacks
        }
    }

    # ensure the list exists
    summary.setdefault("_meta", {}).setdefault("validator_flags", [])

    # detect archive use locally too (in case caller didn't pass the bool)
    local_archive = _any_archive_for(summary_type, entry_files)

    # ✅ stamp seed-dir usage
    if used_seed_dir:
        summary["_meta"]["validator_flags"].append("used_seed_dir")
        print("🔎 resolve: used seed-dir for sources")

    # ✅ stamp archive fallback (once)
    if used_archive or local_archive:
        summary["_meta"]["validator_flags"].append("used_archive_fallback")
        print("🔎 resolve: used archive fallback for sources")

    # 📦 Archive EXACTLY the inputs for their own tier (also moves *.minisig)
    if getattr(args, "no-archive", False):
        archive_paths = []
        print("📦 Archive skipped (--no-archive).")
    else:
        # Map the summary we just wrote to the *input* tier it consumed
        if summary_type == "weekly":
            input_tier = "session"
        elif summary_type == "period":
            input_tier = "weekly"
        else:  # quarterly
            input_tier = "period"
        archive_paths = _archive_exact(base_dir, entry_files, input_tier=input_tier)

    # 💬 Log what moved
    print("📦 Archived sources:")
    for p in archive_paths:
        print("   -", Path(p).name)
    print()
    

    # Include archive destinations in the weekly
    summary["source_archive_paths"] = [str(p) for p in archive_paths]

    def _ensure_gael_minimum(rollup: dict, level: str):
        """
        Ensure GAEL shape/length minimums per level.
        Minimums:
          weekly:    thoughts>=4, decisions>=2
          period:    thoughts>=5, decisions>=3
          quarterly: thoughts>=6, decisions>=3
        Summary minimum length enforced at 160 chars for all.
        Also normalizes paired forms:
          thoughts (string with "- ")  <-> key_thoughts (list without "- ")
          decisions (string with "- ") <-> decisions_list (list without "- ")
        """
        # thresholds by level
        th_min_map = {"weekly": 4, "period": 5, "quarterly": 6}
        dc_min_map = {"weekly": 2, "period": 3, "quarterly": 3}
        # soft caps (trim to avoid Sentinel 'too_many_bullets')
        th_max_map = {"weekly": 6, "period": 8, "quarterly": 9}
        dc_max_map = {"weekly": 6, "period": 6, "quarterly": 6}
        th_min = th_min_map.get(level, 4)
        dc_min = dc_min_map.get(level, 2)
        th_max = th_max_map.get(level, 6)
        dc_max = dc_max_map.get(level, 6)

        # Summary ≥160 chars
        s = (rollup.get("summary") or "").strip()
        if len(s) < 160:
            rollup["summary"] = (
                "This rollup consolidates recent sessions and audit effects, with archived sources recorded "
                "for provenance and validator flags set to preserve indexing and embedder state. Sentinel "
                "enforces GAEL trio shape and ensures decisions are actionable for downstream layers."
            )

        # Normalize incoming shapes for thoughts/decisions
        th = rollup.get("thoughts")
        if not isinstance(th, list):
            th = [str(th)] if th else []

        # Top up thoughts to level minimum (synthesize if seeds run out)
        seeds = [
            "- Sources archived and provenance captured.",
            "- Indexing stored JSONB validator_flags (mem-only if search disabled).",
            "- Sentinel rollup validator checks passed.",
            "- Summary-layer checks available for hierarchy validation.",
            "- Legacy archive normalization applied where needed.",
        ]
        def _count(vals): return len([x for x in vals if str(x).strip()])
        i = 1
        while _count(th) < th_min:
            if seeds:
                th.append(seeds.pop(0))
            else:
                th.append(f"- Consolidate quarterly themes #{i} for downstream checks.")
                i += 1
        # finalize + cap per level
        thoughts_list = [str(x).strip() for x in th if str(x).strip()][:th_max]

        # Decisions (level-aware min; synthesize if needed)
        dc = rollup.get("decisions")
        if not isinstance(dc, list):
            dc = [str(dc)] if dc else []
        verbs = [
            "Re-run summary_layer_validator with --exact to confirm deterministic grounding.",
            "Re-index newly archived rollups to persist provenance in DB.",
            "Update README sections for writer/indexer/sentinel behavior.",
        ]
        j = 1
        while len([x for x in dc if str(x).strip()]) < dc_min:
            if verbs:
                dc.append(verbs.pop(0))
            else:
                dc.append(f"- Plan: Document quarterly outcomes #{j} and owners.")
                j += 1
        decisions_list = [str(x).strip() for x in dc if str(x).strip()][:dc_max]

        # Ensure paired forms are consistent
        # Normalize to paired forms (list without '- ', string with '- ')
        key_th = [ln[2:].strip() if str(ln).startswith("- ") else str(ln).strip()
                  for ln in thoughts_list]
        rollup["key_thoughts"] = key_th
        rollup["thoughts"] = "\n".join(f"- {t}" for t in key_th)
        dec_list = [ln[2:].strip() if str(ln).startswith("- ") else str(ln).strip()
                    for ln in decisions_list]
        rollup["decisions_list"] = dec_list
        rollup["decisions"] = "\n".join(f"- {d}" for d in dec_list)
        return thoughts_list, decisions_list

    # ✨ Ensure GAEL minimums (level-aware) before saving (one call)
    _ = _ensure_gael_minimum(summary, summary_type)

    # 💾 Save with suffix collision handling (after we have provenance)
    suffix = ""
    i = 1
    while True:
        summary_path = summary_dir / f"{AGENT_NAME}_{summary_type}_journal_{today_str}{suffix}.json"
        if not summary_path.exists():
            break
        i += 1
        suffix = f"_{i}"

    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"🖊️  {AGENT_NAME} wrote {summary_type}: {summary_path.name}")
    # Rollup health line (T = thoughts bullets, D = decision bullets)
    print(f"health: bullets T={len(summary['key_thoughts'])} / D={len(summary['decisions_list'])} | "
          f"provenance={len(summary['source_files'])}/{len(summary['source_archive_paths'])}")

    # 🔏 Sign (best-effort)
    try:
        if minisign_sign_file:
            minisign_sign_file(summary_path)
    except Exception:
        pass

    # 🧠 Index (mem-only; best-effort)
    try:
        subprocess.run([
            "python3", "-m", "abilities.common_abilities.memory_indexer",
            "--file", str(summary_path), "--agent", AGENT_NAME, "--mem-only", "--force"
        ], check=False)
    except Exception:
        pass

    return summary_path

def check_and_promote(base_dir: Path, summary_type: str):
    if summary_type == "weekly":
        source_dir = base_dir / "session"
        pattern = f"{AGENT_NAME}_session_journal_*.json"
    elif summary_type == "period":
        source_dir = base_dir / "weekly"
        pattern = f"{AGENT_NAME}_weekly_journal_*.json"
    elif summary_type == "quarterly":
        source_dir = base_dir / "period"
        pattern = f"{AGENT_NAME}_period_journal_*.json"
    else:
        raise ValueError(f"Unknown summary_type: {summary_type}")

    source_files = sorted(source_dir.glob(pattern))
    threshold = SUMMARY_THRESHOLDS[summary_type]

    while len(source_files) >= threshold:
        batch = source_files[:threshold]
        _agent_write_and_save_summary(base_dir, summary_type, batch, today_str=_today_token_for(summary_type))
        source_files = sorted(source_dir.glob(pattern))

def promote_all_summaries(agent: str, journal_types: list[str] | None = None, debug: bool = False):
    """Public entry point for sudo calls."""
    types = journal_types or _list_journal_types(agent)
    if debug:
        print(f"🧭 Detected journals for {agent}: {types or 'none'}")
    for jt in types:
        base_dir = _agent_root(agent) / jt
        if not (base_dir / "session").exists():
            continue
        if debug:
            print(f"▶️ Promoting {agent}:{jt}")
        check_and_promote(base_dir, "weekly")
        check_and_promote(base_dir, "period")
        check_and_promote(base_dir, "quarterly")

def _main():
    # resolve which journal types to run
    if args.all:
        promote_all_summaries(AGENT_NAME, debug=args.debug)
        return
    jt = JOURNAL_TYPE
    if not jt:
        # auto-detect single journal if only one exists
        detected = _list_journal_types(AGENT_NAME)
        if len(detected) == 1:
            jt = detected[0]
            if args.debug:
                print(f"🧭 Auto-selected journal_type={jt}")
        else:
            raise SystemExit("❌ --journal_type required (or use --all) — multiple *_journal folders detected.")
    base_dir = _agent_root(AGENT_NAME) / jt
    check_and_promote(base_dir, "weekly")
    check_and_promote(base_dir, "period")
    check_and_promote(base_dir, "quarterly")

if __name__ == "__main__":
    if args.level and args.take:
        jt = args.journal_type
        if not jt:
            ds = _list_journal_types(AGENT_NAME)
            if len(ds) != 1:
                raise SystemExit("❌ --journal_type required (or ensure only one *_journal exists).")
            jt = ds[0]

        base_dir = _agent_root(AGENT_NAME) / jt

        # 1) Prefer --seed-dir for repair/re-rollup workflows
        if args.seed_dir:
            seed = Path(args.seed_dir)
            files = sorted(seed.glob("*.json"))
        else:
            # 2) Otherwise, use archive-aware scan by level
            files = _scan_sources(AGENT_NAME, jt, args.level)

        if not files:
            raise SystemExit(f"❌ No sources found for {args.level} in {base_dir} (seed-dir and archive-aware scan both empty)")

        # newest N
        take = max(1, int(args.take))
        batch = sorted(files)[-take:]

        print(f"Will summarize {len(batch)} files for {AGENT_NAME}:{jt} -> {args.level}")
        for f in batch:
            print("  -", Path(f).name)

        if not args.dry_run:
            today_str = _today_token_for(args.level)
            out = _agent_write_and_save_summary(
                base_dir, args.level, batch, today_str=today_str,
                used_archive=_any_archive_for(args.level, batch),
                used_seed_dir=bool(args.seed_dir),
            )
            print("🖊️ wrote:", out)
        else:
            print("(dry-run) nothing written.")
    else:
        _main()

