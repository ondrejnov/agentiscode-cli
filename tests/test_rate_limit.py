from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from agentiscode.cli import EXIT_RATE_LIMITED, run
from agentiscode.core import AgentConfig, AgentEvent, AgentWrapper
from agentiscode.rate_limit import parse_resets_at
from agentiscode.telemetry import AgentisTelemetry
from tests.test_agentis_telemetry import FakeClient
from tests.test_agentiscode import _fake_subprocess

RESETS_AT = 4102444800  # 2100-01-01T00:00:00Z


def _line(payload: dict[str, Any]) -> str:
    return json.dumps(payload) + "\n"


def _init() -> str:
    return _line({"type": "system", "subtype": "init", "session_id": "s1", "model": "claude-x", "cwd": "/w"})


def _rate_limit_event(status: str, *, overage: bool = False) -> str:
    return _line(
        {
            "type": "rate_limit_event",
            "session_id": "s1",
            "rate_limit_info": {
                "status": status,
                "resetsAt": RESETS_AT,
                "rateLimitType": "five_hour",
                "isUsingOverage": overage,
            },
        }
    )


def _limit_message(**extra: Any) -> str:
    return _line(
        {
            "type": "assistant",
            "session_id": "s1",
            "message": {
                "id": "m1",
                "model": "<synthetic>",
                "content": [{"type": "text", "text": "You've hit your session limit · resets 3:40pm (Europe/Prague)"}],
            },
            "error": "rate_limit",
            **extra,
        }
    )


def _result(is_error: bool) -> str:
    return _line({"type": "result", "session_id": "s1", "is_error": is_error, "result": "x"})


def _collect(monkeypatch, lines: list[str], adapter: str = "claude") -> list[AgentEvent]:
    monkeypatch.setattr("agentiscode.runners.claude.asyncio.create_subprocess_exec", _fake_subprocess(lines))

    async def collect() -> list[AgentEvent]:
        wrapper = AgentWrapper(AgentConfig(adapter=adapter, cwd="/w"))
        return [event async for event in wrapper.stream("Do X")]

    return asyncio.run(collect())


def test_parse_resets_at_from_limit_messages() -> None:
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)  # 14:00 v Praze
    today = parse_resets_at("You've hit your session limit · resets 3:40pm (Europe/Prague)", now=now)
    assert datetime.fromtimestamp(today, timezone.utc) == datetime(2026, 10, 23, 13, 40, tzinfo=timezone.utc)
    tomorrow = parse_resets_at("You've hit your session limit · resets 2am (Europe/Prague)", now=now)
    assert datetime.fromtimestamp(tomorrow, timezone.utc) == datetime(2026, 10, 24, 0, 0, tzinfo=timezone.utc)
    dated = parse_resets_at("You've hit your weekly limit · resets Oct 25, 5pm (UTC)", now=now)
    assert datetime.fromtimestamp(dated, timezone.utc) == datetime(2026, 10, 25, 17, 0, tzinfo=timezone.utc)
    assert parse_resets_at("Claude AI usage limit reached|1792590000") == 1792590000
    assert parse_resets_at("resets 3pm (Not/AZone)") is None
    assert parse_resets_at("all good") is None


def test_wrapper_marks_result_rate_limited(monkeypatch) -> None:
    events = _collect(monkeypatch, [_init(), _rate_limit_event("rejected"), _limit_message(), _result(True)])

    assert [e.type for e in events] == ["session", "rate_limit", "text", "rate_limit", "result"]
    assert events[1].data == {
        "status": "rejected",
        "resets_at": RESETS_AT,
        "rate_limit_type": "five_hour",
        "is_using_overage": False,
    }
    result = events[-1].data
    assert result["rate_limited"] is True
    assert result["rate_limit_resets_at"] == "2100-01-01T00:00:00Z"
    assert result["rate_limit_type"] == "five_hour"


def test_wrapper_ignores_rejected_limit_when_run_succeeds(monkeypatch) -> None:
    events = _collect(monkeypatch, [_init(), _rate_limit_event("rejected", overage=True), _result(False)])
    assert "rate_limited" not in events[-1].data

    events = _collect(monkeypatch, [_init(), _rate_limit_event("allowed"), _result(True)])
    assert "rate_limited" not in events[-1].data


def test_wrapper_reads_claude_p_transcript_limit_message(monkeypatch) -> None:
    info = {"status": "rejected", "resetsAt": RESETS_AT, "rateLimitType": "seven_day", "isUsingOverage": False}
    lines = [
        _line({"type": "mode", "sessionId": "s1"}),
        _limit_message(apiError="usage_limit_reached", apiErrorParams={"rate_limit_info": info}),
        _result(True),
    ]
    result = _collect(monkeypatch, lines, adapter="claude-p")[-1].data
    assert result["rate_limited"] is True
    assert result["rate_limit_resets_at"] == "2100-01-01T00:00:00Z"
    assert result["rate_limit_type"] == "seven_day"


def test_wrapper_falls_back_to_reset_time_in_message_text(monkeypatch) -> None:
    result = _collect(monkeypatch, [_init(), _limit_message(), _result(True)])[-1].data
    assert result["rate_limited"] is True
    assert result["rate_limit_resets_at"].endswith("Z")


def test_telemetry_schedule_retry_moves_task_to_todo() -> None:
    client = FakeClient()
    telemetry = AgentisTelemetry(task_id="task-1", prompt="p", adapter="claude", client=client)

    assert telemetry.schedule_retry(datetime(2100, 1, 1, 1, 1)) is True
    assert client.params_for("task.save") == {
        "data": {"id": "task-1", "status": 2, "scheduled_at": "2100-01-01T01:01:00"}
    }


def test_cli_reschedules_task_after_rate_limit(monkeypatch) -> None:
    lines = [_init(), _rate_limit_event("rejected"), _limit_message(), _result(True)]
    monkeypatch.setattr("agentiscode.runners.claude.asyncio.create_subprocess_exec", _fake_subprocess(lines))
    calls: list[str] = []

    class FakeTelemetry:
        task_id = "task-1"

        def __init__(self, **kwargs: Any) -> None:
            pass

        def start(self) -> str:
            return "run-1"

        def handle(self, event: AgentEvent) -> None:
            pass

        def finish(self) -> None:
            calls.append("finish")

        def schedule_retry(self, scheduled_at: datetime) -> bool:
            calls.append(f"schedule {scheduled_at.isoformat()}")
            return True

        def close(self) -> None:
            pass

    monkeypatch.setattr("agentiscode.cli.AgentisTelemetry", FakeTelemetry)

    exit_code = run(
        [
            "--adapter",
            "claude",
            "--task-id",
            "task-1",
            "--agentis-api",
            "http://agentis.local/api",
            "--agentis-timezone",
            "Europe/Prague",
            "udelej",
        ]
    )

    assert exit_code == EXIT_RATE_LIMITED
    # Až po finish(): `failed` run by task přepnul do In Review.
    assert calls == ["finish", "schedule 2100-01-01T01:01:00"]
