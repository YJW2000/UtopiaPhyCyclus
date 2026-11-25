from codecs import BOM_UTF16_BE, BOM_UTF16_LE
import sqlite3

import pytest

from services import question_solution_save, question_solution_service
from utils import question_index


@pytest.fixture
def bank_question(tmp_path):
    chapters = tmp_path / 'chapters'
    path = chapters / '电场' / '2026' / '2026-M-模拟卷-1-电场.tex'
    path.parent.mkdir(parents=True)
    source = '\r\n'.join((
        '\ufeff% === Begin Label Data ===', '% ID: 47', '% 标签: 电场', '% === End  Label Data ===',
        r'\begin{problem}{2026}{M}{模拟卷}{1}{电场}',
        r'电场强度已知，求正电荷受力。\includegraphics{原图.png}', r'\end{problem}',
        r'\begin{answer}向右\end{answer}', r'\begin{solutions}[【解析】]',
        '% 预留解答', r'\end{solutions}', '',
    ))
    base = source.encode('utf-8')
    path.write_bytes(base)
    relative = path.relative_to(chapters).as_posix()
    index = tmp_path / 'utils' / '题库索引表.csv'
    question_index.ensure_index(index, bootstrap_from_tex=False)
    question_index.put_row({
        '题目ID': '47', '文件名称': path.stem, '相对文件路径': relative, '标签': '电场',
        '初次录入的时间': '2026-01-01 00:00:00', '组卷引用次数': '4', '答案': '向右',
    }, csv_path=index)
    return path, chapters, base, index, relative


def filled_source(base, monkeypatch):
    def agent(stem, existing, missing, source, directories):
        assert existing['answer'] == '向右'
        assert missing == ['solutions']
        return {'solutions': r'由 $F=qE$ 可知，正电荷受力向右。'}

    monkeypatch.setattr(question_solution_service, '_solve', agent)
    return question_solution_service.fill_missing_solution(base.decode('utf-8'))


def test_save_reviewed_supplement_preserves_tex_and_updates_index(bank_question, monkeypatch):
    path, chapters, base, index, relative = bank_question
    updated = filled_source(base, monkeypatch)
    question_solution_save.save_reviewed_solution(path, base, updated, chapters_dir=chapters)

    assert path.read_bytes() == updated.encode('utf-8')
    assert '\ufeff% === Begin Label Data ===\r\n% ID: 47' in updated
    assert '% 预留解答\r\n' in updated
    assert r'\begin{answer}向右\end{answer}' in updated
    assert r'\includegraphics{原图.png}' in updated
    backups = list((chapters.parent / '.backups' / path.parent.relative_to(chapters.parent)).glob('*.tex'))
    assert len(backups) == 1 and backups[0].read_bytes() == base
    row = question_index.get_row(relative, csv_path=index)
    assert row['题目ID'] == '47' and row['初次录入的时间'] == '2026-01-01 00:00:00'
    assert row['组卷引用次数'] == '4' and row['标签'] == '电场'
    assert row['答案'] == '向右' and row['包含解析'] == '是'
    assert row['解析'] == r'由 $F=qE$ 可知，正电荷受力向右。'


def test_changed_original_is_bypassed(bank_question, monkeypatch):
    path, chapters, base, index, relative = bank_question
    updated = filled_source(base, monkeypatch)
    latest = base.replace('正电荷'.encode(), '粒子'.encode())
    path.write_bytes(latest)
    with pytest.raises(question_solution_save.SolutionSaveConflict, match='原题已修改'):
        question_solution_save.save_reviewed_solution(path, base, updated, chapters_dir=chapters)
    assert path.read_bytes() == latest
    assert not question_index.get_row(relative, csv_path=index)['解析']
    assert not (chapters.parent / '.backups').exists()


def test_failed_index_save_rolls_back_and_can_be_retried(bank_question, monkeypatch):
    path, chapters, base, index, relative = bank_question
    updated = filled_source(base, monkeypatch)

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError('index unavailable')

    with monkeypatch.context() as patch:
        patch.setattr(question_index, 'update_solution_fields', unavailable)
        with pytest.raises(sqlite3.OperationalError, match='index unavailable'):
            question_solution_save.save_reviewed_solution(path, base, updated, chapters_dir=chapters)
    assert path.read_bytes() == base
    assert not question_index.get_row(relative, csv_path=index)['解析']
    question_solution_save.save_reviewed_solution(path, base, updated, chapters_dir=chapters)
    assert question_index.get_row(relative, csv_path=index)['解析']


def test_supplement_cannot_rewrite_existing_answer_or_original_metadata(bank_question, monkeypatch):
    path, chapters, base, _index, _relative = bank_question
    updated = filled_source(base, monkeypatch)
    for tampered in (updated.replace('% ID: 47', '% ID: 21'), updated.replace(r'\begin{answer}向右', r'\begin{answer}向左')):
        with pytest.raises(ValueError, match='必须保留原题、标签和已有解答'):
            question_solution_save.save_reviewed_solution(path, base, tampered, chapters_dir=chapters)
    assert path.read_bytes() == base


@pytest.mark.parametrize(('encoding', 'marker'), [('gb18030', b''), ('utf-16-le', BOM_UTF16_LE), ('utf-16-be', BOM_UTF16_BE)])
def test_supplement_preserves_legacy_encoding(bank_question, monkeypatch, encoding, marker):
    path, chapters, base, _index, _relative = bank_question
    source = base.decode('utf-8-sig')
    legacy = marker + source.encode(encoding)
    path.write_bytes(legacy)
    assert question_solution_save.solution_source_text(legacy) == source

    updated = filled_source(base, monkeypatch).removeprefix('\ufeff').replace('受力向右', r'受力为 $F=3\,\mathrm{N}$，向右')
    question_solution_save.save_reviewed_solution(path, legacy, updated, chapters_dir=chapters)
    assert path.read_bytes() == marker + updated.encode(encoding)
    assert question_solution_save.solution_source_text(path.read_bytes()) == updated
