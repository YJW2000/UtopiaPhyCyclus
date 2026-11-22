"""Exercise the assistant dividers in Electron with a disposable profile and bank."""
import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
from tempfile import TemporaryDirectory

import websockets

from desktop_speed_probe import Page, ROOT, target


WIDE = 1400
NARROW = 850


class PanePage(Page):
    async def paint(self):
        await self.js('''Promise.race([
            new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))),
            new Promise(resolve => setTimeout(resolve, 180))])''')

    async def select(self, selector):
        return await self.js(f'''(() => {{
            const el = [...document.querySelectorAll({json.dumps(selector)})].find(e => e.checkVisibility());
            if (!el) return null;
            const rect = el.getBoundingClientRect();
            return {{x: rect.x, y: rect.y, w: rect.width, h: rect.height}};
        }})()''')

    async def mouse(self, kind, x, y, *, down=False):
        params = dict(type=kind, x=x, y=y, buttons=int(down), pointerType='mouse')
        if kind != 'mouseMoved':
            params.update(button='left', clickCount=1)
        elif down:
            params['button'] = 'left'
        await self.command('Input.dispatchMouseEvent', params)

    async def interact(self, selector, dx=None, y_fraction=0.5):
        rect = await self.select(selector)
        assert rect and rect['w'] > 0 and rect['h'] > 0, (selector, rect)
        x, y = rect['x'] + rect['w'] / 2, rect['y'] + rect['h'] * y_fraction
        await self.mouse('mouseMoved', x, y)
        await self.mouse('mousePressed', x, y, down=True)
        if dx is not None:
            for step in range(1, 7):
                await self.mouse('mouseMoved', x + dx * step / 6, y, down=True)
        await self.mouse('mouseReleased', x + (dx or 0), y)
        await self.paint()

    async def keyboard(self, selector, key, code, number):
        active = await self.js(f'''(() => {{
            const el = [...document.querySelectorAll({json.dumps(selector)})].find(e => e.checkVisibility());
            el?.focus(); return document.activeElement === el;
        }})()''')
        assert active, (selector, key)
        params = dict(key=key, code=code, windowsVirtualKeyCode=number, nativeVirtualKeyCode=number)
        text = '\r' if key == 'Enter' else ''
        await self.command('Input.dispatchKeyEvent', dict(type='keyDown', text=text, unmodifiedText=text, **params))
        await self.command('Input.dispatchKeyEvent', dict(type='keyUp', **params))
        await self.paint()

    async def type_text(self, selector, value):
        active = await self.js(f'''(() => {{
            const el = [...document.querySelectorAll({json.dumps(selector)})].find(e => e.checkVisibility());
            if (!el) return false;
            el.focus(); el.setSelectionRange(el.value.length, el.value.length);
            return document.activeElement === el;
        }})()''')
        assert active, selector
        await self.command('Input.insertText', {'text': value})
        await self.paint()

    async def viewport(self, width, *, scale=1):
        await self.command('Emulation.setDeviceMetricsOverride', dict(width=width, height=800, deviceScaleFactor=scale, mobile=False))
        await self.paint()
        assert await self.js('innerWidth') == width

    async def pane(self):
        state = await self.js('''(() => {
            const visible = selector => [...document.querySelectorAll(selector)].filter(e => e.checkVisibility());
            const [toggle] = visible('.pane-divider-toggle');
            if (!toggle) return null;
            const divider = toggle.closest('.pane-divider');
            const grip = divider.querySelector('.pane-divider-grip');
            const panel = document.getElementById(toggle.getAttribute('aria-controls'));
            const container = divider.parentElement;
            return {toggles: visible('.pane-divider-toggle').length, label: toggle.getAttribute('aria-label'),
                open: toggle.getAttribute('aria-expanded') === 'true', sibling: divider.nextElementSibling === panel,
                width: panel && panel.checkVisibility() ? panel.getBoundingClientRect().width : 0,
                container: container.getBoundingClientRect().width, scroll: container.scrollWidth - container.clientWidth,
                min: Number(grip?.getAttribute('aria-valuemin')), max: Number(grip?.getAttribute('aria-valuemax')),
                current: Number(grip?.getAttribute('aria-valuenow'))};
        })()''')
        assert state and state['toggles'] == 1 and state['sibling'], state
        return state


TOGGLE = '.pane-divider-toggle'
GRIP = '.pane-divider-grip'
DOC_GRIP = '.wb-divider[aria-label="调整两栏宽度"]'
GROUP_DRAFT = 'textarea[aria-label="发送给题库助手"]'
EDITOR_DRAFT = '#wb-chat-prompt'
SOURCE = 'textarea[aria-label="编辑 LaTeX 源码"]'
RENDERED = "[...document.querySelectorAll('.wb-document canvas')].some(c => c.checkVisibility() && c.style.visibility === 'visible')"


