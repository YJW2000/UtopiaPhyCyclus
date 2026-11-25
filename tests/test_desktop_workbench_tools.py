from pathlib import Path
from time import monotonic, sleep

import pytest

from services import desktop_workspace
from services.desktop_model import selected_provider


@pytest.fixture
def workbench(tmp_path):
    workspace = desktop_workspace.DesktopWorkspace(tmp_path / 'bank', tmp_path / 'state')
    yield workspace
    workspace.jobs.close()


def result(workbench, task):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        state = workbench.jobs.get({'id': task['taskId']})
        if state['status'] not in ('queued', 'running'):
            assert state['status'] == 'succeeded', state
            return state['result']
        sleep(0.01)
    pytest.fail('桌面任务没有完成。')


def test_new_chat_task_limits_context_and_keeps_prior_messages(workbench, monkeypatch):
    calls = []

    def agent(_source, _prompt, _scope, _ranges, messages, _images, **_):
        calls.append((selected_provider(), [message['text'] for message in messages]))
        return '题目无需修改', []

    monkeypatch.setattr(desktop_workspace, 'chat_revision', agent)
    doc = workbench.create({'title': '题目', 'source': '电路'})
    params = {'id': doc['id'], 'prompt': '旧任务'}
    previous = result(workbench, workbench.chat(params))
    anchor = previous['messages'][-1]['id']

    for prompt in ('新任务', '继续讨论'):
        current = result(workbench, workbench.chat({**params, 'prompt': prompt, 'historyAfter': anchor, 'agent': 'qoder'}))

    assert calls == [('codex', []), ('qoder', []), ('qoder', ['新任务', '题目无需修改'])]
    assert current['messages'][:2] == previous['messages']


def test_locate_checks_pdf_revision_and_returns_utf16_offsets(workbench, monkeypatch):
    monkeypatch.setattr(desktop_workspace, 'compile_tex_preview', lambda *_a, **_kw: {'ok': True, 'pdf': b'%PDF-1.4', 'log': ''})

    def locate(source, pdf, page, rect, **_):
        assert source == '🚀电路' and pdf == b'%PDF-1.4'
        assert page == 1 and rect == [0.1, 0.2, 0.3, 0.4]
        return {'start': 1, 'end': 3}

    monkeypatch.setattr(desktop_workspace, 'locate_pdf_selection', locate)
    doc = workbench.create({'title': '选区', 'source': '🚀电路'})
    compiled = result(workbench, workbench.compile({'id': doc['id']}))
    params = {'id': doc['id'], 'page': 1, 'rect': [0.1, 0.2, 0.3, 0.4], 'previewPath': compiled['previewPath']}

    assert Path(compiled['previewPath']).is_file()
    assert result(workbench, workbench.locate(params)) == {'start': 2, 'end': 4}
    workbench.save({'id': doc['id'], 'source': '电路已改'})
    with pytest.raises(ValueError, match='预览与源码版本不一致'):
        workbench.locate(params)
