import base64
from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pymupdf
import pytest
from PIL import Image

from services import desktop_library, desktop_revision, desktop_workspace, pdf_import_service
from services.local_process import run
from utils.image_assets import graphics_references


def wait_task(get, queued):
    for _ in range(240):
        task = get({'id': queued['taskId']})
        if task['status'] not in ('queued', 'running'):
            return task
        time.sleep(0.05)
    pytest.fail(f'task timed out: {queued}')


@pytest.fixture
def workspace(tmp_path):
    library = tmp_path / 'bank'
    (library / 'chapters').mkdir(parents=True)
    workbench = desktop_workspace.DesktopWorkspace(library, tmp_path / 'state')
    yield workbench
    workbench.jobs.close()


def test_draft_persists_multi_selection_proposal_apply_and_undo(workspace, monkeypatch):
    def compiled(source, *args, **kwargs):
        return {'ok': True, 'pdf': b'%PDF-1.4', 'log': 'mock compile'}

    def agent(prompt, schema, images):
        assert '第一处 2' in prompt and '第二处 3' in prompt
        return {'reply': '已更正数值。', 'edits': [
            {'selection': 1, 'before': '2', 'after': '25'},
            {'selection': 2, 'before': '3', 'after': '30'},
        ]}

    monkeypatch.setattr(desktop_workspace, 'compile_tex_preview', compiled)
    monkeypatch.setattr(desktop_revision, 'run_local_codex_json', agent)
    original = '🚀第一处 2；第二处 3。'
    document = workspace.create({'title': '选区', 'source': original})
    ident = document['id']
    left = {'id': 'left', 'start': 2, 'end': 7, 'label': '第一处'}
    right = {'id': 'right', 'start': 8, 'end': 13, 'label': '第二处'}
    saved = workspace.save({'id': ident, 'selections': [left, right], 'activeSelectionId': 'right'})
    assert saved['activeSelectionId'] == 'right'
    job = workspace.chat({'id': ident, 'prompt': '更正两个数值', 'scope': 'selection', 'selections': [left, right]})
    task = wait_task(workspace.jobs.get, job)
    assert task['status'] == 'succeeded', task.get('error')
    proposal = task['result']['proposals'][0]
    assert proposal['baseSource'] == original
    assert proposal['source'] == '🚀第一处 25；第二处 30。'
    assert len(proposal['changes']) == 2
    assert Path(proposal['beforePath']).exists() and Path(proposal['afterPath']).exists()

    another = desktop_workspace.DesktopWorkspace(workspace.library, workspace.state)
    try:
        assert another.resume({})['messages'][-1]['proposalId'] == proposal['id']
        changed = another.apply({'id': ident, 'proposalId': proposal['id']})
        assert changed['previewCurrent'] and changed['canUndo']
        assert changed['selections'][1]['end'] == right['end'] + 2
        assert Path(changed['sourcePath']).read_bytes().decode() == proposal['source']
        restored = another.undo({'id': ident})
        assert restored['source'] == original
        assert restored['selections'] == [left, right]
    finally:
        another.jobs.close()


def test_open_original_reconstruct_and_keep_real_crop(workspace, tmp_path, monkeypatch):
    original = tmp_path / '原卷.pdf'
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40, 70), 'Front cover')
        page = pdf.new_page()
        page.insert_text((40, 70), 'Question 7')
        page.draw_rect(pymupdf.Rect(100, 100, 160, 160), color=(0, 0, 0), fill=(0.8, 0.5, 0.3))
        pdf.save(original)

    def agent(prompt, schema, images):
        assert 'Question 7' in prompt and len(images) == 1
        return dict(status='ok', fragments=[dict(question_id='q1', number='7', kind='stem', latex='题图 [[FIG:f1]]')],
                    figures=[dict(id='f1', rect=[0.16, 0.115, 0.275, 0.20])])

    monkeypatch.setattr(pdf_import_service, 'run_local_codex_json', agent)
    created = workspace.create({'path': str(original)})
    assert created['pdfPageCount'] == 2
    assert 'inputKind' not in created
    assert workspace.create({'path': str(original)})['id'] == created['id']
    original.unlink()
    queued = workspace.reconstruct({'id': created['id'], 'pages': [2]})
    task = wait_task(workspace.jobs.get, queued)
    assert task['status'] == 'succeeded', task.get('error')
    imported = task['result']
    assert imported['originalPage'] == 2
    ref = graphics_references(imported['source'])[0]['path']
    crop = Path(imported['sourcePath']).parent / ref
    assert crop.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')


