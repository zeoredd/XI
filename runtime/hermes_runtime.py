#!/usr/bin/env python3
"""
🤖 hermes_runtime.py
Runs Hermes using llama-cpp-python with GGUF + context
"""

import argparse
from pathlib import Path
from llama_cpp import Llama

# 🛠️ CLI Args
parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
parser.add_argument("--model", required=True)
parser.add_argument("--context", required=True)
parser.add_argument("--temp", type=float, default=0.8)  # 🔥 Temperature
args = parser.parse_args()

# 📂 Load memory context
context = ""
context_path = Path(args.context)
if context_path.exists():
    with open(context_path, "r") as f:
        context = f.read()

# 🧠 Init LLaMA model
llm = Llama(
    model_path=args.model,
    n_ctx=2048,
    n_threads=6,
    use_mlock=True,
    use_mmap=True
)

# 🧾 Prompt construction
prompt = context + f"\nUser: {args.input}\nHermes:"
#print(f"📝 Prompting (temp={args.temp}):\n", prompt[:1000], flush=True)  # Optional preview

# 🧠 Run inference
response = llm(
    prompt,
    max_tokens=512,
    stop=["User:", "Hermes:"],
    temperature=args.temp,  # 🔥 Use CLI temp
    echo=False
)

# 🧠 Output response
if "choices" in response and response["choices"]:
    output = response["choices"][0].get("text", "").strip()
    print(output)
else:
    print("(no output — model returned nothing)")

