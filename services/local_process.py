"""Run local tools with optional cancellation supplied by the desktop worker."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
import signal
import subprocess
import time


class TaskCancelled(Exception):
    pass


_cancel_event = ContextVar('local_process_cancel_event', default=None)


@contextmanager
def cancellable(event):
    token = _cancel_event.set(event)
    try:
        yield
    finally:
        _cancel_event.reset(token)


def check_cancelled():
    event = _cancel_event.get()
    if event is not None and event.is_set():
        raise TaskCancelled()


def kill_process_tree(process):
    if os.name == 'nt':
        try:
            subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run(command, *, input=None, capture_output=False, timeout=None, kill_tree_on_timeout=False, **kwargs):
    if _cancel_event.get() is None and not kill_tree_on_timeout:
        return subprocess.run(command, input=input, capture_output=capture_output, timeout=timeout, **kwargs)
    check_cancelled()
    if capture_output:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if input is not None:
        kwargs['stdin'] = subprocess.PIPE
    else:
        kwargs.setdefault('stdin', subprocess.DEVNULL)
    if os.name != 'nt':
        kwargs['start_new_session'] = True
    deadline = None if timeout is None else time.monotonic() + timeout
    with subprocess.Popen(command, **kwargs) as process:
        try:
            while True:
                check_cancelled()
                remaining = 0.15 if deadline is None else min(0.15, deadline - time.monotonic())
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    stdout, stderr = process.communicate(input, timeout=remaining)
                    check_cancelled()
                    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                except subprocess.TimeoutExpired as partial:
                    input = None
                    if deadline is not None and time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(command, timeout, partial.stdout, partial.stderr) from None
        except (TaskCancelled, subprocess.TimeoutExpired):
            kill_process_tree(process)
            raise
