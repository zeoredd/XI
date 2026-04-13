#!/usr/bin/env python3
"""
🤖 davinci_runtime.py
Runs Davinci (llama-cpp-python, GGUF). Can act as:
 - one-shot CLI (default)
 - persistent server (--server) over a simple JSON-lines TCP protocol
"""
import argparse, json, os, signal, socket, sys, time
from pathlib import Path
from typing import List, Optional
USE_LLAMA_CLI = os.getenv("XI_USE_LLAMA_CLI") == "1"
LLAMA_CLI = os.getenv("XI_LLAMA_CLI_PATH", "/home/ghost/llama.cpp/build/bin/llama-cli")
LLAMA_CLI_OK = os.path.exists(LLAMA_CLI)
LLAMA_HAS_NODISPLAY = False
LLAMA_HTTP = os.getenv("XI_LLAMA_HTTP")  # e.g., "127.0.0.1:11436"

# 🔧 Behavior toggles (env)
STREAM_PARTIALS = os.getenv("XI_STREAM_PARTIALS", "1") == "1"   # keep semi-live debug on
IDLE_BREAK_TICKS = int(os.getenv("XI_IDLE_BREAK_TICKS", "3"))   # 3s quiet = done
STRIP_PROMPT_LINES = os.getenv("XI_STRIP_PROMPT_LINES", "1") != "0"
# 🔕 Default OFF: avoid llama-cli printing "Interrupted by user" into stdout
SEND_SIGINT_ON_IDLE_BREAK = os.getenv("XI_SEND_SIGINT_ON_IDLE_BREAK", "0") == "1"
EOS_STRINGS = [s for s in os.getenv("XI_EOS_STRINGS", "</s>").split(",") if s]
# 🎛️ Streaming chunk controls
CHUNK_MODE = os.getenv("XI_STREAM_CHUNK_MODE", "whitespace")  # whitespace | fixed | char
CHUNK_CHARS = int(os.getenv("XI_STREAM_CHUNK_CHARS", "24"))
# 🧹 Only strip a *leading* quote line at the very start of the model’s reply
DROP_LEADING_QUOTE = os.getenv("XI_DROP_LEADING_QUOTE", "1") == "1"

import subprocess, threading, queue, codecs, re
_persistent_cli = None
_cli_stdout_q = queue.Queue()
_cli_lock = threading.Lock()
_RE_BARE_PROMPT = re.compile(r'^\s*>\s*$')
_RE_LEADING_QUOTE = re.compile(r'^\s*>\s?.*$')  # only used for *first* line of a reply
_STOP_EVENT = threading.Event()
_STOP_LOCK = threading.Lock()

TOKEN_COUNTER_PATH = Path("/home/ghost/XI/runtime/token_counter.json")

def _write_token_usage(prompt_tokens: int, completion_tokens: int, max_context: int):
    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_used": prompt_tokens + completion_tokens,
        "max_context": max_context,
        "percent_used": round(((prompt_tokens + completion_tokens) / max_context) * 100, 1)
    }
    try:
        TOKEN_COUNTER_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_COUNTER_PATH.write_text(json.dumps(usage, indent=2))
    except Exception as e:
        print(f"⚠️ token counter write failed: {e}", flush=True)

def _drain_stdout_q():
    """Discard any leftover bytes from previous turns so we don't mix outputs."""
    try:
        while True:
            _cli_stdout_q.get_nowait()
    except queue.Empty:
        return



