from concurrent.futures import ThreadPoolExecutor
import os

from services import library_maintenance
from utils import question_index
from utils.index_rebuild import file_timestamp


def write_question(chapters, number, stem, ident='', *, directory='电路/2026'):
    path = chapters / directory / f'2026-M-测试卷-{number}-电路.tex'
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = f'% === Meta Data ===\n% ID: {ident}\n% === End Meta ===\n' if ident else ''
    path.write_text(metadata + '\\begin{problem}\n' + stem + '\n\\end{problem}\n', encoding='utf-8')
    return path


def test_initial_scan_preserves_sources_and_indexes_only_questions(tmp_path, monkeypatch):
    chapters = tmp_path / 'chapters'
    anonymous = write_question(chapters, 1, '电阻和滑动变阻器')
    identified = write_question(chapters, 2, '闭合电路', '1')
    asset = write_question(chapters, 3, '用于排版的示例', directory='电路/2026/相关图')
    contents = chapters / '电路' / 'content_电路.tex'
    contents.write_text('用户自定义章节索引', encoding='utf-8')
    snapshot = {path: path.read_bytes() for path in (anonymous, identified, asset, contents)}
    csv_path = tmp_path / 'utils' / '题库索引表.csv'

    assert library_maintenance.maintain_library(tmp_path) == {
        'integrity': 'ok', 'added': 2, 'updated': 0, 'missing': 0, 'skipped': 0,
    }
    assert question_index.get_row(str(identified.relative_to(chapters)), csv_path=csv_path)['题目ID'] == '1'
    assert question_index.get_row(str(anonymous.relative_to(chapters)), csv_path=csv_path)['题目ID'] == '2'
    assert question_index.fuzzy_search('滑动变阻器', csv_path=csv_path)[0]['row']['题目ID'] == '2'
    token = question_index.cache_token(csv_path)

    def must_not_parse(*args):
        raise AssertionError('unchanged questions should be skipped')

    monkeypatch.setattr(library_maintenance, 'row_from_tex', must_not_parse)
    assert library_maintenance.maintain_library(tmp_path) == {
        'integrity': 'ok', 'added': 0, 'updated': 0, 'missing': 0, 'skipped': 0,
    }
    assert question_index.cache_token(csv_path) == token
    assert not csv_path.exists()
    assert all(path.read_bytes() == original for path, original in snapshot.items())


def test_incremental_changes_leave_existing_ids_and_missing_rows_intact(tmp_path):
    chapters = tmp_path / 'chapters'
    updated = write_question(chapters, 1, '电阻变化', '10')
    missing = write_question(chapters, 2, '闭合开关', '20')
    incomplete = write_question(chapters, 3, '电流方向', '30')
    csv_path = tmp_path / 'utils' / '题库索引表.csv'
    assert library_maintenance.maintain_library(tmp_path)['added'] == 3
    tracked_path = str(updated.relative_to(chapters))
    prior = question_index.get_row(tracked_path, csv_path=csv_path)
    prior['备注'] = '用户留存资料'
    question_index.put_row(prior, csv_path=csv_path)

    original_mtime_ns = updated.stat().st_mtime_ns
    updated = write_question(chapters, 1, '电流变化', '99')
    seconds = original_mtime_ns // 1_000_000_000
    # Change the timestamp within the index's displayed second.
    new_mtime_ns = seconds * 1_000_000_000 + 123_456_700
    if new_mtime_ns == original_mtime_ns:
        new_mtime_ns += 100
    os.utime(updated, ns=(updated.stat().st_atime_ns, new_mtime_ns))
    assert file_timestamp(updated.stat().st_mtime) == prior['最后修改时间']
    missing.unlink()
    incomplete.write_text('\\begin{problem}\n仍在编辑的题目', encoding='utf-8')
    duplicate = write_question(chapters, 4, '重复占用题号', '20')
    new = write_question(chapters, 5, '灯泡功率')
    assert library_maintenance.maintain_library(tmp_path) == {
        'integrity': 'ok', 'added': 1, 'updated': 1, 'missing': 1, 'skipped': 2,
    }
    current = question_index.get_row(tracked_path, csv_path=csv_path)
    assert current['题干'] == '电流变化'
    assert current['题目ID'] == prior['题目ID']
    assert current['初次录入的时间'] == prior['初次录入的时间']
    assert current['备注'] == '用户留存资料'
    assert question_index.get_row(question_id='20', csv_path=csv_path)['题干'] == '闭合开关'
    assert question_index.get_row(question_id='30', csv_path=csv_path)['题干'] == '电流方向'
    assert question_index.get_row(str(duplicate.relative_to(chapters)), csv_path=csv_path) is None
    assert question_index.get_row(str(new.relative_to(chapters)), csv_path=csv_path)['题目ID'] == '31'


def test_two_maintenance_runs_share_a_consistent_index(tmp_path):
    write_question(tmp_path / 'chapters', 1, '电路中的灯泡')
    write_question(tmp_path / 'chapters', 2, '电流表的读数')

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(library_maintenance.maintain_library, (tmp_path, tmp_path)))

    assert sorted(result['added'] for result in results) == [0, 2]
    rows = question_index.read_rows(csv_path=tmp_path / 'utils' / '题库索引表.csv')
    assert [row['题目ID'] for row in rows] == ['1', '2']
