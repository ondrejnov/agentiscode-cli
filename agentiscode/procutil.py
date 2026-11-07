"""Spouštění a zabíjení procesu agenta napříč platformami.

POSIX: ``bash -c "exec <cmd>"`` ve vlastní session (``start_new_session``), kill celé
process group přes ``killpg``. Windows: nemá ``preexec_fn``, process groups ani
``SIGKILL`` a ``bash`` by CreateProcess našel jako WSL spouštěč v ``System32`` —
příkaz se proto spustí přímo (``shutil.which`` najde i ``.cmd``/``.exe``) v nové
process group a strom se zabíjí přes ``taskkill /T /F``.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import shutil
import signal
import subprocess
from typing import Any, Callable, Dict, List, Optional, Sequence

IS_WINDOWS = os.name == "nt"

#: Windows nemá SIGKILL; ``Popen.send_signal(SIGTERM)`` tam volá TerminateProcess.
SIGKILL: int = getattr(signal, "SIGKILL", signal.SIGTERM)


def command_argv(command: str, args: Sequence[str]) -> List[str]:
    """Argv pro spuštění ``command args`` (POSIX přes ``bash -c exec``, Windows přímo)."""
    if IS_WINDOWS:
        return [shutil.which(command) or command, *args]
    return ["bash", "-c", "exec " + shlex.join([command, *args])]


def spawn_kwargs(preexec_fn: Optional[Callable[[], None]] = None) -> Dict[str, Any]:
    """Kwargs pro ``create_subprocess_exec``, aby šel proces zabít i s potomky."""
    if IS_WINDOWS:
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)}
    kwargs: Dict[str, Any] = {"start_new_session": True}
    if preexec_fn is not None:
        kwargs["preexec_fn"] = preexec_fn
    return kwargs


def signal_group(proc: Any, signum: int) -> None:
    """Pošle ``signum`` process group procesu; na Windows SIGKILL = ``taskkill /T /F``."""
    if proc is None or proc.returncode is not None:
        return
    if IS_WINDOWS:
        if signum == SIGKILL:
            kill_tree(proc)
            return
        with contextlib.suppress(Exception):
            proc.send_signal(signum)
        return
    with contextlib.suppress(Exception):
        os.killpg(os.getpgid(proc.pid), signum)
        return
    with contextlib.suppress(Exception):
        proc.send_signal(signum)


def kill_tree(proc: Any) -> None:
    """Tvrdě ukončí proces i jeho potomky."""
    if proc is None or proc.returncode is not None:
        return
    if IS_WINDOWS:
        with contextlib.suppress(Exception):
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    else:
        with contextlib.suppress(Exception):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            return
    with contextlib.suppress(Exception):
        proc.kill()