def test_image_draft_persists_and_recognizes_editable_source(workspace, monkeypatch):
    calls = []

    def agent(prompt, schema, images):
        calls.append(images)
        assert len(images) == 1 and images[0].startswith(b'\x89PNG\r\n\x1a\n')
        return dict(status='ok', fragments=[dict(question_id='q1', number='1', kind='stem', latex='图片题 [[FIG:f1]]')],
                    figures=[dict(id='f1', rect=[0.25, 0.25, 0.75, 0.75])])

    monkeypatch.setattr(pdf_import_service, 'run_local_codex_json', agent)
    buffer = BytesIO()
    Image.new('RGB', (320, 240), 'white').save(buffer, format='PNG')
    handlers = desktop_library.create_library_handlers(workspace.library, workspace.state)
    image = handlers['library.imageInput']({'data': base64.b64encode(buffer.getvalue()).decode(),
                                            'type': 'png', 'name': '截图.png'})
    created = workspace.create({'pdfPath': image['path'], 'inputKind': 'image', 'title': '截图'})
    ident = created['id']
    directory = Path(created['sourcePath']).parent
    metadata = directory / 'document.json'
    summary = directory / 'summary.json'
    assert json.loads(metadata.read_text(encoding='utf-8'))['inputKind'] == 'image'
    assert created['source'] == '' and calls == []

    # Drafts created by the old image entry only stored the upload path.
    for path in (metadata, summary):
        old = json.loads(path.read_text(encoding='utf-8'))
        old.pop('inputKind')
        path.write_text(json.dumps(old), encoding='utf-8')
    Path(image['path']).unlink()
    assert workspace.resume({})['inputKind'] == 'image'
    assert workspace.list({})[0]['inputKind'] == 'image'

    queued = workspace.reconstruct({'id': ident, 'firstPage': 1, 'lastPage': 1})
    task = wait_task(workspace.jobs.get, queued)
    assert task['status'] == 'succeeded', task.get('error')
    recognized = task['result']
    assert recognized['inputKind'] == 'image' and len(calls) == 1
    ref = graphics_references(recognized['source'])[0]['path']
    with Image.open(directory / ref) as crop:
        assert crop.size == (160, 120)
    edited = workspace.save({'id': ident, 'source': recognized['source'].replace('图片题', '已修改')})
    assert '已修改' in edited['source']


def test_pdf_regions_reject_edits_outside_visible_text(workspace, monkeypatch):
    demo = workspace.state / 'demo'
    demo.mkdir()
    tex = demo / 'bank.tex'
    tex.write_text('Alpha 2 and keep 5\nBeta 3.', encoding='utf-8')
    preview = demo / 'visible.pdf'
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 70), 'Alpha 2 and keep 5')
        page.insert_text((40, 110), 'Beta 3.')
        regions = []
        for text in ('Alpha 2', 'Beta 3.'):
            hit = page.search_for(text)[0]
            hit.x0 -= 1
            hit.y0 -= 1
            hit.y1 += 1
            regions.append({'page': 1, 'rect': [hit.x0 / page.rect.width, hit.y0 / page.rect.height,
                                                 hit.x1 / page.rect.width, hit.y1 / page.rect.height]})
        pdf.save(preview)
    document = workspace.create({'path': str(tex), 'previewPath': str(preview)})

    def agent(prompt, schema, images):
        assert 'Alpha 2' in prompt and 'Beta 3.' in prompt and len(images) == 2
        return {'reply': '已修改。', 'edits': [{'selection': 1, 'before': '5', 'after': '6'}]}

    monkeypatch.setattr(desktop_revision, 'run_local_codex_json', agent)
    pending = workspace.chat({'id': document['id'], 'prompt': '改动两个选区', 'scope': 'selection', 'pdfSelections': regions})
    result = wait_task(workspace.jobs.get, pending)
    assert result['status'] == 'failed'
    assert '之外' in result['error']
    assert workspace.open({'id': document['id']})['source'] == document['source']