def assert_width(state, *, fraction, maximum=None):
    assert state['open'], state
    limit = min(maximum or state['container'], fraction * state['container'])
    assert state['width'] > 0 and state['width'] <= limit + 2, (state, limit)
    assert state['min'] <= state['current'] <= state['max'] and abs(state['current'] - state['width']) <= 2, state
    assert state['scroll'] <= 2, state


async def check_pane(page, draft_selector, *, name, fraction, maximum=None):
    text = f'{name} isolated unsent draft'
    await page.type_text(draft_selector, text)
    start = await page.pane()
    assert name in start['label']
    assert_width(start, fraction=fraction, maximum=maximum)
    await page.interact(TOGGLE)
    closed = await page.pane()
    assert not closed['open'] and closed['width'] == 0, closed
    await page.keyboard(TOGGLE, ' ', 'Space', 32)
    assert (await page.pane())['open']
    await page.keyboard(TOGGLE, 'Enter', 'Enter', 13)
    assert not (await page.pane())['open']
    await page.interact(TOGGLE, -100)
    reopened = await page.pane()
    assert_width(reopened, fraction=fraction, maximum=maximum)
    # A drag from collapsed may start at the minimum width.
    await page.interact(TOGGLE, -80)
    reopened = await page.pane()
    await page.interact(TOGGLE, 58)
    right = await page.pane()
    assert right['open'] and right['width'] < reopened['width'] - 20, (right, reopened)
    await page.interact(TOGGLE, -56)
    left = await page.pane()
    assert left['open'] and left['width'] > right['width'] + 20, (left, right)
    await page.interact(GRIP, 38, y_fraction=.23)
    grip_right = await page.pane()
    assert grip_right['width'] < left['width'] - 15, (grip_right, left)
    await page.interact(GRIP, -68, y_fraction=.23)
    grip_left = await page.pane()
    assert grip_left['width'] > grip_right['width'] + 20, (grip_left, grip_right)
    await page.keyboard(GRIP, 'ArrowRight', 'ArrowRight', 39)
    key_right = await page.pane()
    assert key_right['width'] < grip_left['width'] - 5, (key_right, grip_left)
    await page.keyboard(TOGGLE, 'ArrowLeft', 'ArrowLeft', 37)
    key_left = await page.pane()
    assert key_left['width'] > key_right['width'] + 5, (key_left, key_right)
    assert await page.js(f'document.querySelector({json.dumps(draft_selector)}).value') == text
    # Wide preference survives a temporary narrow, higher-DPI viewport.
    await page.interact(GRIP, -370, y_fraction=.23)
    wide = await page.pane()
    assert_width(wide, fraction=fraction, maximum=maximum)
    await page.viewport(NARROW, scale=1.25)
    narrow = await page.pane()
    assert_width(narrow, fraction=fraction, maximum=maximum)
    assert abs(await page.js('devicePixelRatio') - 1.25) < .01
    assert narrow['width'] < wide['width'] - 20 and narrow['max'] < wide['max'], (narrow, wide)
    await page.viewport(560, scale=1.5)
    compact = await page.pane()
    assert_width(compact, fraction=fraction, maximum=maximum)
    assert compact['width'] < narrow['width'], (compact, narrow)
    await page.viewport(WIDE)
    assert abs((await page.pane())['width'] - wide['width']) <= 2, wide
    await page.interact(GRIP, 66, y_fraction=.23)
    final = await page.pane()
    assert 30 < wide['width'] - final['width'] < 100, (final, wide)
    return {key: round(state['width']) for key, state in [
        ('start', start), ('wide', wide), ('narrow125', narrow), ('compact150', compact), ('saved', final),
    ]}


async def open_demo(page):
    await page.js("document.querySelector('button[data-page=tex]').click()")
    await page.click('打开演示题 ↗')
    await page.until(RENDERED)


async def editor_doc_panes(page):
    await page.js("document.querySelector('.wb-layout-tools button[aria-expanded]').click()")
    await page.click('LaTeX ＋ PDF 预览')
    await page.until(RENDERED)
    source, pdf = '.wb-document-panes .wb-source', '.wb-document-panes .wb-document-panel-grow'

    async def sizes():
        return [(await page.select(selector))['w'] for selector in (source, pdf)]

    start = await sizes()
    await page.interact(DOC_GRIP, -65)
    left = await sizes()
    assert left[0] < start[0] - 30 and left[1] > start[1] + 30, (start, left)
    await page.interact(DOC_GRIP, 85)
    right = await sizes()
    assert right[0] > left[0] + 50 and right[1] < left[1] - 50, (left, right)
    await page.keyboard(DOC_GRIP, 'ArrowLeft', 'ArrowLeft', 37)
    keyboard = await sizes()
    assert keyboard[0] < right[0] - 8 and keyboard[1] > right[1] + 8, (right, keyboard)
    await page.until(RENDERED)
    return {key: [round(width) for width in values] for key, values in [('start', start), ('left', left), ('right', right), ('keyboard', keyboard)]}


