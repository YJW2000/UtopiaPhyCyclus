"""Verify automatic maintenance on Windows using a disposable bank and profile."""
import argparse
import asyncio
from contextlib import closing
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
from time import perf_counter

import websockets

from desktop_speed_probe import Page, ROOT, target


SAMPLE = ROOT / 'desktop/sample/力与运动.tex'


def add_question(chapters, number):
    relative = Path('牛顿运动定律') / '2026' / f'2026-M-生命周期验证-{number}-牛顿运动定律.tex'
    path = chapters / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    source = SAMPLE.read_text(encoding='utf-8').replace('}{力与运动}{1}', f'}}{{生命周期验证}}{{{number}}}')
    path.write_text(source, encoding='utf-8')
    return relative.as_posix()


def source_files(bank):
    return {path.relative_to(bank): path.read_bytes()
            for folder in ('chapters', '.backups') for path in (bank / folder).rglob('*') if path.is_file()}


def read_index(database):
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
        assert db.execute('PRAGMA quick_check').fetchone() == ('ok',)
        return {name.replace('\\', '/'): ident
                for name, ident in db.execute('SELECT "相对文件路径", "题目ID" FROM questions')}


def report(state, phase):
    path = state / f'maintenance-{phase}.json'
    if not path.is_file():
        return None
    result = json.loads(path.read_text(encoding='utf-8'))
    assert result['phase'] == phase and result['ok'] and result['result']['integrity'] == 'ok', result
    return result


