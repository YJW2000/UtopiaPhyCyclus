"""Exercise desktop image intake in an isolated Electron window on Windows.

Pass the stock Electron executable after building the renderer to verify native
IPC against deterministic ClipboardItems. With --packaged, pass the built app
executable to verify renderer paste, Agent selection, compilation and editing.
Both modes use an isolated Codex CLI fixture and leave the Windows clipboard untouched.
Add --cancel to check Antigravity model selection, stopping the active OCR subprocess, and retry latency.
"""
import argparse
import asyncio
import base64
import ctypes
from ctypes import wintypes
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from urllib.parse import quote
from urllib.request import urlopen

import pymupdf
import websockets

from desktop_speed_probe import Page, ROOT, target
from desktop_papilot_probe import RENDER_FLAGS, desktop_target, point_note
from desktop_maintenance_probe import park_test_window, wait_for


PASTE = '粘贴图片'
RETRY = '重试识别'
STOP = '停止识别'
IMAGE = '.intake img[alt="原图"]'
INTAKE_ERROR = '.image-intake-error[role=alert]'
SOURCE = 'textarea[aria-label="编辑 LaTeX 源码"]'
GROUP_CHAT = 'textarea[aria-label="发送给题库助手"]'
EDITOR_CHAT = '#wb-chat-prompt'
OCR_SEED = '截图回归测试：一辆小车受到水平恒力。'
EXPLICIT_MODEL = 'fixture-vision-v2'
ORCHESTRATOR = 'Orchestrator (Carbon)'
WORKSPACE = "document.querySelector('.editor-mount')?.style.display === 'flex'"