def _start_persistent_cli():
    global _persistent_cli, LLAMA_HAS_NODISPLAY
    with _cli_lock:
        if _persistent_cli is not None and _persistent_cli.poll() is None:
            return _persistent_cli

        # Probe whether --no-display-prompt exists
        try:
            help_txt = subprocess.run([LLAMA_CLI, "--help"], capture_output=True, text=True, timeout=3)
            LLAMA_HAS_NODISPLAY = "--no-display-prompt" in (help_txt.stdout or "")
        except Exception:
            LLAMA_HAS_NODISPLAY = False

        cmd = [
            LLAMA_CLI, "-i",
            "-m", args.model,
            "-ngl", str(args.gpu_layers if args.gpu_layers is not None else -1),
            "-c", str(args.ctx_size),
            "--batch-size", str(args.batch),
            "--temp", str(args.temp),
        ]
        # Ensure machine-friendly IO (no REPL prompt/echo)
        # --simple-io stops llama.cpp from printing the interactive prompt
        cmd.append("--simple-io")  # (only once)
        if LLAMA_HAS_NODISPLAY:
            cmd.append("--no-display-prompt")
        if args.init_file:
            cmd.extend(["-f", args.init_file])

        _persistent_cli = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0
        )

    # background reader thread with a proper UTF-8 incremental decoder
    def _reader_thread(proc, q):
        dec = codecs.getincrementaldecoder("utf-8")()
        while True:
            raw = proc.stdout.read(4096)  # block read, efficient
            if not raw:
                break
            text = dec.decode(raw)
            if text:
                for ch in text:
                    q.put(ch)
        # flush any remaining decoder state on EOF
        tail = dec.decode(b"", final=True)
        if tail:
            for ch in tail:
                q.put(ch)
    # start the reader from _start_persistent_cli (correct scope) and return proc
    threading.Thread(target=_reader_thread, args=(_persistent_cli, _cli_stdout_q), daemon=True).start()
    return _persistent_cli

def _interrupt_generation(proc):
    """Send SIGINT once and drain a tiny tail so next turn starts clean."""
    with _STOP_LOCK:
        try:
            if proc and proc.poll() is None:
                proc.send_signal(signal.SIGINT)
        except Exception:
            pass
    t0 = time.time()
    while time.time() - t0 < 0.25:
        try:
            _cli_stdout_q.get(timeout=0.05)  # discard tail
        except queue.Empty:
            break


try:
    from llama_cpp import Llama
    _HAVE_LLAMA_CPP = True
except Exception:
    _HAVE_LLAMA_CPP = False

# ── CLI Args ──────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
if not os.getenv("XI_LLAMA_HTTP"):
    parser.add_argument("--model", required=True)
else:
    parser.add_argument("--model", default="(http)")
