#memory_selection_utils.py
from pathlib import Path
from typing import List
import json

def present_memory_choices(agent_name: str, hits: list) -> str:
    """
    Render a compact, provenance-aware preview list.
    Supports both dict hits (with 'id','content','agent','file','score') and raw strings.
    """
    lines = []
    for idx, hit in enumerate(hits, 1):
        if isinstance(hit, dict):
            title = (hit.get("content","") or "").strip().split("\n")[0][:150]
            hid = hit.get("id") or "?"
            hagent = hit.get("agent") or agent_name
            hfile = hit.get("file") or hit.get("source_file") or "?"
            score = hit.get("score")
            score_s = f" score={float(score):.2f}" if isinstance(score, (int,float)) else ""
            lines.append(f"{idx}. [{hid}] {title}  ({hagent} :: {hfile}{score_s})")
        else:
            # raw string
            title = str(hit).strip().split("\n")[0][:150]
            lines.append(f"{idx}. {title}")
    return "\n".join(lines)

def extract_selected_memory(selected_index: int, hits: list) -> str:
    if selected_index < 1 or selected_index > len(hits):
        return None
    return hits[selected_index - 1]["content"].strip()

def log_selected_memory_to_flash(agent_name: str, memory_chunk: str, memory_id: str = None):
    """flash cache disabled — no-op stub kept for compatibility."""
    return None

# 📊 Decide whether vector search should run at all
def should_vector_search(agent_name: str, user_input: str) -> bool:
    """
    Simple heuristic to determine if vector memory search is necessary.

    You can expand this logic later based on keywords, question type, etc.
    """
    if not user_input.strip():
        return False

    if len(user_input) < 20:
        return False  # too short, likely not worth searching

    # Could add keyword logic here
    trigger_keywords = ["remember", "last time", "what did you say", "previous", "remind me"]
    if any(kw in user_input.lower() for kw in trigger_keywords):
        return True

    # By default, allow search
    return True

# 🧠 Agent Memory Selector (with scoring + logging)
def agent_decides_memory_selection(agent_name: str, hits: List[str]) -> int:
    """
    Agent chooses the most relevant memory chunk based on scoring.
    Returns index (1-based) of selected item.
    Logs considered and selected memories.
    """
    from abilities.common_abilities.memory_selection_utils import extract_text_from_structured_memory

    best_index = -1
    best_score = -1
    memory_scores = []

    for i, raw in enumerate(hits):
        # structured dict or raw text
        text = extract_text_from_structured_memory(raw) if isinstance(raw, dict) else str(raw)
        hid = (raw.get("id") if isinstance(raw, dict) else None) or f"idx:{i+1}"
        score = 0

        # 📏 Simple heuristics for now
        if "summary" in text.lower():
            score += 1
        if "thought" in text.lower():
            score += 1
        if "decision" in text.lower():
            score += 1
        if len(text.split()) > 30:
            score += 1
        if "indexing" in text.lower() or "daemon" in text.lower():
            score += 1  # Topical boost (example)

        memory_scores.append((i, score, text))

    # Sort by score descending
    memory_scores.sort(key=lambda x: x[1], reverse=True)

    # flash logging removed

    if memory_scores:
        best_index, best_score, best_text = memory_scores[0]
        # flash logging removed

        return best_index + 1  # 1-based for consistency

    return 1  # fallback default

# 🧼 Validate selected memory chunk before injection
def is_valid_memory_chunk(text) -> bool:
    """
    Filters out blank/tiny entries. Supports dict or string.
    JSON is allowed; we normalize it first if needed.
    """
    if text is None:
        return False

    if isinstance(text, dict):
        text = extract_text_from_structured_memory(text)

    s = str(text).strip()
    return len(s) > 10

# 🧠 Step 1: Clean wrapper for raw string memory
def parse_and_extract_text(raw_chunk) -> str:
    """
    Accepts dict or string. If string looks like JSON, try to parse;
    otherwise treat as plain text without logging warnings.
    """
    if raw_chunk is None:
        return ""
    if isinstance(raw_chunk, dict):
        return extract_text_from_structured_memory(raw_chunk)

    s = str(raw_chunk).strip()
    # Only try JSON if it "looks" like JSON; otherwise just return text.
    if s[:1] in ("{", "["):
        try:
            data = json.loads(s)
            return extract_text_from_structured_memory(data)
        except Exception:
            # Silent fallback to raw text
            return s
    return s

# 🧠 Step 2: Existing structured extractor
def extract_text_from_structured_memory(memory_entry: dict) -> str:
    """
    Converts a structured memory dict into a readable text block.
    Returns a string with summary, thoughts, decisions (if available).
    """
    if not isinstance(memory_entry, dict):
        return str(memory_entry)

    fields = []
    for key in ("summary", "thoughts", "decisions"):
        val = memory_entry.get(key)
        if val:
            fields.append(f"{key.capitalize()}: {val.strip()}")

    return "\n\n".join(fields) if fields else str(memory_entry)

# test_is_valid_memory_chunk.py
from abilities.common_abilities.memory_selection_utils import is_valid_memory_chunk

def run_tests():
    test_cases = {
        "✅ Normal text": "This is a valid memory chunk with enough content.",
        "❌ Blank string": "",
        "❌ Only spaces": "     ",
        "❌ Short text": "Too short",
        "❌ JSON-like artifact": "{'foo': 'bar'}",
        "✅ Starts with bracket later": "Note: this is okay {but not too early}",
        "❌ NoneType": None,
        "✅ Multiline valid": "Line 1\nLine 2\nLine 3"
    }

    for label, case in test_cases.items():
        result = is_valid_memory_chunk(case)
        print(f"{label.ljust(25)} => {'✅ PASS' if result else '❌ FAIL'}")

if __name__ == "__main__":
    run_tests()





