# XI Runtime (CLI-Interactive) — Developer Guide & Troubleshooting

This README explains how our **CLI-interactive** LLM runtime works, how to run it, how the **JSONL streaming** protocol behaves, how to **/stop** a generation, and how to debug common issues. It assumes a developer joining the project fresh.

Core files:
- `runtime/davinci_runtime.py` – persistent JSONL server over TCP; manages llama.cpp `llama-cli -i`, streaming, chunking, stop control, cleaning.  
- `runtime/launch_idea_flow.py` – starter that boots the runtime and launches a sample flow with correct env/ports.  
- `flows/flow_orchestrator.py` – client that calls the runtime (stream or single-shot), prints live deltas, exposes a GUI-friendly `stop_generation()` helper.  
- `flows/idea_generator_flow.py` – tiny interactive loop using the orchestrator.  

---

## High-level architecture

```
Your terminal / GUI
        │
        │  (JSONL over TCP)
        ▼
flow_orchestrator.py  ──▶  davinci_runtime.py  ──▶  llama-cli -i --simple-io (GGUF)
  (client)                     (server)
```

- The **runtime** keeps a persistent `llama-cli -i` process and exposes a TCP server that accepts **one JSON request per line** and optionally **streams deltas** back as JSON Lines (JSONL).
- The **orchestrator** connects to that server, prints each `delta` as it arrives (live tokens), and also assembles the full text for journaling.
- A small **control socket** on `port+1` lets you send `STOP` to cut a generation immediately (great for GUIs).

---

## Quick start

```bash
# 1) activate env
source venv/bin/activate

# 2) enable live streaming and smooth chunking
export XI_LIVE_STREAM=1
export XI_STREAM_CHUNK_MODE=whitespace
export XI_STREAM_CHUNK_CHARS=40
export XI_DROP_LEADING_QUOTE=1   # strip only a *leading* '>' line if it sneaks in

# 3) start the stack
python3 runtime/launch_idea_flow.py
```

What this does:
- Launches the **runtime server** on `127.0.0.1:<RUNTIME_PORT>` and sets `XI_DAVINCI_SERVER` for the client flow.
- The flow uses `_davinci_server_call()` to request **streaming** via `"stream": true`, prints each delta live, and returns the final assembled text.

---

## Runtime server (under the hood)

- Runs `llama-cli -i` with `--simple-io` and (when supported) `--no-display-prompt`, so no interactive prompt is echoed back.  
- Starts a UTF-8 incremental decoder thread to read stdout and enqueue characters safely (avoids dropping multi-byte emoji/quotes).  
- Modes:
  - **Server mode (`--server`)**: JSONL TCP server, one request per line; optionally streams back `"delta"` chunks followed by `{"done": true, "text": ...}`.  
  - **One-shot mode**: If you call the runtime with `--input`, it prints one final response to stdout (legacy compatibility).  

**Stop control**:  
A second socket listens on **`port+1`** for `STOP` or `{"stop": true}` messages. On receipt, the runtime:
- SIGINTs the underlying `llama-cli`
- Drains a short tail
- Flushes any buffered text
- Ends the stream cleanly  

You’ll see:  
```
🎛 stop control on 127.0.0.1:11436 (send 'STOP' or {"stop":true})
```

---

## Client (flow_orchestrator.py)

- `_davinci_server_call()` sets `"stream": true` when `XI_LIVE_STREAM=1`, reads JSONL back, **prints each delta**, and assembles the full text.  
- `stop_generation(host_port=None)` connects to `port+1` and sends `STOP
` so GUIs can interrupt mid-stream.  
- Suppresses duplicate full reply when streaming if `XI_SUPPRESS_FINAL_ECHO_WHEN_STREAMING=1`.  

---

## Launch script (launch_idea_flow.py)

- Ensures the model file exists.  
- Kills any leftover process on the runtime port.  
- Starts `runtime.davinci_runtime --server` and then `flows.idea_generator_flow` with `XI_DAVINCI_SERVER` set.  
- Populates CUDA env vars (safe even if CUDA isn’t used).  
- Forwards Ctrl-C to clean up child processes.  

---

## Sample flow (idea_generator_flow.py)

- A simple REPL that reads `> ` from stdin, calls `run_agent_with_runtime("davinci", ...)`, and prints the reply.  
- Used for smoke-testing the runtime end-to-end.  

---

## JSONL protocol

**Request** (one JSON line):
```json
{"input": "hello world", "context_path": "/path/to/compiled_context.txt", "temp": 0.8, "max_tokens": 512, "stream": true}
```