parser.add_argument("--input", help="One-shot prompt (omit when --server)")
parser.add_argument("--init-file", help="Optional path to a text file; contents are prepended once at startup")
parser.add_argument("--temp", type=float, default=0.8)
parser.add_argument("--max_tokens", type=int, default=512)
# Only stop on the *next* user turn marker; do NOT include "Davinci:" (prevents empty output)
# Use a regex that matches the next user turn on a new line. '^User:' is more portable than '\nUser:'.
# Use a regex that matches a new turn reliably. Do NOT include "Davinci:" (prevents empty output).
parser.add_argument("--stop", nargs="*", default=[])
parser.add_argument("--server", action="store_true", help="Run as persistent server")
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=11435)
parser.add_argument("--ctx_size", type=int, default=4096)
parser.add_argument("--threads", type=int, default=max(1, os.cpu_count() // 2))
parser.add_argument("--gpu_layers", type=int, default=-1, help="-1 = all to GPU")
parser.add_argument("--batch", type=int, default=512)
parser.add_argument("--cli_timeout", type=float, default=float(os.getenv("XI_CLI_TIMEOUT", "60")))
args = parser.parse_args()

# ── Load model once ───────────────────────────────────────────────────────────
INIT_CONTEXT = ""
if getattr(args, "init_file", None):
    try:
        INIT_CONTEXT = Path(args.init_file).read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        INIT_CONTEXT = ""

_INIT_SENT = False

def _init_llm():
    return Llama(
        model_path=args.model,
        n_ctx=args.ctx_size,
        n_threads=args.threads,
        n_gpu_layers=args.gpu_layers,
        n_batch=args.batch,
        use_mlock=True,
        use_mmap=True,
        numa=False,
        verbose=False
    )
llm = None
# Only initialize llama-cpp if no HTTP backend and no CLI override
if not LLAMA_HTTP and not USE_LLAMA_CLI and _HAVE_LLAMA_CPP:
    llm = _init_llm()

# One-time CLI capability probe (avoid doing this on every request)
if USE_LLAMA_CLI and LLAMA_CLI_OK:
    try:
        import subprocess
        help_out = subprocess.run([LLAMA_CLI, "-h"], capture_output=True, text=True).stdout
        LLAMA_HAS_NODISPLAY = "--no-display-prompt" in (help_out or "")
    except Exception:
        LLAMA_HAS_NODISPLAY = False


def _gen_llama_cpp(prompt: str, temp: float, max_tokens: int, stop: Optional[List[str]]):
    out = llm(
        prompt,
        max_tokens=max_tokens,
        stop=["^User:"],
        temperature=temp,
        echo=False,
    )
    if "choices" in out and out["choices"]:
        return (out["choices"][0].get("text", "") or "").strip()
    return ""

def _gen_llama_cli(prompt: str, temp: float, max_tokens: int, stop: Optional[List[str]]):
    proc = _start_persistent_cli()

    # Ensure we’re not carrying leftovers from the previous turn
    _drain_stdout_q()

    # Send prompt
    try:
        proc.stdin.write((prompt.strip() + "\n").encode("utf-8"))
        proc.stdin.flush()
    except BrokenPipeError:
        # restart once if CLI died
        proc = None
        _start_persistent_cli()
        proc = _persistent_cli
        _drain_stdout_q()
        proc.stdin.write((prompt.strip() + "\n").encode("utf-8"))
        proc.stdin.flush()

    out = []
    eos_buf = []
    tokens_seen = 0
    idle_ticks = 0
    saw_output = False
    broke_on_idle = False

    def _have_eos():
        tail = "".join(eos_buf[-16:])
        return any(e in tail for e in EOS_STRINGS)

    while True:
        try:
            ch = _cli_stdout_q.get(timeout=1)
        except queue.Empty:
            idle_ticks += 1
            # Only *consider* idle break after we've seen something
            if saw_output and idle_ticks >= IDLE_BREAK_TICKS:
                broke_on_idle = True
                break
            # If process died, we must exit
            if proc.poll() is not None:
                break
            continue

        idle_ticks = 0
        saw_output = True
        out.append(ch)
        eos_buf.append(ch)

        # semi-live debug (stdout) every ~50 chars
        if STREAM_PARTIALS and (len(out) % 50 == 0):
            preview = "".join(out[-50:]).replace("\n", "\\n")
            print(f"[davinci/partial {len(out)}] {preview}", flush=True)

        # Count "tokens" crudely on whitespace
        if ch.isspace():
            tokens_seen += 1
            if max_tokens and tokens_seen >= max_tokens:
                break

        # explicit EOS
        if _have_eos():
            break

    # If we bailed due to idle (no EOS), optionally signal to stop generation.
    if broke_on_idle and SEND_SIGINT_ON_IDLE_BREAK and proc.poll() is None:
        try:
            proc.send_signal(signal.SIGINT)
        except Exception:
            pass
        # drain a short burst AFTER SIGINT so next turn is clean
        t0 = time.time()
        while time.time() - t0 < 0.2:
            try:
                _ = _cli_stdout_q.get(timeout=0.05)  # discard; don't append to current turn
            except queue.Empty:
                break

    return _clean_cli_text("".join(out)) if out else ""


def _clean_cli_text(s: str, *, strip_edges: bool = True) -> str:
    # Remove EOS markers but otherwise preserve whitespace unless explicitly asked
    for eos in EOS_STRINGS:
        if eos:
            s = s.replace(eos, "")
    if STRIP_PROMPT_LINES:
        # Remove only the exact prompt/interrupt lines without collapsing spaces
        # keepends=True preserves newlines/spacing inside chunks
        s = "".join(
            ln for ln in s.splitlines(keepends=True)
            if (not ln.lstrip().startswith("> ")) and (ln.strip() != "Interrupted by user")
        )
    return s.strip() if strip_edges else s

# ── Streaming generator (yields small text chunks as they arrive) ────────────
def _gen_llama_cli_stream(prompt: str, temp: float, max_tokens: int, stop=None, chunk_chars: int = None):
    if chunk_chars is None:
        chunk_chars = CHUNK_CHARS
    proc = _start_persistent_cli()
    _drain_stdout_q()  # clear leftovers from previous turn

    # send prompt
    proc.stdin.write((prompt.strip() + "\n").encode("utf-8"))
    proc.stdin.flush()

    buf, eos_buf = [], []
    idle_ticks = 0
    saw_out = False
    tokens_seen = 0
    IDLE = IDLE_BREAK_TICKS

    def have_eos() -> bool:
        tail = "".join(eos_buf[-16:])
        return any(s and s in tail for s in EOS_STRINGS)

    while True:
        # 🔴 External stop request?
        if _STOP_EVENT.is_set():
            _STOP_EVENT.clear()
            _interrupt_generation(proc)
            if buf:
                yield "".join(buf); buf.clear()
            break
        try:
            ch = _cli_stdout_q.get(timeout=1.0)
        except queue.Empty:
            idle_ticks += 1
            if saw_out and idle_ticks >= IDLE:
                if buf:
                    yield "".join(buf); buf.clear()
                break
            if proc.poll() is not None:
                if buf:
                    yield "".join(buf); buf.clear()
                break
            continue

        idle_ticks = 0
        saw_out = True
        eos_buf.append(ch)
        buf.append(ch)

        # crude token cap (whitespace ~= token boundary)
        if ch.isspace():
            tokens_seen += 1
            if max_tokens and tokens_seen >= max_tokens:
                if buf:
                    yield "".join(buf); buf.clear()
                break
        # ── Chunking strategies ──────────────────────────────────────────────
        mode = CHUNK_MODE
        if mode == "char":
            # Per-char streaming: exact bytes, zero chance to lose spaces
            s = "".join(buf)
            yield s
            buf.clear()
            continue

        if ch == "\n":
            # Natural break: flush everything including the newline
            yield "".join(buf); buf.clear()
        elif mode == "fixed":
            if len(buf) >= chunk_chars:
                s = "".join(buf)
                yield s
                buf.clear()
        else:  # "whitespace" (default)
            # Prefer whitespace boundaries to avoid cutting right before a space
            if len(buf) >= chunk_chars:
                s = "".join(buf)
                split_at = max(s.rfind(" "), s.rfind("\t"), s.rfind("\n"))
                if split_at != -1 and split_at >= len(s) // 3:
                    yield s[:split_at + 1]
                    buf = list(s[split_at + 1:])
                else:
                    # No good boundary yet — wait for a little more context
                    # Only hard-flush if the buffer grows too large
                    if len(buf) >= (chunk_chars * 2):
                        yield s
                        buf.clear()

        if have_eos():
            if buf:
                yield "".join(buf); buf.clear()
            break



def generate(user_input: str,
             temp: float,
             max_tokens: int,
             stop: Optional[List[str]]) -> str:
    prompt = user_input
    if USE_LLAMA_CLI or not _HAVE_LLAMA_CPP:
        return _gen_llama_cli(prompt, temp, max_tokens, stop)
    return _gen_llama_cpp(prompt, temp, max_tokens, stop)

# ── Server mode (JSONL over TCP) ──────────────────────────────────────────────
def serve(host: str, port: int):
    engine = ("llama-http" if LLAMA_HTTP else
              ("llama-cli" if (USE_LLAMA_CLI and LLAMA_CLI_OK) else
               ("llama_cpp" if _HAVE_LLAMA_CPP else "none")))
    name = os.getenv("XI_AGENT_NAME", "Davinci")
    print(f"🔥 {name} server listening on {host}:{port} | engine={engine} | model={'(http)' if LLAMA_HTTP else args.model}")
    running = True
    def _sigint(_sig, _frm):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, _sigint)
    signal.signal(signal.SIGTERM, _sigint)

    # 🎛 Lightweight STOP control server on (port+1)
    def _stop_server():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as cs:
            cs.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            cs.bind((host, port + 1))
            cs.listen(4)
            cs.settimeout(1.0)
            print(f"🎛 stop control on {host}:{port+1} (send 'STOP' or {{\"stop\":true}})", flush=True)
            while running:
                try:
                    c, _ = cs.accept()
                except socket.timeout:
                    continue
                with c:
                    c.settimeout(1.0)
                    try:
                        data = c.recv(1024)
                        if not data:
                            continue
                        msg = data.decode("utf-8", "ignore").strip()
                        if msg.upper().startswith("STOP"):
                            _STOP_EVENT.set()
                            c.sendall(b"OK\n"); continue
                        try:
                            j = json.loads(msg)
                            if isinstance(j, dict) and j.get("stop"):
                                _STOP_EVENT.set()
                                c.sendall(b"OK\n"); continue
                        except Exception:
                            pass
                        c.sendall(b"?\n")
                    except Exception:
                        pass

    threading.Thread(target=_stop_server, daemon=True).start()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port))
        srv.listen(16)
        srv.settimeout(1.0)
        while running:
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            conn.settimeout(120.0)
            with conn:
                f = conn.makefile(mode="rwb", buffering=0)
                # simple JSON-lines protocol: one request per line; one response per line
                while running:
                    try:
                        line = f.readline()
                        if not line:
                            break
                        raw = line.decode("utf-8", "ignore")
                        print(f"➡️  req: {raw.strip()}", flush=True)
                        req = json.loads(raw)
                        # Streaming branch: push JSONL deltas when requested
                        if req.get("stream"):
                            f.write(b'{"started": true}\n'); f.flush()
                            t0 = time.time()
                            assembled: list[str] = []
                            leading_line_handled = False
                            for delta in _gen_llama_cli_stream(
                                prompt=req.get("input", ""),
                                temp=float(req.get("temp", args.temp)),
                                max_tokens=int(req.get("max_tokens", args.max_tokens)),
                                stop=req.get("stop", args.stop),
                            ):
                                # ⚠️ Do NOT strip edges on streaming chunks (prevents smashed words)
                                # 🚫 keep deltas raw to preserve spacing
                                if not delta:
                                    continue
                                # Drop only a *bare* CLI prompt, never real blockquotes
                                if _RE_BARE_PROMPT.match(delta):
                                    continue
                                # Optionally strip a *leading* '>' line once (user preference)
                                if DROP_LEADING_QUOTE and not leading_line_handled:
                                    # if the very first line begins with '>', drop that first line only
                                    if _RE_LEADING_QUOTE.match(delta.lstrip()):
                                        nl = delta.find("\n")
                                        if nl != -1:
                                            delta = delta[nl+1:]
                                        else:
                                            # this whole chunk is the leading quote; skip and continue
                                            continue
                                    leading_line_handled = True
                                f.write(json.dumps({"delta": delta}).encode("utf-8") + b"\n")
                                f.flush()
                                assembled.append(delta)
                            # Now clean once at the end (safe to trim edges here)
                            final_text = _clean_cli_text("".join(assembled), strip_edges=True)
                            dt = time.time() - t0
                            f.write(json.dumps({"done": True, "text": final_text, "elapsed_sec": round(dt, 3)}).encode("utf-8") + b"\n")
                            f.flush()
                            print(f"⬅️  stream done: {len(final_text)} chars in {dt:.2f}s", flush=True)
                        else:
                            # Legacy single-shot response
                            t0 = time.time()
                            text = generate(
                                user_input=req.get("input", ""),
                                temp=float(req.get("temp", args.temp)),
                                max_tokens=int(req.get("max_tokens", args.max_tokens)),
                                stop=req.get("stop", args.stop),
                            )
                            dt = time.time() - t0
                            resp = {"text": text, "elapsed_sec": round(dt, 3)}
                            out = json.dumps(resp) + "\n"
                            f.write(out.encode("utf-8"))
                            print(f"⬅️  resp: {len(text)} chars in {dt:.2f}s", flush=True)
                    except (BrokenPipeError, ConnectionResetError):
                        # client dropped; end this connection cleanly
                        break
                    except Exception as e:
                        try:
                            err = json.dumps({"error": str(e)}) + "\n"
                            f.write(err.encode("utf-8"))
                            print(f"❌  resp error: {e}", flush=True)
                        except Exception:
                            break

# ── Main ──────────────────────────────────────────────────────────────────────
if args.server:
    if USE_LLAMA_CLI and not LLAMA_CLI_OK:
        print(f"⚠️ XI_USE_LLAMA_CLI=1 but '{LLAMA_CLI}' is not found; falling back to python binding.", flush=True)
    serve(args.host, args.port)
    sys.exit(0)

# One-shot CLI mode
if not args.input:
    print("⚠️ Provide --input for one-shot mode (or pass --server).", file=sys.stderr)
    sys.exit(2)
text = generate(
    user_input=args.input,
    temp=args.temp,
    max_tokens=args.max_tokens,
    stop=args.stop,
)
print(text if text else "(no output — model returned nothing)")




