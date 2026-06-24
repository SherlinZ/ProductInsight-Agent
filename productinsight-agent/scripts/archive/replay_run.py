from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default="run_demo_ai_agent_001")
    args = parser.parse_args()
    print(f"Replay run loaded: {args.run_id}")


if __name__ == "__main__":
    main()
