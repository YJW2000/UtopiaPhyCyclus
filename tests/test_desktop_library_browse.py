import sqlite3

import pytest

from services import desktop_question_delete, operation_log
from services.desktop_library import create_library_handlers
from services.library_maintenance import maintain_library
from utils import question_index
from utils.core_config import SUBJECTS


def test_browse_groups_and_filters_by_primary_module(tmp_path):
    csv = str(tmp_path / 'utils' / '题库索引表.csv')
    subjects = ['运动的描述，电场', '运动的描述,功与能量', '电场', '', '自定义板块']
    question_index.replace_rows([
        {
            '题目ID': str(index),
            '文件名称': f'{index}.tex',
            '相对文件路径': f'{index}.tex',
            '知识板块': subject,
            '年份': '2025' if index == 1 else '2026',
            '题干': '速度变化' if index <= 2 else '其他题干',
        }
        for index, subject in enumerate(subjects, start=1)
    ], csv_path=csv)
    library = create_library_handlers(tmp_path, tmp_path / 'state')
    summary = library['library.summary']({})
    assert [module['name'] for module in summary['modules']] == [*SUBJECTS, '自定义板块']
    assert {module['name']: module['count'] for module in summary['modules'] if module['count']} == {
        '运动的描述': 2, '电场': 1, '未分类': 1, '自定义板块': 1,
    }

    search = library['library.search']
    browse = {'groupModules': True, 'module': '运动的描述', 'query': '速度变化', 'pageSize': 1}
    first = search(browse)
    assert first['total'] == first['pages'] == 2
    assert [(row['id'], row['module']) for row in first['rows']] == [('2', '运动的描述')]
    assert [row['id'] for row in search({**browse, 'page': 2})['rows']] == ['1']
    assert [row['id'] for row in search({**browse, 'year': '2025'})['rows']] == ['1']
    assert [row['id'] for row in search({'groupModules': True, 'module': '未分类'})['rows']] == ['4']
    assert [row['id'] for row in search({'groupModules': True, 'module': '电场'})['rows']] == ['3']

    [precise] = search({'subject': '运动的描述，电场'})['rows']
    assert precise['id'] == '1'
    assert 'module' not in precise
    assert search({'subject': '运动的描述'})['total'] == 0


def test_browse_by_paper_uses_year_type_and_original_question_number(tmp_path):
    csv = str(tmp_path / 'utils' / '题库索引表.csv')
    question_index.replace_rows([
        {'题目ID': str(i), '文件名称': f'{i}.tex', '相对文件路径': f'{i}.tex', '试卷名称': '同名试卷',
         '年份': year, '试卷类型': paper_type, '原卷题号': number, '知识板块': subject}
        for i, (year, paper_type, number, subject) in enumerate([
            ('2026', 'M', '10', '电场'), ('2026', 'M', '2', '光学'), ('2026', 'M', '附加题', '力学试验'),
            ('2026', 'G', '1', '电场'), ('2025', 'M', '1', '电场'),
        ], 1)
    ], csv_path=csv)
    library = create_library_handlers(tmp_path, tmp_path / 'state')
    groups = library['library.summary']({})['paperGroups']
    mock = next(paper for paper in groups if paper['year'] == '2026' and paper['paperType'] == 'M')
    assert len(groups) == 3 and mock['count'] == 3 and mock['paperTypeName'] == '模拟题'
    browse = {'browseMode': 'paper', 'paper': mock['key'], 'pageSize': 2}
    first = library['library.search'](browse)
    assert first['total'] == 3 and first['pages'] == 2
    assert [(row['number'], row['paperKey']) for row in first['rows']] == [('2', mock['key']), ('10', mock['key'])]
    assert [row['number'] for row in library['library.search']({**browse, 'page': 2})['rows']] == ['附加题']


def test_browse_by_entry_date_uses_edit_date_only_as_fallback(tmp_path):
    csv = str(tmp_path / 'utils' / '题库索引表.csv')
    question_index.replace_rows([
        {'题目ID': str(i), '文件名称': f'{i}.tex', '相对文件路径': f'{i}.tex',
         '初次录入的时间': entered, '最后修改时间': modified}
        for i, (entered, modified) in enumerate([
            ('2024-01-01', '2026-07-01'), ('2026-05-01', '2026-05-01'), ('', '2025-04-01'),
        ], 1)
    ], csv_path=csv)
    search = create_library_handlers(tmp_path, tmp_path / 'state')['library.search']
    assert [row['id'] for row in search({})['rows']] == ['1', '2', '3']
    assert [row['id'] for row in search({'browseMode': 'entry'})['rows']] == ['2', '3', '1']
    ascending = search({'browseMode': 'entry', 'entryOrder': 'asc', 'pageSize': 1, 'page': 2})
    assert ascending['total'] == 3
    assert [(row['id'], row['entered']) for row in ascending['rows']] == [('3', '2025-04-01')]


