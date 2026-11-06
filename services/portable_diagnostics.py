"""Executable self-test for the bundled Python, TeX and offline speech runtimes."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import wave


def _sqlite_check(work: Path) -> None:
    """Exercise the frozen SQLite/FTS DLL with disposable, non-question markers."""
    import csv
    from utils import question_index as index
    from utils.index_schema import CSV_HEADERS
    with tempfile.TemporaryDirectory(prefix='sqlite-probe-', dir=work) as folder:
        csv_path = str(Path(folder, 'index.csv'))
        row = dict.fromkeys(CSV_HEADERS, '')
        row.update({'题目ID': '1', '文件名称': 'portable-index-probe',
                    '相对文件路径': 'portable-index-probe.tex', '试卷类型': 'M',
                    '题干': 'portableindexprobebefore'})
        with open(csv_path, 'w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_HEADERS)
            writer.writeheader()
            writer.writerow(row)
        assert index.browse_page(csv_path=csv_path)['total'] == 1
        # Reproduce the early detail=none configuration and verify its repair.
        with index.connection(csv_path) as conn, conn:
            conn.execute('DROP TABLE question_fts')
            conn.execute("CREATE VIRTUAL TABLE question_fts USING fts5(fuzzy_tokens,stem_tokens,content='questions',content_rowid='rowid',detail=none)")
            conn.execute("INSERT INTO question_fts(question_fts) VALUES('rebuild')")
            conn.execute("UPDATE index_meta SET value='1' WHERE key='schema_version'")
        index._ready.clear()
        assert len(index.fuzzy_search('portableindexprobebefore', csv_path=csv_path)) == 1
        row['题干'] = 'portableindexprobeupdated'
        index.put_row(row, csv_path=csv_path)
        assert len(index.exact_rows([('题目内容', row['题干'])], csv_path=csv_path)) == 1
        assert len(index.fuzzy_search(row['题干'], csv_path=csv_path)) == 1
        assert not index.exact_rows([('题目内容', 'portableindexprobebefore')], csv_path=csv_path)
        with index.connection(csv_path) as conn:
            assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            conn.execute("INSERT INTO question_fts(question_fts,rank) VALUES('integrity-check',1)")
        index.delete_row(row['相对文件路径'], csv_path=csv_path)
        assert index.read_rows(csv_path=csv_path) == []


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
        _sqlite_check(work)
        report['checks']['sqlite_index'] = {'ok': True, 'fts5': True, 'legacy_fts_repair': True}
        print('PASS: SQLite pagination, save, search and FTS migration', flush=True)
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