**Response (streaming)**:
```
{"started": true}
{"delta": "Hel"}
{"delta": "lo "}
{"delta": "world"}
{"done": true, "text": "Hello world", "elapsed_sec": 1.23}
```

Server logs also show helpful markers like:
```
➡️  req: {...}
⬅️  stream done: 27 chars in 1.23s
```

---

## Environment variables

**Runtime (server side):**
- `XI_USE_LLAMA_CLI=1` – use `llama-cli -i`.  
- `XI_LLAMA_CLI_PATH=/path/to/llama-cli` – override binary path.  
- `XI_STREAM_CHUNK_MODE=whitespace|fixed|char` – chunking strategy.  
- `XI_STREAM_CHUNK_CHARS=40` – chunk size for whitespace/fixed modes.  
- `XI_DROP_LEADING_QUOTE=1` – strip only a leading `>` line.  
- `XI_STRIP_PROMPT_LINES=1` – remove bare REPL prompts.  
- `XI_SEND_SIGINT_ON_IDLE_BREAK=0` – don’t auto-SIGINT; prefer `/stop`.  
- `XI_IDLE_BREAK_TICKS=3` – idle seconds before marking done.  
- `XI_DEBUG_STREAM_DELTA=1` – debug: print raw deltas + hex.  

**Client (flow side):**
- `XI_DAVINCI_SERVER=host:port` – runtime endpoint.  
- `XI_LIVE_STREAM=1` – request streaming.  
- `XI_SUPPRESS_FINAL_ECHO_WHEN_STREAMING=1` – don’t re-print full text.  
- `XI_DAVINCI_MAX_TOKENS=512` – max tokens per reply.  
- `XI_AGENT_TIMEOUT=180` – socket timeout.  

---

## How to STOP a generation

**In Python (GUI button):**
```python
from flows.flow_orchestrator import stop_generation
stop_generation()  # uses XI_DAVINCI_SERVER and sends STOP to port+1
```

**From shell:**
```bash
printf 'STOP
' | nc 127.0.0.1 11436
```

---

## Recommended run profiles

**Everyday streaming:**
```bash
export XI_LIVE_STREAM=1
export XI_STREAM_CHUNK_MODE=whitespace
export XI_STREAM_CHUNK_CHARS=40
export XI_DROP_LEADING_QUOTE=1
```

**Byte-proof debug:**
```bash
export XI_LIVE_STREAM=1
export XI_STREAM_CHUNK_MODE=char
export XI_DEBUG_STREAM_DELTA=1
```

---

## Troubleshooting

- **`'NoneType' object has no attribute 'stdin'`**: llama-cli didn’t start; restart launcher.  
- **Stray `>` first line**: set `XI_DROP_LEADING_QUOTE=1`.  
- **Duplicate final output**: set `XI_SUPPRESS_FINAL_ECHO_WHEN_STREAMING=1`.  
- **Mushed words**: switch to `whitespace` chunking with ~40 chars.  
- **GPU busy after finish**: use `/stop` instead of idle SIGINT.  
- **Port already in use**: relaunch with `launch_idea_flow.py`, which kills old processes.  
- **Model missing**: update path in `launch_idea_flow.py` or set `XI_LLAMA_CLI_PATH`.  

---

## Integrating with a GUI

- Use `_davinci_server_call()` for live token streaming.  
- Connect a GUI stop button to `stop_generation()`.  
- Store the full `"text"` at stream end for journaling.  

---

## Notes on memory/contexts

- On startup, the flow rebuilds compiled contexts (`memory/davinci/compiled_context.txt` etc.).  
- Logs confirm context freshness on each launch.  

---

## Appendix A — Typical console logs

```
▶ Starting davinci_runtime (CLI mode): ...
🔥 Davinci server listening on 127.0.0.1:11435 | engine=llama-cli | model=companions/davinci/davinci.gguf
🎛 stop control on 127.0.0.1:11436 (send 'STOP' or {"stop":true})
➡️  req: {"input": "hey", ...}
⬅️  stream done: 2448 chars in 16.04s
```

---

## Appendix B — File map & roles

- `runtime/davinci_runtime.py`: server, CLI process mgmt, streaming, stop control.  
- `runtime/launch_idea_flow.py`: supervised boot of runtime + flow.  
- `flows/flow_orchestrator.py`: streaming client + stop helper.  
- `flows/idea_generator_flow.py`: sample test flow.  
