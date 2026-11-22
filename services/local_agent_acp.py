"""Run a single ACP prompt in a child owned by the cancellable desktop worker."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryFile


def _conversation(process, prompt, provider):
    chunks = []

    def send(payload):
        process.stdin.write((json.dumps({'jsonrpc': '2.0', **payload}, ensure_ascii=False) + '\n').encode('utf-8'))
        process.stdin.flush()

    def request(ident, method, params):
        send({'id': ident, 'method': method, 'params': params})
        for line in process.stdout:
            try:
                message = json.loads(line)
            except (UnicodeError, ValueError):
                continue
            if not isinstance(message, dict):
                continue
            incoming = message.get('method')
            if incoming is None and message.get('id') == ident:
                if 'error' in message:
                    error = message['error']
                    if isinstance(error, dict) and error.get('code') == -32000:
                        raise RuntimeError(f'Authentication or session configuration error: {error}')
                    raise RuntimeError(str(error))
                return message['result']
            if incoming == 'session/update':
                update = message.get('params', {}).get('update', {})
                content = update.get('content', {})
                if update.get('sessionUpdate') == 'agent_message_chunk' and content.get('type') == 'text':
                    chunks.append(content['text'])
            if incoming and 'id' in message:
                if incoming == 'session/request_permission':
                    options = message.get('params', {}).get('options', [])
                    deny = next((item['optionId'] for item in options if item.get('kind') == 'reject_once'), None)
                    outcome = {'outcome': 'selected', 'optionId': deny} if deny else {'outcome': 'cancelled'}
                    send({'id': message['id'], 'result': {'outcome': outcome}})
                else:
                    send({'id': message['id'], 'error': {'code': -32601, 'message': 'Client method unavailable'}})
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
    session = request(2, 'session/new', {'cwd': str(Path.cwd()), 'mcpServers': []})
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
    result = request(5, 'session/prompt', {'sessionId': session_id, 'prompt': prompt})
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
            process.stdout.close()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        sys.stdout.buffer.write(reply.encode('utf-8'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
