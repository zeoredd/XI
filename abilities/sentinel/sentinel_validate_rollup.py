#!/usr/bin/env python3
import argparse, json, os, re, sys
from datetime import datetime

BANNED = ["Summary unavailable", "TBD", "lorem ipsum"]  # normalized

import os, re
from datetime import datetime, timezone, timedelta

# Anti-fluff & action verbs
FLUFF_RX = re.compile(r'^\s*[-*•]\s*(i think|well[, ]|maybe|perhaps)\b', re.I)
# Expanded, engineering-friendly decision verbs (product + eng + ops + ML)
# Keep this list in sync with the writer's APPROVED_VERBS.
VERB_RX = re.compile(
    r'^\s*[-*•]\s*('
    r'add|enable|remove|create|ship|fix|patch|write|refactor|test|validate|index|archive|promote|'
    r'deploy|rollback|migrate|upgrade|schedule|document|plan|design|'
    r'compute|calculate|process|analyze|measure|benchmark|monitor|observe|detect|profile|optimize|tune|'
    r'train|retrain|evaluate|compare|calibrate|'
    r'audit|verify|sign'
    r')\b',
    re.I
)

def _infer_level_from_path(path: str) -> str:
    # naive but effective: .../weekly/..., /period/, /quarterly/
    path_l = path.lower()
    if "/quarterly/" in path_l: return "quarterly"
    if "/period/" in path_l:    return "period"
    if "/weekly/" in path_l:    return "weekly"
    # fallback to filename tokens
    if re.search(r'_quarterly_journal_\d{4}q[1-4]\.json$', path_l): return "quarterly"
    if re.search(r'_period_journal_\d{4}p\d{2}\.json$', path_l):    return "period"
    if re.search(r'_weekly_journal_\d{8}\.json$', path_l):          return "weekly"
    return "weekly"

def _level_thresholds(level: str):
    # Summary min chars; thoughts/decisions bullet ranges
    if level == "quarterly":
        return dict(min_summary=300, th_min=6, th_max=9, dc_min=3, dc_max=7)
    if level == "period":
        return dict(min_summary=220, th_min=5, th_max=9, dc_min=3, dc_max=7)
    return dict(min_summary=160, th_min=4, th_max=8, dc_min=2, dc_max=6)  # weekly

def _as_text(x):
    """Return a string representation suitable for emptiness checks."""
    if isinstance(x, str):
        return x
    if isinstance(x, list):
        return "\n".join(str(i) for i in x if str(i).strip())
    return "" if x is None else str(x)


def _as_list(x):
    """Return a list of bullet-like items.
    - If x is a list, coerce each to str and drop empties.
    - If x is a string, use lines starting with '-' or '*' as bullets; else single-item if non-empty.
    """
    if isinstance(x, list):
        return [str(i).strip() for i in x if str(i).strip()]
    if isinstance(x, str):
        lines = [ln.strip() for ln in x.splitlines() if ln.strip()]
        bullets = [ln for ln in lines if ln.startswith(("-", "*"))]
        if bullets:
            return bullets
        return [x.strip()] if x.strip() else []
    return []

def _read(fp): 
    with open(fp, "r", encoding="utf-8") as f: 
        return json.load(f)

def _write(fp, obj):
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def _bullet_lines(s: str):
    return [ln for ln in (s or "").splitlines() if ln.strip()]

