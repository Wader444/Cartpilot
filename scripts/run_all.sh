#!/usr/bin/env bash
# CartPilot – run everything in one go:
#   tests -> Member B data checks -> demo story -> 20-basket evaluation -> live API walkthrough -> UI
# Usage (from the project folder, with the virtualenv active):  bash scripts/run_all.sh
# Ctrl+C stops the UI and the API together.
set -u
cd "$(dirname "$0")/.."
B=http://127.0.0.1:8000
API_PID=""
cleanup() { [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null && echo "API stopped."; }
trap cleanup EXIT INT TERM

for port in 8000 8501; do
  pids=$(lsof -ti:"$port" 2>/dev/null || true)
  [ -n "$pids" ] && echo "Port $port is in use (pid $pids) – stop that process first." && exit 1
done

echo; echo "===== 1. TESTS ====="
python -m pytest -q 2>/dev/null | tail -1

echo; echo "===== 2. MEMBER B DATA CHECKS ====="
python integration_check.py | tail -1

echo; echo "===== 3. DEMO STORY ====="
python scripts/demo.py

echo; echo "===== 4. 20-BASKET EVALUATION (mock data) ====="
python scripts/evaluate.py | sed -n 3,18p

echo; echo "===== 5. LIVE API ====="
CARTPILOT_ENABLE_FAULTS=true CARTPILOT_FIXED_NOW=17:00 \
  python -m uvicorn app.main:app --port 8000 > /tmp/cartpilot_api.log 2>&1 &
API_PID=$!
for _ in $(seq 1 30); do curl -s "$B/health" >/dev/null 2>&1 && break; sleep 0.5; done
curl -s "$B/health" | python -c 'import sys,json;d=json.load(sys.stdin);print("health:",d["status"],"| optimizer:",d["optimizer"],"| matching:",d["matching"]);print(d["payments"])'
R=$(curl -s -X POST "$B/api/shopping/plan" -H 'Content-Type: application/json' \
      -d '{"text":"Weekly groceries for 4 people, vegetarian, under ₹2,500, delivered before 9 PM."}')
echo "$R" | python -c 'import sys,json;d=json.load(sys.stdin);print("phase:",d["phase"]);print(d["proposal_summary"])'
SID=$(echo "$R" | python -c 'import sys,json;print(json.load(sys.stdin)["session_id"])')
PID=$(echo "$R" | python -c 'import sys,json;print(json.load(sys.stdin)["proposal_id"])')
curl -s -X POST "$B/api/shopping/$SID/approve" -H 'Content-Type: application/json' \
     -d "{\"proposal_id\":\"$PID\",\"remind_in_days\":7}" \
  | python -c 'import sys,json;d=json.load(sys.stdin);print(d["message"]);[print("  ",c["cart_ref"],"payment_executed =",c["payment_executed"]) for c in d["session"]["prepared_carts"]]'

echo; echo "===== 6. UI ====="
echo "Sidebar: Load demo basket -> tick Inject demo faults -> Plan my cart -> Agent trace -> Approve"
echo "API docs: $B/docs   |   Ctrl+C here stops everything."
USE_MOCK=0 API_URL="$B" python -m streamlit run ui/app.py
