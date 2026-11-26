"""Codex CLI adapter and the existing structured request entry point."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from services.local_process import run


def _find_codex(path: str | None = None) -> str | None:
    from services.desktop_tool_paths import configured_agent_command, resolve_agent_command

    command = configured_agent_command('CODEX')
    if command is not None:
        return resolve_agent_command(command, path)
    executable = shutil.which("codex", path=path) if path else shutil.which("codex")
    if executable:
        return executable
    if os.name == "nt":
        # A Start menu launch does not inherit the private PATH that Codex
        # Desktop gives its own terminal. The Store package also ships a CLI.
        shell = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        query = ("[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); "
                 "Get-AppxPackage -Name OpenAI.Codex | Select-Object -First 1 -ExpandProperty InstallLocation")
        try:
            result = run([str(shell), "-NoProfile", "-NonInteractive", "-Command", query],
                                    capture_output=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            return None
        location = result.stdout.decode("utf-8-sig", "replace").strip()
        if result.returncode or not location:
            return None
        bundled = Path(location) / "app/resources/codex.exe"
    else:
        bundled = Path("/Applications/Codex.app/Contents/Resources/codex")
    return str(bundled) if bundled.is_file() else None


def run_local_codex_json(prompt: str, schema: dict, images: list[bytes], *, timeout: int = 180) -> dict:
    """Dispatch existing document callers through the desktop preference when set."""
    from services.desktop_model import selected_model, selected_provider
    from services.local_agent import run_agent_json
    provider = selected_provider()
    model = selected_model()
    if provider == 'codex' and not model and not os.environ.get('UTOPIA_DESKTOP_STATE'):
        return _run_codex_json(prompt, schema, images, timeout=timeout)
    return run_agent_json(provider, prompt, schema, images, timeout=timeout, **({'model': model} if model else {}))


def _run_codex_json(prompt, schema, images, *, timeout, tool_path=None, env=None, model=None):
    executable = _find_codex(tool_path) if tool_path else _find_codex()
    if not executable:
        raise RuntimeError("未找到本机 Codex CLI。请安装 Codex CLI 并运行 codex login 后重试。")
    with TemporaryDirectory(prefix="utopia-codex-") as temp:
        directory = Path(temp)
        (directory / "schema.json").write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
        command = [executable, "-a", "never", "exec", "--sandbox", "read-only", "--skip-git-repo-check",
                   "-c", "features.shell_tool=false", "-c", "features.apps=false", "-c", "features.plugins=false",
                   "-c", "mcp_servers={}", "-c", 'web_search="disabled"',
                   "--ephemeral", "--output-schema", "schema.json", "-o", "result.json"]
        if model:
            command.extend(["--model", model])
        for index, data in enumerate(images):
            name = f"image-{index + 1}.png"
            (directory / name).write_bytes(data)
            command.extend(["--image", name])
        try:
            launch = {'env': env, 'kill_tree_on_timeout': True} if env is not None else {}
            result = run(
                [*command, "-"], cwd=directory, input=prompt.encode("utf-8"), capture_output=True,
                timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **launch,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"本地 Codex 在 {timeout} 秒内未完成，请稍后重试。") from exc
        except OSError as exc:
            raise RuntimeError("本地 Codex 启动失败，请在终端检查 codex --version。") from exc
        if result.returncode:
            error = result.stderr.decode("utf-8", "replace").lower()
            if any(term in error for term in ("unauthorized", "401", "not logged", "login required", "sign in")):
                raise RuntimeError("本地 Codex 登录已失效，请在终端运行 codex login 后重试。")
            if any(term in error for term in ("usage limit", "rate limit", "quota", "429")):
                raise RuntimeError("本地 Codex 的调用额度暂时受限，请稍后重试。")
            raise RuntimeError("本地 Codex 调用失败，请检查终端中的 codex exec 是否可用。")
        try:
            value = json.loads((directory / "result.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise RuntimeError("本地 Codex 未返回有效的结构化结果，请重试。") from exc
        if not isinstance(value, dict):
            raise ValueError("本地 Codex 返回的结果格式无效。")
        return value
