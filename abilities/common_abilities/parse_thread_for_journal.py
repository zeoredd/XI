# 📄 abilities/common_abilities/parse_thread_for_journal.py
from __future__ import annotations
import json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

RE_SUMMARY   = re.compile(r'^\[Summary\]:\s*(?:\[[^\]]+\]:\s*)?(.*)$', re.MULTILINE)
RE_THOUGHTS  = re.compile(r'^\[Thoughts\]:\s*(?:\[[^\]]+\]:\s*)?(.*)$', re.MULTILINE)
RE_DECISIONS = re.compile(r'^\[Decisions\]:\s*(?:\[[^\]]+\]:\s*)?(.*)$', re.MULTILINE)
# Explicit tagged fences like ```SUMMARY ... ```
FENCE_RE     = re.compile(r"```(?:\s*)(SUMMARY|THOUGHTS|DECISIONS)\s*\n(.*?)\n```",
                          re.IGNORECASE | re.DOTALL)
# NEW: multi-line bracket blocks, optionally fenced on following lines.
# Matches:
# [Summary]: ```markdown\n...body...\n```   OR   [Summary]:\nbody (until next tag)
BRACKET_BLOCK_RE = re.compile(
    r'^\[(Summary|Thoughts|Decisions)\]:\s*(?:```[^\n]*\n)?'  # optional opening fence with language
    r'(.*?)'                                                  # body (non-greedy)
    r'(?:\n```)?'                                             # optional closing fence
    r'(?=\n\[(?:Summary|Thoughts|Decisions)\]:|\Z)',          # stop at next tag or end
    re.IGNORECASE | re.DOTALL | re.MULTILINE
)

def _pick_last(regex: re.Pattern, text: str) -> str:
    hits = [m.strip() for m in regex.findall(text) if m and m.strip()]
    if not hits: return ""
    out = hits[-1]
    if out.startswith("[Thought]:"):  # strip stray prefix
        out = out[len("[Thought]:"):].lstrip()
    return out.replace("The final answer is:", "").strip()

def _unwrap_fence(text: str) -> str:
    """If text is a full code-fenced block, unwrap it."""
    if not isinstance(text, str): return ""
    m = re.match(r"^```[^\n]*\n(.*?)\n```$", text.strip(), flags=re.DOTALL)
    return (m.group(1).strip() if m else text.strip())

def _is_just_fence_marker(text: str) -> bool:
    if not isinstance(text, str): return False
    t = text.strip()
    if t == "```": return True
    # e.g., ```markdown  (no newline/body)
    return bool(re.fullmatch(r"```[a-zA-Z0-9_\-]*", t))

def _sanitize_block(text: str) -> str:
    if not text: return ""
    text = _unwrap_fence(text)
    if _is_just_fence_marker(text):
        return ""
    return text.strip()

def _dedupe_lines_block(text: str) -> str:
    if not text: return ""
    seen, out = set(), []
    for ln in (ln.strip() for ln in text.splitlines()):
        if not ln: continue
        key = ln.lower().strip("-• ").rstrip(".")
        if key in seen: continue
        seen.add(key)
        out.append(ln)
    return "\n".join(out).strip()

def _read_thread_json(thread_path: Path) -> dict:
    try:
        with open(thread_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Failed to read thread {thread_path}: {e}")
        return {}

def _gather_agent_outputs(entries: list[dict], agent: str) -> str:
    pieces = []
    for entry in entries:
        if entry.get("agent") == agent:
            out = entry.get("output") or ""
            if isinstance(out, str):
                pieces.append(out)
    return "\n".join(pieces)

def _pick_fenced(blob: str, tag: str) -> str:
    """Return first fenced block body for tag (SUMMARY/THOUGHTS/DECISIONS), else ''."""
    if not isinstance(blob, str) or not blob:
        return ""
    tag = (tag or "").upper()
    blocks = list(FENCE_RE.finditer(blob))
    for m in blocks:
        if (m.group(1) or "").upper() == tag:
            return (m.group(2) or "").strip()
    return ""

def _pick_bracket_block(blob: str, tag: str) -> str:
    """Return the last bracket-labeled block for tag, multi-line & optionally fenced."""
    if not isinstance(blob, str) or not blob:
        return ""
    tag = (tag or "").upper()
    out = ""
    for m in BRACKET_BLOCK_RE.finditer(blob):
        if (m.group(1) or "").upper() == tag:
            out = (m.group(2) or "").strip()
    return out


def extract_gael_journal_fields(thread_path: str | Path, agent: str) -> dict:
    thread_path = Path(thread_path)
    if not thread_path.is_absolute(): thread_path = ROOT / thread_path
    data = _read_thread_json(thread_path)
    blob = _gather_agent_outputs(data.get("thread", []), agent)

    # Prefer explicit tag-fences, then robust bracket blocks, then single-line fallback
    summary   = _pick_fenced(blob, "SUMMARY")   or _pick_bracket_block(blob, "SUMMARY")   or _pick_last(RE_SUMMARY,   blob)
    thoughts  = _pick_fenced(blob, "THOUGHTS")  or _pick_bracket_block(blob, "THOUGHTS")  or _pick_last(RE_THOUGHTS,  blob)
    decisions = _pick_fenced(blob, "DECISIONS") or _pick_bracket_block(blob, "DECISIONS") or _pick_last(RE_DECISIONS, blob)

    # Sanitize (unwrap fences, drop lone fence markers)
    summary   = _sanitize_block(summary)
    thoughts  = _sanitize_block(thoughts)
    decisions = _sanitize_block(decisions)
    decisions = _dedupe_lines_block(decisions)
    return {"summary": summary, "thoughts": thoughts, "decisions": decisions}

def parse_thread_for_journal(agent: str, flow_context: dict) -> dict:
    p = flow_context.get("thread_path")
    if p:
        tp = Path(p) if Path(p).is_absolute() else ROOT / p
        out = extract_gael_journal_fields(tp, agent)
    else:
        refs = flow_context.get("thread_refs", [])
        if not refs:
            return {"summary": "", "thoughts": "", "decisions": ""}
        out = extract_gael_journal_fields(ROOT / refs[-1], agent)

    # 🛡️ Sanity fallback: ensure no None values and trim whitespace
    for k, v in out.items():
        if not isinstance(v, str) or not v.strip():
            out[k] = ""
        else:
            out[k] = v.strip()

    return out


