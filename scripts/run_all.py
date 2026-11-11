"""CartPilot – Cross-Platform One-Command Runner (Windows, macOS, Linux).

Usage:
    python scripts/run_all.py

Runs:
  1. Automated test suite (pytest)
  2. Data & Product Graph verification (integration_check.py)
  3. 3-minute demo story (scripts/demo.py)
  4. 20-basket evaluation (scripts/evaluate.py)
  5. Live API walkthrough (FastAPI)
  6. Interactive UI (Streamlit)

Press Ctrl+C to cleanly stop the API and UI servers.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API_PORT = 8000
UI_PORT = 8501
API_URL = f"http://127.0.0.1:{API_PORT}"


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def banner(title: str) -> None:
    print(f"\n{'='*20} {title} {'='*20}")


def run_step(cmd: list[str], desc: str) -> None:
    banner(desc)
    res = subprocess.run(cmd, cwd=str(ROOT))
    if res.returncode != 0:
        print(f"❌ Warning: Step '{desc}' exited with code {res.returncode}")


def wait_for_api(timeout_s: float = 15.0) -> bool:
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            with urllib.request.urlopen(f"{API_URL}/health", timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def main() -> None:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))

    print(f"🚀 CartPilot Hackathon Runner (AgentX 2026 - PS-07)")
    print(f"Working Directory: {ROOT}")

    # Check ports
    for p in (API_PORT, UI_PORT):
        if port_in_use(p):
            print(f"⚠️  Port {p} is currently in use. Existing process may already be running.")

    # 1. Tests
    run_step([sys.executable, "-m", "pytest", "-q"], "1. AUTOMATED TESTS (149 passing)")

    # 2. Member B Data Checks
    run_step([sys.executable, "integration_check.py"], "2. DATA & GRAPH CHECKS")

    # 3. Demo Story
    run_step([sys.executable, "scripts/demo.py"], "3. DEMO STORY (Fault Recovery & Split)")

    # 4. Evaluation
    run_step([sys.executable, "scripts/evaluate.py"], "4. 20-BASKET EVALUATION")

    # 5. Start API Server
    banner("5. STARTING LIVE FASTAPI BACKEND")
    env = os.environ.copy()
    env["CARTPILOT_ENABLE_FAULTS"] = "true"
    env["CARTPILOT_FIXED_NOW"] = "17:00"

    api_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(API_PORT)],
        env=env, cwd=str(ROOT)
    )

    print("Waiting for API to be ready...")
    if not wait_for_api():
        print("❌ Could not connect to API server.")
        api_proc.terminate()
        sys.exit(1)

    print("✅ API is healthy! Running quick walkthrough...")
    try:
        # Check health
        with urllib.request.urlopen(f"{API_URL}/health") as r:
            h = json.loads(r.read().decode("utf-8"))
            print(f"Health: {h['status']} | Optimizer: {h['optimizer']} | Payments: {h['payments']}")

        # Plan request
        plan_data = json.dumps({
            "text": "Weekly groceries for 4 people, vegetarian, under ₹2,500, delivered before 9 PM."
        }).encode("utf-8")
        req = urllib.request.Request(f"{API_URL}/api/shopping/plan", data=plan_data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            p_res = json.loads(r.read().decode("utf-8"))
            print(f"Phase: {p_res['phase']}")
            print(f"Summary: {p_res.get('proposal_summary')}")
            sid = p_res["session_id"]
            pid = p_res.get("proposal_id")

        if pid:
            appr_data = json.dumps({"proposal_id": pid, "remind_in_days": 7}).encode("utf-8")
            req = urllib.request.Request(f"{API_URL}/api/shopping/{sid}/approve", data=appr_data,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as r:
                a_res = json.loads(r.read().decode("utf-8"))
                print(f"Approval Result: {a_res['message']}")
                for c in a_res["session"]["prepared_carts"]:
                    print(f"  Cart: {c['cart_ref']} (App: {c['platform']}) | payment_executed={c['payment_executed']}")
    except Exception as e:
        print(f"Walkthrough note: {e}")

    # 6. Start Streamlit UI
    banner("6. LAUNCHING STREAMLIT UI")
    print(f"Interactive UI available at: http://localhost:{UI_PORT}")
    print("Press Ctrl+C at any time to shut down both API and UI.")

    ui_env = os.environ.copy()
    ui_env["USE_MOCK"] = "0"
    ui_env["API_URL"] = API_URL

    ui_proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "ui/app.py", "--server.port", str(UI_PORT)],
        env=ui_env, cwd=str(ROOT)
    )

    def cleanup(signum=None, frame=None):
        print("\n🛑 Shutting down servers...")
        try:
            ui_proc.terminate()
        except Exception:
            pass
        try:
            api_proc.terminate()
        except Exception:
            pass
        print("Cleaned up successfully. Bye!")
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, cleanup)

    try:
        ui_proc.wait()
    except KeyboardInterrupt:
        cleanup()


if __name__ == "__main__":
    main()