def native_close(process_id, *, title=None):
    """Post the title-bar Close command to this test process's visible window."""
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = (callback, wintypes.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    handles = []

    @callback
    def inspect(window, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
        if pid.value == process_id and user32.IsWindowVisible(window):
            name = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(window, name, len(name))
            if name.value == 'Chrome_WidgetWin_1':
                caption = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(window, caption, len(caption))
                if title is None or title == caption.value:
                    handles.append(window)
        return True

    user32.EnumWindows(inspect, 0)
    if not handles:
        return False
    assert len(handles) == 1, f'Expected one test window, found {len(handles)}.'
    if not user32.PostMessageW(handles[0], 0x0112, 0xF060, 0):  # WM_SYSCOMMAND / SC_CLOSE
        raise ctypes.WinError(ctypes.get_last_error())
    return True


async def wait_for(process, check, *, seconds=20):
    async with asyncio.timeout(seconds):
        while not (result := check()):
            assert process.poll() is None, f'Electron exited with {process.returncode}'
            await asyncio.sleep(.025)
    return result


async def launch(exe, development, root, bank, callback, *, chromium_flags=()):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    command = [str(exe), *([str(ROOT / 'desktop')] if development else []), *chromium_flags,
               f'--remote-debugging-port={port}', '--library', str(bank)]
    env = {**os.environ, 'UTOPIA_DESKTOP_PROFILE': str(root / 'profile'),
           'UTOPIA_RUNTIME_CACHE': str(root / 'runtime-cache')}
    env.pop('UTOPIA_DESKTOP_VITE_URL', None)
    log = root / 'electron.log'
    with log.open('wb') as output:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=output, stderr=output,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        result = await callback(process, port)
        assert await asyncio.to_thread(process.wait, 18) == 0
        return result
    except BaseException:
        if process.poll() is None:
            taskkill = Path(os.environ['SystemRoot']) / 'System32/taskkill.exe'
            subprocess.run([taskkill, '/PID', str(process.pid), '/T', '/F'], check=False,
                           capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
            await asyncio.to_thread(process.wait, 5)
        print(log.read_text(encoding='utf-8', errors='replace'))
        raise


async def probe(exe, development, root):
    bank = root / 'bank'
    chapters = bank / 'chapters'
    database = bank / 'utils/question_index.sqlite3'
    first = add_question(chapters, 1)
    related = chapters / first.replace('.tex', ' 相关图') / '插图.tex'
    related.parent.mkdir()
    related.write_text(r'\draw (0,0) -- (1,1);', encoding='utf-8')
    backup = bank / '.backups/原题.tex'
    backup.parent.mkdir()
    backup.write_bytes((chapters / first).read_bytes())
    originals = source_files(bank)

    async def normal(process, port):
        url = await wait_for(process, lambda: target(port))
        async with websockets.connect(url) as ws:
            page = Page(ws)
            await page.until("""document.querySelector('button[data-page=browse]')?.checkVisibility()""")
            assert await page.js("""(() => {
                const nav = document.querySelector('.app-nav nav');
                return !nav.querySelector('[data-page=tools]') && !nav.textContent.includes('维护工具');
            })()""")
            await page.js("document.querySelector('button[data-page=browse]').click()")
            await page.until("document.querySelector('.section-head small')?.textContent === '共 1 题'", seconds=60)
            await page.until("document.querySelector('.question-link')?.checkVisibility()")
            ping = await page.js("window.utopia.call('system.ping')")
            assert Path(ping['library']) == bank
            state = Path(ping['stateDir'])
            startup = await wait_for(process, lambda: report(state, 'startup'))
            assert set(read_index(database)) == {first}
            await page.js("document.querySelector('button[data-page=settings]').click()")
            await page.until("document.querySelector('time[datetime]')?.dateTime === "
                             + json.dumps(startup['finishedAt']))
            second = add_question(chapters, 2)
            originals[Path('chapters') / second] = (chapters / second).read_bytes()
            assert set(read_index(database)) == {first}
            started = perf_counter()
            await wait_for(process, lambda: native_close(process.pid))
            return state, startup, second, started

    state, startup, second, closed_at = await launch(exe, development, root, bank, normal)
    normal_close_ms = round((perf_counter() - closed_at) * 1000)
    shutdown = report(state, 'shutdown')
    assert shutdown and startup['finishedAt'] <= shutdown['finishedAt']
    previous = read_index(database)
    assert set(previous) == {first, second}, previous
    assert source_files(bank) == originals
    assert startup['result']['added'] == shutdown['result']['added'] == 1

    third = add_question(chapters, 3)
    originals[Path('chapters') / third] = (chapters / third).read_bytes()
    for phase in ('startup', 'shutdown'):
        (state / f'maintenance-{phase}.json').unlink()

    # Force startup and quick close to overlap with a short-lived writer.
    with closing(sqlite3.connect(database)) as writer:
        writer.execute('BEGIN IMMEDIATE')

        async def close_during_startup(process, port):
            try:
                await wait_for(process, lambda: native_close(process.pid))
                started = perf_counter()
                await wait_for(process, lambda: target(port) is None)
                assert process.poll() is None and report(state, 'startup') is None
                return started
            finally:
                writer.rollback()

        fast_closed_at = await launch(exe, development, root, bank, close_during_startup)
    fast_close_ms = round((perf_counter() - fast_closed_at) * 1000)
    startup = report(state, 'startup')
    shutdown = report(state, 'shutdown')
    assert startup and shutdown and startup['finishedAt'] <= shutdown['finishedAt']
    assert startup['result']['added'] == 1 and shutdown['result']['added'] == 0
    indexed = read_index(database)
    assert set(indexed) == {first, second, third}, indexed
    assert {name: indexed[name] for name in previous} == previous
    assert source_files(bank) == originals
    return {'indexed': len(indexed), 'normalCloseMs': normal_close_ms, 'fastCloseMs': fast_close_ms}


async def main(args):
    with TemporaryDirectory(prefix='utopia-maintenance-qa-', ignore_cleanup_errors=True) as temporary:
        root = Path(temporary)
        exe = args.exe.resolve(strict=True)
        if args.development:
            # Run the stock binary at normal integrity, outside the workspace.
            electron = root / 'electron'
            shutil.copytree(exe.parent, electron, copy_function=shutil.copyfile)
            exe = electron / exe.name
        print(json.dumps(await probe(exe, args.development, root)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--development', action='store_true',
                        help='Launch the compiled renderer with the Electron development binary.')
    asyncio.run(main(parser.parse_args()))
