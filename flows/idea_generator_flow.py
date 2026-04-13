#!/usr/bin/env python3
"""
💡 idea_generator_flow.py (Davinci-only)
Live flow: user → davinci → user loop until 'sudo end flow'.
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))
from flows.flow_orchestrator import run_agent_with_runtime
from abilities.common_abilities.result_codes import (
    END,
    REFLECT,
    SESSION_JOURNAL,
    START_PREFIX,
    NOOP
)

import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

from abilities.common_abilities.sudo_command_handler import check_for_sudo_commands
from flows.flow_orchestrator import (
    start_flow,
    run_agent_with_runtime,
    end_flow,
    FLOW_CONTEXT,
    VECTOR_SEARCH_STATE,
    LAST_VECTOR_HITS,
    AGENT_PROFILES,
)
# 📁 Model path (Davinci only)
AGENT_MODELS = {"davinci": "companions/davinci/davinci.gguf"}

def run_idea_flow():
    # Initialize the flow properly
    start_flow("idea_generator", temp=0.8, model="davinci")
    print("✅ Started flow: idea_generator | model=davinci")

    ended = False
    try:
        history = []
        while True:
            user_input = input("> ").rstrip("\n")
            if not user_input.strip():
                continue
            # allow exiting without sudo
            if user_input.strip().lower() in ("exit", "quit"):
                break

            # 🔐 Intercept sudo commands locally (no model call)
            signal = check_for_sudo_commands(
                user_input=user_input,
                flow_context=FLOW_CONTEXT,
                vector_state=VECTOR_SEARCH_STATE,
                last_hits=LAST_VECTOR_HITS,
                agent_profiles=AGENT_PROFILES,
                flows_folder=Path(__file__).resolve().parent,
                memory_root=Path(__file__).resolve().parents[1] / "memory",
            )
            if signal is not None:
                # Control-plane signals from router
                # - END: 'sudo end flow' (router already journals & ends)
                # - SESSION_JOURNAL: write journals then end
                if signal == END or signal == SESSION_JOURNAL:
                    ended = True
                    break
                # START_PREFIX + name: start/switch hints
                if isinstance(signal, str) and signal.startswith(START_PREFIX):
                    print(f"{signal}")
                    continue
                # REFLECT/NOOP/etc → already handled by router; continue loop
                continue

            # Not a sudo cmd → call the model
            davinci_output = run_agent_with_runtime(
                "davinci",
                user_input,
                model_path=AGENT_MODELS["davinci"],
            )
            if davinci_output:
                print(davinci_output)

            history.append(f"User: {user_input}")
            history.append(f"Davinci: {davinci_output}")
    finally:
        # Avoid double-ending if the router already called end_flow()
        if not ended:
            end_flow()


# 🔁 Manual test entry point
if __name__ == "__main__":
    run_idea_flow()

# Back-compat alias so flows package can import .run
run = run_idea_flow


