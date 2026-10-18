"""Úklid stromu procesů agenta (Linux, best-effort).

Agent (claude/opencode) běží ve vlastní session a jeho Bash tool spouští příkazy
v dalších vlastních sessions. ``killpg`` skupiny agenta je proto nezasáhne: po
smrti agenta by přešly pod init a běžely dál (typicky ``ultracode run`` i se
všemi svými subagenty). Proto:

- :func:`become_subreaper` — osiřelí potomci se přesunou pod nás, ne pod init,
  takže je po smrti agenta pořád najdeme ve stromu pod naším PID;
- :func:`terminate_descendants` — SIGTERM všem potomkům, krátká lhůta na úklid,
  pak SIGKILL zbytku;
- :func:`die_with_parent` — PDEATHSIG na nás samotné (pro volající, kteří nás
  mohou ztratit tvrdým SIGKILL, např. ultracode).

Mimo Linux (bez ``prctl`` a ``/proc``) se nic neděje.
"""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import time
from typing import Dict, List, Set

_PR_SET_PDEATHSIG = 1
_PR_SET_CHILD_SUBREAPER = 36

#: Jak často se během lhůty kontroluje, jestli potomci už skončili.
_POLL_SEC = 0.05


def _prctl(option: int, value: int) -> bool:
    if not sys.platform.startswith("linux"):
        return False
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        return libc.prctl(option, value, 0, 0, 0) == 0
    except Exception:
        return False


def become_subreaper() -> bool:
    """Osiřelí potomci se přesunou pod tento proces místo pod init."""
    return _prctl(_PR_SET_CHILD_SUBREAPER, 1)


def die_with_parent(signum: int = signal.SIGTERM) -> bool:
    """Po smrti rodiče (i tvrdým SIGKILL) dostaneme ``signum``.

    Když rodič umřel už před nastavením (race), pošleme si signál hned.
    """
    parent = os.getppid()
    if not _prctl(_PR_SET_PDEATHSIG, signum):
        return False
    if os.getppid() != parent:
        os.kill(os.getpid(), signum)
    return True


def _ppid_map() -> Dict[int, int]:
    """PID → PPID všech procesů z ``/proc`` (prázdné mimo Linux)."""
    out: Dict[int, int] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return out
    for name in entries:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as fh:
                stat = fh.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        # comm v závorkách může obsahovat mezery i závorky → parsujeme až za poslední ')'.
        fields = stat[stat.rfind(")") + 2 :].split()
        if len(fields) < 2:
            continue
        with contextlib.suppress(ValueError):
            out[int(name)] = int(fields[1])
    return out


def descendants(root: int) -> List[int]:
    """Všichni (živí) potomci ``root`` podle ``/proc``."""
    children: Dict[int, List[int]] = {}
    for pid, ppid in _ppid_map().items():
        children.setdefault(ppid, []).append(pid)
    found: List[int] = []
    seen: Set[int] = {root}
    stack = [root]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in seen:
                seen.add(child)
                found.append(child)
                stack.append(child)
    return found


def _signal_all(pids: List[int], signum: int) -> None:
    for pid in pids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signum)


def _reap_zombies() -> None:
    """Jako subreaper musíme sklidit adoptované sirotky, jinak zůstanou jako zombie."""
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def terminate_descendants(grace_sec: float) -> int:
    """SIGTERM všem potomkům tohoto procesu, po ``grace_sec`` SIGKILL zbytku.

    Synchronní (blokuje nejvýš ``grace_sec``). Vrací počet procesů, které
    dostaly SIGTERM. Volat až po ukončení vlastního agenta, jinak bychom
    sklízeli i jeho a čekali na něj.
    """
    me = os.getpid()
    initial = descendants(me)
    if not initial:
        return 0
    _signal_all(initial, signal.SIGTERM)
    deadline = time.monotonic() + max(grace_sec, 0.0)
    while time.monotonic() < deadline:
        _reap_zombies()
        if not _alive(descendants(me)):
            return len(initial)
        time.sleep(_POLL_SEC)
    # Kdo nestihl skončit (nebo se mezitím narodil), jde dolů tvrdě.
    _signal_all(descendants(me), signal.SIGKILL)
    _reap_zombies()
    return len(initial)


def _alive(pids: List[int]) -> bool:
    """Je mezi ``pids`` aspoň jeden proces, který není zombie?"""
    for pid in pids:
        try:
            with open(f"/proc/{pid}/stat", "rb") as fh:
                stat = fh.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        state = stat[stat.rfind(")") + 2 :].split(" ", 1)[0]
        if state != "Z":
            return True
    return False


__all__ = ["become_subreaper", "die_with_parent", "descendants", "terminate_descendants"]