def make_codex_fixture(root):
    fixture = root / 'codex-fixture'
    fixture.mkdir()
    recognition = {
        'status': 'ok',
        'fragments': [dict(question_id='q1', number='1', kind='stem', latex=OCR_SEED + r' $F=6\,\mathrm{N}$。[[FIG:diagram]]')],
        'figures': [dict(id='diagram', rect=[0.25, 0.2, 0.75, 0.68])],
    }
    (fixture / 'recognition.json').write_text(json.dumps(recognition), encoding='ascii')
    source = fixture / 'CodexFixture.cs'
    source.write_text(r"""
using System;
using System.IO;
using System.Text;
using System.Threading;

class CodexFixture {
    static int Main(string[] args) {
        if (args.Length == 1 && args[0] == "--version") {
            Console.WriteLine("codex-cli 0.0.0");
            return 0;
        }
        var image = Array.IndexOf(args, "--image");
        var output = Array.IndexOf(args, "-o");
        if (image < 0 || image + 1 >= args.Length || output < 0 || output + 1 >= args.Length ||
            Array.LastIndexOf(args, "--image") != image || Array.IndexOf(args, "exec") < 0) {
            return 2;
        }
        Console.InputEncoding = Encoding.UTF8;
        if (!Console.In.ReadToEnd().Contains("[[FIG:")) {
            return 3;
        }
        var fixture = AppDomain.CurrentDomain.BaseDirectory;
        if (File.Exists(Path.Combine(fixture, "preview-only"))) return 1;
        var expectedModel = Path.Combine(fixture, "expected-model");
        if (File.Exists(expectedModel)) {
            var model = Array.IndexOf(args, "--model");
            if (model < 0 || model + 1 >= args.Length || args[model + 1] != File.ReadAllText(expectedModel)) {
                Console.Error.WriteLine("The selected model was not passed to Codex");
                return 4;
            }
        }
        var first = Path.Combine(fixture, "first.png");
        if (!File.Exists(first)) {
            File.WriteAllText(Path.Combine(fixture, "first.pid"), System.Diagnostics.Process.GetCurrentProcess().Id.ToString());
            File.Copy(args[image + 1], first);
            if (!File.Exists(Path.Combine(fixture, "direct"))) {
                var deadline = DateTime.UtcNow.AddSeconds(40);
                while (!File.Exists(Path.Combine(fixture, "release")) && DateTime.UtcNow < deadline) {
                    Thread.Sleep(20);
                }
                Console.Error.WriteLine("Intentional fixture failure");
                return 1;
            }
        }
        File.Copy(args[image + 1], Path.Combine(fixture, "retry.png"));
        File.Copy(Path.Combine(fixture, "recognition.json"), args[output + 1], true);
        return 0;
    }
}
""".strip(), encoding='ascii')
    executable = fixture / 'codex.exe'
    compiler = Path(os.environ['SystemRoot']) / 'Microsoft.NET' / 'Framework' / 'v4.0.30319' / 'csc.exe'
    subprocess.run([str(compiler), '/nologo', f'/out:{executable}', str(source)], check=True,
                   capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    return executable


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

    async def preview_image(self, click, original, name=None, expect_failure=True):
        before = await self.get_docs()
        await click()
        await self.paint()
        await self.until(f"""(() => {{
          const image = document.querySelector({json.dumps(IMAGE)});
          return image?.checkVisibility() && image.complete && image.naturalWidth === {original.width};
        }})()""")
        if name:
            await self.until(f"document.querySelector('.intake').innerText.includes({json.dumps(name)})")
        src = await self.js(f'document.querySelector({json.dumps(IMAGE)}).currentSrc')
        preview = pymupdf.Pixmap(base64.b64decode(src.split(',', 1)[1]))
        assert preview.samples == original.samples, 'The intake changed the original screenshot pixels.'
        assert not await self.js(WORKSPACE)
        if expect_failure:
            await self.until("document.querySelector('.image-intake-error[role=alert]')?.textContent.includes('本地 Codex 调用失败')", seconds=45)
            drafts = await self.get_docs()
            assert len(drafts) == len(before) + 1, drafts

    async def close_editor(self):
        await self.js("document.querySelector('button[aria-label=返回]').click()")
        await self.until("!document.querySelector('.editor-mount') || document.querySelector('.editor-mount').style.display === 'none'")
        await self.paint()


def sequence():
    return ctypes.windll.user32.GetClipboardSequenceNumber()


def wait_for_process_exit(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER: the process has already exited
            return
        raise ctypes.WinError(error)
    try:
        assert kernel.WaitForSingleObject(handle, 6000) == 0, f'OCR subprocess {pid} continued after cancellation'
    finally:
        kernel.CloseHandle(handle)


def make_fixture(destination):
    source = ROOT / 'desktop' / 'sample' / '力与运动.pdf'
    with pymupdf.open(source) as pdf:
        page = pdf[0]
        clip = pymupdf.Rect(24, 25, page.rect.width - 24, 270)
        screenshot = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=clip, alpha=False)
        screenshot.save(destination)
    return screenshot


def check_original_pdf(document, screenshot):
    assert document['inputKind'] == 'image' and document['pdfPageCount'] == 1, document
    with pymupdf.open(document['pdfPath']) as pdf:
        images = pdf[0].get_images(full=True)
        assert len(images) == 1, images
        embedded = pymupdf.Pixmap(pdf, images[0][0])
        assert (embedded.width, embedded.height) == (screenshot.width, screenshot.height)
        assert embedded.samples == screenshot.samples, 'The original screenshot pixels changed during import.'


def check_manuscript_pdf(origin, project_id, key, crop):
    url = f'{origin}/api/projects/{quote(project_id)}/manuscript.pdf?key={quote(key)}'
    with urlopen(url, timeout=10) as response, pymupdf.open(stream=response.read(), filetype='pdf') as pdf:
        assert len(pdf) == 1 and '水平恒力' in pdf[0].get_text()
        images = pdf[0].get_images(full=True)
        assert images, 'The revision PDF lost the cropped illustration.'
        for image in images:
            embedded = pymupdf.Pixmap(pdf, image[0])
            if (embedded.width, embedded.height, embedded.samples) == (crop.width, crop.height, crop.samples):
                return
        raise AssertionError('The revision PDF does not contain the original cropped pixels.')


def make_launcher(directory):
    launcher = directory / 'launcher'
    launcher.mkdir()
    (launcher / 'package.json').write_text(json.dumps({'name': 'utopia-image-qa', 'main': 'main.cjs'}), encoding='utf-8')
    (launcher / 'main.cjs').write_text(r"""
const { clipboard, ClipboardItem } = require('electron');
const fs = require('node:fs');
const Module = require('node:module');
const { pathToFileURL } = require('node:url');
const { clipboardMode, clipboardLog, screenshotFile, productionMain, rendererSnapshot } = process.env;
Object.defineProperty(clipboard, 'read', { value: async () => {
  const mode = fs.readFileSync(clipboardMode, 'utf8');
  fs.appendFileSync(clipboardLog, mode + '\n');
  if (mode === 'image') return [new ClipboardItem({'image/png': new Blob([fs.readFileSync(screenshotFile)], {type:'image/png'})})];
  if (mode === 'text') return [new ClipboardItem({'text/plain': pathToFileURL(screenshotFile).href})];
  if (mode === 'remote-uri') return [new ClipboardItem({'text/uri-list': '# remote source\r\nhttps://example.test/photo.png\r\nfile://example.test/share/photo.png'})];
  if (mode === 'local-uri') return [new ClipboardItem({'text/uri-list': '# copied files\r\nhttps://example.test/photo.png\r\n' + pathToFileURL(screenshotFile).href})];
  return [];
}});
const source = fs.readFileSync(productionMain, 'utf8');
const renderer = "const RENDERER = path.join(__dirname, 'dist');";
if (!source.includes(renderer)) throw Error('Cannot locate the production renderer root');
let mainSource = source.replace(renderer, 'const RENDERER = ' + JSON.stringify(rendererSnapshot) + ';');
if (process.env.probeResources) {
  const resources = /^const RESOURCES = .+?;$/m;
  if (!resources.test(mainSource)) throw Error('Cannot locate desktop runtime resources');
  mainSource = mainSource.replace(resources, () => 'const RESOURCES = ' + JSON.stringify(process.env.probeResources) + ';');
}
const entry = new Module(productionMain, module);
entry.filename = productionMain;
entry._compile(mainSource, productionMain);
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
    await page.preview_image(lambda: page.click(PASTE), screenshot)
    print('PASS explicit paste reads Electron ClipboardItem and previews the original pixels', flush=True)
    await page.route('stats')
    await page.route('entry')
    assert not await page.js(f'Boolean(document.querySelector({json.dumps(IMAGE)}))')

    async def shortcut():
        await page.js('document.activeElement.blur()')
        assert await page.guarded_ctrl_v('body') == {'trusted': True, 'prevented': True}

    await page.preview_image(shortcut, screenshot)
    image_modes = negative_modes + ['image', 'image']
    assert log_file.read_text(encoding='utf-8').splitlines() == image_modes
    print('PASS trusted Ctrl+V takes the clipboard IPC path exactly once', flush=True)

    mode_file.write_text('local-uri', encoding='utf-8')
    await page.preview_image(lambda: page.click(PASTE), screenshot, screenshot_file.name)
    image_modes.append('local-uri')
    assert log_file.read_text(encoding='utf-8').splitlines() == image_modes
    print('PASS Explorer-style file URI preserves the original file name with Chinese characters and a space', flush=True)
    return image_modes


async def run(args, root):
    screenshot_file = root / '截图 输入.png'
    screenshot = make_fixture(screenshot_file)
    profile, bank = root / 'profile', root / 'bank'
    (bank / 'chapters').mkdir(parents=True)
    identity = sha256(str(bank.resolve()).lower().encode('utf-8')).hexdigest()[:24]
    (profile / 'workspaces' / identity / 'workspace').mkdir(parents=True)
    codex = make_codex_fixture(root)
    first_image = codex.with_name('first.png')
    retry_image = codex.with_name('retry.png')
    release = codex.with_name('release')
    preview_only = codex.with_name('preview-only')
    preview_only.touch()
    if args.direct:
        codex.with_name('direct').touch()
    if args.model:
        codex.with_name('expected-model').write_text(EXPLICIT_MODEL, encoding='ascii')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, UTOPIA_DESKTOP_PROFILE=str(profile), UTOPIA_CODEX_BIN=str(codex))
    if args.cancel:
        env['UTOPIA_AGY_BIN'] = str(codex)
    env.pop('UTOPIA_DESKTOP_VITE_URL', None)
    executable = args.exe.resolve()
    command = [str(executable)]
    if not args.packaged:
        # A checkout's Electron files can be labeled Low Integrity on Windows.
        electron = root / 'electron'
        renderer = root / 'renderer'
        shutil.copytree(executable.parent, electron, copy_function=shutil.copyfile)
        # Keep lazy chunks paired with their entry point while other builds run.
        shutil.copytree(ROOT / 'desktop' / 'dist', renderer)
        command = [str(electron / executable.name), str(make_launcher(root))]
        env.update(clipboardMode=str(root / 'mode.txt'), clipboardLog=str(root / 'clipboard.log'),
                   screenshotFile=str(screenshot_file), productionMain=str(ROOT / 'desktop' / 'main.cjs'),
                   rendererSnapshot=str(renderer))
        if args.resources:
            env['probeResources'] = str(args.resources.resolve(strict=True))
    command.extend([*RENDER_FLAGS, f'--remote-debugging-port={port}', '--library', str(bank)])
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
        async with websockets.connect(url, max_size=12_000_000, ping_interval=None) as ws:
            page = IntakePage(ws)
            await page.command('Page.bringToFront')
            await page.command('Emulation.setFocusEmulationEnabled', {'enabled': True})
            await page.command('Emulation.setDeviceMetricsOverride', {'width': 1400, 'height': 920, 'deviceScaleFactor': 1, 'mobile': False})
            await page.until("Boolean(document.querySelector('button[data-page=entry]') && window.utopia)")
            park_test_window(process.pid)
            await page.js("window.utopia.call('ui.save', {provider:'cursor'})")
            await page.route('settings')
            await page.until("Boolean(document.querySelector('.settings-agent-list input'))", seconds=45)
            assert await page.js("""(() => {
              const settings = document.querySelector('.page-content');
              return !settings.querySelector('#settings-provider-api,[aria-label="自定义 API"]') &&
                !settings.textContent.includes('打开 API 设置');
            })()"""), 'Settings still displays the custom API entry.'
            print('PASS settings shows local Agent connections only', flush=True)
            await page.route('entry')
            await page.until("document.querySelector('h1')?.innerText.includes('图片识别')")
            await page.paint()
            intake = await page.js("document.querySelector('.intake').innerText")
            assert all(label in intake for label in ('Ctrl+V', PASTE, '选择图片', '自动')), intake
            if args.cancel:
                picker = 'button[aria-label="选择图片识别模型"]'
                await page.js(f'document.querySelector({json.dumps(picker)}).click()')
                await page.until("""Boolean(document.querySelector(
                    '.model-picker-popover [role=group] button[title="gemini-pro-orchestrator"]'
                ))""")
                await page.click(ORCHESTRATOR)
                choice = await page.js("JSON.parse(localStorage.getItem('utopia-image-intake-model'))")
                assert choice['agent'] == 'agy' and choice['model'] == 'gemini-pro-orchestrator', choice
                await page.js(f'document.querySelector({json.dumps(picker)}).click()')
                await page.click('自动选择模型')
                print('PASS the image model picker offers Antigravity Orchestrator and stores its exact model ID', flush=True)
            if args.model:
                await page.js("document.querySelector('button[aria-label=选择图片识别模型]').click()")
                await page.until("Boolean(document.querySelector('.model-picker-popover [role=group][aria-label=Codex]'))")
                await page.click('输入模型 ID…')
                await page.js("document.querySelector('input[placeholder=\"输入模型 ID\"]').focus()")
                await page.command('Input.insertText', {'text': EXPLICIT_MODEL})
                await page.click('使用模型')
                await page.until(f"document.querySelector('button[aria-label=选择图片识别模型]')?.textContent.includes({json.dumps(EXPLICIT_MODEL)})")
                print('PASS choosing an exact OCR model from the image intake UI', flush=True)
            clean_screen = await page.command('Page.captureScreenshot', {'format': 'png'}) if args.screenshot else None
            baseline = sequence()
            if args.packaged:
                print('Packaged smoke uses renderer paste and upload with a private clipboard fixture.', flush=True)
            else:
                image_modes = await check_native_clipboard(page, root, screenshot_file, screenshot)

            async def png_event(name):
                fixture = base64.b64encode(screenshot_file.read_bytes()).decode()
                allowed = await page.js(f"""(() => {{
                  const pixels = Uint8Array.from(atob({json.dumps(fixture)}), ch => ch.charCodeAt(0));
                  const data = new DataTransfer();
                  data.items.add(new File([pixels], {json.dumps(name)}, {{type:'image/png'}}));
                  return document.body.dispatchEvent(new ClipboardEvent('paste', {{clipboardData:data, bubbles:true, cancelable:true}}));
                }})()""")
                assert not allowed, 'The image paste event was not handled.'

            pasted_name = '剪贴板事件测试.png'
            await page.preview_image(lambda: png_event(pasted_name), screenshot, pasted_name)
            print('PASS renderer paste event previews the original PNG and starts recognition', flush=True)

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

            await page.preview_image(upload, screenshot, screenshot_file.name)
            if not args.packaged:
                assert (root / 'clipboard.log').read_text(encoding='utf-8').splitlines() == image_modes
            previous_draft_count = len(await page.get_docs())
            preview_only.unlink()
            print('PASS choosing a file starts recognition without a second submission', flush=True)

            recognition_name = '识别编译截图.png'
            started = perf_counter()
            await page.preview_image(lambda: png_event(recognition_name), screenshot, recognition_name, expect_failure=False)
            intake_error = f'document.querySelector({json.dumps(INTAKE_ERROR)})'
            if args.cancel:
                await page.until(f"""[...document.querySelectorAll('.intake button')].some(
                    button => button.checkVisibility() && !button.disabled && button.textContent.trim() === {json.dumps(STOP)}
                )""", seconds=2)
                feedback = perf_counter() - started
                assert feedback < 2, f'Image intake took {feedback:.2f}s to show the stop control'
                print(f'PASS the image and stop control appeared in {feedback:.2f}s', flush=True)
            async with asyncio.timeout(30):
                while not first_image.exists():
                    await asyncio.sleep(.05)
            if not args.direct:
                assert not await page.js(WORKSPACE), 'Workbench opened before image recognition completed.'
                if args.cancel:
                    pid = int(codex.with_name('first.pid').read_text(encoding='ascii'))
                    stop_started = perf_counter()
                    await page.click(STOP)
                    await page.until("document.querySelector('.intake [role=status]')?.textContent.includes('识别已停止')", seconds=2)
                    stopped_at = perf_counter() - stop_started
                    await asyncio.to_thread(wait_for_process_exit, pid)
                    print(f'PASS stop restored intake in {stopped_at:.2f}s and terminated the active OCR subprocess', flush=True)
                else:
                    release.touch()
                    await page.until(f"{intake_error}?.textContent.includes('本地 Codex 调用失败')")
                assert await page.js(f'document.querySelector({json.dumps(IMAGE)})?.checkVisibility()')
                assert not await page.js(WORKSPACE), 'Incomplete recognition opened the workbench.'
                failed = await page.raw_document()
                assert failed['source'] == '' and len(await page.get_docs()) == previous_draft_count + 1, failed
                started = perf_counter()
                await page.click(RETRY)

            await page.until(f'{WORKSPACE} || Boolean({intake_error})', seconds=150)
            assert await page.js(WORKSPACE), await page.js(f'{intake_error}?.textContent')
            recognized = await page.raw_document()
            assert recognized['title'] == recognition_name
            if not args.direct:
                assert recognized['id'] == failed['id']
            assert OCR_SEED in recognized['source'] and len(await page.get_docs()) == previous_draft_count + 1
            if args.cancel:
                await page.until(f'document.querySelector({json.dumps(SOURCE)})?.checkVisibility()', seconds=8)
                assert OCR_SEED in await page.js(f'document.querySelector({json.dumps(SOURCE)}).value')
                editable_at = perf_counter() - started
                assert editable_at < 8, f'Editable LaTeX took {editable_at:.2f}s with a local OCR fixture'
                print(f'PASS retry reopened editable LaTeX in {editable_at:.2f}s using the deterministic OCR fixture', flush=True)
            await page.until(f"""(async () => {{
                const current = await window.utopia.call('workspace.open', {{id: {json.dumps(recognized['id'])}}});
                return current.previewCurrent || [...document.querySelectorAll('.wb-notice button')]
                  .some(button => button.textContent.trim() === '编译日志');
            }})()""", seconds=60)
            recognized = await page.raw_document()
            if not recognized['previewCurrent']:
                await page.click('编译日志')
                log = await page.js("document.querySelector('.wb-log')?.textContent.slice(-4000)")
                raise AssertionError(f'Image reconstruction did not produce a current PDF: {log}')
            assert Path(recognized['previewPath']).is_file()
            if args.cancel:
                print(f'PASS PDF became current {perf_counter() - started:.2f}s after retry', flush=True)
            check_original_pdf(recognized, screenshot)
            crop = re.search(r'\\includegraphics\[[^\n]*?\]\{([^}]+)\}', recognized['source'])
            assert crop, recognized['source']
            saved_crop = pymupdf.Pixmap(str(Path(recognized['sourcePath']).parent / crop[1]))
            assert abs(saved_crop.width - screenshot.width * 0.5) <= 2, saved_crop.width
            assert abs(saved_crop.height - screenshot.height * 0.48) <= 2, saved_crop.height
            with pymupdf.open(recognized['previewPath']) as compiled:
                assert len(compiled) == 1 and '水平恒力' in compiled[0].get_text(), recognized['previewPath']
            assert pymupdf.Pixmap(str(retry_image)).width > 0
            assert retry_image.read_bytes() == first_image.read_bytes()
            assert (await page.js("window.utopia.call('ui.load')"))['provider'] == 'cursor'
            assert recognized['recognitionAgent'] == 'codex'
            if args.model:
                assert recognized['recognitionModel'] == EXPLICIT_MODEL
                print('PASS the selected model reached the Codex CLI and was saved on the draft', flush=True)
            revision_button = """[...document.querySelectorAll('button')].some(
                button => button.checkVisibility() && !button.disabled && button.textContent.trim() === 'PDF 校订'
            )"""
            await page.until(f"Boolean(document.querySelector('.papilot-workspace')) || {revision_button}", seconds=45)
            if await page.js(revision_button):
                await page.click('PDF 校订')
            await page.until("document.querySelector('.papilot-workspace')?.getAttribute('aria-busy') === 'false'", seconds=135)
            embedded_url = await wait_for(process, lambda: desktop_target(port, embedded=True), seconds=45)
            async with websockets.connect(embedded_url, ping_interval=None) as project_socket:
                project = Page(project_socket)
                await project.command('Page.bringToFront')
                await project.command('Emulation.setFocusEmulationEnabled', {'enabled': True})
                await project.until(f'document.title.includes({json.dumps(recognition_name)})', seconds=150)
                projects = await project.js("fetch('/api/state').then(r => r.json()).then(s => s.projects.map(({id, path}) => ({id, path})))")
                active, = (item for item in projects if item['path'] and Path(item['path']).parent.name == recognized['id'])
                await project.until('Boolean(document.querySelector(".pdfViewer .page canvas"))', seconds=150)
                endpoint = json.dumps(f"/api/projects/{active['id']}/manuscript")
                ready = f'fetch({endpoint}).then(r => r.json())'
                await project.until(f'{ready}.then(s => s.build?.status === "ready")', seconds=90)
                build_key = await project.js(f'{ready}.then(s => s.build.key)')
                await asyncio.to_thread(check_manuscript_pdf, await project.js('location.origin'), active['id'], build_key, saved_crop)
                await point_note(project, active['id'])
            if args.direct:
                print('PASS first paste automatically opens the recognized document', flush=True)
            else:
                outcome = 'cancellation' if args.cancel else 'failure'
                print(f'PASS recognition {outcome} retains the screenshot and retries the same draft', flush=True)
            print('PASS image recognition preserves source crops, compiles LaTeX, and opens the PDF in the embedded revision view', flush=True)
            print('PASS the revision PDF contains the original cropped pixels and saves an inline note', flush=True)

            await page.click('LaTeX 源码')
            await page.until(f'document.querySelector({json.dumps(SOURCE)})?.checkVisibility()')
            assert await page.guarded_ctrl_v(EDITOR_CHAT) == {'trusted': True, 'prevented': False}
            await page.text_paste_event(EDITOR_CHAT, '题目助手普通文字')
            await page.js(f"""(() => {{
              const source = document.querySelector({json.dumps(SOURCE)});
              source.focus();
              source.setSelectionRange(0, 0);
            }})()""")
            edit = '% 截图探针编辑\n'
            await page.command('Input.insertText', {'text': edit})
            draft_id = json.dumps(recognized['id'])
            await page.until(f"(async () => (await window.utopia.call('workspace.open', {{id: {draft_id}}})).source.startsWith({json.dumps(edit)}))()")
            await page.close_editor()
            draft = f"[...document.querySelectorAll('.draft-link')].find(b => b.querySelector('span')?.textContent.trim().endsWith({json.dumps(recognition_name)}))"
            await page.until(f"!!{draft}")
            await page.js(f"{draft}.click()")
            await page.until(WORKSPACE)
            reopened = await page.raw_document()
            assert reopened['id'] == recognized['id'] and reopened['source'].startswith(edit), reopened
            print('PASS editing the recognized LaTeX persists to the same draft and reopens from recent documents', flush=True)

            assert sequence() == baseline, 'System clipboard sequence changed during testing.'
            if clean_screen:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                args.screenshot.write_bytes(base64.b64decode(clean_screen['data']))
            await page.command('Page.close')
    except BaseException:
        print(console.read_text(encoding='utf-8', errors='replace'), file=sys.stderr)
        runtime_log = profile / 'papilot/logs/backend.log'
        if runtime_log.is_file():
            print(runtime_log.read_text(encoding='utf-8', errors='replace')[-4000:], file=sys.stderr)
        raise
    finally:
        release.touch()
        try:
            await asyncio.to_thread(process.wait, 3)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, 6)
            except subprocess.TimeoutExpired:
                await asyncio.to_thread(subprocess.run, ['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=6,
                                        creationflags=subprocess.CREATE_NO_WINDOW)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, required=True, help='Stock Electron executable or built desktop application.')
    parser.add_argument('--resources', type=Path, help='Use staged or installed runtime resources for a stock Electron test.')
    variants = parser.add_mutually_exclusive_group()
    variants.add_argument('--direct', action='store_true', help='Verify a successful first paste without retrying OCR.')
    variants.add_argument('--cancel', action='store_true', help='Stop an active OCR process and retry the same draft.')
    parser.add_argument('--model', action='store_true', help='Verify the chosen model reaches the OCR subprocess.')
    parser.add_argument('--packaged', action='store_true', help='Run an unpacked or installed build directly and exercise renderer paste.')
    parser.add_argument('--screenshot', type=Path, help='Write a screenshot of the clean image intake page after the probe passes.')
    args = parser.parse_args()
    if not args.exe.is_file():
        parser.error(f'Executable not found: {args.exe}')
    with TemporaryDirectory(prefix='utopia-image-qa-', ignore_cleanup_errors=True) as temporary:
        asyncio.run(run(args, Path(temporary)))
