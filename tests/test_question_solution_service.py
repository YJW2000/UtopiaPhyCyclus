import pytest
from PIL import Image

from services import question_solution_service as solver
from services.tex_import_service import parse_tex_questions
from utils.csv_ops import _parse_tex_content


def test_fills_only_blanks_in_multiple_questions(monkeypatch):
    source = r'''% metadata stays
\begin{problem}{2026}{M}{练习}{1}{运动学}
题甲，求加速度。
\end{problem}
\begin{answer}
$2\,\mathrm{m/s^2}$
\end{answer}
\begin{solution}[教师解析][保留标题]
% 原题暂缺解析
\end{solution}

\begin{problem}{2026}{M}{练习}{2}{运动学}
题乙，求位移。
\end{problem}
\begin{solutions}
由 $x=vt$ 得 $x=6\,\mathrm{m}$。
\end{solutions}
'''
    prompts = []

    def agent(prompt, schema, images):
        prompts.append(prompt)
        assert not images
        if '题甲' in prompt:
            assert '仅补充以下内容：解析' in prompt and r'2\\,\\mathrm' in prompt
            assert '题乙' not in prompt
            return {'answer': '此字段会被忽略', 'solutions': '由 $a=v/t$ 得结果。', 'issue': ''}
        assert '题乙' in prompt and '仅补充以下内容：答案' in prompt
        assert '题甲' not in prompt and '由 $x=vt$' in prompt
        return {'answer': r'$6\,\mathrm{m}$', 'solutions': '', 'issue': ''}

    monkeypatch.setattr(solver, 'run_local_codex_json', agent)
    updated = solver.fill_missing_solution(source)
    assert len(prompts) == 2
    assert updated.replace('由 $a=v/t$ 得结果。\n', '').replace(
        '\n\n\\begin{answer}\n$6\\,\\mathrm{m}$\n\\end{answer}\n', '', 1,
    ) == source
    assert solver.fill_missing_solution(updated) == updated
    assert len(prompts) == 2


def test_raw_stem_with_external_figure_and_legacy_tikz(monkeypatch, tmp_path):
    images_dir = tmp_path / '相关图'
    images_dir.mkdir()
    Image.new('RGB', (12, 15), color='white').save(images_dir / '读数.jpg')
    (images_dir / '电路.tex').write_text(r'\begin{tikzpicture}\draw (0,0)--(1,0);\end{tikzpicture}', encoding='utf-8')
    source = '读取电流。\r\n' + r'\input{相关图/电路}\includegraphics{相关图/读数.jpg}'

    def agent(prompt, schema, images):
        assert '读取电流' in prompt and '【附件图 1】' in prompt
        assert r'\\draw (0,0)--(1,0)' in prompt
        assert '仅补充以下内容：答案、解析' in prompt
        assert len(images) == 1 and images[0].startswith(b'\x89PNG')
        return {'answer': '$1$ A', 'solutions': '由表盘读取。', 'issue': ''}

    monkeypatch.setattr(solver, 'run_local_codex_json', agent)
    updated = solver.fill_missing_solution(source, project_dir=tmp_path)
    assert updated.startswith(source + '\r\n\r\n\\begin{answer}\r\n$1$ A')
    assert '\r\n\\begin{solutions}\r\n由表盘读取。' in updated
    [question] = parse_tex_questions(updated, '2026-M-练习-1-电路.tex', {})
    _, _, _, stem, answer, solution, _ = _parse_tex_content(question.content, '练习')
    assert stem.splitlines() == source.splitlines()
    assert answer == '$1$ A' and solution == '由表盘读取。'


@pytest.mark.parametrize(('payload', 'reason'), [
    ({'answer': '$1$', 'solutions': '', 'issue': '缺少长度'}, '缺少长度'),
    ({'answer': '$1$', 'solutions': r'\frac{1}{', 'issue': ''}, '花括号'),
    ({'answer': '$1', 'solutions': '推导', 'issue': ''}, '未闭合的数学公式'),
    ({'answer': r'\input{other}', 'solutions': '推导', 'issue': ''}, '无效 LaTeX'),
])
def test_invalid_or_underdetermined_result_is_rejected(monkeypatch, payload, reason):
    monkeypatch.setattr(solver, 'run_local_codex_json', lambda *args: payload)
    with pytest.raises(ValueError, match=reason):
        solver.fill_missing_solution('长度是多少？')


def test_multiple_problems_report_the_unsolved_question(monkeypatch):
    source = r'\begin{problem}题甲\end{problem}' + '\n' + r'\begin{problem}题乙\end{problem}'
    replies = iter([{'answer': '$1$', 'solutions': '第一题推导。', 'issue': ''},
                    {'answer': '', 'solutions': '', 'issue': '图中刻度不清楚'}])
    monkeypatch.setattr(solver, 'run_local_codex_json', lambda *args: next(replies))
    with pytest.raises(ValueError, match='第 2 道题.*图中刻度不清楚'):
        solver.fill_missing_solution(source)


def test_generated_directions_use_uncircled_symbols(monkeypatch):
    source = r'判断磁场方向。\begin{answer}原有答案 $\odot$\end{answer}'
    monkeypatch.setattr(solver, 'run_local_codex_json', lambda *args: {
        'answer': '', 'solutions': r'$\otimes$ 向里，$\odot$ 向外，可记作 ⊗、⊙。', 'issue': '',
    })
    updated = solver.fill_missing_solution(source)
    assert updated.startswith(source)
    assert r'$\times$ 向里，$\bullet$ 向外，可记作 \ensuremath{\times}、\ensuremath{\bullet}。' in updated


@pytest.mark.parametrize('command', [r'\input{题目相关图/图1}', r'\includegraphics{表盘.png}'])
def test_missing_referenced_figure_does_not_call_agent(monkeypatch, tmp_path, command):
    monkeypatch.setattr(solver, 'run_local_codex_json', lambda *args: pytest.fail('unexpected Agent request'))
    with pytest.raises(ValueError, match='找不到'):
        solver.fill_missing_solution('根据附图读取指定时刻的电流。\n' + command, project_dir=tmp_path)
