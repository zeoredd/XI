from pathlib import Path
from datetime import datetime
import subprocess

# === 📁 Setup paths ===
ROOT = Path("/home/node-alpha/XI")
SUMMARY_SCRIPT = ROOT / "abilities" / "common_abilities" / "write_all_summaries_then_archive.py"
AGENTS = ["davinci", "hermes", "sentinel"]
JOURNAL_TYPES = {
    "davinci": ["idea_generator_journal", "daily_briefing_and_strategy_journal"],
    "hermes": ["idea_generator_journal", "daily_briefing_and_strategy_journal"],
    "sentinel": ["sentinel_audit_journal"]
}

def run_summary_promotions():
    today_flag_path = ROOT / "runtime" / f".summary_run_{datetime.today().date()}"
    if today_flag_path.exists():
        print("🛑 Summary promotions already run today. Skipping...")
        return

    print("📚 Running daily summary promotions...")

    for agent in AGENTS:
        for journal_type in JOURNAL_TYPES[agent]:
            try:
                subprocess.run(
                    ["python3", str(SUMMARY_SCRIPT), "--agent", agent, "--journal_type", journal_type],
                    check=True
                )
            except subprocess.CalledProcessError as e:
                print(f"⚠️ Summary promotion failed for {agent}/{journal_type}: {e}")

    # Mark that summaries have been processed today
    today_flag_path.touch()
    print("✅ Daily summary promotions completed.")

# 🧪 Run manually or from routines_orchestrator
if __name__ == "__main__":
    run_summary_promotions()

