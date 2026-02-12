import sys
from pathlib import Path

# Allow direct script execution: `python scripts/run_slack_bot.py`
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

def main() -> None:
    from src.slack.app import main as run_main

    run_main()


if __name__ == "__main__":
    main()
