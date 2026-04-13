#!/usr/bin/env python3
"""
test_gael_prompts.py
Run a dry GAEL single-pass to verify unified prompts.
"""

import os
from abilities.common_abilities import sudo_command_handler as sch
from abilities.common_abilities import situational_awareness_prompts as sap

def main():
    # Fake context and agent
    flow_context = {"flow_type": "test_flow"}
    agent = "davinci"

    print("=== Centralized GAEL Prompts Check ===")
    print("Summary prompt:\n", sap.get_gael_summary_prompt(flow_context))
    print("\nThoughts prompt:\n", sap.get_gael_thoughts_prompt(flow_context))
    print("\nDecisions prompt:\n", sap.get_gael_decisions_prompt(flow_context))

    # Check handler uses the same
    print("\n--- From sudo_command_handler ---")
    print("_prompt_summary:\n", sch._prompt_summary(flow_context))
    print("_prompt_thoughts:\n", sch._prompt_thoughts(flow_context))
    print("_prompt_decisions:\n", sch._prompt_decisions(flow_context))

if __name__ == "__main__":
    main()

