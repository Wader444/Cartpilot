"""Action log: every tool call and decision is recorded with id, tool, input,
output, timestamp, reason, status and duration. Tool calls run with a hard
timeout and bounded retries.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Callable, Optional

from pydantic import BaseModel

from app.core.models import ActionStatus, AgentAction, AgentState, Phase, utcnow

_executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="cartpilot-tool")


def to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k.value if hasattr(k, "value") else k): to_jsonable(v) for k, v in value.items()}
    if hasattr(value, "value") and not isinstance(value, (str, int, float, bool)):
        return value.value
    return value


class ToolCallError(Exception):
    def __init__(self, message: str, action: AgentAction):
        super().__init__(message)
        self.action = action


class TraceRecorder:
    def __init__(self, state: AgentState, timeout_s: float = 5.0, max_retries: int = 2,
                 backoff_s: float = 0.05):
        self.state = state
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.backoff_s = backoff_s

    @property
    def phase(self) -> Phase:
        return self.state.phase

    def note(self, tool: str, reason: str, output: Any = None, input: Optional[dict] = None,
             status: ActionStatus = ActionStatus.SUCCESS, phase: Optional[Phase] = None) -> AgentAction:
        """Record a decision / non-tool step (planner reasoning, re-plan, guardrail block)."""
        action = AgentAction(phase=phase or self.phase, tool=tool, input=to_jsonable(input or {}),
                             output=to_jsonable(output), reason=reason, status=status)
        self.state.trace.append(action)
        self.state.updated_at = utcnow()
        return action

    def call(self, tool: str, input: dict, fn: Callable[[], Any], reason: str,
             retry_on: tuple[type[BaseException], ...] = (Exception,),
             postprocess: Optional[Callable[[Any, AgentAction], Any]] = None,
             max_retries: Optional[int] = None) -> tuple[Any, AgentAction]:
        """Run a tool with timeout + retries. Returns (result, final_action). Raises ToolCallError."""
        attempts = (self.max_retries if max_retries is None else max_retries) + 1
        last_err: Optional[str] = None
        for attempt in range(1, attempts + 1):
            action = AgentAction(phase=self.phase, tool=tool, input=to_jsonable(input), reason=reason, attempt=attempt)
            t0 = time.perf_counter()
            try:
                result = _executor.submit(fn).result(timeout=self.timeout_s)
                if postprocess:
                    result = postprocess(result, action)
                action.output = to_jsonable(result)
                action.status = ActionStatus.SUCCESS
                action.duration_ms = round((time.perf_counter() - t0) * 1000, 2)
                self.state.trace.append(action)
                self.state.updated_at = utcnow()
                return result, action
            except FutureTimeout:
                last_err = f"timeout after {self.timeout_s}s"
            except retry_on as e:  # noqa: B030 – tuple of exception types
                last_err = f"{type(e).__name__}: {e}"
            action.duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            action.error = last_err
            action.status = ActionStatus.RETRIED if attempt < attempts else ActionStatus.FAILED
            self.state.trace.append(action)
            if attempt < attempts:
                time.sleep(self.backoff_s * attempt)
        raise ToolCallError(f"{tool} failed after {attempts} attempts: {last_err}", action)
