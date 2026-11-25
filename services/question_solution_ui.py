"""Shared Streamlit action for filling missing parts of a solution draft."""

import streamlit as st


FILL_SOLUTION_LABEL = "补充答案与解析"


def generate_solution_draft(source, project_dir, source_dir=None):
    from services.question_solution_service import fill_missing_solution

    try:
        with st.spinner("Agent 正在补充答案与解析…"):
            updated = fill_missing_solution(source, project_dir=project_dir, source_dir=source_dir)
    except (OSError, RuntimeError, ValueError) as exc:
        st.toast(f"补充解答失败：{exc}", icon="❌")
        return None
    if updated == source:
        st.toast("答案与解析已齐全。")
        return None
    return updated
