#!/usr/bin/env python3
import os, signal, subprocess, sys, time, atexit, shutil

# ---- Config (adjust paths/ports if you like) ----
MODEL_PATH = "companions/davinci/davinci.gguf"
RUNTIME_PORT = "11435"

CTX_SIZE = "28192"
BATCH = "1536"
GPU_LAYERS = "-1"   # set to -1 = CPU; or a positive number to offload to GPU

env = os.environ.copy()
# CUDA env (optional; harmless if CUDA not used)
env["LD_LIBRARY_PATH"] = "/usr/local/cuda-12.8/lib64:" + env.get("LD_LIBRARY_PATH","")
env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
env["CUDA_VISIBLE_DEVICES"] = "0"
env.pop("GGML_CUDA_ENABLE_GRAPHS", None)

procs = []

def kill_port(port):
    try:
        subprocess.run(["fuser","-k",f"{port}/tcp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def stop_all():
    for p in procs[::-1]:
        try:
            p.terminate()
        except Exception:
            pass
    time.sleep(0.5)
    for p in procs[::-1]:
        try:
            p.kill()
        except Exception:
            pass

atexit.register(stop_all)

def check(paths):
    for p in paths:
        if not os.path.exists(p):
            print(f"Missing: {p}")
            sys.exit(1)

def main():
    check([MODEL_PATH])

    # Clean leftovers
    kill_port(RUNTIME_PORT)

    # Start Davinci runtime (will spawn persistent llama-cli internally)
    env["XI_USE_LLAMA_CLI"] = "1"
    runtime_cmd = [
        sys.executable, "-m", "runtime.davinci_runtime", "--server",
        "--host", "127.0.0.1", "--port", RUNTIME_PORT,
        "--ctx_size", CTX_SIZE, "--batch", BATCH,
        "--gpu_layers", GPU_LAYERS,
        "--threads", str(os.cpu_count() or 8),
        "--model", MODEL_PATH,
        "--init-file", "memory/davinci/compiled_context.txt",
    ]
    print("▶ Starting davinci_runtime (CLI mode):", " ".join(runtime_cmd), flush=True)
    p_runtime = subprocess.Popen(runtime_cmd, env=env)
    procs.append(p_runtime)

    # Run the flow
    env["XI_DAVINCI_SERVER"] = f"127.0.0.1:{RUNTIME_PORT}"
    flow_cmd = [sys.executable, "-m", "flows.idea_generator_flow"]
    print("▶ Running flow:", " ".join(flow_cmd), flush=True)
    p_flow = subprocess.Popen(flow_cmd, env=env)
    procs.append(p_flow)

    # Forward Ctrl+C to stop everything
    try:
        rc = p_flow.wait()
    except KeyboardInterrupt:
        print("\n⏹ Ctrl+C received; stopping...")
    finally:
        stop_all()

if __name__ == "__main__":
    main()

