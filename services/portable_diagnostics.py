"""Executable self-test for the bundled Python, TeX and offline speech runtimes."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import wave


def run(root: Path) -> dict:
    from services.portable_runtime import configure
    tools = configure(root)
    work = root / '.runtime/selftest'
    work.mkdir(parents=True, exist_ok=True)
    report = {'version': (root/'VERSION').read_text().strip(),
              'frozen_python': bool(getattr(sys, 'frozen', False)), 'checks': {}}
    try:
        import fitz
        import streamlit
        from PIL import Image
        report['checks']['python_dependencies'] = {
            'ok': True, 'streamlit': streamlit.__version__, 'pymupdf': fitz.VersionBind}
        assert 'xelatex' in tools and 'node' in tools and 'whisper_model' in tools, 'Bundled runtime missing'
        documents = [('libraries', r'''\documentclass{ctexart}
\usepackage{tikz,pgfplots,circuitikz,siunitx}
\pgfplotsset{compat=1.18}
\begin{document}
环境自检：中文与公式 $F=ma$。
\begin{tikzpicture}\draw (0,0) rectangle (1,1);\end{tikzpicture}
\end{document}
''')]
        for name in ['试卷类模板', '练习类模板', '讲义类模板']:
            template = root / 'Test Paper Group/主题模板' / name / (name + '.tex')
            documents.append((name, template.read_text(encoding='utf-8')))
        for number, (name, tex) in enumerate(documents):
            folder = work / f'tex-{number}'
            folder.mkdir(exist_ok=True)
            (folder / 'probe.tex').write_text(tex, encoding='utf-8')
            completed = subprocess.run([tools['xelatex'], '-interaction=nonstopmode', '-halt-on-error',
                                        '-recorder', 'probe.tex'], cwd=folder,
                                       capture_output=True, timeout=120,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            (folder / 'compiler-output.txt').write_bytes(completed.stdout + completed.stderr)
            assert completed.returncode == 0, f'TeX check failed: {name}; see .runtime/selftest/tex-{number}/compiler-output.txt'
            pdf = fitz.open(folder / 'probe.pdf')
            assert len(pdf) > 0
            if number == 0:
                assert '环境自检' in ''.join(page.get_text() for page in pdf), 'Chinese font/text check failed'
            pdf.close()
            inputs = (folder / 'probe.fls').read_text(encoding='utf-8', errors='replace')
            assert 'd:/texlive' not in inputs.lower().replace('\\', '/'), 'External TeX installation used'
            report['checks']['tex_' + name] = {'ok': True}
            print('PASS: TeX ' + name, flush=True)
        from services import local_whisper
        available, detail = local_whisper.availability()
        assert available, detail
        with wave.open(str(root / 'diagnostics/jfk.wav'), 'rb') as audio:
            assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (16000, 1, 2)
            pcm = audio.readframes(audio.getnframes())
        try:
            for iteration in range(2):
                started = time.monotonic()
                ok, text = local_whisper.transcribe_pcm16(pcm, language='en', timeout=240)
                assert ok and 'country' in text.lower(), f'Offline speech check failed: {text}'
                report['checks'][f'offline_speech_{iteration+1}'] = {
                    'ok': True, 'seconds': round(time.monotonic()-started, 2),
                    'persistent_server': local_whisper._STATE.get('port') is not None}
                print(f'PASS: offline speech {iteration+1}', flush=True)
        finally:
            local_whisper.shutdown()
        report['passed'] = True
    except Exception as exc:
        report['passed'] = False
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        (work / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return report
