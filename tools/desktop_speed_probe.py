"""Measure an isolated desktop preview using the actual renderer and local RPC."""
import argparse
import asyncio
import base64
import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import random
import socket
import statistics
import subprocess
import tempfile
import time
from urllib.request import urlopen

import pymupdf
import websockets

ROOT = Path(__file__).resolve().parents[1]


class Page:
    def __init__(self, ws):
        self.ws, self.serial = ws, 0

    async def command(self, method, params=None):
        self.serial += 1
        await self.ws.send(json.dumps(dict(id=self.serial, method=method, params=params or {})))
        async with asyncio.timeout(40):
            async for raw in self.ws:
                message = json.loads(raw)
                if message.get('id') == self.serial:
                    if 'error' in message:
                        raise RuntimeError(message['error'])
                    return message.get('result', {})

    async def js(self, expression):
        result = await self.command('Runtime.evaluate', dict(expression=expression, returnByValue=True, awaitPromise=True))
        if result.get('exceptionDetails'):
            raise RuntimeError(result['exceptionDetails']['exception']['description'])
        return result['result'].get('value')

    async def until(self, expression, seconds=15):
        try:
            async with asyncio.timeout(seconds):
                while not await self.js(expression):
                    await asyncio.sleep(.016)
        except TimeoutError as error:
            state=await self.js("({view:document.querySelector('.wb-mode-switch [aria-pressed=true]')?.textContent, panel:document.querySelector('.wb-view-switch [aria-pressed=true]')?.textContent, notice:document.querySelector('.wb-notice,.error-strip,.wb-empty')?.textContent, viewport:[...document.querySelectorAll('canvas')].map(c=>[c.width,c.height,c.offsetWidth,c.style.visibility])})")
            raise TimeoutError(f'Preview readiness timed out: {expression}, {state}') from error

    async def click(self, label):
        await self.js(f"""(()=>{{const b=[...document.querySelectorAll('button')].find(b=>b.offsetWidth&&b.textContent.trim()===
        {json.dumps(label)}); if(!b) throw Error('找不到按钮 '+{json.dumps(label)});b.click()}})()""")

    async def paint(self):
        await self.js('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))))')

    async def screen(self, path):
        response = await self.command('Page.captureScreenshot', {'format':'png'})
        path.write_bytes(base64.b64decode(response['data']))


def create_pdf(path):
    rng = random.Random(191)
    with pymupdf.open() as doc:
        for index in range(20):
            page = doc.new_page()
            page.insert_text((38,54), f'Physics file-open measurement / page {index+1}')
            pixels = pymupdf.Pixmap(pymupdf.csGRAY,1024,1024,rng.randbytes(1024*1024),False)
            page.insert_image(pymupdf.Rect(38,90,548,600),pixmap=pixels)
        doc.save(path)


def submit_picker(process_id, path):
    """Select a test file in the native picker owned by this Electron process."""
    api=ctypes.windll.user32
    callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    api.GetDlgItem.restype=wintypes.HWND
    api.SendMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
    deadline=time.perf_counter()+8
    while time.perf_counter()<deadline:
        handles=[]
        def inspect(window,_):
            pid=wintypes.DWORD()
            api.GetWindowThreadProcessId(window,ctypes.byref(pid))
            cls=ctypes.create_unicode_buffer(128)
            api.GetClassNameW(window,cls,128)
            if pid.value==process_id and cls.value=='#32770' and api.IsWindowVisible(window):
                handles.append(window)
            return True
        api.EnumWindows(callback(inspect),0)
        if handles:
            edits=[]
            def edit(child,_):
                cls=ctypes.create_unicode_buffer(128)
                api.GetClassNameW(child,cls,128)
                if cls.value=='Edit' and api.IsWindowVisible(child):
                    edits.append(child)
                return True
            api.EnumChildWindows(handles[0],callback(edit),0)
            if edits:
                value=ctypes.create_unicode_buffer(str(path))
                api.SendMessageW(edits[-1],12,0,ctypes.addressof(value))
                started=time.perf_counter()
                api.PostMessageW(handles[0],0x111,1,api.GetDlgItem(handles[0],1))
                return started
        time.sleep(.02)
    raise RuntimeError('The native file picker did not appear.')


