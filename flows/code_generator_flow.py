#!/usr/bin/env python3
"""
🎯 code_generator_flow.py
Minimal flow for Devstral (stateless code generator).
- No memory, no journaling, no GAEL.
- Talks directly to llama-server via HTTP.
- Optional file inputs (drag/drop in GUI later).
"""

import argparse, os, re, socket, sys, json
import requests
from pathlib import Path

# Resolve server target:
# Priority: --server → XI_DEVSTRAL_SERVER → XI_DAVINCI_SERVER → fallback 127.0.0.1:11445
# NOTE: We support TWO modes:
#  - TCP JSONL to the runtime (host:port)  ← default & recommended
#  - HTTP to llama-server (/completion)     ← when a full URL is provided
def resolve_server(arg_server: str | None) -> str:
    if arg_server:
        return arg_server.strip()
    if os.getenv("XI_DEVSTRAL_SERVER"):
        return os.getenv("XI_DEVSTRAL_SERVER").strip()
    if os.getenv("XI_DAVINCI_SERVER"):
        return os.getenv("XI_DAVINCI_SERVER").strip()
    return "127.0.0.1:11445"

def filter_code_only(text: str) -> str:
    """
    Keep only fenced code blocks if present, else return the raw text.
    """
    blocks = re.findall(r"```[a-zA-Z0-9]*\n([\s\S]*?)```", text)
    if blocks:
        return "\n\n".join(b.strip() for b in blocks).strip()
    return text.strip()

def _run_via_http_llama(prompt: str, url: str) -> str:
    """POST to llama-server /completion (HTTP)."""
    # Ensure endpoint ends with /completion
    base = url.strip()
    if not base.startswith(("http://","https://")):
        base = "http://" + base
    if not base.rstrip("/").endswith("/completion"):
        base = base.rstrip("/") + "/completion"
    r = requests.post(
        base,
        json={"prompt": prompt, "n_predict": 512, "temperature": 0.2, "cache_prompt": True},
        timeout=300
    )
    r.raise_for_status()
    data = r.json()
    # llama.cpp can return either {completion: "..."} or {content: "..."}
    return (data.get("content") or data.get("completion") or "").strip()

def _run_via_tcp_runtime(prompt: str, host_port: str) -> str:
    """Send one JSON line to the runtime TCP server and read one JSON line back."""
    host, port = host_port.split(":")
    payload = {
        "input": prompt,
        "temp": 0.2,
        "max_tokens": 512,
        "stop": ["^User:"]
    }
    with socket.create_connection((host, int(port)), timeout=10) as sock:
        sock_file = sock.makefile(mode="rwb", buffering=0)
        line = (json.dumps(payload) + "\n").encode("utf-8")
        sock_file.write(line)
        resp_line = sock_file.readline().decode("utf-8", "ignore")
    # runtime returns {"text": "...", "elapsed_sec": ...}
    obj = json.loads(resp_line or "{}")
    return (obj.get("text") or "").strip()

def run_devstral(prompt: str, server: str) -> str:
    try:
        if server.startswith(("http://","https://")):
            out = _run_via_http_llama(prompt, server)
        else:
            out = _run_via_tcp_runtime(prompt, server)
        return filter_code_only(out)
    except Exception as e:
        return f"[Error contacting Devstral: {e}]"

def main():
    parser = argparse.ArgumentParser(description="Run Devstral (stateless code generator).")
    parser.add_argument("prompt", nargs="*", help="Prompt/idea for Devstral. Leave blank to read from stdin.")
    parser.add_argument("--file", type=Path, help="Optional file to include (contents appended to the prompt).")
    parser.add_argument(
        "--server",
        help="Override runtime endpoint (host:port or full URL). Example: 127.0.0.1:11445"
    )
    args = parser.parse_args()

    server = resolve_server(args.server)

    while True:
        try:
            if args.prompt:
                user_prompt = " ".join(args.prompt)
                args.prompt = None  # only use CLI arg once
            else:
                user_prompt = input("\nEnter your idea/specification (or 'quit'): ").strip()
            if not user_prompt or user_prompt.lower() in {"quit","exit"}:
                break

            if args.file and args.file.exists():
                file_text = args.file.read_text(errors="ignore")
                user_prompt += f"\n\n# Attached file: {args.file}\n{file_text}"

            print("\n=== Devstral Output ===\n")
            output = run_devstral(user_prompt, server)
            print(output)
        except (KeyboardInterrupt, EOFError):
            break

if __name__ == "__main__":
    main()