def test_cancel_running_tool_leaves_draft_source_unchanged(workspace, monkeypatch, tmp_path):
    started = tmp_path / 'child-running'

    def agent(*args):
        script = 'from pathlib import Path; import sys, time; Path(sys.argv[1]).touch(); time.sleep(60)'
        run([sys.executable, '-c', script, str(started)], timeout=70, capture_output=True)
        pytest.fail('cancelled tool should never complete')

    monkeypatch.setattr(desktop_revision, 'run_local_codex_json', agent)
    document = workspace.create({'source': 'Existing source.'})
    queued = workspace.chat({'id': document['id'], 'prompt': '等待模型'})
    for _ in range(60):
        if started.exists():
            break
        time.sleep(0.025)
    assert started.exists()
    before_cancel = time.monotonic()
    assert workspace.jobs.cancel({'id': queued['taskId']})['status'] == 'cancelled'
    workspace.jobs.close()
    assert time.monotonic() - before_cancel < 8
    resumed = workspace.resume({})
    assert resumed['source'] == document['source']
    assert [message['role'] for message in resumed['messages']] == ['user']


def test_rpc_adds_only_to_connected_bank(tmp_path):
    bank = tmp_path / 'bank'
    (bank / 'chapters').mkdir(parents=True)
    state = tmp_path / 'state'
    command = [sys.executable, '-u', str(Path(__file__).resolve().parents[1] / 'services/desktop_rpc.py'),
               '--library', str(bank), '--state-dir', str(state)]
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding='utf-8') as child:
        def call(method, params=None):
            request = {'id': 9, 'method': method, 'params': params or {}}
            child.stdin.write(json.dumps(request, ensure_ascii=False) + '\n')
            child.stdin.flush()
            reply = json.loads(child.stdout.readline())
            assert reply.get('id') == 9 and 'error' not in reply, reply
            return reply['result']

        try:
            assert Path(call('system.ping')['library']) == bank
            assert call('library.summary')['total'] == 0
            header = r'\begin{problem}{2024}{M}{原卷}{3}{磁场运动}'
            source = header + '\n' + r'一物体满足 $F=ma$，求加速度。\end{problem}'
            document = call('workspace.create', {'title': '隔离题库样题', 'source': source})
            queued = call('workspace.export', {'id': document['id'], 'chapter': '牛顿运动定律',
                                               'title': '测试卷', 'year': '2026', 'tags': ['桌面测试']})
            result = wait_task(lambda params: call('task.get', params), queued)
            assert result['status'] == 'succeeded', result
            saved = result['result']['files'][0]
            assert saved['status'] == 'success', saved
            path = Path(saved['path'])
            written = path.read_text(encoding='utf-8')
            assert path.is_relative_to(bank / 'chapters') and '桌面测试' in written
            assert r'\begin{problem}{2026}{M}{测试卷}{3}{牛顿运动定律}' in written
            assert call('library.summary')['total'] == 1
            assert call('workspace.resume')['source'] == source
            original = Path(document['sourcePath'])
            assert original.is_relative_to(state) and original.is_file()
        finally:
            child.stdin.close()
            assert child.wait(timeout=10) == 0


@pytest.mark.skipif(shutil.which('xelatex') is None, reason='needs XeLaTeX')
def test_real_preview_and_export_pdf(workspace, tmp_path):
    document = workspace.create({'source': r'\begin{problem}{2026}{M}{编译}{1}{电场}电场强度为 $E=2\,\mathrm{N/C}$。\end{problem}'})
    ident = document['id']
    queued = workspace.compile({'id': ident})
    for _ in range(600):
        task = workspace.jobs.get({'id': queued['taskId']})
        if task['status'] not in ('queued', 'running'):
            break
        time.sleep(0.1)
    assert task['status'] == 'succeeded', (task.get('error'), task.get('log', '')[-2000:])
    assert workspace.open({'id': ident})['previewCurrent']
    destination = tmp_path / '预览.pdf'
    transfer = wait_task(workspace.jobs.get, workspace.export_pdf({'id': ident, 'path': str(destination)}))
    assert transfer['status'] == 'succeeded'
    with pymupdf.open(destination) as pdf:
        assert pdf.page_count == 1
        assert '电场强度' in pdf[0].get_text()
