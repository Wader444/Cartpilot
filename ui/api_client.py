import os
import httpx
from mock_data import SAMPLE_PROPOSAL

USE_MOCK = os.getenv("USE_MOCK", "1") == "1"
API_URL = os.getenv("API_URL", "http://localhost:8000")

def get_proposal(goal: str) -> dict:
    if USE_MOCK:
        return {**SAMPLE_PROPOSAL, "goal": goal or SAMPLE_PROPOSAL["goal"]}
    resp = httpx.post(f"{API_URL}/proposal", json={"goal": goal}, timeout=30)  # path: confirm with Cherish
    resp.raise_for_status()
    return resp.json()