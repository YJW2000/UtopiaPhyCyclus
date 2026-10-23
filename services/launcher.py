"""Offline fast bootstrap; reuse only an identified process owned by this project."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser


def environment_fingerprint(root):
    payload = (root/'requirements.txt').read_bytes()
    payload += repr((sys.version, str(Path(sys.executable).resolve()), str(root.resolve()))).encode()
    return hashlib.sha256(payload).hexdigest()


def dependencies_ready(root, marker):
    try:
        if json.loads(marker.read_text(encoding='utf-8'))['fingerprint'] != environment_fingerprint(root):
            return False
        for line in (root/'requirements.txt').read_text(encoding='utf-8').splitlines():
            line = line.split('#', 1)[0].strip()
            if line:
                name, version = line.split('==', 1)
                if importlib.metadata.version(name.strip()) != version.strip():
                    return False
        return True
    except (OSError, ValueError, KeyError, importlib.metadata.PackageNotFoundError):
        return False


def healthy(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=.4) as r:
            return r.status == 200 and r.read(64).strip() == b'ok'
    except Exception:
        return False


def process_identity(pid):
    """Process creation time also protects against PID reuse. Never kills processes."""
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)]*4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        creation, exit_, system, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_), ctypes.byref(system), ctypes.byref(user)):
            return None
        return (creation.dwHighDateTime << 32) | creation.dwLowDateTime
    finally:
        kernel.CloseHandle(handle)


def reusable_port(root, state):
    try:
        if state['root'] != str(root.resolve()) or state['python'] != str(Path(sys.executable).resolve()):
            return None
        identity = process_identity(int(state['pid']))
        if identity is None or identity != state['identity']:
            return None
        port = int(state['port'])
        return port if healthy(port) else None
    except (KeyError, TypeError, ValueError):
        return None


def available_port():
    for port in range(8501, 8511):
        with socket.socket() as probe:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind(('0.0.0.0', port))
                return port
            except OSError:
                pass
    raise RuntimeError('Ports 8501-8510 are busy.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repair', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    from services.portable_runtime import configure
    configure(root)
    os.chdir(root)
    runtime = root/'.runtime'
    runtime.mkdir(exist_ok=True)
    marker, state_path = runtime/'dependencies.json', runtime/'server.json'
    if args.repair or not dependencies_ready(root, marker):
        print('[SETUP] Synchronizing dependencies...', flush=True)
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '-r', str(root/'requirements.txt')], check=True)
        temp = marker.with_suffix('.tmp')
        temp.write_text(json.dumps(dict(fingerprint=environment_fingerprint(root))), encoding='utf-8')
        os.replace(temp, marker)
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = {}
    port = reusable_port(root, state)
    if port:
        webbrowser.open(f'http://localhost:{port}')
        print(f'[READY] Reusing this project at http://localhost:{port}')
        return
    port = available_port()
    child = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', str(root/'utopia_phycyclus_app.py'),
        f'--server.port={port}', '--server.headless=true',
        # 代码/样式改动后自动热更新：轮询监听（不依赖 watchdog）+ 自动重跑，
        # 避免服务进程缓存旧脚本、只在浏览器里刷新始终看不到新界面。
        '--server.fileWatcherType=poll', '--server.runOnSave=true'], cwd=root)
    started = time.perf_counter()
    try:
        while time.perf_counter()-started < 40:
            if child.poll() is not None:
                raise RuntimeError(f'Service exited with code {child.returncode}')
            if healthy(port):
                state_path.write_text(json.dumps(dict(root=str(root.resolve()), python=str(Path(sys.executable).resolve()),
                    pid=child.pid, identity=process_identity(child.pid), port=port)), encoding='utf-8')
                print(f'[READY] http://localhost:{port} ({time.perf_counter()-started:.2f}s)', flush=True)
                webbrowser.open(f'http://localhost:{port}')
                break
            time.sleep(.1)
        else:
            raise RuntimeError('Service health check timed out')
        raise SystemExit(child.wait())
    finally:
        if child.poll() is None:
            child.terminate()


if __name__ == '__main__':
    main()
