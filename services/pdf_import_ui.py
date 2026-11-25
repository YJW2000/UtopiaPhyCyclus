"""PDF question intake and its session-local, independently editable drafts."""
from datetime import date
import hashlib
from pathlib import Path
import shutil
from uuid import uuid4

import streamlit as st

from services.pdf_edit_ui import has_pending_pdf_edit, pdf_revision, render_pdf_edit
from services.pdf_import_service import import_pdf, inspect_pdf
from services.question_solution_ui import FILL_SOLUTION_LABEL, generate_solution_draft
from services.tex_compile_service import compile_tex_preview
from utils.core_config import PAPER_TYPES, SUBJECTS
from utils.pdf_selection import st_pdf_selection


@st.cache_data(show_spinner=False, max_entries=8)
def _page_count(pdf):
    return inspect_pdf(pdf)


def _revision(draft, project_dir, source):
    return pdf_revision(source, project_dir, draft["defaults"], "xelatex", draft["resources"], "PDF录入")


def _compile(draft, project_dir, source):
    try:
        return compile_tex_preview(source, str(project_dir), draft["defaults"], "xelatex", draft["resources"])
    except (OSError, RuntimeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def _record_compile(draft, project_dir, result):
    draft["result"] = result
    if result.get("ok"):
        draft["preview"] = dict(result, fingerprint=_revision(draft, project_dir, draft["source"]))


def _new_draft(project_dir, name, pdf, first, last, defaults, on_progress):
    uid = uuid4().hex
    resources = Path(project_dir).resolve() / ".runtime" / "pdf-imports" / uid
    resources.mkdir(parents=True)
    try:
        source = import_pdf(pdf, resource_dir=resources, first_page=first, last_page=last,
                            defaults=defaults, on_progress=on_progress, prompt_library=project_dir)
        if not source or not source.strip():
            raise ValueError("所选页未生成题目，请核对原页并重试。")
    except Exception:
        # This import owns the newly created, empty-at-start directory.
        shutil.rmtree(resources, ignore_errors=True)
        raise
    draft = dict(id=uid, name=name, original=pdf, first=first, last=last, defaults=defaults,
                 resources=str(resources), source=source)
    _record_compile(draft, project_dir, _compile(draft, project_dir, source))
    return draft


def _original(pdf, identity, page, key):
    try:
        st_pdf_selection(pdf, key=key, document_id=identity, disabled=True, initial_page=page)
    except (OSError, RuntimeError, ValueError) as exc:
        st.error(f"原 PDF 页面显示失败：{exc}")


def _upload(project_dir, drafts):
    upload = st.file_uploader("上传试题 PDF", type=["pdf"], key="pdf_import_upload")
    if upload is None:
        return
    pdf = upload.getvalue()
    try:
        pages = _page_count(pdf)
    except (OSError, RuntimeError, ValueError) as exc:
        st.error(str(exc))
        return
    identity = hashlib.sha256(pdf).hexdigest()
    prefix = f"pdf_upload_{identity}"
    c1, c2 = st.columns(2)
    first = c1.number_input("起始页", min_value=1, max_value=pages, value=1, key=prefix + "_first")
    last = c2.number_input("结束页", min_value=1, max_value=pages, value=pages, key=prefix + "_last")
    with st.expander("题库信息"):
        a, b = st.columns(2)
        year = a.text_input("年份", str(date.today().year), key=prefix + "_year")
        kind = b.selectbox("试卷类别", [k for k in PAPER_TYPES if k != "WK"], index=1,
                           format_func=lambda k: f"{k} · {PAPER_TYPES[k]}", key=prefix + "_type")
        paper = st.text_input("试卷名称", Path(upload.name).stem, key=prefix + "_paper")
        number = a.number_input("起始题号", min_value=1, value=1, key=prefix + "_number")
        subject = b.selectbox("缺省知识板块", SUBJECTS, index=SUBJECTS.index("未分类"), key=prefix + "_subject")
    if first > last:
        st.warning("起始页应小于或等于结束页。")
    elif not paper.strip() or not year.strip():
        st.warning("请补全年份和试卷名称。")
    defaults = dict(year=year.strip(), paper_type=kind, paper=paper.strip(), number=str(number), subject=subject)
    if st.button("识别所选页并生成草稿", key="pdf_import_run", type="primary",
                 disabled=first > last or not paper.strip() or not year.strip()):
        status = st.empty()
        def progress(page, total):
            status.info(f"正在识别第 {page} 页，本次共 {total} 页。")
        try:
            with st.spinner("本地 Agent 正在识别题目并编译 PDF…"):
                draft = _new_draft(project_dir, upload.name, pdf, first, last, defaults, progress)
            drafts[draft["id"]] = draft
            st.session_state["pdf_import_selected"] = draft["id"]
        except (OSError, RuntimeError, ValueError) as exc:
            st.error(f"PDF 识别失败：{exc}")
        finally:
            status.empty()
    if not drafts or st.checkbox("查看本次上传的原 PDF", key=prefix + "_original"):
        _original(pdf, identity, first, prefix + "_preview")


def _render_draft(draft, project_dir, save_questions):
    key = "pdf_draft_" + draft["id"]
    source_key = key + "_source"
    view = st.radio("草稿视图", ["PDF 框选修改", "LaTeX 源码", "原 PDF 对照"], horizontal=True, key=key + "_view")

    def update_source():
        draft["source"] = st.session_state[source_key]
        draft.pop("save_logs", None)

    source = draft["source"]
    revision = _revision(draft, project_dir, source)
    pending = has_pending_pdf_edit(key + "_edit", revision, draft.get("preview"))
    c_compile, c_fill, c_save = st.columns(3)
    if c_fill.button(FILL_SOLUTION_LABEL, key=key + "_fill_solution", disabled=not source.strip() or pending):
        updated = generate_solution_draft(source, project_dir, draft["resources"])
        if updated is not None:
            source = st.session_state[source_key] = draft["source"] = updated
            revision = _revision(draft, project_dir, source)
            draft.pop("save_logs", None)
            with st.spinner("正在编译已补充的草稿…"):
                _record_compile(draft, project_dir, _compile(draft, project_dir, updated))
            st.toast("已补充到草稿，可核对后入库。", icon="✅")

    if view == "LaTeX 源码":
        st.session_state.setdefault(source_key, draft["source"])
        st.text_area("题目 LaTeX 源码", key=source_key, height=430, on_change=update_source)
    elif view == "原 PDF 对照":
        _original(draft["original"], draft["id"], draft["first"], key + "_original")

    def compile_current():
        # Widget values arrive before callbacks; a click can commit pending text.
        if view == "LaTeX 源码":
            update_source()
        with st.spinner("正在用 XeLaTeX 编译草稿…"):
            result = _compile(draft, project_dir, draft["source"])
            _record_compile(draft, project_dir, result)
            if result.get("ok"):
                st.session_state[key + "_view"] = "PDF 框选修改"

    c_compile.button("编译当前草稿", key=key + "_compile", disabled=not source.strip(), on_click=compile_current)
    result = draft.get("result", {})
    preview = draft.get("preview")
    current = bool(preview and preview["fingerprint"] == revision and result.get("ok"))
    pending = current and has_pending_pdf_edit(key + "_edit", revision, preview)
    if pending:
        st.caption("请先在 PDF 框选修改中采用或放弃待确认的修改，再保存到题库。")
    if c_save.button("保存到题库", key=key + "_save", disabled=not current or pending):
        try:
            draft["save_logs"] = save_questions(source, draft["defaults"], draft["resources"])
        except (ValueError, RuntimeError, OSError) as exc:
            st.error(f"保存失败：{exc}")
    for log in draft.get("save_logs", []):
        show = {"success": st.success, "skip": st.warning, "error": st.error}.get(log["status"], st.info)
        show(f"{log.get('file', '')}　{log.get('message', '')}")

    if not result.get("ok"):
        st.error(result.get("error") or "草稿编译失败。可在 LaTeX 源码中修改后重新编译。")
        if result.get("log"):
            with st.expander("编译日志"):
                st.code(result["log"], language="text")
    elif not current:
        st.warning("草稿已修改，请编译当前草稿以更新 PDF。")

    def accept(candidate, compiled):
        draft["source"] = candidate
        draft.pop("save_logs", None)
        _record_compile(draft, project_dir, compiled)
        st.toast("已采用修改。")
        st.rerun()

    if view == "PDF 框选修改" and preview:
        if not current:
            st.caption("下方为上次成功编译的 PDF。")
        try:
            render_pdf_edit(preview, source=source, revision=revision, key=key + "_edit",
                            compile_candidate=lambda text: _compile(draft, project_dir, text),
                            on_accept=accept, disabled=not current)
        except (OSError, RuntimeError, ValueError) as exc:
            st.error(f"草稿 PDF 显示失败：{exc}")


def render_pdf_import(project_dir, save_questions):
    st.header("PDF 录入")
    drafts = st.session_state.setdefault("pdf_import_drafts", {})
    _upload(project_dir, drafts)
    if drafts:
        def label(uid):
            d = drafts[uid]
            return f"{d['name']} · 第 {d['first']}–{d['last']} 页 · 草稿 {list(drafts).index(uid) + 1}"
        selected = st.selectbox("当前草稿", list(drafts), format_func=label, key="pdf_import_selected")
        _render_draft(drafts[selected], project_dir, save_questions)
