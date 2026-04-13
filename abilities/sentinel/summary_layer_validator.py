#!/usr/bin/env python3
"""
🧠 summary_layer_validator.py (MVP clean)
Validates weekly→session, period→weekly, quarterly→period using compiled UUIDs.
Writes a single report JSON under sentinel audit logs.
"""

from pathlib import Path
from datetime import datetime, timezone
import json, argparse
import sys
import difflib
sys.path.append(str(Path(__file__).resolve().parents[2]))

from abilities.sentinel.resolve_uuid_to_source import resolve_uuids_to_files

AGENTS = ["davinci", "hermes", "sentinel"]
JOURNAL_TYPES = [
    "idea_generator_journal",
    "daily_briefing_and_strategy_journal",
    "personal_and_group_conversation_journal",
]
JOURNAL_ROOT = Path("memory")
AUDIT_LOG_DIR = Path("memory/sentinel/sentinel_audit_journal/summary_layer_checks")
AUDIT_LOG_DIR.mkdir(parents=True, exist_ok=True)
SUMMARY_FIELDS = ["summary", "thoughts", "decisions"]

def extract_text(entry):
    return " ".join(str(entry.get(f, "")) for f in SUMMARY_FIELDS)

def validate_consistency(lower_entries, upper_entry):
    upper_text = extract_text(upper_entry).lower()
    issues = []
    for lower in lower_entries:
        lower_text = extract_text(lower).lower()
        # naive keyword coverage heuristic
        missing = []
        for word in set(lower_text.split()):
            if len(word) > 5 and word not in upper_text:
                missing.append(word)
        if missing:
            issues.append({
                "lower_file": lower.get("_file_path", "unknown"),
                "missing_keywords": missing[:10],
            })
    return issues

def load_json(path: Path):
    try:
        with open(path) as f:
            data = json.load(f)
            data["_file_path"] = str(path)
            return data
    except Exception:
        return None

