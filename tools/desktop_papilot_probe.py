"""Exercise the embedded Papilot, TeX, and Git workflow in a disposable Electron profile."""
import argparse
import asyncio
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from time import perf_counter
from urllib.parse import quote, urlsplit
from urllib.request import urlopen

import pymupdf
import websockets

from desktop_maintenance_probe import launch, native_close, wait_for
from desktop_speed_probe import Page


SOURCE = r'''
\documentclass[UTF8,fontset=fandol]{ctexart}
\begin{document}
题库项目验收：电阻的阻值为 $6\,\Omega$，电压为 $3\,\mathrm{V}$。
\[ I = \frac{U}{R} = 0.5\,\mathrm{A} \]
\end{document}
'''.strip()
ORIGINAL = '题库项目验收'
REVISED = '编译和版本管理验收'
NOTE = '校核电流和单位'
COMMIT = '验证 Papilot 版本记录'
SLOT = '.papilot-workspace'
# An occluded QA window still needs to draw PDF pages and observe resize events.
RENDER_FLAGS = ('--disable-backgrounding-occluded-windows', '--disable-background-timer-throttling',
                '--disable-renderer-backgrounding')


def desktop_target(port, embedded=False):
    try:
        with urlopen(f'http://127.0.0.1:{port}/json/list', timeout=.3) as response:
            for page in json.load(response):
                origin = urlsplit(page['url'])
                host = ('http', '127.0.0.1') if embedded else ('utopia', 'app')
                if (origin.scheme, origin.hostname) == host and page.get('title'):
                    return page.get('webSocketDebuggerUrl')
    except (OSError, ValueError):
        return None


def find(selector):
    return f'document.querySelector({json.dumps(selector)})'


async def click(page, selector):
    element = find(selector)
    await page.until(f'{element}?.checkVisibility() && !{element}.disabled', seconds=30)
    await page.js(f'{element}.click()')


async def click_label(page, label):
    button = f'[...document.querySelectorAll("button")].find(b => b.checkVisibility() && b.textContent.trim() === {json.dumps(label)})'
    await page.until(f'Boolean({button})', seconds=30)
    await page.js(f'({button}).click()')


async def point_note(page, project):
    await click(page, '.pdf-rail [aria-label="便签"], .pdf-rail [aria-label="Sticky note"], .pdf-rail [aria-keyshortcuts="P"]')
    position = await page.js('''(() => {
        const r = document.querySelector('.pdfViewer .page').getBoundingClientRect()
        return {x: Math.round(r.left + r.width * .5), y: Math.round(r.top + Math.min(160, r.height * .27))}
    })()''')
    for event_type in ('mousePressed', 'mouseReleased'):
        await page.command('Input.dispatchMouseEvent', {'type': event_type, 'button': 'left', 'clickCount': 1, **position})
    textbox = 'document.activeElement?.matches("textarea,[contenteditable=true]")'
    await page.until(textbox)
    await page.command('Input.insertText', {'text': NOTE})
    for event_type in ('keyDown', 'keyUp'):
        await page.command('Input.dispatchKeyEvent', {'type': event_type, 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13})
    await page.until('Boolean(document.querySelector(".note-tag[data-kind=point]"))')
    endpoint = json.dumps(f'/api/projects/{project}/manuscript')
    assert await page.js(f'fetch({endpoint}).then(r => r.json()).then(s => s.annotations.some(n => n.comment === {json.dumps(NOTE)}))')


def compiled_pdf_text(origin, project, build_key):
    with urlopen(f'{origin}/api/projects/{quote(project)}/manuscript.pdf?key={quote(build_key)}', timeout=5) as response:
        payload = response.read()
    with pymupdf.open(stream=payload, filetype='pdf') as document:
        return ''.join(''.join(page.get_text() for page in document).split())


def git(folder, *args):
    return subprocess.run(['git', '-C', str(folder), *args], check=True, capture_output=True, text=True, encoding='utf-8').stdout.strip()


async def commit_tex_edit(page, folder):
    assert not git(folder, 'remote'), 'The fixture repository must not have any remotes.'
    git(folder, 'config', 'user.name', 'Utopia QA')
    git(folder, 'config', 'user.email', 'utopia-test@localhost')
    git(folder, 'config', 'commit.gpgsign', 'false')
    source = folder / 'main.tex'
    original = source.read_text(encoding='utf-8')
    assert ORIGINAL in original
    source.write_text(original.replace(ORIGINAL, REVISED, 1), encoding='utf-8')
    await click(page, '.git-link')
    await page.until('document.querySelector(".git-panel")?.textContent.includes("main.tex")', seconds=35)
    await page.js('document.querySelector(".git-message-box textarea").focus()')
    await page.command('Input.insertText', {'text': COMMIT})
    await click(page, '.git-commit-button')
    await page.until('!document.querySelector(".git-panel .spinner.light") && !document.querySelector(".git-panel")?.textContent.includes("main.tex")')
    assert git(folder, 'log', '-1', '--pretty=%s') == COMMIT
    await click(page, '.git-detail-switcher button:nth-of-type(2)')
    await page.until(f'document.querySelector(".git-workspace")?.textContent.includes({json.dumps(COMMIT)})')


async def resize_view(shell, project):
    initial_width = await project.js('innerWidth')
    await click(shell, '.collapse-nav')
    await shell.until('document.querySelector(".app-shell")?.classList.contains("compact")')
    try:
        await project.until(f'innerWidth > {initial_width} + 30', seconds=40)
    except TimeoutError as error:
        slot_width = await shell.js(f'{find(SLOT)}?.getBoundingClientRect().width')
        current_width = await project.js('innerWidth')
        raise AssertionError(f'Shell slot {slot_width}px, native view {initial_width}px → {current_width}px') from error