@pytest.fixture
def removable_questions(tmp_path, monkeypatch):
    csv = tmp_path / 'utils' / '题库索引表.csv'
    monkeypatch.setattr(operation_log, 'OPERATION_LOG_PATH', str(csv.with_name('operation_log.sqlite3')))
    chapters = tmp_path / 'chapters'
    chapter = chapters / '电场'
    questions = [chapter / '2026' / f'2026-M-模拟卷-{number}-电场.tex' for number in (1, 2, 3)]
    stems = ['静电屏蔽', '矢量叠加', '平行板电容器']
    questions[0].parent.mkdir(parents=True)
    for number, (question, stem) in enumerate(zip(questions, stems), 1):
        question.write_text(f'\\begin{{problem}}{{2026}}{{M}}{{模拟卷}}{{{number}}}{{电场}}\n{stem}\n\\end{{problem}}\n',
                            encoding='utf-8')
    paths = [question.relative_to(chapters).as_posix() for question in questions]
    figures = questions[0].with_name(questions[0].stem + ' 相关图')
    figures.mkdir()
    (figures / '实验.png').write_bytes(b'figure')
    chapter_index = chapter / 'content_电场.tex'
    chapter_index.write_text('\n'.join(r'\input{chapters/' + path.removesuffix('.tex') + '}' for path in paths),
                             encoding='utf-8')
    question_index.ensure_index(csv, bootstrap_from_tex=False)
    question_index.replace_rows([
        {'题目ID': str(number), '文件名称': question.stem, '相对文件路径': path, '题干': stem, '知识板块': '电场'}
        for number, (question, path, stem) in enumerate(zip(questions, paths, stems), 1)
    ], csv_path=csv)
    handlers = create_library_handlers(tmp_path, tmp_path / 'state')
    return csv, questions, paths, figures, chapter_index, handlers


def test_bulk_delete_syncs_sqlite_search_and_files(removable_questions, tmp_path):
    csv, questions, paths, figures, chapter_index, handlers = removable_questions
    before = question_index.cache_token(csv)
    source = questions[0].read_bytes()
    search = handlers['library.search']
    assert search({'mode': 'fuzzy', 'query': '静电屏蔽'})['total'] == 1
    assert handlers['library.deleteQuestions']({'paths': [*paths[:2], paths[0]]}) == {
        'deletedPaths': paths[:2], 'warnings': [],
    }

    assert [question.exists() for question in questions] == [False, False, True]
    assert not figures.exists()
    backup_dir = tmp_path / '.backups' / questions[0].parent.relative_to(tmp_path)
    [backup] = backup_dir.glob(f'{questions[0].stem}.*.tex')
    assert backup.read_bytes() == source
    [figure_backup] = backup_dir.glob(figures.name + '.*')
    assert (figure_backup / '实验.png').read_bytes() == b'figure'
    assert question_index.cache_token(csv) == (*before[:2], before[2] + 1)
    assert [row['path'] for row in search({})['rows']] == [paths[2]]
    assert search({'mode': 'fuzzy', 'query': '静电屏蔽'})['total'] == 0
    assert handlers['library.summary']({})['total'] == 1
    assert handlers['library.check']({}) == {'integrity': 'ok', 'total': 1, 'missing': []}
    with question_index.connection(csv) as db:
        assert not db.execute('SELECT question_rowid FROM vocabulary WHERE question_rowid IN (1, 2)').fetchall()
        db.execute("INSERT INTO question_fts(question_fts, rank) VALUES('integrity-check', 1)")
    output = chapter_index.read_text(encoding='utf-8')
    assert questions[2].stem in output and questions[0].stem not in output and questions[1].stem not in output
    assert {entry['question_id'] for entry in operation_log.read_recent_operations()} == {'1', '2'}
    assert maintain_library(tmp_path)['added'] == 0
    assert handlers['library.deleteQuestions']({'paths': paths[:2]})['deletedPaths'] == paths[:2]


def test_bulk_delete_rolls_back_files_and_index_after_second_row_fails(removable_questions):
    csv, questions, paths, figures, chapter_index, handlers = removable_questions
    before = question_index.cache_token(csv)
    original = chapter_index.read_bytes()
    with question_index.connection(csv) as db, db:
        db.execute('''CREATE TRIGGER stop_second BEFORE DELETE ON questions WHEN OLD."题目ID" = '2'
                      BEGIN SELECT RAISE(ABORT, 'index unavailable'); END''')
    with pytest.raises(sqlite3.IntegrityError, match='index unavailable'):
        handlers['library.deleteQuestions']({'paths': paths[:2]})
    assert all(question.is_file() for question in questions)
    assert (figures / '实验.png').read_bytes() == b'figure'
    assert question_index.cache_token(csv) == before
    assert len(question_index.read_rows(csv_path=csv)) == 3
    assert chapter_index.read_bytes() == original


def test_bulk_delete_rejects_unindexed_files_and_cleans_stale_rows(removable_questions):
    csv, questions, paths, _, _, handlers = removable_questions
    unindexed = questions[0].with_name('未收录.tex')
    unindexed.write_text('未收录', encoding='utf-8')
    unindexed_path = paths[0].replace(questions[0].name, unindexed.name)
    delete = handlers['library.deleteQuestions']
    with pytest.raises(ValueError, match='已不在索引中'):
        delete({'paths': [paths[0], unindexed_path]})
    with pytest.raises(ValueError, match='当前题库'):
        delete({'paths': [paths[0], '../未收录.tex']})
    assert questions[0].is_file() and unindexed.is_file()
    orphaned_figures = questions[1].with_name(questions[1].stem + ' 相关图')
    orphaned_figures.mkdir()
    questions[1].unlink()
    assert delete({'paths': [paths[1]]})['deletedPaths'] == [paths[1]]
    assert question_index.get_row(paths[1], csv_path=csv) is None
    assert orphaned_figures.is_dir()


def test_chapter_index_access_error_reports_committed_delete(removable_questions, monkeypatch):
    csv, questions, paths, _, _, handlers = removable_questions

    def inaccessible(*args, **kwargs):
        raise PermissionError('simulated chapter index access error')

    monkeypatch.setattr(desktop_question_delete, 'update_chapter_contents', inaccessible)
    result = handlers['library.deleteQuestions']({'paths': [paths[0]]})
    assert result['deletedPaths'] == [paths[0]]
    assert 'simulated chapter index access error' in result['warnings'][0]
    assert not questions[0].exists()
    assert question_index.get_row(paths[0], csv_path=csv) is None