async def isolated_run(args, profile, bank, run):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    command = [str(args.exe.resolve()), *([str(ROOT / 'desktop')] if args.development else []),
               f'--remote-debugging-port={port}', '--library', str(bank)]
    env = {**os.environ, 'UTOPIA_DESKTOP_PROFILE': str(profile)}
    env.pop('UTOPIA_DESKTOP_VITE_URL', None)
    process = subprocess.Popen(command, cwd=ROOT, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        async with asyncio.timeout(30):
            while not (url := await asyncio.to_thread(target, port)):
                assert process.poll() is None, f'Electron exited with code {process.returncode}'
                await asyncio.sleep(.05)
        async with websockets.connect(url, max_size=8_000_000) as ws:
            page = PanePage(ws)
            await page.command('Page.bringToFront')
            await page.command('Emulation.setFocusEmulationEnabled', {'enabled': True})
            await page.until("!!document.querySelector('.metrics strong') && /^\\d/.test(document.querySelector('.metrics strong').textContent)")
            await page.viewport(WIDE)
            try:
                return await run(page)
            finally:
                await page.command('Page.close')
    finally:
        try:
            await asyncio.to_thread(process.wait, 8)
        except subprocess.TimeoutExpired:
            process.terminate()
            await asyncio.to_thread(process.wait, 8)


async def main(args):
    marker = '\n% pane QA saved draft\n'
    results = {}
    screenshot = None

    async def first(page):
        nonlocal screenshot
        results['library'] = await check_pane(page, GROUP_DRAFT, name='题库助手', maximum=520, fraction=.44)
        await open_demo(page)
        if args.screenshot:
            await page.paint()
            screenshot = await page.command('Page.captureScreenshot', {'format': 'png'})
        results['editor'] = await check_pane(page, EDITOR_DRAFT, name='题目助手', maximum=680, fraction=.52)
        results['document_split'] = await editor_doc_panes(page)
        await page.type_text(SOURCE, marker)
        assert marker in await page.js(f'document.querySelector({json.dumps(SOURCE)}).value')
        await page.interact(TOGGLE)
        assert marker in await page.js(f'document.querySelector({json.dumps(SOURCE)}).value')
        await page.interact(TOGGLE)
        await page.interact('button[aria-label="返回"]')
        await page.until('document.querySelector(".editor-mount")?.style.display === "none"')
        await page.until("(async()=> (await window.utopia.call('workspace.resume')).source.includes(" + json.dumps(marker) + '))()')

    async def second(page):
        saved_library = (await page.pane())['width']
        await open_demo(page)
        saved_editor = (await page.pane())['width']
        await page.until(f'document.querySelector({json.dumps(SOURCE)})?.value.includes({json.dumps(marker)})')
        assert abs(saved_library - results['library']['saved']) <= 2, (results['library'], saved_library)
        assert abs(saved_editor - results['editor']['saved']) <= 2, (results['editor'], saved_editor)
        results['saved_after_restart'] = True

    with TemporaryDirectory(prefix='utopia-pane-qa-', ignore_cleanup_errors=True) as temporary:
        root = Path(temporary)
        bank, profile = root / 'bank', root / 'profile'
        (bank / 'chapters').mkdir(parents=True)
        if args.development:
            # A workspace executable can inherit a Low Mandatory Level on
            # Windows. Copy the stock Electron files into a normal temp path.
            electron = root / 'electron'
            shutil.copytree(args.exe.resolve().parent, electron, copy_function=shutil.copyfile)
            args.exe = electron / args.exe.name
        await isolated_run(args, profile, bank, first)
        await isolated_run(args, profile, bank, second)
    if screenshot:
        args.screenshot.parent.mkdir(parents=True, exist_ok=True)
        args.screenshot.write_bytes(base64.b64decode(screenshot['data']))
    print(json.dumps(results, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--development', action='store_true', help='Launch a compiled renderer with the Electron development binary.')
    parser.add_argument('--screenshot', type=Path, help='Write a screenshot of the clean demo after all checks pass.')
    asyncio.run(main(parser.parse_args()))
