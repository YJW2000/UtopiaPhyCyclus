"""Verify an empty-bank source copy and the frozen Windows application's real session."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def source_test():
    from streamlit.testing.v1 import AppTest
    app = ROOT / '.release-work' / 'verification-source'
    if app.exists():
        if not app.resolve().is_relative_to((ROOT / '.release-work').resolve()):
            raise RuntimeError('Unsafe verification directory')
        shutil.rmtree(app)
    shutil.copytree(ROOT / '.release-work/source', app, ignore=shutil.ignore_patterns('.git'))
    os.chdir(app)
    sys.path.insert(0, str(app))
    os.environ['PI_AGENT_DISABLE_PLUGINS'] = '0'
    at = AppTest.from_file(str(app / 'utopia_phycyclus_app.py'), default_timeout=60).run()
    if at.exception:
        raise AssertionError([e.message for e in at.exception])
    from utils.core_config import BASE_DIR
    assert Path(BASE_DIR).resolve() == app.resolve(), BASE_DIR
    assert not list((app / 'chapters').rglob('*.tex'))
    from utils.csv_ops import read_csv_index
    assert read_csv_index() == []
    print(json.dumps({'empty_bank_startup': 'passed', 'startup_exceptions': 0, 'question_count': 0}))


async def frozen_session(port):
    from tornado.websocket import websocket_connect
    from tornado.httpclient import HTTPRequest
    from streamlit.proto.BackMsg_pb2 import BackMsg
    from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
    request = HTTPRequest(f'ws://127.0.0.1:{port}/_stcore/stream', headers={'Origin': f'http://127.0.0.1:{port}'}, connect_timeout=10)
    connection = await websocket_connect(request, subprotocols=['streamlit'])
    message = BackMsg()
    message.rerun_script.query_string = ''
    connection.write_message(message.SerializeToString(), binary=True)
    messages, exceptions = 0, []
    try:
        while True:
            raw = await asyncio.wait_for(connection.read_message(), timeout=60)
            if raw is None:
                raise RuntimeError('Frozen session disconnected before script completion')
            forward = ForwardMsg()
            forward.ParseFromString(raw)
            messages += 1
            if forward.HasField('delta') and forward.delta.HasField('new_element') and forward.delta.new_element.HasField('exception'):
                exceptions.append(forward.delta.new_element.exception.message)
            if forward.HasField('script_finished'):
                break
    finally:
        connection.close()
    if exceptions:
        raise AssertionError(exceptions)
    assert messages > 5, messages
    return messages


def frozen_test():
    original = ROOT / '.release-work/frozen/UtopiaPhyCyclus'
    app = ROOT / '.release-work/verification-frozen'
    if app.exists():
        if not app.resolve().is_relative_to((ROOT / '.release-work').resolve()):
            raise RuntimeError('Unsafe verification directory')
        shutil.rmtree(app)
    shutil.copytree(original, app)
    executable = app / 'UtopiaPhyCyclus.exe'
    result = subprocess.run([str(executable), '--check'], cwd=app, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    print(result.stdout.strip())
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    log_path = ROOT / '.release-work/frozen-smoke.log'
    with log_path.open('w', encoding='utf-8') as log:
        proc = subprocess.Popen([str(executable), '--no-browser', '--port', str(port)], cwd=app,
                                stdout=log, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise RuntimeError(log_path.read_text(encoding='utf-8'))
                try:
                    with opener.open(f'http://127.0.0.1:{port}/_stcore/health', timeout=1) as response:
                        if response.read() == b'ok':
                            break
                except OSError:
                    time.sleep(.2)
            else:
                raise TimeoutError('Frozen HTTP health did not become ready')
            messages = asyncio.run(frozen_session(port))
            assert not list((app / 'chapters').rglob('*.tex'))
            print(json.dumps({'frozen_health': 'passed', 'real_websocket_session': 'passed',
                              'session_messages': messages, 'startup_exceptions': 0}))
        finally:
            proc.terminate()
            proc.wait(timeout=15)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('kind', choices=['source', 'frozen'])
    args = parser.parse_args()
    (source_test if args.kind == 'source' else frozen_test)()
