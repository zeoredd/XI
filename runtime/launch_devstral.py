#!/usr/bin/env python3
"""
One-command launcher for Devstral (no-frills coder).
Starts:
  1) llama-server (devstral.gguf)
  2) devstral_runtime (proxy)
Optionally runs a code flow if present (flows.code_generator_flow).
Ctrl+C stops everything cleanly.
"""

import os, sys, time, atexit, subprocess, signal, shutil

# --- Adjust these if your paths/ports differ ---
MODEL_PATH   = "/home/ghost/XI/companions/devstral/devstral.gguf"
LLAMA_BIN    = os.path.expanduser("~/llama.cpp/build/bin/llama-server")
LLAMA_PORT   = "11446"   # llama-server (Devstral)
RUNTIME_PORT = "11445"   # devstral_runtime
CTX_SIZE     = "8192"
BATCH        = "1536"
GPU_LAYERS   = os.environ.get("GPU_LAYERS", "auto")  # "auto" will pick a safe value
THREADS      = str(os.cpu_count() or 8)

# Try to run a code flow after the runtime is up. Set to "" to skip.
OPTIONAL_CODE_FLOW = "flows.code_generator_flow"  # leave as-is; will be skipped if module missing

procs = []
env = os.environ.copy()

# Optional quiet mode for cleaner terminals
QUIET = env.get("XI_QUIET") == "1"