def validate(d: dict, rollup_path: str):
    """
    Validate a weekly/period/quarterly rollup. Robust to GAEL trio being either strings or lists.
    Uses level-aware thresholds from _level_thresholds().
    """
    probs: list[str] = []

    # --- Normalize GAEL trio ---
    # _as_text: string for emptiness/length checks
    # _as_list: list of bullet-like items for counts
    summary_text = _as_text(d.get("summary")).strip()
    thoughts_list = _as_list(d.get("thoughts"))      # list[str]
    decisions_list = _as_list(d.get("decisions"))    # list[str]

    # --- Missing fields ---
    if not summary_text:
        probs.append("missing:summary")
    if not thoughts_list:
        probs.append("missing:thoughts")
    if not decisions_list:
        probs.append("missing:decisions")

    # --- Level-aware thresholds ---
    level = _infer_level_from_path(rollup_path)
    thres = _level_thresholds(level)  # expects keys: min_summary, th_min, th_max, dc_min, dc_max

    # --- Summary length (if present) ---
    if summary_text and len(summary_text) < thres["min_summary"]:
        probs.append(f"summary:too_short(<{thres['min_summary']} chars)")

    # --- Bullet counts (use normalized lists directly) ---
    th_count = len(thoughts_list)
    dc_count = len(decisions_list)

    if th_count < thres["th_min"]:
        probs.append(f"thoughts:too_few_bullets(<{thres['th_min']})")
    if dc_count < thres["dc_min"]:
        probs.append(f"decisions:too_few_bullets(<{thres['dc_min']})")

    # (Soft) caps: flag if overly long; do not fail hard
    if th_count > thres["th_max"]:
        probs.append(f"thoughts:too_many_bullets(>{thres['th_max']})")
    if dc_count > thres["dc_max"]:
        probs.append(f"decisions:too_many_bullets(>{thres['dc_max']})")

    return probs

    # banned boilerplate
    blob = "\n".join([summary, thoughts, decisions])
    for bad in BANNED:
        if bad in blob:
            probs.append(f"banned_phrase:{bad}")

    # Decisions style: must be bullets; start with action verb; no fluff
    for i, ln in enumerate(dc_lines[:7], 1):
        if not ln.strip().startswith(("-", "*", "•")):
            probs.append(f"decision_not_bullet:line{i}")
        if FLUFF_RX.search(ln):
            probs.append(f"decision_fluffy:line{i}")
        if not VERB_RX.search(ln):
            probs.append(f"decision_weak_verb:line{i}")

    # provenance check (archived sources exist)
    arc = d.get("source_archive_paths") or []
    if not arc:
        probs.append("provenance:archive_paths_missing")
    else:
        for p in arc:
            if not os.path.exists(p):
                probs.append(f"provenance:missing_file:{p}")

    return probs

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--fix", action="store_true")
    args = ap.parse_args()

    d = _read(args.file)
    problems = validate(d, args.file)

    # Result status: True if no problems were found
    ok = (len(problems) == 0)

    audit = {
        "rollup_file": args.file,
        "ok": ok,
        "problems": problems,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "strict": args.strict,
        "fix_attempted": False,
    }

    # Echo writer-stamped flags into the audit
    flags = (d.get("_meta") or {}).get("validator_flags") or []
    if flags:
        audit["meta"] = {"validator_flags": flags}

    if problems and args.fix:
        audit["fix_attempted"] = True
        d["verified"] = False
        d.setdefault("_sentinel", {})["repair_suggestion"] = {
            "summary":   "Write a denser, 4–10 sentence summary with concrete outcomes and numbers.",
            "thoughts":  "Provide 5–9 bullets highlighting themes, risks, changes vs. previous tier.",
            "decisions": "Provide 3–7 bullets; each starts with an action verb and is testable with an owner.",
        }
        _write(args.file, d)

    # === NEW: write audits under Sentinel's tree ===
    import pathlib
    def _level_from_path(p: str) -> str:
        pl = p.lower()
        if "/quarterly/" in pl: return "quarterly"
        if "/period/" in pl:    return "period"
        if "/weekly/" in pl:    return "weekly"
        return "misc"

    level = _level_from_path(args.file)
    subdir = {
        "weekly": "weekly_checks",
        "period": "period_checks",
        "quarterly": "quarterly_checks",
        "misc": "checks",
    }[level]

    audit_root = pathlib.Path("memory/sentinel/sentinel_audit_journal")
    out_dir = audit_root / subdir
    out_dir.mkdir(parents=True, exist_ok=True)

    base = pathlib.Path(args.file).stem
    if base.endswith("_sentinel_audit"):
        base = base[: -len("_sentinel_audit")]
    audit_fp = out_dir / f"{base}_sentinel_audit.json"
    _write(str(audit_fp), audit)

    if problems:
        msg = f"❌ sentinel: {len(problems)} issues. See {audit_fp}"
        if args.strict:
            print(msg); sys.exit(2)
        else:
            print(msg); sys.exit(1)
    else:
        print(f"✅ sentinel: ok=True issues=0 audit={audit_fp}")
        sys.exit(0)

if __name__ == "__main__":
    main()

