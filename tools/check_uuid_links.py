#!/usr/bin/env python3
import json, os, glob, re, sys
ROOT="memory"
missing=0
def iter_json_files():
    for p in glob.glob(f"{ROOT}/**/*.json", recursive=True):
        yield p
def load(path):
    try:
        with open(path,"r") as f: return json.load(f)
    except Exception: return None
def file_exists_for_link(link):
    # link may be a filename or embedded uuid; accept direct files only here
    return os.path.exists(link) or os.path.exists(os.path.join(ROOT, link))
for path in iter_json_files():
    data=load(path)
    if not isinstance(data, dict): continue
    links = []
    if "_meta_fields" in data and isinstance(data["_meta_fields"], dict):
        links += data["_meta_fields"].get("source_files", []) or []
    if "thread_refs" in data:
        links += data.get("thread_refs", [])
    for ref in links:
        if not file_exists_for_link(ref):
            print(f"[MISSING] {path} -> {ref}")
            missing+=1
print(f"Done. Missing links: {missing}")
sys.exit(1 if missing else 0)

