#!/usr/bin/env python3
"""
🛡️ verify_signature.py
Verifies the Minisign signature of a journal entry using the correct agent public key.
"""

import subprocess
import sys
from pathlib import Path

if len(sys.argv) != 2:
    print("Usage: python3 verify_signature.py path/to/journal.json")
    sys.exit(1)

journal_path = Path(sys.argv[1])
sig_path = journal_path.with_suffix(journal_path.suffix + ".minisig")

if not journal_path.exists():
    print(f"❌ Journal file not found: {journal_path}")
    sys.exit(1)

if not sig_path.exists():
    print(f"❌ Signature file not found: {sig_path}")
    sys.exit(1)

# Infer agent name from journal filename
filename_parts = journal_path.name.split("_")
if not filename_parts or len(filename_parts) < 2:
    print("❌ Unable to parse agent name from filename.")
    sys.exit(1)

agent = filename_parts[0]
public_key_path = Path("keys") / f"{agent}_keys" / f"public_{agent}.txt"

if not public_key_path.exists():
    print(f"❌ Public key not found: {public_key_path}")
    sys.exit(1)

# 🧪 Run Minisign verification
result = subprocess.run([
    "minisign",
    "-Vm", str(journal_path),
    "-x", str(sig_path),
    "-p", str(public_key_path)
])

if result.returncode == 0:
    print(f"✅ Signature verified: {journal_path.name}")
else:
    print(f"⚠️ Signature verification failed: {journal_path.name}")

