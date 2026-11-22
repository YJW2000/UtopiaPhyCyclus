"""Streamlit workflow for proposing and reviewing an edit on a typeset question."""
import hashlib
import json
from pathlib import Path
import re

import streamlit as st

from services.file_service import file_change_token
from services.pdf_selection_edit import edit_pdf_selection
from utils.image_assets import graphics_fingerprint
from utils.pdf_selection import st_pdf_selection


def pdf_revision(source, project_dir, defaults, engine, resource_dir, mode=''):
    project = Path(project_dir)
    inputs = []
    for name in re.findall(r'\\(?:input|include)\s*\{([^{}\\]+)\}', source):
        path = Path(name.strip())
        if not path.name:
            continue
        if not path.suffix:
            path = path.with_suffix('.tex')
        for folder in (Path(resource_dir or project), project):
            candidate = folder / path
            if candidate.is_file():
                inputs.append((str(candidate), file_change_token(candidate)))
                break
    state = (source, str(project), defaults, engine, str(resource_dir), mode,
             graphics_fingerprint(source, project_dir, resource_dir), inputs,
             file_change_token(project / 'main.tex'), file_change_token(project / 'UtopiaPhyCyclus_book.cls'))
    return hashlib.sha256(json.dumps(state, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _document_id(preview, revision):
    return revision + ':' + hashlib.sha256(preview['pdf']).hexdigest()


def has_pending_pdf_edit(key, revision, preview):
    proposal = st.session_state.get(key + '_proposal')
    return bool(proposal and preview and proposal['base'] == _document_id(preview, revision))


def render_pdf_edit(preview, *, source, revision, key, compile_candidate, on_accept, disabled=False):
    """Only pass a compiled candidate to the owning draft when the user accepts it."""
    pdf = preview['pdf']
    document_id = _document_id(preview, revision)
    pending_key = key + '_proposal'
    proposal = st.session_state.get(pending_key)
    if proposal and (disabled or proposal['base'] != document_id):
        del st.session_state[pending_key]
        proposal = None

    if proposal:
        st.caption(f"第 {proposal['page']} 页的修改要求 · {proposal['instruction']}")
        view = st.radio('对照 PDF', ['修改后', '修改前'], horizontal=True, key=key + '_compare')
        show_candidate = view == '修改后'
        st_pdf_selection(proposal['compiled']['pdf'] if show_candidate else pdf,
                         key=key + '_review_' + str(show_candidate),
                         document_id=document_id + ':' + proposal['id'] + ':' + str(show_candidate),
                         initial_page=proposal['page'], disabled=True)
        accept, discard = st.columns(2)
        if accept.button('采用修改', type='primary', key=key + '_accept'):
            del st.session_state[pending_key]
            on_accept(proposal['source'], proposal['compiled'])
        elif discard.button('放弃这次修改', key=key + '_discard'):
            del st.session_state[pending_key]
            st.rerun()
        return

    if not disabled:
        st.caption('在 PDF 上拖框选中要修改的文字、公式或插图。')
    selection = st_pdf_selection(pdf, key=key + '_select', document_id=document_id, disabled=disabled)
    if disabled or not selection or selection['document_id'] != document_id:
        return

    with st.form(key + '_request_' + selection['selection_id']):
        instruction = st.text_area(f"第 {selection['page']} 页 · 希望怎样修改？",
                                   placeholder='例如：把加速度改为 2 m/s²，并保留原有单位格式。')
        submit = st.form_submit_button('生成修改预览', type='primary')
    if not submit:
        return
    instruction = instruction.strip()
    if not instruction:
        st.warning('请填写选区的修改要求。')
        return
    try:
        with st.spinner('本地 Agent 正在修改选区…'):
            candidate = edit_pdf_selection(source, pdf, selection['page'], selection['rect'], instruction,
                                           source_map=preview.get('source_map'))
        with st.spinner('正在编译修改后的 PDF…'):
            compiled = compile_candidate(candidate)
        if not compiled.get('ok'):
            st.error('修改稿编译失败，当前草稿已保留。')
            st.caption(compiled.get('error') or '请调整修改要求后重试。')
            return
    except (ValueError, RuntimeError, OSError) as exc:
        st.error(str(exc))
        return
    st.session_state[pending_key] = dict(
        base=document_id, page=selection['page'], instruction=instruction,
        id=hashlib.sha256(candidate.encode()).hexdigest(), source=candidate, compiled=compiled)
    st.rerun()
