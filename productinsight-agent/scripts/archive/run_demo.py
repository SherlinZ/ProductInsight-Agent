from __future__ import annotations

import subprocess
import sys


def main() -> None:
    subprocess.check_call([sys.executable, "scripts/seed_demo_data.py"])
    print("Demo data is ready.")
    print("Start backend: uvicorn backend.app.main:app --reload --port 8000")
    print("Start frontend: streamlit run frontend/app.py")


if __name__ == "__main__":
    main()