def kill_port(port: str):
    try:
        subprocess.run(["fuser", "-k", f"{port}/tcp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def hard_cleanup():
    # close ports
    for port in (RUNTIME_PORT, LLAMA_PORT):
        kill_port(port)
    # kill any lingering servers/runtimes
    for pat in ("llama-server", "runtime.devstral_runtime", "runtime.davinci_runtime"):
        try:
            subprocess.run(["pkill","-f",pat], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    time.sleep(0.6)

def stop_all():
    # stop in reverse order
    for p in procs[::-1]:
        try: p.terminate()
        except Exception: pass
    time.sleep(0.4)
    for p in procs[::-1]:
        try: p.kill()
        except Exception: pass

atexit.register(stop_all)

def check_paths():
    missing = [p for p in (MODEL_PATH, LLAMA_BIN) if not os.path.exists(p)]
    if missing:
        print("Missing required path(s):")
        for m in missing: print(" -", m)
        sys.exit(1)

def choose_gpu_layers(default_layers: str = GPU_LAYERS) -> str:
    # if user gave an explicit integer, respect it
    if default_layers not in ("-1", "auto"):
        return default_layers
    # no nvidia-smi? play safe
    if not shutil.which("nvidia-smi"):
        return "0"  # CPU
    try:
        out = subprocess.check_output(
            ["nvidia-smi","--query-gpu=memory.free","--format=csv,noheader,nounits"]
        ).decode().strip().splitlines()
        free_mib = int(out[0])
    except Exception:
        return "0"
    # Empirical sizing from your success case:
    # ~23206 MiB for 41 layers → ~566 MiB/layer; keep ~1500 MiB headroom
    per_layer = 566
    headroom = 1500
    max_layers = max(0, (free_mib - headroom) // per_layer)
    max_layers = min(max_layers, 41)  # Devstral total layers ~41
    return "0" if max_layers <= 0 else str(max_layers)


def start_llama():
    if not QUIET:
        print(f"▶ Starting llama-server for Devstral on :{LLAMA_PORT}")
    ngl = choose_gpu_layers(GPU_LAYERS)
    if not QUIET:
        print(f"   • GPU offload (-ngl) = {ngl}")
    # expose chosen ngl so the runtime can receive a numeric --gpu_layers (no 'auto')
    env["XI_NGL"] = ngl
    cmd = [
        LLAMA_BIN,
        "-m", MODEL_PATH,
        "-ngl", ngl,
        "-c", CTX_SIZE,
        "--batch-size", BATCH,
        "--port", LLAMA_PORT,
        "--chat-template", ""   # disable baked-in role scaffolding
    ]

    # redirect logs to file if XI_LOG_DIR set
    stdout = stderr = None
    if env.get("XI_LOG_DIR"):
        os.makedirs(env["XI_LOG_DIR"], exist_ok=True)
        f = open(os.path.join(env["XI_LOG_DIR"], "llama_server_devstral.log"), "a")
        procs.append(f)  # keep ref to avoid GC
        stdout = stderr = f
    p = subprocess.Popen(cmd, env=env, stdout=stdout, stderr=stderr)
    procs.append(p)
    time.sleep(1.2)  # give it time to bind

def start_runtime():
    if not QUIET:
        print(f"▶ Starting devstral_runtime on :{RUNTIME_PORT} (upstream llama :{LLAMA_PORT})")
    env["XI_LLAMA_HTTP"] = f"127.0.0.1:{LLAMA_PORT}"
    env["XI_AGENT_NAME"] = "Devstral"
    cmd = [
        sys.executable, "-m", "runtime.devstral_runtime",
        "--server", "--host", "127.0.0.1", "--port", RUNTIME_PORT,
        "--ctx_size", CTX_SIZE, "--batch", BATCH,
        # runtime expects an integer; use the numeric value chosen for llama-server
        "--gpu_layers", env.get("XI_NGL", "0"), "--threads", THREADS,
    ]
    stdout = stderr = None
    if env.get("XI_LOG_DIR"):
        f = open(os.path.join(env["XI_LOG_DIR"], "devstral_runtime.log"), "a")
        procs.append(f)
        stdout = stderr = f
    p = subprocess.Popen(cmd, env=env, stdout=stdout, stderr=stderr)
    procs.append(p)
    time.sleep(0.2)  # brief settle time
    # export for flows or GUI to use
    env["XI_DEVSTRAL_SERVER"] = f"127.0.0.1:{RUNTIME_PORT}"

def maybe_run_code_flow():
    if not OPTIONAL_CODE_FLOW:
        return None
    # Check if the flow module exists before trying
    try:
        __import__(OPTIONAL_CODE_FLOW)
    except Exception:
        if not QUIET:
            print("ℹ️ No code flow module found; Devstral runtime is up and ready.")
        return None
    if not QUIET:
        print(f"▶ Running code flow: {OPTIONAL_CODE_FLOW}")
    # Pass the runtime address explicitly so the flow never hits old ports.
    # Normalize into host:port only, since the flow's resolver will add http:// + /completion.
    server_arg = env["XI_DEVSTRAL_SERVER"]
    if server_arg.startswith("http://") or server_arg.startswith("https://"):
        # strip schema if present
        server_arg = server_arg.split("://", 1)[1]
    cmd = [sys.executable, "-m", OPTIONAL_CODE_FLOW, "--server", server_arg]
    p = subprocess.Popen(cmd, env=env)
    procs.append(p)
    return p

def main():
    check_paths()
    # clean up leftovers (ports + processes) before starting
    hard_cleanup()

    start_llama()
    start_runtime()
    if not QUIET:
        print(f"✅ Devstral ready at XI_DEVSTRAL_SERVER={env['XI_DEVSTRAL_SERVER']}")
        print("   Tip: set XI_QUIET=1 to silence logs; XI_LOG_DIR=/path to log to files.")

    p_flow = maybe_run_code_flow()

    try:
        if p_flow is not None:
            # Wait but keep servers alive even if the flow exits (e.g., wrong URL, user quits)
            rc = p_flow.wait()
            if not QUIET:
                print(f"ℹ️ Flow exited with code {rc}. Servers remain up. Press Ctrl+C to stop.")
            while True:
                time.sleep(3600)
        else:
            # No flow: just keep servers alive
            while True:
                time.sleep(3600)
    except KeyboardInterrupt:
        if not QUIET:
            print("\n⏹ Stopping Devstral…")
    finally:
        stop_all()

if __name__ == "__main__":
    main()

