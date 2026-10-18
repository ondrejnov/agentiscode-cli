"""Úklid potomků agenta při ukončení signálem (reálné procesy, jen Linux)."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="prctl + /proc jen na Linuxu")

REPO = Path(__file__).resolve().parents[1]

# Falešný `claude`: jako Bash tool Claude Code spustí potomka ve vlastní session
# (killpg agenta ho nezasáhne), zapíše jeho PID a čeká.
FAKE_CLAUDE = """#!/usr/bin/env bash
cat >/dev/null
setsid sleep 60 </dev/null >/dev/null 2>&1 &
echo $! > "$ORPHAN_PID_FILE"
trap 'exit 143' TERM
sleep 60 &
wait
"""


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as fh:
            state = fh.read().rsplit(")", 1)[1].split()[0]
    except OSError:
        return False
    return state != "Z"


def _wait_for(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture()
def env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text(FAKE_CLAUDE)
    claude.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "PYTHONPATH": str(REPO),
        "ORPHAN_PID_FILE": str(tmp_path / "orphan.pid"),
    }


def _orphan_pid(env: dict[str, str]) -> int:
    pid_file = Path(env["ORPHAN_PID_FILE"])
    assert _wait_for(lambda: pid_file.exists() and pid_file.read_text().strip() != ""), "fake claude nestartoval"
    return int(pid_file.read_text())


def test_sigterm_kills_agent_descendants_in_foreign_session(env: dict[str, str]) -> None:
    proc = subprocess.Popen(
        [sys.executable, "-m", "agentiscode", "--adapter", "claude", "ahoj"],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    orphan = _orphan_pid(env)
    try:
        assert _alive(orphan)
        started = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10) == 143
        # Musí se vejít do 5s lhůty agentis-workflow před SIGKILL.
        assert time.monotonic() - started < 5.0
        assert _wait_for(lambda: not _alive(orphan), timeout=3.0), "potomek agenta přežil"
    finally:
        with_suppress_kill(orphan)
        if proc.poll() is None:
            proc.kill()


def test_die_with_parent_terminates_after_parent_sigkill(env: dict[str, str]) -> None:
    env = {**env, "AGENTISCODE_DIE_WITH_PARENT": "1"}
    # Rodič, který umře tvrdě (SIGKILL) a nemá šanci nic uklidit.
    parent = subprocess.Popen(
        ["bash", "-c", f'"{sys.executable}" -m agentiscode --adapter claude ahoj </dev/null >/dev/null 2>&1 & wait'],
        env=env,
        start_new_session=True,
    )
    orphan = _orphan_pid(env)
    try:
        assert _alive(orphan)
        parent.kill()
        parent.wait(timeout=5)
        assert _wait_for(lambda: not _alive(orphan), timeout=8.0), "potomek agenta přežil smrt rodiče"
    finally:
        with_suppress_kill(orphan)
        with_suppress_kill_group(parent.pid)


def with_suppress_kill(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def with_suppress_kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
