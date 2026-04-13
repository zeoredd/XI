# 🔎 memory_fuzzy_scan.py
# Search all memory JSON files for fuzzy matches of a keyword or phrase

import os
import json
import difflib
import argparse
from pathlib import Path

# 📁 Defaults
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # Points to ~/XI
MEMORY_BASE = PROJECT_ROOT / "memory"
DEFAULT_THRESHOLD = 0.6
INCREMENTS = [round(i * 0.1, 1) for i in range(2, 11)]  # 0.2 to 1.0

THREAD_SUBFOLDERS = [
    "daily_briefing_and_strategy",
    "idea_generator",
    "personal_and_group_conversation"
]

# 🔍 Scan memory folder
def scan_memory(keyword, min_score=DEFAULT_THRESHOLD, root_folder=MEMORY_BASE, debug=False, exclude_archive=False):
    matches = []
    total_files = 0
    total_entries = 0

    for dirpath, _, filenames in os.walk(root_folder):
        path_parts = Path(dirpath).parts
        if exclude_archive and "archive" in path_parts:
            continue

        if debug:
            print(f"📂 Checking folder: {dirpath}")

        for filename in filenames:
            if filename.endswith(".json"):
                full_path = os.path.join(dirpath, filename)
                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        content = json.load(f)
                        entries = content.get("summaries") or content.get("entries") or []
                        if isinstance(entries, dict):
                            entries = [entries]

                        total_files += 1
                        total_entries += len(entries)

                        for entry in entries:
                            text_blob = json.dumps(entry)
                            score = difflib.SequenceMatcher(None, text_blob.lower(), keyword.lower()).ratio()

                            # 🧠 Loose override for direct substring match
                            if keyword.lower() in text_blob.lower():
                                score = max(score, 1.0)

                            if score >= min_score:
                                matches.append({
                                    "file": full_path,
                                    "score": round(score, 3),
                                    "preview": text_blob[:500]
                                })
                except Exception as e:
                    if debug:
                        print(f"⚠️ Error reading {full_path}: {e}")

    matches.sort(key=lambda m: m["score"], reverse=True)

    if debug:
        print("\n🛠 DEBUG INFO")
        print(f"📄 Files scanned: {total_files}")
        print(f"🧠 Total entries checked: {total_entries}")
        print(f"✅ Matches found: {len(matches)}\n")

    return matches

# 📊 Sliding threshold scan
def sliding_scan(keyword, root_folder, debug=False, exclude_archive=False):
    print("\n📊 Sliding scale hit summary:")
    for t in INCREMENTS:
        results = scan_memory(keyword, min_score=t, root_folder=root_folder, debug=False, exclude_archive=exclude_archive)
        print(f"• Threshold {t:.1f} → {len(results)} matches")

# 🧪 Command line usage
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fuzzy search memory JSON files.")
    parser.add_argument("keyword", help="Keyword or phrase to search for")
    parser.add_argument("--root", type=str, default=str(MEMORY_BASE), help="Root folder to search from")
    parser.add_argument("--limit", type=int, default=10, help="Limit number of results shown")
    parser.add_argument("--threshold", type=float, default=None, help="Fuzzy match threshold (0 to 1)")
    parser.add_argument("--sliding", action="store_true", help="Show match counts for thresholds 0.2 to 1.0")
    parser.add_argument("--exclude-archive", action="store_true", help="Skip folders containing /archive")
    parser.add_argument("--debug", action="store_true", help="Show debug info")

    args = parser.parse_args()
    root_folder = Path(args.root).resolve()

    if args.sliding:
        sliding_scan(
            args.keyword,
            root_folder=root_folder,
            debug=args.debug,
            exclude_archive=args.exclude_archive
        )
    else:
        threshold = args.threshold if args.threshold is not None else DEFAULT_THRESHOLD
        results = scan_memory(
            args.keyword,
            min_score=threshold,
            root_folder=root_folder,
            debug=args.debug,
            exclude_archive=args.exclude_archive
        )

        output_lines = []
        for match in results[:args.limit]:
            entry = (
                f"📁 File: {match['file']}\n"
                f"🔢 Score: {match['score']}\n"
                f"📝 Preview: {match['preview']}\n"
                + "-" * 40 + "\n"
            )
            print(entry)
            output_lines.append(entry)

