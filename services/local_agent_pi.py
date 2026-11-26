"""One-shot Pi RPC requests using the user's local model configuration."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryFile


def pi_request(executable, directory, prompt, schema, images, env, timeout, model=None):
    from services.local_agent import AgentResponseError, _image_blocks, _invoke, _structured_prompt

    command = [executable, '--mode', 'rpc', '--no-session', '--no-tools', '--no-mcp',
               '--no-extensions', '--no-skills', '--no-prompt-templates', '--no-context-files', '--no-approve']
    payload = {
        'command': command,
        'prompt': {
            'id': 'prompt', 'type': 'prompt', 'message': _structured_prompt(prompt, schema),
            'images': [{'type': 'image', 'mimeType': 'image/png', 'data': image} for image in _image_blocks(images)],
        },
    }
    if model:
        payload['model'] = model
    worker = str(Path(__file__).resolve())
    result = _invoke([sys.executable, '-B', '-X', 'utf8', worker], directory,
                     json.dumps(payload, ensure_ascii=False).encode('utf-8'), env, timeout)
    if result.returncode:
        raise AgentResponseError(result.stderr[-4000:])
    return result.stdout.decode('utf-8')


def _send(process, request):
    process.stdin.write((json.dumps(request, ensure_ascii=False) + '\n').encode('utf-8'))
    process.stdin.flush()


def _read(process):
    for line in process.stdout:
        try:
            event = json.loads(line)
        except (UnicodeError, ValueError):
            continue
        if isinstance(event, dict):
            yield event
    raise RuntimeError('Pi exited before returning a response')


def _reply(process, prompt, model_id=None):
    events = _read(process)
    if model_id:
        provider, _, model = model_id.partition('/')
        _send(process, {'id': 'model', 'type': 'set_model', 'provider': provider, 'modelId': model})
        change = next(event for event in events if event.get('type') == 'response' and event.get('id') == 'model')
        if not change.get('success'):
            raise RuntimeError(f'Could not select requested model {model_id}: {change.get("error", "")}')
    _send(process, {'id': 'state', 'type': 'get_state'})
    state = next(event for event in events if event.get('type') == 'response' and event.get('id') == 'state')
    if not state.get('success'):
        raise RuntimeError(state.get('error') or 'Could not read Pi model state')
    model = state.get('data', {}).get('model')
    if not model:
        raise RuntimeError('Pi has no configured model. Sign in in the Pi CLI.')
    if model_id and f'{model.get("provider")}/{model.get("id")}' != model_id:
        raise RuntimeError(f'Could not select requested model {model_id}')
    if prompt['images'] and 'image' not in model.get('input', []):
        raise RuntimeError('Pi model does not support image input')

    _send(process, prompt)
    accepted = settled = False
    final = {}
    for event in events:
        kind = event.get('type')
        if kind == 'response' and event.get('id') == 'prompt':
            if not event.get('success') or event.get('data', {}).get('disposition') == 'handled':
                raise RuntimeError(event.get('error') or 'Pi did not accept the prompt')
            accepted = True
        elif kind == 'message_end' and event.get('message', {}).get('role') == 'assistant':
            final = event['message']
        elif kind == 'agent_settled':
            settled = True
        if accepted and settled:
            break

    if final.get('stopReason') != 'stop':
        raise RuntimeError(final.get('errorMessage') or 'Pi did not finish the response')
    answer = '\n'.join(part['text'] for part in final.get('content', []) if part.get('type') == 'text')
    if not answer.strip():
        raise RuntimeError('Pi returned an empty response')
    return answer


def main():
    payload = json.load(sys.stdin)
    with TemporaryFile() as diagnostics:
        try:
            with subprocess.Popen(payload['command'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=diagnostics, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
                try:
                    reply = _reply(process, payload['prompt'], payload.get('model'))
                finally:
                    process.stdin.close()
                    process.stdout.close()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
        except (OSError, KeyError, TypeError, RuntimeError, ValueError) as error:
            sys.stderr.write(str(error) + '\n')
            diagnostics.seek(0, 2)
            diagnostics.seek(max(0, diagnostics.tell() - 2000))
            sys.stderr.buffer.write(diagnostics.read(2000))
            return 1
    sys.stdout.buffer.write(reply.encode('utf-8'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
