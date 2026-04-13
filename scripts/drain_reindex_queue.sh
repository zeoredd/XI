#!/usr/bin/env bash
set -euo pipefail

Q="runtime/reindex_queue.txt"

# nothing to do if queue doesn't exist
[[ -f "$Q" ]] || exit 0

# move queue to a temp file so new work can be queued while we drain
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
mv "$Q" "$TMP" || true

# drain
while IFS= read -r f; do
  [[ -n "$f" ]] || continue
  agent="$(echo "$f" | awk -F/ '{print $2}')"
  echo "🧭 Reindexing $f (agent=$agent)"
  python3 abilities/common_abilities/memory_indexer.py --file "$f" --agent "$agent" || true
done < "$TMP"

