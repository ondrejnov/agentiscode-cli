from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentiscode import procutil
from agentiscode.runners.claude import ClaudeCodeClient, ClaudeRunConfig


def test_command_argv_posix_wraps_in_bash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(procutil, "IS_WINDOWS", False)
    assert procutil.command_argv("claude", ["--print", "a b"]) == ["bash", "-c", "exec claude --print 'a b'"]
    kwargs = procutil.spawn_kwargs(lambda: None)
    assert kwargs["start_new_session"] is True and callable(kwargs["preexec_fn"])


def test_windows_runs_command_directly_without_preexec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(procutil, "IS_WINDOWS", True)
    monkeypatch.setattr(procutil.shutil, "which", lambda name: r"C:\npm\claude.cmd" if name == "claude" else None)

    assert procutil.command_argv("claude", ["--print", "-"]) == [r"C:\npm\claude.cmd", "--print", "-"]
    kwargs = procutil.spawn_kwargs(lambda: None)
    assert set(kwargs) == {"creationflags"}


def test_windows_kill_uses_taskkill(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(procutil, "IS_WINDOWS", True)
    calls: list[list[str]] = []
    monkeypatch.setattr(procutil.subprocess, "run", lambda argv, **_: calls.append(argv))

    class _Proc:
        pid = 42
        returncode = None
        killed = False

        def kill(self) -> None:
            self.killed = True

    proc = _Proc()
    procutil.signal_group(proc, procutil.SIGKILL)

    assert calls == [["taskkill", "/T", "/F", "/PID", "42"]]
    assert proc.killed


def test_claude_stream_spawn_kwargs_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(procutil, "IS_WINDOWS", True)
    monkeypatch.setattr(procutil.shutil, "which", lambda name: None)
    captured: dict[str, Any] = {}

    async def fake_create_subprocess_exec(*args: Any, **kwargs: Any) -> Any:
        captured["args"], captured["kwargs"] = args, kwargs
        raise RuntimeError("stop")

    monkeypatch.setattr("agentiscode.runners.claude.asyncio.create_subprocess_exec", fake_create_subprocess_exec)

    async def consume() -> None:
        async for _event in ClaudeCodeClient(ClaudeRunConfig(cwd=".")).stream("ahoj"):
            pass

    with pytest.raises(RuntimeError, match="stop"):
        asyncio.run(consume())

    assert captured["args"][0] == "claude"
    assert "preexec_fn" not in captured["kwargs"] and "start_new_session" not in captured["kwargs"]