async def probe(executable, development, root, screenshot=None):
    bank = root / 'bank'
    (bank / 'chapters').mkdir(parents=True)

    async def run(process, port):
        shell_url = await wait_for(process, lambda: desktop_target(port), seconds=120)
        async with websockets.connect(shell_url) as shell_socket:
            shell = Page(shell_socket)
            started = perf_counter()
            await click(shell, 'button[data-page=papilot]')
            await shell.until(f'Boolean({find(SLOT)})')
            assert await shell.js(f'!{find(SLOT)}.querySelector("iframe")')
            await shell.until(f'{find(SLOT)}?.getAttribute("aria-busy") === "false"', seconds=135)
            error = await shell.js(f'{find(SLOT)}.querySelector("[role=alert]")?.textContent')
            assert not error, error
            embedded_url = await wait_for(process, lambda: desktop_target(port, embedded=True), seconds=75)
            async with websockets.connect(embedded_url) as project_socket:
                project = Page(project_socket)
                await project.until('Boolean(window.papilotDesktop?.chooseDirectory && document.querySelector(".sidebar"))', seconds=45)
                first_open_ms = round((perf_counter() - started) * 1000)
                assert await project.js('window.top === window && !window.require && !window.utopia')
                print(f'Embedded renderer ready in {first_open_ms} ms.', flush=True)
                await click(shell, 'button[data-page=tex]')
                await click(shell, 'summary')
                await shell.js('document.querySelector("textarea.paste-tex").focus()')
                await shell.command('Input.insertText', {'text': SOURCE})
                await click_label(shell, '在工作台打开')
                await click(shell, 'button[aria-label="更多操作"]')
                await click_label(shell, '在 Papilot 工作台打开')
                await shell.until(f'Boolean({find(SLOT)})')
                state_read = '''fetch('/api/state').then(r => r.json()).then(s => ({
                    version: s.version,
                    projects: s.projects.filter(p => p.path?.split(/[/\\\\]/).includes('papilot-projects')).map(({id, path}) => ({id, path}))
                }))'''
                await project.until(f'({state_read}).then(s => s.projects.length === 1 && Boolean(document.querySelector(".manuscript-link")))', seconds=55)
                state = await project.js(state_read)
                projects = state['projects']
                assert len(projects) == 1, projects
                folder = Path(projects[0]['path']).resolve(strict=True)
                assert folder.is_relative_to(root), 'The opened project must be inside the disposable workspace.'
                assert not list((bank / 'chapters').rglob('*.tex'))
                await click(project, '.manuscript-link')
                project_id = projects[0]['id']
                endpoint = json.dumps(f'/api/projects/{project_id}/manuscript')
                await project.until(f'fetch({endpoint}).then(r => r.json()).then(s => s.build?.status === "ready")', seconds=150)
                build_key = await project.js(f'fetch({endpoint}).then(r => r.json()).then(s => s.build.key)')
                origin = await project.js('location.origin')
                contents = await asyncio.to_thread(compiled_pdf_text, origin, project_id, build_key)
                assert ORIGINAL in contents and '\ufffd' not in contents, contents
                print('Embedded LaTeX PDF compiled with readable Chinese.', flush=True)
                await shell.command('Page.bringToFront')
                await project.command('Page.bringToFront')
                await project.command('Emulation.setFocusEmulationEnabled', {'enabled': True})
                await project.until('Boolean(document.querySelector(".pdfViewer .page canvas"))', seconds=45)
                await point_note(project, project_id)
                if screenshot:
                    await project.screen(screenshot)
                await commit_tex_edit(project, folder)
                print('PDF annotation and Git commit verified.', flush=True)

            await click_label(shell, '返回当前文稿')
            await click(shell, 'button[aria-label="更多操作"]')
            await click_label(shell, '从 Papilot 导入改动')
            await click(shell, '[aria-label="查看 LaTeX 源码"]')
            source_field = find('textarea[aria-label="编辑 LaTeX 源码"]')
            await shell.until(f'{source_field}?.value.includes({json.dumps(REVISED)})', seconds=45)
            assert not list((bank / 'chapters').rglob('*.tex'))
            print('Git-reviewed source round-tripped to the Utopia draft.', flush=True)

            await click(shell, 'button[data-page=papilot]')
            await shell.until(f'{find(SLOT)}?.getAttribute("aria-busy") === "false"')
            async with websockets.connect(embedded_url) as project_socket:
                await resize_view(shell, Page(project_socket))
        await wait_for(process, lambda: native_close(process.pid, title='乌托邦高中物理题库助手'))
        return {'embedded': True, 'resizes': True, 'texCompiled': True, 'annotations': True,
                'gitCommit': True, 'draftRoundTrip': True, 'papilotVersion': state['version'], 'firstOpenMs': first_open_ms}

    return await launch(executable, development, root, bank, run, chromium_flags=RENDER_FLAGS)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--development', action='store_true')
    parser.add_argument('--screenshot', type=Path)
    args = parser.parse_args()
    with TemporaryDirectory(prefix='utopia-papilot-qa-', ignore_cleanup_errors=True) as temporary:
        root = Path(temporary).resolve()
        executable = args.exe.resolve(strict=True)
        if args.development:
            staging = root / 'electron'
            shutil.copytree(executable.parent, staging, copy_function=shutil.copyfile)
            executable = staging / executable.name
        result = asyncio.run(probe(executable, args.development, root, args.screenshot))
        print(json.dumps(result))
