#!/usr/bin/env python3
"""
🧹 context_prepper.py
Minimal version: includes permanent memory and only adds summary snapshots if present.
"""

from pathlib import Path
import json

# 📁 Base path for memory
memory_root = Path("memory")

for agent_folder in memory_root.iterdir():
    if not agent_folder.is_dir():
        continue

    agent = agent_folder.name
    print(f"🧠 Building compiled_context.txt for {agent}...")
    context_blocks = []

    # 🧱 Permanent Memory (now plain text file)
    permanent_path = agent_folder / "permanent_memory.txt"
    if permanent_path.exists():
        try:
            with open(permanent_path, "r", encoding="utf-8") as f:
                text = f.read()
            if text:
                context_blocks.append(text)
        except Exception as e:
            context_blocks.append(f"# Error loading permanent memory: {e}")

    # 📝 Future Summary Snapshots (only if summaries exist later)
    summary_path = agent_folder / "summary_snapshots.json"
    if summary_path.exists():
        try:
            with open(summary_path, "r") as f:
                summaries = json.load(f).get("summaries", [])
                if summaries:
                    context_blocks.extend(summaries)
        except Exception as e:
            context_blocks.append(f"# Error loading summary snapshots: {e}")

    # ✅ Write context
    compiled = "\n".join(context_blocks) if context_blocks else "# No context available."
    compiled_file = agent_folder / "compiled_context.txt"

    with open(compiled_file, "w") as f:
        f.write(compiled.strip())

    print(f"✅ Saved: {compiled_file}")


