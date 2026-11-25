"""Run a single ACP prompt in a child owned by the cancellable desktop worker."""
from __future__ import annotations

import json
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
from tempfile import TemporaryFile
from threading import Thread
from time import monotonic


def _read_grok_lines(stdout):
    lines = Queue()

    def read():
        try:
            for line in stdout:
                lines.put(line)
        finally:
            lines.put(None)

    Thread(target=read, daemon=True).start()
    return lines


def _set_restricted_mode(request, session, mode):
    session_id = session['sessionId']
    modes = session.get('modes') or {}
    if modes.get('currentModeId') == mode:
        return True
    if any(entry.get('id') == mode for entry in modes.get('availableModes', [])):
        request(3, 'session/set_mode', {'sessionId': session_id, 'modeId': mode})
        return True
    for option in session.get('configOptions') or []:
        if option.get('category') != 'mode' and option.get('id') not in {'mode', 'permission_mode'}:
            continue
        if option.get('currentValue') == mode:
            return True
        choices = [choice for group in option.get('options', []) for choice in group.get('options', [group])]
        if any(choice.get('value') == mode for choice in choices):
            params = {'sessionId': session_id, 'configId': option['id'], 'value': mode}
            result = request(3, 'session/set_config_option', params)
            if not result.get('configOptions') or any(
                    entry.get('id') == option['id'] and entry.get('currentValue') == mode
                    for entry in result['configOptions']):
                return True
            raise RuntimeError(f'Agent did not apply the required {mode} mode')
    return False


def _conversation(process, prompt, provider):
    chunks = []
    session_id = None
    grok_lines = _read_grok_lines(process.stdout) if provider == 'grok' else None
    lines = iter(grok_lines.get, None) if grok_lines is not None else process.stdout

    def send(payload):
        process.stdin.write((json.dumps({'jsonrpc': '2.0', **payload}, ensure_ascii=False) + '\n').encode('utf-8'))
        process.stdin.flush()

    def receive(line):
        try:
            message = json.loads(line)
        except (UnicodeError, ValueError):
            return None
        if not isinstance(message, dict):
            return None
        incoming = message.get('method')
        params = message.get('params') or {}
        if incoming == 'session/update' and session_id and params.get('sessionId', session_id) == session_id:
            update = params.get('update') or {}
            content = update.get('content') or {}
            if update.get('sessionUpdate') == 'agent_message_chunk' and content.get('type') == 'text':
                chunks.append(content['text'])
        if incoming and 'id' in message:
            if incoming == 'session/request_permission':
                options = params.get('options') or []
                deny = next((item['optionId'] for item in options if item.get('kind') == 'reject_once'), None)
                outcome = {'outcome': 'selected', 'optionId': deny} if deny else {'outcome': 'cancelled'}
                send({'id': message['id'], 'result': {'outcome': outcome}})
            else:
                send({'id': message['id'], 'error': {'code': -32601, 'message': 'Client method unavailable'}})
        return message if incoming is None else None

    def request(ident, method, params):
        send({'id': ident, 'method': method, 'params': params})
        for line in lines:
            message = receive(line)
            if not message or message.get('id') != ident:
                continue
            if 'error' in message:
                error = message['error']
                if isinstance(error, dict) and error.get('code') == -32000:
                    raise RuntimeError(f'Authentication or session configuration error: {error}')
                raise RuntimeError(str(error))
            return message['result']
        raise RuntimeError('Agent exited before returning a response')

    initialized = request(1, 'initialize', {
        'protocolVersion': 1, 'clientCapabilities': {},
        'clientInfo': {'name': 'utopia-phycyclus', 'version': '1'},
    })
    if initialized.get('protocolVersion') != 1:
        raise RuntimeError('Unsupported ACP protocol version')
    capabilities = initialized.get('agentCapabilities', {}).get('promptCapabilities', {})
    if any(block['type'] == 'image' for block in prompt) and not capabilities.get('image'):
        raise RuntimeError('Agent does not advertise image input')
    params = {'cwd': str(Path.cwd()), 'mcpServers': []}
    if provider == 'grok':
        methods = {method.get('id') for method in initialized.get('authMethods') or []}
        if os.environ.get('XAI_API_KEY') and 'xai.api_key' in methods:
            method = 'xai.api_key'
        elif 'cached_token' in methods:
            method = 'cached_token'
        else:
            raise RuntimeError('Grok authentication requires a saved login or XAI_API_KEY')
        request(6, 'authenticate', {'methodId': method, '_meta': {'headless': True}})
        params['_meta'] = {'yoloMode': False, 'autoMode': False}
    session = request(2, 'session/new', params)
    session_id = session['sessionId']
    if provider == 'copilot':
        modes = session.get('modes', {})
        agent_mode = 'https://agentclientprotocol.com/protocol/session-modes#agent'
        if modes.get('currentModeId') != agent_mode and any(
                mode.get('id') == agent_mode for mode in modes.get('availableModes', [])):
            request(3, 'session/set_mode', {'sessionId': session_id, 'modeId': agent_mode})
        if any(option.get('id') == 'allow_all' for option in session.get('configOptions', [])):
            request(4, 'session/set_config_option', {'sessionId': session_id, 'configId': 'allow_all', 'value': 'off'})
    elif provider == 'cursor' and session.get('modes', {}).get('currentModeId') != 'ask':
        request(3, 'session/set_mode', {'sessionId': session_id, 'modeId': 'ask'})
    elif provider in {'kimi', 'qwen'}:
        restricted = _set_restricted_mode(request, session, 'plan')
        # Qwen also pins Plan mode in its CLI arguments. Kimi must expose a
        # compatible native session mode so its personal defaults cannot win.
        if not restricted and (provider == 'kimi' or session.get('modes')):
            raise RuntimeError('Agent does not support the required plan mode')
    elif provider == 'deepseek':
        _set_restricted_mode(request, session, 'read-only')
    chunks.clear()
    result = request(5, 'session/prompt', {'sessionId': session_id, 'prompt': prompt})
    if grok_lines is not None and result.get('stopReason') == 'end_turn':
        # Some Grok versions deliver their final ACP updates after the response.
        deadline = monotonic() + 1
        while (remaining := deadline - monotonic()) > 0:
            try:
                line = grok_lines.get(timeout=min(remaining, 0.3))
            except Empty:
                break
            if line is None:
                break
            receive(line)
    if result.get('stopReason') != 'end_turn' or not chunks:
        raise RuntimeError(f'Agent did not complete the response: {result.get("stopReason")}')
    return ''.join(chunks)


def main():
    payload = json.load(sys.stdin)
    with TemporaryFile() as diagnostics:
        process = subprocess.Popen(payload['command'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=diagnostics, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            reply = _conversation(process, payload['prompt'], payload['provider'])
        except (OSError, KeyError, TypeError, RuntimeError, ValueError) as error:
            sys.stderr.write(str(error) + '\n')
            diagnostics.seek(max(0, os.fstat(diagnostics.fileno()).st_size - 4000))
            sys.stderr.buffer.write(diagnostics.read(4000))
            return 1
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            process.stdout.close()
        sys.stdout.buffer.write(reply.encode('utf-8'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
