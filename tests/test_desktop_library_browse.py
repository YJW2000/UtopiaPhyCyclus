from services.desktop_library import create_library_handlers
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