def main():
    # CLI (add --exact for stricter thresholds)
    ap = argparse.ArgumentParser(description="Validate summary layer consistency across journal tiers.")
    ap.add_argument(
        "--exact",
        action="store_true",
        help="Use stricter, deterministic thresholds (higher similarity; no fuzzy tolerance)."
    )
    args = ap.parse_args()

    results = []
    for agent in AGENTS:
        for journal_type in JOURNAL_TYPES:
            base = JOURNAL_ROOT / agent / journal_type

            for level, lower_folder in [("weekly","session"), ("period","weekly"), ("quarterly","period")]:
                summary_dir = base / level
                if not summary_dir.exists():
                    continue

                for summary_file in sorted(summary_dir.glob("*.json")):
                    summary_entry = load_json(summary_file)
                    if not summary_entry:
                        continue

                    uuids = summary_entry.get("compiled_uuids", [])
                    source_files = resolve_uuids_to_files(agent, uuids)

                    lower_entries = []
                    for sf in source_files:
                        e = load_json(sf)
                        if e:
                            lower_entries.append(e)

                    # 1) summary-layer consistency (existing)
                    issues = validate_consistency(lower_entries, summary_entry)

                    # 2) decisions-vs-sources grounding (new, light heuristic)
                    def _norm(s):
                        return " ".join(str(s or "").split()).lower()

                    def _best_sim(line, corpus):
                        return max(
                            (difflib.SequenceMatcher(None, _norm(line), _norm(c)).ratio() for c in corpus),
                            default=0.0
                        )

                    decisions_blob = (summary_entry.get("decisions") or summary_entry.get("Decisions") or "")
                    dec_lines = [ln.strip() for ln in str(decisions_blob).splitlines() if ln.strip()]

                    corpus = []
                    for e in lower_entries:
                        for k in ("summary","thoughts","key_thoughts","decisions","decisions_list",
                                  "gael_block","notes","body","text","content"):
                            v = e.get(k)
                            if isinstance(v, list):
                                corpus.extend([str(x) for x in v])
                            elif isinstance(v, str):
                                corpus.extend(v.splitlines())

                    weak_flags = []
                    for i, ln in enumerate(dec_lines, 1):
                        if not ln:
                            continue
                        if _best_sim(ln, corpus) < 0.55:
                            weak_flags.append({"line": i, "decision": ln})

                    # 3) reverse coverage: do key source claims appear in rollup?
                    import re, difflib

                    def _norm(s):
                        return " ".join(str(s or "").split()).lower()

                    # Build rollup text corpus (Summary + Thoughts + Decisions)
                    rollup_lines = []
                    for k in ("summary","thoughts","decisions"):
                        v = summary_entry.get(k) or ""
                        if isinstance(v, list):
                            rollup_lines.extend([str(x) for x in v])
                        else:
                            rollup_lines.extend(str(v).splitlines())
                    rollup_corpus = [_norm(x) for x in rollup_lines if _norm(x)]

                    # Extract candidate claims from lower entries (scope to informative fields)
                    source_lines = []
                    for e in lower_entries:
                        for k in ("summary","thoughts","key_thoughts","decisions","decisions_list"):
                            v = e.get(k)
                            if isinstance(v, list):
                                source_lines.extend([str(x) for x in v])
                            elif isinstance(v, str):
                                source_lines.extend(v.splitlines())

                    # sentence split + light filters (keep it compact & salient)
                    sent_split = []
                    for ln in source_lines:
                        # split on sentence boundaries and bullets
                        parts = re.split(r'(?<=\.)\s+|[\n•]+|^\s*-\s*', ln)
                        for p in parts:
                            s = p.strip()
                            if 40 <= len(s) <= 240 and re.search(r'\b(\w+ed|\bwill\b|\bis\b|\bare\b|\bto\b)', s, re.I):
                                sent_split.append(s)

                    # De-dup and cap to top 50 by length (proxy for salience)
                    seen = set()
                    claims = []
                    for s in sent_split:
                        n = _norm(s)
                        if n and n not in seen:
                            seen.add(n)
                            claims.append(s)
                    claims = sorted(claims, key=len, reverse=True)[:50]

                    def _best_sim(a, corpus):
                        an = _norm(a)
                        return max((difflib.SequenceMatcher(None, an, c).ratio() for c in corpus), default=0.0)

                    # thresholds (default vs --exact)
                    MIN_SIM = 0.55
                    TARGET  = 0.60
                    if args.exact:
                        # deterministic, stricter mode
                        MIN_SIM = 0.80
                        TARGET  = 0.80
                    # NOTE: rest of logic unchanged; only thresholds vary

                    matched = 0
                    uncovered = []
                    for c in claims:
                        if _best_sim(c, rollup_corpus) >= MIN_SIM:
                            matched += 1
                        else:
                            if len(uncovered) < 6:  # keep report small
                                uncovered.append(c)

                    coverage = (matched / max(1, len(claims)))

                    reverse_cov = {
                        "total_claims": len(claims),
                        "matched": matched,
                        "coverage": round(coverage, 3),
                        "min_sim": MIN_SIM,
                        "target": TARGET,
                        "uncovered_examples": uncovered,
                    }

                    if issues or weak_flags or (coverage < TARGET and len(claims) >= 5):
                        results.append({
                            "agent": agent,
                            "journal_type": journal_type,
                            "level": f"{level} vs {lower_folder}",
                            "upper_file": summary_file.name,
                            "issues": issues,
                            "decisions_vs_sources_weak": weak_flags,
                            "reverse_coverage": reverse_cov,
                        })
                    else:
                        results.append({
                            "agent": agent,
                            "journal_type": journal_type,
                            "level": f"{level} vs {lower_folder}",
                            "upper_file": summary_file.name,
                            "issues": issues,
                            "decisions_vs_sources_weak": weak_flags,
                            "reverse_coverage": reverse_cov,
                            "ok": True,
                        })

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_file = AUDIT_LOG_DIR / f"summary_hierarchy_validation_{ts}.json"
    with open(report_file, "w") as f:
        json.dump({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "findings": results,
            "written_by": "sentinel",
        }, f, indent=2)

    print(f"✅ Summary-layer validation written to {report_file}")

if __name__ == "__main__":
    main()

