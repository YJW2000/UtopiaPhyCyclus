"""Bulk solution selection, generation and review in the classic question browser."""

from difflib import unified_diff
import hashlib
import os
from pathlib import Path
import sqlite3

import streamlit as st

from services.question_solution_service import fill_missing_solution
from services.question_solution_save import SolutionSaveConflict, solution_source_text


_SELECTED = 'browse_solution_batch_selected'
_RESULTS = 'browse_solution_batch_results'
_REVIEWED = 'browse_solution_batch_reviewed'

_LABELS = {
    'ready': '已生成', 'saved': '已保存', 'skipped': '答案与解析已齐全',
    'failed': '补充失败', 'conflict': '原题已变更',
}


def _toggle_question(widget, path, title):
    selected = st.session_state[_SELECTED]
    if st.session_state[widget]:
        selected[path] = title
    else:
        selected.pop(path, None)


def render_question_choice(path, title, scope):
    path = os.path.normcase(os.path.abspath(path))
    widget = 'browse_solution_choice_' + hashlib.sha256(f'{scope}:{path}'.encode()).hexdigest()[:16]
    st.session_state[widget] = path in st.session_state.setdefault(_SELECTED, {})
    st.checkbox('加入批量补充', key=widget, on_change=_toggle_question, args=(widget, path, title))


def _generate(queued, results, reviewed, project_dir, chapters_dir):
    root = Path(chapters_dir).resolve()
    progress = st.progress(0, text='Agent 正在逐题补充答案与解析…')
    try:
        for index, (path, title) in enumerate(queued, start=1):
            progress.progress((index - 1) / len(queued), text=f'正在补充第 {index} / {len(queued)} 题 · {title}')
            item = {'title': title}
            try:
                original = Path(path).resolve(strict=True)
                if original.suffix.lower() != '.tex' or not original.is_relative_to(root) or not original.is_file():
                    raise ValueError('题目已移动或超出当前题库。')
                base = original.read_bytes()
                source = solution_source_text(base)
                updated = fill_missing_solution(source, project_dir=project_dir, source_dir=original.parent,
                                                fallback_source_dir=root.parent)
                if updated == source:
                    item['status'] = 'skipped'
                else:
                    item.update(status='ready', base=base, updated=updated)
            except (OSError, RuntimeError, ValueError) as exc:
                item.update(status='failed', error=str(exc))
            reviewed.discard(path)
            results[path] = item
    finally:
        progress.empty()


def _save_results(results, reviewed, save, on_saved):
    saved_count = 0
    for path, item in results.items():
        if item['status'] != 'ready' or path not in reviewed:
            continue
        try:
            save(path, item['base'], item['updated'])
        except SolutionSaveConflict as exc:
            reviewed.discard(path)
            item.update(status='conflict', error=str(exc))
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            item['error'] = str(exc)
        else:
            reviewed.discard(path)
            item.pop('error', None)
            item.pop('base')
            item.pop('updated')
            item['status'] = 'saved'
            saved_count += 1
    if saved_count:
        on_saved()
        st.toast(f'已保存 {saved_count} 道题。', icon='✅')


def _toggle_review(widget, path):
    reviewed = st.session_state[_REVIEWED]
    if st.session_state[widget]:
        reviewed.add(path)
    else:
        reviewed.discard(path)


def _render_review(results, reviewed, save, on_saved, render_preview):
    pending = [path for path, item in results.items() if item['status'] == 'ready']
    with st.expander(f'批量补充结果 · {len(pending)} 道待保存', expanded=bool(pending)):
        summary = ' · '.join(f'{label} {count} 道' for status, label in _LABELS.items()
                             if (count := sum(item['status'] == status for item in results.values())))
        st.caption(summary)
        paths = [*pending, *(path for path in results if path not in pending)]

        def label(path):
            item = results[path]
            status = ('已核对' if path in reviewed else '待核对') if item['status'] == 'ready' else _LABELS[item['status']]
            return f'{item["title"]} · {status}'

        path = st.selectbox('核对题目', paths, key='browse_solution_result_path', format_func=label)
        item = results[path]
        if item.get('error'):
            st.warning(item['error'])
        if item['status'] in ('ready', 'conflict'):
            view = st.radio('核对视图', ('排版预览', 'LaTeX 改动'), horizontal=True, key='browse_solution_review_view')
            if view == '排版预览':
                try:
                    render_preview(item['updated'], source_dir=os.path.dirname(path))
                except (OSError, RuntimeError, ValueError) as exc:
                    st.warning(f'排版预览失败，可切换到 LaTeX 改动查看。{exc}')
            else:
                before = solution_source_text(item['base'])
                diff = unified_diff(before.splitlines(), item['updated'].splitlines(), lineterm='', n=2)
                st.code('\n'.join(diff), language='diff')
        if item['status'] == 'ready':
            check = 'browse_solution_reviewed_' + hashlib.sha256(path.encode()).hexdigest()[:16]
            st.session_state[check] = path in reviewed
            c_review, c_discard = st.columns([2, 1])
            c_review.checkbox('这道题已核对', key=check, on_change=_toggle_review, args=(check, path))
            if c_discard.button('放弃本题结果', key='browse_solution_discard'):
                reviewed.discard(path)
                del results[path]
                st.rerun()
        checked = sum(path in reviewed for path in pending)
        save_label = f'保存已核对的 {checked} 道题' if checked else '保存已核对题目'
        if st.button(save_label, key='browse_solution_save', type='primary', disabled=not checked):
            _save_results(results, reviewed, save, on_saved)
            st.rerun()


def render_batch_solution_panel(project_dir, chapters_dir, *, save, on_saved, render_preview):
    """Expose multi-select only on the global browser and retain drafts across pages."""
    enabled = st.toggle('批量补充答案与解析', key='browse_solution_batch_enabled')
    if not enabled:
        return False
    selected = st.session_state.setdefault(_SELECTED, {})
    results = st.session_state.setdefault(_RESULTS, {})
    reviewed = st.session_state.setdefault(_REVIEWED, set())
    c_summary, c_generate, c_clear = st.columns([2, 1.6, 1])
    c_summary.caption(f'已选 {len(selected)} 道题，可跨页勾选。')
    queued = [(path, title) for path, title in selected.items() if results.get(path, {}).get('status') != 'ready']
    if c_generate.button('生成补充结果', key='browse_solution_generate', disabled=not queued):
        _generate(queued, results, reviewed, project_dir, chapters_dir)
    c_clear.button('清空勾选', key='browse_solution_clear', disabled=not selected, on_click=selected.clear)
    if results:
        _render_review(results, reviewed, save, on_saved, render_preview)
    return True
