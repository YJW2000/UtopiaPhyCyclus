"""One-shot desktop Agent requests using each CLI's own account and model."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from services.desktop_tool_paths import (
    configured_agent_command, fresh_tool_path, managed_papilot_agent, resolve_agent_command,
)
from services.local_codex import _find_codex, _run_codex_json
from services.local_process import run


@dataclass(frozen=True)
class LocalAgent:
    label: str
    binary: str | None
    login: str
    setup: str
    images: bool = True
    alias: str | None = None


LOCAL_AGENTS = {
    'codex': LocalAgent('Codex', 'codex', 'codex login', '使用本机已登录的 Codex CLI。'),
    'claude': LocalAgent('Claude Code', 'claude', 'claude auth login', '使用本机 Claude Code 的现有登录和模型。'),
    'copilot': LocalAgent('GitHub Copilot', 'copilot', 'copilot login', '需要独立的 GitHub Copilot CLI 及可用的 Copilot 账户。图片需要支持视觉的模型。'),
    'cursor': LocalAgent('Cursor', 'cursor-agent', 'cursor-agent login', '使用 Cursor Agent CLI 的现有登录，支持纯文本任务。', images=False),
    'opencode': LocalAgent('OpenCode', 'opencode', 'opencode auth login', '使用 OpenCode 已连接的提供商和模型。图片需要支持视觉的模型。'),
    'gemini': LocalAgent('Gemini CLI', 'gemini', 'gemini', '先在终端启动 Gemini CLI 并完成登录，再回到题库。'),
    'codebuddy': LocalAgent('CodeBuddy CLI', 'codebuddy', 'codebuddy', '首次启动时按提示登录腾讯账号，也可在 CodeBuddy CLI 中输入 /login。'),
    'qoder': LocalAgent('Qoder CLI', 'qoder', 'qoder login', '使用本机已登录的 Qoder CLI。图片需使用支持视觉的模型。', alias='qodercli'),
    'agy': LocalAgent('Antigravity CLI (agy)', 'agy', 'agy acp --login', '用本机 Antigravity CLI 登录 Google 账号后返回题库。'),
    'workbuddy': LocalAgent('WorkBuddy', 'workbuddy', '', '沿用独立 WorkBuddy ACP 启动器的登录。图片需由该启动器和模型支持。'),
    'pi': LocalAgent('Pi', 'pi', 'pi', '在终端运行 Pi，输入 /login 登录，然后选择模型。图片需由所选模型支持。'),
    'grok': LocalAgent('Grok Build', 'grok', 'grok login', '使用 Grok Build CLI 的现有登录。图片需由所选模型支持。'),
    'kimi': LocalAgent('Kimi Code', 'kimi', 'kimi login', '使用本机 Kimi Code CLI 的登录。图片需由所选模型支持。'),
    'deepseek': LocalAgent('DeepSeek Harness', 'dsh', 'dsh web', '在 DeepSeek Harness 中登录，再使用本机 dsh 启动器。', images=False),
    'qwen': LocalAgent('Qwen Code', 'qwen', 'qwen', '在终端启动 Qwen Code 并完成登录。图片需由所选模型支持。'),
    'glm': LocalAgent('GLM', None, '', '在本机完成 GLM 凭据配置后重新检测。使用文本模型 GLM-5.3。', images=False),
}


def _find_agent(agent_id, tool_path):
    if agent_id == 'codex':
        return _find_codex(tool_path)
    if agent_id == 'workbuddy':
        from services.local_agent_workbuddy import find_workbuddy_launcher
        return find_workbuddy_launcher(tool_path)
    agent = LOCAL_AGENTS[agent_id]
    if agent.binary is None:
        return None
    command = configured_agent_command('CURSOR' if agent_id == 'cursor' else agent.binary.upper())
    if command is not None:
        return resolve_agent_command(command, tool_path)
    executable = shutil.which(agent.binary, path=tool_path)
    if not executable and agent.alias:
        executable = shutil.which(agent.alias, path=tool_path)
    return executable or managed_papilot_agent(agent_id)


def _login_command(agent, executable):
    if not executable or not agent.login:
        return agent.login
    launcher = Path(executable)
    if os.name == 'nt' and launcher.is_absolute():
        quoted_path = str(launcher).replace("'", "''")
        _, space, arguments = agent.login.partition(' ')
        return f"& '{quoted_path}'{space}{arguments}"
    if agent.alias and launcher.stem.lower() == agent.alias:
        return agent.login.replace(agent.binary, agent.alias, 1)
    return agent.login


def _setup_help(agent, executable):
    login = _login_command(agent, executable)
    return f'请在终端运行 {login} 完成配置后重试。' if login else agent.setup


def agent_status(tool_path):
    from services.local_agent_glm import glm_configured

    result = []
    for agent_id, agent in LOCAL_AGENTS.items():
        executable = _find_agent(agent_id, tool_path)
        installed = glm_configured() if agent_id == 'glm' else bool(executable)
        result.append(dict(id=agent_id, label=agent.label, installed=installed, path=executable,
                           images=agent.images, modelSelection=True, loginCommand=_login_command(agent, executable),
                           setup=agent.setup, auth='unknown'))
    return result


def require_image_support(provider):
    agent = LOCAL_AGENTS.get(provider)
    if agent and not agent.images:
        raise ValueError(f'当前选择的 {agent.label} 只支持文本。请在设置中选用支持图片的 Agent 再试。')


def _agent_environment(tool_path):
    # The legacy OpenAI-shaped adapter uses app credentials internally. The
    # local CLI selects its own account and must never receive those values.
    app_credentials = {'AI_BASE_URL', 'AI_API_KEY', 'AI_MODEL_NAME', 'AI_SOLVER_MODEL_NAME'}
    return {**{key: value for key, value in os.environ.items() if key.upper() not in app_credentials}, 'PATH': tool_path}


def _invoke(command, directory, payload, env, timeout):
    return run(command, cwd=directory, input=payload, capture_output=True, env=env, timeout=timeout,
               kill_tree_on_timeout=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def _structured_prompt(prompt, schema):
    return ('完成以下任务。只返回符合此 JSON Schema 的一个 JSON 对象，请勿添加 Markdown 围栏或说明。\n'
            + json.dumps(schema, ensure_ascii=False) + '\n\n' + prompt)


def _image_blocks(images):
    return [base64.b64encode(image).decode('ascii') for image in images]


def _message_blocks(prompt, images):
    return [{'type': 'text', 'text': prompt}, *[
        {'type': 'image', 'mimeType': 'image/png', 'data': image} for image in _image_blocks(images)
    ]]


def _claude(executable, directory, prompt, schema, images, env, timeout, model=None):
    npm_binary = Path(executable).parent / 'node_modules/@anthropic-ai/claude-code/bin/claude.exe'
    if executable.lower().endswith('.cmd') and npm_binary.is_file():
        executable = str(npm_binary)
    (directory / 'settings.json').write_text('{"disableAllHooks":true}', encoding='utf-8')
    # Claude's built-in structured output is a pseudo tool. Real built-in and
    # MCP tools remain disabled while that schema output is available.
    command = [executable, '--print', '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose',
               '--permission-mode', 'dontAsk', '--tools', '', '--strict-mcp-config', '--mcp-config', 'mcp.json',
               '--settings', 'settings.json', '--no-chrome', '--no-session-persistence', '--safe-mode']
    if model:
        command.extend(['--model', model])
    (directory / 'mcp.json').write_text('{"mcpServers":{}}', encoding='utf-8')
    # Keep the JSON value out of cmd.exe parsing when npm supplies a .cmd shim.
    # In this case Claude still follows the schema supplied in the user message.
    if not executable.lower().endswith(('.cmd', '.bat')):
        command.extend(['--json-schema', json.dumps(schema, ensure_ascii=True, separators=(',', ':'))])
    content = [{'type': 'text', 'text': _structured_prompt(prompt, schema)}]
    for image in _image_blocks(images):
        content.append({'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': image}})
    message = {'type': 'user', 'message': {'role': 'user', 'content': content},
               'parent_tool_use_id': None, 'session_id': ''}
    result = _invoke(command, directory, (json.dumps(message, ensure_ascii=False) + '\n').encode(), env, timeout)
    events = [event for event in _events(result.stdout) if event.get('type') == 'result']
    if result.returncode or not events or events[-1].get('is_error'):
        raise AgentResponseError(result.stderr + b'\n' + result.stdout[-4000:])
    return events[-1].get('structured_output', events[-1].get('result'))


def _opencode_config(executable, directory, env, timeout):
    version = _invoke([executable, '--version'], directory, b'', env, min(8, timeout))
    match = re.search(rb'(?m)^v?(\d+)\.\d+', version.stdout)
    if version.returncode or not match:
        raise AgentResponseError(version.stderr)
    if int(match[1]) >= 2:
        config = {'policies': [dict(action=action, resource='*', effect='deny')
                               for action in ('tool.use', 'mcp.use', 'skill.use', 'plugin.use', 'websearch.use')],
                  'permissions': [{'action': '*', 'resource': '*', 'effect': 'deny'}]}
    else:
        config = {'permission': {'*': 'deny'}, 'default_agent': 'utopia-reply',
                  'agent': {'utopia-reply': {'mode': 'primary', 'permission': {'*': 'deny'}}}, 'autoupdate': False}
        env['OPENCODE_PERMISSION'] = '{"*":"deny"}'
    env['OPENCODE_CONFIG_CONTENT'] = json.dumps(config)


def _acp(provider, executable, directory, prompt, schema, images, env, timeout, model=None):
    if provider == 'opencode':
        _opencode_config(executable, directory, env, timeout)
        command = [executable, 'acp']
    elif provider == 'copilot':
        env.update(COPILOT_ALLOW_ALL='false', COPILOT_ASSISTED_APPROVAL='false', COPILOT_AUTO_UPDATE='false',
                   GITHUB_COPILOT_PROMPT_MODE_EXTENSIONS='false')
        command = [executable, '--acp', '--stdio', '--no-auto-update', '--no-ask-user', '--no-custom-instructions',
                   '--disable-builtin-mcps', '--disable-mcp-server=computer-use', '--sync-session=off',
                   '--available-tools=ask_user', '--deny-tool=read,write,shell,url,memory']
    elif provider == 'cursor':
        cursor = directory / '.cursor'
        cursor.mkdir()
        (cursor / 'cli.json').write_text(json.dumps({'permissions': {
            'deny': ['Shell(*)', 'Read(**)', 'Write(**)', 'WebFetch(*)', 'Mcp(*:*)'],
        }}), encoding='utf-8')
        command = [executable, 'acp']
    elif provider == 'gemini':
        (directory / 'utopia-policy.toml').write_text(
            '[[rule]]\ntoolName = "*"\ndecision = "deny"\npriority = 999\n', encoding='ascii')
        gemini = directory / '.gemini'
        gemini.mkdir()
        (gemini / 'settings.json').write_text(json.dumps({
            'general': {'enableAutoUpdate': False}, 'hooks': {'enabled': False},
            'mcp': {'allowed': ['utopia-disabled']}, 'tools': {'discoveryCommand': '', 'callCommand': ''},
        }), encoding='utf-8')
        command = [executable, '--acp', '--approval-mode', 'default', '--admin-policy', 'utopia-policy.toml',
                   '--allowed-mcp-server-names', 'utopia-disabled']
    elif provider == 'codebuddy':
        from services.codebuddy_agent import codebuddy_acp_command
        command = codebuddy_acp_command(executable, directory, env)
    elif provider == 'qoder':
        from services.local_agent_qoder import qoder_acp_command
        command = qoder_acp_command(executable, directory, env)
    elif provider == 'agy':
        from services.local_agent_agy import agy_acp_command
        command = agy_acp_command(executable, directory, env)
    elif provider == 'workbuddy':
        from services.local_agent_workbuddy import workbuddy_acp_command
        command = workbuddy_acp_command(executable, directory, env)
    else:
        from services.local_agent_papilot_acp import papilot_acp_command
        command = papilot_acp_command(provider, executable, directory, env)
    task = _structured_prompt(prompt, schema)
    if images:
        task += '\n\n本次任务包含图片，若当前模型无法读取图片，请只返回 {"utopia_error":"image_unsupported"}。'
    payload = {'provider': provider, 'command': command, 'prompt': _message_blocks(task, images)}
    if model:
        payload['model'] = model
    worker = str(Path(__file__).with_name('local_agent_acp.py'))
    result = _invoke([sys.executable, '-B', '-X', 'utf8', worker], directory,
                     json.dumps(payload, ensure_ascii=False).encode(), env, timeout)
    if result.returncode:
        raise AgentResponseError(result.stderr)
    return result.stdout.decode('utf-8')


class AgentResponseError(Exception):
    pass


def _events(stdout):
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except (UnicodeError, ValueError):
            continue
        if isinstance(event, dict):
            yield event


def _decode_output(value, schema):
    from jsonschema import ValidationError, validate

    if isinstance(value, str):
        value = value.strip()
        fence = re.fullmatch(r'```(?:json)?\s*([\s\S]*?)\s*```', value, re.I)
        value = json.loads(fence[1] if fence else value)
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    if value.get('utopia_error') == 'image_unsupported':
        raise AgentResponseError(b'image input not supported')
    try:
        validate(value, schema)
    except ValidationError as exc:
        raise ValueError('Invalid JSON object') from exc
    return value


def run_agent_json(provider, prompt, schema, images, *, timeout=180, model=None):
    from services.desktop_model import requested_model

    if not isinstance(provider, str) or provider not in LOCAL_AGENTS:
        raise ValueError('请选择可用的本机 Agent。')
    model = requested_model(model, provider)
    selection = {'model': model} if model else {}
    if images:
        require_image_support(provider)
    if provider == 'glm':
        from services.local_agent_glm import glm_request
        return glm_request(prompt, schema, images, timeout=timeout, **selection)
    tool_path = fresh_tool_path()
    if provider == 'codex':
        return _run_codex_json(prompt, schema, images, timeout=timeout, tool_path=tool_path,
                               env=_agent_environment(tool_path), **selection)

    agent = LOCAL_AGENTS[provider]
    executable = _find_agent(provider, tool_path)
    if not executable:
        raise RuntimeError(f'未找到 {agent.label} 的可用启动器。请在设置中检查安装。{_setup_help(agent, executable)}')

    try:
        with TemporaryDirectory(prefix=f'utopia-{provider}-') as temp:
            directory = Path(temp)
            env = _agent_environment(tool_path)
            if provider == 'claude':
                value = _claude(executable, directory, prompt, schema, images, env, timeout, **selection)
            elif provider == 'pi':
                from services.local_agent_pi import pi_request
                value = pi_request(executable, directory, prompt, schema, images, env, timeout, **selection)
            else:
                value = _acp(provider, executable, directory, prompt, schema, images, env, timeout, **selection)
        return _decode_output(value, schema)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'{agent.label} 在 {timeout} 秒内未完成，请稍后重试。') from None
    except OSError:
        raise RuntimeError(f'{agent.label} 启动失败。{_setup_help(agent, executable)}') from None
    except AgentResponseError as error:
        detail = error.args[0].decode('utf-8', 'replace').lower()
        if any(term in detail for term in ('unauthorized', '401', 'authentication', 'not logged', 'login required', 'not authenticated', 'sign in', 'api key')):
            message = f'{agent.label} 登录或模型配置不可用。{_setup_help(agent, executable)}'
        elif any(term in detail for term in ('usage limit', 'rate limit', 'quota', '429')):
            message = f'{agent.label} 的调用额度暂时受限，请稍后重试。'
        elif model and 'could not select requested model' in detail:
            message = f'{agent.label} 无法切换到模型 {model}。请检查模型 ID、账户权限及 CLI 版本。'
        elif images and any(term in detail for term in ('vision', 'multimodal', 'image support', 'image input')):
            message = f'{agent.label} 当前模型无法接收图片，请在该 CLI 中切换到支持视觉的模型后重试。'
        else:
            message = f'{agent.label} 调用失败，请检查 CLI 版本、登录和默认模型配置。'
        raise RuntimeError(message) from None
    except (TypeError, ValueError):
        raise RuntimeError(f'{agent.label} 未返回有效的结构化结果，请重试。') from None
