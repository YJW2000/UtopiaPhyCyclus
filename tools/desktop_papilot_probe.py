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

from desktop_maintenance_probe import add_question, launch, native_close, park_test_window, source_files, wait_for
from desktop_speed_probe import Page


SOURCE = r'''
\documentclass[UTF8,fontset=fandol]{ctexart}
\begin{document}
题库项目验收：电阻的阻值为 $6\,\Omega$，电压为 $3\,\mathrm{V}$。
\[ I = \frac{U}{R} = 0.5\,\mathrm{A} \]
\par Polish the explanation.
\par Check the units.
\par Remove the repeated answer.
\end{document}
'''.strip()
ORIGINAL = '题库项目验收'
REVISED = '编译和版本管理验收'
NOTE = '校核电流和单位'
STAMP_NAME = '检查单位'
STAMP_TEXT = '逐项核对所选推导中的物理量和单位。'
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
    button = f'[...document.querySelectorAll("button")].find(b => b.checkVisibility() && !b.disabled && b.textContent.trim() === {json.dumps(label)})'
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


async def mark_text(page, shortcut, word):
    await click(page, f'.pdf-rail [aria-keyshortcuts="{shortcut}"]')
    bounds = await page.js(f'''(() => {{
        const word = {json.dumps(word)};
        const span = [...document.querySelectorAll('.pdfViewer .textLayer span')].find(
            item => item.firstChild?.nodeType === Node.TEXT_NODE && item.firstChild.data.includes(word));
        if (!span) throw Error('Missing fixture text ' + word);
        span.scrollIntoView({{block: 'nearest'}});
        const node = span.firstChild, start = node.data.indexOf(word);
        const range = new Range();
        range.setStart(node, start);
        range.setEnd(node, start + word.length);
        const r = range.getBoundingClientRect();
        return {{left: r.left + 1, right: r.right - 1, y: r.top + r.height / 2}};
    }})()''')
    x, end, y = bounds['left'], bounds['right'], bounds['y']
    await page.command('Input.dispatchMouseEvent', {'type': 'mousePressed', 'button': 'left', 'buttons': 1, 'clickCount': 1, 'x': x, 'y': y})
    for step in range(1, 6):
        await page.command('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'button': 'left', 'buttons': 1, 'x': x + (end - x) * step / 5, 'y': y})
    await page.command('Input.dispatchMouseEvent', {'type': 'mouseReleased', 'button': 'left', 'clickCount': 1, 'x': end, 'y': y})


async def set_magic_default(shell, screenshot):
    await click(shell, 'button[data-page=settings]')
    await click(shell, '.magic-pen-settings button[aria-label="编辑 润色"]')
    for selector, value in [('input', STAMP_NAME), ('textarea', STAMP_TEXT)]:
        field = find('.magic-pen-settings-form ' + selector)
        await shell.js(f'{field}.focus(); {field}.select()')
        await shell.command('Input.insertText', {'text': value})
        await shell.until(f'{field}.value === {json.dumps(value)}')
    await click(shell, '.magic-pen-settings-form [type=submit]')
    row = find('.magic-pen-settings-list li:first-child')
    await shell.until(f'{row}?.textContent.includes({json.dumps(STAMP_TEXT)})')
    if screenshot:
        await shell.js(f'{find(".magic-pen-settings")}.scrollIntoView({{block:"start"}})')
        await shell.screen(screenshot.with_stem(screenshot.stem + '-settings'))


async def verify_marking_tools(page, project, screenshot=None):
    endpoint = json.dumps(f'/api/projects/{project}/manuscript')
    notes = f'fetch({endpoint}).then(r => r.json()).then(s => s.annotations)'
    await page.until("document.querySelector('.textLayer')?.textContent.includes('Remove')")
    await mark_text(page, 'Q', 'Polish')
    await page.until(f'{find(".stamp-wheel .stamp-slot")}?.checkVisibility()')
    if screenshot:
        await page.screen(screenshot.with_stem(screenshot.stem + '-wheel'))
    await click(page, '.stamp-wheel .stamp-slot')
    await page.until(f'({notes}).then(a => a.some(n => n.stamp && n.quote.includes("Polish") && n.rects.length))')
    await mark_text(page, 'T', 'Check')
    await page.until("document.activeElement?.matches('.pdf-note-card textarea')")
    await page.command('Input.insertText', {'text': NOTE})
    if screenshot:
        await page.screen(screenshot.with_stem(screenshot.stem + '-comment'))
    for kind in ('keyDown', 'keyUp'):
        await page.command('Input.dispatchKeyEvent', {'type': kind, 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13})
    await page.until(f'({notes}).then(a => a.some(n => n.comment === {json.dumps(NOTE)} && n.quote.includes("Check")))')
    await mark_text(page, 'D', 'Remove')
    await page.until(f'({notes}).then(a => a.some(n => n.kind === "delete" && n.quote.includes("Remove") && n.rects.length))')
    strike = '.note-mark[data-kind=delete]'
    tag = '.note-tag[data-kind=delete]'
    await page.until(f'{find(tag)}?.checkVisibility() && Number({find(tag)}.textContent) > 0')
    assert await page.js(f'getComputedStyle({find(strike)}).backgroundImage.includes("linear-gradient")')
    await click(page, '.rail-history [aria-keyshortcuts="Control+Z"]')
    await page.until(f'!{find(tag)}')
    await click(page, '.rail-history [aria-keyshortcuts="Control+Shift+Z"]')
    await page.until(f'{find(tag)}?.checkVisibility()')
    if screenshot:
        await page.screen(screenshot.with_stem(screenshot.stem + '-delete'))
    print('Selected real PDF text, used the quick-note wheel and inline comment, struck text and undid/redid its numbered mark.', flush=True)


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
    if not await page.js(f'{find(".git-link")}?.checkVisibility()'):
        await click(page, '.sidebar-toggle')
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
    chapters = bank / 'chapters'
    question_path = chapters / add_question(chapters, 1)
    originals = source_files(bank)
    runtime_log = root / 'profile/papilot/logs/backend.log'

    async def run(process, port):
        shell_url = await wait_for(process, lambda: desktop_target(port), seconds=120)
        async with websockets.connect(shell_url) as shell_socket:
            shell = Page(shell_socket)
            await shell.until(f'{find("button[data-page=tex]")}?.checkVisibility()')
            assert await shell.js('!document.querySelector(".app-nav [data-page=papilot]")')
            park_test_window(process.pid)
            assert not await shell.js(f'Boolean({find(SLOT)})')
            assert not runtime_log.exists()
            assert desktop_target(port, embedded=True) is None

            await click(shell, 'button[data-page=tex]')
            await click(shell, 'summary')
            await shell.command('Emulation.setFocusEmulationEnabled', {'enabled': True})
            await shell.js('document.querySelector("textarea.paste-tex").focus()')
            await shell.command('Input.insertText', {'text': SOURCE})
            await shell.until(f'{find("textarea.paste-tex")}?.value === {json.dumps(SOURCE)}')
            started = perf_counter()
            await click(shell, '.paste-tex + button')
            await shell.until(f'{find(SLOT)}?.checkVisibility() || {find(".error-strip")}?.checkVisibility()', seconds=180)
            assert not (error := await shell.js(f'{find(".error-strip")}?.textContent')), error
            assert await shell.js('Boolean(document.querySelector("[data-page=tex][aria-current=page]"))')
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
                assert source_files(bank) == originals
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
                try:
                    await project.until('Boolean(document.querySelector(".pdfViewer .page canvas"))', seconds=90)
                except TimeoutError as error:
                    state = await project.js('({ title: document.title, visibility: document.visibilityState, message: document.body.innerText.slice(-1200) })')
                    raise AssertionError(f'Compiled manuscript did not render: {state}') from error
                await verify_marking_tools(project, project_id, screenshot)
                await point_note(project, project_id)
                if screenshot:
                    await project.screen(screenshot)
                await commit_tex_edit(project, folder)
                print('PDF annotation and Git commit verified.', flush=True)

            await click_label(shell, 'LaTeX 源码')
            await shell.until(f'!{find(SLOT)}?.checkVisibility()')
            source_field = find('textarea[aria-label="编辑 LaTeX 源码"]')
            await shell.until(f'{source_field}?.value.includes({json.dumps(REVISED)})', seconds=45)
            assert source_files(bank) == originals
            print('Git-reviewed source round-tripped to the Utopia draft.', flush=True)

            await click(shell, 'button[aria-label="返回 PDF 校订"]')
            await shell.until(f'{find(SLOT)}?.getAttribute("aria-busy") === "false"')
            async with websockets.connect(embedded_url) as project_socket:
                project = Page(project_socket)
                await resize_view(shell, project)

                await set_magic_default(shell, screenshot)
                await click(shell, 'button[data-page=tex]')
                await click(shell, 'button.draft-link')
                await shell.until(f'{find(SLOT)}?.getAttribute("aria-busy") === "false"', seconds=75)
                await project.until("document.querySelector('.textLayer')?.textContent.includes('explanation')", seconds=90)
                await mark_text(project, 'Q', 'explanation')
                slot = f'.stamp-slot[aria-label="{STAMP_NAME}"]'
                await project.until(f'{find(slot)}?.checkVisibility()')
                await project.js(f'{find(slot)}.focus()')
                await project.until(f'{find(".stamp-caption")}?.textContent === {json.dumps(STAMP_TEXT)}')
                if screenshot:
                    await project.screen(screenshot.with_stem(screenshot.stem + '-custom-wheel'))
                await click(project, slot)
                await project.until(f'fetch({endpoint}).then(r => r.json()).then(s => s.annotations.some(n => n.stamp === {json.dumps(STAMP_NAME)} && n.comment === {json.dumps(STAMP_TEXT)} && n.quote.includes("explanation")))')
                print('Saved a custom sentence in desktop Settings and applied it from the existing PDF wheel.', flush=True)

                await click(shell, 'button[data-page=browse]')
                selected = find('.question-link[aria-current=true]')
                await shell.until(f'{selected}?.checkVisibility()', seconds=60)
                dock = find(f'.browse-workbench {SLOT}')
                await shell.until(f'{dock}?.checkVisibility() && {dock}.getAttribute("aria-busy") === "false"', seconds=60)
                assert await shell.js('Boolean(document.querySelector("[data-page=browse][aria-current=page]"))')
                await project.until(f'document.title.includes({json.dumps(question_path.stem)})', seconds=45)
                browse_projects = (await project.js(state_read))['projects']
                (browse_project,) = (item for item in browse_projects if item['id'] != project_id)
                browse_folder = Path(browse_project['path']).resolve(strict=True)
                assert browse_folder.is_relative_to(root)
                exported_source = (browse_folder.parent / 'original.tex').read_text(encoding='utf-8')
                assert exported_source == question_path.read_text(encoding='utf-8')
                assert source_files(bank) == originals
                print('Browse automatically opened the selected bank question in its right-hand workbench.', flush=True)
        await wait_for(process, lambda: native_close(process.pid, title='乌托邦高中物理题库助手'))
        return {'embedded': True, 'documentScoped': True, 'lazyStartup': True, 'resizes': True, 'browseProject': True,
                'texCompiled': True, 'annotations': True,
                'gitCommit': True, 'draftRoundTrip': True, 'papilotVersion': state['version'], 'firstOpenMs': first_open_ms}

    async def check_restart(process, port):
        url = await wait_for(process, lambda: desktop_target(port), seconds=120)
        async with websockets.connect(url) as socket:
            shell = Page(socket)
            await click(shell, 'button[data-page=settings]')
            row = find('.magic-pen-settings-list li:first-child')
            await shell.until(f'{row}?.textContent.includes({json.dumps(STAMP_TEXT)})', seconds=90)
            assert desktop_target(port, embedded=True) is None
            await click_label(shell, '恢复内置语句')
            await shell.until(f'{row}?.querySelector("strong").textContent === "润色"')
        await wait_for(process, lambda: native_close(process.pid, title='乌托邦高中物理题库助手'))

    try:
        result = await launch(executable, development, root, bank, run, chromium_flags=RENDER_FLAGS, shutdown_timeout=100)
        await launch(executable, development, root, bank, check_restart, chromium_flags=RENDER_FLAGS, shutdown_timeout=100)
        return {**result, 'customWheelInstruction': True, 'persistedAfterRestart': True}
    except Exception:
        if runtime_log.is_file():
            print(runtime_log.read_text(encoding='utf-8', errors='replace'))
        raise


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
