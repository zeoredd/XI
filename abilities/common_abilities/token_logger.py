import sys, json
from pathlib import Path

TOKEN_COUNTER_PATH = Path("/home/ghost/XI/runtime/token_counter.json")

def log_retrieval_usage(engine, tokens, mode=None, provenance=None):
    entry = {
        "engine": engine,
        "mode": mode,
        "tokens": tokens,
        "provenance": provenance or []
    }
    try:
        if TOKEN_COUNTER_PATH.exists():
            data = json.loads(TOKEN_COUNTER_PATH.read_text())
        else:
            data = {}
        data.setdefault("retrieval", []).append(entry)
        TOKEN_COUNTER_PATH.write_text(json.dumps(data, indent=2))
    except Exception as e:
        sys.stderr.write(f"⚠️ retrieval log write failed: {e}\n")
    # Always stderr-print
    line = f"[retrieval] {engine}{'('+mode+')' if mode else ''} injected {tokens} tokens"
    sys.stderr.write(line + "\n")
    sys.stderr.flush()

