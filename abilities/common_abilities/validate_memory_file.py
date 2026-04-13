#!/usr/bin/env python3
import json
import sys
from datetime import datetime
from pathlib import Path

def validate_entry(entry, i=0):
    errors = []
    required = ["uuid", "summary", "timestamp"]
    for field in required:
        if field not in entry:
            errors.append(f"Entry {i}: Missing '{field}'")
    if "timestamp" in entry:
        try:
            datetime.fromisoformat(entry["timestamp"])
        except Exception:
            errors.append(f"Entry {i}: Invalid timestamp format")
    return errors

def validate_file(path):
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except Exception as e:
        return [f"JSON error: {e}"]

    results = []
    if isinstance(data, dict) and "entries" in data:
        for i, entry in enumerate(data["entries"]):
            results += validate_entry(entry, i)
    elif isinstance(data, list):
        for i, entry in enumerate(data):
            results += validate_entry(entry, i)
    elif isinstance(data, dict):
        results += validate_entry(data)
    else:
        results += ["Unknown JSON structure (expected list, dict with 'entries', or flat dict entry)"]

    return results

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python validate_memory_file.py path/to/file.json")
        sys.exit(1)

    file_path = Path(sys.argv[1])
    if not file_path.exists():
        print("File not found.")
        sys.exit(1)

    issues = validate_file(file_path)
    if not issues:
        print("✅ No issues found.")
    else:
        print("❌ Issues found:")
        for issue in issues:
            print(" -", issue)

