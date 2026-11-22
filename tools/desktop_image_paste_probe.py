"""Exercise desktop image intake in an isolated Electron window on Windows.

Pass the stock Electron executable after building the renderer to verify native
IPC against deterministic ClipboardItems. With --packaged, pass the built app
executable to verify renderer paste, upload, draft persistence and recognition.
Both modes use a loopback OCR server and leave the Windows clipboard untouched.
"""
import argparse
import asyncio
import base64
import ctypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread

import pymupdf
import websockets

from desktop_speed_probe import Page, ROOT, target


PASTE = '粘贴图片'
SOURCE = 'textarea[aria-label="编辑 LaTeX 源码"]'
GROUP_CHAT = 'textarea[aria-label="发送给题库助手"]'
EDITOR_CHAT = '#wb-chat-prompt'
OCR_SEED = '截图回归测试：一辆小车受到水平恒力。'
RENDERED = "[...document.querySelectorAll('.wb-document canvas')].some(c => c.checkVisibility() && c.style.visibility === 'visible')"
WORKSPACE = "document.querySelector('.editor-mount')?.style.display === 'flex'"


class LocalOcr(BaseHTTPRequestHandler):
    payloads = []

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.payloads.append(data)
        recognition = {
            'status': 'ok',
            'fragments': [dict(question_id='q1', number='1', kind='stem', latex=OCR_SEED + r' $F=6\,\mathrm{N}$。')],
            'figures': [],
        }
        result = json.dumps({'choices': [{'message': {'content': json.dumps(recognition)}}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(result))
        self.end_headers()
        self.wfile.write(result)

    def log_message(self, *_):
        pass


class IntakePage(Page):
    chooser = None

    async def command(self, method, params=None):
        self.serial += 1
        await self.ws.send(json.dumps(dict(id=self.serial, method=method, params=params or {})))
        async with asyncio.timeout(40):
            async for raw in self.ws:
                message = json.loads(raw)
                if message.get('method') == 'Page.fileChooserOpened':
                    self.chooser = message['params']
                if message.get('id') == self.serial:
                    if 'error' in message:
                        raise RuntimeError(message['error'])
                    return message.get('result', {})

    async def paint(self):
        await self.js("Promise.race([new Promise(ok => requestAnimationFrame(() => requestAnimationFrame(ok))), new Promise(ok => setTimeout(ok, 140))])")

    async def route(self, path):
        await self.js(f"document.querySelector('button[data-page={path}]').click()")
        await self.until(f"document.querySelector('button[data-page={path}]')?.getAttribute('aria-current') === 'page'")

    async def trusted_click(self, label):
        x, y = await self.js(f"""(() => {{
          const el = [...document.querySelectorAll('button')].find(e => e.checkVisibility() && e.innerText.trim() === {json.dumps(label)});
          const rect = el.getBoundingClientRect();
          return [rect.x + rect.width / 2, rect.y + rect.height / 2];
        }})()""")
        mouse = dict(x=x, y=y, button='left', clickCount=1)
        await self.command('Input.dispatchMouseEvent', dict(type='mousePressed', buttons=1, **mouse))
        await self.command('Input.dispatchMouseEvent', dict(type='mouseReleased', buttons=0, **mouse))

    async def ctrl_v(self):
        keys = dict(key='v', code='KeyV', windowsVirtualKeyCode=86, nativeVirtualKeyCode=86, modifiers=2)
        await self.command('Input.dispatchKeyEvent', dict(type='rawKeyDown', **keys))
        await self.command('Input.dispatchKeyEvent', dict(type='keyUp', **keys))
        await self.paint()

    async def guarded_ctrl_v(self, selector):
        # Suppress Chromium's system clipboard read after observing app handling.
        # The application listener was registered before this bubble listener.
        await self.js(f"""(() => {{
          document.querySelector({json.dumps(selector)})?.focus();
          window.qaCtrlV = undefined;
          window.addEventListener('keydown', e => {{
            window.qaCtrlV = {{trusted: e.isTrusted, prevented: e.defaultPrevented}};
            e.preventDefault();
          }}, {{once: true}});
        }})()""")
        await self.ctrl_v()
        return await self.js('window.qaCtrlV')

    async def text_paste_event(self, selector, value):
        allowed = await self.js(f"""(() => {{
          const el = document.querySelector({json.dumps(selector)});
          if (!el || el !== document.activeElement) throw Error('No active chat field');
          const clipboard = new DataTransfer();
          clipboard.setData('text/plain', {json.dumps(value)});
          return el.dispatchEvent(new ClipboardEvent('paste', {{clipboardData: clipboard, bubbles: true, cancelable: true}}));
        }})()""")
        assert allowed, 'The chat intercepted a normal text paste.'
        # Untrusted DOM paste does not run the browser's default insertion.
        await self.command('Input.insertText', {'text': value})
        assert await self.js(f'document.querySelector({json.dumps(selector)}).value') == value

    async def raw_document(self):
        return await self.js("window.utopia.call('workspace.resume')")

    async def get_docs(self):
        return await self.js("window.utopia.call('workspace.list')")

    async def open_image(self, click, original):
        before = len(await self.get_docs())
        await click()
        await self.until(WORKSPACE)
        await self.until(RENDERED)
        document = await self.raw_document()
        assert document['inputKind'] == 'image', document
        assert document['source'] == '' and document['pdfPageCount'] == 1, document
        with pymupdf.open(document['pdfPath']) as pdf:
            assert len(pdf) == 1
            images = pdf[0].get_images(full=True)
            assert len(images) == 1, images
            embedded = pymupdf.Pixmap(pdf, images[0][0])
            assert (embedded.width, embedded.height) == (original.width, original.height)
            assert embedded.samples == original.samples, 'The original screenshot pixels changed during import.'
        assert len(await self.get_docs()) == before + 1
        return document

    async def close_editor(self):
        await self.js("document.querySelector('button[aria-label=返回]').click()")
        await self.until("!document.querySelector('.editor-mount') || document.querySelector('.editor-mount').style.display === 'none'")
        await self.paint()


def sequence():
    return ctypes.windll.user32.GetClipboardSequenceNumber()


def make_fixture(destination):
    source = ROOT / 'desktop' / 'sample' / '力与运动.pdf'
    with pymupdf.open(source) as pdf:
        page = pdf[0]
        clip = pymupdf.Rect(24, 25, page.rect.width - 24, 270)
        screenshot = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=clip, alpha=False)
        screenshot.save(destination)
    return screenshot


def make_launcher(directory):
    launcher = directory / 'launcher'
    launcher.mkdir()
    (launcher / 'package.json').write_text(json.dumps({'name': 'utopia-image-qa', 'main': 'main.cjs'}), encoding='utf-8')
    (launcher / 'main.cjs').write_text(r"""
const { clipboard, ClipboardItem } = require('electron');
const fs = require('node:fs');
const { pathToFileURL } = require('node:url');
const { clipboardMode, clipboardLog, screenshotFile, productionMain } = process.env;
Object.defineProperty(clipboard, 'read', { value: async () => {
  const mode = fs.readFileSync(clipboardMode, 'utf8');
  fs.appendFileSync(clipboardLog, mode + '\n');
  if (mode === 'image') return [new ClipboardItem({'image/png': new Blob([fs.readFileSync(screenshotFile)], {type:'image/png'})})];
  if (mode === 'text') return [new ClipboardItem({'text/plain': pathToFileURL(screenshotFile).href})];
  if (mode === 'remote-uri') return [new ClipboardItem({'text/uri-list': '# remote source\r\nhttps://example.test/photo.png\r\nfile://example.test/share/photo.png'})];
  if (mode === 'local-uri') return [new ClipboardItem({'text/uri-list': '# copied files\r\nhttps://example.test/photo.png\r\n' + pathToFileURL(screenshotFile).href})];
  return [];
}});
require(productionMain);
""".strip(), encoding='utf-8')
    return launcher


async def check_native_clipboard(page, root, screenshot_file, screenshot):
    mode_file, log_file = root / 'mode.txt', root / 'clipboard.log'
    negative_modes = ['text', 'remote-uri', 'empty']
    for mode in negative_modes:
        mode_file.write_text(mode, encoding='utf-8')
        await page.click(PASTE)
        await page.until("""[...document.querySelectorAll('[role=alert],[role=status]')]
              .some(el => el.checkVisibility() && /剪贴板.*(?:没有|未|不含).*图片/.test(el.innerText))""")
        assert await page.get_docs() == []
    assert log_file.read_text(encoding='utf-8').splitlines() == negative_modes
    print('PASS text-only, remote-URI and empty clipboard show actionable feedback', flush=True)

    mode_file.write_text('image', encoding='utf-8')
    native = await page.open_image(lambda: page.click(PASTE), screenshot)
    await page.close_editor()
    print('PASS explicit paste reads Electron ClipboardItem and opens a pristine, pixel-preserving draft', flush=True)

    async def shortcut():
        await page.js('document.activeElement.blur()')
        assert await page.guarded_ctrl_v('body') == {'trusted': True, 'prevented': True}

    keyboard = await page.open_image(shortcut, screenshot)
    await page.close_editor()
    assert keyboard['id'] != native['id']
    image_modes = negative_modes + ['image', 'image']
    assert log_file.read_text(encoding='utf-8').splitlines() == image_modes
    print('PASS trusted Ctrl+V takes the clipboard IPC path exactly once', flush=True)

    mode_file.write_text('local-uri', encoding='utf-8')
    copied = await page.open_image(lambda: page.click(PASTE), screenshot)
    assert copied['title'] == screenshot_file.name, copied
    await page.close_editor()
    image_modes.append('local-uri')
    assert log_file.read_text(encoding='utf-8').splitlines() == image_modes
    print('PASS Explorer-style file URI preserves the original file name with Chinese characters and a space', flush=True)
    return image_modes


async def run(args, root):
    screenshot_file = root / '截图 输入.png'
    screenshot = make_fixture(screenshot_file)
    profile, bank = root / 'profile', root / 'bank'
    (bank / 'chapters').mkdir(parents=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), LocalOcr)
    Thread(target=server.serve_forever, daemon=True).start()
    (bank / '.env').write_text(
        f'AI_BASE_URL=http://127.0.0.1:{server.server_port}/v1\nAI_API_KEY=isolated-fixture\nAI_MODEL_NAME=local-ocr-fixture\n',
        encoding='utf-8',
    )
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, UTOPIA_DESKTOP_PROFILE=str(profile))
    env.pop('UTOPIA_DESKTOP_VITE_URL', None)
    executable = args.exe.resolve()
    command = [str(executable)]
    if not args.packaged:
        # A checkout's Electron files can be labeled Low Integrity on Windows.
        electron = root / 'electron'
        shutil.copytree(executable.parent, electron, copy_function=shutil.copyfile)
        command = [str(electron / executable.name), str(make_launcher(root))]
        env.update(clipboardMode=str(root / 'mode.txt'), clipboardLog=str(root / 'clipboard.log'),
                   screenshotFile=str(screenshot_file), productionMain=str(ROOT / 'desktop' / 'main.cjs'))
    command.extend([f'--remote-debugging-port={port}', '--library', str(bank)])
    console = root / 'electron.log'
    with console.open('wb') as output:
        process = subprocess.Popen(command, env=env, cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW,
                                   stdout=output, stderr=output)
    try:
        async with asyncio.timeout(25):
            while not (url := await asyncio.to_thread(target, port)):
                if process.poll() is not None:
                    raise RuntimeError(f'Electron exited before startup with code {process.returncode}.')
                await asyncio.sleep(.1)
        async with websockets.connect(url, max_size=12_000_000) as ws:
            page = IntakePage(ws)
            await page.command('Page.bringToFront')
            await page.command('Emulation.setFocusEmulationEnabled', {'enabled': True})
            await page.command('Emulation.setDeviceMetricsOverride', {'width': 1400, 'height': 920, 'deviceScaleFactor': 1, 'mobile': False})
            await page.until("!!document.querySelector('.metrics strong') && /^\\d/.test(document.querySelector('.metrics strong').textContent)")
            await page.js("window.utopia.call('ui.save', {provider:'api'})")
            await page.route('entry')
            await page.until("document.querySelector('h1')?.innerText.includes('图片识别')")
            await page.paint()
            intake = await page.js("document.querySelector('.intake').innerText")
            assert all(label in intake for label in ('Ctrl+V', PASTE, '选择图片', '开始识别图片')), intake
            clean_screen = await page.command('Page.captureScreenshot', {'format': 'png'}) if args.screenshot else None
            baseline = sequence()
            if args.packaged:
                print('Packaged smoke uses renderer paste and upload with a private clipboard fixture.', flush=True)
            else:
                image_modes = await check_native_clipboard(page, root, screenshot_file, screenshot)

            async def png_event():
                fixture = base64.b64encode(screenshot_file.read_bytes()).decode()
                allowed = await page.js(f"""(() => {{
                  const pixels = Uint8Array.from(atob({json.dumps(fixture)}), ch => ch.charCodeAt(0));
                  const data = new DataTransfer();
                  data.items.add(new File([pixels], '剪贴板事件测试.png', {{type:'image/png'}}));
                  return document.body.dispatchEvent(new ClipboardEvent('paste', {{clipboardData:data, bubbles:true, cancelable:true}}));
                }})()""")
                assert not allowed, 'The image paste event was not handled.'

            await page.open_image(png_event, screenshot)
            await page.close_editor()
            print('PASS renderer paste event with real PNG uses the same workspace path', flush=True)

            assert await page.guarded_ctrl_v(GROUP_CHAT) == {'trusted': True, 'prevented': False}
            await page.text_paste_event(GROUP_CHAT, '题库助手普通文字')
            assert not await page.js(WORKSPACE)
            print('PASS image intake leaves Ctrl+V and text paste alone in the bank chat', flush=True)

            async def upload():
                await page.command('Page.enable')
                await page.command('DOM.enable')
                await page.command('Page.setInterceptFileChooserDialog', {'enabled': True})
                await page.trusted_click('选择图片')
                async with asyncio.timeout(4):
                    while not page.chooser:
                        await page.paint()
                assert page.chooser['mode'] == 'selectSingle', page.chooser
                await page.command('DOM.setFileInputFiles', {
                    'backendNodeId': page.chooser['backendNodeId'], 'files': [str(screenshot_file)],
                })

            uploaded = await page.open_image(upload, screenshot)
            assert uploaded['title'] == screenshot_file.name, uploaded
            assert await page.guarded_ctrl_v(EDITOR_CHAT) == {'trusted': True, 'prevented': False}
            await page.text_paste_event(EDITOR_CHAT, '题目助手普通文字')
            if not args.packaged:
                assert (root / 'clipboard.log').read_text(encoding='utf-8').splitlines() == image_modes
            await page.close_editor()
            print('PASS normal upload launches the file chooser and opens a draft; editor chat text is unaffected', flush=True)

            assert len(LocalOcr.payloads) == 0, 'Importing an image unexpectedly called OCR.'
            draft = f"[...document.querySelectorAll('.draft-link')].find(b => b.querySelector('span')?.textContent.trim().endsWith({json.dumps(screenshot_file.name)}))"
            await page.until(f"!!{draft}")
            await page.js(f"{draft}.click()")
            await page.until(WORKSPACE)
            assert (await page.raw_document())['id'] == uploaded['id']
            await page.click('开始识别图片')
            await page.until(f"document.querySelector({json.dumps(SOURCE)})?.value.includes({json.dumps(OCR_SEED)})", seconds=45)
            assert OCR_SEED in (await page.raw_document())['source']
            assert len(LocalOcr.payloads) == 1, len(LocalOcr.payloads)
            payload = LocalOcr.payloads[0]
            image = next(part['image_url']['url'] for part in payload['messages'][0]['content'] if part['type'] == 'image_url')
            assert payload['model'] == 'local-ocr-fixture' and image.startswith('data:image/png;base64,')
            assert bool(pymupdf.Pixmap(base64.b64decode(image.split(',', 1)[1])).width)
            print('PASS reopened image draft explicitly runs real recognition IPC with a loopback model and receives editable LaTeX', flush=True)

            assert sequence() == baseline, 'System clipboard sequence changed during testing.'
            if clean_screen:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                args.screenshot.write_bytes(base64.b64decode(clean_screen['data']))
            await page.command('Page.close')
    except BaseException:
        print(console.read_text(encoding='utf-8', errors='replace'), file=sys.stderr)
        raise
    finally:
        server.shutdown()
        server.server_close()
        try:
            await asyncio.to_thread(process.wait, 3)
        except subprocess.TimeoutExpired:
            process.terminate()
            await asyncio.to_thread(process.wait, 6)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, required=True, help='Stock Electron executable or built desktop application.')
    parser.add_argument('--packaged', action='store_true', help='Run an unpacked or installed build directly and exercise renderer paste.')
    parser.add_argument('--screenshot', type=Path, help='Write a screenshot of the clean image intake page after the probe passes.')
    args = parser.parse_args()
    if not args.exe.is_file():
        parser.error(f'Executable not found: {args.exe}')
    with TemporaryDirectory(prefix='utopia-image-qa-', ignore_cleanup_errors=True) as temporary:
        asyncio.run(run(args, Path(temporary)))