async def operations(page, out, process_id):
    metrics = {}
    started=time.perf_counter()
    await page.js("document.querySelector('[data-page=browse]').click()")
    await page.until("!!document.querySelector('.question-link,.section-head small') && !!document.querySelector('.section-head small')?.textContent.match(/共/)")
    await page.paint()
    metrics['browse_ms']=round(1000*(time.perf_counter()-started))
    await page.js("document.querySelector('[data-page=tex]').click()")
    await page.click('打开演示题 ↗')
    await page.command('Page.bringToFront')
    drawn="!![...document.querySelectorAll('canvas')].find(c=>c.offsetWidth && c.style.visibility==='visible')"
    await page.until(drawn)
    await page.paint()
    await page.screen(out/'分屏工作台.png')
    await page.click('LaTeX')
    await page.until("!!document.querySelector('.wb-source textarea')")
    await page.click('PDF 预览')
    await page.until(drawn)
    starts=[]
    for i in range(30):
        mode=('聊天','审阅','分屏')[i%3]
        tick=time.perf_counter()
        await page.click(mode)
        if mode!='聊天':
            await page.until(drawn)
        await page.paint()
        starts.append(round(1000*(time.perf_counter()-tick)))
    metrics['mode_ms']=starts
    metrics['mode_p95_ms']=sorted(starts)[math.ceil(.95*len(starts))-1]
    await page.click('并排 ⌄')
    await page.click('LaTeX ＋ PDF 预览')
    await page.until(drawn)
    await page.paint()
    await page.screen(out/'源码与排版并排.png')

    # Generate fixtures outside the renderer's preauthorized read locations.
    ping=await page.js("window.utopia.call('system.ping')")
    folder=Path(ping['stateDir'])/'probe-input'
    folder.mkdir(exist_ok=True)
    pdf=folder/'20-page-scan.pdf'
    await asyncio.to_thread(create_pdf,pdf)
    tex=folder/'source.tex'
    tex.write_bytes((ROOT/'desktop/sample/力与运动.tex').read_bytes())
    metrics['pdf_bytes']=pdf.stat().st_size
    for kind,path in [('pdf',pdf),('tex',tex)]:
        for repeat in (False,True):
            await page.js(f"document.querySelector('[data-page={kind}]').click()")
            await page.until("!!document.querySelector('.intake button')?.offsetWidth")
            await page.click('选择 PDF 文件' if kind=='pdf' else '选择 TeX 文件')
            tick=await asyncio.to_thread(submit_picker,process_id,path)
            await page.until(f"!![...document.querySelectorAll('[aria-label=题目标题]')].find(i=>i.offsetWidth && i.value==={json.dumps(path.name)})")
            await page.command('Page.bringToFront')
            if kind=='pdf':
                await page.until(drawn)
            else:
                await page.until("!!document.querySelector('.wb-view-switch')?.offsetWidth")
                await page.click('LaTeX')
                await page.until("!!document.querySelector('.wb-source textarea')?.value")
            await page.paint()
            label=f'{kind}_{"cached" if repeat else "first"}'
            metrics[label+'_picker_to_visible_ms']=round(1000*(time.perf_counter()-tick))
            metrics[label+'_ms']=round(await page.js("performance.now()-performance.getEntriesByName('document-open').at(-1).startTime"))
    for kind,label in [('tex','导出 LaTeX'),('pdf','导出 PDF')]:
        output=folder/('exported.'+kind)
        await page.js("document.querySelector('button[aria-label=更多操作]').click()")
        await page.click(label)
        await asyncio.to_thread(submit_picker,process_id,output)
        async with asyncio.timeout(90):
            while not output.is_file():
                await asyncio.sleep(.1)
        if kind=='pdf':
            with pymupdf.open(output) as exported:
                assert len(exported)>=1 and '小车' in ''.join(p.get_text() for p in exported)
        else:
            assert '小车' in output.read_text(encoding='utf-8')
        metrics[kind+'_export_ok']=True
    return metrics


def target(port):
    try:
        with urlopen(f'http://127.0.0.1:{port}/json/list', timeout=.3) as response:
            return next((r['webSocketDebuggerUrl'] for r in json.load(response) if r.get('type')=='page' and r['url'].startswith('utopia://app')),None)
    except (OSError, ValueError):
        return None


async def main(args):
    out=args.output.resolve()
    out.mkdir(parents=True,exist_ok=True)
    metrics={'startup_ms':[]}
    for index in range(args.runs):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            port=sock.getsockname()[1]
        with tempfile.TemporaryDirectory(prefix='utopia-speed-',ignore_cleanup_errors=True) as profile:
            command=[str(args.exe.resolve()),*([str(ROOT/'desktop')] if args.development else []),
                     f'--remote-debugging-port={port}','--library',str(args.library.resolve())]
            start=time.perf_counter()
            proc=subprocess.Popen(command,env=dict(os.environ,UTOPIA_DESKTOP_PROFILE=profile),cwd=ROOT,creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                async with asyncio.timeout(30):
                    while not (url:=await asyncio.to_thread(target,port)):
                        if proc.poll() is not None:
                            raise RuntimeError(f'Electron exited with {proc.returncode}')
                        await asyncio.sleep(.025)
                async with websockets.connect(url,max_size=8_000_000) as ws:
                    page=Page(ws)
                    await page.command('Page.bringToFront')
                    await page.until("!!document.querySelector('button[data-page=pdf]') && /^\\d/.test(document.querySelector('.metrics strong')?.textContent || '')")
                    await page.paint()
                    metrics['startup_ms'].append(round(1000*(time.perf_counter()-start)))
                    if index==args.runs-1:
                        metrics.update(await operations(page,out,proc.pid))
                    await page.command('Page.close')
            finally:
                try:
                    await asyncio.to_thread(proc.wait,5)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    await asyncio.to_thread(proc.wait)
    metrics['startup_median_ms']=statistics.median(metrics['startup_ms'])
    (out/'performance.json').write_text(json.dumps(metrics,indent=2),encoding='utf-8')
    print(json.dumps(metrics,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe',type=Path,required=True)
    parser.add_argument('--development',action='store_true')
    parser.add_argument('--library',type=Path,default=ROOT)
    parser.add_argument('--runs',type=int,default=5)
    parser.add_argument('--output',type=Path,default=ROOT/'deliverables/desktop-preview')
    asyncio.run(main(parser.parse_args()))
