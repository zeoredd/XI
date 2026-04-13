# ===============================
# file: README_diver.md
# ===============================
# XI Diver — quick start


## Setup
```bash
python xi/scripts/bootstrap_memdb.py
```
This creates `xi/mem.db` with sample memories, links, and an FTS index.


## Run Base Diver
```bash
python xi/diver/diver_controller.py "search engine" --db xi/mem.db
```


Optional filters:
```bash
python xi/diver/diver_controller.py "search" --db xi/mem.db --layer weekly --agent xi --topk 10
```


Notes:
- Vector search is stubbed; FTS5 BM25 is used by default.
- `--mode cascade` is reserved for the upcoming Cascade Diver.
