"""Run with the portable executable, not a separately installed Python.

The PowerShell driver copies this file inside the extracted application's .runtime.
Only synthetic diagnostics are used; reports never contain API keys or responses.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request


async def session_checks(port):
    from tornado.httpclient import HTTPRequest
    from tornado.websocket import websocket_connect
    from streamlit.proto.BackMsg_pb2 import BackMsg
    from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
    connection = await websocket_connect(HTTPRequest(
        f'ws://127.0.0.1:{port}/_stcore/stream',
        headers={'Origin': f'http://127.0.0.1:{port}'}), subprotocols=['streamlit'])
    async def render(widget=None, value=None):
        back = BackMsg()
        back.rerun_script.query_string = ''
        if widget:
            state = back.rerun_script.widget_states.widgets.add()
            state.id = widget.id
            if type(value) is bool:
                state.trigger_value = value
            else:
                state.int_value = value
        connection.write_message(back.SerializeToString(), binary=True)
        elements, count = [], 0
        while True:
            raw = await asyncio.wait_for(connection.read_message(), timeout=120)
            assert raw is not None, 'Session closed before script completion'
            forward = ForwardMsg()
            forward.ParseFromString(raw)
            count += 1
            if forward.HasField('delta') and forward.delta.HasField('new_element'):
                element = forward.delta.new_element
                assert not element.HasField('exception'), 'Application session raised an exception'
                elements.append(element)
            if forward.HasField('script_finished'):
                if forward.script_finished == ForwardMsg.FINISHED_EARLY_FOR_RERUN:
                    continue
                assert forward.script_finished == ForwardMsg.FINISHED_SUCCESSFULLY, 'Script did not finish successfully'
                break
        assert count > 5
        return elements, count
    try:
        elements, count = await render()
        pages = {'startup': count}
        navigation = next(e.radio for e in elements if e.HasField('radio') and e.radio.id.endswith('-nav_group_a'))
        elements, count = await render(navigation, 3)
        pages['browse'] = count
        mode = next(e.radio for e in elements if e.HasField('radio') and e.radio.id.endswith('-browse_mode'))
        elements, count = await render(mode, 2)
        pages['chronological_browse'] = count
        api = next(e.button for e in elements if e.HasField('button') and e.button.id.endswith('-mc-nav-bridge-9-1'))
        elements, count = await render(api, True)
        pages['api_settings'] = count
        assert any(e.HasField('text_input') for e in elements), 'API settings did not render inputs'
        return pages
    finally:
        connection.close()


def main():
    root = Path(sys.executable).resolve().parent
    report = {'version': (root/'VERSION').read_text().strip(),
              'frozen_python': bool(getattr(sys, 'frozen', False)), 'checks': {}}
    assert report['frozen_python'], 'Use UtopiaPhyCyclus.exe to execute this script'
    for filename, key in [('UtopiaPhyCyclus.exe', 'executable_sha256'), ('runtime-manifest.json', 'runtime_manifest_sha256')]:
        with (root/filename).open('rb') as source:
            report[key] = hashlib.file_digest(source, 'sha256').hexdigest()
    report['external_python_needed'] = False
    process = subprocess.Popen([str(root/'UtopiaPhyCyclus.exe'), '--no-browser', '--port=18501'],
                               cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(180):
            try:
                with opener.open('http://127.0.0.1:18501/_stcore/health', timeout=2) as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
            assert process.poll() is None, 'Application exited during startup'
            time.sleep(1)
        else:
            raise RuntimeError('Application health timeout')
        report['checks']['http_health'] = True
        report['checks']['websocket_pages'] = asyncio.run(session_checks(18501))
        from services.browse_preferences import load_browse_mode
        assert load_browse_mode() == '按录入顺序浏览'
        report['checks']['browse_preference_saved'] = True
        from utils.csv_ops import read_csv_index
        assert read_csv_index() == []
        assert not list((root/'chapters').rglob('*.tex'))
        report['checks']['empty_question_bank'] = True
        if os.environ.get('UTOPIA_VALIDATE_API') == '1':
            from services.ai_service import test_model_availability
            result = test_model_availability(os.environ['AI_BASE_URL'], os.environ['AI_API_KEY'],
                                             os.environ['AI_MODEL_NAME'], timeout=240)
            # Do not copy raw provider errors or configuration into public reports.
            report['checks']['configured_api'] = {k: result[k] for k in ('ok', 'status_code', 'latency_ms') if k in result}
            assert result['ok'], 'Configured API model probe failed (details kept private)'
        report['passed'] = True
    except Exception as exc:
        report['passed'] = False
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
        (root/'.runtime/clean-windows-app-report.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
