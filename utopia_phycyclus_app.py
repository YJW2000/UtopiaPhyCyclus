import time as _import_clock
_mc_import_started = _import_clock.perf_counter()
import streamlit as st
import os
from contextlib import nullcontext
import re
import time
import datetime
import base64
import requests
import hashlib
import html
import json
import subprocess
import shutil
import uuid
import sys
from dotenv import load_dotenv, dotenv_values
try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None

import streamlit.components.v1 as components
import io
from services.ai_service import extract_json_obj_from_text, normalize_chat_completions_url, post_chat_completion, fetch_available_models, test_model_availability
from services.file_service import atomic_write_text, backup_existing_file, file_change_token
from services.operation_log import read_recent_operations, record_operation
from services.question_service import (
    QuestionDuplicateError,
    create_question_file,
    find_duplicate_matches,
    question_fingerprint,
    scan_duplicate_pairs,
)
from services.tex_import_service import decode_tex, parse_tex_questions, detect_tex_questions, repair_problem_headers, join_tex_questions
from services.tex_compile_service import cleanup_preview_cache, compile_tex_preview, render_pdf_page
from services.question_import_ai_service import classify_question, split_question_with_ai
from services.question_type_service import QUESTION_TYPES, infer_question_type, require_question_type
from services.semantic_search_service import (
    SemanticSearchError,
    build_index as build_semantic_index,
    invalidate_path as invalidate_semantic_path,
    index_status as semantic_index_status,
    search as semantic_search,
)
from utils.runtime_files import ensure_log_csv
from utils.sortable_list import st_sortable_list
from utils.local_stats import sync_question_activity

def _compat_container(*, key=None, **kwargs):
    return st.container(**kwargs)


def _question_key(prefix: str, fpath: str) -> str:
    return hashlib.md5(f"{prefix}:{fpath}".encode("utf-8", errors="ignore")).hexdigest()[:12]


APP_ROOT = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(APP_ROOT, ".env"))
ensure_log_csv(APP_ROOT)

from utils.core_config import *
from utils.file_ops import *
from utils.tikz_ops import *
from utils.latex_ops import *
from utils.latex_ops import parse_meta_data as _parse_question_meta, inject_meta_data as _inject_question_meta
from utils.diagram_palette import as_tex_definitions as _diagram_color_definitions
from utils.csv_ops import add_to_csv_index, read_csv_index, update_csv_index_for_edit
from utils.theme_system import (
    normalize_skin_id,
    generate_theme_css,
)

def render_diagram_agent_dialog(**kwargs):
    from services.diagram_agent_ui import render_diagram_agent_dialog as render
    return render(**kwargs)

from services.performance import report as _report_performance
_report_performance('module_import', _mc_import_started)
# ================= 工具函数 =================
def _save_batch_question_if_new(
    file_path,
    content,
    year,
    paper_type,
    paper_name,
    question_number,
    subject,
    duplicate_matches_out=None,
):
    """Save a batch result only when its path and statement are new."""
    if os.path.exists(file_path):
        return False

    try:
        create_question_file(
            file_path,
            content,
            year,
            paper_type,
            paper_name,
            question_number,
            subject,
        )
    except QuestionDuplicateError as exc:
        if duplicate_matches_out is not None:
            duplicate_matches_out.extend(exc.matches)
        return False
    return True


def _editable_paper_type_options(paper_type_scope=None):
    """Keep WK confined to the dedicated cloze-question workspace."""
    if paper_type_scope == "WK":
        return ["WK"]
    return [paper_type for paper_type in PAPER_TYPES if paper_type != "WK"]


def render_question_preview(content: str, show_title: bool = False, prepared_markdown: str = None):
    """Render a question preview with typography isolated from surrounding UI."""
    st.markdown('<span class="mc-question-preview-anchor"></span>', unsafe_allow_html=True)
    preview_markdown = prepared_markdown
    if preview_markdown is None:
        preview_markdown = _cached_latex_to_markdown(content, show_title)
    st.markdown(preview_markdown, unsafe_allow_html=True)


def _cached_latex_to_markdown(content: str, show_title: bool = False):
    return _latex_markdown_cached(content, show_title, _preview_render_token())


def _preview_render_token():
    engine = shutil.which('xelatex') or ''
    # latex_ops.py 参与 Markdown 预览转换，修改它（figure/minipage 剥离等）后
    # 必须让 st.cache_data 失效，否则用户仍会看到旧的残留 TeX 预览。
    return (file_change_token(os.path.join(APP_ROOT, 'utils', 'latex_ops.py')),
            file_change_token(os.path.join(APP_ROOT, 'utils', 'tikz_ops.py')),
            file_change_token(os.path.join(APP_ROOT, 'UtopiaPhyCyclus_book.cls')),
            engine, file_change_token(engine),
            tuple(os.getenv(key, '') for key in ('TEXINPUTS', 'TEXMFHOME', 'TEXMFCNF')))


@st.cache_data(show_spinner=False, max_entries=256)
def _latex_markdown_cached(content: str, show_title: bool, render_token):
    return latex_to_markdown(content, show_title=show_title)


@st.cache_data(show_spinner=False, max_entries=512)
def _read_question_text_cached(fpath: str, change_token):
    with open(fpath, "r", encoding="utf-8") as question_file:
        return question_file.read()


def read_question_text(fpath: str):
    return _read_question_text_cached(fpath, file_change_token(fpath))


@st.cache_data(show_spinner=False, max_entries=512)
def _load_question_editor_assets_cached(fpath: str, change_token, render_token):
    with open(fpath, "r", encoding="utf-8") as question_file:
        content = question_file.read()
    meta, _ = parse_meta_data(content)
    return {
        "content": content,
        "meta": meta,
        "editor_height": get_editor_height(content),
        "preview_markdown": _cached_latex_to_markdown(content, show_title=False),
    }


def load_question_editor_assets(fpath: str):
    return _load_question_editor_assets_cached(fpath, file_change_token(fpath), _preview_render_token())


@st.cache_data(show_spinner=False, max_entries=512)
def _cached_editor_height(content: str):
    return get_editor_height(content)


@st.cache_data(show_spinner=False, max_entries=512)
def _cached_question_meta(content: str):
    meta, _ = parse_meta_data(content)
    return meta


# 注入自定义 CSS
def inject_custom_css():
    st.markdown("""
        <style>
        html {
            scrollbar-gutter: stable;
            background-color: var(--mc-bg) !important;
        }
        body {
            overflow-y: scroll;
            overflow-x: hidden;
            background-color: var(--mc-bg) !important;
            color: var(--mc-text) !important;
        }
        div[data-testid="stAppViewContainer"] {
            overflow-y: scroll;
            scrollbar-gutter: stable;
            background-color: var(--mc-bg) !important;
            color: var(--mc-text) !important;
        }
        header[data-testid="stHeader"] {
            background: transparent !important;
            height: 0 !important;
            overflow: visible !important;
        }
        div[data-testid="stToolbar"],
        div[data-testid="stDecoration"],
        #MainMenu {
            visibility: hidden !important;
            height: 0 !important;
        }
        .stApp {
            overflow-x: hidden;
            background: var(--mc-bg) !important; 
            color: var(--mc-text) !important;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
        }
        
        .block-container,
        div[data-testid="stAppViewBlockContainer"] {
            padding-top: 0 !important;
            padding-bottom: 2rem !important;
            max-width: 95% !important;
        }
        section.main > div.block-container,
        main .block-container,
        div[data-testid="stMainBlockContainer"] {
            padding-top: 0 !important;
        }
        h1, h2, h3, h4, h5, h6 {
            color: var(--mc-text) !important;
            letter-spacing: -0.015em !important;
            font-weight: 650 !important;
        }
        .block-container h1:first-child,
        .block-container h2:first-child,
        .block-container h3:first-child {
            margin-top: 0 !important;
        }
        div[data-testid="stMarkdownContainer"] h1,
        div[data-testid="stMarkdownContainer"] h2 {
            margin-top: 0 !important;
            padding-top: 0 !important;
        }
        p, li, label, span {
            letter-spacing: 0 !important;
            color: var(--mc-text);
        }
        hr {
            border-color: var(--mc-border) !important;
        }
        
        /* 侧边栏适配深浅自适应主题 */
        section[data-testid="stSidebar"] {
            background: var(--mc-sidebar) !important;
            border-right: 1px solid var(--mc-border) !important;
            box-shadow: none !important;
        }
        
        /* 输入框、表单等元素自适应现代卡片 */
        div[data-testid="stExpander"],
        div[data-testid="stForm"],
        div[data-testid="stPopover"] > div {
            border: 1px solid var(--mc-border) !important;
            border-radius: var(--mc-radius) !important;
            background: var(--mc-surface) !important;
            box-shadow: var(--mc-shadow) !important;
        }
        div[data-testid="stTextInput"] input,
        div[data-testid="stNumberInput"] input,
        div[data-testid="stTextArea"] textarea,
        div[data-baseweb="select"] > div {
            border-color: var(--mc-border) !important;
            background: var(--mc-surface) !important;
            color: var(--mc-text) !important;
            border-radius: var(--mc-control-radius) !important;
        }
        div[data-testid="stButton"] > button,
        div[data-testid="stDownloadButton"] > button,
        div[data-testid="stPopover"] > button {
            min-height: 2.32rem !important;
            border-radius: var(--mc-control-radius) !important;
            border: 1px solid var(--mc-border-strong) !important;
            background: var(--mc-surface) !important;
            color: var(--mc-text) !important;
            box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04) !important;
            transition: transform 0.14s ease, border-color 0.14s ease, background 0.14s ease, box-shadow 0.14s ease !important;
        }
        body:has(#mc-entry-page-anchor) div[data-testid="stVerticalBlock"]:has(#entry-save-button-anchor) div[data-testid="stButton"] > button[kind="primary"] {
            background: var(--mc-action-bg) !important;
            border-color: var(--mc-action-border) !important;
            color: var(--mc-action-text) !important;
            box-shadow: var(--mc-action-shadow) !important;
        }
        body:has(#mc-entry-page-anchor) div[data-testid="stVerticalBlock"]:has(#entry-save-button-anchor) div[data-testid="stButton"] > button[kind="primary"]:hover {
            background: var(--mc-action-bg-hover) !important;
            border-color: var(--mc-action-border) !important;
            color: var(--mc-action-text) !important;
        }
        body:has(#mc-entry-page-anchor) div[data-testid="stVerticalBlock"]:has(#entry-save-button-anchor) div[data-testid="stButton"] > button[kind="primary"]:focus-visible {
            outline: 3px solid var(--mc-focus) !important;
            outline-offset: 2px !important;
        }
        div[data-testid="stButton"] > button:hover,
        div[data-testid="stDownloadButton"] > button:hover,
        div[data-testid="stPopover"] > button:hover {
            border-color: var(--mc-accent) !important;
            background: var(--mc-accent-soft) !important;
            color: var(--mc-accent-dark) !important;
            box-shadow: 0 3px 10px rgba(0, 0, 0, 0.07) !important;
            transform: translateY(-1px);
        }
        div[data-testid="stButton"] > button:active,
        div[data-testid="stDownloadButton"] > button:active,
        div[data-testid="stPopover"] > button:active {
            transform: translateY(0);
            box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05) !important;
        }
        button[kind="primary"],
        div[data-testid="stButton"] > button[kind="primary"] {
            border-color: var(--mc-action-border) !important;
            background: var(--mc-action-bg) !important;
            color: var(--mc-action-text) !important;
            box-shadow: var(--mc-action-shadow) !important;
        }
        button[kind="primary"]:hover,
        div[data-testid="stButton"] > button[kind="primary"]:hover {
            background: var(--mc-action-bg-hover) !important;
            border-color: var(--mc-action-border) !important;
            color: var(--mc-action-text) !important;
        }
        div[data-testid="stDataFrame"],
        div[data-testid="stTable"] {
            background: var(--mc-surface) !important;
            color: var(--mc-text) !important;
            border: 1px solid var(--mc-border) !important;
            border-radius: var(--mc-radius) !important;
        }
        ::selection {
            background: rgba(0, 122, 255, 0.3);
        }
        .katex .boxed {
            border: 1px solid #555555 !important;
            border-radius: 4px !important;
            padding: 2px 6px !important;
        }
        div[data-testid="stMarkdownContainer"]:has(> .mc-question-preview-anchor) {
            display: none !important;
        }
        div[data-testid="stVerticalBlockBorderWrapper"]:has(.mc-question-actions-grid-anchor) {
            border: 0 !important;
            background: transparent !important;
            box-shadow: none !important;
            padding: 0 !important;
            margin-top: 0.62rem !important;
        }
        div[data-testid="stMarkdownContainer"]:has(> .mc-question-actions-grid-anchor),
        div[data-testid="stVerticalBlockBorderWrapper"]:has(.mc-question-actions-grid-anchor) div[data-testid="stElementContainer"]:has(.mc-question-actions-grid-anchor) {
            display: none !important;
        }
        div[data-testid="stVerticalBlockBorderWrapper"]:has(.mc-question-actions-grid-anchor) div[data-testid="stButton"] {
            width: 100% !important;
            min-width: 0 !important;
        }
        div[data-testid="stVerticalBlockBorderWrapper"]:has(.mc-question-actions-grid-anchor) div[data-testid="stButton"] > button {
            width: 100% !important;
            min-height: 2.45rem !important;
            justify-content: center !important;
            white-space: normal !important;
        }
        div[data-testid="column"]:has(.mc-question-preview-anchor):not(:has(div[data-testid="column"] .mc-question-preview-anchor)) div[data-testid="stMarkdownContainer"] {
            font-size: 1rem !important;
            line-height: 1.65 !important;
        }
        div[data-testid="column"]:has(.mc-question-preview-anchor):not(:has(div[data-testid="column"] .mc-question-preview-anchor)) div[data-testid="stMarkdownContainer"] p,
        div[data-testid="column"]:has(.mc-question-preview-anchor):not(:has(div[data-testid="column"] .mc-question-preview-anchor)) div[data-testid="stMarkdownContainer"] li {
            line-height: 1.65 !important;
            margin-top: 0 !important;
            margin-bottom: 0.7rem !important;
        }
        div[data-testid="column"]:has(.mc-question-preview-anchor):not(:has(div[data-testid="column"] .mc-question-preview-anchor)) .katex-display {
            margin: 0.8rem 0 !important;
        }
        div[data-testid="column"]:has(.mc-question-preview-anchor):not(:has(div[data-testid="column"] .mc-question-preview-anchor)) .katex {
            line-height: normal !important;
        }
        div[data-testid="stHorizontalBlock"]:has(#left-panel-anchor),
        div[data-testid="stHorizontalBlock"]:has(#paper-left-anchor),
        div[data-testid="stHorizontalBlock"]:has(#time-left-anchor),
        div[data-testid="stHorizontalBlock"]:has(#adv-search-left-anchor) {
            display: flex !important;
            flex-flow: row nowrap !important;
            align-items: flex-start !important;
            width: 100% !important;
            gap: 1rem !important;
            overflow: visible !important;
        }
        div[data-testid="stHorizontalBlock"]:has(#adv-search-left-anchor):has(#adv-search-right-anchor) {
            display: grid !important;
            grid-template-columns: minmax(300px, 20%) minmax(0, 80%) !important;
            align-items: start !important;
        }
        div[data-testid="stHorizontalBlock"]:has(#adv-search-left-anchor):has(#adv-search-right-anchor) > div[data-testid="column"] {
            width: auto !important;
            max-width: none !important;
            min-width: 0 !important;
            flex: initial !important;
        }
        div[data-testid="column"]:has(#left-panel-anchor),
        div[data-testid="column"]:has(#time-left-anchor) {
            flex: 0 0 28% !important;
            width: 28% !important;
            max-width: 28% !important;
        }
        div[data-testid="column"]:has(#paper-left-anchor) {
            flex: 0 0 21% !important;
            width: 21% !important;
            max-width: 21% !important;
        }
        div[data-testid="column"]:has(#adv-search-left-anchor) {
            flex: 0 0 20% !important;
            width: 20% !important;
            max-width: 20% !important;
        }
        div[data-testid="column"]:has(#right-panel-anchor) {
            flex: 1 1 0% !important;
            width: auto !important;
            max-width: none !important;
        }
        div[data-testid="column"]:has(#time-right-anchor) {
            flex: 0 0 70% !important;
            width: 70% !important;
            max-width: 70% !important;
        }
        div[data-testid="column"]:has(#paper-right-anchor) {
            flex: 0 0 77% !important;
            width: 77% !important;
            max-width: 77% !important;
        }
        div[data-testid="column"]:has(#adv-search-right-anchor) {
            flex: 0 0 78% !important;
            width: 78% !important;
            max-width: 78% !important;
        }
        div[data-testid="column"]:has(#left-panel-anchor),
        div[data-testid="column"]:has(#paper-left-anchor),
        div[data-testid="column"]:has(#time-left-anchor),
        div[data-testid="column"]:has(#adv-search-left-anchor),
        div[data-testid="column"]:has(#right-panel-anchor),
        div[data-testid="column"]:has(#paper-right-anchor),
        div[data-testid="column"]:has(#time-right-anchor),
        div[data-testid="column"]:has(#adv-search-right-anchor) {
            min-width: 0 !important;
            flex-shrink: 0 !important;
            height: auto !important;
            box-sizing: border-box !important;
        }
        div[data-testid="column"]:has(#left-panel-anchor),
        div[data-testid="column"]:has(#paper-left-anchor),
        div[data-testid="column"]:has(#time-left-anchor),
        div[data-testid="column"]:has(#adv-search-left-anchor) {
            overflow: visible !important;
            padding-right: 1rem !important;
        }
        div[data-testid="column"]:has(#left-panel-anchor),
        div[data-testid="column"]:has(#paper-left-anchor),
        div[data-testid="column"]:has(#time-left-anchor),
        div[data-testid="column"]:has(#adv-search-left-anchor) {
            position: sticky !important;
            top: 56px !important;
            align-self: flex-start !important;
            max-height: calc(100vh - 72px) !important;
            overflow-y: auto !important;
            overflow-x: hidden !important;
            scrollbar-gutter: stable !important;
        }
        div[data-testid="column"]:has(#left-panel-anchor)::-webkit-scrollbar,
        div[data-testid="column"]:has(#paper-left-anchor)::-webkit-scrollbar,
        div[data-testid="column"]:has(#time-left-anchor)::-webkit-scrollbar,
        div[data-testid="column"]:has(#adv-search-left-anchor)::-webkit-scrollbar {
            width: 8px;
        }
        div[data-testid="column"]:has(#left-panel-anchor)::-webkit-scrollbar-thumb,
        div[data-testid="column"]:has(#paper-left-anchor)::-webkit-scrollbar-thumb,
        div[data-testid="column"]:has(#time-left-anchor)::-webkit-scrollbar-thumb,
        div[data-testid="column"]:has(#adv-search-left-anchor)::-webkit-scrollbar-thumb {
            background: rgba(119, 102, 142, 0.28);
            border-radius: 999px;
        }
        div[data-testid="column"]:has(#right-panel-anchor),
        div[data-testid="column"]:has(#paper-right-anchor),
        div[data-testid="column"]:has(#time-right-anchor),
        div[data-testid="column"]:has(#adv-search-right-anchor) {
            overflow: visible !important;
            border-left: 1px solid #e1e4e8 !important;
            padding-left: 1.5rem !important;
        }
        div[data-testid="column"]:has(#right-panel-anchor) > div[data-testid="stVerticalBlock"],
        div[data-testid="column"]:has(#paper-right-anchor) > div[data-testid="stVerticalBlock"],
        div[data-testid="column"]:has(#time-right-anchor) > div[data-testid="stVerticalBlock"],
        div[data-testid="column"]:has(#adv-search-right-anchor) > div[data-testid="stVerticalBlock"],
        div[data-testid="column"]:has(#right-panel-anchor) > div[data-testid="stVerticalBlock"] > div[data-testid="stElementContainer"],
        div[data-testid="column"]:has(#paper-right-anchor) > div[data-testid="stVerticalBlock"] > div[data-testid="stElementContainer"],
        div[data-testid="column"]:has(#time-right-anchor) > div[data-testid="stVerticalBlock"] > div[data-testid="stElementContainer"],
        div[data-testid="column"]:has(#adv-search-right-anchor) > div[data-testid="stVerticalBlock"] > div[data-testid="stElementContainer"] {
            width: 100% !important;
            min-width: 0 !important;
            max-width: none !important;
            align-self: stretch !important;
            box-sizing: border-box !important;
        }
        /* Fill the detail pane with the editor/preview split instead of its intrinsic content width. */
        div[data-testid="column"]:has(#right-panel-anchor) div[data-testid="stHorizontalBlock"]:has(.mc-question-preview-anchor),
        div[data-testid="column"]:has(#paper-right-anchor) div[data-testid="stHorizontalBlock"]:has(.mc-question-preview-anchor),
        div[data-testid="column"]:has(#time-right-anchor) div[data-testid="stHorizontalBlock"]:has(.mc-question-preview-anchor),
        div[data-testid="column"]:has(#adv-search-right-anchor) div[data-testid="stHorizontalBlock"]:has(.mc-question-preview-anchor) {
            display: grid !important;
            grid-template-columns: minmax(0, 0.95fr) minmax(0, 1.05fr) !important;
            width: 100% !important;
            min-width: 0 !important;
            max-width: none !important;
            gap: 1rem !important;
            align-items: start !important;
        }
        div[data-testid="column"]:has(#right-panel-anchor) div[data-testid="stHorizontalBlock"]:has(.mc-question-preview-anchor) > div[data-testid="column"],
        div[data-testid="column"]:has(#paper-right-anchor) div[data-testid="stHorizontalBlock"]:has(.mc-question-preview-anchor) > div[data-testid="column"],
        div[data-testid="column"]:has(#time-right-anchor) div[data-testid="stHorizontalBlock"]:has(.mc-question-preview-anchor) > div[data-testid="column"],
        div[data-testid="column"]:has(#adv-search-right-anchor) div[data-testid="stHorizontalBlock"]:has(.mc-question-preview-anchor) > div[data-testid="column"] {
            width: auto !important;
            min-width: 0 !important;
            max-width: none !important;
            flex: initial !important;
            box-sizing: border-box !important;
        }
        div[data-testid="column"]:has(#right-panel-anchor) div[data-testid="stHorizontalBlock"]:has(.mc-question-preview-anchor) .stMarkdownContainer,
        div[data-testid="column"]:has(#paper-right-anchor) div[data-testid="stHorizontalBlock"]:has(.mc-question-preview-anchor) .stMarkdownContainer,
        div[data-testid="column"]:has(#time-right-anchor) div[data-testid="stHorizontalBlock"]:has(.mc-question-preview-anchor) .stMarkdownContainer,
        div[data-testid="column"]:has(#adv-search-right-anchor) div[data-testid="stHorizontalBlock"]:has(.mc-question-preview-anchor) .stMarkdownContainer {
            width: 100% !important;
            max-width: none !important;
            overflow-wrap: anywhere;
        }
        @media (max-width: 980px) {
            div[data-testid="column"]:has(#left-panel-anchor),
            div[data-testid="column"]:has(#paper-left-anchor),
            div[data-testid="column"]:has(#time-left-anchor),
            div[data-testid="column"]:has(#adv-search-left-anchor) {
                position: static !important;
                max-height: none !important;
                overflow: visible !important;
                padding-right: 0 !important;
            }
            div[data-testid="column"]:has(#right-panel-anchor) div[data-testid="stHorizontalBlock"]:has(.mc-question-preview-anchor),
            div[data-testid="column"]:has(#paper-right-anchor) div[data-testid="stHorizontalBlock"]:has(.mc-question-preview-anchor),
            div[data-testid="column"]:has(#time-right-anchor) div[data-testid="stHorizontalBlock"]:has(.mc-question-preview-anchor),
            div[data-testid="column"]:has(#adv-search-right-anchor) div[data-testid="stHorizontalBlock"]:has(.mc-question-preview-anchor) {
                grid-template-columns: minmax(0, 1fr) !important;
            }
        }
        div[data-testid="column"]:has(#right-panel-anchor) div[data-testid="stHorizontalBlock"]:has(.mc-question-preview-anchor) div[data-testid="stTextArea"],
        div[data-testid="column"]:has(#paper-right-anchor) div[data-testid="stHorizontalBlock"]:has(.mc-question-preview-anchor) div[data-testid="stTextArea"],
        div[data-testid="column"]:has(#time-right-anchor) div[data-testid="stHorizontalBlock"]:has(.mc-question-preview-anchor) div[data-testid="stTextArea"],
        div[data-testid="column"]:has(#adv-search-right-anchor) div[data-testid="stHorizontalBlock"]:has(.mc-question-preview-anchor) div[data-testid="stTextArea"] {
            width: 100% !important;
            max-width: none !important;
        }
        /* ===== 按录入顺序浏览：浏览设置已上移到标题行，内容区铺满 ===== */
        div[data-testid="stHorizontalBlock"]:has(#time-left-anchor):has(#time-right-anchor) {
            gap: 0 !important;
        }
        /* 左列塌缩为 0（仅保留锚点，让既有分栏 CSS 继续命中） */
        div[data-testid="column"]:has(#time-left-anchor) {
            flex: 0 0 0 !important;
            width: 0 !important;
            max-width: 0 !important;
            min-width: 0 !important;
            padding: 0 !important;
            margin: 0 !important;
            overflow: hidden !important;
            border: none !important;
            position: static !important;
            max-height: none !important;
        }
        div[data-testid="column"]:has(#time-right-anchor) {
            flex: 1 1 100% !important;
            width: 100% !important;
            max-width: 100% !important;
            border-left: none !important;
            padding-left: 0 !important;
        }
        /* 标题行内联的浏览设置：小字号、单行、均匀分布 */
        div[data-testid="stHorizontalBlock"]:has(.mc-browse-settings-anchor) {
            align-items: center !important;
            gap: 0.55rem !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.mc-browse-settings-anchor) h2 {
            margin: 0 !important;
            padding: 0 !important;
            font-size: 1.85rem !important;
            line-height: 1.1 !important;
            white-space: nowrap !important;
            color: var(--mc-text) !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.mc-browse-settings-anchor) div[data-testid="stRadio"] {
            display: flex !important;
            flex-direction: row !important;
            align-items: center !important;
            flex-wrap: nowrap !important;
            gap: 0.45rem !important;
            margin: 0 !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.mc-browse-settings-anchor) div[data-testid="stRadio"] > label[data-testid="stWidgetLabel"] {
            margin: 0 !important;
            min-height: 0 !important;
            flex: 0 0 auto !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.mc-browse-settings-anchor) div[data-testid="stRadio"] > label[data-testid="stWidgetLabel"] p {
            margin: 0 !important;
            font-size: 0.78rem !important;
            font-weight: 700 !important;
            color: var(--mc-muted) !important;
            white-space: nowrap !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.mc-browse-settings-anchor) div[data-testid="stRadio"] div[role="radiogroup"] {
            display: flex !important;
            flex-direction: row !important;
            align-items: center !important;
            flex-wrap: nowrap !important;
            gap: 0.7rem !important;
            margin: 0 !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.mc-browse-settings-anchor) div[data-testid="stRadio"] div[role="radiogroup"] > label {
            margin: 0 !important;
            padding: 0 !important;
            min-height: 0 !important;
            gap: 0.3rem !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.mc-browse-settings-anchor) div[data-testid="stRadio"] div[role="radiogroup"] > label p {
            margin: 0 !important;
            font-size: 0.8rem !important;
            white-space: nowrap !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.mc-browse-settings-anchor) div[data-testid="stSlider"] {
            display: flex !important;
            flex-direction: row !important;
            align-items: center !important;
            gap: 0.45rem !important;
            margin: 0 !important;
            padding-top: 0 !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.mc-browse-settings-anchor) div[data-testid="stSlider"] > label[data-testid="stWidgetLabel"] {
            margin: 0 !important;
            min-height: 0 !important;
            flex: 0 0 auto !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.mc-browse-settings-anchor) div[data-testid="stSlider"] > label[data-testid="stWidgetLabel"] p {
            margin: 0 !important;
            font-size: 0.78rem !important;
            font-weight: 700 !important;
            color: var(--mc-muted) !important;
            white-space: nowrap !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.mc-browse-settings-anchor) div[data-testid="stSlider"] > div {
            flex: 1 1 auto !important;
            min-width: 90px !important;
        }
        /* 滑块只保留轨道，当前数值在右侧「每页 N 道」中显示，压成一行高度 */
        div[data-testid="stHorizontalBlock"]:has(.mc-browse-settings-anchor) div[data-testid="stSlider"] [data-testid="stThumbValue"],
        div[data-testid="stHorizontalBlock"]:has(.mc-browse-settings-anchor) div[data-testid="stSlider"] [data-testid="stSliderThumbValue"] {
            display: none !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.mc-browse-settings-anchor) div[data-testid="stSlider"] [data-baseweb="slider"] {
            margin: 0 !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.mc-browse-settings-anchor) div[data-testid="stSlider"] div[role="slider"] {
            top: 50% !important;
        }
        /* 隐藏刻度条，让滑块压成一行高度 */
        div[data-testid="stHorizontalBlock"]:has(.mc-browse-settings-anchor) div[data-testid="stSlider"] [data-testid="stTickBar"] {
            display: none !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.mc-browse-settings-anchor) div[data-testid="stSlider"] [data-baseweb="slider"] {
            margin: 0.1rem 0 !important;
        }
        /* 标题容器去掉默认上下留白，与工具控件同一基线 */
        div[data-testid="stHorizontalBlock"]:has(.mc-browse-settings-anchor) [data-testid="stHeadingWithActionElements"] {
            margin: 0 !important;
            padding: 0 !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.mc-browse-settings-anchor) [data-testid="stHeadingWithActionElements"] h2 {
            margin: 0 !important;
        }
        .mc-browse-page-indicator {
            font-size: 0.78rem;
            font-weight: 700;
            color: var(--mc-muted);
            line-height: 1.2;
            text-align: center;
            white-space: nowrap;
            padding-left: 0.15rem;
        }
        div[data-testid="stHorizontalBlock"]:has(.mc-browse-settings-anchor) div[data-testid="stButton"] > button {
            min-height: 32px !important;
            height: 32px !important;
            font-size: 0.78rem !important;
            padding: 0 0.55rem !important;
            white-space: nowrap !important;
        }
        /* ===== 浏览列表整体上移、收紧：分隔线 / 计数行 / 卡片内边距 ===== */
        .mc-browse-divider {
            margin: 0.3rem 0 0.35rem !important;
            border-top: 1px solid #e1e4e8 !important;
        }
        .mc-browse-count {
            font-size: 1.12rem;
            font-weight: 800;
            line-height: 1.3;
            color: var(--mc-text);
            margin: 0.1rem 0 0.45rem;
        }
        /* 组卷页：浮动试题篮的 iframe 占位不占高度，浏览列表整体上移 */
        body:has(#mc-exam-page-anchor) div[data-testid="stVerticalBlock"]:has(> div[data-testid="stVerticalBlockBorderWrapper"].mc-exam-floating-basket-panel) {
            gap: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
            min-height: 0 !important;
        }
        body:has(#mc-exam-page-anchor) div[data-testid="stVerticalBlock"]:has(> div[data-testid="stVerticalBlockBorderWrapper"].mc-exam-floating-basket-panel) > div[data-testid="element-container"]:has(iframe[data-testid="stIFrame"]) {
            display: none !important;
        }
        /* 组卷页选题卡片：外壳内边距收小，卡片更紧凑 */
        body:has(#mc-exam-page-anchor) div[data-testid="stVerticalBlockBorderWrapper"]:has(.mc-exam-card-anchor) {
            padding: 0.45rem 0.65rem 0.5rem !important;
        }
        /* 调整 st.dialog 的背景遮罩透明度为 40% 黑色 */
        div[data-testid="stDialog"] > div:first-child {
            background-color: rgba(0, 0, 0, 0.7) !important;
        }

        /* st.dialog 基础背景适配主题配色，保证阅读舒适 */
        div[data-testid="stDialog"] div[role="dialog"] {
            background: var(--mc-surface, #ffffff) !important;
            color: var(--mc-text, #1e293b) !important;
            border: 1px solid var(--mc-border, #e2e8f0) !important;
            border-radius: var(--mc-radius, 14px) !important;
            box-shadow: var(--mc-shadow) !important;
        }
        div[data-testid="stDialog"] div[role="dialog"] h2,
        div[data-testid="stDialog"] div[role="dialog"] p,
        div[data-testid="stDialog"] div[role="dialog"] span,
        div[data-testid="stDialog"] div[role="dialog"] label {
            color: var(--mc-text, #1e293b) !important;
        }
        /* 仅在查看大图时切换为宽屏暗光影院模式 */
        div[data-testid="stDialog"]:has(#img-container) div[role="dialog"] {
            width: 90vw !important;
            max-width: 1600px !important;
            background: #181c24 !important;
            color: #ffffff !important;
        }
        </style>
    """, unsafe_allow_html=True)

AI_ENV_DEFAULTS = {
    "AI_API_KEY": "",
    "AI_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "AI_MODEL_NAME": "qwen3.8-max",
    "AI_SOLVER_MODEL_NAME": "qwen3.6-flash",
    "AI_EMBEDDING_MODEL_NAME": "",
    "APP_THEME_MODE": "light",
    "APP_THEME_SKIN": "claude_warm",
    "AI_OCR_PROMPT": (
        "请识别图片中的高中物理题，并严格按照 LaTeX 格式输出。"
        "如果图片中有多道独立题目，必须逐题输出独立的文件名分隔符和完整 problem、answer、solutions 环境，"
        "禁止把多道题合并进同一个 problem 环境。"
        "每题内容中禁止出现 \\newpage、\\clearpage、\\pagebreak 等分页命令。"
    ),
}

AI_ENV_WRITE_ORDER = (
    "AI_API_KEY",
    "AI_BASE_URL",
    "AI_MODEL_NAME",
    "AI_SOLVER_MODEL_NAME",
    "AI_EMBEDDING_MODEL_NAME",
    "APP_THEME_MODE",
    "APP_THEME_SKIN",
)

def _root_env_path() -> str:
    return os.path.join(APP_ROOT, ".env")

def _read_root_ai_env_config() -> dict:
    env_path = _root_env_path()
    file_exists = os.path.exists(env_path)
    raw = {}
    if file_exists:
        try:
            raw = {k: (v or "") for k, v in dotenv_values(env_path).items() if k}
        except Exception:
            raw = {}

    values = {}
    for key, default in AI_ENV_DEFAULTS.items():
        if file_exists:
            values[key] = raw.get(key, default)
        else:
            values[key] = os.getenv(key, default)
    return values

def _format_env_value(value: str) -> str:
    value = (value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    value = value.replace("\n", "\\n")
    if value == "":
        return ""
    if re.search(r"\s|#|\"|'|\\", value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\"') + '"'
    return value

def _write_root_ai_env_config(updates: dict):
    env_path = _root_env_path()
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    else:
        lines = [
            "# AI service configuration.",
            "# Managed by UtopiaPhyCyclus API 设置.",
            "",
        ]

    seen = set()
    output = []
    env_line_re = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
    for line in lines:
        match = env_line_re.match(line)
        if match and match.group(1) in updates:
            key = match.group(1)
            output.append(f"{key}={_format_env_value(updates[key])}")
            seen.add(key)
        else:
            output.append(line)

    missing = [key for key in AI_ENV_WRITE_ORDER if key in updates and key not in seen]
    if missing and output and output[-1].strip():
        output.append("")
    for key in missing:
        output.append(f"{key}={_format_env_value(updates[key])}")

    atomic_write_text(env_path, "\n".join(output).rstrip() + "\n", backup=False)
    load_dotenv(env_path, override=True)

def _load_cached_api_models() -> list[str]:
    cache_path = os.path.join(APP_ROOT, ".api_models_cache.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list) and data:
                    return data
        except Exception:
            pass
    return []

def _save_cached_api_models(models: list[str]) -> None:
    cache_path = os.path.join(APP_ROOT, ".api_models_cache.json")
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(models, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

THEME_SKIN_OPTIONS = ("default", "liquid_glass", "claude_warm", "crystal_glass")

def _normalize_theme_skin(skin: str) -> str:
    return normalize_skin_id(skin)

def _get_current_theme_skin() -> str:
    _load_appearance_preferences()
    return st.session_state['app_theme_skin']


def _load_appearance_preferences():
    if not st.session_state.get('_appearance_preferences_loaded',False):
        from services.ui_preferences import load_preferences
        preferences=load_preferences(_read_root_ai_env_config())
        st.session_state['app_theme_skin']=preferences['skin']
        st.session_state['app_theme_mode']=preferences['mode']
        st.session_state['_appearance_preferences_loaded']=True

def _set_current_theme_skin(skin: str):
    norm = _normalize_theme_skin(skin)
    from services.ui_preferences import save_preferences
    save_preferences(norm,_get_current_theme_mode())
    st.session_state['app_theme_skin']=norm

def _get_current_theme_mode() -> str:
    _load_appearance_preferences()
    return st.session_state['app_theme_mode']

def _set_current_theme_mode(mode: str):
    from services.ui_preferences import save_preferences
    mode='dark' if mode=='dark' else 'light'
    save_preferences(_get_current_theme_skin(),mode)
    st.session_state['app_theme_mode']=mode


def page_ui_settings():
    from services.theme_settings_ui import render_theme_settings
    render_theme_settings(_get_current_theme_skin(),_get_current_theme_mode(),
        _set_current_theme_skin,_set_current_theme_mode)

def _read_ocr_prompt_for_settings(config: dict) -> tuple[str, bool]:
    if os.path.exists(ocr_prompt_file):
        with open(ocr_prompt_file, "r", encoding="utf-8") as f:
            return f.read(), True
    return config.get("AI_OCR_PROMPT", AI_ENV_DEFAULTS["AI_OCR_PROMPT"]).replace("\\n", "\n"), False

def _write_ocr_prompt_file(prompt: str):
    normalized = (prompt or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    atomic_write_text(ocr_prompt_file, normalized + ("\n" if normalized else ""), backup=False)
    globals()["AI_OCR_PROMPT"] = normalized

def page_api_settings():
    """API 配置：侧边栏设置下的二级功能。"""
    st.markdown('<span id="mc-api-page-anchor"></span>', unsafe_allow_html=True)
    _c_api_title, _c_api_hint = st.columns([1.1, 3.0], gap="small")
    with _c_api_title:
        st.header("⚙️ API 设置")
    with _c_api_hint:
        st.caption("修改后点「保存配置」写入根目录 .env / ocr_prompt.txt，当前会话立即生效。")
    env_path = _root_env_path()
    config = _read_root_ai_env_config()
    ocr_prompt_value, prompt_file_exists = _read_ocr_prompt_for_settings(config)
    env_state = "已读取根目录 .env" if os.path.exists(env_path) else "未找到 .env，保存时会自动创建"
    prompt_state = "已读取根目录 ocr_prompt.txt" if prompt_file_exists else "未找到 ocr_prompt.txt，保存时会自动创建"

    st.markdown(
        """
        <style>
        /* API 设置使用深色高对比控件，并覆盖 BaseWeb 的原生聚焦背景。 */
        div[data-testid="stMainBlockContainer"]:has(#mc-api-settings-anchor) div[data-testid="stTextInput"] input,
        div[data-testid="stMainBlockContainer"]:has(#mc-api-settings-anchor) div[data-testid="stTextArea"] textarea,
        div[data-testid="stMainBlockContainer"]:has(#mc-api-settings-anchor) div[data-baseweb="input"],
        div[data-testid="stMainBlockContainer"]:has(#mc-api-settings-anchor) div[data-baseweb="textarea"],
        div[data-testid="stMainBlockContainer"]:has(#mc-api-settings-anchor) div[data-baseweb="input"] > div,
        div[data-testid="stMainBlockContainer"]:has(#mc-api-settings-anchor) div[data-baseweb="textarea"] > div {
            border: 1px solid var(--mc-border) !important;
            border-color: var(--mc-border) !important;
            outline: none !important;
            box-shadow: inset 0 1px 1px rgba(44, 39, 51, 0.03) !important;
            background: var(--mc-surface) !important;
            color: var(--mc-text) !important;
            caret-color: var(--mc-text) !important;
        }
        div[data-testid="stMainBlockContainer"]:has(#mc-api-settings-anchor) div[data-testid="stTextInput"] input:focus,
        div[data-testid="stMainBlockContainer"]:has(#mc-api-settings-anchor) div[data-testid="stTextArea"] textarea:focus,
        div[data-testid="stMainBlockContainer"]:has(#mc-api-settings-anchor) div[data-baseweb="input"]:focus-within,
        div[data-testid="stMainBlockContainer"]:has(#mc-api-settings-anchor) div[data-baseweb="textarea"]:focus-within,
        div[data-testid="stMainBlockContainer"]:has(#mc-api-settings-anchor) div[data-baseweb="input"] > div:focus-within,
        div[data-testid="stMainBlockContainer"]:has(#mc-api-settings-anchor) div[data-baseweb="textarea"] > div:focus-within {
            border: 1px solid var(--mc-action-border) !important;
            border-color: var(--mc-action-border) !important;
            box-shadow: 0 0 0 3px var(--mc-focus), inset 0 1px 1px rgba(44, 39, 51, 0.03) !important;
            background: var(--mc-surface-soft) !important;
            color: var(--mc-text) !important;
        }
        div[data-testid="stMainBlockContainer"]:has(#mc-api-settings-anchor) input::placeholder,
        div[data-testid="stMainBlockContainer"]:has(#mc-api-settings-anchor) textarea::placeholder {
            color: var(--mc-muted) !important;
            opacity: 1 !important;
        }
        div[data-testid="stMainBlockContainer"]:has(#mc-api-settings-anchor) input:invalid,
        div[data-testid="stMainBlockContainer"]:has(#mc-api-settings-anchor) textarea:invalid,
        div[data-testid="stMainBlockContainer"]:has(#mc-api-settings-anchor) div[data-baseweb="input"]:has(input:invalid),
        div[data-testid="stMainBlockContainer"]:has(#mc-api-settings-anchor) div[data-baseweb="textarea"]:has(textarea:invalid),
        div[data-testid="stMainBlockContainer"]:has(#mc-api-settings-anchor) div[data-baseweb="input"]:has(input[aria-invalid="true"]) > div,
        div[data-testid="stMainBlockContainer"]:has(#mc-api-settings-anchor) div[data-baseweb="textarea"]:has(textarea[aria-invalid="true"]) > div,
        div[data-testid="stMainBlockContainer"]:has(#mc-api-settings-anchor) div[data-baseweb="input"]:has(input[aria-invalid="true"]),
        div[data-testid="stMainBlockContainer"]:has(#mc-api-settings-anchor) div[data-baseweb="textarea"]:has(textarea[aria-invalid="true"]),
        div[data-testid="stMainBlockContainer"]:has(#mc-api-settings-anchor) div[data-testid="stTextInput"] input[aria-invalid="true"],
        div[data-testid="stMainBlockContainer"]:has(#mc-api-settings-anchor) div[data-testid="stTextArea"] textarea[aria-invalid="true"] {
            border: 1px solid var(--mc-action-border) !important;
            border-color: var(--mc-action-border) !important;
            outline: none !important;
            box-shadow: 0 0 0 3px rgba(109, 40, 217, 0.12), inset 0 1px 1px rgba(44, 39, 51, 0.03) !important;
        }
        </style>
        <span id="mc-api-settings-anchor"></span>
        """,
        unsafe_allow_html=True,
    )

    st.caption(f"{env_state}：{env_path}")
    st.caption(f"{prompt_state}：{ocr_prompt_file}")
    st.info("OCR 实际优先使用 ocr_prompt.txt。下方提示词框显示并保存的就是这个文件内容；.env 里的 AI_OCR_PROMPT 只作为文件不存在时的备用。", icon="ℹ️")

    cur_api_key = config.get("AI_API_KEY", "")
    cur_base_url = config.get("AI_BASE_URL", AI_ENV_DEFAULTS["AI_BASE_URL"])
    cur_ocr_model = config.get("AI_MODEL_NAME", AI_ENV_DEFAULTS["AI_MODEL_NAME"])

    # API 实际可用模型探测与极小请求测试控制栏
    c_fetch, c_probe = st.columns([1.2, 1.2])
    with c_fetch:
        if st.button("🔄 自动拉取 API 可用模型列表", key="btn_fetch_api_models", use_container_width=True):
            with st.spinner("正在从当前 Base URL 拉取实际可用模型..."):
                ok, fetched_models, err = fetch_available_models(cur_base_url, cur_api_key)
            if ok and fetched_models:
                st.session_state["api_discovered_models"] = fetched_models
                _save_cached_api_models(fetched_models)
                st.toast(f"成功获取到 {len(fetched_models)} 个可用模型！", icon="✅")
                st.rerun()
            else:
                st.error(f"获取模型列表失败: {err}")

    with c_probe:
        if st.button("🧪 极小请求测试当前主模型可用性", key="btn_probe_current_model", use_container_width=True):
            with st.spinner(f"正在发送 1-token 极小请求探测 [{cur_ocr_model}]..."):
                res = test_model_availability(cur_base_url, cur_api_key, cur_ocr_model)
            if res.get("ok"):
                st.toast(res.get("message", "模型测试可用！"), icon="✅")
                st.success(f"✅ {res.get('message', '测试成功')}")
            else:
                st.error(res.get("error", "模型不可用"))

    # 读取当前 API 实际可用的模型列表（来自 API 探测或本地缓存）
    discovered_models = st.session_state.get("api_discovered_models") or _load_cached_api_models()
    if not discovered_models and cur_base_url and cur_api_key:
        ok, f_models, _ = fetch_available_models(cur_base_url, cur_api_key, timeout=3)
        if ok and f_models:
            discovered_models = f_models
            st.session_state["api_discovered_models"] = discovered_models
            _save_cached_api_models(discovered_models)

    with st.form("api_settings_form"):
        api_key = st.text_input(
            "API Key（密钥字符串，例如 sk-... 或 DashScope API Key）",
            value=config.get("AI_API_KEY", ""),
            type="password",
            placeholder="sk-... / dashscope_xxx",
        )
        base_url = st.text_input(
            "Base URL（接口根地址，例如 https://dashscope.aliyuncs.com/compatible-mode/v1）",
            value=config.get("AI_BASE_URL", AI_ENV_DEFAULTS["AI_BASE_URL"]),
            placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

        # 1. OCR / Agent 主模型下拉构建
        cur_model = config.get("AI_MODEL_NAME", AI_ENV_DEFAULTS["AI_MODEL_NAME"])
        if discovered_models:
            ocr_opts = list(discovered_models)
            if cur_model and cur_model not in ocr_opts:
                ocr_opts.insert(0, cur_model)
        else:
            ocr_opts = [cur_model] if cur_model else []
        ocr_opts.append("自定义输入...")
        default_ocr_idx = ocr_opts.index(cur_model) if cur_model in ocr_opts else (len(ocr_opts) - 1)
        sel_ocr_model = st.selectbox(
            "OCR / 图片识别与插图 Agent 主模型（下拉选择实际可用模型）",
            options=ocr_opts,
            index=default_ocr_idx,
            help="下拉选项直接来源于当前 API 支持的真实模型列表",
        )
        if sel_ocr_model == "自定义输入...":
            custom_ocr_model = st.text_input(
                "自定义 OCR/Agent 模型名称",
                value=cur_model if cur_model not in discovered_models else "",
                placeholder="输入实际的模型名称，例如：gemini-3.8-flash-high",
            )
        else:
            custom_ocr_model = ""

        # 2. 解题模型下拉构建
        cur_solver = config.get("AI_SOLVER_MODEL_NAME", AI_ENV_DEFAULTS["AI_SOLVER_MODEL_NAME"])
        if discovered_models:
            solver_opts = list(discovered_models)
            if cur_solver and cur_solver not in solver_opts:
                solver_opts.insert(0, cur_solver)
        else:
            solver_opts = [cur_solver] if cur_solver else []
        solver_opts.append("自定义输入...")
        default_solver_idx = solver_opts.index(cur_solver) if cur_solver in solver_opts else (len(solver_opts) - 1)
        sel_solver = st.selectbox(
            "解题模型（下拉选择；负责解答推导与生成）",
            options=solver_opts,
            index=default_solver_idx,
            help="用于解答生成与解析推导的模型",
        )
        if sel_solver == "自定义输入...":
            custom_solver = st.text_input(
                "自定义解题模型名称",
                value=cur_solver if cur_solver not in discovered_models else "",
                placeholder="输入解题模型名称，例如：deepseek-reasoner",
            )
        else:
            custom_solver = ""

        # 3. Embedding 模型下拉构建
        cur_emb = config.get("AI_EMBEDDING_MODEL_NAME", AI_ENV_DEFAULTS["AI_EMBEDDING_MODEL_NAME"])
        emb_opts = ["不启用 Embedding (仅精确检索)"]
        if discovered_models:
            # 优先提取带 embed 或 embedding 的模型，无则展示全部
            emb_candidates = [m for m in discovered_models if "embed" in m.lower()]
            emb_opts.extend(emb_candidates if emb_candidates else discovered_models)
        if cur_emb and cur_emb not in emb_opts:
            emb_opts.insert(1, cur_emb)
        emb_opts.append("自定义输入...")
        if not cur_emb:
            default_emb_idx = 0
        elif cur_emb in emb_opts:
            default_emb_idx = emb_opts.index(cur_emb)
        else:
            default_emb_idx = len(emb_opts) - 1

        sel_emb = st.selectbox(
            "Embedding 检索模型（下拉选择；用于语义搜索）",
            options=emb_opts,
            index=default_emb_idx,
            help="选择不启用可继续使用纯精确搜索；选择向量模型后可在检索页启用语义搜索。",
        )
        if sel_emb == "自定义输入...":
            custom_emb = st.text_input(
                "自定义 Embedding 模型名称",
                value=cur_emb if cur_emb not in emb_opts else "",
                placeholder="例如：text-embedding-v4",
            )
        else:
            custom_emb = ""

        ocr_prompt = st.text_area(
            "OCR 提示词（保存到 ocr_prompt.txt）",
            value=ocr_prompt_value,
            height=260,
        )

        st.markdown(
            '<div class="mc-theme-fixed-note">🎨 外观请在「设置 → UI设置」中选择，支持四套皮肤及浅色／深色模式。</div>',
            unsafe_allow_html=True,
        )

        submitted = st.form_submit_button("保存配置", type="primary", use_container_width=True)

    if submitted:
        required_missing = []
        if not api_key.strip():
            required_missing.append("API Key")
        if not base_url.strip():
            required_missing.append("Base URL")
        final_model_name = custom_ocr_model.strip() if sel_ocr_model == "自定义输入..." else sel_ocr_model.strip()
        final_solver_model = custom_solver.strip() if sel_solver == "自定义输入..." else sel_solver.strip()
        if sel_emb == "不启用 Embedding (仅精确检索)":
            final_embedding_model = ""
        elif sel_emb == "自定义输入...":
            final_embedding_model = custom_emb.strip()
        else:
            final_embedding_model = sel_emb.strip()

        if not final_model_name:
            required_missing.append("OCR / 图片识别模型")
        if required_missing:
            st.error("请补全：" + "、".join(required_missing))
            return

        try:
            _write_root_ai_env_config({
                "AI_API_KEY": api_key.strip(),
                "AI_BASE_URL": base_url.strip(),
                "AI_MODEL_NAME": final_model_name,
                "AI_SOLVER_MODEL_NAME": final_solver_model,
                "AI_EMBEDDING_MODEL_NAME": final_embedding_model,
            })
            _write_ocr_prompt_file(ocr_prompt)
            st.success(".env 与配置已保存，当前会话已更新 API 参数。")
            st.rerun()
        except Exception as e:
            st.error(f"保存失败：{e}")

def _query_param_enabled(name: str) -> bool:
    try:
        value = st.query_params.get(name, "")
    except Exception:
        return False
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value).lower() in {"1", "true", "yes", "on"}

def _remove_query_param(name: str):
    try:
        if name in st.query_params:
            del st.query_params[name]
    except Exception:
        pass

def format_question_title(filename):
    """
    Format a question filename into a readable title.
    Expected filename format: Year-Type-PaperName-Num-Subject.tex
    Expected output: 【Year PaperName 第Num题】 (Subject)
    """
    basename = os.path.basename(filename).replace(".tex", "")
    parts = basename.split("-")
    if len(parts) >= 5:
        year = parts[0]
        paper_name = parts[2]
        num = parts[3]
        # 处理可能包含连字符的 subject (防万一)
        subject = "-".join(parts[4:])
        return f"【{year} {paper_name} 第{num}题】 ({subject})"
    elif len(parts) >= 4:
        year = parts[0]
        paper_name = parts[2]
        num = parts[3]
        return f"【{year} {paper_name} 第{num}题】"
    else:
        return basename

def extract_problem_header_fields(tex: str):
    m = re.search(r'\\begin\{problem\}\{(.*?)\}\{(.*?)\}\{(.*?)\}\{(.*?)\}\{(.*?)\}', tex or "", re.DOTALL)
    if not m:
        return None
    y, t, p, n, s = m.groups()
    t = (t or "").strip()
    t_clean = t.split("(")[0].split("（")[0].strip()
    if t_clean in PAPER_TYPES:
        t = t_clean
    else:
        for k, v in PAPER_TYPES.items():
            if t_clean == v or t == v:
                t = k
                break
    return {
        "year": (y or "").strip(),
        "p_type": t,
        "paper": (p or "").strip(),
        "number": (n or "").strip(),
        "subject_str": (s or "").strip(),
    }

def replace_problem_header(tex: str, new_year: str, new_type: str, new_name: str, new_num: str, new_subject_str: str) -> str:
    new_header = f"\\begin{{problem}}{{{new_year}}}{{{new_type}}}{{{new_name}}}{{{new_num}}}{{{new_subject_str}}}"
    s = repair_problem_headers(tex or "")
    s2, replaced_count = re.subn(
        r"\\begin\{problem\}\s*\{.*?\}\s*\{.*?\}\s*\{.*?\}\s*\{.*?\}\s*\{.*?\}",
        lambda _m: new_header,
        s,
        count=1,
    )
    if replaced_count == 0 and "\\begin{problem}" in s:
        s2 = re.sub(r"\\begin\{problem\}", lambda _m: new_header, s, count=1)
    return s2

def apply_meta_rename_and_update(old_path: str, new_year: str, new_type: str, new_name: str, new_num: str, new_subject_str: str):
    old_path = old_path or ""
    if not old_path or not os.path.exists(old_path):
        raise FileNotFoundError(old_path)

    base = os.path.basename(old_path).replace(".tex", "")
    parts = base.split("-")
    if len(parts) < 5:
        raise ValueError("invalid filename format")

    primary_subj = (new_subject_str or "").split("，")[0].strip() if new_subject_str else ""
    target_dir = os.path.join(CHAPTERS_DIR, primary_subj, str(new_year))
    ensure_dir(target_dir)

    new_filename = generate_filename(new_year, new_type, new_name, new_num, new_subject_str)
    new_path = os.path.join(target_dir, new_filename)

    with open(old_path, "r", encoding="utf-8") as f:
        old_content = f.read()

    new_content = replace_problem_header(old_content, str(new_year), new_type, new_name, new_num, new_subject_str)
    same_path = os.path.normcase(os.path.abspath(new_path)) == os.path.normcase(os.path.abspath(old_path))
    if not same_path and os.path.exists(new_path):
        raise FileExistsError(f"目标题目文件已存在：{new_path}")

    backup_path = ""
    if same_path:
        atomic_write_text(new_path, new_content, backup=True)
    else:
        atomic_write_text(new_path, new_content, backup=False)
        try:
            backup_path = backup_existing_file(old_path)
            os.remove(old_path)
        except Exception:
            if os.path.exists(new_path):
                os.remove(new_path)
            raise

    try:
        update_csv_index_for_edit(old_path, new_path, new_content, str(new_year), new_type, new_name, new_num, new_subject_str)
    except Exception:
        if not same_path:
            try:
                if os.path.exists(new_path):
                    os.remove(new_path)
                if backup_path and os.path.exists(backup_path):
                    ensure_dir(os.path.dirname(old_path))
                    shutil.copy2(backup_path, old_path)
            except OSError:
                pass
        raise

    record_operation(
        "rename_question" if not same_path else "update_question_meta",
        path=os.path.abspath(new_path),
        related_path=os.path.abspath(old_path) if not same_path else "",
        details="metadata and filename updated",
    )
    _invalidate_semantic_for_file(old_path)
    _invalidate_semantic_for_file(new_path)
    clear_statistics_cache()
    _clear_advanced_search_result_cache()
    return new_path, new_content


THEME_SKIN_PALETTES = {
    "praetorius": {
        "name": "Tetiana Praetorius · 极简现代风",
        "description": "经典小红书商务科技风，微哑光纯色现代卡片，沉稳清晰，低眩光视觉层级",
        "light": {
            "color-scheme": "light",
            "--mc-bg": "#f4f6fa",
            "--mc-surface": "#ffffff",
            "--mc-surface-soft": "#edf2f7",
            "--mc-sidebar": "#f8fafc",
            "--mc-border": "#e2e8f0",
            "--mc-border-strong": "#cbd5e1",
            "--mc-text": "#1e293b",
            "--mc-muted": "#64748b",
            "--mc-accent": "#6366f1",
            "--mc-accent-dark": "#4f46e5",
            "--mc-accent-soft": "rgba(99, 102, 241, 0.08)",
            "--mc-action-bg": "#4f46e5",
            "--mc-action-bg-hover": "#4338ca",
            "--mc-action-border": "#4f46e5",
            "--mc-action-text": "#ffffff",
            "--mc-action-shadow": "0 6px 18px rgba(79, 70, 229, 0.22)",
            "--mc-focus": "rgba(99, 102, 241, 0.22)",
            "--mc-radius": "14px",
            "--mc-control-radius": "9px",
            "--mc-shadow": "0 4px 20px -2px rgba(15, 23, 42, 0.05), 0 1px 3px rgba(15, 23, 42, 0.03)",
            "--mc-backdrop": "none",
            "--mc-card-border": "1px solid var(--mc-border)",
            "--mc-tooltip-bg": "#0f172a",
            "--mc-tooltip-text": "#f8fafc",
        },
        "dark": {
            "color-scheme": "dark",
            "--mc-bg": "#0f141c",
            "--mc-surface": "#171f2c",
            "--mc-surface-soft": "#1e2838",
            "--mc-sidebar": "#131a26",
            "--mc-border": "#283548",
            "--mc-border-strong": "#3c4e68",
            "--mc-text": "#f1f5f9",
            "--mc-muted": "#94a3b8",
            "--mc-accent": "#818cf8",
            "--mc-accent-dark": "#a5b4fc",
            "--mc-accent-soft": "rgba(129, 140, 248, 0.14)",
            "--mc-action-bg": "#6366f1",
            "--mc-action-bg-hover": "#4f46e5",
            "--mc-action-border": "#818cf8",
            "--mc-action-text": "#ffffff",
            "--mc-action-shadow": "0 6px 18px rgba(99, 102, 241, 0.35)",
            "--mc-focus": "rgba(129, 140, 248, 0.25)",
            "--mc-radius": "14px",
            "--mc-control-radius": "9px",
            "--mc-shadow": "0 6px 24px -2px rgba(0, 0, 0, 0.45)",
            "--mc-backdrop": "none",
            "--mc-card-border": "1px solid var(--mc-border)",
            "--mc-tooltip-bg": "#1e293b",
            "--mc-tooltip-text": "#f8fafc",
        },
    },
    "ghani_glass": {
        "name": "Ghani Pradita · 灵动毛玻璃拟态风",
        "description": "小红书最新高热毛玻璃风，光感弥散氛围背景、通透半透明卡片与水晶高光边框",
        "light": {
            "color-scheme": "light",
            "--mc-bg": "radial-gradient(at 0% 0%, rgba(246, 218, 203, 0.52) 0px, transparent 55%), radial-gradient(at 100% 0%, rgba(219, 234, 254, 0.52) 0px, transparent 50%), radial-gradient(at 100% 100%, rgba(254, 240, 218, 0.48) 0px, transparent 55%), #f4f6fb",
            "--mc-surface": "rgba(255, 255, 255, 0.72)",
            "--mc-surface-soft": "rgba(255, 255, 255, 0.52)",
            "--mc-sidebar": "rgba(255, 255, 255, 0.68)",
            "--mc-border": "rgba(255, 255, 255, 0.82)",
            "--mc-border-strong": "rgba(226, 232, 240, 0.85)",
            "--mc-text": "#1f2937",
            "--mc-muted": "#6b7280",
            "--mc-accent": "#6366f1",
            "--mc-accent-dark": "#4f46e5",
            "--mc-accent-soft": "rgba(99, 102, 241, 0.10)",
            "--mc-action-bg": "#4f46e5",
            "--mc-action-bg-hover": "#4338ca",
            "--mc-action-border": "rgba(255, 255, 255, 0.95)",
            "--mc-action-text": "#ffffff",
            "--mc-action-shadow": "0 8px 24px rgba(79, 70, 229, 0.28)",
            "--mc-focus": "rgba(99, 102, 241, 0.24)",
            "--mc-radius": "16px",
            "--mc-control-radius": "10px",
            "--mc-shadow": "0 8px 32px 0 rgba(31, 38, 135, 0.08), inset 0 1px 1px 0 rgba(255, 255, 255, 0.95)",
            "--mc-backdrop": "blur(20px) saturate(180%)",
            "--mc-card-border": "1px solid rgba(255, 255, 255, 0.85)",
            "--mc-tooltip-bg": "rgba(15, 23, 42, 0.88)",
            "--mc-tooltip-text": "#f8fafc",
        },
        "dark": {
            "color-scheme": "dark",
            "--mc-bg": "radial-gradient(at 0% 0%, rgba(99, 102, 241, 0.22) 0px, transparent 55%), radial-gradient(at 100% 0%, rgba(168, 85, 247, 0.18) 0px, transparent 50%), radial-gradient(at 50% 100%, rgba(37, 99, 235, 0.2) 0px, transparent 60%), #0c1017",
            "--mc-surface": "rgba(22, 29, 43, 0.72)",
            "--mc-surface-soft": "rgba(30, 41, 59, 0.55)",
            "--mc-sidebar": "rgba(16, 22, 33, 0.75)",
            "--mc-border": "rgba(255, 255, 255, 0.12)",
            "--mc-border-strong": "rgba(255, 255, 255, 0.20)",
            "--mc-text": "#f3f4f6",
            "--mc-muted": "#9ca3af",
            "--mc-accent": "#818cf8",
            "--mc-accent-dark": "#a5b4fc",
            "--mc-accent-soft": "rgba(129, 140, 248, 0.18)",
            "--mc-action-bg": "#6366f1",
            "--mc-action-bg-hover": "#4f46e5",
            "--mc-action-border": "rgba(255, 255, 255, 0.22)",
            "--mc-action-text": "#ffffff",
            "--mc-action-shadow": "0 8px 24px rgba(99, 102, 241, 0.38)",
            "--mc-focus": "rgba(129, 140, 248, 0.28)",
            "--mc-radius": "16px",
            "--mc-control-radius": "10px",
            "--mc-shadow": "0 10px 40px 0 rgba(0, 0, 0, 0.45), inset 0 1px 1px 0 rgba(255, 255, 255, 0.15)",
            "--mc-backdrop": "blur(22px) saturate(170%)",
            "--mc-card-border": "1px solid rgba(255, 255, 255, 0.12)",
            "--mc-tooltip-bg": "rgba(30, 41, 59, 0.90)",
            "--mc-tooltip-text": "#f8fafc",
        },
    },
    "claude_design": {
        "name": "暖烘陶 · 羊皮书卷风",
        "description": "Anthropic Claude 标志性暖陶与羊皮纸暖白，极度护眼、零蓝光刺激、学术雅致书卷气",
        "light": {
            "color-scheme": "light",
            "--mc-bg": "#f5eedc",
            "--mc-surface": "#fffdf9",
            "--mc-surface-soft": "#ede3ce",
            "--mc-sidebar": "#f4eedd",
            "--mc-border": "#ded2bb",
            "--mc-border-strong": "#c9bc9f",
            "--mc-text": "#28231d",
            "--mc-muted": "#797063",
            "--mc-accent": "#cc785c",
            "--mc-accent-dark": "#b56345",
            "--mc-accent-soft": "rgba(204, 120, 92, 0.09)",
            "--mc-action-bg": "#cc785c",
            "--mc-action-bg-hover": "#b56345",
            "--mc-action-border": "#cc785c",
            "--mc-action-text": "#ffffff",
            "--mc-action-shadow": "0 6px 18px rgba(204, 120, 92, 0.24)",
            "--mc-focus": "rgba(204, 120, 92, 0.24)",
            "--mc-radius": "13px",
            "--mc-control-radius": "9px",
            "--mc-shadow": "0 4px 18px -2px rgba(40, 35, 29, 0.06), 0 1px 3px rgba(40, 35, 29, 0.03)",
            "--mc-backdrop": "none",
            "--mc-card-border": "1px solid var(--mc-border)",
            "--mc-tooltip-bg": "#28231d",
            "--mc-tooltip-text": "#f5eedc",
        },
        "dark": {
            "color-scheme": "dark",
            "--mc-bg": "#191613",
            "--mc-surface": "#24201c",
            "--mc-surface-soft": "#302a24",
            "--mc-sidebar": "#1d1a16",
            "--mc-border": "#3a332a",
            "--mc-border-strong": "#4e4438",
            "--mc-text": "#f4eee5",
            "--mc-muted": "#a89e90",
            "--mc-accent": "#e08866",
            "--mc-accent-dark": "#f29e7e",
            "--mc-accent-soft": "rgba(224, 136, 102, 0.16)",
            "--mc-action-bg": "#cc785c",
            "--mc-action-bg-hover": "#b56345",
            "--mc-action-border": "#e08866",
            "--mc-action-text": "#ffffff",
            "--mc-action-shadow": "0 6px 20px rgba(204, 120, 92, 0.35)",
            "--mc-focus": "rgba(224, 136, 102, 0.26)",
            "--mc-radius": "13px",
            "--mc-control-radius": "9px",
            "--mc-shadow": "0 6px 24px -2px rgba(0, 0, 0, 0.45)",
            "--mc-backdrop": "none",
            "--mc-card-border": "1px solid var(--mc-border)",
            "--mc-tooltip-bg": "#2b2520",
            "--mc-tooltip-text": "#f4eee5",
        },
    },
    "sage": {
        "name": "Forest Eye-Care · 鼠尾草护眼绿",
        "description": "Forest Eye-Care 极致护眼绿，温润舒适，大幅降低长时间阅题疲劳",
        "light": {
            "color-scheme": "light",
            "--mc-bg": "#f3f6f3",
            "--mc-surface": "#ffffff",
            "--mc-surface-soft": "#eaf0eb",
            "--mc-sidebar": "#f7faf7",
            "--mc-border": "#d8e2da",
            "--mc-border-strong": "#b9cbbe",
            "--mc-text": "#182c21",
            "--mc-muted": "#526e5e",
            "--mc-accent": "#2d7a54",
            "--mc-accent-dark": "#215e40",
            "--mc-accent-soft": "rgba(45, 122, 84, 0.09)",
            "--mc-action-bg": "#2d7a54",
            "--mc-action-bg-hover": "#236344",
            "--mc-action-border": "#2d7a54",
            "--mc-action-text": "#ffffff",
            "--mc-action-shadow": "0 6px 18px rgba(45, 122, 84, 0.22)",
            "--mc-focus": "rgba(45, 122, 84, 0.22)",
            "--mc-radius": "14px",
            "--mc-control-radius": "9px",
            "--mc-shadow": "0 4px 20px -2px rgba(24, 44, 33, 0.05), 0 1px 3px rgba(24, 44, 33, 0.03)",
            "--mc-backdrop": "none",
            "--mc-card-border": "1px solid var(--mc-border)",
            "--mc-tooltip-bg": "#13251c",
            "--mc-tooltip-text": "#f3f8f5",
        },
        "dark": {
            "color-scheme": "dark",
            "--mc-bg": "#0d1712",
            "--mc-surface": "#14221b",
            "--mc-surface-soft": "#1b2e24",
            "--mc-sidebar": "#101c15",
            "--mc-border": "#23382c",
            "--mc-border-strong": "#335241",
            "--mc-text": "#e3efe7",
            "--mc-muted": "#8aa897",
            "--mc-accent": "#4ade80",
            "--mc-accent-dark": "#86efac",
            "--mc-accent-soft": "rgba(74, 222, 128, 0.14)",
            "--mc-action-bg": "#2d7a54",
            "--mc-action-bg-hover": "#236344",
            "--mc-action-border": "#4ade80",
            "--mc-action-text": "#ffffff",
            "--mc-action-shadow": "0 6px 18px rgba(74, 222, 128, 0.28)",
            "--mc-focus": "rgba(74, 222, 128, 0.25)",
            "--mc-radius": "14px",
            "--mc-control-radius": "9px",
            "--mc-shadow": "0 6px 24px -2px rgba(0, 0, 0, 0.45)",
            "--mc-backdrop": "none",
            "--mc-card-border": "1px solid var(--mc-border)",
            "--mc-tooltip-bg": "#182b20",
            "--mc-tooltip-text": "#e3efe7",
        },
    },
    "sand": {
        "name": "Cozy Linen · 暖沙燕麦素雅纸感",
        "description": "Cozy Linen 暖木与燕麦纸感，柔和素雅，书卷质感防眩光",
        "light": {
            "color-scheme": "light",
            "--mc-bg": "#f8f6f2",
            "--mc-surface": "#ffffff",
            "--mc-surface-soft": "#f1ede5",
            "--mc-sidebar": "#faf9f6",
            "--mc-border": "#e6e0d6",
            "--mc-border-strong": "#d3c8b9",
            "--mc-text": "#2c2622",
            "--mc-muted": "#786e66",
            "--mc-accent": "#b45309",
            "--mc-accent-dark": "#92400e",
            "--mc-accent-soft": "rgba(180, 83, 9, 0.08)",
            "--mc-action-bg": "#b45309",
            "--mc-action-bg-hover": "#92400e",
            "--mc-action-border": "#b45309",
            "--mc-action-text": "#ffffff",
            "--mc-action-shadow": "0 6px 18px rgba(180, 83, 9, 0.22)",
            "--mc-focus": "rgba(180, 83, 9, 0.22)",
            "--mc-radius": "14px",
            "--mc-control-radius": "9px",
            "--mc-shadow": "0 4px 20px -2px rgba(44, 38, 34, 0.05), 0 1px 3px rgba(44, 38, 34, 0.03)",
            "--mc-backdrop": "none",
            "--mc-card-border": "1px solid var(--mc-border)",
            "--mc-tooltip-bg": "#29231e",
            "--mc-tooltip-text": "#faf6f0",
        },
        "dark": {
            "color-scheme": "dark",
            "--mc-bg": "#171412",
            "--mc-surface": "#211c19",
            "--mc-surface-soft": "#2c2622",
            "--mc-sidebar": "#1b1715",
            "--mc-border": "#39312b",
            "--mc-border-strong": "#4e433b",
            "--mc-text": "#f5f0ea",
            "--mc-muted": "#aaa096",
            "--mc-accent": "#fbbf24",
            "--mc-accent-dark": "#fde68a",
            "--mc-accent-soft": "rgba(251, 191, 36, 0.14)",
            "--mc-action-bg": "#c26a27",
            "--mc-action-bg-hover": "#a7561b",
            "--mc-action-border": "#fbbf24",
            "--mc-action-text": "#ffffff",
            "--mc-action-shadow": "0 6px 18px rgba(194, 106, 39, 0.3)",
            "--mc-focus": "rgba(251, 191, 36, 0.25)",
            "--mc-radius": "14px",
            "--mc-control-radius": "9px",
            "--mc-shadow": "0 6px 24px -2px rgba(0, 0, 0, 0.45)",
            "--mc-backdrop": "none",
            "--mc-card-border": "1px solid var(--mc-border)",
            "--mc-tooltip-bg": "#2a2420",
            "--mc-tooltip-text": "#f5f0ea",
        },
    },
}
# 别名映射兼容
THEME_SKIN_PALETTES["indigo"] = THEME_SKIN_PALETTES["praetorius"]
THEME_SKIN_PALETTES["glass"] = THEME_SKIN_PALETTES["ghani_glass"]
THEME_SKIN_PALETTES["slate"] = THEME_SKIN_PALETTES["praetorius"]
THEME_SKIN_PALETTES["claude"] = THEME_SKIN_PALETTES["claude_design"]

def _dict_to_css_vars(vmap: dict) -> str:
    lines = []
    for k, v in vmap.items():
        lines.append(f"            {k}: {v};")
    return "\n".join(lines)

from textwrap import dedent as _dedent

_MC_QUESTION_BANK_STYLES = _dedent("""
        <style>

        /* 侧边栏品牌区域与排版 */
        .mc-sidebar-brand-box {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            padding: 0.2rem 0 1rem;
            margin-bottom: 0.6rem;
            border-bottom: 1px solid var(--mc-border);
            text-align: center;
        }
        .mc-sidebar-avatar {
            width: 48px;
            height: 48px;
            border-radius: 12px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
            margin-bottom: 8px;
            object-fit: cover;
        }
        .mc-sidebar-brand-title {
            font-size: 15.5px;
            font-weight: 750;
            color: var(--mc-text);
            letter-spacing: -0.01em;
            line-height: 1.3;
        }
        .mc-sidebar-brand-sub {
            font-size: 11.5px;
            color: var(--mc-muted);
            margin-top: 2px;
        }
        .sol-logo, .sol-logo-link {
            display: none !important;
        }

        /* 侧边栏导航单选框视觉升级 */
        [data-testid="stSidebar"] div[role="radiogroup"] {
            display: flex !important;
            flex-direction: column !important;
            align-items: flex-start !important;
            gap: 4px !important;
            width: 100% !important;
            padding: 0 !important;
            margin: 0 !important;
        }
        [data-testid="stSidebar"] div[data-testid="stRadio"] > div {
            width: 100% !important;
            padding: 0 !important;
        }
        /* Streamlit 默认给侧栏内容 24px 左右内边距，把可用宽度压到 112px；收紧到 8px */
        [data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {
            padding-left: 8px !important;
            padding-right: 8px !important;
            padding-top: 0.4rem !important;
        }

        /* 侧栏内部各层容器强制铺满宽度（否则会被 flex 压成内容宽 112px） */
        [data-testid="stSidebar"] [data-testid="stSidebarContent"],
        [data-testid="stSidebar"] [data-testid="stSidebarUserContent"],
        [data-testid="stSidebar"] div[data-testid="stVerticalBlock"],
        [data-testid="stSidebar"] div[data-testid="stVerticalBlockBorderWrapper"],
        [data-testid="stSidebar"] div[data-testid="stElementContainer"],
        [data-testid="stSidebar"] div[data-testid="stRadio"],
        [data-testid="stSidebar"] div[data-testid="stRadio"] > div,
        [data-testid="stSidebar"] div[role="radiogroup"] {
            width: 100% !important;
            max-width: 100% !important;
            align-items: stretch !important;
        }

        /* 侧栏排版对齐「复盘软件」：整行功能项（图标 + 单行名称），折叠态由 rail 规则接管 */
        [data-testid="stSidebar"][aria-expanded="true"] div[role="radiogroup"] > label {
            position: relative !important;
            display: flex !important;
            flex-direction: row !important;
            align-items: center !important;
            justify-content: flex-start !important;
            width: 100% !important;
            min-width: 0 !important;
            max-width: 100% !important;
            min-height: 40px !important;
            margin: 1px 0 !important;
            padding: 0 11px !important;
            border-radius: 12px !important;
            border: 1px solid transparent !important;
            background: transparent !important;
            cursor: pointer !important;
            gap: 9px !important;
            transition: all 0.18s cubic-bezier(0.4, 0, 0.2, 1) !important;
        }
        [data-testid="stSidebar"] div[role="radiogroup"] > label input,
        [data-testid="stSidebar"] div[role="radiogroup"] > label > div:first-of-type,
        [data-testid="stSidebar"] div[role="radiogroup"] > label > div:not(:last-child),
        [data-testid="stSidebar"] div[role="radiogroup"] > label div[class*="radio"],
        [data-testid="stSidebar"] div[role="radiogroup"] > label svg {
            display: none !important;
            width: 0 !important;
            height: 0 !important;
            opacity: 0 !important;
            position: absolute !important;
            pointer-events: none !important;
        }
        [data-testid="stSidebar"] div[role="radiogroup"] > label > div:last-child {
            display: flex !important;
            align-items: center !important;
            justify-content: flex-start !important;
            width: 100% !important;
            height: 100% !important;
        }
        [data-testid="stSidebar"] div[role="radiogroup"] > label p {
            display: flex !important;
            align-items: center !important;
            gap: 8px !important;
            margin: 0 !important;
            padding: 0 !important;
            text-align: left !important;
            font-size: 12.8px !important;
            line-height: 1.3 !important;
            font-weight: 640 !important;
            color: var(--mc-text) !important;
            white-space: nowrap !important;
            pointer-events: none !important;
            letter-spacing: -0.005em !important;
        }
        [data-testid="stSidebar"] div[role="radiogroup"] > label:hover {
            background: var(--mc-accent-soft) !important;
            border-color: var(--mc-border) !important;
            transform: translateX(2px) !important;
        }
        [data-testid="stSidebar"] div[role="radiogroup"] > label:hover p {
            color: var(--mc-accent-dark) !important;
        }
        /* 选中态：深墨胶囊（与复盘软件一致） */
        [data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) {
            background: #26231E !important;
            color: #FFFFFF !important;
            box-shadow: 0 4px 14px rgba(38, 35, 30, 0.22) !important;
            border-color: #26231E !important;
            border-radius: 12px !important;
            transform: translateX(0) !important;
        }
        [data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) p {
            color: #FFFFFF !important;
            font-weight: 750 !important;
        }
        /* 二级（录入模式）缩进与竖线，选中同样深墨 */
        [data-testid="stSidebar"][aria-expanded="true"] div[role="radiogroup"][aria-label="录入模式"] > label {
            min-height: 32px !important;
            padding: 0 9px !important;
            border-radius: 9px !important;
        }
        [data-testid="stSidebar"][aria-expanded="true"] div[role="radiogroup"][aria-label="录入模式"] > label p {
            font-size: 12.2px !important;
            font-weight: 560 !important;
        }
        [data-testid="stSidebar"][aria-expanded="true"] div[role="radiogroup"][aria-label="录入模式"] > label:has(input:checked) {
            border-radius: 9px !important;
            box-shadow: 0 3px 10px rgba(38, 35, 30, 0.20) !important;
        }

        /* 状态胶囊 */
        .mc-sidebar-status {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            padding: 6px 12px;
            margin-top: 14px;
            margin-bottom: 8px;
            border-radius: 9999px;
            background: rgba(16, 185, 129, 0.12);
            color: #059669;
            font-size: 12px;
            font-weight: 650;
            border: 1px solid rgba(16, 185, 129, 0.25);
        }

        /* 题目编辑与预览专用高级容器 */
        div[data-testid="stVerticalBlockBorderWrapper"]:has(.mc-question-actions-grid-anchor) {
            border: 0 !important;
            background: transparent !important;
            box-shadow: none !important;
            padding: 0 !important;
            margin-top: 0.62rem !important;
        }
        div[data-testid="stMarkdownContainer"]:has(> .mc-question-preview-anchor) {
            display: none !important;
        }
        div[data-testid="stMarkdownContainer"]:has(> .mc-question-actions-grid-anchor),
        div[data-testid="stVerticalBlockBorderWrapper"]:has(.mc-question-actions-grid-anchor) div[data-testid="stElementContainer"]:has(.mc-question-actions-grid-anchor) {
            display: none !important;
        }
        div[data-testid="stVerticalBlockBorderWrapper"]:has(.mc-question-actions-grid-anchor) div[data-testid="stButton"] {
            width: 100% !important;
            min-width: 0 !important;
        }
        div[data-testid="stVerticalBlockBorderWrapper"]:has(.mc-question-actions-grid-anchor) div[data-testid="stButton"] > button {
            width: 100% !important;
            min-height: 2.45rem !important;
            justify-content: center !important;
            white-space: normal !important;
        }

        /* 侧边栏折叠时的主内容区域自适应 */
        [data-testid="collapsedControl"],
        [data-testid="stSidebarCollapsedControl"] {
            top: 14px !important;
            left: 14px !important;
            z-index: 10001 !important;
            visibility: visible !important;
        }

        /* 隐藏顶部导航遗留的水平条 */
        div[data-testid="stHorizontalBlock"]:has(#mc-top-nav-anchor) {
            display: none !important;
        }

        @media (prefers-reduced-motion: reduce) {
            *, *::before, *::after {
                scroll-behavior: auto !important;
                transition-duration: 0.01ms !important;
                animation-duration: 0.01ms !important;
            }
        }
        </style>
        """)


def inject_unified_visual_system_css():
    """一次性注入暖陶主题 + 题库组件样式（合并为单个 <style>，避免重复样式块）。"""
    theme_css = generate_theme_css(_get_current_theme_skin(), _get_current_theme_mode())
    st.markdown(theme_css + _MC_QUESTION_BANK_STYLES, unsafe_allow_html=True)

@st.dialog("🔍 查看大图", width="large")
def zoom_image(img):
    # 将图片转换为 Base64
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode()

    # HTML/JS 缩放组件 (增大可视高度)
    html_code = f"""
    <div style="width: 100%; height: auto; min-height: 400px; overflow: visible; position: relative; display: flex; justify-content: center; align-items: flex-start; background: transparent;">
        <div id="img-container" style="transition: transform 0.1s; cursor: grab; width: 100%; display: flex; justify-content: center;">
            <img id="zoomed-img" src="data:image/png;base64,{img_str}" style="max-width: 100%; height: auto; display: block;">
        </div>
        <div style="position: fixed; top: 15px; left: 15px; background: rgba(0,0,0,0.65); padding: 35px 25px; border-radius: 35px; display: flex; align-items: center; gap: 20px; z-index: 9999; box-shadow: 0 4px 6px rgba(0,0,0,0.3);">
            <button onclick="zoomOut()" style="background: transparent; border: none; color: white; font-size: 35px; cursor: pointer; display: flex; align-items: center; justify-content: center; padding: 0;">➖</button>
            <span id="zoom-level" style="color: white; font-family: sans-serif; font-size: 18px; font-weight: bold; min-width: 60px; text-align: center; margin: 0;">100%</span>
            <button onclick="zoomIn()" style="background: transparent; border: none; color: white; font-size: 35px; cursor: pointer; display: flex; align-items: center; justify-content: center; padding: 0;">➕</button>
            <button onclick="resetZoom()" style="background: transparent; border: none; color: white; font-size: 20px; cursor: pointer; margin-left: 5px; display: flex; align-items: center; justify-content: center; padding: 0;">🔄</button>
        </div>
    </div>
    <script>
        let scale = 1;
        let pX = 0;
        let pY = 0;
        const container = document.getElementById('img-container');
        const zoomLevel = document.getElementById('zoom-level');

        function updateTransform() {{
            container.style.transform = `translate(${{pX}}px, ${{pY}}px) scale(${{scale}})`;
            zoomLevel.innerText = Math.round(scale * 100) + '%';
        }}

        function zoomIn() {{
            scale *= 1.2;
            updateTransform();
        }}

        function zoomOut() {{
            scale /= 1.2;
            updateTransform();
        }}

        function resetZoom() {{
            scale = 1;
            pX = 0;
            pY = 0;
            updateTransform();
        }}

        // 滚轮缩放
        document.querySelector('div').addEventListener('wheel', (e) => {{
            e.preventDefault();
            if (e.deltaY < 0) {{
                scale *= 1.1;
            }} else {{
                scale /= 1.1;
            }}
            updateTransform();
        }});

        // 简单的拖拽逻辑
        let isDragging = false;
        let startX, startY, initialPx, initialPy;

        container.addEventListener('mousedown', (e) => {{
            isDragging = true;
            startX = e.clientX;
            startY = e.clientY;
            initialPx = pX;
            initialPy = pY;
            container.style.cursor = 'grabbing';
            e.preventDefault();
        }});

        window.addEventListener('mousemove', (e) => {{
            if (!isDragging) return;
            const dx = e.clientX - startX;
            const dy = e.clientY - startY;
            pX = initialPx + dx;
            pY = initialPy + dy;
            updateTransform();
        }});

        window.addEventListener('mouseup', () => {{
            isDragging = false;
            container.style.cursor = 'grab';
        }});
    </script>
    """
    components.html(html_code, height=800, scrolling=True)

@st.dialog("乌托邦高中物理题库助手 · UtopiaPhyCyclus", width="large")
def show_utopiaphycyclus_intro():
    page_system_intro()

def _adv_search_queries_from_session():
    t1 = st.session_state.get("adv_t1", "全文内容")
    t2 = st.session_state.get("adv_t2", "全文内容")
    t3 = st.session_state.get("adv_t3", "全文内容")

    q1 = st.session_state.get("adv_q1_sel" if t1 == "题目类型" else "adv_q1", "")
    q2 = st.session_state.get("adv_q2_sel" if t2 == "题目类型" else "adv_q2", "")
    q3 = st.session_state.get("adv_q3_sel" if t3 == "题目类型" else "adv_q3", "")
    return (q1 or ""), (q2 or ""), (q3 or "")

def _adv_search_has_query():
    q1, q2, q3 = _adv_search_queries_from_session()
    search_mode = st.session_state.get("adv_search_mode", "精确筛选")
    semantic_query = st.session_state.get("adv_semantic_query", "") if search_mode != "精确筛选" else ""
    return bool(str(q1).strip() or str(q2).strip() or str(q3).strip() or str(semantic_query).strip())


def _semantic_api_config() -> dict:
    config = _read_root_ai_env_config()
    return {
        "base_url": config.get("AI_BASE_URL", ""),
        "api_key": config.get("AI_API_KEY", ""),
        "model_name": config.get("AI_EMBEDDING_MODEL_NAME", ""),
    }


def _semantic_lexical_boost(text: str, query: str) -> float:
    """Small lexical boost keeps exact terminology ahead of merely related text."""
    haystack = (text or "").casefold()
    needle = (query or "").strip().casefold()
    if not needle:
        return 0.0
    score = 0.25 if needle in haystack else 0.0
    tokens = re.findall(r"[A-Za-z0-9_]+", needle)
    for chinese_run in re.findall(r"[\u4e00-\u9fff]{2,}", needle):
        tokens.extend(chinese_run[index:index + 2] for index in range(len(chinese_run) - 1))
    if tokens:
        matched_ratio = sum(token in haystack for token in tokens) / len(tokens)
        score += 0.2 * matched_ratio
    return score


def _semantic_item_text(item: dict) -> str:
    return "\n".join([
        item["full_text"],
        item["type"],
        item["file"],
        item["row"].get("知识板块", "") or "",
    ])


def _invalidate_semantic_for_file(file_path: str) -> None:
    try:
        relative_path = os.path.relpath(file_path, CHAPTERS_DIR)
        invalidate_semantic_path(relative_path)
    except Exception:
        # Semantic search is optional and must never block a normal save/delete.
        pass

def _clear_advanced_search_result_cache():
    st.session_state.pop("adv_last_query", None)
    st.session_state.pop("adv_last_results", None)

def _duplicate_save_confirmation(file_path: str, content: str, scope: str = "edit") -> bool:
    """Require a second click before an edit creates a duplicate statement."""
    matches = find_duplicate_matches(
        content,
        exclude_path=file_path,
        similarity_threshold=0.88,
        max_results=5,
    )
    key_hash = hashlib.md5(f"{scope}:{file_path}".encode("utf-8", errors="ignore")).hexdigest()[:12]
    state_key = f"duplicate_save_confirmation_{key_hash}"
    fingerprint = question_fingerprint(content)
    if not matches:
        st.session_state.pop(state_key, None)
        return True

    pending = st.session_state.get(state_key) or {}
    if pending.get("fingerprint") != fingerprint:
        st.session_state[state_key] = {"fingerprint": fingerprint, "matches": matches}
        first_match = matches[0]
        st.toast(
            f"检测到可能重复题目：{first_match.get('name') or first_match.get('relative_path')}；再次点击保存以确认",
            icon="⚠️",
        )
        return False

    st.session_state.pop(state_key, None)
    return True

def save_modified_tex_file(file_path, new_content):
    """
    保存修改后的 tex 文件：
    将修改后的内容通过 extract_and_replace_tikz 处理（保留内联 TikZ 并在后台生成副本），然后保存。
    """
    save_dir = os.path.dirname(file_path)
    filename = os.path.basename(file_path)

    # 提取并生成独立文件副本，但 final_content 仍包含原生 TikZ
    final_content = extract_and_replace_tikz(new_content, filename, save_dir)

    # 直接写入包含原生 TikZ 的内容
    atomic_write_text(file_path, final_content, backup=True)
    record_operation(
        "update_question_content",
        path=os.path.abspath(file_path),
        details="LaTeX content updated",
    )
    _invalidate_semantic_for_file(file_path)

    return final_content

def _norm_abs_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(path or ""))

def _is_managed_question_file(file_path: str) -> bool:
    abs_path = os.path.abspath(file_path or "")
    chapters_abs = os.path.abspath(CHAPTERS_DIR)
    try:
        if os.path.commonpath([chapters_abs, abs_path]) != chapters_abs:
            return False
    except ValueError:
        return False

    rel_path = os.path.relpath(abs_path, chapters_abs)
    rel_parts = os.path.normpath(rel_path).split(os.sep)
    basename = os.path.basename(abs_path)

    return (
        os.path.isfile(abs_path)
        and basename.endswith(".tex")
        and not basename.startswith("content_")
        and not any("相关图" in part for part in rel_parts)
        and " 图" not in basename
    )

def _remove_question_from_csv_index(file_path: str) -> int:
    from utils.csv_ops import read_csv_index, write_csv_index

    abs_path = os.path.abspath(file_path)
    rel_path = os.path.relpath(abs_path, CHAPTERS_DIR)
    target_rel = os.path.normcase(os.path.normpath(rel_path))
    target_name = os.path.basename(abs_path).replace(".tex", "")

    rows = read_csv_index()
    kept_rows = []
    removed_count = 0
    for row in rows:
        row_rel = os.path.normcase(os.path.normpath(row.get("相对文件路径", "") or ""))
        row_name = (row.get("文件名称", "") or "").strip()
        if row_rel == target_rel or (not row_rel and row_name == target_name):
            removed_count += 1
            continue
        kept_rows.append(row)

    if removed_count:
        write_csv_index(kept_rows)
    return removed_count

def _get_question_csv_rows(file_path: str):
    from utils.csv_ops import read_csv_index

    abs_path = os.path.abspath(file_path)
    rel_path = os.path.relpath(abs_path, CHAPTERS_DIR)
    target_rel = os.path.normcase(os.path.normpath(rel_path))
    target_name = os.path.basename(abs_path).replace(".tex", "")

    matched_rows = []
    for row in read_csv_index():
        row_rel = os.path.normcase(os.path.normpath(row.get("相对文件路径", "") or ""))
        row_name = (row.get("文件名称", "") or "").strip()
        if row_rel == target_rel or (not row_rel and row_name == target_name):
            matched_rows.append(dict(row))
    return matched_rows

def _csv_has_question_record(file_path: str) -> bool:
    return bool(_get_question_csv_rows(file_path))

def _forget_deleted_question_path(file_path: str):
    target = _norm_abs_path(file_path)

    for key in ("adv_last_results",):
        rows = st.session_state.get(key)
        if isinstance(rows, list):
            st.session_state[key] = [
                row for row in rows
                if _norm_abs_path(row.get("path") if isinstance(row, dict) else row) != target
            ]

    for key in ("exam_selected_qs", "recent_saved_paths"):
        paths = st.session_state.get(key)
        if isinstance(paths, list):
            st.session_state[key] = [p for p in paths if _norm_abs_path(p) != target]

def _related_tikz_dir_for_question(file_path: str) -> str:
    base_name = os.path.basename(file_path).replace(".tex", "")
    return os.path.join(os.path.dirname(file_path), f"{base_name} 相关图")

def _backup_related_tikz_dir(file_path: str) -> str:
    tikz_dir = _related_tikz_dir_for_question(file_path)
    if not os.path.isdir(tikz_dir):
        return ""

    root_dir = os.getcwd()
    try:
        rel_path = os.path.relpath(os.path.abspath(tikz_dir), root_dir)
        if rel_path.startswith(".."):
            rel_path = os.path.basename(tikz_dir)
    except ValueError:
        rel_path = os.path.basename(tikz_dir)

    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_dir = os.path.join(root_dir, ".backups", rel_path + f".{timestamp}")
    shutil.copytree(tikz_dir, backup_dir)
    return backup_dir

def _remove_related_tikz_dir(file_path: str):
    tikz_dir = _related_tikz_dir_for_question(file_path)
    if os.path.isdir(tikz_dir):
        shutil.rmtree(tikz_dir)

def _update_chapter_indexes_from_ui():
    try:
        import utils.batch_gen as batch_gen
        batch_gen.update_chapter_contents()
    except Exception as e:
        st.toast(f"章节索引更新失败：{e}", icon="⚠️")


def _rebuild_csv_index_from_files():
    """删除题目后按磁盘现状全量重建 CSV，避免遗留孤立索引记录。"""
    import runpy

    init_script = os.path.join(BASE_DIR, "utils", "init_csv_index.py")
    runpy.run_path(init_script, run_name="__main__")
    _clear_advanced_search_result_cache()
    clear_statistics_cache()

def _remember_deleted_question(record: dict):
    history = st.session_state.setdefault("delete_mode_deleted_records", [])
    history.insert(0, record)

def delete_question_file_and_sync(file_path: str):
    if not _is_managed_question_file(file_path):
        raise ValueError("只能删除 chapters 目录下的普通题目 .tex 文件。")

    abs_path = os.path.abspath(file_path)
    with open(abs_path, "r", encoding="utf-8") as f:
        original_content = f.read()
    original_csv_rows = _get_question_csv_rows(abs_path)
    q_label = format_question_title(os.path.basename(abs_path))
    backup_path = backup_existing_file(abs_path)
    tikz_backup_path = _backup_related_tikz_dir(abs_path)
    try:
        os.remove(abs_path)
        removed_rows = _remove_question_from_csv_index(abs_path)
    except Exception:
        if backup_path and os.path.exists(backup_path) and not os.path.exists(abs_path):
            ensure_dir(os.path.dirname(abs_path))
            shutil.copy2(backup_path, abs_path)
        raise

    try:
        _remove_related_tikz_dir(abs_path)
    except Exception as e:
        st.toast(f"题目已删除，但相关图目录未能删除：{e}", icon="⚠️")
    _forget_deleted_question_path(abs_path)
    _invalidate_semantic_for_file(abs_path)
    _clear_advanced_search_result_cache()
    clear_statistics_cache()

    _update_chapter_indexes_from_ui()
    try:
        _rebuild_csv_index_from_files()
    except Exception as e:
        st.toast(f"题目已删除，但重建题库索引失败：{e}", icon="⚠️")

    _remember_deleted_question({
        "id": hashlib.md5(f"{abs_path}:{time.time()}".encode("utf-8")).hexdigest()[:12],
        "label": q_label,
        "original_path": abs_path,
        "backup_path": backup_path,
        "tikz_backup_path": tikz_backup_path,
        "content": original_content,
        "csv_rows": original_csv_rows,
        "deleted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "restored": False,
    })

    question_id = ""
    if original_csv_rows:
        question_id = str(next(iter(original_csv_rows[0].values()), "") or "")
    record_operation(
        "delete_question",
        path=abs_path,
        question_id=question_id,
        details=f"removed_index_rows={removed_rows}",
    )

    return backup_path or tikz_backup_path, removed_rows

def restore_deleted_question_and_sync(record: dict):
    original_path = record.get("original_path") or ""
    backup_path = record.get("backup_path") or ""
    tikz_backup_path = record.get("tikz_backup_path") or ""

    if not original_path or not backup_path or not os.path.exists(backup_path):
        raise FileNotFoundError("未找到该题的备份文件，无法恢复。")
    if os.path.exists(original_path):
        raise FileExistsError("原位置已经存在同名题目文件，请先检查题库目录。")

    from utils.csv_ops import read_csv_index, write_csv_index
    csv_rows = record.get("csv_rows") or []
    if csv_rows:
        data = read_csv_index()
        existing_rels = {
            os.path.normcase(os.path.normpath(row.get("相对文件路径", "") or ""))
            for row in data
        }
        added_any = False
        for row in csv_rows:
            rel_path = os.path.normcase(os.path.normpath(row.get("相对文件路径", "") or ""))
            if rel_path not in existing_rels:
                data.append(row)
                existing_rels.add(rel_path)
                added_any = True
        if added_any:
            write_csv_index(data)
    else:
        basename = os.path.basename(original_path).replace(".tex", "")
        parts = basename.split("-")
        if len(parts) >= 5 and not _csv_has_question_record(original_path):
            with open(backup_path, "r", encoding="utf-8") as f:
                restored_content = f.read()
            add_to_csv_index(original_path, restored_content, parts[0], parts[1], parts[2], parts[3], parts[4])

    ensure_dir(os.path.dirname(original_path))
    shutil.copy2(backup_path, original_path)

    if tikz_backup_path and os.path.isdir(tikz_backup_path):
        base_name = os.path.basename(original_path).replace(".tex", "")
        tikz_target = os.path.join(os.path.dirname(original_path), f"{base_name} 相关图")
        if not os.path.exists(tikz_target):
            shutil.copytree(tikz_backup_path, tikz_target)

    record["restored"] = True
    record_operation(
        "restore_question",
        path=os.path.abspath(original_path),
        related_path=os.path.abspath(backup_path),
        details="question and related assets restored",
    )
    _clear_advanced_search_result_cache()
    clear_statistics_cache()
    _update_chapter_indexes_from_ui()

def _backup_root_dir() -> str:
    return os.path.join(BASE_DIR, ".backups")

def _strip_backup_timestamp(filename: str) -> str:
    stem, ext = os.path.splitext(filename)
    stem = re.sub(r"\.\d{8}-\d{6}(?:-\d{6})?$", "", stem)
    return stem + (ext or ".tex")

def _backup_original_path(backup_path: str) -> str:
    rel_path = os.path.relpath(os.path.abspath(backup_path), _backup_root_dir())
    rel_dir = os.path.dirname(rel_path)
    original_filename = _strip_backup_timestamp(os.path.basename(backup_path))
    return os.path.join(BASE_DIR, rel_dir, original_filename)

def _backup_deleted_at(backup_path: str) -> str:
    stem = os.path.splitext(os.path.basename(backup_path))[0]
    match = re.search(r"\.(\d{8})-(\d{6})(?:-(\d{6}))?$", stem)
    if not match:
        return ""
    date_s, time_s, micro_s = match.groups()
    try:
        dt = datetime.datetime.strptime(date_s + time_s, "%Y%m%d%H%M%S")
        if micro_s:
            dt = dt.replace(microsecond=int(micro_s))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""

def _find_tikz_backup_for_question_backup(backup_path: str, original_path: str) -> str:
    root = _backup_root_dir()
    base_name = os.path.basename(original_path).replace(".tex", "")
    rel_original_dir = os.path.relpath(os.path.dirname(original_path), BASE_DIR)
    backup_parent = os.path.join(root, rel_original_dir)
    if not os.path.isdir(backup_parent):
        return ""

    candidates = []
    prefix = f"{base_name} 相关图."
    for name in os.listdir(backup_parent):
        full_path = os.path.join(backup_parent, name)
        if os.path.isdir(full_path) and name.startswith(prefix):
            candidates.append(full_path)
    if not candidates:
        return ""
    return max(candidates, key=lambda p: os.path.getmtime(p))

def scan_question_backup_records():
    root = _backup_root_dir()
    chapters_backup = os.path.join(root, "chapters")
    if not os.path.isdir(chapters_backup):
        return []

    records = []
    for walk_root, dirs, files in os.walk(chapters_backup):
        dirs[:] = [d for d in dirs if "相关图" not in d]
        for filename in files:
            if not filename.endswith(".tex"):
                continue
            if filename.startswith("content_") or " 图" in filename:
                continue
            backup_path = os.path.join(walk_root, filename)
            original_path = _backup_original_path(backup_path)
            original_filename = os.path.basename(original_path)
            try:
                with open(backup_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                content = ""

            records.append({
                "id": hashlib.md5(backup_path.encode("utf-8")).hexdigest()[:12],
                "label": format_question_title(original_filename),
                "original_path": original_path,
                "backup_path": backup_path,
                "tikz_backup_path": _find_tikz_backup_for_question_backup(backup_path, original_path),
                "content": content,
                "csv_rows": [],
                "deleted_at": _backup_deleted_at(backup_path),
                "restored": os.path.exists(original_path),
            })

    records.sort(key=lambda rec: os.path.getmtime(rec["backup_path"]) if os.path.exists(rec["backup_path"]) else 0, reverse=True)
    return records

def permanently_delete_backup_record(record: dict):
    backup_path = record.get("backup_path") or ""
    tikz_backup_path = record.get("tikz_backup_path") or ""

    root = os.path.abspath(_backup_root_dir())
    targets = []
    if backup_path and os.path.isfile(backup_path):
        targets.append(os.path.abspath(backup_path))
    if tikz_backup_path and os.path.isdir(tikz_backup_path):
        targets.append(os.path.abspath(tikz_backup_path))

    for target in targets:
        try:
            if os.path.commonpath([root, target]) != root:
                raise ValueError("备份路径不在 .backups 目录内，已拒绝删除。")
        except ValueError:
            raise ValueError("备份路径不在 .backups 目录内，已拒绝删除。")

    for target in targets:
        if os.path.isdir(target):
            shutil.rmtree(target)
        elif os.path.isfile(target):
            os.remove(target)

    record_operation(
        "permanently_delete_backup",
        path=backup_path,
        related_path=tikz_backup_path,
        details="backup removed permanently",
    )

def clear_all_question_backups():
    records = scan_question_backup_records()
    deleted_count = 0
    for record in records:
        permanently_delete_backup_record(record)
        deleted_count += 1

    chapters_backup = os.path.join(_backup_root_dir(), "chapters")
    if os.path.isdir(chapters_backup):
        for walk_root, dirs, files in os.walk(chapters_backup, topdown=False):
            for dirname in dirs:
                full_path = os.path.join(walk_root, dirname)
                if "相关图." in dirname and os.path.isdir(full_path):
                    shutil.rmtree(full_path)
            if walk_root != chapters_backup and not os.listdir(walk_root):
                os.rmdir(walk_root)
    return deleted_count

@st.dialog("恢复误删题目", width="large")
def restore_deleted_questions_dialog():
    records = st.session_state.get("delete_mode_deleted_records", [])

    c_title, c_exit = st.columns([4, 1], vertical_alignment="center")
    with c_title:
        st.caption("这里显示本次删除模式中删除、且尚未恢复的题目。恢复会复制备份回原路径，并同步 CSV 索引和章节索引。")
    with c_exit:
        st.markdown('<span class="delete-exit-btn-hook"></span>', unsafe_allow_html=True)
        with _compat_container(key="restore_deleted_close_wrap"):
            if st.button("退出恢复界面", key="restore_deleted_close", use_container_width=True):
                st.rerun()

    active_records = [
        rec for rec in records
        if not rec.get("restored") and rec.get("backup_path") and os.path.exists(rec.get("backup_path"))
    ]

    if not active_records:
        st.info("本次删除模式中暂无可恢复的误删题目。")
        return

    for idx, rec in enumerate(active_records):
        q_label = rec.get("label") or format_question_title(os.path.basename(rec.get("original_path", "")))
        content = rec.get("content", "")
        extra_label = ""
        if rec.get("deleted_at"):
            extra_label = f"<span style='font-size:0.5em; color:gray; font-weight:normal; margin-left: 10px;'>删除于 {html.escape(rec.get('deleted_at'))}</span>"

        render_static_question_header(q_label, content, rec.get("original_path", ""), extra_html_label=extra_label)
        try:
            st.markdown(latex_to_markdown(content, show_title=False), unsafe_allow_html=True)
        except Exception as e:
            st.error(f"渲染错误: {e}")

        st.markdown('<span class="blue-restore-btn-hook"></span>', unsafe_allow_html=True)
        restore_key = f"restore_deleted_{rec.get('id', idx)}"
        original_exists = os.path.exists(rec.get("original_path", ""))
        restore_label = "原位置已有同名题" if original_exists else "↩️ 恢复该题"
        with _compat_container(key=f"restore_deleted_btn_wrap_{rec.get('id', idx)}"):
            if st.button(
                restore_label,
                key=restore_key,
                type="primary",
                use_container_width=True,
                disabled=original_exists,
            ):
                try:
                    restore_deleted_question_and_sync(rec)
                    st.toast(f"已恢复 {q_label}", icon="✅")
                    st.rerun()
                except Exception as e:
                    st.error(f"恢复失败: {_format_file_write_error(e)}")
        st.divider()

@st.dialog("管理备份问题", width="large")
def manage_backup_questions_dialog():
    records = scan_question_backup_records()

    c_search, c_clear, c_exit = st.columns([3.1, 1.35, 1.05], vertical_alignment="bottom")
    with c_search:
        query = st.text_input(
            "查找备份题目",
            placeholder="输入题号、试卷名、知识板块或题干关键词...",
            key="backup_manager_query",
            label_visibility="collapsed",
        )
    with c_clear:
        st.markdown('<span class="backup-manage-btn-hook"></span>', unsafe_allow_html=True)
        with _compat_container(key="backup_manager_clear_all_wrap"):
            if st.button("清除所有备份问题", key="backup_manager_clear_all", use_container_width=True):
                st.session_state["backup_manager_confirm_clear"] = True
    with c_exit:
        st.markdown('<span class="delete-exit-btn-hook"></span>', unsafe_allow_html=True)
        with _compat_container(key="backup_manager_close_wrap"):
            if st.button("退出备份管理界面", key="backup_manager_close", use_container_width=True):
                st.session_state["backup_manager_confirm_clear"] = False
                st.rerun()

    if st.session_state.get("backup_manager_confirm_clear"):
        st.warning("确认永久删除所有题目备份吗？该操作不会进入回收站，也无法通过本系统恢复。")
        c_ok, c_cancel, _ = st.columns([1, 1, 3])
        with c_ok:
            st.markdown('<span class="red-btn-hook"></span>', unsafe_allow_html=True)
            with _compat_container(key="backup_manager_clear_ok_wrap"):
                if st.button("确认清除", key="backup_manager_clear_ok", type="primary", use_container_width=True):
                    try:
                        count = clear_all_question_backups()
                        st.session_state["backup_manager_confirm_clear"] = False
                        st.toast(f"已清除 {count} 条题目备份。", icon="✅")
                        st.rerun()
                    except Exception as e:
                        st.error(f"清除失败: {_format_file_write_error(e)}")
        with c_cancel:
            if st.button("取消", key="backup_manager_clear_cancel", use_container_width=True):
                st.session_state["backup_manager_confirm_clear"] = False
                st.rerun()

    if not records:
        st.info("当前没有可管理的题目备份。")
        return

    q = (query or "").strip()
    if q:
        records = [
            rec for rec in records
            if q in (rec.get("label", "") or "")
            or q in (rec.get("content", "") or "")
            or q in (rec.get("original_path", "") or "")
            or q in (rec.get("backup_path", "") or "")
            or q in (rec.get("deleted_at", "") or "")
        ]

    st.caption(f"当前显示 {len(records)} 条题目备份。恢复会复制备份回原路径；永久删除只会删除 .backups 中的备份文件。")

    if not records:
        st.warning("未找到匹配的备份题目。")
        return

    for idx, rec in enumerate(records):
        q_label = rec.get("label") or format_question_title(os.path.basename(rec.get("original_path", "")))
        content = rec.get("content", "")
        extra_label = ""
        if rec.get("deleted_at"):
            extra_label = f"<span style='font-size:0.5em; color:gray; font-weight:normal; margin-left: 10px;'>备份于 {html.escape(rec.get('deleted_at'))}</span>"

        render_static_question_header(q_label, content, rec.get("original_path", ""), extra_html_label=extra_label)
        try:
            st.markdown(latex_to_markdown(content, show_title=False), unsafe_allow_html=True)
        except Exception as e:
            st.error(f"渲染错误: {e}")

        c_restore, c_delete = st.columns([1, 1])
        with c_restore:
            st.markdown('<span class="blue-restore-btn-hook"></span>', unsafe_allow_html=True)
            original_exists = os.path.exists(rec.get("original_path", ""))
            restore_label = "原位置已有同名题" if original_exists else "↩️ 恢复该题"
            with _compat_container(key=f"backup_restore_btn_wrap_{rec.get('id', idx)}"):
                if st.button(
                    restore_label,
                    key=f"backup_restore_{rec.get('id', idx)}",
                    type="primary",
                    use_container_width=True,
                    disabled=original_exists,
                ):
                    try:
                        restore_deleted_question_and_sync(rec)
                        st.toast(f"已恢复 {q_label}", icon="✅")
                        st.rerun()
                    except Exception as e:
                        st.error(f"恢复失败: {_format_file_write_error(e)}")
        with c_delete:
            st.markdown('<span class="red-btn-hook"></span>', unsafe_allow_html=True)
            with _compat_container(key=f"backup_delete_wrap_{rec.get('id', idx)}"):
                if st.button("永久删除", key=f"backup_delete_{rec.get('id', idx)}", type="primary", use_container_width=True):
                    try:
                        permanently_delete_backup_record(rec)
                        st.toast(f"已永久删除 {q_label} 的备份。", icon="✅")
                        st.rerun()
                    except Exception as e:
                        st.error(f"永久删除失败: {_format_file_write_error(e)}")
        st.divider()

DELETE_SKIP_CONFIRM_KEY = "delete_skip_confirm_until_refresh"

def _format_file_write_error(e: Exception) -> str:
    msg = str(e)
    is_permission_error = isinstance(e, PermissionError)
    has_windows_lock_code = "WinError 32" in msg or "WinError 5" in msg
    if "题库索引表.csv" in msg:
        return (
            f"{msg}\n\n"
            "处理办法：请先关闭 Excel/WPS/编辑器中打开的 utils/题库索引表.csv，"
            "并确认只保留一个题库系统页面在执行删除/恢复，然后重试。"
        )
    if is_permission_error or has_windows_lock_code or "无法写入文件" in msg:
        return (
            f"{msg}\n\n"
            "处理办法：请关闭正在打开相关题目文件、备份文件或目录的编辑器/预览程序，"
            "并避免同时点击多个删除/恢复/清除按钮，然后重试。"
        )
    return msg

def _execute_delete_question_from_ui(fpath: str, q_label: str):
    try:
        backup_path, removed_rows = delete_question_file_and_sync(fpath)
        st.toast(f"已删除 {q_label}，移除索引记录 {removed_rows} 条。", icon="✅")
        if backup_path:
            st.session_state["last_deleted_question_backup"] = backup_path
        st.rerun()
    except Exception as e:
        st.error(f"删除失败: {_format_file_write_error(e)}")

@st.dialog("确认删除该题", width="small")
def confirm_delete_question_dialog(fpath: str, q_label: str, key_hash: str):
    st.warning(f"确认删除：{q_label}？")
    st.caption("删除会移除题目文件、CSV 索引记录和章节索引引用，原题目文件会自动备份到 .backups。若误删，可点删除模式右上角的“恢复误删题目”恢复本次删除记录，或点“管理备份问题”查找历史备份；当前备份不会自动定期清理。")

    c_opt, c_ok, c_cancel = st.columns([2.2, 1, 1], vertical_alignment="bottom")
    with c_opt:
        skip_confirm = st.checkbox(
            "本次彻底刷新页面前不再提醒",
            key=f"delete_skip_confirm_checkbox_{key_hash}",
        )
    with c_ok:
        if st.button("确认", key=f"delete_confirm_ok_{key_hash}", type="primary", use_container_width=True):
            if skip_confirm:
                st.session_state[DELETE_SKIP_CONFIRM_KEY] = True
            _execute_delete_question_from_ui(fpath, q_label)
    with c_cancel:
        if st.button("取消", key=f"delete_confirm_cancel_{key_hash}", use_container_width=True):
            st.rerun()

def render_static_question_header(q_label: str, content: str, fpath: str, extra_html_label: str = ""):
    st.markdown(f"### {html.escape(q_label)} {extra_html_label}", unsafe_allow_html=True)

    meta = _cached_question_meta(content)
    diff = (meta.get("难度星级", "") or "").strip()
    tags = (meta.get("标签", "") or "").strip()
    knowledge_points = (meta.get("知识点", "") or "").strip()
    physics_methods = (meta.get("物理方法", "") or "").strip()
    remark = (meta.get("备注", "") or "").strip()

    tag_badges = ""
    for tag in [t.strip() for t in tags.split("，") if t.strip()]:
        tag_badges += f"<span class='static-meta-badge static-meta-tag'>🏷️ {html.escape(tag)}</span>"
    if not tag_badges:
        tag_badges = "<span class='static-meta-empty'>无标签</span>"

    diff_text = html.escape(diff) if diff else "未设置"
    remark_text = html.escape(remark) if remark else "无备注"

    st.markdown("""
    <style>
    .static-meta-row {
        display: flex;
        flex-wrap: wrap;
        gap: 8px 14px;
        align-items: center;
        margin: -2px 0 12px 0;
        padding: 10px 12px;
        border: 1px solid var(--mc-border);
        border-radius: var(--mc-control-radius);
        background: var(--mc-surface-soft);
    }
    .static-meta-item {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        color: var(--mc-text);
        font-size: 14px;
        line-height: 1.45;
    }
    .static-meta-label {
        font-weight: 700;
    }
    .static-meta-badge {
        display: inline-flex;
        align-items: center;
        padding: 2px 8px;
        border-radius: 999px;
        font-size: 14px;
        line-height: 1.45;
        font-weight: 600;
    }
    .static-meta-tag {
        color: var(--mc-accent-dark);
        background-color: var(--mc-accent-soft);
        border: 1px solid var(--mc-border);
    }
    .static-meta-empty {
        color: var(--mc-muted);
        font-size: 13px;
    }
    .static-meta-remark {
        padding: 2px 8px;
        color: var(--mc-text);
        background-color: var(--mc-surface);
        border: 1px solid var(--mc-border);
        border-radius: 6px;
        font-weight: 500;
    }
    </style>
    """, unsafe_allow_html=True)

    st.markdown(
        f"""
        <div class="static-meta-row">
            <div class="static-meta-item"><span class="static-meta-label">难度星级：</span><span>{diff_text}</span></div>
            <div class="static-meta-item"><span class="static-meta-label">其他标签：</span>{tag_badges}</div>
            <div class="static-meta-item"><span class="static-meta-label">知识点：</span><span>{html.escape(knowledge_points) if knowledge_points else "未填写"}</span></div>
            <div class="static-meta-item"><span class="static-meta-label">物理方法：</span><span>{html.escape(physics_methods) if physics_methods else "未填写"}</span></div>
            <div class="static-meta-item"><span class="static-meta-label">备注：</span><span class="static-meta-remark">{remark_text}</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

def render_delete_question_item(fpath: str, q_label: str = None, content: str = None, key_prefix: str = "delete_mode", extra_html_label: str = "", show_header: bool = True):
    if not fpath or not os.path.exists(fpath):
        return

    if content is None:
        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()

    q_label = q_label or format_question_title(os.path.basename(fpath))
    if show_header:
        render_static_question_header(q_label, content, fpath, extra_html_label=extra_html_label)

    try:
        st.markdown(latex_to_markdown(content, show_title=False), unsafe_allow_html=True)
    except Exception as e:
        st.error(f"渲染错误: {e}")

    key_hash = hashlib.md5(f"{key_prefix}:{fpath}".encode("utf-8")).hexdigest()[:12]
    st.markdown('<span class="red-btn-hook"></span>', unsafe_allow_html=True)
    if st.button("🗑️ 删除该题", key=f"{key_prefix}_delete_{key_hash}", type="primary", use_container_width=True):
        if st.session_state.get(DELETE_SKIP_CONFIRM_KEY):
            _execute_delete_question_from_ui(fpath, q_label)
        else:
            confirm_delete_question_dialog(fpath, q_label, key_hash)

def ocr_image_to_latex(images=None, max_image_size: int = 1600, max_tokens: int = 4096, spinner_text: str = "🤖 AI 正在识别中，请稍候..."):
    """调用 AI 接口识别图片中的高中物理题（支持多张）。
    Args:
        images: List of PIL Image objects
        max_image_size: 发送给视觉模型前的图片最长边限制。
        max_tokens: 本次 OCR 请求允许返回的最大 token 数。
    """
    # 动态加载 .env 配置，支持热更新
    load_dotenv(_root_env_path(), override=True)

    api_key = os.getenv("AI_API_KEY")
    base_url = os.getenv("AI_BASE_URL", "https://api.openai.com/v1")
    model_name = os.getenv("AI_MODEL_NAME", AI_ENV_DEFAULTS["AI_MODEL_NAME"])

    # 重新读取提示词文件 (支持热更新)
    prompt = AI_OCR_PROMPT
    if os.path.exists(ocr_prompt_file):
        with open(ocr_prompt_file, "r", encoding="utf-8") as f:
            prompt = f.read()
    prompt = (
        "你是 OCR 转写助手。你只需要把图片中的内容逐字逐符号转写成 LaTeX 源码。\n"
        "禁止解题、禁止推理、禁止补全缺失步骤、禁止生成答案与解析。\n"
        "如果图片里本身包含答案/解析/提示，请原样转写；否则不要凭空生成。\n\n"
        "只输出题目正文以及必要的 problem、choices、answer、solutions、tikzpicture 环境。\n"
        "不要输出 Markdown 代码围栏或 latex/tex 语言标记；不要输出 documentclass、usepackage、"
        "usetikzlibrary、begin{document}、end{document} 等完整文档前导内容。\n"
        "TikZ 节点中的角度、上下标等数学内容必须放入 $...$，例如 {$60^\\circ$}。\n\n"
        + (prompt or "")
    )

    if not api_key:
        return "❌ 请先在 .env 文件中配置 AI_API_KEY"

    if not images:
        return "❌ 没有提供图片"

    try:
        from PIL import Image
        import io

        # 构造消息内容
        content_parts = [{"type": "text", "text": prompt}]

        for img in images:
            # 试卷截图中的细线、短箭头和小字标签对分辨率很敏感。
            # 小图适度放大，大图则限制长边，避免请求体过大。
            if max(img.size) < 1000:
                ratio = min(2.0, max_image_size / max(img.size))
                new_size = (max(1, int(img.size[0] * ratio)), max(1, int(img.size[1] * ratio)))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
            if max(img.size) > max_image_size:
                ratio = max_image_size / max(img.size)
                new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)

            # 使用无损 PNG，避免 JPEG 块效应损坏物理图中的细线和小字。
            buffered = io.BytesIO()
            img = img.convert("RGB") # 兼容 PNG 透明通道
            img.save(buffered, format="PNG", optimize=True)
            base64_image = base64.b64encode(buffered.getvalue()).decode('utf-8')

            content_parts.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{base64_image}"
                }
            })

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }

        # 兼容 OpenAI Vision API 格式
        payload = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": content_parts
                }
            ],
            "max_tokens": max_tokens
        }

        with st.spinner(spinner_text):
            # 处理 URL: 兼容不同的 Base URL 写法
            url = normalize_chat_completions_url(base_url)

            st.toast(f"正在请求: {url}")
            print(f"Requesting URL: {url}") # 控制台打印

            try:
                # 设置超时时间为 180 秒 (3分钟)
                response = requests.post(url, headers=headers, json=payload, timeout=180)
            except requests.exceptions.Timeout:
                return "❌ 请求超时 (180s)，请检查网络或稍后重试。"
            except requests.exceptions.RequestException as req_err:
                 return f"❌ 网络请求失败: {str(req_err)}\n请检查 URL ({url}) 是否正确及服务是否可达。"

            if response.status_code != 200:
                return f"❌ 识别失败 (HTTP {response.status_code}):\n{response.text[:500]}"

            try:
                result = response.json()
            except Exception as json_err:
                return f"❌ JSON 解析失败: {str(json_err)}\n\n原始响应内容(前500字符):\n{response.text[:500]}"

            if 'choices' in result and len(result['choices']) > 0:
                return result['choices'][0]['message']['content']
            else:
                return f"❌ 未收到有效回复: {result}"

    except Exception as e:
        return f"❌ 发生错误: {str(e)}"

def render_pdf_pages_to_images(pdf_bytes: bytes, page_numbers, dpi: int = 160):
    """Render selected 1-based PDF pages into in-memory PIL images for OCR."""
    try:
        import fitz
        from PIL import Image
    except ImportError as e:
        return [], f"缺少 PDF 识别依赖：{e}"

    try:
        images = []
        with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf_doc:
            if pdf_doc.needs_pass:
                return [], "PDF 已加密，暂不支持识别受密码保护的文件。"

            for page_number in page_numbers:
                if not 1 <= page_number <= pdf_doc.page_count:
                    return [], f"页码 {page_number} 超出 PDF 的有效范围。"

                page = pdf_doc.load_page(page_number - 1)
                pix = page.get_pixmap(dpi=dpi, alpha=False)
                with Image.open(io.BytesIO(pix.tobytes("png"))) as rendered_image:
                    rendered_image.load()
                    images.append(rendered_image.convert("RGB").copy())

        return images, ""
    except Exception as e:
        return [], f"PDF 页面渲染失败：{e}"

def ocr_solution_images_to_answer_solutions(images=None) -> dict:
    load_dotenv(_root_env_path(), override=True)
    api_key = os.getenv("AI_API_KEY")
    base_url = os.getenv("AI_BASE_URL", "https://api.openai.com/v1")
    model_name = os.getenv("AI_MODEL_NAME", AI_ENV_DEFAULTS["AI_MODEL_NAME"])

    if not api_key or not base_url or not model_name:
        return {"error": "AI 配置不完整，请检查 .env 文件"}
    if not images:
        return {"error": "没有提供图片"}

    prompt = (
        "你是高中物理 OCR 转写助手。请把图片中的“答案/解答/解析”逐字逐符号转写为 LaTeX。\n"
        "禁止解题、禁止推理、禁止补全缺失步骤、禁止凭空生成内容。\n\n"
        "忠实保留物理量、矢量、上下标、正负号、有效数字、单位、方向以及原图已有的受力图或电路图；"
        "单位用 \\mathrm{} 排版，清晰的简图用 tikzpicture 转写，禁止输出宏包或完整文档前导。\n\n"
        "严格输出 JSON，且只包含两个字段：answer_tex 与 solutions_tex。\n"
        "answer_tex：必须是完整的 \\begin{answer}...\\end{answer} 环境；如果图片里没有明确答案，则输出空字符串。\n"
        "solutions_tex：必须是完整的 \\begin{solutions}...\\end{solutions} 环境；如果图片里没有解答过程，则输出空字符串。\n"
        "禁止输出反引号 ` 或 Markdown 代码块。\n"
    )

    try:
        from PIL import Image
        import io

        content_parts = [{"type": "text", "text": prompt}]
        for img in images:
            max_size = 1400
            if max(img.size) > max_size:
                ratio = max_size / max(img.size)
                new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)

            buffered = io.BytesIO()
            img = img.convert("RGB")
            img.save(buffered, format="JPEG", quality=85)
            base64_image = base64.b64encode(buffered.getvalue()).decode("utf-8")
            content_parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}})

        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": content_parts}],
            "temperature": 0.1,
            "max_tokens": 2600,
            "response_format": {"type": "json_object"} if "gpt" in model_name.lower() or "qwen" in model_name.lower() else None,
        }

        response, _ = post_chat_completion(base_url, headers, payload, timeout=180)
        if response.status_code != 200:
            return {"error": f"识别失败 (HTTP {response.status_code}):\n{response.text[:500]}"}

        result = response.json()
        if "choices" not in result or not result["choices"]:
            return {"error": "AI 未返回有效内容"}
        reply = result["choices"][0]["message"]["content"]
        try:
            data = _extract_json_obj_from_text(reply)
        except Exception:
            return {"error": "AI 返回格式解析失败（非 JSON）"}

        answer_tex = _repair_latex_from_json_escapes(str(data.get("answer_tex", "")).strip())
        solutions_tex = _repair_latex_from_json_escapes(str(data.get("solutions_tex", "")).strip())
        return {"answer_tex": answer_tex, "solutions_tex": solutions_tex}
    except requests.exceptions.Timeout:
        return {"error": f"请求超时（模型：{model_name}）。可重试或切换更快模型。"}
    except Exception as e:
        return {"error": f"请求发生异常: {str(e)}"}

def call_ai_for_tags(content: str) -> dict:
    try:
        result = classify_question(content, SUBJECTS, _read_root_ai_env_config())
        result['difficulty'] = float(result['difficulty'])
        return result
    except Exception as exc:
        return {'error': str(exc)}

def _extract_json_obj_from_text(text: str):
    return extract_json_obj_from_text(text)

def _extract_problem_env(tex: str) -> str:
    if not tex:
        return ""
    m = re.search(r"\\begin\{problem\}[\s\S]*?\\end\{problem\}", tex)
    return m.group(0).strip() if m else ""

def _extract_env_block(tex: str, env_name: str) -> str:
    if not tex:
        return ""
    m = re.search(rf"\\begin\{{{re.escape(env_name)}\}}[\s\S]*?\\end\{{{re.escape(env_name)}\}}", tex)
    return m.group(0).strip() if m else ""

def _extract_env_inner_text(tex: str, env_pattern: str) -> str:
    if not tex:
        return ""
    m = re.search(rf"\\begin\{{{env_pattern}\}}(?:\[[^\]]*\])?([\s\S]*?)\\end\{{{env_pattern}\}}", tex)
    return (m.group(1) or "").strip() if m else ""

def _has_nonempty_answer_and_solution(tex: str) -> bool:
    answer_inner = _extract_env_inner_text(tex, "answer")
    solution_inner = _extract_env_inner_text(tex, "solutions?")
    return bool(answer_inner.strip()) and bool(solution_inner.strip())

def _strip_label_data_for_export(tex: str) -> str:
    try:
        _, clean_content = parse_meta_data(tex or "")
        return clean_content.strip()
    except Exception:
        return re.sub(
            r'%(?: === Meta Data ===| === Begin Label Data ===)\r?\n([\s\S]*?)%(?: === End Meta ===| === End\s+Label Data ===)\r?\n',
            '',
            tex or '',
            flags=re.DOTALL,
        ).strip()

def _sanitize_export_filename_component(value: str, fallback: str = "挖空题") -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value or ""))
    text = re.sub(r"\s+", "_", text).strip("._ ")
    return (text or fallback)[:80]

def _build_question_image_tex(content: str) -> str:
    body = _strip_label_data_for_export(content)

    def _inline_input_file(match):
        input_path = (match.group(1) or "").strip()
        if not input_path:
            return match.group(0)
        candidate = input_path if os.path.isabs(input_path) else os.path.join(BASE_DIR, input_path)
        if not candidate.endswith(".tex"):
            candidate += ".tex"
        if not os.path.exists(candidate):
            return match.group(0)
        try:
            with open(candidate, "r", encoding="utf-8") as f:
                return "\n" + f.read() + "\n"
        except Exception:
            return match.group(0)

    body = re.sub(r"\\input\{([^}]+)\}", _inline_input_file, body)
    preamble = r"""\documentclass[12pt]{ctexart}
\usepackage[a4paper,margin=1.35cm]{geometry}
\usepackage{amsmath,amssymb,mathtools}
\usepackage{xparse}
\usepackage{xcolor}
\usepackage{tikz}
\usepackage{pgfplots}
\usepackage{tasks}
\usepackage{enumitem}
\usepackage{array,booktabs,graphicx,float,caption}
\pgfplotsset{compat=1.16}
\usetikzlibrary{patterns,calc,positioning,intersections,arrows,arrows.meta,quotes,angles,3d,trees,decorations.pathreplacing,decorations.markings,decorations.pathmorphing,shapes.geometric,through,shapes.symbols,shapes.arrows,automata,shadows,shapes.callouts}
\pagestyle{empty}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.65em}
\settasks{label=\Alph*.}
\NewDocumentEnvironment{problem}{ m m m m m +b }{
  \par\noindent\textbf{【#1~~#3，#4】}\par
  #6
  \par\vspace{0.3em}
}{}
\NewDocumentEnvironment{answer}{ O{【答案】} O{} O{} +b }{
  \par\noindent\textbf{#1}\quad #4\par
}{}
\NewDocumentEnvironment{solutions}{ O{【解答】} O{} O{} +b }{
  \par\noindent\textbf{#1}\quad #4\par
}{}
\NewDocumentEnvironment{solution}{ O{【解答】} O{} O{} +b }{
  \par\noindent\textbf{#1}\quad #4\par
}{}
\NewDocumentEnvironment{choices}{ O{2} }{\begin{tasks}(#1)}{\end{tasks}}
\NewDocumentCommand{\choice}{m}{\task #1}
\newcommand{\circled}[1]{\tikz[baseline=(char.base)]{\node[shape=circle,draw,inner sep=1pt](char){#1};}}
\newcommand{\circledV}[1]{\tikz[baseline=(char.base)]{\node[shape=circle,draw,inner sep=1pt](char){$\mathrm{V}_{#1}$};}}
\newcommand{\circledA}[1]{\tikz[baseline=(char.base)]{\node[shape=circle,draw,inner sep=1pt](char){$\mathrm{A}_{#1}$};}}
\newcommand{\jc}[1]{\textbf{#1}}
\newcommand{\bj}[1]{\textbf{#1}}
"""
    # AI/OCR 生成的 TikZ 常用模板未定义的语义色名（primaryblue/meterblue/...），
    # 题目图片导出模板必须与题库文档类共用同一组别名，否则导出会报 pgfkeys unknown key。
    preamble += _diagram_color_definitions()
    preamble += "\\begin{document}\n"
    return preamble + "\n" + body + "\n" + r"\end{document}" + "\n"

def generate_question_png_from_latex(content: str, filename_hint: str = "挖空题", cloze_type: str = "") -> dict:
    if not (content or "").strip():
        return {"ok": False, "error": "当前内容为空，无法生成图片。"}

    xelatex = shutil.which("xelatex")
    if not xelatex:
        return {"ok": False, "error": "未检测到 xelatex，无法将 LaTeX 编译为图片。"}

    try:
        import fitz
        from PIL import Image
    except ImportError as e:
        return {"ok": False, "error": f"缺少图片导出依赖：{e}"}

    export_dir = os.path.join(BASE_DIR, "cloze_exports")
    compile_dir = os.path.join(export_dir, "_compile")
    ensure_dir(export_dir)
    ensure_dir(compile_dir)

    unique_id = uuid.uuid4().hex
    tex_name = f"{unique_id}.tex"
    tex_path = os.path.join(compile_dir, tex_name)
    pdf_path = os.path.join(compile_dir, f"{unique_id}.pdf")
    tex_content = _build_question_image_tex(content)
    last_output = ""

    try:
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(tex_content)

        completed = subprocess.run(
            [
                xelatex,
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-file-line-error",
                tex_name,
            ],
            cwd=compile_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
        last_output = completed.stdout or ""
        if completed.returncode != 0 or not os.path.exists(pdf_path):
            return {
                "ok": False,
                "error": "LaTeX 编译失败，暂未生成图片。",
                "log": last_output[-3000:],
            }

        doc = fitz.open(pdf_path)
        page_images = []
        try:
            for page in doc:
                pix = page.get_pixmap(dpi=180, alpha=False)
                page_images.append(Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB"))
        finally:
            doc.close()

        if not page_images:
            return {"ok": False, "error": "PDF 中没有可导出的页面。"}

        width = max(img.width for img in page_images)
        height = sum(img.height for img in page_images)
        combined = Image.new("RGB", (width, height), "white")
        y = 0
        for img in page_images:
            x = (width - img.width) // 2
            combined.paste(img, (x, y))
            y += img.height

        buffer = io.BytesIO()
        combined.save(buffer, format="PNG", optimize=True)
        png_bytes = buffer.getvalue()

        safe_hint = _sanitize_export_filename_component(filename_hint, "挖空题")
        safe_type = _sanitize_export_filename_component(cloze_type, "当前题目")
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        out_name = f"{safe_hint}-{safe_type}-{timestamp}-{unique_id[:6]}.png"
        out_path = os.path.join(export_dir, out_name)
        with open(out_path, "wb") as f:
            f.write(png_bytes)

        return {"ok": True, "path": out_path, "filename": out_name, "bytes": png_bytes}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "LaTeX 编译超时，暂未生成图片。", "log": last_output[-3000:]}
    except Exception as e:
        return {"ok": False, "error": f"图片生成失败：{e}", "log": last_output[-3000:]}
    finally:
        for ext in (".tex", ".aux", ".log", ".out", ".pdf", ".xdv"):
            temp_path = os.path.join(compile_dir, f"{unique_id}{ext}")
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass

def _replace_first_env_or_insert_after_problem(tex: str, env_name: str, new_block: str) -> str:
    new_block = (new_block or "").strip()
    if not new_block:
        return tex
    pat = re.compile(rf"\\begin\{{{re.escape(env_name)}\}}[\s\S]*?\\end\{{{re.escape(env_name)}\}}")
    if pat.search(tex):
        return pat.sub(lambda m: new_block, tex, count=1)
    if "\\end{problem}" in tex:
        return tex.replace("\\end{problem}", "\\end{problem}\n\n" + new_block, 1)
    return tex.rstrip() + "\n\n" + new_block + "\n"

def _insert_block_after(tex: str, anchor_pat: str, new_block: str) -> str:
    m = re.search(anchor_pat, tex)
    if not m:
        return tex.rstrip() + "\n\n" + new_block.strip() + "\n"
    insert_pos = m.end()
    prefix = tex[:insert_pos]
    suffix = tex[insert_pos:]
    return prefix + "\n\n" + new_block.strip() + suffix

def _insert_block_before(tex: str, anchor_pat: str, new_block: str) -> str:
    m = re.search(anchor_pat, tex)
    if not m:
        return new_block.strip() + "\n\n" + tex.lstrip()
    insert_pos = m.start()
    prefix = tex[:insert_pos]
    suffix = tex[insert_pos:]
    return prefix.rstrip() + "\n\n" + new_block.strip() + "\n\n" + suffix.lstrip()

def _replace_or_insert_answer_solutions(tex: str, new_answer: str, new_solutions: str) -> str:
    answer_pat = re.compile(r"\\begin\{answer\}[\s\S]*?\\end\{answer\}")
    sol_pat = re.compile(r"\\begin\{solutions\}[\s\S]*?\\end\{solutions\}")
    has_answer = answer_pat.search(tex) is not None
    has_sol = sol_pat.search(tex) is not None

    if has_answer and has_sol:
        updated = answer_pat.sub(lambda m: new_answer.strip(), tex, count=1)
        updated = sol_pat.sub(lambda m: new_solutions.strip(), updated, count=1)
        return updated

    if has_answer and not has_sol:
        updated = answer_pat.sub(lambda m: new_answer.strip(), tex, count=1)
        return _insert_block_after(updated, r"\\end\{answer\}", new_solutions)

    if not has_answer and has_sol:
        updated = sol_pat.sub(lambda m: new_solutions.strip(), tex, count=1)
        return _insert_block_before(updated, r"\\begin\{solutions\}", new_answer)

    if "\\end{problem}" in tex:
        return _insert_block_after(tex, r"\\end\{problem\}", (new_answer.strip() + "\n\n" + new_solutions.strip()).strip())
    return (tex.rstrip() + "\n\n" + new_answer.strip() + "\n\n" + new_solutions.strip() + "\n").strip() + "\n"

def _extract_solutions_inner(new_solutions_block: str) -> str:
    m = re.search(r"\\begin\{solutions\}(?:\[[^\]]*\])?([\s\S]*?)\\end\{solutions\}", new_solutions_block or "")
    if not m:
        return ""
    return (m.group(1) or "").strip()

def _append_alt_solutions_after_last_solutions(tex: str, new_solutions_block: str, alt_label: str) -> str:
    inner = _extract_solutions_inner(new_solutions_block)
    if not inner:
        raise ValueError("empty solutions")

    label = (alt_label or "").strip()
    if label:
        alt_block = f"\\begin{{solutions}}[{label}]\n{inner}\n\\end{{solutions}}"
    else:
        alt_block = f"\\begin{{solutions}}\n{inner}\n\\end{{solutions}}"

    matches = list(re.finditer(r"\\end\{solutions\}", tex))
    if matches:
        last = matches[-1]
        insert_pos = last.end()
        return tex[:insert_pos] + "\n\n" + alt_block + tex[insert_pos:]

    if "\\end{answer}" in tex:
        return _insert_block_after(tex, r"\\end\{answer\}", alt_block)
    if "\\end{problem}" in tex:
        return _insert_block_after(tex, r"\\end\{problem\}", alt_block)
    return tex.rstrip() + "\n\n" + alt_block + "\n"

def _prepend_line_after_begin(block: str, env_name: str, line: str) -> str:
    block = (block or "").strip()
    if not block:
        return block
    begin = f"\\begin{{{env_name}}}"
    if begin not in block:
        return block
    return block.replace(begin, begin + "\n" + line, 1)

def _split_answer_solutions_from_text(text: str):
    ans = _extract_env_block(text, "answer")
    sol = _extract_env_block(text, "solutions")
    return ans, sol

def _repair_latex_from_json_escapes(text: str) -> str:
    s = text or ""
    s = s.replace("\x08", r"\b")
    s = s.replace("\x0c", r"\f")
    s = s.replace("\x09", r"\t")
    s = s.replace("\x0d", r"\r")
    s = s.replace("\x1b", "\\")
    s = re.sub(r"\n(?=eq\b)", r"\\n", s)
    s = re.sub(r"\n(?=abla\b)", r"\\n", s)

    keep_cmds = {
        "nabla",
        "neq",
        "nexists",
        "nmid",
        "not",
        "notin",
        "nu",
        "nparallel",
        "nsubseteq",
        "nsupseteq",
        "nRightarrow",
        "nrightarrow",
        "nLeftarrow",
        "nleftarrow",
        "nLeftrightarrow",
        "nleftrightarrow",
        "nVdash",
        "nvDash",
        "nvdash",
        "nVDash",
    }

    out = []
    i = 0
    while i < len(s):
        if i + 1 < len(s) and s[i] == "\\" and s[i + 1] == "n":
            j = i + 2
            if j < len(s) and s[j].isalpha():
                k = j
                while k < len(s) and s[k].isalpha():
                    k += 1
                cmd = "n" + s[j:k]
                if cmd in keep_cmds:
                    out.append("\\" + cmd)
                else:
                    out.append("\n" + s[j:k])
                i = k
                continue
            out.append("\n")
            i += 2
            continue
        out.append(s[i])
        i += 1
    s = "".join(out)
    return s

def _normalize_ai_generated_tex_for_preview(text: str) -> str:
    s = _repair_latex_from_json_escapes(text or "")
    s = s.replace("```json", "").replace("```latex", "").replace("```", "")
    s = s.replace("`", "")
    s = re.sub(r"\$\$\s*([\s\S]*?)\s*\$\$", lambda m: "$$\n" + m.group(1).strip() + "\n$$", s)
    s = re.sub(r"(?<!\$)\$([^$\n]*?)\$(?!\$)", lambda m: "$" + m.group(1).strip() + "$", s)
    s = re.sub(r"(\$|\$\$)\s*。", r"\1.", s)
    s = re.sub(r"(\$|\$\$)\s*，", r"\1,", s)
    s = re.sub(r"(\$|\$\$)\s*；", r"\1;", s)
    s = re.sub(r"\$\$\s*([。．\.，,；;])", r"$$\n\1", s)
    s = re.sub(r"([。．\.，,；;])\s*\$\$", r"\1\n$$", s)
    s = re.sub(r"[ \t]*\$\$[ \t]*", "$$", s)
    s = re.sub(r"(?<!\n)\$\$", r"\n$$", s)
    s = re.sub(r"\$\$(?!\n)", r"$$\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)

    def _fix_answer_env(m):
        inner = (m.group(1) or "").strip()
        if not inner:
            fixed_inner = ""
        else:
            fixed_inner = re.sub(r"\\frac(?=\{)", r"\\dfrac", inner)
            has_dollar = ("$" in fixed_inner) or ("$$" in fixed_inner)
            if not has_dollar:
                fixed_inner = "$" + fixed_inner.strip() + "$"
        return "\\begin{answer}\n" + fixed_inner + "\n\\end{answer}"

    s = re.sub(r"\\begin\{answer\}\s*([\s\S]*?)\s*\\end\{answer\}", _fix_answer_env, s)
    s = re.sub(r"\\begin\{solutions\}\s*", lambda _m: "\\begin{solutions}\n", s)
    s = re.sub(r"\s*\\end\{solutions\}", lambda _m: "\n\\end{solutions}", s)
    return s.strip()


CLOZE_BLANK_TEX = r"\underline{\hspace{4em}}"
CLOZE_GENERATION_RULES = {
    "简单定义基础计算挖空": "只挖定义、直接使用的公式、基础计算结果与最终数值。保留关键推导结构，通常设置 $6$ 至 $10$ 个空。",
    "基础推导与题意读取提炼挖空": "挖已知条件的关键转述、核心公式、必要的等价变形和中间结果。通常设置 $5$ 至 $8$ 个空，要求学生能完成完整推导。",
    "关键思路抽象推导挖空": "挖方法选择、关键定理、核心转化、结论衔接和决定性推导。通常设置 $6$ 至 $8$ 个空，但不能把同一行或同一公式挖得无法作答。",
}


def _normalize_cloze_blank_markup(tex: str) -> str:
    """Use one stable blank form so generated cloze questions render consistently."""
    return re.sub(
        r"\\underline\s*\{\s*\\hspace\*?\s*\{[^}]*\}\s*\}",
        lambda _m: CLOZE_BLANK_TEX,
        tex or "",
    )


def _normalize_cloze_generated_tex(text: str) -> str:
    """Clean transport artifacts without changing the answer-key layout."""
    tex = _repair_latex_from_json_escapes(text or "")
    tex = tex.replace("```json", "").replace("```latex", "").replace("```", "").replace("`", "")
    tex = re.sub(r"\\begin\{(problem|answer|solutions)\}\s*", lambda m: f"\\begin{{{m.group(1)}}}\n", tex)
    tex = re.sub(r"\s*\\end\{(problem|answer|solutions)\}", lambda m: f"\n\\end{{{m.group(1)}}}", tex)
    tex = re.sub(r"\n{3,}", "\n\n", tex)
    return _normalize_cloze_blank_markup(tex.strip())


def call_ai_for_cloze_question(source_tex: str, cloze_type: str) -> dict:
    """Generate an editable student cloze version plus its answer and completed solution."""
    load_dotenv(_root_env_path(), override=True)
    api_key = os.getenv("AI_API_KEY")
    base_url = os.getenv("AI_BASE_URL")
    model_name = os.getenv("AI_SOLVER_MODEL_NAME") or os.getenv("AI_MODEL_NAME")

    if not api_key or not base_url or not model_name:
        return {"error": "AI 配置不完整，请检查 .env 文件"}

    source_tex = _strip_label_data_for_export(source_tex)
    if not source_tex or not _extract_problem_env(source_tex):
        return {"error": "来源题目缺少完整的 problem 环境"}

    rule = CLOZE_GENERATION_RULES.get(cloze_type, CLOZE_GENERATION_RULES["简单定义基础计算挖空"])
    prompt = fr"""你是一名高中物理教研专家。请把下面的原题制作成可供学生练习的 LaTeX 挖空题。

本次挖空类型：{cloze_type}
具体要求：{rule}

必须严格输出 JSON，且只包含一个字段 cloze_tex。cloze_tex 必须是完整的 LaTeX 题目，依次包含：
1. 一个完整的 \\begin{{problem}}{{年份}}{{WK}}{{试卷名称}}{{题号}}{{知识板块}} ... \\end{{problem}}。题干必须保留原题的已知条件、小问、TikZ 图和其他必要排版；随后在 problem 内加入“解答过程：”，写出供学生填写的分步解答。
2. 一个完整的 \\begin{{answer}} ... \\end{{answer}}，按空格出现顺序给出每个空对应的内容，并保留原题最终答案。
3. 一个完整的 \\begin{{solutions}} ... \\end{{solutions}}，给出无挖空、完整且可核对的解答过程。

强制规则：
- 每一个空都必须且只能写为 `{CLOZE_BLANK_TEX}`，不得使用其他长度、\_\_\_、\verb|\blank|、\verb|\fillin| 等形式。
- 不能只挖最终答案；空格要覆盖与本次类型相符的关键学习步骤。
- 学生版的 problem 至少包含一个空，且解答过程应能独立作答。
- solutions 中不得保留空格，必须写出完整内容。
- 原题含有 TikZ、wrapfigure 或其他图形代码时，必须逐字保留原图代码，不能删改坐标、标注、样式或图形位置。
- 不得输出 Markdown、反引号或 JSON 以外的说明。
- LaTeX 命令在 JSON 字符串中必须正确转义，使用真实换行，不要输出文字 \\n。

原题：
{source_tex}"""

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "You are a JSON output bot. You only output valid JSON."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 5600,
        "response_format": {"type": "json_object"} if "gpt" in model_name.lower() or "qwen" in model_name.lower() else None,
    }

    try:
        response, _ = post_chat_completion(base_url, headers, payload, timeout=(10, 180))
        if response.status_code != 200:
            return {"error": f"API 请求失败: {response.status_code}\n{response.text[:500]}"}
        result = response.json()
        if "choices" not in result or not result["choices"]:
            return {"error": "AI 未返回有效内容"}
        try:
            data = _extract_json_obj_from_text(result["choices"][0]["message"]["content"])
        except Exception:
            return {"error": "AI 返回格式解析失败（非 JSON）"}

        cloze_tex = _normalize_cloze_generated_tex(str(data.get("cloze_tex", "")))
        problem_tex = _extract_problem_env(cloze_tex)
        answer_tex = _extract_env_block(cloze_tex, "answer")
        solutions_tex = _extract_env_block(cloze_tex, "solutions")
        if not problem_tex or not answer_tex or not solutions_tex:
            return {"error": "AI 返回内容缺少完整的 problem、answer 或 solutions 环境"}
        if CLOZE_BLANK_TEX not in problem_tex:
            return {"error": "AI 未在学生版题目中生成统一格式的挖空"}
        if CLOZE_BLANK_TEX in solutions_tex:
            return {"error": "AI 在完整解析中保留了挖空，请重试"}
        return {"cloze_tex": cloze_tex}
    except requests.exceptions.Timeout:
        return {"error": f"请求超时（模型：{model_name}），请重试"}
    except Exception as e:
        return {"error": f"请求发生异常: {str(e)}"}


def _cloze_source_row_matches(row: dict, field: str, query: str) -> bool:
    query = (query or "").strip()
    if not query:
        return True
    values = {
        "题目ID": row.get("题目ID", ""),
        "题目类型": row.get("题型", ""),
        "题目内容": row.get("题干", ""),
        "解答内容": row.get("解析", ""),
        "难度星级": row.get("难度星级", ""),
        "标签": row.get("标签", ""),
        "其他自由标签": row.get("标签", ""),
        "知识点": row.get("知识点", ""),
        "物理方法": row.get("物理方法", ""),
        "备注": row.get("备注", ""),
    }
    if field == "全文内容":
        value = "\n".join(str(row.get(key, "") or "") for key in ("题干", "答案", "解析", "标签", "知识点", "物理方法", "备注", "试卷名称", "知识板块"))
    else:
        value = values.get(field, "")
    return query in str(value or "")


def _cloze_source_label(row: dict) -> str:
    question_id = (row.get("题目ID", "") or "").strip() or "未分配 ID"
    year = (row.get("年份", "") or "").strip()
    paper = (row.get("试卷名称", "") or "").strip()
    number = (row.get("原卷题号", "") or "").strip()
    subject = (row.get("知识板块", "") or "").strip()
    return f"[{question_id}] {year} {paper} 第{number}题 | {subject}"


def _cloze_source_sort_key(row: dict) -> int:
    try:
        return int(str(row.get("题目ID", "") or "").strip())
    except (TypeError, ValueError):
        return -1


def _load_cloze_source_row(row: dict) -> str:
    rel_path = (row.get("相对文件路径", "") or "").strip()
    source_path = os.path.join(CHAPTERS_DIR, rel_path) if rel_path else ""
    if not source_path or not os.path.exists(source_path):
        raise FileNotFoundError("来源题目文件不存在，请先重建题库索引")
    with open(source_path, "r", encoding="utf-8") as f:
        source_tex = _strip_label_data_for_export(f.read())

    fields = extract_problem_header_fields(source_tex) or {}
    year = fields.get("year") or (row.get("年份", "") or "").strip()
    paper = fields.get("paper") or (row.get("试卷名称", "") or "").strip()
    number = fields.get("number") or (row.get("原卷题号", "") or "").strip()
    subject_str = fields.get("subject_str") or (row.get("知识板块", "") or "").strip()
    draft_tex = replace_problem_header(source_tex, year, "WK", paper, number, subject_str)

    st.session_state["cloze_source_tex"] = source_tex
    st.session_state["cloze_source_path"] = source_path
    st.session_state["cloze_source_label"] = _cloze_source_label(row)
    st.session_state["cloze_source_question_id"] = str(row.get("题目ID", "") or "")
    st.session_state["entry_year"] = year
    st.session_state["entry_p_type"] = "WK"
    st.session_state["entry_paper_name"] = paper
    st.session_state["entry_number"] = number
    st.session_state["entry_subject_multi"] = [item.strip() for item in subject_str.split("，") if item.strip() in SUBJECTS]
    st.session_state["entry_subject_user_locked"] = True
    st.session_state["entry_content"] = draft_tex
    st.session_state["cloze_image_result"] = None
    return draft_tex


def render_cloze_source_picker():
    """Three-condition lookup limited to ordinary questions, then load a source into the draft."""
    search_fields = ["全文内容", "题目ID", "题目类型", "题目内容", "解答内容", "难度星级", "其他自由标签", "知识点", "物理方法", "备注"]
    query_specs = []

    with st.expander("选择来源原题", expanded=False):
        search_cols = st.columns(3)
        for index, column in enumerate(search_cols, start=1):
            with column:
                field = st.selectbox("检索字段", search_fields, key=f"cloze_source_field_{index}")
                if field == "题目类型":
                    query = st.selectbox("检索内容", ["", *QUESTION_TYPES], key=f"cloze_source_query_type_{index}")
                else:
                    query = st.text_input("检索内容", key=f"cloze_source_query_{index}")
                query_specs.append((field, query))

        try:
            from utils.csv_ops import read_csv_index
            source_rows = [
                row for row in read_csv_index()
                if (row.get("试卷类型", "") or "").strip() != "WK"
                and _cloze_source_row_matches(row, query_specs[0][0], query_specs[0][1])
                and _cloze_source_row_matches(row, query_specs[1][0], query_specs[1][1])
                and _cloze_source_row_matches(row, query_specs[2][0], query_specs[2][1])
            ]
        except Exception as e:
            st.error(f"读取来源题目索引失败：{e}")
            source_rows = []

        source_rows.sort(key=_cloze_source_sort_key, reverse=True)
        source_rows = source_rows[:100]
        if source_rows:
            options_by_path = {}
            for row in source_rows:
                rel_path = (row.get("相对文件路径", "") or "").strip()
                if rel_path:
                    options_by_path[rel_path] = row
            option_paths = list(options_by_path)
            if not option_paths:
                st.warning("匹配题目缺少文件路径，请先重建题库索引")
                return
            selected_key = "cloze_source_candidate_path"
            if st.session_state.get(selected_key) not in option_paths:
                st.session_state.pop(selected_key, None)
            selected_path = st.selectbox(
                "候选原题",
                options=option_paths,
                format_func=lambda path: _cloze_source_label(options_by_path[path]),
                key=selected_key,
            )
            if st.button("载入原题", key="btn_load_cloze_source", use_container_width=True):
                try:
                    _load_cloze_source_row(options_by_path[selected_path])
                    st.toast("已载入来源原题", icon="✅")
                    st.rerun()
                except Exception as e:
                    st.error(f"载入来源原题失败：{e}")
        else:
            st.info("没有找到匹配的普通题目")

    source_tex = st.session_state.get("cloze_source_tex", "")
    if not source_tex:
        return

    st.caption(f"来源原题：{st.session_state.get('cloze_source_label', '')}")
    generate_col, clear_col = st.columns([3, 1])
    with generate_col:
        if st.button("✨ 生成挖空题", key="btn_generate_cloze", type="primary", use_container_width=True):
            cloze_type = st.session_state.get("cloze_generation_type", "简单定义基础计算挖空")
            with st.spinner("AI 正在生成挖空题..."):
                result = call_ai_for_cloze_question(source_tex, cloze_type)
            if result.get("error"):
                st.error(result["error"])
            else:
                fields = extract_problem_header_fields(source_tex) or {}
                generated_tex = replace_problem_header(
                    result["cloze_tex"],
                    fields.get("year") or st.session_state.get("entry_year", ""),
                    "WK",
                    fields.get("paper") or st.session_state.get("entry_paper_name", ""),
                    fields.get("number") or st.session_state.get("entry_number", ""),
                    fields.get("subject_str") or "，".join(st.session_state.get("entry_subject_multi", [])),
                )
                st.session_state["entry_content"] = generated_tex
                st.session_state["cloze_image_result"] = None
                st.session_state["cloze_generated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                st.toast("挖空题已生成，可继续人工修改", icon="✅")
                st.rerun()
    with clear_col:
        if st.button("清除来源", key="btn_clear_cloze_source", use_container_width=True):
            for key in ("cloze_source_tex", "cloze_source_path", "cloze_source_label", "cloze_source_question_id", "cloze_generated_at"):
                st.session_state.pop(key, None)
            st.rerun()

def call_ai_for_answer_solutions(problem_tex: str, fast: bool = True) -> dict:
    load_dotenv(_root_env_path(), override=True)
    api_key = os.getenv("AI_API_KEY")
    base_url = os.getenv("AI_BASE_URL")
    model_name = os.getenv("AI_SOLVER_MODEL_NAME") or "qwen3.6-flash"

    if not api_key or not base_url or not model_name:
        return {"error": "AI 配置不完整，请检查 .env 文件"}

    problem_tex = (problem_tex or "").strip()
    if not problem_tex:
        return {"error": "未识别到 \\begin{problem}...\\end{problem}，无法生成解答"}

    def _build_prompt() -> str:
        if fast:
            return f"""请为下面的 LaTeX problem 生成答案与解析。

严格输出 JSON，且只包含两个字段：answer_tex 与 solutions_tex。
answer_tex 必须是完整的 \\begin{{answer}}...\\end{{answer}} 环境（只写最终答案）。
solutions_tex 必须是完整的 \\begin{{solutions}}...\\end{{solutions}} 环境（步骤尽量精简，关键式子即可）。
禁止输出反引号 ` 或 Markdown 代码块。

problem_tex：
{problem_tex}"""
        return f"""你是一名资深高中物理教研专家。请为下面的 LaTeX problem 生成对应的答案与解析。

要求：
1) 严格输出 JSON 格式，包含两个字段：answer_tex 与 solutions_tex。不要输出多余解释。
2) answer_tex 必须用 \\begin{{answer}}...\\end{{answer}} 包裹，且这两句一定要单独一行，中间内容仅输出最终答案（如选项字母、物理量、数值与单位）。
3) solutions_tex 必须用 \\begin{{solutions}}...\\end{{solutions}} 包裹，且这两句一定要单独一行。
4) **解析要求极简高效**：请给出关键公式和核心推导步骤，不需要过多啰嗦的文字描述。优先采用公式表达，且因为所以采用中文，不采用数学符号做这种连接，同时避免长篇大论。
5) 保持 LaTeX 书写规范（如下），注意数学符号排版，最终结论可以使用 \\boxed{{}}；\\boxed{{}} 外面不需要打 $，内部内容涉及到公式时再单独打 $。
6) 禁止输出任何反引号 ` 或 Markdown 代码块标记。
7) solutions_tex 中每个逻辑步骤单独成段，段落之间空一行（用两个换行）。
8) 重要：你输出的是 JSON 字符串。所有 LaTeX 命令的反斜杠必须写成双反斜杠，例如 \\\\frac、\\\\boxed、\\\\neq、\\\\text、\\\\right、\\\\left、\\\\displaystyle。
9) 重要：禁止输出 $\\boxed{...}$ 或 $$\\boxed{...}$$，只能输出 \\boxed{...}。
10) 重要：换行请直接使用真实换行，不要输出 \\n 字符串来表示换行。

排版与符号规范：
- 【公式环境】行内公式用 $ 包裹，居中行间公式用 $$ 包裹。要求：$$ 必须单独占一行（不要写成 $$ 公式 $$），公式本体单独占一行。绝对禁止使用 \\(\\) 或 \\[\\]。
- 【符号规范】遇到分式、求和、累乘等公式（包含行内公式），内部必须强制加 \\displaystyle 指令！数学括号必须使用 \\left( \\right)、\\left[ \\right] 等自适应大小指令！平行用 \\mathop{{//}}。带圈数字（如①、②、③、④等）必须无条件使用 \\circled{{1}}、\\circled{{2}} 格式，绝对禁止直接输出特殊字符①②③！
- 【独立数字/字母】单独的阿拉伯数字(1, 2)或英文字母(A, a)必须、无条件用 $ $ 包裹(如 $1$, $A$)！
- 【标点与空格规范】纯中文句子的结尾正常使用中文句号 。；但紧跟在数学公式或表达式后面的句号，必须严格使用英文句号 .！
- 【文字与公式间距】物理公式与前后的中文文字之间建议加一个空格（例如：物体以速度 $v_0$ 开始运动）。但 $ 与公式内容之间严禁留空格，必须写成 $v_0$，不要写成 $ v_0 $。每一个完整的话（或段落）之间必须空一空行！题目小问直接写（1）（2）或（a），禁止使用 {{enumerate}} 环境。

problem_tex：
{problem_tex}"""

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "You are a JSON output bot. You only output valid JSON."},
            {"role": "user", "content": _build_prompt()},
        ],
        "temperature": 0.1,
        "max_tokens": 1600 if fast else 2600,
        "response_format": {"type": "json_object"} if "gpt" in model_name.lower() or "qwen" in model_name.lower() else None,
    }

    try:
        response, _ = post_chat_completion(base_url, headers, payload, timeout=(10, 90 if fast else 150))
        if response.status_code != 200:
            return {"error": f"API 请求失败: {response.status_code}\n{response.text[:500]}"}
        result = response.json()
        if "choices" not in result or not result["choices"]:
            return {"error": "AI 未返回有效内容"}
        reply = result["choices"][0]["message"]["content"]
        try:
            data = _extract_json_obj_from_text(reply)
        except Exception:
            return {"error": "AI 返回格式解析失败（非 JSON）"}

        answer_tex = str(data.get("answer_tex", "")).strip()
        solutions_tex = str(data.get("solutions_tex", "")).strip()
        answer_tex = _repair_latex_from_json_escapes(answer_tex)
        solutions_tex = _repair_latex_from_json_escapes(solutions_tex)
        if not answer_tex or not solutions_tex:
            return {"error": "AI 返回 JSON 缺少 answer_tex / solutions_tex"}
        if "\\begin{answer}" not in answer_tex or "\\end{answer}" not in answer_tex:
            return {"error": "answer_tex 不是完整的 answer 环境"}
        if "\\begin{solutions}" not in solutions_tex or "\\end{solutions}" not in solutions_tex:
            return {"error": "solutions_tex 不是完整的 solutions 环境"}
        return {"answer_tex": answer_tex, "solutions_tex": solutions_tex}
    except requests.exceptions.Timeout:
        return {"error": f"请求超时（模型：{model_name}）。可重试或切换更快模型。"}
    except Exception as e:
        return {"error": f"请求发生异常: {str(e)}"}

def _update_csv_index_for_content_change(fpath: str, new_content: str):
    from utils.csv_ops import update_csv_index_for_edit
    basename = os.path.basename(fpath).replace(".tex", "")
    parts = basename.split("-")
    if len(parts) >= 5:
        update_csv_index_for_edit(fpath, fpath, new_content, parts[0], parts[1], parts[2], parts[3], parts[4])

def _save_tex_from_widget(fpath: str, widget_key: str, edit_mode_key: str = "", toast_msg: str = "文件已保存！"):
    raw = st.session_state.get(widget_key, "")
    if not _duplicate_save_confirmation(fpath, raw, scope=widget_key):
        return False
    final_content = save_modified_tex_file(fpath, raw)
    _update_csv_index_for_content_change(fpath, final_content)
    _clear_advanced_search_result_cache()
    if edit_mode_key:
        st.session_state[edit_mode_key] = False
    st.session_state["last_saved"] = time.time()
    _request_question_scroll_restore(fpath)
    st.toast(toast_msg, icon="✅")
    return True

def _apply_generated_answer_solutions_to_file(fpath: str, new_answer: str, new_solutions: str, mode: str, alt_label: str = ""):
    with open(fpath, "r", encoding="utf-8") as f:
        old_tex = f.read()

    new_answer = _normalize_ai_generated_tex_for_preview((new_answer or "").strip())
    new_solutions = _normalize_ai_generated_tex_for_preview((new_solutions or "").strip())
    if mode == "replace":
        if not new_answer or not new_solutions:
            raise ValueError("empty answer/solutions")
    else:
        if not new_solutions:
            raise ValueError("empty solutions")

    if mode == "replace":
        updated = _replace_or_insert_answer_solutions(old_tex, new_answer, new_solutions)
    elif mode == "append":
        updated = _append_alt_solutions_after_last_solutions(old_tex, new_solutions, alt_label)
    else:
        raise ValueError("invalid mode")

    final_content = save_modified_tex_file(fpath, updated)
    _update_csv_index_for_content_change(fpath, final_content)
    _clear_advanced_search_result_cache()
    clear_statistics_cache()
    return final_content

def _ai_sol_keys(fpath: str, key_prefix: str):
    import hashlib
    fhash = hashlib.md5(f"{key_prefix}:{fpath}".encode()).hexdigest()[:10]
    data_key = f"ai_sol_data_{fhash}"
    editor_key = f"ai_sol_editor_{fhash}"
    return fhash, data_key, editor_key

def render_ai_solution_generate_button(
    fpath: str,
    current_content: str,
    key_prefix: str,
    use_container_width: bool = True,
    compact: bool = False,
    action_columns=None,
):
    fhash, data_key, editor_key = _ai_sol_keys(fpath, key_prefix)
    do = None
    upload_open_key = f"ai_sol_upload_open_{fhash}"

    if compact:
        if action_columns:
            c_ai, c_img = action_columns
            with c_ai:
                if st.button("\U0001f916 AI\u751f\u6210\u89e3\u7b54", key=f"ai_sol_gen_{fhash}", type="primary", use_container_width=use_container_width):
                    do = "ai"
            with c_img:
                if st.button("\U0001f5bc\ufe0f \u89e3\u7b54\u56fe\u7247\u8bc6\u522b", key=f"ai_sol_img_toggle_{fhash}", type="secondary", use_container_width=use_container_width):
                    st.session_state[upload_open_key] = not st.session_state.get(upload_open_key, False)
        else:
            if st.button("\U0001f916 AI\u751f\u6210\u89e3\u7b54", key=f"ai_sol_gen_{fhash}", type="primary", use_container_width=use_container_width):
                do = "ai"
            if st.button("\U0001f5bc\ufe0f \u89e3\u7b54\u56fe\u7247\u8bc6\u522b", key=f"ai_sol_img_toggle_{fhash}", type="secondary", use_container_width=use_container_width):
                st.session_state[upload_open_key] = not st.session_state.get(upload_open_key, False)
    else:
        c_ai, c_img = st.columns([1, 1])
        with c_ai:
            if st.button("🤖 AI生成解答", key=f"ai_sol_gen_{fhash}", type="primary", use_container_width=use_container_width):
                do = "ai"
        with c_img:
            if st.button("🖼️ 解答图片识别", key=f"ai_sol_img_toggle_{fhash}", type="secondary", use_container_width=use_container_width):
                st.session_state[upload_open_key] = not st.session_state.get(upload_open_key, False)

    if do:
        problem_tex = _extract_problem_env(current_content)
        with st.spinner("🤖 AI 正在生成解答..."):
            res = call_ai_for_answer_solutions(problem_tex, fast=False)
        if "error" in res:
            st.toast(res["error"], icon="❌")
        else:
            combined = _normalize_ai_generated_tex_for_preview(res["answer_tex"].strip() + "\n\n" + res["solutions_tex"].strip())
            st.session_state[data_key] = {"answer_tex": res["answer_tex"], "solutions_tex": res["solutions_tex"]}
            st.session_state[editor_key] = combined
            st.toast("已生成解答（未写回文件）", icon="🪄")
            st.rerun()

def render_ai_solution_image_ocr_section(fpath: str, key_prefix: str, max_images: int = 5, compact: bool = False):
    fhash, data_key, editor_key = _ai_sol_keys(fpath, key_prefix)
    upload_open_key = f"ai_sol_upload_open_{fhash}"
    if not st.session_state.get(upload_open_key, False):
        return

    st.markdown('<hr style="border-top: 1px solid #e1e4e8; margin: 8px 0 12px 0;">', unsafe_allow_html=True)

    try:
        from PIL import Image
    except Exception:
        st.toast("缺少 pillow，无法读取图片", icon="❌")
        return

    queue_key = f"ai_sol_img_queue_{fhash}"
    prev_ids_key = f"ai_sol_img_uploader_prev_{fhash}"
    if queue_key not in st.session_state:
        st.session_state[queue_key] = []
    if prev_ids_key not in st.session_state:
        st.session_state[prev_ids_key] = []

    upload_targets = [st.container(), st.container()] if compact else st.columns([1, 1])
    with upload_targets[0]:
        if st.button("📋 粘贴剪贴板图片", key=f"ai_sol_img_paste_{fhash}", use_container_width=True):
            if not ImageGrab:
                st.toast("缺少 ImageGrab，无法读取剪贴板", icon="❌")
            else:
                try:
                    clip = ImageGrab.grabclipboard()
                    new_imgs = []
                    if isinstance(clip, Image.Image):
                        new_imgs.append(clip)
                    elif isinstance(clip, list):
                        for item in clip:
                            if isinstance(item, str) and os.path.isfile(item):
                                try:
                                    img = Image.open(item)
                                    img.load()
                                    new_imgs.append(img)
                                except Exception:
                                    continue
                    if not new_imgs:
                        st.toast("剪贴板中没有可用图片", icon="⚠️")
                    else:
                        room = max(0, max_images - len(st.session_state[queue_key]))
                        if room <= 0:
                            st.toast(f"队列已满（最多 {max_images} 张）", icon="⚠️")
                        else:
                            st.session_state[queue_key].extend(new_imgs[:room])
                            st.toast(f"已添加 {min(len(new_imgs), room)} 张图片", icon="✅")
                            st.rerun()
                except Exception as e:
                    st.toast(f"剪贴板读取失败: {e}", icon="❌")

    with upload_targets[1]:
        uploaded_files = st.file_uploader("📂 本地上传", type=["png", "jpg", "jpeg"], accept_multiple_files=True, key=f"ai_sol_img_uploader_{fhash}")
        if uploaded_files:
            current_ids = [f"{f.name}_{f.size}" for f in uploaded_files]
            prev_ids = st.session_state.get(prev_ids_key, [])
            room = max(0, max_images - len(st.session_state[queue_key]))
            added = 0
            for uf in uploaded_files:
                if room <= 0:
                    break
                fid = f"{uf.name}_{uf.size}"
                if fid in prev_ids:
                    continue
                try:
                    img = Image.open(uf)
                    img.load()
                    st.session_state[queue_key].append(img)
                    added += 1
                    room -= 1
                except Exception:
                    continue
            st.session_state[prev_ids_key] = current_ids
            if added > 0:
                st.toast(f"已添加 {added} 张图片", icon="✅")
                st.rerun()

    imgs = st.session_state.get(queue_key, []) or []
    c_status, c_clear = ([st.container(), st.container()] if compact else st.columns([3, 1]))
    with c_status:
        st.caption(f"当前队列：{len(imgs)}/{max_images} 张")
    with c_clear:
        if st.button("清空", key=f"ai_sol_img_clear_{fhash}", use_container_width=True):
            st.session_state[queue_key] = []
            st.session_state[prev_ids_key] = []
            st.rerun()

    if imgs:
        cols = [st.container() for _ in imgs] if compact else st.columns(min(max_images, len(imgs)))
        for i, img in enumerate(list(imgs)):
            with cols[i % len(cols)]:
                st.image(img, use_column_width=True)
                if st.button("🗑️ 删除", key=f"ai_sol_img_del_{fhash}_{i}", use_container_width=True):
                    try:
                        st.session_state[queue_key].pop(i)
                    except Exception:
                        pass
                    st.rerun()

    if imgs:
        if st.button(f"开始识别（{len(imgs)} 张）", key=f"ai_sol_img_run_{fhash}", type="primary", use_container_width=True):
            with st.spinner("🤖 AI 正在识别解答图片..."):
                res = ocr_solution_images_to_answer_solutions(images=imgs[:max_images])
            if "error" in res:
                st.toast(res["error"], icon="❌")
            else:
                combined = _normalize_ai_generated_tex_for_preview((res.get("answer_tex") or "").strip() + "\n\n" + (res.get("solutions_tex") or "").strip())
                st.session_state[data_key] = {"answer_tex": res.get("answer_tex") or "", "solutions_tex": res.get("solutions_tex") or ""}
                st.session_state[editor_key] = combined
                st.toast("已识别解答（未写回文件）", icon="🪄")
                st.rerun()

def render_ai_solution_panel(fpath: str, q_label: str, key_prefix: str):
    fhash, data_key, editor_key = _ai_sol_keys(fpath, key_prefix)
    if data_key not in st.session_state:
        return

    st.markdown(f"### {q_label} <span style='color: var(--mc-accent-dark);'>问题的新生成解答</span>", unsafe_allow_html=True)

    col_close, _ = st.columns([0.15, 0.85])
    with col_close:
        if st.button("✖ 关闭面板", key=f"close_ai_panel_{fhash}", use_container_width=True):
            del st.session_state[data_key]
            st.rerun()

    c_left, c_right = st.columns([1, 1])
    with c_left:
        gen_text = st.text_area("解答源码", key=editor_key, height=320)
    with c_right:
        try:
            preview_text = _normalize_ai_generated_tex_for_preview(gen_text)
            st.markdown(latex_to_markdown(preview_text, show_title=False), unsafe_allow_html=True)
        except Exception as e:
            st.error(f"渲染错误: {e}")

    ans, sol = _split_answer_solutions_from_text(gen_text)
    if not sol:
        st.warning("解答源码中未检测到 solutions 环境，暂无法写回。")
        return

    opt_c1, opt_c2 = st.columns([1, 1])
    with opt_c1:
        if st.button("✅ 替换原本的解答与答案", key=f"ai_sol_apply_replace_{fhash}", type="primary", use_container_width=True):
            if not ans:
                st.toast("缺少 answer 环境，无法执行替换。", icon="❌")
                return
            try:
                _apply_generated_answer_solutions_to_file(fpath, ans, sol, mode="replace")
                st.toast("已替换并保存", icon="✅")
                del st.session_state[data_key]
                _request_question_scroll_restore(fpath)
                st.rerun()
            except Exception as e:
                st.toast(f"保存失败: {e}", icon="❌")
    with opt_c2:
        st.markdown("**保存为（另解/解法）**", unsafe_allow_html=True)
        alt_label = st.text_input("保存为（另解/解法）", value="另解", key=f"ai_sol_alt_label_{fhash}", label_visibility="collapsed")
        if st.button("💾 保存为另解/解法", key=f"ai_sol_apply_append_{fhash}", use_container_width=True):
            try:
                _apply_generated_answer_solutions_to_file(fpath, ans, sol, mode="append", alt_label=alt_label)
                st.toast("已追加并保存", icon="✅")
                del st.session_state[data_key]
                _request_question_scroll_restore(fpath)
                st.rerun()
            except Exception as e:
                st.toast(f"保存失败: {e}", icon="❌")

def call_ai_for_polish(intent_text: str) -> str:
    """调用 AI 润色用户的组卷意图"""
    api_key = os.getenv("AI_API_KEY")
    base_url = os.getenv("AI_BASE_URL")
    model_name = os.getenv("AI_MODEL_NAME")

    if not api_key or not base_url or not model_name:
        return "❌ AI 配置不完整，请检查 .env 文件"
        
    prompt = f"""你是一个资深的高中物理教研专家。请帮我润色以下组卷意图，使其更加专业、明确、富有条理。
润色后的文本将用于指导后续的 AI 抽题算法。
要求：
1. 保持原意不变，但语言更精准。
2. 直接输出润色后的文本，不要带有任何“好的”、“没问题”等废话。

原想法：
{intent_text}"""

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    payload = {
        "model": model_name,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }

    try:
        response, _ = post_chat_completion(base_url, headers, payload, timeout=20)
        if response.status_code == 200:
            result = response.json()
            if 'choices' in result and len(result['choices']) > 0:
                return result['choices'][0]['message']['content'].strip()
            else:
                return "❌ AI 未返回有效内容"
        else:
            return f"❌ API 请求失败: {response.status_code}"
    except Exception as e:
        return f"❌ 请求发生异常: {str(e)}"

def sanitize_ocr_latex_response(text):
    """移除视觉模型常附带的 Markdown 标记和完整 LaTeX 文档外壳。"""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    text = re.sub(r'^\s*```(?:latex|tex)?\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*```\s*$', '', text)
    text = re.sub(r'^\s*\\?(?:latex|tex)\b\s*', '', text, count=1, flags=re.IGNORECASE)

    document_match = re.search(
        r'\\begin\{document\}([\s\S]*?)\\end\{document\}',
        text,
        flags=re.IGNORECASE,
    )
    if document_match:
        text = document_match.group(1)
    else:
        text = re.sub(
            r'^\s*\\(?:documentclass|usepackage|usetikzlibrary)'
            r'(?:\[[^\n]*?\])?\{[^\n]*?\}\s*$',
            '',
            text,
            flags=re.MULTILINE | re.IGNORECASE,
        )
        text = re.sub(r'\\(?:begin|end)\{document\}', '', text, flags=re.IGNORECASE)

    text = re.sub(r'^\s*\\maketitle\s*$', '', text, flags=re.MULTILINE)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def process_ocr_result(ocr_result, mode):
    """处理识别结果并更新界面"""
    if "❌" in ocr_result:
        st.error(ocr_result)
    else:
        st.success("识别成功！")

        ocr_result = sanitize_ocr_latex_response(ocr_result)
        
        # 强制后处理：将公式结尾的中文句号“。”替换为英文句号“.”
        ocr_result = re.sub(r'\$(\s*)。', r'$\1.', ocr_result)
        ocr_result = re.sub(r'\$\$(\s*)。', r'$$\1.', ocr_result)

        if mode == "单题录入":
            st.session_state["entry_tex_direct"] = False
            st.session_state["entry_content"] = ""
            st.session_state["entry_custom_tags"] = ""
            st.session_state["entry_knowledge_points"] = ""
            st.session_state["entry_physics_methods"] = ""
            st.session_state["entry_remark"] = ""
            st.session_state["entry_difficulty"] = 0.0
            st.session_state["entry_subject_user_locked"] = False

            # 强制清理 AI 可能生成的 ---xxx.tex--- 分隔符
            ocr_result = re.sub(r'---.*?\.tex---\n*', '', ocr_result).strip()

            # 解析 LaTeX 填充表单
            match = re.search(r'\\begin\{problem\}\{(.*?)\}\{(.*?)\}\{(.*?)\}\{(.*?)\}\{(.*?)\}', ocr_result, re.DOTALL)
            if match:
                y, t, n, num, s = match.groups()
                st.session_state["entry_year"] = y

                # 尝试匹配类型代码
                found_type = False
                # t 可能是 "XK", "XK(学考题)", "学考题" 等形式
                t_clean = t.split('(')[0].split('（')[0].strip()

                for k, v in PAPER_TYPES.items():
                    if k == t_clean or v == t_clean or k == t or v == t:
                        st.session_state["entry_p_type"] = k
                        found_type = True
                        break

                if not found_type:
                    # 如果没匹配到，默认 G，并在名称里备注原类型
                    st.session_state["entry_p_type"] = "G"
                    if t:
                        n = f"{t}-{n}"

                st.session_state["entry_paper_name"] = n
                st.session_state["entry_number"] = num

                # 解析 AI 提取的板块 (支持多板块)
                extracted_subjects = [subj.strip() for subj in s.split("，")]
                valid_subjects = [subj for subj in extracted_subjects if subj in SUBJECTS]
                if valid_subjects:
                    st.session_state["entry_subject_multi"] = valid_subjects
                    st.session_state["entry_subject_user_locked"] = True

                # 标记这次内容更新来源于 AI 识别，避免在后续渲染时被本地启发式逻辑覆盖
                st.session_state["_ai_override_subjects"] = True

                # 如果没有解析和答案环境，自动预留并重构正确的 \end{problem} 位置
                clean_res = normalize_single_problem_structure(ocr_result.strip(), y, st.session_state.get("entry_p_type", "G"), n, num, s)

                # 存储完整的 LaTeX 内容（不再强行插入 Label Data，保持编辑框清爽）
                st.session_state["entry_content"] = clean_res
                st.rerun()
            else:
                st.warning("识别内容未包含标准 problem 结构，已自动进行结构重组。")
                selected_subjects = st.session_state.get("entry_subject_multi", []) or []
                st.session_state["entry_content"] = normalize_single_problem_structure(
                    ocr_result.strip(),
                    st.session_state.get("entry_year", "?"),
                    st.session_state.get("entry_p_type", "G"),
                    st.session_state.get("entry_paper_name", "?"),
                    st.session_state.get("entry_number", "?"),
                    "，".join(selected_subjects) if selected_subjects else "?",
                )
                st.rerun()

        else: # 批量模式（包括同卷试题录入和批量试题录入）
            # 智能解析批量OCR结果，支持多问题识别
            try:
                processed_result = process_batch_ocr_result(ocr_result, mode)
            except ValueError as exc:
                st.error(f'分题检测失败：{exc}；现有草稿已保留。')
                return

            existing = st.session_state.get("batch_content", "").strip()
            if existing and st.session_state.get("entry_batch_merge", "追加到现有题目") == "追加到现有题目":
                processed_result = existing + "\n\n" + processed_result
            st.session_state["batch_content"] = processed_result
            st.session_state["batch_items_src_hash"] = None
            st.rerun()

def normalize_single_problem_structure(text, s_year="?", s_type="?", s_paper="?", s_num="?", s_subj="?"):
    r"""安全提取并重组单题的 LaTeX 结构，确保 \begin{problem}...\end{problem} 包裹正确，并预留答案和解析。"""
    text = sanitize_ocr_latex_response(text)

    # 提取并移除答案
    ans_match = re.search(r'\\begin\{answer\}(.*?)\\end\{answer\}', text, re.DOTALL)
    ans_text = ans_match.group(0) if ans_match else ""

    # 提取并移除解析
    sol_match = re.search(r'\\begin\{solutions?\}(.*?)\\end\{solutions?\}', text, re.DOTALL)
    sol_text = sol_match.group(0) if sol_match else ""

    # 获取剩余的题干部分
    stem_text = text
    if ans_text:
        stem_text = stem_text.replace(ans_text, "")
    if sol_text:
        stem_text = stem_text.replace(sol_text, "")

    # 如果存在旧的 \begin{problem}{...} 参数头部，提取它以便在没有传入新参数时复用
    old_params_match = re.search(r'\\begin\{problem\}(\{.*?\})?(\{.*?\})?(\{.*?\})?(\{.*?\})?(\{.*?\})?', stem_text)
    if old_params_match and s_year == "?":
        params = [p.strip('{}') if p else "?" for p in old_params_match.groups()]
        s_year, s_type, s_paper, s_num, s_subj = (params + ["?", "?", "?", "?", "?"])[:5]

    # 彻底清理掉 \begin{problem} 和 \end{problem} 标签，只留纯文本题干
    stem_text = re.sub(r'\\begin\{problem\}(\{.*?\}){0,5}', '', stem_text)
    stem_text = stem_text.replace(r'\end{problem}', '')
    stem_text = stem_text.strip()

    # 重新组装
    full_text = f"\\begin{{problem}}{{{s_year}}}{{{s_type}}}{{{s_paper}}}{{{s_num}}}{{{s_subj}}}\n{stem_text}\n\\end{{problem}}"

    if ans_text:
        full_text += f"\n\n{ans_text}"
    else:
        full_text += f"\n\n\\begin{{answer}}\n\n\\end{{answer}}"

    if sol_text:
        full_text += f"\n\n{sol_text}"
    else:
        full_text += f"\n\n\\begin{{solutions}}\n\n\\end{{solutions}}"

    # 修复选择题前面缺少 (\hspace{1cm}) 的问题
    # 先把任何形式的空括号() （）删掉，或者如果已经有 (\hspace{1cm}) 则保留
    # 用更安全的字符串处理方式，避免正则表达式的 Lookbehind 在变长字符串下失效
    if r"\begin{choices}" in full_text:
        parts = full_text.split(r"\begin{choices}")
        for i in range(len(parts) - 1):
            prefix = parts[i].rstrip()
            if prefix.endswith("()") or prefix.endswith("（）"):
                prefix = prefix[:-2]

            # 如果结尾还不是 \hspace{1cm}，就加上
            if not prefix.endswith(r"\hspace{1cm})"):
                prefix += r" (\hspace{1cm})"
            parts[i] = prefix + "\n"
        full_text = r"\begin{choices}".join(parts)

    return full_text

def fix_problem_format(text):
    """修复 \begin{problem} 的非标准格式，统一转为 {年份}{类别}{试卷}{题号}{板块} 格式"""

    # 模式1: [xxx][yyy][zzz] ||aa||bb 格式（AI可能返回的非标准格式）
    pattern1 = r'\\begin\{problem\}\[(.*?)\]\[(.*?)\]\[(.*?)\]\s*\|\|(.*?)\|\|(.*?)\]'
    def repl1(m):
        return f'\\begin{{problem}}{{{m.group(1)}}}{{{m.group(2)}}}{{{m.group(3)}}}{{{m.group(4)}}}{{{m.group(5)}}}'
    text = re.sub(pattern1, repl1, text)

    # 模式2: [xxx][yyy][zzz] [aa][bb] 格式（另一种可能的非标准格式）
    pattern2 = r'\\begin\{problem\}\[(.*?)\]\[(.*?)\]\[(.*?)\]\s*\[(.*?)\]\[(.*?)\]'
    def repl2(m):
        return f'\\begin{{problem}}{{{m.group(1)}}}{{{m.group(2)}}}{{{m.group(3)}}}{{{m.group(4)}}}{{{m.group(5)}}}'
    text = re.sub(pattern2, repl2, text)

    # 模式3: 无参数的 \begin{problem}（确保有5个参数）
    pattern3 = r'\\begin\{problem\}(?!\{)'
    text = re.sub(pattern3, r'\\begin{problem}{?}{?}{?}{?}{?}', text)

    # 修复 \choice 内部的异常换行，将多行的 \choice{{...}} 合并为单行
    # 匹配 \choice{{ 开头，直到 }} 结尾的内容，将其内部的换行符替换为空格
    def fix_choice_newlines(match):
        inner_content = match.group(1).replace('\n', ' ')
        return f"\\choice{{{{{inner_content}}}}}"

    text = re.sub(r'\\choice\{\{(.*?)\}\}', fix_choice_newlines, text, flags=re.DOTALL)

    return text

def _increment_question_number(number: str, offset: int = 1) -> str:
    """Increment numeric question numbers while keeping non-numeric labels editable."""
    value = str(number or "").strip()
    if value.isdigit():
        return str(int(value) + offset)
    return f"{value}-{offset + 1}" if value else str(offset + 1)

def _split_problem_block_by_choices(problem_block: str) -> list[str]:
    """Split an OCR block only when repeated choice environments give clear boundaries."""
    choices = list(re.finditer(r"\\begin\{choices\}", problem_block or ""))
    if len(choices) < 2:
        return [problem_block]

    # Do not guess where provided answers or solutions belong.
    for env_name in ("answer", "solutions", "solution"):
        match = re.search(rf"\\begin\{{{env_name}\}}(.*?)\\end\{{{env_name}\}}", problem_block or "", re.DOTALL)
        if match and match.group(1).strip():
            return [problem_block]

    boundaries = []
    for choice_start in choices[1:]:
        previous_end = problem_block.rfind(r"\end{choices}", 0, choice_start.start())
        if previous_end < 0:
            return [problem_block]
        boundary = previous_end + len(r"\end{choices}")
        between = problem_block[boundary:choice_start.start()]
        # There must be real text between two choice groups; otherwise this may be
        # a malformed single question rather than two questions.
        if not re.search(r"[^\s\\{}%]", between):
            return [problem_block]
        boundaries.append(boundary)

    pieces = []
    starts = [0] + boundaries
    ends = boundaries + [len(problem_block)]
    for start, end in zip(starts, ends):
        piece = problem_block[start:end].strip()
        if piece:
            pieces.append(piece)
    return pieces if len(pieces) == len(choices) else [problem_block]

def process_batch_ocr_result(ocr_result, mode):
    """Normalize OCR output into one independently editable block per problem."""

    # 先修复 \begin{problem} 的非标准格式（AI可能返回 [xxx][yyy] ||zzz||www 格式）
    ocr_result = fix_problem_format(ocr_result)
    defaults = dict(year=st.session_state.get('u_batch_year', st.session_state.get('entry_year', '2026')),
                    paper_type=st.session_state.get('u_batch_type', st.session_state.get('entry_p_type', 'G')),
                    paper=st.session_state.get('u_batch_paper', st.session_state.get('entry_paper_name', '待填写')),
                    number=st.session_state.get('entry_number', '1') or '1', subject='未分类')
    ocr_result = join_tex_questions(detect_tex_questions(ocr_result, 'AI识别.tex', defaults))

    # 以 problem 环境而非 AI 输出的文件分隔符作为唯一切分依据。模型可能只为第一题
    # 输出分隔符，而每个 problem 都是完整题目的可靠边界。
    problem_starts = list(re.finditer(r'\\begin\{problem\}', ocr_result))
    if not problem_starts:
        return ocr_result.strip()

    from utils.csv_ops import get_next_id

    current_id = get_next_id()
    normalized_items = []
    first_info = None
    ignored_prefix = ocr_result[:problem_starts[0].start()]
    if ignored_prefix.strip() and not re.fullmatch(r'[\s\-\.\w\u4e00-\u9fff（）()]+', ignored_prefix.strip()):
        print(f"忽略首个题目前的 OCR 内容：{ignored_prefix[:120]!r}")

    for index, problem_start in enumerate(problem_starts):
        problem_end = problem_starts[index + 1].start() if index + 1 < len(problem_starts) else len(ocr_result)
        problem_block = ocr_result[problem_start.start():problem_end]

        # 删除模型可能残留在当前题块尾部的下一题文件分隔符。
        problem_block = re.sub(r'\n?---[^\n]*?\.tex---\s*$', '', problem_block).strip()
        # 模型常给每道题末尾补 \newpage（本意是分页），入库前统一清掉，
        # 否则预览与组卷时会多出一行 "\newpage" 文本。
        problem_block = strip_layout_commands(problem_block).strip()
        header_match = re.match(
            r'\\begin\{problem\}\{(.*?)\}\{(.*?)\}\{(.*?)\}\{(.*?)\}\{(.*?)\}',
            problem_block,
            re.DOTALL,
        )
        if not header_match:
            continue

        year, p_type, paper, number, subject = (value.strip() for value in header_match.groups())
        if first_info is None:
            first_info = {"year": year, "type": p_type, "paper": paper}

        fragments = _split_problem_block_by_choices(problem_block)
        used_numbers = set()
        for fragment_index, fragment in enumerate(fragments):
            fragment_number = number if fragment_index == 0 else _increment_question_number(number, fragment_index)
            while fragment_number in used_numbers:
                fragment_number = _increment_question_number(fragment_number)
            used_numbers.add(fragment_number)
            if len(fragments) == 1:
                fragment_content = fragment
            else:
                fragment_content = normalize_single_problem_structure(
                    fragment,
                    year,
                    p_type,
                    paper,
                    fragment_number,
                    subject or "未分类",
                )
            filename = generate_filename(year, p_type, paper, fragment_number, subject or "未分类")
            label_data = (
                "% === Begin Label Data ===\n"
                f"% ID: {current_id}\n"
                "% 难度星级: \n"
                "% 标签: \n"
                "% 备注: \n"
                "% 组卷引用次数: 0\n"
                "% === End  Label Data ==="
            )
            current_id += 1
            normalized_items.append(f"---{filename}---\n\n{label_data}\n\n{fragment_content}")

    if first_info and not (
        st.session_state.get("batch_content", "").strip()
        and st.session_state.get("entry_batch_merge", "追加到现有题目") == "追加到现有题目"
    ):
        update_batch_form_from_ocr(first_info)

    return "\n\n".join(normalized_items).strip() if normalized_items else ocr_result.strip()

def extract_info_to_form(ocr_result):
    """从单个OCR结果中提取信息并更新到表单"""
    match = re.search(r'\\begin\{problem\}\{(.*?)\}\{(.*?)\}\{(.*?)\}\{(.*?)\}\{(.*?)\}', ocr_result, re.DOTALL)
    if match:
        y, t, n, num, s = match.groups()
        info = {'year': y, 'type': t, 'paper': n}
        update_batch_form_from_ocr(info)

def update_batch_form_from_ocr(info):
    """更新同卷试题录入表单中的统一信息"""
    try:
        # 更新年份
        if info.get('year') and info['year'].isdigit():
            st.session_state["u_batch_year"] = info['year']

        # 更新类别
        if info.get('type'):
            t_clean = info['type'].split('(')[0].split('（')[0].strip()
            for k, v in PAPER_TYPES.items():
                if k == t_clean or v == t_clean or k == info['type'] or v == info['type']:
                    st.session_state["u_batch_type"] = k
                    break

        # 更新试卷名称
        if info.get('paper'):
            st.session_state["u_batch_paper"] = info['paper']

        st.toast("✅ 已自动提取并填入年份和试卷信息", icon="📝")
    except Exception as e:
        print(f"更新表单信息时出错: {e}")

# ================= 页面：新题录入 =================
TEX_ENTRY_EXAMPLE = r"""\begin{problem}{2026}{M}{物理模拟卷}{1}{牛顿运动定律}
质量为 $m$ 的物体受到合力 $F$，求加速度。
\end{problem}

\begin{answer}
$a=F/m$
\end{answer}

\begin{solutions}[解析]
由牛顿第二定律 $F=ma$ 可得。
\end{solutions}
"""


def _tex_editor_changed():
    index = st.session_state.get("tex_workspace_selected", 0)
    st.session_state["tex_workspace_documents"][index]["source"] = st.session_state.get("tex_workspace_source", "")


def _tex_document_changed():
    index = st.session_state["tex_workspace_selected"]
    st.session_state["tex_workspace_source"] = st.session_state["tex_workspace_documents"][index]["source"]


def _tex_load_bank_question():
    path = st.session_state.get('tex_workspace_bank_file', '')
    if not path or not os.path.isfile(path):
        st.session_state['tex_workspace_notice'] = '找不到选中的题目文件。'
        return
    if os.path.commonpath([os.path.abspath(path), os.path.abspath(CHAPTERS_DIR)]) != os.path.abspath(CHAPTERS_DIR):
        st.session_state['tex_workspace_notice'] = '题目路径不在当前题库内。'
        return
    with open(path, 'rb') as handle:
        source = decode_tex(handle.read())
    source = repair_problem_headers(source)
    fields = extract_problem_header_fields(source) or {}
    defaults = dict(year=fields.get('year', '2026'), paper_type=fields.get('p_type', 'G'),
                    paper=fields.get('paper', '待填写'), number=fields.get('number', '1'),
                    subject=fields.get('subject_str', '未分类'))
    questions = detect_tex_questions(source, os.path.basename(path), defaults)
    documents = st.session_state['tex_workspace_documents']
    if len(documents) == 1 and not documents[0]['source'].strip():
        documents[0] = dict(name=os.path.basename(path), source=source)
    else:
        documents.append(dict(name=os.path.basename(path), source=source))
    st.session_state.update(tex_workspace_selected=len(documents) - 1, tex_workspace_source=source,
                            tex_workspace_year=defaults['year'], tex_workspace_type=defaults['paper_type'],
                            tex_workspace_paper=defaults['paper'], tex_workspace_mode='批量试题录入' if len(questions) > 1 else '单题录入')
    numbers = [int(q.number) for q in questions if q.number.isdigit()]
    if numbers:
        st.session_state['tex_workspace_number'] = min(numbers)
        st.session_state['tex_workspace_end_number'] = str(max(numbers)) if len(questions) > 1 else ''
    st.session_state['tex_workspace_notice'] = f'已载入并检测到 {len(questions)} 道题。请生成逐题草稿；原题文件保留。'


def _tex_merge_ai_draft():
    mode = st.session_state.get("tex_workspace_mode", "单题录入")
    draft = st.session_state.get("entry_content" if mode == "单题录入" else "batch_content", "").strip()
    if not draft:
        st.session_state["tex_workspace_notice"] = "AI 录入中还没有对应模式的草稿。"
        return
    index = st.session_state["tex_workspace_selected"]
    current = st.session_state.get("tex_workspace_source", "").rstrip()
    defaults = dict(year=st.session_state.get("tex_workspace_year", "2026"),
                    paper_type=st.session_state.get("tex_workspace_type", "G"),
                    paper=st.session_state.get("tex_workspace_paper", "物理模拟卷"),
                    number=str(st.session_state.get("tex_workspace_number", 1)),
                    subject=st.session_state.get("tex_workspace_subject", "未分类"))
    try:
        # Drop export markers only after parsing both drafts independently, so
        # an AI file delimiter cannot hide the preceding TeX draft on import.
        ai_questions = parse_tex_questions(draft, "AI草稿.tex", defaults)
        draft = "\n\n".join(q.content for q in ai_questions)
        if current and r"\end{document}" not in current:
            existing = parse_tex_questions(current, st.session_state["tex_workspace_documents"][index]["name"], defaults)
            current = "\n\n".join(q.content for q in existing)
        elif current and not re.search(r'\\begin\{problem\}', current):
            st.session_state["tex_workspace_notice"] = "完整文档中的原题请先用 problem 环境标记，再合并 AI 草稿。"
            return
    except ValueError as exc:
        st.session_state["tex_workspace_notice"] = f"合并失败，草稿已保留：{exc}"
        return
    if r"\end{document}" in current:
        merged = current.replace(r"\end{document}", "\n" + draft + "\n" + r"\end{document}", 1)
    else:
        merged = current + "\n\n" + draft
    st.session_state["tex_workspace_documents"][index]["source"] = merged
    st.session_state["tex_workspace_source"] = merged


def _tex_workspace_questions(source, filename, defaults, mode):
    questions = detect_tex_questions(source, filename, defaults)
    if mode == "单题录入" and len(questions) != 1:
        raise ValueError(f"当前文件包含 {len(questions)} 道题，请选择批量或同卷模式。")
    if mode == "同卷试题录入":
        for question in questions:
            question.year = defaults["year"]
            question.paper_type = defaults["paper_type"]
            question.paper = defaults["paper"]
            question.content = replace_problem_header(question.content, question.year, question.paper_type,
                                                      question.paper, question.number, question.subject)
    for question in questions:
        if question.paper_type not in _editable_paper_type_options():
            raise ValueError(f"第 {question.number} 题的类别 {question.paper_type} 不属于普通题库。")
        if not question.year.isdigit():
            raise ValueError(f"第 {question.number} 题的年份需填写数字。")
    return questions


def _tex_classify_one(question):
    result = classify_question(question.content, SUBJECTS, _read_root_ai_env_config())
    question.subject = result['subject']
    question.content = replace_problem_header(question.content, question.year, question.paper_type, question.paper,
                                             question.number, question.subject)
    meta, _ = parse_meta_data(question.content)
    meta.update({'知识点': result['knowledge_points'], '物理方法': result['physics_methods'],
                 '标签': result['tags'], '难度星级': result['difficulty']})
    question.content = inject_meta_data(question.content, meta)


def _tex_detect_review(doc, defaults, mode, expected_count=None, ai_enabled=True):
    questions = _tex_workspace_questions(doc['source'], doc['name'], defaults, mode)
    if expected_count and len(questions) != expected_count:
        if len(questions) == 1 and ai_enabled:
            questions = split_question_with_ai(questions[0], expected_count, int(defaults['number']), _read_root_ai_env_config())
        else:
            raise ValueError(f'题号范围需要 {expected_count} 道题，实际检测到 {len(questions)} 道。请补充题号或 problem 环境。')
    elif len(questions) == 1 and mode != '单题录入' and ai_enabled and not expected_count:
        questions = split_question_with_ai(questions[0], None, int(defaults['number']), _read_root_ai_env_config())
    if expected_count:
        for index, question in enumerate(questions):
            question.number = str(int(defaults['number']) + index)
            question.content = replace_problem_header(question.content, question.year, question.paper_type, question.paper,
                                                      question.number, question.subject)
    elif mode == '单题录入':
        questions[0].number = defaults['number']
        q = questions[0]
        q.content = replace_problem_header(q.content, q.year, q.paper_type, q.paper, q.number, q.subject)
    errors = []
    if ai_enabled:
        progress = st.progress(0)
        for index, question in enumerate(questions):
            try:
                _tex_classify_one(question)
            except Exception as exc:
                errors.append(f'第 {question.number} 题：AI 标注失败，请重试或手动修改。{exc}')
            progress.progress((index + 1) / len(questions))
        progress.empty()
    return questions, errors


def _render_tex_question_review(doc, selected, signature):
    questions = doc.get('review_questions', [])
    if not questions:
        return
    if doc.get('review_signature') != signature:
        st.warning('源码、题号范围或录入信息已改变，请重新检测分题后保存。')
        return
    st.markdown(f'##### 已分成 {len(questions)} 道独立题目')
    st.caption('每道题可修改题号、分类、知识点与源码；保存会分别写成独立文件。')
    generation = doc.get('review_generation', 0)
    for index, question in enumerate(questions):
        prefix = f'tex_review_{selected}_{generation}_{index}'
        with st.expander(f'第 {question.number} 题 · {question.subject}', expanded=len(questions) == 1):
            values = dict(number=question.number, subject=[s for s in question.subject.split('，') if s in SUBJECTS],
                          content=question.content)
            meta, _ = parse_meta_data(question.content)
            values.update(points=meta.get('知识点', ''), methods=meta.get('物理方法', ''),
                          tags=meta.get('标签', ''), difficulty=meta.get('难度星级', ''),
                          question_type=infer_question_type(question.content, question.subject))
            if st.button('🪄 重新 AI 分类与标注', key=prefix + '_retry'):
                try:
                    with st.spinner('正在分析这一道题…'):
                        _tex_classify_one(question)
                    meta, _ = parse_meta_data(question.content)
                    values.update(subject=question.subject.split('，'), points=meta.get('知识点', ''),
                                  methods=meta.get('物理方法', ''), tags=meta.get('标签', ''),
                                  difficulty=meta.get('难度星级', ''), content=question.content)
                    for field, value in values.items():
                        st.session_state[prefix + '_' + field] = value
                except Exception as exc:
                    st.error(f'AI 标注失败：{exc}')
            for field, value in values.items():
                st.session_state.setdefault(prefix + '_' + field, value)
            c_num, c_subj = st.columns([1, 3])
            question.number = c_num.text_input('题号', key=prefix + '_number').strip()
            subjects = c_subj.multiselect('知识板块（首个为主）', SUBJECTS, key=prefix + '_subject')
            question.subject = '，'.join(subjects) or '未分类'
            question_type = st.selectbox('题型（必选）', QUESTION_TYPES, key=prefix + '_question_type')
            points = st.text_input('知识点', key=prefix + '_points')
            methods = st.text_input('物理方法', key=prefix + '_methods')
            c_tags, c_diff = st.columns([3, 1])
            tags = c_tags.text_input('自由标签', key=prefix + '_tags')
            difficulty = c_diff.text_input('难度星级', key=prefix + '_difficulty')
            body = st.text_area('本题 LaTeX 源码', key=prefix + '_content', height=220)
            question.content = replace_problem_header(body, question.year, question.paper_type, question.paper,
                                                      question.number, question.subject)
            meta.update({'知识点': points, '物理方法': methods, '标签': tags, '难度星级': difficulty, '题型': question_type})
            question.content = inject_meta_data(question.content, meta)
            render_question_preview(question.content, show_title=True)


def _save_tex_workspace_questions(questions):
    logs = []
    from utils.csv_ops import get_next_id
    # Validate the whole selection before writing its first file.
    for question in questions:
        require_question_type(parse_meta_data(question.content)[0].get("题型"))
    for question in questions:
        try:
            filename = question.filename
            # Use the filename's canonical fields consistently in headers and index.
            year, paper_type, paper, number, subject = filename[:-4].split("-")
            path = os.path.join(CHAPTERS_DIR, subject.split("，")[0], year, filename)
            if os.path.exists(path):
                logs.append(dict(status="skip", file=filename, message="同名题目已存在，已跳过。"))
                continue
            content = replace_problem_header(question.content, year, paper_type, paper, number, subject)
            meta, _ = parse_meta_data(content)
            meta.update(ID=get_next_id(), **{"组卷引用次数": "0"})
            content = inject_meta_data(content, meta)
            ensure_dir(os.path.dirname(path))
            create_question_file(path, content, year, paper_type, paper, number, subject)
            logs.append(dict(status="success", file=filename, message="已保存", path=path))
        except QuestionDuplicateError:
            logs.append(dict(status="skip", file=question.filename, message="题干与已有题目完全相同，已跳过。"))
        except Exception as exc:
            logs.append(dict(status="error", file=question.filename, message=str(exc)))
    if any(log["status"] == "success" for log in logs):
        clear_statistics_cache()
    return logs


@st.cache_data(show_spinner=False, max_entries=12)
def _tex_pdf_page(pdf, page_number, scale):
    return render_pdf_page(pdf, page_number, scale)


#: 应用启动时清扫 TeX 录入预览残留：只删早于该年龄的目录，避免误删其它会话正在编译的目录。
TEX_ENTRY_STALE_CACHE_SECONDS = 600
#: 进入 TeX 录入页时清扫更久远的残留（保存/离开页面时会立即清空本次会话的目录）。
TEX_ENTRY_ROUTE_SWEEP_SECONDS = 3600


def _cleanup_tex_entry_temp(min_age_seconds: float = 0) -> int:
    """删除 TeX 录入预览生成的临时编译目录；返回清理数量，绝不抛异常。"""
    try:
        return cleanup_preview_cache(BASE_DIR, min_age_seconds=min_age_seconds)
    except Exception:
        return 0


def _sync_tex_entry_cache_lifecycle(selected_nav: str, tex_entry_nav_option: str) -> None:
    """录题页临时编译目录生命周期：进入页清扫残留，离开页立即清空。

    - 进入 TeX 录入页：记录页面激活状态，并清扫一小时前的历史残留；
    - 从 TeX 录入页切到其它页面：立即删除本页预览编译产生的全部临时目录；
    - 保存题目入题库后（page_tex_entry 内）也会调用本清理。
    预览 PDF 已保存在会话内存中，删除磁盘临时目录不影响右栏预览/下载。
    """
    was_active = bool(st.session_state.get("_tex_entry_page_active", False))
    if selected_nav == tex_entry_nav_option:
        st.session_state["_tex_entry_page_active"] = True
        if not st.session_state.get("_tex_entry_route_swept", False):
            _cleanup_tex_entry_temp(min_age_seconds=TEX_ENTRY_ROUTE_SWEEP_SECONDS)
            st.session_state["_tex_entry_route_swept"] = True
    elif was_active:
        _cleanup_tex_entry_temp()
        st.session_state["_tex_entry_page_active"] = False


def page_tex_entry():
    st.header("📄 TeX 录入")
    st.caption("左侧编辑源码、右侧实时编译预览；录入模式在左侧边栏「录入模式」中切换。草稿独立于 AI 录入。")
    if "tex_workspace_documents" not in st.session_state:
        st.session_state["tex_workspace_documents"] = [{"name": "草稿.tex", "source": ""}]
        st.session_state["tex_workspace_selected"] = 0
        st.session_state["tex_workspace_source"] = ""
    documents = st.session_state["tex_workspace_documents"]
    # 录入模式由侧边栏二级导航控制（与智能录题共用交互习惯，但各自独立存储）
    if st.session_state.get("tex_workspace_mode") not in ("单题录入", "批量试题录入", "同卷试题录入"):
        st.session_state["tex_workspace_mode"] = "单题录入"
    mode = st.session_state["tex_workspace_mode"]
    with st.expander("⚙️ 录入信息与编译设置", expanded=False):
        st.caption("单题 / 批量用于补全缺失参数；同卷模式将统一年份、类别和试卷名称。")
        cols = st.columns([1, 1.2, 2, 1, 2])
        year = cols[0].text_input("年份", value="2026", key="tex_workspace_year")
        paper_type = cols[1].selectbox("试卷类别", _editable_paper_type_options(), key="tex_workspace_type")
        paper = cols[2].text_input("试卷名称", value="物理模拟卷", key="tex_workspace_paper")
        number = cols[3].number_input("题号" if mode == "单题录入" else "起始题号", min_value=1, value=1, key="tex_workspace_number")
        subject = cols[4].selectbox("缺省知识板块", SUBJECTS, index=SUBJECTS.index("未分类"), key="tex_workspace_subject")
        end_number = st.text_input('终止题号', placeholder='可选，例如 10；填写后按题号范围校验题数', key='tex_workspace_end_number') if mode != '单题录入' else ''
        expected_count, range_error = None, ''
        if end_number.strip():
            if not end_number.strip().isdigit() or int(end_number) < number:
                range_error = '终止题号应为不小于起始题号的整数。'
                st.error(range_error)
            else:
                expected_count = int(end_number) - number + 1
                st.info(f'题号 {number}～{int(end_number)}，应拆为 {expected_count} 道独立题目。')
        c_engine, c_resource = st.columns([1, 3])
        engine = c_engine.selectbox("本地编译器", ["xelatex", "lualatex", "pdflatex"], key="tex_workspace_engine")
        resource_dir = c_resource.text_input("图片 / input 资源目录", value=BASE_DIR, key="tex_workspace_resource_dir")
        st.caption("题目片段自动使用题库模板；完整 document 文档保留自己的导言区。含中文的题库模板推荐 XeLaTeX。")
    defaults = dict(year=year.strip(), paper_type=paper_type, paper=paper.strip(), number=str(number), subject=subject)
    left, right = st.columns([1, 1], gap="medium")
    with left:
        with st.expander('📚 从题库载入待拆分题', expanded=False):
            paths = []
            for row in reversed(read_csv_index()):
                relative = row.get('相对文件路径', '')
                if relative and row.get('试卷类型') != 'WK':
                    path = os.path.abspath(os.path.join(CHAPTERS_DIR, relative))
                    if os.path.isfile(path):
                        paths.append(path)
                if len(paths) >= 200:
                    break
            if paths:
                st.selectbox('已保存题目', paths, format_func=lambda path: os.path.basename(path), key='tex_workspace_bank_file')
                st.button('载入并检测原合并题', key='tex_workspace_load_bank', on_click=_tex_load_bank_question)
                st.caption('载入后可逐题分类、修改并另存；原文件保留。')
        with st.expander("📂 上传 TeX 文件 / 新建草稿", expanded=False):
            uploads = st.file_uploader("上传 TeX 文件", type=["tex"], accept_multiple_files=True, key="tex_workspace_uploads")
            c_load, c_new, c_example = st.columns(3)
            if c_load.button("载入文件", key="tex_workspace_load", disabled=not uploads, use_container_width=True):
                try:
                    loaded = [{"name": f.name, "source": decode_tex(f.getvalue())} for f in uploads]
                    if len(documents) == 1 and not documents[0]["source"].strip():
                        documents[:] = loaded
                        selected = 0
                    else:
                        selected = len(documents)
                        documents.extend(loaded)
                    st.session_state["tex_workspace_selected"] = selected
                    st.session_state["tex_workspace_source"] = documents[selected]["source"]
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
            if c_new.button("新建草稿", key="tex_workspace_new", use_container_width=True):
                documents.append(dict(name=f"草稿{len(documents) + 1}.tex", source=""))
                st.session_state["tex_workspace_selected"] = len(documents) - 1
                st.session_state["tex_workspace_source"] = ""
                st.rerun()
            c_example.download_button("下载示例", TEX_ENTRY_EXAMPLE.encode("utf-8"), "题目录入示例.tex", "text/plain", key="tex_workspace_example", use_container_width=True)
        c_doc, c_compile = st.columns([2.3, 1], gap="small")
        with c_doc:
            selected = st.selectbox("当前文件", range(len(documents)), format_func=lambda i: f"{i + 1}. {documents[i]['name']}", key="tex_workspace_selected", on_change=_tex_document_changed)
        with c_compile:
            compile_clicked = st.button("▶ 编译预览", key="tex_workspace_compile", type="primary", use_container_width=True)
        st.markdown("<style>[data-testid='stTextArea'] textarea[aria-label='TeX 源码'] {font-family: Consolas, 'Cascadia Code', monospace; font-size:14px; line-height:1.55; tab-size:2;}</style>", unsafe_allow_html=True)
        source = st.text_area("TeX 源码", key="tex_workspace_source", height=600, on_change=_tex_editor_changed,
                              placeholder=TEX_ENTRY_EXAMPLE, label_visibility="collapsed")
        documents[selected]["source"] = source
        c_download, c_ai = st.columns(2)
        c_download.download_button("⬇ 下载当前源码", source.encode("utf-8"), documents[selected]["name"], "text/plain", key="tex_workspace_download_source", use_container_width=True)
        c_ai.button("📥 合并 AI 录入草稿", key="tex_workspace_merge_ai", on_click=_tex_merge_ai_draft, use_container_width=True)
        if st.session_state.get("tex_workspace_notice"):
            st.info(st.session_state.pop("tex_workspace_notice"))
        signature = hashlib.sha256((source + json.dumps(defaults, ensure_ascii=False) + mode + str(expected_count)).encode('utf-8')).hexdigest()
        c_detect, c_tag = st.columns([1.5, 1])
        ai_enabled = c_tag.checkbox('逐题 AI 分类及知识点标注', value=True, key='tex_workspace_ai_tagging')
        if c_detect.button('🔎 检测分题并生成逐题草稿', key='tex_workspace_detect', type='primary', disabled=bool(range_error), use_container_width=True):
            try:
                detecting_all = st.session_state.get('tex_workspace_save_scope') == '所有已载入文件'
                detect_defaults = dict(defaults)
                if detecting_all:
                    detect_defaults['number'] = str(number + sum(len(doc.get('review_questions', [])) or 1 for doc in documents[:selected]))
                with st.spinner('正在检测题目边界并逐题标注…'):
                    questions, errors = _tex_detect_review(documents[selected], detect_defaults, mode,
                                                         None if detecting_all else expected_count, ai_enabled)
                documents[selected].update(review_questions=questions, review_signature=signature,
                                            review_generation=documents[selected].get('review_generation', 0) + 1)
                for error in errors:
                    st.warning(error)
            except Exception as exc:
                st.error(f'分题检测失败：{exc}；原始源码仍保留。')
        _render_tex_question_review(documents[selected], selected, signature)
        c_scope, c_save = st.columns([1.4, 1])
        scope = c_scope.selectbox("保存范围", ["当前文件", "所有已载入文件"] if mode != "单题录入" else ["当前文件"], key="tex_workspace_save_scope", label_visibility="collapsed")
        if c_save.button("💾 保存到题库", key="tex_workspace_save", type="primary", use_container_width=True):
            try:
                pending = []
                for doc in documents if scope == "所有已载入文件" else [documents[selected]]:
                    doc_defaults = dict(defaults, number=str(number + len(pending)))
                    doc_signature = hashlib.sha256((doc['source'] + json.dumps(defaults, ensure_ascii=False) + mode + str(expected_count)).encode('utf-8')).hexdigest()
                    if doc.get('review_signature') == doc_signature:
                        pending.extend(doc['review_questions'])
                    else:
                        detected = _tex_workspace_questions(doc["source"], doc["name"], doc_defaults, mode)
                        raise ValueError(f'{doc["name"]} 检测到 {len(detected)} 道题。请先点击“检测分题并生成逐题草稿”，完成分类、标注与人工检查后再保存。')
                if range_error:
                    raise ValueError(range_error)
                if expected_count and len(pending) != expected_count:
                    raise ValueError(f'题号范围应保存 {expected_count} 道题，当前只有 {len(pending)} 道。请先检测分题。')
                if expected_count and {q.number for q in pending} != {str(n) for n in range(number, number + expected_count)}:
                    raise ValueError('逐题题号不符合起始～终止范围，请修改题号或录入范围。')
                if any(not q.number for q in pending) or len({(q.year, q.paper_type, q.paper, q.number) for q in pending}) != len(pending):
                    raise ValueError('题号为空或有重复，请在逐题草稿中修改。')
                st.session_state["tex_workspace_save_logs"] = _save_tex_workspace_questions(pending)
                # 题目已入库：立即删除本次录题产生的临时编译目录（预览 PDF 仍在会话内存中）
                _cleanup_tex_entry_temp()
            except ValueError as exc:
                st.error(str(exc))
        logs = st.session_state.get("tex_workspace_save_logs", [])
        if logs:
            saved = [log for log in logs if log["status"] == "success"]
            st.info(f"上次保存：{len(saved)} 道成功，{sum(log['status'] == 'skip' for log in logs)} 道跳过，{sum(log['status'] == 'error' for log in logs)} 道失败。")
            with st.expander("保存日志"):
                for log in logs:
                    getattr(st, "success" if log["status"] == "success" else "error" if log["status"] == "error" else "warning")(f"{log['file']}：{log['message']}")
            if saved:
                def open_tex_saved():
                    st.session_state.update(main_sidebar_radio="🔍\n全局浏览\n与编辑", adv_search_active=False,
                                            recent_saved_active=True, recent_saved_paths=[log["path"] for log in saved])
                st.button("在题库中查看已保存题目 ↗", key="tex_workspace_browse", on_click=open_tex_saved)
    fingerprint = hashlib.sha256((source + json.dumps(defaults, ensure_ascii=False) + engine + resource_dir + mode).encode("utf-8")).hexdigest()
    if compile_clicked:
        if not source.strip():
            documents[selected]["compile_result"] = dict(ok=False, error="请先输入或上传 TeX 源码。", log="")
        else:
            compile_source = source
            if mode == "同卷试题录入":
                compile_source = re.sub(
                    r'\\begin\{problem\}\{(.*?)\}\{(.*?)\}\{(.*?)\}\{(.*?)\}\{(.*?)\}',
                    lambda m: replace_problem_header(m[0], defaults["year"], defaults["paper_type"], defaults["paper"], m[4], m[5]),
                    source, flags=re.DOTALL,
                )
            with st.spinner(f"正在调用本地 {engine} 编译…"):
                result = compile_tex_preview(compile_source, BASE_DIR, defaults, engine, resource_dir)
            documents[selected]["compile_result"] = result
            if result["ok"]:
                documents[selected]["preview"] = dict(pdf=result["pdf"], fingerprint=fingerprint, engine=engine)
    with right:
        st.markdown("#### PDF 编译预览")
        preview = documents[selected].get("preview")
        result = documents[selected].get("compile_result", {})
        if result and not result.get("ok"):
            st.error(result.get("error", "编译失败"))
            if result.get("diagnosis"):
                with st.expander("🩺 编译诊断与修复建议", expanded=True):
                    st.code(result["diagnosis"], language="text")
        if preview:
            if preview["fingerprint"] != fingerprint:
                st.warning("源码或编译设置已改变，下方为上次成功编译的 PDF。点击“编译预览”更新。")
            else:
                st.caption(f"本地 {preview['engine']} 编译成功")
            st.download_button("⬇ 下载 PDF", preview["pdf"], "题目预览.pdf", "application/pdf", key="tex_workspace_download_pdf")
            try:
                import fitz
                with fitz.open(stream=preview["pdf"], filetype="pdf") as pdf:
                    page_count = len(pdf)
                key = f"tex_workspace_page_{selected}"
                if st.session_state.get(key, 1) > page_count:
                    st.session_state[key] = 1
                c_page, c_zoom = st.columns([1, 2])
                page_number = c_page.number_input(f"页码（共 {page_count} 页）", min_value=1, max_value=page_count, value=1, key=key)
                scale = c_zoom.slider("缩放", min_value=0.8, max_value=2.0, value=1.2, step=0.1, key="tex_workspace_zoom")
                png, _ = _tex_pdf_page(preview["pdf"], page_number, scale)
                with st.container(height=690):
                    st.image(png, width=int(595 * scale))
            except Exception as exc:
                st.error(f"PDF 页面显示失败：{exc}；可下载 PDF 查看。")
        else:
            with st.container(height=690):
                st.info("上传或粘贴源码后，点击左侧“编译预览”。PDF 在本机生成，编译错误会显示文件位置和行号。")
        if result.get("log"):
            with st.expander("编译日志", expanded=not result.get("ok")):
                st.code(result["log"], language="text")


def page_entry(force_single_mode: bool = False, cloze_mode: bool = False):
    st.markdown('<span id="mc-entry-page-anchor"></span>', unsafe_allow_html=True)
    if force_single_mode:
        # 挖空题生成页保持原来的布局：标题独占一行，识别素材工具在下方
        st.header("🧩 挖空题生成" if cloze_mode else "📝 AI 录入")
    
    # 初始化 Session State
    if "entry_year" not in st.session_state: st.session_state["entry_year"] = "2024"
    if "entry_p_type" not in st.session_state: st.session_state["entry_p_type"] = "G"
    if "entry_subject_multi" not in st.session_state: st.session_state["entry_subject_multi"] = []
    if "entry_number" not in st.session_state: st.session_state["entry_number"] = "1"
    if "entry_paper_name" not in st.session_state: st.session_state["entry_paper_name"] = "新高考I卷"
    if "entry_content" not in st.session_state: st.session_state["entry_content"] = ""
    if "batch_content" not in st.session_state: st.session_state["batch_content"] = ""
    if "entry_subject_user_locked" not in st.session_state: st.session_state["entry_subject_user_locked"] = False
    
    if force_single_mode:
        mode = "单题录入"
        if cloze_mode:
            st.session_state["entry_p_type"] = "WK"
            cloze_options = ["简单定义基础计算挖空", "基础推导与题意读取提炼挖空", "关键思路抽象推导挖空"]
            if st.session_state.get("cloze_generation_type") not in cloze_options:
                st.session_state["cloze_generation_type"] = cloze_options[0]
            st.markdown("""
            <style>
            div[class*="st-key-btn_open_cloze_library"] button {
                min-height: 48px !important;
                font-size: 16px !important;
                font-weight: 700 !important;
                white-space: nowrap !important;
            }
            </style>
            """, unsafe_allow_html=True)
            cloze_mode_col, cloze_library_col = st.columns([3, 1.4])
            with cloze_mode_col:
                st.radio("挖空要求", cloze_options, key="cloze_generation_type", horizontal=True)
            with cloze_library_col:
                if st.button("📚 挖空题库", key="btn_open_cloze_library", use_container_width=True):
                    st.session_state["tools_subpage"] = "cloze_library"
                    st.session_state["adv_search_active"] = False
                    for key in ("paper_type", "paper_year", "paper_name", "selected_q_idx"):
                        st.session_state.pop(key, None)
                    _clear_advanced_search_result_cache()
                    st.rerun()
            render_cloze_source_picker()
            st.caption("选择普通题库中的原题后生成挖空版本；生成结果可在下方继续人工修改、预览与保存。")
    else:
        # 录入模式已移到左侧边栏二级导航（🙋 单题 / 📚 批量 / 📑 同卷）
        if "entry_mode_sidebar" not in st.session_state:
            st.session_state["entry_mode_sidebar"] = "单题录入"
        mode = st.session_state.get("entry_mode_sidebar") or "单题录入"

    # 批量/同卷模式的「识别结果写入方式」并入下方来源工具的同一行，不再单独占一行
    _show_batch_merge_in_toolbar = (not force_single_mode) and mode != "单题录入"
    
    # 顶部：来源识别条（上传 / 粘贴 / 识别），整行铺开，压缩高度
    source_scope = st.container()
    st.markdown(
        """
        <style>
          /* ===== 顶部工具行：标题 / 添加识别素材 / 上传 / 粘贴 / 写入方式 / 识别 / 清空 / 提示 同一行 ===== */
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) {
            align-items: center !important;
            flex-wrap: nowrap !important;
            justify-content: space-between !important;
            gap: 0.7rem !important;
          }
          /* 行内的页面大标题：与工具控件同基线，右侧加一道细竖线分隔 */
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) h2 {
            margin: 0 !important;
            padding: 0 !important;
            line-height: 1.1 !important;
            font-size: 1.95rem !important;
            font-weight: 700 !important;
            letter-spacing: -0.015em !important;
            white-space: nowrap !important;
            color: var(--mc-text) !important;
          }
          /* 列宽：标题 / 小标题 / 写入方式固定占位，其余列按比例分配并均匀铺满整行 */
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) > div[data-testid="column"] {
            min-width: 0 !important;
          }
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) > div[data-testid="column"]:has(h1),
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) > div[data-testid="column"]:has(h2) {
            flex: 0 0 200px !important;
            width: 200px !important;
            padding-right: 0.6rem !important;
            border-right: 1px solid rgba(214, 202, 185, 0.95) !important;
          }
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) > div[data-testid="column"]:has(div[data-testid="stRadio"]) {
            flex: 0 0 236px !important;
            width: 236px !important;
          }
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) > div[data-testid="column"]:has(.mc-entry-strip-title) {
            flex: 0 0 96px !important;
            width: 96px !important;
          }
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) .mc-entry-strip-title {
            font-size: 0.78rem !important;
            font-weight: 700 !important;
            color: var(--mc-muted) !important;
            line-height: 1.15 !important;
            white-space: nowrap !important;
            margin: 0 !important;
            padding: 0 0.1rem !important;
          }
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) div[data-testid="stMarkdownContainer"]:has(.mc-entry-strip-title) {
            margin: 0 !important;
          }
          /* 说明小字（提示）：同一行内单行显示，空间不足时省略号收尾，悬停可看全文 */
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) .mc-entry-strip-hint {
            margin: 0 !important;
            font-size: 0.73rem !important;
            line-height: 1.2 !important;
            color: var(--mc-muted) !important;
            white-space: nowrap !important;
            overflow: hidden !important;
            text-overflow: ellipsis !important;
          }
          /* 批量/同卷行控件更多，提示字再收一档以保住整行 */
          div[data-testid="stHorizontalBlock"]:has([data-testid="stRadio"]) .mc-entry-strip-hint {
            font-size: 0.68rem !important;
          }
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) [data-testid="stCaptionContainer"] {
            margin: 0 !important;
          }
          /* 上传区：32px 单行胶囊，图标 + 说明 + Browse files 横排 */
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) [data-testid="stFileUploaderDropzone"] {
            min-height: 32px !important;
            padding: 0.1rem 0.4rem !important;
            gap: 0.35rem !important;
            align-items: center !important;
            flex-wrap: nowrap !important;
            border-radius: 10px !important;
          }
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) [data-testid="stFileUploaderDropzoneInstructions"] {
            display: flex !important;
            flex-direction: row !important;
            align-items: center !important;
            gap: 0.4rem !important;
            min-width: 0 !important;
            flex: 1 1 auto !important;
            overflow: hidden !important;
            margin: 0 !important;
          }
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) [data-testid="stFileUploaderDropzoneInstructions"] > span {
            display: inline-flex !important;
            align-items: center !important;
            flex: 0 0 auto !important;
          }
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) [data-testid="stFileUploaderDropzoneInstructions"] svg {
            width: 17px !important;
            height: 17px !important;
          }
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) [data-testid="stFileUploaderDropzoneInstructions"] > div {
            display: flex !important;
            flex-direction: row !important;
            align-items: baseline !important;
            gap: 0.45rem !important;
            flex: 1 1 auto !important;
            min-width: 0 !important;
            overflow: hidden !important;
          }
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) [data-testid="stFileUploaderDropzoneInstructions"] span {
            flex: 0 1 auto !important;
            min-width: 0 !important;
            white-space: nowrap !important;
            overflow: hidden !important;
            text-overflow: ellipsis !important;
            font-size: 0.74rem !important;
            line-height: 1.2 !important;
          }
          /* 200MB/格式这类明细收进 hover 提示，工具行不再多占宽度 */
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) [data-testid="stFileUploaderDropzoneInstructions"] small {
            display: none !important;
          }
          /* 窄窗口只留云朵图标 + Browse files，把宽度让给按钮和提示 */
          @media (max-width: 1500px) {
            div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) [data-testid="stFileUploaderDropzoneInstructions"] > div {
              display: none !important;
            }
          }
          /* 批量/同卷工具行控件更多，上传区固定为「云朵图标 + Browse files」 */
          div[data-testid="stHorizontalBlock"]:has([data-testid="stRadio"]) [data-testid="stFileUploaderDropzoneInstructions"] > div {
            display: none !important;
          }
          /* Browse files：与工具行按钮同风格的小号胶囊 */
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) [data-testid="stFileUploaderDropzone"] button {
            flex: 0 0 auto !important;
            min-height: 24px !important;
            height: 24px !important;
            padding: 0 0.6rem !important;
            font-size: 0.7rem !important;
            border-radius: 7px !important;
          }
          /* 上传控件原生的已选文件列表会另起一行撑高工具栏；队列在下方「已添加」面板里统一管理 */
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) div[data-testid="stFileUploader"] > div:has([data-testid="stFileUploaderFile"]) {
            display: none !important;
          }
          /* 批量/同卷：写入方式（追加 / 替换）与工具栏同一行，标签与选项横向排列 */
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) div[data-testid="stRadio"] {
            display: flex !important;
            flex-direction: row !important;
            align-items: center !important;
            flex-wrap: nowrap !important;
            gap: 0.45rem !important;
            margin: 0 !important;
            padding: 0 !important;
          }
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) div[data-testid="stRadio"] > label[data-testid="stWidgetLabel"] {
            margin: 0 !important;
            padding: 0 !important;
            min-height: 0 !important;
            flex: 0 0 auto !important;
          }
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) div[data-testid="stRadio"] > label[data-testid="stWidgetLabel"] p {
            margin: 0 !important;
            font-size: 0.76rem !important;
            font-weight: 700 !important;
            color: var(--mc-muted) !important;
            line-height: 1.2 !important;
            white-space: nowrap !important;
          }
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) div[data-testid="stRadio"] div[role="radiogroup"] {
            display: flex !important;
            flex-direction: row !important;
            align-items: center !important;
            flex-wrap: nowrap !important;
            gap: 0.55rem !important;
            margin: 0 !important;
          }
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) div[data-testid="stRadio"] div[role="radiogroup"] > label {
            margin: 0 !important;
            padding: 0 !important;
            min-height: 0 !important;
            gap: 0.3rem !important;
          }
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) div[data-testid="stRadio"] div[role="radiogroup"] > label p {
            margin: 0 !important;
            font-size: 0.76rem !important;
            line-height: 1.2 !important;
            white-space: nowrap !important;
          }
          /* 批量工具行需要给「写入方式」让位：只保留一行上传提示，隐藏 200MB 明细 */
          div[data-testid="stHorizontalBlock"]:has([data-testid="stRadio"]) [data-testid="stFileUploaderDropzoneInstructions"] small {
            display: none !important;
          }
          /* 工具行里「写入方式」小标题不显示（radiogroup 仍带 aria-label，读屏语义完整） */
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) div[data-testid="stRadio"] > label[data-testid="stWidgetLabel"] {
            display: none !important;
          }
          /* 工具行按钮：32px 精致小号（统一圆角、细边、轻柔投影，主按钮暖陶实心） */
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) div[data-testid="stButton"] button {
            min-height: 30px !important;
            height: 30px !important;
            font-size: 0.75rem !important;
            font-weight: 600 !important;
            padding: 0 0.65rem !important;
            border-radius: 9px !important;
            border: 1px solid rgba(188, 172, 150, 0.72) !important;
            background: var(--mc-surface) !important;
            color: var(--mc-text) !important;
            box-shadow: 0 1px 2px rgba(120, 95, 60, 0.06), inset 0 1px 0 rgba(255, 255, 255, 0.65) !important;
            transition: background 0.16s ease, border-color 0.16s ease, box-shadow 0.16s ease !important;
            white-space: nowrap !important;
            overflow: hidden !important;
          }
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) div[data-testid="stButton"] button:hover {
            background: var(--mc-surface-soft) !important;
            border-color: var(--mc-accent) !important;
            color: var(--mc-accent-dark) !important;
            box-shadow: 0 3px 10px rgba(204, 120, 92, 0.15) !important;
            transform: none !important;
          }
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) div[data-testid="stButton"] button[kind="primary"] {
            background: var(--mc-action-bg) !important;
            border-color: var(--mc-action-border) !important;
            color: var(--mc-action-text) !important;
            box-shadow: 0 2px 8px rgba(204, 120, 92, 0.24) !important;
          }
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) div[data-testid="stButton"] button[kind="primary"]:hover {
            background: var(--mc-action-bg-hover) !important;
            border-color: var(--mc-action-bg-hover) !important;
            color: var(--mc-action-text) !important;
            box-shadow: 0 4px 12px rgba(204, 120, 92, 0.3) !important;
          }
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) div[data-testid="stButton"] button[kind="primary"] p {
            color: var(--mc-action-text) !important;
          }
          /* 窗口较窄时长标签以省略号收尾，不撑破按钮 */
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) div[data-testid="stButton"] button > div,
          div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"]) div[data-testid="stButton"] button p {
            min-width: 0 !important;
            max-width: 100% !important;
            overflow: hidden !important;
            text-overflow: ellipsis !important;
            white-space: nowrap !important;
          }
          /* 队列缩略图缩小 */
          div[data-testid="stVerticalBlock"]:has(#mc-entry-source-anchor) div[data-testid="stImage"] img {
            max-height: 96px !important;
            width: auto !important;
          }
          /* 来源区内的分隔线收窄 */
          div[data-testid="stVerticalBlock"]:has(#mc-entry-source-anchor) hr {
            margin: 0.35rem 0 !important;
          }
        </style>
        <div id="mc-entry-source-anchor"></div>
        """,
        unsafe_allow_html=True,
    )
    col_left = source_scope

    # 主体：题目详情（左）与实时预览（右）并排
    if mode == "单题录入":
        col_mid, col_right = st.columns([1.22, 1], gap="large")
    else:
        col_mid, col_right = st.columns([1, 0.001], gap="large")
    
    # === 左侧：AI 识别区 ===
    with col_left:
        inject_custom_css() # 注入样式

        # 常规 AI 录入页：页面标题 + 识别素材工具 + 说明小字 全部收进同一行，横向均匀分布
        _header_in_row = not force_single_mode
        _strip_hint = "提示: 如果 AI 响应时间过长，请直接刷新页面以中断。"
        # 队列为空时「识别 / 清空」不占位，整行保持紧凑且间距均匀
        _has_queue_pre = bool(st.session_state.get("ocr_queue") or st.session_state.get("pdf_queue"))
        if _header_in_row:
            if _show_batch_merge_in_toolbar:
                _strip_ratios = (
                    [1.2, 0.5, 1.0, 0.95, 1.3, 1.35, 0.6, 1.7] if _has_queue_pre
                    else [1.2, 0.5, 1.2, 1.0, 1.2, 1.85]
                )
            else:
                _strip_ratios = (
                    [1.2, 0.6, 1.9, 0.62, 0.95, 0.55, 1.65] if _has_queue_pre
                    else [1.2, 0.6, 1.9, 0.7, 1.65]
                )
            _n_cols = (8 if _show_batch_merge_in_toolbar else 7) if _has_queue_pre else (
                6 if _show_batch_merge_in_toolbar else 5)
            _strip_cols = st.columns(
                _strip_ratios, gap="small", vertical_alignment="center"
            )
            if _show_batch_merge_in_toolbar:
                c_head, c_title, c_up, c_paste, c_mode = _strip_cols[:5]
                with c_mode:
                    st.radio(
                        "写入方式",
                        ["追加到现有题目", "替换现有题目"],
                        horizontal=True,
                        key="entry_batch_merge",
                    )
                if _has_queue_pre:
                    c_go, c_clear, c_hint = _strip_cols[5:]
                else:
                    c_go = None
                    c_clear = None
                    c_hint = _strip_cols[5]
            else:
                c_head, c_title, c_up, c_paste = _strip_cols[:4]
                c_mode = None
                if _has_queue_pre:
                    c_go, c_clear, c_hint = _strip_cols[4:]
                else:
                    c_go = None
                    c_clear = None
                    c_hint = _strip_cols[4]
            with c_head:
                st.header("📝 AI 录入")
            with c_hint:
                st.markdown(
                    f'<div class="mc-entry-strip-hint" title="{_strip_hint}">{_strip_hint}</div>',
                    unsafe_allow_html=True,
                )
        else:
            # 挖空题生成页：标题已单独占行，工具栏保持原样
            c_title, c_up, c_paste, c_go, c_clear = st.columns(
                [0.7, 2.6, 1.14, 1.02, 0.5], gap="small", vertical_alignment="center"
            )
            c_mode = None
            c_hint = None
        with c_title:
            st.markdown('<div class="mc-entry-strip-title">📥 添加识别素材</div>', unsafe_allow_html=True)
        
        # 确保 Image 模块可用
        try:
            from PIL import Image
        except ImportError:
            st.error("缺少 PIL 库，请安装 pillow")
            return

        # 初始化上传队列
        if "ocr_queue" not in st.session_state:
            st.session_state["ocr_queue"] = []
        if "pdf_queue" not in st.session_state:
            st.session_state["pdf_queue"] = []
        if "entry_upload_prev_files" not in st.session_state:
            st.session_state["entry_upload_prev_files"] = []

        max_pdf_files = 3
        max_image_files = 5
        pdf_ocr_batch_size = 2

        def _upload_file_id(file_obj):
            return f"{file_obj.name}_{file_obj.size}"

        def _format_upload_size(size_bytes):
            if size_bytes >= 1024 * 1024:
                return f"{size_bytes / (1024 * 1024):.2f} MB"
            return f"{size_bytes / 1024:.1f} KB"

        def _get_pdf_page_count(pdf_bytes):
            try:
                import fitz
                with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf_doc:
                    return pdf_doc.page_count
            except Exception:
                return None

        # 1. 统一文件上传区域（紧凑一行：标题 / 上传 / 粘贴 / 识别 / 清空）
        _queue_total = len(st.session_state.get("ocr_queue", [])) + len(st.session_state.get("pdf_queue", []))
        _image_count_pre = len(st.session_state.get("ocr_queue", []))
        _pdf_page_count_pre = sum(
            max(0, (item.get("page_end") or 0) - (item.get("page_start") or 0) + 1)
            for item in st.session_state.get("pdf_queue", [])
            if item.get("page_count") and (item.get("page_start") or 0) <= (item.get("page_end") or 0)
        )
        _action_label_pre = "🚀 识别所选图片和 PDF" if _image_count_pre and _pdf_page_count_pre else (
            "🚀 识别所有图片" if _image_count_pre else "🚀 识别所选 PDF 页面"
        )
        with c_up:
            uploaded_files = st.file_uploader(
                "📁 上传图片或 PDF",
                type=["pdf", "png", "jpg", "jpeg"],
                key="entry_file_uploader",
                accept_multiple_files=True,
                label_visibility="collapsed",
                help="拖拽文件到此处或点击选择。图片最多 5 张，PDF 最多 3 个。",
            )

        if uploaded_files:
            current_file_ids = [_upload_file_id(f) for f in uploaded_files]
            prev_file_ids = st.session_state["entry_upload_prev_files"]
            queued_pdf_ids = {item["id"] for item in st.session_state["pdf_queue"]}
            ignored_pdf_count = 0
            ignored_image_count = 0
            added_pdf_count = 0
            added_image_count = 0

            for uploaded_file in uploaded_files:
                file_id = _upload_file_id(uploaded_file)
                if file_id in prev_file_ids:
                    continue

                extension = os.path.splitext(uploaded_file.name)[1].lower()
                if extension == ".pdf":
                    if file_id in queued_pdf_ids or len(st.session_state["pdf_queue"]) >= max_pdf_files:
                        ignored_pdf_count += 1
                        continue
                    pdf_bytes = uploaded_file.getvalue()
                    page_count = _get_pdf_page_count(pdf_bytes)
                    st.session_state["pdf_queue"].append({
                        "id": file_id,
                        "name": uploaded_file.name,
                        "size": uploaded_file.size,
                        "bytes": pdf_bytes,
                        "page_count": page_count,
                        "page_start": 1 if page_count else None,
                        "page_end": page_count,
                        "page_range": f"1-{page_count}" if page_count else "",
                    })
                    queued_pdf_ids.add(file_id)
                    added_pdf_count += 1
                else:
                    if len(st.session_state["ocr_queue"]) >= max_image_files:
                        ignored_image_count += 1
                        continue
                    try:
                        img = Image.open(uploaded_file)
                        img.load()
                        st.session_state["ocr_queue"].append(img.copy())
                        added_image_count += 1
                    except Exception as e:
                        st.error(f"图片 {uploaded_file.name} 读取失败: {e}")

            st.session_state["entry_upload_prev_files"] = current_file_ids
            if added_pdf_count or added_image_count:
                added_parts = []
                if added_image_count:
                    added_parts.append(f"{added_image_count} 张图片")
                if added_pdf_count:
                    added_parts.append(f"{added_pdf_count} 个 PDF")
                st.toast(f"已添加 {'、'.join(added_parts)}", icon="✅")
            if ignored_image_count:
                st.warning(f"图片最多上传 {max_image_files} 张，已忽略 {ignored_image_count} 张图片。")
            if ignored_pdf_count:
                st.warning(f"PDF 最多上传 {max_pdf_files} 个，已忽略 {ignored_pdf_count} 个文件。")
        else:
            st.session_state["entry_upload_prev_files"] = []

        if st.session_state["pdf_queue"]:
          with st.expander(f"📄 已添加的 PDF（{len(st.session_state['pdf_queue'])} 个，点开设置页码）", expanded=False):
            c_pdf_header, c_pdf_clear = st.columns([3, 1])
            with c_pdf_header:
                st.write(f"当前 PDF: {len(st.session_state['pdf_queue'])}/{max_pdf_files} 个")
            with c_pdf_clear:
                if st.button("🗑️ 清空", key="clear_pdf_queue", use_container_width=True):
                    st.session_state["pdf_queue"] = []
                    st.rerun()

            for i, pdf_item in enumerate(st.session_state["pdf_queue"]):
                st.caption(f"{i + 1}. {pdf_item['name']} · {_format_upload_size(pdf_item['size'])}")
                page_count = pdf_item.get("page_count")
                if page_count:
                    default_start = pdf_item.get("page_start") or 1
                    default_end = pdf_item.get("page_end") or page_count
                    c_range_label, c_page_start, c_range_separator, c_page_end, c_page_count, c_pdf_del = st.columns([1.5, 1, 0.3, 1, 0.7, 0.8])
                    with c_range_label:
                        st.caption("扫描页面范围")
                    with c_page_start:
                        page_start = st.number_input(
                            "起始页",
                            min_value=1,
                            max_value=page_count,
                            value=min(max(default_start, 1), page_count),
                            step=1,
                            key=f"pdf_page_start_{pdf_item['id']}",
                            label_visibility="collapsed",
                        )
                    with c_range_separator:
                        st.markdown(
                            "<div style='height: 2.35rem; display: flex; align-items: center; justify-content: center; font-size: 18px; color: #4b5563;'>—</div>",
                            unsafe_allow_html=True,
                        )
                    with c_page_end:
                        page_end = st.number_input(
                            "结束页",
                            min_value=1,
                            max_value=page_count,
                            value=min(max(default_end, 1), page_count),
                            step=1,
                            key=f"pdf_page_end_{pdf_item['id']}",
                            label_visibility="collapsed",
                        )
                    with c_page_count:
                        st.caption(f"最大 {page_count} 页")
                    with c_pdf_del:
                        delete_pdf = st.button("删除", key=f"del_pdf_{i}", use_container_width=True)
                    if page_start > page_end:
                        st.warning("起始页不能大于结束页。")
                    else:
                        pdf_item["page_start"] = page_start
                        pdf_item["page_end"] = page_end
                        pdf_item["page_range"] = f"{page_start}-{page_end}"
                else:
                    st.caption("未能读取 PDF 页数，后续可重新上传该文件。")
                    delete_pdf = st.button("删除", key=f"del_pdf_{i}")

                if delete_pdf:
                    st.session_state["pdf_queue"].pop(i)
                    st.rerun()

        with c_paste:
            def _read_clipboard_image_candidates():
                if not ImageGrab:
                    st.error("缺少 PIL 库")
                    return []
                clipboard_content = ImageGrab.grabclipboard()
                candidates = []
                if isinstance(clipboard_content, Image.Image):
                    candidates.append({"label": "剪贴板图片 1", "image": clipboard_content.copy()})
                elif isinstance(clipboard_content, list):
                    for item in clipboard_content:
                        if isinstance(item, str) and os.path.isfile(item):
                            try:
                                img = Image.open(item)
                                img.load()
                                candidates.append({"label": os.path.basename(item), "image": img.copy()})
                            except Exception:
                                pass
                return candidates

            def _append_clipboard_first_image():
                try:
                    candidates = _read_clipboard_image_candidates()
                except Exception as e:
                    st.error(f"剪贴板读取失败: {e}")
                    return
                if not candidates:
                    st.warning("剪贴板中没有图片或支持的图片文件")
                    return
                if len(st.session_state["ocr_queue"]) >= max_image_files:
                    st.warning(f"图片最多上传 {max_image_files} 张。")
                    return
                st.session_state["ocr_queue"].append(candidates[0]["image"])
                st.toast("已从剪贴板添加 1 张图片", icon="✅")
                st.rerun()

            if st.button("📋 粘贴图片", use_container_width=True, help="粘贴剪贴板里的首张图片"):
                _append_clipboard_first_image()

        if c_go is not None:
            with c_go:
                if _queue_total:
                    _run_ocr_clicked = st.button(_action_label_pre, type="primary", use_container_width=True)
                else:
                    _run_ocr_clicked = False
        else:
            _run_ocr_clicked = False
        if c_clear is not None:
            with c_clear:
                if _queue_total:
                    if st.button("🗑️ 清空", key="clear_all_sources", use_container_width=True):
                        st.session_state["ocr_queue"] = []
                        st.session_state["pdf_queue"] = []
                        st.session_state["entry_upload_prev_files"] = []
                        st.rerun()

        if _queue_total:
            st.caption(f"本次将识别 {_image_count_pre} 张图片、{_pdf_page_count_pre} 页 PDF；单题模式会合并识别，批量模式每 2 页分批识别。")

        image_count = _image_count_pre
        pdf_page_count = _pdf_page_count_pre
        if _run_ocr_clicked:
            total_source_pages = image_count + pdf_page_count
            if mode == "单题录入" and total_source_pages > max_image_files:
                st.warning("单题录入一次最多识别 5 张图片或页面；多页 PDF 请切换到“批量试题录入”或“同卷试题录入”。")
                st.stop()

            ocr_batches = []
            batch_labels = []
            single_mode_images = []

            if st.session_state["ocr_queue"]:
                if mode == "单题录入":
                    single_mode_images.extend(st.session_state["ocr_queue"])
                else:
                    ocr_batches.append(list(st.session_state["ocr_queue"]))
                    batch_labels.append(f"图片 1-{image_count}")

            for pdf_item in st.session_state["pdf_queue"]:
                page_start = pdf_item.get("page_start")
                page_end = pdf_item.get("page_end")
                if not pdf_item.get("page_count"):
                    st.error(f"PDF {pdf_item['name']} 无法读取页数，不能识别。")
                    continue
                if not page_start or not page_end or page_start > page_end:
                    st.error(f"PDF {pdf_item['name']} 的扫描页面范围无效。")
                    continue

                selected_pages = range(page_start, page_end + 1)
                with st.spinner(f"正在渲染 PDF：{pdf_item['name']}（第 {page_start}-{page_end} 页）..."):
                    pdf_images, render_error = render_pdf_pages_to_images(pdf_item["bytes"], selected_pages)
                if render_error:
                    st.error(f"PDF {pdf_item['name']}：{render_error}")
                    continue

                if mode == "单题录入":
                    single_mode_images.extend(pdf_images)
                else:
                    for offset in range(0, len(pdf_images), pdf_ocr_batch_size):
                        page_batch = pdf_images[offset:offset + pdf_ocr_batch_size]
                        first_page = page_start + offset
                        last_page = first_page + len(page_batch) - 1
                        ocr_batches.append(page_batch)
                        batch_labels.append(f"{pdf_item['name']} 第 {first_page}-{last_page} 页")

            if mode == "单题录入" and single_mode_images:
                ocr_batches.append(single_mode_images)
                batch_labels.append(f"已选图片与 PDF 页面（共 {len(single_mode_images)} 页/张）")

            if not ocr_batches:
                st.warning("没有可识别的图片或有效 PDF 页面。")
            else:
                ocr_results = []
                progress = st.progress(0)
                status = st.empty()
                total_batches = len(ocr_batches)

                for batch_index, (image_batch, batch_label) in enumerate(zip(ocr_batches, batch_labels), start=1):
                    status.text(f"正在识别 {batch_label}（{batch_index}/{total_batches}）")
                    result = ocr_image_to_latex(
                        images=image_batch,
                        max_image_size=1600,
                        max_tokens=8192,
                        spinner_text=f"🤖 AI 正在识别 {batch_label}...",
                    )
                    if result.startswith("❌"):
                        status.empty()
                        progress.empty()
                        st.error(f"{batch_label} 识别失败：{result}")
                        break
                    ocr_results.append(result.strip())
                    progress.progress(batch_index / total_batches)
                else:
                    status.empty()
                    progress.empty()
                    process_ocr_result("\n\n".join(ocr_results), mode)

        # 3. 图片队列展示与管理
        if st.session_state["ocr_queue"]:
          with st.expander(f"🖼️ 已添加的图片（{len(st.session_state['ocr_queue'])} 张，点开可排序/删除/放大）", expanded=False):
            c_q_header, c_q_clear = st.columns([3, 1])
            with c_q_header:
                st.write(f"当前队列: {len(st.session_state['ocr_queue'])}/5 张")
            with c_q_clear:
                if st.button("🗑️ 清空", key="clear_queue", use_container_width=True):
                    st.session_state["ocr_queue"] = []
                    st.rerun()
            
            for i, img in enumerate(st.session_state["ocr_queue"]):
                st.caption(f"图片 {i+1}")
                st.image(img, width=190)
                # 当前区域已位于 col_left 中，操作按钮只再嵌套一层列。
                c_btn1, c_btn2, c_btn3, c_btn4 = st.columns(4)
                with c_btn1:
                    if i > 0:
                        if st.button("⬆️", key=f"mv_up_{i}", help="前移"):
                            st.session_state["ocr_queue"][i], st.session_state["ocr_queue"][i-1] = st.session_state["ocr_queue"][i-1], st.session_state["ocr_queue"][i]
                            st.rerun()
                with c_btn2:
                    if i < len(st.session_state["ocr_queue"]) - 1:
                        if st.button("⬇️", key=f"mv_down_{i}", help="后移"):
                            st.session_state["ocr_queue"][i], st.session_state["ocr_queue"][i+1] = st.session_state["ocr_queue"][i+1], st.session_state["ocr_queue"][i]
                            st.rerun()
                with c_btn3:
                    if st.button("🗑️", key=f"del_{i}", help="删除"):
                        st.session_state["ocr_queue"].pop(i)
                        st.rerun()
                with c_btn4:
                    if st.button("🔍", key=f"zoom_{i}", help="放大"):
                        zoom_image(img)
            


        # 增加手动中断提示（常规 AI 录入页已把提示并入顶部工具行）
        if force_single_mode:
            st.caption("提示: 如果 AI 响应时间过长，请直接刷新页面以中断。")
    # === 中间：录入/批量区 ===
    with col_mid:
        if mode == "单题录入":
            st.subheader("📝 单题详情")
            
            def update_content_wrapper():
                """根据表单字段更新 entry_content 中的 problem 包裹"""
                content = st.session_state.get("entry_content", "")
                year = st.session_state.get("entry_year", "")
                p_type = st.session_state.get("entry_p_type", "G")
                paper = st.session_state.get("entry_paper_name", "")
                number = st.session_state.get("entry_number", "")
                subj_list = st.session_state.get("entry_subject_multi", [])
                subj = "，".join(subj_list) if subj_list else ""
                
                # 尝试匹配现有的 problem 包裹
                prob_match = re.search(r'(\\begin\{problem\})\{.*?\}\{.*?\}\{.*?\}\{.*?\}\{.*?\}', content, re.DOTALL)
                if prob_match:
                    # 替换现有的参数
                    new_header = f"\\begin{{problem}}{{{year}}}{{{p_type}}}{{{paper}}}{{{number}}}{{{subj}}}"
                    content = content[:prob_match.start()] + new_header + content[prob_match.end():]
                    st.session_state["entry_content"] = content
                elif year and p_type and paper and number:
                    # 没有 problem 包裹，添加一个
                    content = f"\\begin{{problem}}{{{year}}}{{{p_type}}}{{{paper}}}{{{number}}}{{{subj}}}\n{content}\n\\end{{problem}}"
                    st.session_state["entry_content"] = content
            
            c_r1_1, c_r1_2, c_r1_3 = st.columns([1, 2, 1.5])
            with c_r1_1:
                year = st.text_input("年份", key="entry_year", on_change=update_content_wrapper)
            with c_r1_2:
                # 知识板块推断与选择逻辑优化
                current_content = st.session_state.get("entry_content", "")
                last_inferred_content = st.session_state.get("_last_inferred_content", None)
                
                if st.session_state.get("_ai_override_subjects", False):
                    st.session_state["_ai_override_subjects"] = False
                    st.session_state["_last_inferred_content"] = current_content
                elif (not st.session_state.get("entry_subject_user_locked", False)) and current_content != last_inferred_content and current_content.strip() != "":
                    inferred_subjects = []
                    for s in SUBJECTS:
                        if len(s) > 1 and s in current_content:
                            inferred_subjects.append(s)
                    if inferred_subjects:
                        st.session_state["entry_subject_multi"] = inferred_subjects
                    st.session_state["_last_inferred_content"] = current_content

                current_multi = st.session_state.get("entry_subject_multi", [])
                valid_current_multi = [s for s in current_multi if s in SUBJECTS]
                    
                if st.session_state.get("entry_subject_multi") != valid_current_multi:
                    st.session_state["entry_subject_multi"] = valid_current_multi
                def _on_subject_change():
                    st.session_state["entry_subject_user_locked"] = True
                    update_content_wrapper()
                st.multiselect("知识板块 (首个为主)", options=SUBJECTS, key="entry_subject_multi", on_change=_on_subject_change)
                subjects = st.session_state.get("entry_subject_multi") or []
                subject = "，".join(subjects) if subjects else ""
            with c_r1_3:
                type_opts = _editable_paper_type_options("WK" if cloze_mode else None)
                current_p_type = st.session_state.get("entry_p_type", "G")
                if current_p_type not in type_opts:
                    current_p_type = type_opts[0]
                    st.session_state["entry_p_type"] = current_p_type
                default_type_idx = type_opts.index(current_p_type)

                if cloze_mode:
                    st.selectbox("试卷类别", options=type_opts, index=default_type_idx, format_func=lambda x: f"{x} ({PAPER_TYPES[x]})", key="entry_p_type", disabled=True)
                else:
                    st.selectbox("试卷类别", options=type_opts, index=default_type_idx, format_func=lambda x: f"{x} ({PAPER_TYPES[x]})", key="entry_p_type", on_change=update_content_wrapper)
                p_type_code = st.session_state.get("entry_p_type", "G")

            c_r2_1, c_r2_2 = st.columns([3, 1])
            with c_r2_1:
                paper_name = st.text_input("试卷名称", key="entry_paper_name", on_change=update_content_wrapper)
            with c_r2_2:
                number = st.text_input("题号", key="entry_number", on_change=update_content_wrapper)
            
            st.markdown("##### 🏷️ 附加属性")
            st.selectbox("题型（必选）", QUESTION_TYPES, index=None, key="entry_question_type",
                         placeholder="请选择本题题型")
            st.markdown(
                """
                <style>
                .mc-entry-attribute-label {
                    color: var(--mc-muted) !important;
                    font-size: 0.88rem;
                    font-weight: 700;
                    line-height: 1.35;
                    margin: 0.1rem 0 0.38rem;
                }
                div[data-testid="stMarkdownContainer"]:has(.mc-entry-attribute-label) {
                    margin-bottom: 0 !important;
                }
                div[data-testid="stHorizontalBlock"]:has(.mc-entry-attribute-row-anchor):not(:has(div[data-testid="stHorizontalBlock"] .mc-entry-attribute-row-anchor)) {
                    display: grid !important;
                    grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
                    gap: 0.85rem !important;
                    width: 100% !important;
                    align-items: start !important;
                }
                div[data-testid="stHorizontalBlock"]:has(.mc-entry-attribute-row-anchor):not(:has(div[data-testid="stHorizontalBlock"] .mc-entry-attribute-row-anchor)) > div[data-testid="column"] {
                    width: auto !important;
                    min-width: 0 !important;
                    max-width: none !important;
                    flex: initial !important;
                }
                div[data-testid="stMarkdownContainer"]:has(.mc-entry-attribute-row-anchor) {
                    display: none !important;
                }
                /* 锚点所在的整个容器一起隐藏，避免占位导致左右两列标签错位 */
                div[data-testid="element-container"]:has(.mc-entry-attribute-row-anchor),
                div[data-testid="stElementContainer"]:has(.mc-entry-attribute-row-anchor) {
                    display: none !important;
                    height: 0 !important;
                    min-height: 0 !important;
                    margin: 0 !important;
                    padding: 0 !important;
                }
                @media (max-width: 760px) {
                    div[data-testid="stHorizontalBlock"]:has(.mc-entry-attribute-row-anchor):not(:has(div[data-testid="stHorizontalBlock"] .mc-entry-attribute-row-anchor)) {
                        grid-template-columns: minmax(0, 1fr) !important;
                        gap: 0.5rem !important;
                    }
                }
                </style>
                <div class="mc-entry-attribute-label">难度星级</div>
                """,
                unsafe_allow_html=True,
            )
            from utils.star_rating import st_star_rating
            s_difficulty_val = st_star_rating(label="", value=st.session_state.get("entry_difficulty", 0.0), max_stars=6, key="star_entry_difficulty")
            if s_difficulty_val != st.session_state.get("entry_difficulty", 0.0):
                st.session_state["entry_difficulty"] = s_difficulty_val

            c_attr2, c_attr3 = st.columns(2, gap="medium")
            with c_attr2:
                st.markdown('<span class="mc-entry-attribute-row-anchor"></span>', unsafe_allow_html=True)
                s_tags = st.text_input("其他自由标签", placeholder="例如：压轴题，易错点", key="entry_custom_tags", help="多个标签用中文逗号“，”分隔。")
            with c_attr3:
                s_remark = st.text_input("备注", placeholder="例如：2025 新高考题型", key="entry_remark")

            c_taxonomy1, c_taxonomy2 = st.columns(2, gap="medium")
            with c_taxonomy1:
                st.markdown('<span class="mc-entry-attribute-row-anchor"></span>', unsafe_allow_html=True)
                s_knowledge_points = st.text_input(
                    "知识点",
                    placeholder="例如：牛顿第二定律，动能定理",
                    key="entry_knowledge_points",
                    help="多个知识点用中文逗号“，”分隔。",
                )
            with c_taxonomy2:
                s_physics_methods = st.text_input(
                    "物理方法",
                    placeholder="例如：整体法，隔离法，控制变量法",
                    key="entry_physics_methods",
                    help="多个物理方法用中文逗号“，”分隔。",
                )

            def on_ai_analyze_click():
                content = st.session_state.get("entry_content", "").strip()
                if not content:
                    st.toast("题目内容为空，无法进行 AI 分析", icon="⚠️")
                    return

                res = call_ai_for_tags(content)
                if "error" in res:
                    st.toast(res["error"], icon="❌")
                else:
                    diff = res["difficulty"]
                    tags = res["tags"]
                    st.session_state['entry_knowledge_points'] = res.get('knowledge_points', '')
                    st.session_state['entry_physics_methods'] = res.get('physics_methods', '')
                    if res.get('subject'):
                        st.session_state['entry_subject_multi'] = res['subject'].split('，')
                        st.session_state['entry_subject_user_locked'] = True
                        update_content_wrapper()

                    if 0.0 <= diff <= 6.0:
                        st.session_state["entry_difficulty"] = diff
                    if tags:
                        old_tags = st.session_state.get("entry_custom_tags", "").strip()
                        if old_tags:
                            all_tags = set([t.strip() for t in old_tags.split("，") if t.strip()] + [t.strip() for t in tags.split("，") if t.strip()])
                            st.session_state["entry_custom_tags"] = "，".join(all_tags)
                        else:
                            st.session_state["entry_custom_tags"] = tags

                    st.toast("AI 标签与难度评级成功！", icon="🪄")

            st.button("🪄 AI 自动打标签", on_click=on_ai_analyze_click, use_container_width=True)
            st.markdown("##### 📝 题目内容 (LaTeX)")

        # 所有模式共用的查找替换
        def _normalize_circled_digits(text: str) -> str:
            if not text:
                return text
            mapping = {
                "①": r"\circled{1}",
                "②": r"\circled{2}",
                "③": r"\circled{3}",
                "④": r"\circled{4}",
                "⑤": r"\circled{5}",
            }
            for k, v in mapping.items():
                text = text.replace(k, v)
            return text

        def render_find_replace(target_key):
            with st.expander("🔍 查找与替换", expanded=False):
                c_f_1, c_f_2, c_f_3, c_f_4 = st.columns([2, 2, 1, 1])
                with c_f_1: f_str = st.text_input("查找", key=f"entry_find_{target_key}")
                with c_f_2: r_str = st.text_input("替换", key=f"entry_replace_{target_key}")
                with c_f_3:
                    st.write("")
                    st.write("")
                    if st.button("替换", key=f"btn_entry_replace_{target_key}"):
                        if st.session_state.get(target_key, "") and f_str:
                            st.session_state[target_key] = st.session_state[target_key].replace(f_str, r_str)
                            st.toast("替换完成", icon="✅")
                            st.rerun()
                with c_f_4:
                    st.write("")
                    st.write("")
                    if st.button("圈号→LaTeX", key=f"btn_entry_circled_{target_key}"):
                        cur = st.session_state.get(target_key, "") or ""
                        st.session_state[target_key] = _normalize_circled_digits(cur)
                        st.toast("已替换圈号 ①②③④⑤", icon="✅")
                        st.rerun()

        if mode == "单题录入":
            st.markdown("##### ⚙️ 录入配置")
            auto_solve_label = "如果识别内容缺少解答，则保存时 AI 生成解答" if cloze_mode else "本次录入同时生成解答"
            auto_solve_default = True if cloze_mode else False
            auto_solve_key = "cloze_auto_solve" if cloze_mode else "entry_auto_solve"
            auto_solve_enabled = st.checkbox(auto_solve_label, key=auto_solve_key, value=auto_solve_default)

            render_find_replace("entry_content")
            
            def on_content_change():
                """当用户编辑源码框时，同步回表单字段"""
                content = st.session_state.get("entry_content", "")
                if not content:
                    return
                
                fields = extract_problem_header_fields(content)
                if fields:
                    sy = fields["year"]
                    st_type = fields["p_type"]
                    sp = fields["paper"]
                    sn = fields["number"]
                    ss = fields["subject_str"]
                    if sy:
                        st.session_state["entry_year"] = sy
                    if st_type:
                        st.session_state["entry_p_type"] = st_type
                    if sp:
                        st.session_state["entry_paper_name"] = sp
                    if sn:
                        st.session_state["entry_number"] = sn
                    extracted_subjs = [s.strip() for s in (ss or "").split("，") if s.strip()]
                    valid_subjs = [s for s in extracted_subjs if s in SUBJECTS]
                    if valid_subjs:
                        st.session_state["entry_subject_multi"] = valid_subjs
                        st.session_state["entry_subject_user_locked"] = True
            
            content = st.text_area("题目内容 (LaTeX)", height=400, placeholder="在此粘贴题目内容...", key="entry_content", label_visibility="collapsed", on_change=on_content_change)
            # === 物理/数学插图智能矫正 Agent (Pi-Agent 架构，含实时语音与自然语言反馈) ===
            render_diagram_agent_dialog(content_state_key="entry_content", key_prefix="entry_single", base_dir=APP_ROOT)
            
        elif mode in ["批量试题录入", "同卷试题录入"]:
            def _split_batch_text_to_items(text: str):
                text = text or ""
                parts = re.split(r'---(.+\.tex)---\s*', text)
                items = []
                for i in range(1, len(parts), 2):
                    if i + 1 < len(parts):
                        fname = (parts[i] or "").strip()
                        body = (parts[i + 1] or "").lstrip()
                        if fname:
                            items.append({"filename": fname, "content": body})
                return items
            
            def _join_batch_items(items):
                out = []
                for it in items or []:
                    fname = (it.get("filename") or "").strip()
                    if fname.startswith("---") and fname.endswith("---"):
                        fname = fname[3:-3].strip()
                    fname = fname.replace("\n", " ").replace("\r", " ").strip()
                    if fname and not fname.lower().endswith(".tex"):
                        fname += ".tex"
                    body = (it.get("content") or "").rstrip()
                    if not fname:
                        continue
                    out.append(f"---{fname}---\n{body}".rstrip())
                return "\n\n".join(out).strip()

            def _write_batch_items_state(items, src_hash=None):
                old_count = int(st.session_state.get("batch_item_count") or 0)
                items = items or []
                st.session_state["batch_item_count"] = len(items)
                if src_hash is not None:
                    st.session_state["batch_items_src_hash"] = src_hash
                for idx, it in enumerate(items):
                    st.session_state[f"batch_item_name_{idx}"] = it.get("filename", "")
                    st.session_state[f"batch_item_text_{idx}"] = it.get("content", "")
                    st.session_state[f"batch_item_question_type_{idx}"] = infer_question_type(it.get("content", ""), it.get("filename", ""))
                for idx in range(len(items), old_count):
                    st.session_state.pop(f"batch_item_name_{idx}", None)
                    st.session_state.pop(f"batch_item_text_{idx}", None)
                    st.session_state.pop(f"batch_item_question_type_{idx}", None)
             
            def _ensure_batch_items_state():
                src = st.session_state.get("batch_content", "") or ""
                src_hash = hashlib.md5(src.encode("utf-8", errors="ignore")).hexdigest()
                if st.session_state.get("batch_items_src_hash") == src_hash and st.session_state.get("batch_item_count") is not None:
                    return
                items = _split_batch_text_to_items(src)
                _write_batch_items_state(items, src_hash)
            
            def _current_items_from_state():
                n = int(st.session_state.get("batch_item_count") or 0)
                items = []
                for idx in range(n):
                    fname = st.session_state.get(f"batch_item_name_{idx}", "")
                    body = st.session_state.get(f"batch_item_text_{idx}", "")
                    question_type = st.session_state.get(f"batch_item_question_type_{idx}")
                    if question_type in QUESTION_TYPES:
                        meta, _ = _parse_question_meta(body)
                        meta["题型"] = question_type
                        body = _inject_question_meta(body, meta)
                    items.append({"filename": fname, "content": body})
                return items

            def _set_batch_content_and_hash(new_text: str):
                new_text = (new_text or "").strip()
                st.session_state["batch_content"] = new_text
                st.session_state["batch_items_src_hash"] = hashlib.md5(new_text.encode("utf-8", errors="ignore")).hexdigest()

            def _sync_batch_content_from_items():
                _set_batch_content_and_hash(_join_batch_items(_current_items_from_state()))

            def _manual_split_candidates(content):
                lines = (content or "").splitlines()
                substantive = []
                previous_nonempty = ""
                for line_index, raw_line in enumerate(lines):
                    stripped = raw_line.strip()
                    if not stripped:
                        continue
                    excluded = (
                        stripped.startswith("%")
                        or stripped.startswith(r"\begin{")
                        or stripped.startswith(r"\end{")
                        or stripped.startswith(r"\choice")
                        or stripped.startswith("---")
                    )
                    if not excluded:
                        substantive.append((line_index, stripped, previous_nonempty))
                    previous_nonempty = stripped

                # The first substantive line is the current question stem, not a split point.
                candidates = substantive[1:]
                strong = []
                remaining = []
                for line_index, text, previous in candidates:
                    item = (line_index, text[:90])
                    if previous in {r"\end{choices}", r"\end{problem}", r"\end{answer}", r"\end{solutions}"}:
                        strong.append(item)
                    else:
                        remaining.append(item)
                return strong + remaining

            def _split_batch_item_at_line(item_index, line_index):
                from utils.csv_ops import get_next_id
                from utils.latex_ops import inject_meta_data, parse_meta_data

                current_items = _current_items_from_state()
                if not (0 <= item_index < len(current_items)):
                    return False, "未找到需要拆分的题目。"

                source_item = current_items[item_index]
                source_content = source_item.get("content") or ""
                source_lines = source_content.splitlines()
                if not (0 < line_index < len(source_lines)):
                    return False, "拆分位置无效。"

                head_raw = "\n".join(source_lines[:line_index]).strip()
                tail_raw = "\n".join(source_lines[line_index:]).strip()
                original_meta, clean_source = parse_meta_data(source_content)
                _, clean_head = parse_meta_data(head_raw)
                _, clean_tail = parse_meta_data(tail_raw)
                if not clean_head.strip() or not clean_tail.strip():
                    return False, "拆分位置前后都必须有题目内容。"

                fields = extract_problem_header_fields(clean_source) or {}
                filename = os.path.basename(source_item.get("filename") or "").removesuffix(".tex")
                filename_parts = filename.split("-")
                fallback = {
                    "year": filename_parts[0] if len(filename_parts) >= 5 else "?",
                    "p_type": filename_parts[1] if len(filename_parts) >= 5 else "?",
                    "paper": filename_parts[2] if len(filename_parts) >= 5 else "?",
                    "number": filename_parts[3] if len(filename_parts) >= 5 else "?",
                    "subject_str": "-".join(filename_parts[4:]) if len(filename_parts) >= 5 else "未分类",
                }
                year = fields.get("year") or fallback["year"]
                p_type = fields.get("p_type") or fallback["p_type"]
                paper = fields.get("paper") or fallback["paper"]
                number = fields.get("number") or fallback["number"]
                subject = fields.get("subject_str") or fallback["subject_str"] or "未分类"

                used_numbers = set()
                used_ids = set()
                for current_item in current_items:
                    current_name = os.path.basename(current_item.get("filename") or "").removesuffix(".tex")
                    current_parts = current_name.split("-")
                    if len(current_parts) >= 5:
                        used_numbers.add(current_parts[3])
                    current_meta, _ = parse_meta_data(current_item.get("content") or "")
                    current_id = str(current_meta.get("ID", "")).strip()
                    if current_id.isdigit():
                        used_ids.add(int(current_id))

                next_number = _increment_question_number(number)
                while next_number in used_numbers:
                    next_number = _increment_question_number(next_number)

                next_id = get_next_id()
                while next_id in used_ids:
                    next_id += 1

                head_content = normalize_single_problem_structure(clean_head, year, p_type, paper, number, subject)
                tail_content = normalize_single_problem_structure(clean_tail, year, p_type, paper, next_number, subject)
                head_meta = {
                    "ID": original_meta.get("ID") or str(next_id),
                    "难度星级": original_meta.get("难度星级", ""),
                    "标签": original_meta.get("标签", ""),
                    "知识点": original_meta.get("知识点", ""),
                    "物理方法": original_meta.get("物理方法", ""),
                    "备注": original_meta.get("备注", ""),
                    "组卷引用次数": original_meta.get("组卷引用次数", "0"),
                }
                if not original_meta.get("ID"):
                    used_ids.add(next_id)
                    next_id += 1
                    while next_id in used_ids:
                        next_id += 1
                tail_meta = {
                    "ID": str(next_id),
                    "难度星级": "",
                    "标签": "",
                    "知识点": "",
                    "物理方法": "",
                    "备注": "",
                    "组卷引用次数": "0",
                }

                current_items[item_index] = {
                    "filename": generate_filename(year, p_type, paper, number, subject),
                    "content": inject_meta_data(head_content, head_meta),
                }
                current_items.insert(item_index + 1, {
                    "filename": generate_filename(year, p_type, paper, next_number, subject),
                    "content": inject_meta_data(tail_content, tail_meta),
                })
                new_text = _join_batch_items(current_items)
                _set_batch_content_and_hash(new_text)
                _write_batch_items_state(current_items, st.session_state["batch_items_src_hash"])
                return True, f"已拆分为两题，新题暂定为第 {next_number} 题。"

            def _apply_same_paper_meta_to_items(items, year, p_type, paper):
                synced = []
                for it in items or []:
                    fname = (it.get("filename") or "").strip()
                    content = it.get("content") or ""
                    name_body = fname.replace(".tex", "")
                    segments = name_body.split("-")
                    if len(segments) >= 2:
                        q_num = segments[-2]
                        q_subj = segments[-1]
                    else:
                        q_num = "?"
                        q_subj = "未分类"

                    synced_name = generate_filename(year, p_type, paper, q_num, q_subj)
                    if content.strip():
                        if "\\begin{problem}" in content:
                            synced_content = replace_problem_header(content, year, p_type, paper, q_num, q_subj)
                        else:
                            synced_content = normalize_single_problem_structure(content.strip(), year, p_type, paper, q_num, q_subj)
                    else:
                        synced_content = content
                    synced.append({"filename": synced_name, "content": synced_content})
                return synced
            
            _ensure_batch_items_state()
            
            # 标题 + 两个操作按钮同一行（按钮水平并排，位于「批量试题录入 / 同卷试题录入」标题右侧）
            c_title, c_ai_btn, c_detect_btn = st.columns(
                [2.6, 1, 1], gap="small", vertical_alignment="bottom"
            )
            with c_title:
                st.markdown(f"### 📚 {mode}")
            with c_ai_btn:
                def on_batch_ai_click():
                    batch_text = _join_batch_items(_current_items_from_state())
                    if not batch_text.strip():
                        st.warning("内容为空，无法进行 AI 分析")
                        return
                    try:
                        batch_text = join_tex_questions(detect_tex_questions(batch_text, 'AI批量草稿.tex',
                            dict(year=st.session_state.get('u_batch_year', '2026'),
                                 paper_type=st.session_state.get('u_batch_type', 'G'),
                                 paper=st.session_state.get('u_batch_paper', '待填写'), number='1', subject='未分类')))
                    except ValueError as exc:
                        st.error(f'分题检测失败：{exc}')
                        return
                    
                    # Split by `---filename---`
                    parts = re.split(r'(---.+\.tex---\s*)', batch_text)
                    new_batch_text = ""
                    if len(parts) > 1:
                        new_batch_text = parts[0]
                        from utils.latex_ops import parse_meta_data
                        
                        progress_text = "🤖 正在批量调用 AI..."
                        my_bar = st.progress(0, text=progress_text)
                        total_q = len(parts) // 2
                        
                        for i in range(1, len(parts), 2):
                            current_q = i // 2 + 1
                            my_bar.progress(current_q / total_q, text=f"{progress_text} ({current_q}/{total_q})")
                            
                            header = parts[i]
                            content = parts[i+1]
                            existing_meta, clean_content = parse_meta_data(content)
                            
                            # 调用 AI
                            ai_res = call_ai_for_tags(clean_content)
                            if "error" not in ai_res:
                                diff = ai_res["difficulty"]
                                existing_meta["难度星级"] = str(diff) if diff > 0 else ""
                                existing_meta["标签"] = ai_res["tags"]
                                existing_meta['知识点'] = ai_res.get('knowledge_points', '')
                                existing_meta['物理方法'] = ai_res.get('physics_methods', '')
                                fields = extract_problem_header_fields(clean_content)
                                if fields and ai_res.get('subject'):
                                    clean_content = replace_problem_header(clean_content, fields['year'], fields['p_type'],
                                                                          fields['paper'], fields['number'], ai_res['subject'])
                                    header = '---' + generate_filename(fields['year'], fields['p_type'], fields['paper'],
                                                                       fields['number'], ai_res['subject']) + '---\n'
                            else:
                                st.toast(f"第 {current_q} 题 AI 分析出错: {ai_res['error']}", icon="❌")
                                
                            meta_str = "% === Begin Label Data ===\n"
                            meta_str += f"% ID: {existing_meta.get('ID', '')}\n"
                            meta_str += f"% 难度星级: {existing_meta.get('难度星级', '')}\n"
                            meta_str += f"% 标签: {existing_meta.get('标签', '')}\n"
                            meta_str += f"% 知识点: {existing_meta.get('知识点', '')}\n"
                            meta_str += f"% 物理方法: {existing_meta.get('物理方法', '')}\n"
                            meta_str += f"% 备注: {existing_meta.get('备注', '')}\n"
                            meta_str += f"% 组卷引用次数: {existing_meta.get('组卷引用次数', '0')}\n"
                            meta_str += "% === End  Label Data ===\n\n"
                            
                            new_batch_text += header + meta_str + clean_content.lstrip()
                            
                        my_bar.empty()
                        _set_batch_content_and_hash(new_batch_text)
                        st.toast("批量 AI 自动打标签完成！", icon="🪄")
                    else:
                        st.warning("未检测到有效的分隔线格式，请确保内容符合 `---xxx.tex---` 格式")

                st.button("🪄 AI 自动打标签", key="btn_batch_ai", on_click=on_batch_ai_click, use_container_width=True)

            def on_batch_detect_click():
                try:
                    text = join_tex_questions(detect_tex_questions(_join_batch_items(_current_items_from_state()), '批量草稿.tex',
                        dict(year=st.session_state.get('u_batch_year', '2026'), paper_type=st.session_state.get('u_batch_type', 'G'),
                             paper=st.session_state.get('u_batch_paper', '待填写'), number='1', subject='未分类')))
                    _set_batch_content_and_hash(text)
                    _write_batch_items_state(_split_batch_text_to_items(text), st.session_state['batch_items_src_hash'])
                except ValueError as exc:
                    st.error(f'分题检测失败：{exc}')

            with c_detect_btn:
                st.button('🔎 重新检测分题', key='btn_batch_detect_questions', on_click=on_batch_detect_click, use_container_width=True)
            
            # 如果是同卷录入，提供试卷头部信息输入
            if mode == "同卷试题录入":
                st.info("💡 **同卷模式**：下方填写的【年份】【试卷名称】等信息，将自动应用到所有识别出的题目中。")
                if "u_batch_year" not in st.session_state:
                    st.session_state["u_batch_year"] = "2024"
                if "u_batch_paper" not in st.session_state:
                    st.session_state["u_batch_paper"] = "新高考I卷"
                if "u_batch_type" not in st.session_state:
                    st.session_state["u_batch_type"] = "G"
                    
                c1, c2, c3, c4 = st.columns([1.5, 2, 1.5, 1])
                batch_year = c1.text_input("统一年份", key="u_batch_year")
                batch_pname = c2.text_input("统一试卷名称", key="u_batch_paper")
                type_opts = _editable_paper_type_options()
                batch_ptype = c3.selectbox("统一试卷类别", options=type_opts, format_func=lambda x: f"{x} ({PAPER_TYPES[x]})", key="u_batch_type")
                
                with c4:
                    st.markdown("<div style='padding-top: 28px;'></div>", unsafe_allow_html=True)
                    def on_sync_click():
                        s_y = st.session_state.get("u_batch_year", "")
                        s_t = st.session_state.get("u_batch_type", "G")
                        s_p = st.session_state.get("u_batch_paper", "")
                        
                        if s_y and s_p:
                            items = _apply_same_paper_meta_to_items(_current_items_from_state(), s_y, s_t, s_p)
                            new_text = _join_batch_items(items)
                            
                            # 自动匹配并分配空的 ID
                            from utils.csv_ops import get_next_id
                            current_id = get_next_id()
                            def repl_id(m):
                                nonlocal current_id
                                val = m.group(1).strip()
                                if not val:  # 只有为空时才分配新ID，防止重复点击浪费ID
                                    val = str(current_id)
                                    current_id += 1
                                return f"% ID: {val}"
                            # 修复正则：匹配末尾可能的空格或换行符
                            new_text = re.sub(r'% ID:\s*(.*?)(?=\n|$)', repl_id, new_text)
                            _set_batch_content_and_hash(new_text)
                            _write_batch_items_state(_split_batch_text_to_items(new_text), st.session_state["batch_items_src_hash"])
                            
                    if st.button("🔄 同步更新", help="将上方填写的年份、类别和试卷名称，一键替换下方所有源码中的 problem 标签", on_click=on_sync_click, use_container_width=True, type="secondary"):
                        st.toast("已同步更新所有 problem 标签及预分配ID！", icon="✅")

            st.markdown("##### 🏷️ 本批次统一标签（应用到每一道题）")
            batch_tag_col, batch_point_col, batch_method_col = st.columns(3)
            with batch_tag_col:
                st.text_input(
                    "其他自由标签",
                    placeholder="任意数量，例如：压轴题，易错题，自编题",
                    key="batch_custom_tags",
                    help="内容和数量均可自行定义；使用中英文逗号分隔。保存时与题目已有标签合并。",
                )
            with batch_point_col:
                st.text_input(
                    "知识点",
                    placeholder="例如：牛顿第二定律，动能定理",
                    key="batch_knowledge_points",
                )
            with batch_method_col:
                st.text_input(
                    "物理方法",
                    placeholder="例如：整体法，隔离法，控制变量法",
                    key="batch_physics_methods",
                )

            with st.expander("🔍 查找与替换", expanded=False):
                c_f_1, c_f_2, c_f_3, c_f_4 = st.columns([2, 2, 1, 1])
                with c_f_1:
                    f_str = st.text_input("查找", key="batch_find_all")
                with c_f_2:
                    r_str = st.text_input("替换", key="batch_replace_all")
                with c_f_3:
                    st.write("")
                    st.write("")
                    def _apply_batch_replace():
                        if not f_str:
                            return
                        items = _current_items_from_state()
                        for idx, it in enumerate(items):
                            # 同时替换文件名和正文内容
                            fname = (it.get("filename") or "").replace(f_str, r_str)
                            content = (it.get("content") or "").replace(f_str, r_str)
                            st.session_state[f"batch_item_name_{idx}"] = fname
                            st.session_state[f"batch_item_text_{idx}"] = content
                        _sync_batch_content_from_items()
                    if st.button("替换", key="btn_batch_replace_all", on_click=_apply_batch_replace):
                        st.toast("替换完成（含标题）", icon="✅")
                with c_f_4:
                    st.write("")
                    st.write("")
                    def _apply_batch_circled():
                        items = _current_items_from_state()
                        for idx, it in enumerate(items):
                            st.session_state[f"batch_item_text_{idx}"] = _normalize_circled_digits(it.get("content") or "")
                        _sync_batch_content_from_items()
                    if st.button("圈号→LaTeX", key="btn_batch_circled_all", on_click=_apply_batch_circled):
                        st.toast("已替换圈号 ①②③④⑤", icon="✅")

            st.markdown("##### 📝 题目内容 (LaTeX)")
            items = _current_items_from_state()
            if not items:
                st.text_area("批量内容编辑", height=260, key="batch_content_fallback", label_visibility="collapsed")
            else:
                st.markdown("""
<style>
button[kind="secondary"][data-testid="stBaseButton-secondary"][aria-label="放弃本题×"] {
    color: #dc3545 !important;
    border-color: #dc3545 !important;
}
button[kind="secondary"][data-testid="stBaseButton-secondary"][aria-label="放弃本题×"]:hover {
    color: #fff !important;
    background-color: #dc3545 !important;
    border-color: #dc3545 !important;
}
</style>
                """, unsafe_allow_html=True)
                for idx, it in enumerate(items):
                    st.markdown('<hr style="border-top: 1px solid #e1e4e8; margin-top: 14px; margin-bottom: 14px;">', unsafe_allow_html=True)
                    def _apply_batch_fname():
                        _sync_batch_content_from_items()
                        st.toast("已应用文件名修改", icon="✅")
                        st.rerun()

                    def _discard_batch_item(_idx=idx):
                        # 从 batch_content 中移除该题目
                        current_items = _current_items_from_state()
                        if 0 <= _idx < len(current_items):
                            current_items.pop(_idx)
                            new_text = _join_batch_items(current_items)
                            _set_batch_content_and_hash(new_text)
                            _write_batch_items_state(current_items, st.session_state["batch_items_src_hash"])
                            st.toast("已放弃该题目")
                        st.rerun()

                    # 文件名操作栏与源码/预览栏平级，兼容 Streamlit 1.37 的列嵌套限制。
                    c_fn_1, c_btn_1, c_btn_2 = st.columns([3, 1, 1], vertical_alignment="bottom")
                    with c_fn_1:
                        st.text_input("文件名", key=f"batch_item_name_{idx}", on_change=_sync_batch_content_from_items)
                    with c_btn_1:
                        st.button("应用标题", key=f"batch_apply_fname_{idx}", use_container_width=True, on_click=_apply_batch_fname)
                    with c_btn_2:
                        st.button("放弃本题×", key=f"batch_discard_fname_{idx}", use_container_width=True, on_click=_discard_batch_item, type="secondary")

                    c_src, c_prev = st.columns([1, 1])
                    with c_src:
                        st.selectbox("题型（必选）", QUESTION_TYPES, key=f"batch_item_question_type_{idx}",
                                     on_change=_sync_batch_content_from_items,
                                     help="已按本题内容预选，请逐题核对；可手动修改。")
                        st.text_area("题目源码", height=240, key=f"batch_item_text_{idx}", label_visibility="collapsed", on_change=_sync_batch_content_from_items)
                        st.caption("提示：编辑后点击空白处或按 Ctrl+Enter 以应用更新。")
                    with c_prev:
                        fname = (st.session_state.get(f"batch_item_name_{idx}", "") or "").strip()
                        st.markdown(f"### 📄 {fname}")
                        try:
                            preview_src = st.session_state.get(f"batch_item_text_{idx}", "") or ""
                            st.markdown(latex_to_markdown(preview_src, show_title=False), unsafe_allow_html=True)
                        except Exception as e:
                            st.error(f"预览渲染出错: {e}")

                    split_source = st.session_state.get(f"batch_item_text_{idx}", it.get("content") or "") or ""
                    split_candidates = _manual_split_candidates(split_source)
                    if split_candidates:
                        with st.expander("✂️ 手动拆题", expanded=False):
                            candidate_lines = [line_index for line_index, _ in split_candidates]
                            candidate_labels = {line_index: preview for line_index, preview in split_candidates}
                            selected_line = st.selectbox(
                                "下一题起始行",
                                options=candidate_lines,
                                format_func=lambda line_index: f"第 {line_index + 1} 行 · {candidate_labels[line_index]}",
                                key=f"batch_split_line_{idx}_{hashlib.md5(split_source.encode('utf-8', errors='ignore')).hexdigest()[:8]}",
                                help="选择下一道题题干开始的那一行。明确边界会优先显示。",
                            )
                            if st.button("从选中行拆分", key=f"batch_split_apply_{idx}", type="primary"):
                                split_ok, split_message = _split_batch_item_at_line(idx, selected_line)
                                if split_ok:
                                    st.toast(split_message, icon="✂️")
                                    st.rerun()
                                else:
                                    st.error(split_message)
                _sync_batch_content_from_items()

            st.markdown('<hr style="border-top: 1px solid #e1e4e8; margin-top: 12px; margin-bottom: 16px;">', unsafe_allow_html=True)
            if mode == "同卷试题录入":
                c_same_title, c_same_btn = st.columns([2, 1])
                with c_same_title:
                    st.markdown("### 📚 同卷试题批量处理状态")
                with c_same_btn:
                    if st.button("💾 同卷提取并保存", type="primary", use_container_width=True, key="same_paper_save_btn"):
                        try:
                            for item in _current_items_from_state():
                                require_question_type(_parse_question_meta(item["content"])[0].get("题型"))
                        except ValueError as exc:
                            st.error(str(exc))
                            st.stop()
                        st.session_state["_run_same_paper_batch"] = True
                        st.session_state["_run_ai_tagging_batch"] = st.session_state.get("batch_enable_ai_flag", False)
                        st.session_state["batch_enable_ai_flag"] = False
                        st.rerun()
                st.info("同卷试题批量录入的处理结果会在此显示。")
                
                if st.session_state.get("_run_same_paper_batch", False):
                    st.session_state["_run_same_paper_batch"] = False
                    batch_text = st.session_state.get("batch_content", "")
                    u_year = st.session_state.get("u_batch_year", "")
                    u_paper = st.session_state.get("u_batch_paper", "")
                    u_type = st.session_state.get("u_batch_type", "G")
                    
                    if not batch_text.strip():
                        st.warning("请输入内容")
                    elif not (u_year and u_paper):
                        st.error("请完善年份和试卷名称信息")
                    else:
                        parts = re.split(r'---(.+\.tex)---\s*', batch_text)
                        count = 0
                        log_msg = []
                        progress_bar = st.progress(0)
                        status_text = st.empty()
                        total_files = len(parts) // 2
                        for i in range(1, len(parts), 2):
                            current_idx = i // 2 + 1
                            if i + 1 < len(parts):
                                raw_fname = parts[i].strip()
                                file_content = parts[i+1].strip()
                                status_text.text(f"正在处理: {raw_fname} ({current_idx}/{total_files})")
                                name_body = raw_fname.replace('.tex', '')
                                segments = name_body.split('-')
                                if len(segments) >= 2:
                                    q_num = segments[-2]
                                    q_subj = segments[-1]
                                    final_filename = generate_filename(u_year, u_type, u_paper, q_num, q_subj)
                                    primary_subj = q_subj.split("，")[0]
                                    save_dir = os.path.join(CHAPTERS_DIR, primary_subj, str(u_year))
                                    ensure_dir(save_dir)
                                    file_path = os.path.join(save_dir, final_filename)
                                    if "\\begin{problem}" in file_content:
                                        file_content = replace_problem_header(file_content, str(u_year), u_type, u_paper, q_num, q_subj)
                                    else:
                                        file_content = normalize_single_problem_structure(file_content, str(u_year), u_type, u_paper, q_num, q_subj)
                                    file_content = extract_and_replace_tikz(file_content, final_filename, save_dir)
                                    from utils.latex_ops import parse_meta_data, inject_meta_data
                                    existing_meta, clean_content = parse_meta_data(file_content)
                                    from utils.csv_ops import get_next_id
                                    q_id = get_next_id()
                                    meta_dict = {
                                        "ID": q_id,
                                        "难度星级": existing_meta.get("难度星级", ""),
                                        "标签": _merge_tag_texts(existing_meta.get("标签", ""), st.session_state.get("batch_custom_tags", "")),
                                        "知识点": _merge_tag_texts(existing_meta.get("知识点", ""), st.session_state.get("batch_knowledge_points", "")),
                                        "物理方法": _merge_tag_texts(existing_meta.get("物理方法", ""), st.session_state.get("batch_physics_methods", "")),
                                        "题型": require_question_type(existing_meta.get("题型")),
                                        "备注": existing_meta.get("备注", ""),
                                        "组卷引用次数": existing_meta.get("组卷引用次数", "0"),
                                    }
                                    file_content = inject_meta_data(file_content, meta_dict)
                                    try:
                                        duplicate_matches = []
                                        saved = _save_batch_question_if_new(
                                            file_path,
                                            file_content,
                                            str(u_year),
                                            u_type,
                                            u_paper,
                                            q_num,
                                            q_subj,
                                            duplicate_matches_out=duplicate_matches,
                                        )
                                        if not saved:
                                            log_msg.append({
                                                "status": "skip",
                                                "file": final_filename,
                                                "msg": (
                                                    f"检测到完全相同题干，已跳过（已有：{duplicate_matches[0].get('name', '')}）"
                                                    if duplicate_matches
                                                    else "检测到同名旧题目，已保留旧题并跳过新的识别结果"
                                                ),
                                            })
                                            progress_bar.progress(current_idx / total_files)
                                            continue
                                        count += 1
                                        ai_str = ""
                                        if meta_dict['难度星级'] or meta_dict['标签']:
                                            ai_str = f" [AI自动提取: 星级={meta_dict['难度星级'] or '无'} | 标签={meta_dict['标签'] or '无'}]"
                                        log_msg.append({"status": "success", "file": final_filename, "path": file_path, "ai_info": ai_str})
                                    except Exception as e:
                                        log_msg.append({"status": "error", "file": final_filename, "msg": str(e)})
                                else:
                                    log_msg.append({"status": "skip", "file": raw_fname, "msg": "文件名格式不足 (需至少包含 题号-板块)"})
                            progress_bar.progress(current_idx / total_files)
                        status_text.empty()
                        c_msg, c_jump = st.columns([3, 1])
                        c_msg.success(f"处理完成，共保存 {count} 个文件")
                        def _jump_to_browse_same_paper():
                            st.session_state["main_sidebar_radio"] = "🔍\n全局浏览\n与编辑"
                            st.session_state["adv_search_active"] = False
                            st.session_state["recent_saved_active"] = True
                            st.session_state["recent_saved_paths"] = [log.get("path") for log in log_msg if log.get("status") == "success" and log.get("path")]
                        c_jump.button("跳转至全局浏览查看 ↗", use_container_width=True, type="primary", key="jump_to_browse_same_paper", on_click=_jump_to_browse_same_paper)
                        st.toast(f"同卷处理完成！共保存 {count} 个文件", icon="✅")
                        with st.expander("查看处理日志", expanded=True):
                            for log_index, log in enumerate(log_msg):
                                if log["status"] == "success":
                                    c1, c2 = st.columns([4, 1])
                                    ai_str = log.get('ai_info', '')
                                    c1.success(f"✅ {log['file']}{ai_str}")
                                    log_key = hashlib.md5(
                                        str(log.get("path") or log.get("file") or log_index).encode("utf-8", errors="ignore")
                                    ).hexdigest()[:10]
                                    if c2.button("📂 打开", key=f"open_log_u_{log_index}_{log_key}"):
                                        try:
                                            os.startfile(log['path'])
                                        except Exception as e:
                                            st.error(f"无法打开: {e}")
                                elif log["status"] == "error":
                                    st.error(f"❌ {log['file']}: {log['msg']}")
                                else:
                                    st.warning(f"⚠️ {log['file']}: {log['msg']}")

            elif mode == "批量试题录入":
                c_batch_title, c_batch_btn = st.columns([2, 1])
                with c_batch_title:
                    st.markdown("### 🗃️ 批量处理状态")
                with c_batch_btn:
                    if st.button("💾 批量提取并保存", type="primary", use_container_width=True, key="batch_save_btn"):
                        try:
                            for item in _current_items_from_state():
                                require_question_type(_parse_question_meta(item["content"])[0].get("题型"))
                        except ValueError as exc:
                            st.error(str(exc))
                            st.stop()
                        st.session_state["_run_batch_mode"] = True
                        st.session_state["_run_ai_tagging_global_batch"] = st.session_state.get("batch_enable_ai_flag", False)
                        st.session_state["batch_enable_ai_flag"] = False
                        st.rerun()
                st.info("批量录入的处理结果会在此显示。")
                if st.session_state.get("_run_batch_mode", False):
                    st.session_state["_run_batch_mode"] = False
                    batch_text = st.session_state.get("batch_content", "")
                    if not batch_text.strip():
                        st.warning("请输入内容")
                    else:
                        parts = re.split(r'---(.+\.tex)---\s*', batch_text)
                        count = 0
                        log_msg = []
                        progress_bar = st.progress(0)
                        status_text = st.empty()
                        total_files = len(parts) // 2
                        for i in range(1, len(parts), 2):
                            current_idx = i // 2 + 1
                            if i + 1 < len(parts):
                                filename = parts[i].strip()
                                file_content = parts[i+1].strip()
                                status_text.text(f"正在处理: {filename} ({current_idx}/{total_files})")
                                name_body = filename.replace('.tex', '')
                                segments = name_body.split('-')
                                if len(segments) >= 5:
                                    year_seg = segments[0]
                                    topic_seg = segments[-1]
                                    primary_topic = topic_seg.split("，")[0]
                                    save_dir = os.path.join(CHAPTERS_DIR, primary_topic, str(year_seg))
                                    ensure_dir(save_dir)
                                    file_path = os.path.join(save_dir, filename)
                                    file_content = extract_and_replace_tikz(file_content, filename, save_dir)
                                    from utils.latex_ops import parse_meta_data, inject_meta_data
                                    existing_meta, clean_content = parse_meta_data(file_content)
                                    from utils.csv_ops import get_next_id
                                    q_id = get_next_id()
                                    meta_dict = {
                                        "ID": q_id,
                                        "难度星级": existing_meta.get("难度星级", ""),
                                        "标签": _merge_tag_texts(existing_meta.get("标签", ""), st.session_state.get("batch_custom_tags", "")),
                                        "知识点": _merge_tag_texts(existing_meta.get("知识点", ""), st.session_state.get("batch_knowledge_points", "")),
                                        "物理方法": _merge_tag_texts(existing_meta.get("物理方法", ""), st.session_state.get("batch_physics_methods", "")),
                                        "题型": require_question_type(existing_meta.get("题型")),
                                        "备注": existing_meta.get("备注", ""),
                                        "组卷引用次数": existing_meta.get("组卷引用次数", "0"),
                                    }
                                    file_content = inject_meta_data(file_content, meta_dict)
                                    try:
                                        duplicate_matches = []
                                        header_fields = extract_problem_header_fields(file_content) or {}
                                        saved = _save_batch_question_if_new(
                                            file_path,
                                            file_content,
                                            header_fields.get("year") or segments[0],
                                            header_fields.get("p_type") or segments[1],
                                            header_fields.get("paper") or segments[2],
                                            header_fields.get("number") or segments[3],
                                            header_fields.get("subject_str") or segments[4],
                                            duplicate_matches_out=duplicate_matches,
                                        )
                                        if not saved:
                                            log_msg.append({
                                                "status": "skip",
                                                "file": filename,
                                                "msg": (
                                                    f"检测到完全相同题干，已跳过（已有：{duplicate_matches[0].get('name', '')}）"
                                                    if duplicate_matches
                                                    else "检测到同名旧题目，已保留旧题并跳过新的识别结果"
                                                ),
                                            })
                                            progress_bar.progress(current_idx / total_files)
                                            continue
                                        count += 1
                                        ai_str = ""
                                        if meta_dict['难度星级'] or meta_dict['标签']:
                                            ai_str = f" [AI自动提取: 星级={meta_dict['难度星级'] or '无'} | 标签={meta_dict['标签'] or '无'}]"
                                        log_msg.append({"status": "success", "file": filename, "path": file_path, "id": q_id, "ai_info": ai_str})
                                    except Exception as e:
                                        log_msg.append({"status": "error", "file": filename, "msg": str(e)})
                                else:
                                    log_msg.append({"status": "skip", "file": filename, "msg": "文件名格式错误"})
                            progress_bar.progress(current_idx / total_files)
                        status_text.empty()
                        c_msg, c_jump = st.columns([3, 1])
                        c_msg.success(f"处理完成，共保存 {count} 个文件")
                        def _jump_to_browse_batch():
                            st.session_state["main_sidebar_radio"] = "🔍\n全局浏览\n与编辑"
                            st.session_state["adv_search_active"] = False
                            st.session_state["recent_saved_active"] = True
                            st.session_state["recent_saved_paths"] = [log.get("path") for log in log_msg if log.get("status") == "success" and log.get("path")]
                        c_jump.button("跳转至全局浏览查看 ↗", use_container_width=True, type="primary", key="jump_to_browse_batch", on_click=_jump_to_browse_batch)
                        clear_statistics_cache()
                        st.toast(f"批量处理完成！共保存 {count} 个文件", icon="✅")
                        with st.expander("查看处理日志", expanded=True):
                            for log_index, log in enumerate(log_msg):
                                if log["status"] == "success":
                                    c1, c2 = st.columns([4, 1])
                                    ai_str = log.get('ai_info', '')
                                    c1.success(f"✅ {log['file']}{ai_str}")
                                    log_key = hashlib.md5(
                                        str(log.get("path") or log.get("file") or log_index).encode("utf-8", errors="ignore")
                                    ).hexdigest()[:10]
                                    if c2.button("📂 打开", key=f"open_log_{log_index}_{log_key}"):
                                        try:
                                            os.startfile(log['path'])
                                        except Exception as e:
                                            st.error(f"无法打开: {e}")
                                elif log["status"] == "error":
                                    st.error(f"❌ {log['file']}: {log['msg']}")
                                else:
                                    st.warning(f"⚠️ {log['file']}: {log['msg']}")
            
    # === 右侧：实时预览与保存（仅单题模式） ===
    with col_right:
        if mode == "单题录入":
            if cloze_mode:
                c_preview_title, c_img_btn, c_save_btn = st.columns([2, 1, 1])
            else:
                c_preview_title, c_save_btn = st.columns([2, 1])
            with c_preview_title:
                st.subheader("👁️ 实时预览与保存")
            if cloze_mode:
                with c_img_btn:
                    def on_generate_cloze_image():
                        current = st.session_state.get("entry_content", "").strip()
                        fields = extract_problem_header_fields(current)
                        hint = "挖空题"
                        if fields:
                            hint = generate_filename(
                                fields.get("year") or st.session_state.get("entry_year", ""),
                                fields.get("p_type") or st.session_state.get("entry_p_type", ""),
                                fields.get("paper") or st.session_state.get("entry_paper_name", ""),
                                fields.get("number") or st.session_state.get("entry_number", ""),
                                fields.get("subject_str") or "未分类",
                            ).replace(".tex", "")
                        with st.spinner("正在生成当前挖空题图片..."):
                            res = generate_question_png_from_latex(
                                current,
                                filename_hint=hint,
                                cloze_type=st.session_state.get("cloze_generation_type", "挖空题"),
                            )
                        st.session_state["cloze_image_result"] = res
                        if res.get("ok"):
                            st.toast("挖空题图片已生成", icon="✅")
                        else:
                            st.toast(res.get("error", "图片生成失败"), icon="❌")
                    st.button("🖼️ 生成图片", on_click=on_generate_cloze_image, use_container_width=True)
            with c_save_btn:
                def on_save_entry():
                    s_content = st.session_state.get("entry_content", "")
                    try:
                        s_question_type = require_question_type(st.session_state.get("entry_question_type"))
                    except ValueError as exc:
                        st.toast(str(exc), icon="⚠️")
                        return
                    if cloze_mode:
                        s_content = _normalize_cloze_blank_markup(s_content)
                        st.session_state["entry_content"] = s_content
                    fields = extract_problem_header_fields(s_content)
                    s_year = (fields.get("year") if fields else "") or st.session_state.get("entry_year", "")
                    s_type = "WK" if cloze_mode else ((fields.get("p_type") if fields else "") or st.session_state.get("entry_p_type", ""))
                    s_paper = (fields.get("paper") if fields else "") or st.session_state.get("entry_paper_name", "")
                    s_num = (fields.get("number") if fields else "") or st.session_state.get("entry_number", "")
                    s_subj_from_state = "，".join(st.session_state.get("entry_subject_multi", []) or []).strip()
                    s_subj_str = ((fields.get("subject_str") if fields else "") or s_subj_from_state).strip()
                    s_subj = s_subj_str if s_subj_str else "未分类"
                    s_diff_raw = st.session_state.get("entry_difficulty", 0.0)
                    s_diff = "" if s_diff_raw == 0.0 else str(s_diff_raw)
                    s_tag = _merge_tag_texts(st.session_state.get("entry_custom_tags", ""))
                    s_knowledge_points = _merge_tag_texts(st.session_state.get("entry_knowledge_points", ""))
                    s_physics_methods = _merge_tag_texts(st.session_state.get("entry_physics_methods", ""))
                    s_rem = st.session_state.get("entry_remark", "")
                    if not s_content:
                        st.toast("题目内容不能为空", icon="⚠️")
                        return
                    if cloze_mode and CLOZE_BLANK_TEX not in _extract_problem_env(s_content):
                        st.toast("挖空题至少需要包含一个统一格式的挖空后才能保存", icon="⚠️")
                        return
                    if cloze_mode:
                        cloze_type = st.session_state.get("cloze_generation_type", "挖空题")
                        if cloze_type and cloze_type not in s_paper:
                            s_paper = f"{s_paper}（{cloze_type}）"
                    if st.session_state.get("entry_tex_direct", False):
                        full_text = replace_problem_header(s_content.strip(), s_year, s_type, s_paper, s_num, s_subj)
                    else:
                        full_text = normalize_single_problem_structure(s_content.strip(), s_year, s_type, s_paper, s_num, s_subj)
                    s_filename = generate_filename(s_year, s_type, s_paper, s_num, s_subj)
                    primary_subj = s_subj.split("，")[0]
                    s_save_dir = os.path.join(CHAPTERS_DIR, primary_subj, s_year)
                    ensure_dir(s_save_dir)
                    s_file_path = os.path.join(s_save_dir, s_filename)
                    if os.path.exists(s_file_path):
                        st.toast("目标题目文件已存在，请修改题号或试卷信息后再保存", icon="⚠️")
                        return
                    duplicate_matches = find_duplicate_matches(
                        full_text,
                        exclude_path=s_file_path,
                        similarity_threshold=0.88,
                        max_results=5,
                    )
                    duplicate_fingerprint = question_fingerprint(full_text)
                    pending_duplicate = st.session_state.get("entry_duplicate_warning") or {}
                    if duplicate_matches and pending_duplicate.get("fingerprint") != duplicate_fingerprint:
                        st.session_state["entry_duplicate_warning"] = {
                            "fingerprint": duplicate_fingerprint,
                            "matches": duplicate_matches,
                        }
                        st.toast("发现可能重复的题目，请确认后再次点击保存", icon="⚠️")
                        return
                    allow_duplicate = bool(duplicate_matches)
                    st.session_state.pop("entry_duplicate_warning", None)
                    full_text = extract_and_replace_tikz(full_text, s_filename, s_save_dir)
                    from utils.csv_ops import get_next_id
                    new_id = get_next_id()
                    meta_dict = {
                        "ID": new_id,
                        "难度星级": s_diff,
                        "标签": s_tag,
                        "知识点": s_knowledge_points,
                        "物理方法": s_physics_methods,
                        "题型": s_question_type,
                        "备注": s_rem,
                        "组卷引用次数": 0,
                    }
                    if cloze_mode:
                        meta_dict.update({
                            "来源题目ID": st.session_state.get("cloze_source_question_id", ""),
                            "挖空类型": st.session_state.get("cloze_generation_type", ""),
                            "生成时间": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        })
                    from utils.latex_ops import inject_meta_data
                    full_text = inject_meta_data(full_text, meta_dict)
                    try:
                        new_id = create_question_file(
                            s_file_path,
                            full_text,
                            s_year,
                            s_type,
                            s_paper,
                            s_num,
                            s_subj,
                            allow_duplicate=allow_duplicate,
                        )
                        solve_key = "cloze_auto_solve" if cloze_mode else "entry_auto_solve"
                        should_auto_solve = st.session_state.get(solve_key, False)
                        if cloze_mode:
                            should_auto_solve = should_auto_solve and not _has_nonempty_answer_and_solution(full_text)
                        if should_auto_solve:
                            problem_tex = _extract_problem_env(full_text)
                            with st.spinner("🤖 AI 正在生成解答..."):
                                res = call_ai_for_answer_solutions(problem_tex, fast=False)
                            if "error" in res:
                                st.toast(f"自动生成解答失败: {res['error']}", icon="❌")
                            else:
                                try:
                                    _apply_generated_answer_solutions_to_file(s_file_path, res["answer_tex"], res["solutions_tex"], mode="replace")
                                    st.toast("已自动生成并写回解答", icon="🪄")
                                except Exception as e:
                                    st.toast(f"写回解答失败: {e}", icon="❌")
                        st.toast(f"成功保存到: {s_filename} (分配ID: {new_id})", icon="✅")
                        clear_statistics_cache()
                        st.session_state["entry_year"] = s_year
                        st.session_state["entry_p_type"] = s_type
                        st.session_state["entry_paper_name"] = s_paper
                        st.session_state["entry_number"] = s_num
                        st.session_state["entry_subject_multi"] = [s.strip() for s in (s_subj or "").split("，") if s.strip() and s.strip() in SUBJECTS]
                        st.session_state["entry_subject_user_locked"] = True
                        st.session_state["entry_content"] = ""
                        st.session_state["entry_difficulty"] = 0.0
                        st.session_state["entry_custom_tags"] = ""
                        st.session_state["entry_knowledge_points"] = ""
                        st.session_state["entry_physics_methods"] = ""
                        st.session_state["entry_question_type"] = None
                        st.session_state["entry_remark"] = ""
                        st.session_state["entry_number"] = ""
                    except Exception as e:
                        st.toast(f"保存失败: {e}", icon="❌")
                pending_duplicate = st.session_state.get("entry_duplicate_warning") or {}
                if pending_duplicate.get("matches"):
                    st.warning("检测到相同或高度相似题目。再次点击保存将保留当前题目。")
                    for match in pending_duplicate["matches"][:3]:
                        st.caption(
                            f"{'完全重复' if match.get('kind') == 'exact' else '相似'} "
                            f"{match.get('score', 0) * 100:.1f}%：{match.get('name') or match.get('relative_path')}"
                        )
                save_label = "⚠️ 确认仍然保存" if pending_duplicate.get("matches") else "💾 保存题目"
                st.markdown('<span id="entry-save-button-anchor"></span>', unsafe_allow_html=True)
                st.button(save_label, type="primary", on_click=on_save_entry, use_container_width=True)
            filename = generate_filename(year, p_type_code, paper_name, number, subject or "未分类")
            st.info(f"目标文件名: `{filename}`")
            if cloze_mode:
                img_res = st.session_state.get("cloze_image_result")
                if img_res:
                    if img_res.get("ok"):
                        st.success(f"图片已生成：{img_res.get('path')}")
                        st.download_button(
                            "下载当前图片",
                            data=img_res.get("bytes") or b"",
                            file_name=img_res.get("filename") or "挖空题.png",
                            mime="image/png",
                            use_container_width=True,
                            key="cloze_download_current_image",
                        )
                    else:
                        st.warning(img_res.get("error", "图片生成失败"))
                        if img_res.get("log"):
                            with st.expander("查看 LaTeX 编译日志", expanded=False):
                                st.code(img_res["log"])
            if content.strip():
                st.markdown("---")
                try:
                    md_preview = latex_to_markdown(content, show_title=True)
                    st.markdown(md_preview, unsafe_allow_html=True)
                except Exception as e:
                    st.error(f"预览渲染出错: {e}")
        else:
            st.empty()
                             
# ================= 组卷服务题目卡片 =================
def render_exam_question_card(q_label, content, fpath, action_key):
    """Render a focused, read-only question card for the exam-selection workflow."""
    from utils.latex_ops import parse_meta_data

    def _toggle_exam_selection():
        selected_paths = st.session_state.setdefault("exam_selected_qs", [])
        if fpath in selected_paths:
            st.session_state["exam_selected_qs"] = [p for p in selected_paths if p != fpath]
            if st.session_state.get("exam_expanded_q") == fpath:
                remaining = st.session_state["exam_selected_qs"]
                st.session_state["exam_expanded_q"] = remaining[0] if remaining else None
        else:
            selected_paths.append(fpath)
            st.session_state["exam_selected_qs"] = selected_paths
            st.session_state["exam_expanded_q"] = fpath
            st.session_state["exam_basket_open"] = True
        if st.session_state.get("ai_exam_active"):
            st.session_state["ai_exam_modified"] = True

    meta, _ = parse_meta_data(content)
    difficulty = (meta.get("难度星级", "") or "").strip() or "未设置"
    tags = (meta.get("标签", "") or "").strip() or "无标签"
    is_selected = fpath in st.session_state.get("exam_selected_qs", [])

    with st.container(border=True):
        st.markdown('<span class="mc-exam-card-anchor"></span>', unsafe_allow_html=True)
        title_col, action_col = st.columns([3.4, 1], gap="small", vertical_alignment="center")
        with title_col:
            state_label = "已加入组卷" if is_selected else "待选择"
            state_class = "selected" if is_selected else "available"
            st.markdown(
                f'<div class="mc-exam-card-title">{html.escape(q_label)}</div>'
                f'<span class="mc-exam-card-state {state_class}">{state_label}</span>',
                unsafe_allow_html=True,
            )
        with action_col:
            action_label = "移出组卷" if is_selected else "加入组卷"
            action_type = "primary" if is_selected else "secondary"
            st.button(action_label, key=action_key, type=action_type, use_container_width=True, on_click=_toggle_exam_selection)

        st.markdown(
            f'<div class="mc-exam-card-meta">'
            f'<span><strong>难度</strong> {html.escape(difficulty)}</span>'
            f'<span><strong>标签</strong> {html.escape(tags)}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="mc-exam-card-preview-label">问题预览</div>', unsafe_allow_html=True)
        try:
            st.markdown(_cached_latex_to_markdown(content), unsafe_allow_html=True)
        except Exception as e:
            st.error(f"渲染错误: {e}")


def _question_action_state_key(key_prefix, fhash):
    return f"{key_prefix}_pending_question_action_{fhash}"


def _set_pending_question_action(key_prefix, fhash, action, fpath=""):
    if action == 'save_tex' and fpath:
        # Widget callbacks run before rendering. Save once, then the automatic
        # fragment rerun reads the new file, without an obsolete intermediate render.
        from services.performance import timed
        text_key = f"{key_prefix}_edit_{_question_key('text', fpath)}"
        new_content = st.session_state.get(text_key, '')
        if not _duplicate_save_confirmation(fpath, new_content, scope=f"{key_prefix}:{fpath}"):
            _request_question_scroll_restore(fpath)
            return
        try:
            with timed('browse_save'):
                final_content = save_modified_tex_file(fpath, new_content)
                _update_csv_index_for_content_change(fpath, final_content)
                _clear_advanced_search_result_cache()
            st.session_state[text_key] = final_content
            st.session_state[f"{key_prefix}_tex_edit_mode_{_question_key('tex', fpath)}"] = False
            _request_question_scroll_restore(fpath)
            st.toast('题目已保存', icon='✅')
        except Exception as error:
            st.toast(f'保存未完成，编辑内容已保留：{error}', icon='❌')
        return
    st.session_state[_question_action_state_key(key_prefix, fhash)] = action
    _request_question_scroll_restore(fpath)


def _pop_pending_question_action(key_prefix, fhash):
    return st.session_state.pop(_question_action_state_key(key_prefix, fhash), None)


BROWSE_SCROLL_RESTORE_KEY = "browse_scroll_restore_path"


def _request_question_scroll_restore(fpath: str):
    if fpath:
        st.session_state[BROWSE_SCROLL_RESTORE_KEY] = _norm_abs_path(fpath)


def _render_question_scroll_anchor(fpath: str, fhash: str):
    anchor_id = f"browse-question-{fhash}"
    st.markdown(
        f'<span id="{anchor_id}" style="display:block;scroll-margin-top:96px"></span>',
        unsafe_allow_html=True,
    )
    if st.session_state.get(BROWSE_SCROLL_RESTORE_KEY) != _norm_abs_path(fpath):
        return

    st.session_state.pop(BROWSE_SCROLL_RESTORE_KEY, None)
    components.html(
        f"""
        <script>
        (() => {{
            const doc = window.parent.document;
            const restore = () => {{
                const target = doc.getElementById({json.dumps(anchor_id)});
                const preview = doc.getElementById({json.dumps('browse-question-preview-' + fhash)});
                const card = target?.closest('[data-testid="stVerticalBlockBorderWrapper"]');
                if (target) (card || preview || target).scrollIntoView({{behavior: "auto", block: "center"}});
            }};
            restore();
            window.parent.requestAnimationFrame(restore);
            window.parent.setTimeout(restore, 160);
            window.parent.setTimeout(restore, 500);
        }})();
        </script>
        """,
        height=0,
    )


def _free_tag_items_key(key_prefix: str, fhash: str) -> str:
    return f"{key_prefix}_custom_tag_selection_{fhash}"


def _ensure_free_tag_items(key_prefix: str, fhash: str, tag_text: str):
    items_key = _free_tag_items_key(key_prefix, fhash)
    if items_key not in st.session_state:
        st.session_state[items_key] = _split_tag_values(tag_text)
    st.session_state.setdefault(f"{items_key}_options", list(st.session_state[items_key]))
    return items_key


def _add_free_tag_item(key_prefix: str, fhash: str, fpath: str):
    items_key = _free_tag_items_key(key_prefix, fhash)
    input_key = f"{items_key}_new"
    added = _split_tag_values(st.session_state.get(input_key, ""))
    st.session_state[items_key] = list(dict.fromkeys(st.session_state.get(items_key, []) + added))
    st.session_state[f"{items_key}_options"] = list(dict.fromkeys(st.session_state.get(f"{items_key}_options", []) + added))
    st.session_state[input_key] = ""
    if fpath:
        _request_question_scroll_restore(fpath)


def _free_tag_editor_value(key_prefix: str, fhash: str) -> str:
    items_key = _free_tag_items_key(key_prefix, fhash)
    return _merge_tag_texts(*st.session_state.get(items_key, []), st.session_state.get(f"{items_key}_new", ""))


def _clear_free_tag_editor(key_prefix: str, fhash: str):
    items_key = _free_tag_items_key(key_prefix, fhash)
    st.session_state.pop(items_key, None)
    st.session_state.pop(f"{items_key}_new", None)
    st.session_state.pop(f"{items_key}_options", None)


def _render_free_tag_editor(key_prefix, fhash, tag_text="", fpath=""):
    items_key = _ensure_free_tag_items(key_prefix, fhash, tag_text)
    # Keep options stable when a chip is removed. In Streamlit 1.37 changing
    # options changes the widget identity and can reset the remaining chips.
    options = st.session_state[f"{items_key}_options"]
    st.multiselect(
        "其他自由标签（内容、数量均可自行定义）", options=options, key=items_key,
        help="点击每个标签右侧的 × 删除；下方可输入任意新标签，多个标签用逗号分隔。",
    )
    st.text_input("新增自由标签", key=f"{items_key}_new", placeholder="输入任意新标签，多个用逗号分隔",
                  on_change=_add_free_tag_item, args=(key_prefix, fhash, fpath))
    st.button("＋ 新增自由标签", key=f"{key_prefix}_custom_tag_add_{fhash}",
              on_click=_add_free_tag_item, args=(key_prefix, fhash, fpath))


def _render_native_question_actions(key_prefix, fhash, fpath, is_tag_editing, is_tex_editing):
    action = None
    st.markdown('<span class="mc-question-actions-grid-anchor"></span>', unsafe_allow_html=True)
    tex_label = "\U0001f4be \u4fdd\u5b58\u4fee\u6539" if is_tex_editing else "\u270f\ufe0f \u5f00\u59cb\u4fee\u6539tex\u5185\u5bb9"
    tex_action = "save_tex" if is_tex_editing else "start_tex_edit"
    tex_type = "primary" if is_tex_editing else "secondary"
    edit_label = "\u2705 \u4fdd\u5b58\u6587\u4ef6\u4fe1\u606f" if is_tag_editing else "\U0001f3f7\ufe0f \u4fee\u6539\u6587\u4ef6\u4fe1\u606f"
    edit_type = "primary" if is_tag_editing else "secondary"
    row1_col1, row1_col2, _ = st.columns([0.475, 0.475, 1.05], gap="small")
    row2_col1, row2_col2, _ = st.columns([0.475, 0.475, 1.05], gap="small")
    row3_delete, _ = st.columns([0.95, 1.05], gap="small")
    with row1_col1:
        if st.button(tex_label, key=f"{key_prefix}_tex_edit_{fhash}", type=tex_type, use_container_width=True, on_click=_set_pending_question_action, args=(key_prefix, fhash, tex_action, fpath)):
            action = tex_action
    with row1_col2:
        if st.button(edit_label, key=f"{key_prefix}_edit_meta_{fhash}", type=edit_type, use_container_width=True, on_click=_set_pending_question_action, args=(key_prefix, fhash, "edit_meta", fpath)):
            action = "edit_meta"
    with row2_col1:
        if st.button("\U0001f916 AI\u751f\u6210\u89e3\u7b54", key=f"{key_prefix}_ai_generate_{fhash}", use_container_width=True, on_click=_set_pending_question_action, args=(key_prefix, fhash, "ai_generate", fpath)):
            action = "ai_generate"
    with row2_col2:
        if st.button("\U0001f5bc\ufe0f \u89e3\u7b54\u56fe\u7247\u8bc6\u522b", key=f"{key_prefix}_image_ocr_{fhash}", use_container_width=True, on_click=_set_pending_question_action, args=(key_prefix, fhash, "image_ocr", fpath)):
            action = "image_ocr"
    with row3_delete:
        st.markdown('<span class="red-btn-hook"></span>', unsafe_allow_html=True)
        if st.button("🗑️ 删除该题及相关信息", key=f"{key_prefix}_delete_{fhash}", use_container_width=True, on_click=_set_pending_question_action, args=(key_prefix, fhash, "delete", fpath)):
            action = "delete"
    return action


@st.fragment
def render_browse_question_editor_card(q_label, content, fpath, key_prefix, paper_type_scope=None, extra_html_label="", rename_paths_key=None, prepared_assets=None, interactive_difficulty=True):
    if not os.path.isfile(fpath):
        st.info("题目已移动或删除，请刷新列表。")
        return
    # Fragment arguments persist across local reruns; check the live file token.
    prepared_assets = load_question_editor_assets(fpath)
    content = prepared_assets['content']
    with st.container(border=True):
        _render_browse_question_editor_card_body(q_label, content, fpath, key_prefix, paper_type_scope,
            extra_html_label, rename_paths_key, prepared_assets, interactive_difficulty)


def _render_browse_question_editor_card_body(q_label, content, fpath, key_prefix, paper_type_scope=None, extra_html_label="", rename_paths_key=None, prepared_assets=None, interactive_difficulty=True):
    prepared_assets = prepared_assets or {}
    fhash = _question_key("meta", fpath)
    _render_question_scroll_anchor(fpath, fhash)
    render_question_header(
        q_label,
        content,
        fpath,
        extra_html_label=extra_html_label,
        compact=True,
        prepared_meta=prepared_assets.get("meta"),
        interactive_difficulty=interactive_difficulty,
    )

    tag_edit_key = f"{key_prefix}_tag_edit_mode_{_question_key('tag', fpath)}"
    tex_edit_key = f"{key_prefix}_tex_edit_mode_{_question_key('tex', fpath)}"
    text_area_key = f"{key_prefix}_edit_{_question_key('text', fpath)}"
    pending_text = st.session_state.pop(f"browse_pending_text_{text_area_key}", None)
    if pending_text is not None:
        st.session_state[text_area_key] = pending_text
        st.session_state[tex_edit_key] = False
    action = _pop_pending_question_action(key_prefix, fhash)
    is_tag_editing = st.session_state.get(tag_edit_key, False)
    is_tex_editing = st.session_state.get(tex_edit_key, False)
    if action == "start_tex_edit":
        st.session_state[tex_edit_key] = True
        st.session_state[text_area_key] = content
        is_tex_editing = True
    elif text_area_key not in st.session_state or not is_tex_editing:
        st.session_state[text_area_key] = content
    current_content = st.session_state.get(text_area_key, content) if is_tex_editing else content
    if current_content == content and prepared_assets.get("editor_height") is not None:
        est_height = prepared_assets["editor_height"]
    else:
        est_height = _cached_editor_height(current_content)

    c_src, c_preview = st.columns([0.95, 1.05], gap="large")
    with c_src:
        if is_tex_editing:
            current_content = st.text_area("源码", height=est_height, key=text_area_key)
        else:
            st.text_area("源码", value=content, height=est_height, disabled=True, key=f"{text_area_key}_readonly")
            current_content = content

        if action == "save_tex":
            new_content = st.session_state.get(text_area_key, current_content)
            if not _duplicate_save_confirmation(fpath, new_content, scope=f"{key_prefix}:{fpath}"):
                st.rerun()
            final_content = save_modified_tex_file(fpath, new_content)
            _update_csv_index_for_content_change(fpath, final_content)
            _clear_advanced_search_result_cache()
            st.session_state[tex_edit_key] = False
            _request_question_scroll_restore(fpath)
            st.toast(f"{q_label} 已保存", icon="✅")
            try:
                st.rerun(scope="fragment")
            except st.errors.StreamlitAPIException:
                # AppTest/full-script invocation has no active fragment rerun scope.
                st.rerun()

        if action == "edit_meta":
            if is_tag_editing:
                base = os.path.basename(fpath).replace(".tex", "")
                parts = base.split("-")
                if len(parts) >= 5:
                    old_year, old_ptype, old_pname, old_pnum, old_subj = parts[0], parts[1], parts[2], parts[3], parts[4]
                    new_year = st.session_state.get(f"{key_prefix}_meta_year_{fhash}", old_year)
                    new_type = st.session_state.get(f"{key_prefix}_meta_type_{fhash}", old_ptype)
                    new_name = st.session_state.get(f"{key_prefix}_meta_paper_{fhash}", old_pname)
                    new_num = st.session_state.get(f"{key_prefix}_meta_num_{fhash}", old_pnum)
                    new_subjects = st.session_state.get(f"{key_prefix}_tag_select_{fhash}", [old_subj])
                    new_subject_str = "，".join(new_subjects) if isinstance(new_subjects, list) else str(new_subjects or old_subj)
                    new_custom_tags = _free_tag_editor_value(key_prefix, fhash)
                    new_knowledge_points = st.session_state.get(f"{key_prefix}_knowledge_points_{fhash}", "")
                    new_physics_methods = st.session_state.get(f"{key_prefix}_physics_methods_{fhash}", "")
                    try:
                        new_question_type = require_question_type(st.session_state.get(f"{key_prefix}_question_type_{fhash}"))
                        rename_result = apply_meta_rename_and_update(fpath, str(new_year), str(new_type), str(new_name), str(new_num), new_subject_str)
                        new_path = rename_result[0] if isinstance(rename_result, tuple) else rename_result
                        update_question_meta_fields(new_path, {
                            "标签": new_custom_tags,
                            "知识点": new_knowledge_points,
                            "物理方法": new_physics_methods,
                            "题型": new_question_type,
                        })
                        if rename_paths_key and new_path:
                            old_list = st.session_state.get(rename_paths_key) or []
                            st.session_state[rename_paths_key] = [new_path if p == fpath else p for p in old_list]
                        if new_path and new_path != fpath:
                            new_text_key = f"{key_prefix}_edit_{_question_key('text', new_path)}"
                            st.session_state[new_text_key] = st.session_state.get(text_area_key, current_content)
                        _clear_free_tag_editor(key_prefix, fhash)
                        st.session_state[tag_edit_key] = False
                        _request_question_scroll_restore(new_path)
                        st.toast("修改成功！", icon="✅")
                        st.rerun()
                    except Exception as e:
                        st.error(f"修改失败: {e}")
                else:
                    st.error("文件名格式不支持修改")
            else:
                st.session_state[tag_edit_key] = True
                _request_question_scroll_restore(fpath)
                st.rerun()

        if action == "ai_generate":
            problem_tex = _extract_problem_env(current_content)
            with st.spinner("🤖 AI 正在生成解答..."):
                res = call_ai_for_answer_solutions(problem_tex, fast=False)
            if "error" in res:
                st.toast(res["error"], icon="❌")
            else:
                combined = _normalize_ai_generated_tex_for_preview(res["answer_tex"].strip() + "\n\n" + res["solutions_tex"].strip())
                _, data_key, editor_key = _ai_sol_keys(fpath, "ai_solution_v1")
                st.session_state[data_key] = {"answer_tex": res["answer_tex"], "solutions_tex": res["solutions_tex"]}
                st.session_state[editor_key] = combined
                _request_question_scroll_restore(fpath)
                st.toast("已生成解答（未写回文件）", icon="🪄")
                st.rerun()

        if action == "image_ocr":
            ai_hash, _, _ = _ai_sol_keys(fpath, "ai_solution_v1")
            upload_open_key = f"ai_sol_upload_open_{ai_hash}"
            st.session_state[upload_open_key] = not st.session_state.get(upload_open_key, False)
            _request_question_scroll_restore(fpath)
            st.rerun()

        if action == "delete":
            if st.session_state.get(DELETE_SKIP_CONFIRM_KEY):
                _execute_delete_question_from_ui(fpath, q_label)
            else:
                confirm_delete_question_dialog(fpath, q_label, fhash)

        if is_tag_editing:
            base = os.path.basename(fpath).replace(".tex", "")
            parts = base.split("-")
            cur_year = parts[0] if len(parts) >= 5 else ""
            cur_type = parts[1] if len(parts) >= 5 else "G"
            cur_paper = parts[2] if len(parts) >= 5 else ""
            cur_num = parts[3] if len(parts) >= 5 else ""
            cur_subjects = (parts[4] if len(parts) >= 5 else "").split("，")
            valid_tags = [t for t in cur_subjects if t in SUBJECTS] or [SUBJECTS[0]]
            current_label_meta = prepared_assets.get("meta") or _cached_question_meta(content)
            type_opts = _editable_paper_type_options(paper_type_scope)
            st.text_input("年份", value=str(cur_year), key=f"{key_prefix}_meta_year_{fhash}")
            if cur_type not in type_opts:
                cur_type = type_opts[0]
            st.selectbox("试卷类别", options=type_opts, index=type_opts.index(cur_type), format_func=lambda x: f"{x} ({PAPER_TYPES[x]})", key=f"{key_prefix}_meta_type_{fhash}")
            st.text_input("试卷名称", value=str(cur_paper), key=f"{key_prefix}_meta_paper_{fhash}")
            st.text_input("题号", value=str(cur_num), key=f"{key_prefix}_meta_num_{fhash}")
            st.multiselect("知识板块 (首个为主)", options=SUBJECTS, default=valid_tags, key=f"{key_prefix}_tag_select_{fhash}")
            st.selectbox("题型（必选）", QUESTION_TYPES, index=QUESTION_TYPES.index(infer_question_type(content, "，".join(cur_subjects))),
                         key=f"{key_prefix}_question_type_{fhash}")
            _render_free_tag_editor(key_prefix, fhash, current_label_meta.get("标签", ""), fpath)
            st.text_input(
                "知识点",
                value=current_label_meta.get("知识点", ""),
                key=f"{key_prefix}_knowledge_points_{fhash}",
                help="使用中英文逗号分隔。",
            )
            st.text_input(
                "物理方法",
                value=current_label_meta.get("物理方法", ""),
                key=f"{key_prefix}_physics_methods_{fhash}",
                help="使用中英文逗号分隔。",
            )

        render_ai_solution_image_ocr_section(fpath, key_prefix="ai_solution_v1", compact=True)

    with c_preview:
        st.markdown(f'<span id="browse-question-preview-{fhash}" style="display:block"></span>', unsafe_allow_html=True)
        try:
            prepared_markdown = None
            if current_content == content:
                prepared_markdown = prepared_assets.get("preview_markdown")
            render_question_preview(current_content, show_title=False, prepared_markdown=prepared_markdown)
        except Exception as e:
            st.error(f"渲染错误: {e}")

    _render_native_question_actions(key_prefix, fhash, fpath, is_tag_editing, is_tex_editing)

    render_ai_solution_panel(fpath, q_label, key_prefix="ai_solution_v1")

    # === 物理/数学插图智能矫正 Agent (Pi-Agent 架构，全局浏览与编辑对话框) ===
    def _on_browse_diagram_apply(updated_text):
        final_content = save_modified_tex_file(fpath, updated_text)
        _update_csv_index_for_content_change(fpath, final_content)
        _clear_advanced_search_result_cache()
        # This apply handler runs after the editor widget is instantiated.
        st.session_state[f"browse_pending_text_{text_area_key}"] = final_content
        _request_question_scroll_restore(fpath)
        st.toast(f"{q_label} 插图已保存并同步", icon="✅")
        st.rerun()

    agent_open = st.checkbox("🤖 打开物理插图矫正 AGENT", key=f"browse_agent_open_{fhash}")
    if agent_open:
        render_diagram_agent_dialog(
            content_state_key=text_area_key,
            key_prefix=f"browse_agent_{fhash}",
            base_dir=APP_ROOT,
            use_expander=False,
            target_file_path=fpath,
            initial_content=current_content,
            on_apply_callback=_on_browse_diagram_apply,
        )


NORTHEAST_PHYSICS_CHOICE_SECTION = (
    "选择题：本题共 10 小题，共 46 分。在每小题给出的四个选项中，第 1～7 题只有一项符合题目要求，"
    "每小题 4 分；第 8～10 题有多项符合题目要求，每小题 6 分，全部选对得 6 分，选对但不全得 3 分，"
    "有选错得 0 分。"
)
NORTHEAST_PHYSICS_NON_CHOICE_SECTION = (
    "非选择题：本题共 5 小题，共 54 分。第 11～12 题为实验题，第 13～15 题为计算题。"
    "解答应写出必要的文字说明、方程式和重要演算步骤。"
)


def sync_exam_blocks_to_selected_order(selected_paths):
    """Keep final typesetting order aligned with the floating basket order."""
    selected_paths = list(selected_paths or [])
    blocks = list(st.session_state.get("exam_blocks", []))
    existing_questions = {}
    for block in blocks:
        if block.get("type") == "question" and block.get("path") in selected_paths and block.get("path") not in existing_questions:
            existing_questions[block["path"]] = block

    ordered_questions = [
        existing_questions.get(path) or {"id": str(uuid.uuid4()), "type": "question", "path": path}
        for path in selected_paths
    ]

    # 试卷模板默认直接套用东北黑吉辽蒙物理卷的两个题区，不再要求用户手动插入。
    if "试卷类" in st.session_state.get("exam_theme", "") and not any(
        block.get("type") == "section" for block in blocks
    ):
        st.session_state["exam_blocks"] = [
            {"id": str(uuid.uuid4()), "type": "section", "title": NORTHEAST_PHYSICS_CHOICE_SECTION},
            *ordered_questions[:10],
            {"id": str(uuid.uuid4()), "type": "section", "title": NORTHEAST_PHYSICS_NON_CHOICE_SECTION},
            *ordered_questions[10:],
        ]
        return

    ordered_iter = iter(ordered_questions)
    synced_blocks = []
    for block in blocks:
        if block.get("type") == "question":
            next_block = next(ordered_iter, None)
            if next_block is not None:
                synced_blocks.append(next_block)
        elif block.get("type") in ("chapter", "section", "subsection"):
            synced_blocks.append(block)
    synced_blocks.extend(list(ordered_iter))
    st.session_state["exam_blocks"] = synced_blocks


@st.fragment
def render_exam_floating_basket():
    selected_paths = st.session_state.setdefault("exam_selected_qs", [])
    selected_count = len(selected_paths)
    if "exam_basket_open" not in st.session_state:
        # An empty floating panel otherwise covers the template/AI toolbar.
        st.session_state["exam_basket_open"] = bool(selected_paths)
    if "exam_expanded_q" not in st.session_state:
        st.session_state["exam_expanded_q"] = selected_paths[0] if selected_paths else None
    if st.session_state.get("exam_expanded_q") not in selected_paths:
        st.session_state["exam_expanded_q"] = selected_paths[0] if selected_paths else None
    st.markdown(
        """
        <style>
        body:has(#mc-exam-page-anchor) div[data-testid="stVerticalBlockBorderWrapper"]:has(.mc-exam-floating-basket-anchor):not(:has(div[data-testid="stVerticalBlockBorderWrapper"] .mc-exam-floating-basket-anchor)),
        .mc-exam-floating-basket-panel {
            position: fixed !important;
            right: 16px !important;
            left: auto !important;
            top: 104px !important;
            width: 560px !important;
            height: 150px !important;
            max-height: none !important;
            min-height: 150px !important;
            z-index: 1001 !important;
            overflow: auto !important;
            padding: 0.95rem !important;
            border: 1px solid var(--mc-border-strong) !important;
            border-radius: 12px !important;
            background: var(--mc-surface) !important;
            background-color: var(--mc-surface) !important;
            background-clip: padding-box !important;
            isolation: isolate !important;
            opacity: 1 !important;
            box-shadow: 0 20px 48px rgba(0, 0, 0, 0.46) !important;
        }
        body:has(#mc-exam-page-anchor) div[data-testid="stVerticalBlockBorderWrapper"]:has(.mc-exam-floating-basket-anchor):not(:has(div[data-testid="stVerticalBlockBorderWrapper"] .mc-exam-floating-basket-anchor))::before,
        .mc-exam-floating-basket-panel::before {
            content: "";
            position: absolute;
            inset: 0;
            z-index: 0;
            border-radius: 12px;
            background: var(--mc-surface);
            pointer-events: none;
        }
        body:has(#mc-exam-page-anchor) div[data-testid="stVerticalBlockBorderWrapper"]:has(.mc-exam-floating-basket-anchor):not(:has(div[data-testid="stVerticalBlockBorderWrapper"] .mc-exam-floating-basket-anchor)) > *,
        .mc-exam-floating-basket-panel > * {
            position: relative;
            z-index: 1;
        }
        .mc-exam-floating-basket-panel > div,
        .mc-exam-floating-basket-panel > div[data-testid="stVerticalBlock"],
        .mc-exam-floating-basket-panel div[data-testid="stVerticalBlock"],
        .mc-exam-floating-basket-panel div[data-testid="stHorizontalBlock"],
        .mc-exam-floating-basket-panel div[data-testid="column"],
        .mc-exam-floating-basket-panel div[data-testid="element-container"] {
            background: transparent !important;
            background-color: transparent !important;
            opacity: 1 !important;
        }
        .mc-exam-floating-basket-panel::after {
            content: "";
            position: absolute;
            right: 3px;
            bottom: 3px;
            width: 18px;
            height: 18px;
            background:
                linear-gradient(to right, transparent calc(100% - 2px), rgba(109, 40, 217, 0.50) calc(100% - 2px)),
                linear-gradient(to bottom, transparent calc(100% - 2px), rgba(109, 40, 217, 0.50) calc(100% - 2px));
            border-radius: 0 0 8px 0;
            cursor: nwse-resize;
            z-index: 3;
        }
        .mc-exam-floating-basket-collapsed-panel {
            position: relative !important;
            right: auto !important;
            top: auto !important;
            width: 142px !important;
            max-width: 100% !important;
            margin-left: auto !important;
            z-index: 1001 !important;
            padding: 0 !important;
            border: 0 !important;
            background: transparent !important;
            box-shadow: none !important;
        }
        .mc-exam-floating-basket-panel h4 {
            margin: 0 !important;
            cursor: move !important;
            user-select: none !important;
            color: var(--mc-text) !important;
        }
        .mc-exam-floating-basket-panel div[data-testid="stHorizontalBlock"]:has(.mc-exam-basket-list-anchor) {
            gap: 1rem !important;
            align-items: start !important;
        }
        .mc-exam-floating-basket-panel div[data-testid="column"]:has(.mc-exam-basket-preview-anchor) {
            border-right: 1px solid var(--mc-border) !important;
            padding-right: 1rem !important;
            max-height: calc(100vh - 270px) !important;
            overflow: auto !important;
        }
        .mc-exam-floating-basket-panel div[data-testid="column"]:has(.mc-exam-basket-list-anchor) {
            padding-left: 0.05rem !important;
        }
        .mc-exam-floating-basket-panel div[data-testid="stButton"] > button,
        .mc-exam-floating-basket-collapsed-panel div[data-testid="stButton"] > button {
            width: 100% !important;
            min-height: 2.35rem !important;
            border-radius: 9px !important;
            white-space: normal !important;
        }
        .mc-exam-floating-basket-collapsed-panel div[data-testid="stButton"] > button {
            border: 1px solid var(--mc-action-border) !important;
            background: var(--mc-action-bg) !important;
            color: var(--mc-action-text) !important;
            box-shadow: var(--mc-action-shadow) !important;
            font-weight: 750 !important;
        }
        @media (max-width: 980px) {
            .mc-exam-floating-basket-panel {
                left: 72px !important;
                right: 12px !important;
                width: auto !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    components.html(
        """
        <script>
        (() => {
            const doc = window.parent.document;
            const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

            function findPanel() {
                const marker = doc.querySelector('.mc-exam-floating-basket-anchor');
                return marker ? marker.closest('div[data-testid="stVerticalBlockBorderWrapper"]') : null;
            }

            function markPanels() {
                doc.querySelectorAll('.mc-exam-floating-basket-panel').forEach((node) => {
                    if (!node.querySelector('.mc-exam-floating-basket-anchor')) {
                        node.classList.remove('mc-exam-floating-basket-panel');
                    }
                });
                doc.querySelectorAll('.mc-exam-floating-basket-collapsed-panel').forEach((node) => {
                    if (!node.querySelector('.mc-exam-floating-basket-collapsed-anchor')) {
                        node.classList.remove('mc-exam-floating-basket-collapsed-panel');
                    }
                });
                const panel = findPanel();
                if (panel) panel.classList.add('mc-exam-floating-basket-panel');
                const collapsedMarker = doc.querySelector('.mc-exam-floating-basket-collapsed-anchor');
                const collapsedPanel = collapsedMarker ? collapsedMarker.closest('div[data-testid="stVerticalBlockBorderWrapper"]') : null;
                if (collapsedPanel) collapsedPanel.classList.add('mc-exam-floating-basket-collapsed-panel');
                return panel;
            }

            function applyWidth(panel) {
                const sizeVersion = 'compact-v1';
                if (window.parent.localStorage.getItem('mcExamBasketSizeVersion') !== sizeVersion) {
                    window.parent.localStorage.removeItem('mcExamBasketWidth');
                    window.parent.localStorage.removeItem('mcExamBasketHeight');
                    window.parent.localStorage.setItem('mcExamBasketSizeVersion', sizeVersion);
                }
                const saved = Number(window.parent.localStorage.getItem('mcExamBasketWidth') || 0);
                if (saved > 0) {
                    panel.style.setProperty('width', `${clamp(saved, 560, Math.max(620, window.parent.innerWidth - 104))}px`, 'important');
                } else {
                    panel.style.setProperty('width', '560px', 'important');
                }
                const savedHeight = Number(window.parent.localStorage.getItem('mcExamBasketHeight') || 0);
                if (savedHeight > 0) {
                    panel.style.setProperty('height', `${clamp(savedHeight, 150, Math.max(180, window.parent.innerHeight - 116))}px`, 'important');
                    panel.style.setProperty('max-height', 'none', 'important');
                } else {
                    panel.style.setProperty('height', '150px', 'important');
                    panel.style.setProperty('max-height', 'none', 'important');
                }
            }

            function applyPosition(panel) {
                if (window.parent.innerWidth <= 1180) {
                    // On small screens the expanded basket docks in normal flow.
                    // Do not let the desktop drag position cover form controls.
                    for (const [key,value] of Object.entries({position:'relative',left:'auto',right:'auto',top:'auto',width:'100%','min-width':'0','max-width':'100%',height:'auto'})) {
                        panel.style.setProperty(key,value,'important');
                    }
                    return;
                }
                panel.style.setProperty('position','fixed','important');
                const savedX = Number(window.parent.localStorage.getItem('mcExamBasketX'));
                const savedY = Number(window.parent.localStorage.getItem('mcExamBasketY'));
                const hasSavedPosition = window.parent.localStorage.getItem('mcExamBasketPositionReady') === '1';
                if (hasSavedPosition && Number.isFinite(savedX) && Number.isFinite(savedY) && savedX >= 0 && savedY >= 0) {
                    const rect = panel.getBoundingClientRect();
                    const maxX = Math.max(72, window.parent.innerWidth - rect.width - 12);
                    const maxY = Math.max(12, window.parent.innerHeight - 90);
                    panel.style.setProperty('left', `${clamp(savedX, 72, maxX)}px`, 'important');
                    panel.style.setProperty('top', `${clamp(savedY, 12, maxY)}px`, 'important');
                    panel.style.setProperty('right', 'auto', 'important');
                } else {
                    panel.style.setProperty('left', 'auto', 'important');
                    panel.style.setProperty('right', '16px', 'important');
                    panel.style.setProperty('top', '104px', 'important');
                }
            }

            function bind() {
                const panel = markPanels();
                if (!panel || panel.dataset.mcResizeBound === '1') {
                    if (panel) {
                        applyWidth(panel);
                        applyPosition(panel);
                    }
                    return;
                }
                panel.dataset.mcResizeBound = '1';
                panel.style.setProperty('resize', 'none', 'important');
                panel.style.setProperty('min-width', '560px', 'important');
                panel.style.setProperty('max-width', 'calc(100vw - 104px)', 'important');
                panel.style.setProperty('min-height', '150px', 'important');
                applyWidth(panel);
                applyPosition(panel);

                let cornerResizing = false;
                let moving = false;
                let moveOffsetX = 0;
                let moveOffsetY = 0;
                let resizeStartX = 0;
                let resizeStartY = 0;
                let resizeStartWidth = 0;
                let resizeStartHeight = 0;
                panel.addEventListener('pointerdown', (event) => {
                    const rect = panel.getBoundingClientRect();
                    if (rect.right - event.clientX <= 22 && rect.bottom - event.clientY <= 22) {
                        cornerResizing = true;
                        resizeStartX = event.clientX;
                        resizeStartY = event.clientY;
                        resizeStartWidth = rect.width;
                        resizeStartHeight = rect.height;
                        panel.setPointerCapture(event.pointerId);
                        event.preventDefault();
                        return;
                    }
                    const heading = event.target.closest('h4');
                    if (!heading) return;
                    moving = true;
                    moveOffsetX = event.clientX - rect.left;
                    moveOffsetY = event.clientY - rect.top;
                    panel.setPointerCapture(event.pointerId);
                    event.preventDefault();
                });
                panel.addEventListener('pointermove', (event) => {
                    if (cornerResizing) {
                        const nextWidth = clamp(resizeStartWidth + event.clientX - resizeStartX, 560, Math.max(620, window.parent.innerWidth - panel.getBoundingClientRect().left - 12));
                        const nextHeight = clamp(resizeStartHeight + event.clientY - resizeStartY, 150, Math.max(180, window.parent.innerHeight - panel.getBoundingClientRect().top - 12));
                        panel.style.setProperty('width', `${nextWidth}px`, 'important');
                        panel.style.setProperty('height', `${nextHeight}px`, 'important');
                        panel.style.setProperty('max-height', 'none', 'important');
                        window.parent.localStorage.setItem('mcExamBasketWidth', String(nextWidth));
                        window.parent.localStorage.setItem('mcExamBasketHeight', String(nextHeight));
                        return;
                    }
                    if (!moving) return;
                    const rect = panel.getBoundingClientRect();
                    const nextX = clamp(event.clientX - moveOffsetX, 72, Math.max(72, window.parent.innerWidth - rect.width - 12));
                    const nextY = clamp(event.clientY - moveOffsetY, 12, Math.max(12, window.parent.innerHeight - 90));
                    panel.style.setProperty('left', `${nextX}px`, 'important');
                    panel.style.setProperty('top', `${nextY}px`, 'important');
                    panel.style.setProperty('right', 'auto', 'important');
                    window.parent.localStorage.setItem('mcExamBasketX', String(nextX));
                    window.parent.localStorage.setItem('mcExamBasketY', String(nextY));
                    window.parent.localStorage.setItem('mcExamBasketPositionReady', '1');
                });
                panel.addEventListener('pointerup', () => { cornerResizing = false; moving = false; });
                panel.addEventListener('pointercancel', () => { cornerResizing = false; moving = false; });
            }

            if (window.parent.__mcExamBasketObserver) {
                window.parent.__mcExamBasketObserver.disconnect();
            }
            let frame=0;
            let observer;
            const stats=window.parent.__mcObserverStats ||= {meta:0,basket:0};
            const update=()=>{frame=0;stats.basket++;bind();};
            const timer=window.parent.setTimeout(()=>{
                update();
                const marker=doc.querySelector('.mc-exam-floating-basket-anchor,.mc-exam-floating-basket-collapsed-anchor');
                const scope=marker?.closest('[data-testid="stVerticalBlockBorderWrapper"]')?.parentElement;
                if(scope){
                    observer=new window.parent.MutationObserver(()=>{
                        if(!frame)frame=window.parent.requestAnimationFrame(update);
                    });
                    observer.observe(scope,{childList:true,subtree:true});
                    window.parent.__mcExamBasketObserver=observer;
                }
            },80);
            window.addEventListener('pagehide',()=>{
                window.parent.clearTimeout(timer);
                if(frame)window.parent.cancelAnimationFrame(frame);
                observer?.disconnect();
            },{once:true});
        })();
        </script>
        """,
        height=0,
    )

    if not st.session_state["exam_basket_open"]:
        with st.container(border=True):
            st.markdown('<span class="mc-exam-floating-basket-collapsed-anchor"></span>', unsafe_allow_html=True)
            if st.button(f"🧺 试题篮 · {selected_count}", key="mc_exam_basket_open", use_container_width=True):
                st.session_state["exam_basket_open"] = True
                st.rerun(scope="fragment")
        return

    with st.container(border=True):
        st.markdown('<span class="mc-exam-floating-basket-anchor"></span>', unsafe_allow_html=True)
        h1, h2 = st.columns([5, 1], vertical_alignment="center")
        with h1:
            st.markdown(f"#### 🧺 试题篮 ({selected_count}/{st.session_state.get('exam_q_count_input', 10)})")
        with h2:
            if st.button("收起", key="mc_exam_basket_close", use_container_width=True):
                st.session_state["exam_basket_open"] = False
                st.rerun(scope="fragment")

        if selected_count <= 0:
            st.caption("暂未选择任何题目")
            return

        if st.button("✨ 选题完成，进入排版工作台", type="primary", key="mc_exam_basket_done", use_container_width=True):
            sync_exam_blocks_to_selected_order(selected_paths)
            st.session_state["exam_mode_stage"] = "typesetting"
            st.rerun()

        c_preview, c_list = st.columns([1.15, 1], gap="large")
        with c_preview:
            st.markdown('<span class="mc-exam-basket-preview-anchor"></span>', unsafe_allow_html=True)
            expanded_q = st.session_state.get("exam_expanded_q")
            if expanded_q and os.path.exists(expanded_q):
                st.caption("👁 已选问题预览")
                try:
                    with open(expanded_q, "r", encoding="utf-8") as f:
                        expanded_content = f.read()
                    st.markdown(latex_to_markdown(expanded_content), unsafe_allow_html=True)
                except Exception as e:
                    st.error(f"无法读取文件: {e}")
            else:
                st.caption("选择右侧题目查看预览")

        with c_list:
            st.markdown('<span class="mc-exam-basket-list-anchor"></span>', unsafe_allow_html=True)
            items = [
                {"id": p, "label": f"{i + 1}. {os.path.basename(p).replace('.tex', '')}"}
                for i, p in enumerate(selected_paths)
            ]
            result = st_sortable_list(items, key="exam_basket_sortable")
            if isinstance(result, dict):
                removed = result.get("removed")
                order = result.get("order") or []
                selected = result.get("selected")
                if removed and removed in selected_paths:
                    st.session_state["exam_selected_qs"] = [p for p in selected_paths if p != removed]
                    sync_exam_blocks_to_selected_order(st.session_state["exam_selected_qs"])
                    if st.session_state.get("exam_expanded_q") == removed:
                        remaining = st.session_state["exam_selected_qs"]
                        st.session_state["exam_expanded_q"] = remaining[0] if remaining else None
                    if st.session_state.get("ai_exam_active"):
                        st.session_state["ai_exam_modified"] = True
                    st.rerun(scope="fragment")
                if order and order != selected_paths:
                    valid = [p for p in order if p in selected_paths]
                    missing = [p for p in selected_paths if p not in valid]
                    st.session_state["exam_selected_qs"] = valid + missing
                    sync_exam_blocks_to_selected_order(st.session_state["exam_selected_qs"])
                    if st.session_state.get("ai_exam_active"):
                        st.session_state["ai_exam_modified"] = True
                    st.rerun(scope="fragment")
                if selected and selected in selected_paths and selected != st.session_state.get("exam_expanded_q"):
                    st.session_state["exam_expanded_q"] = selected
                    st.rerun(scope="fragment")


# ================= 页面：浏览/编辑 =================
def _time_browse_key(row):
    return row.get("初次录入的时间", "") or row.get("最后修改时间", "")


def render_time_browse_header(page_title, paper_type_scope):
    """按录入顺序浏览：标题 + 浏览设置（排序 / 每页数量 / 页码 / 翻页）收进同一行，小字号均匀排布。"""
    try:
        from utils.csv_ops import read_csv_index
        csv_data = _filter_question_rows(read_csv_index(), paper_type_scope)
    except Exception as exc:
        csv_data = []
        st.error(f"读取索引失败: {exc}")
    c_title, c_sort, c_count, c_page, c_nav = st.columns(
        [1.55, 1.55, 1.3, 0.62, 1.05], gap="small", vertical_alignment="center"
    )
    with c_title:
        st.markdown('<span class="mc-browse-settings-anchor"></span>', unsafe_allow_html=True)
        st.header(page_title)
    with c_sort:
        sort_order = st.radio(
            "排序方式",
            ["最新录入在最前", "最早录入在最前"],
            horizontal=True,
            key="time_sort_order",
        )
    with c_count:
        max_show = st.slider(
            "每页数量（5–20）", min_value=5, max_value=20, value=10, step=1, key="time_max_show"
        )
    if st.session_state.get("time_max_show_prev") != max_show:
        st.session_state["time_browse_page"] = 1
        st.session_state["time_max_show_prev"] = max_show
    sorted_data = (
        sorted(csv_data, key=_time_browse_key, reverse=(sort_order == "最新录入在最前"))
        if csv_data else []
    )
    total_count = len(sorted_data)
    total_pages = max(1, (total_count + max_show - 1) // max_show)
    current_page = int(st.session_state.get("time_browse_page", 1) or 1)
    current_page = max(1, min(total_pages, current_page))
    st.session_state["time_browse_page"] = current_page
    with c_page:
        st.markdown(
            f'<div class="mc-browse-page-indicator">第 {current_page} / {total_pages} 页 · 每页 {max_show} 道</div>',
            unsafe_allow_html=True,
        )
    with c_nav:
        p_prev, p_next = st.columns(2, gap="small")
        with p_prev:
            if st.button("⬅️ 上一页", key="time_browse_prev", disabled=(current_page <= 1), use_container_width=True):
                st.session_state["time_browse_page"] = current_page - 1
                st.rerun()
        with p_next:
            if st.button("下一页 ➡️", key="time_browse_next", disabled=(current_page >= total_pages), use_container_width=True):
                st.session_state["time_browse_page"] = current_page + 1
                st.rerun()
    return {
        "csv_data": csv_data,
        "sorted_data": sorted_data,
        "max_show": max_show,
        "total_count": total_count,
        "total_pages": total_pages,
        "current_page": current_page,
    }


def render_time_browse_list(ctx, is_exam_mode, is_delete_mode, paper_type_scope):
    """按录入顺序浏览的题目列表（浏览设置所在位置不固定，列表渲染独立出来复用）。"""
    csv_data = ctx["csv_data"]
    sorted_data = ctx["sorted_data"]
    max_show = ctx["max_show"]
    current_page = ctx["current_page"]
    if not csv_data:
        st.info("题库为空或索引未建立，请先一键重建题库索引。")
        return
    total_count = len(sorted_data)
    start_idx = (current_page - 1) * max_show
    end_idx = min(start_idx + max_show, total_count)
    display_data = sorted_data[start_idx:end_idx]

    st.markdown(
        f'<div class="mc-browse-count">共找到 {total_count} 道题目，当前展示第 {current_page} 页。</div>',
        unsafe_allow_html=True,
    )

    for i, row in enumerate(display_data):
        fpath = os.path.join(CHAPTERS_DIR, row["相对文件路径"])
        if not os.path.exists(fpath):
            continue

        fname = row["文件名称"]
        q_label = format_question_title(fname)

        # 增加时间标识显示
        time_str = row.get("初次录入的时间", "") or row.get("最后修改时间", "")
        extra_label = ""
        if time_str:
            extra_label = f"<span style='font-size:0.5em; color:gray; font-weight:normal; margin-left: 10px;'>🕒 {time_str}</span>"

        lazy_key = hashlib.md5(f"time_browse:{fpath}".encode()).hexdigest()[:10]

        if is_delete_mode:
            content = read_question_text(fpath)
            render_delete_question_item(fpath, q_label, content, key_prefix="delete_time", extra_html_label=extra_label)
            st.divider()
            continue

        if is_exam_mode:
            content = read_question_text(fpath)
            render_exam_question_card(q_label, content, fpath, f"exam_action_time_{fpath}")
            continue

        prepared_assets = load_question_editor_assets(fpath)
        content = prepared_assets["content"]
        render_browse_question_editor_card(
            q_label,
            content,
            fpath,
            f"time_{lazy_key}",
            paper_type_scope=paper_type_scope,
            extra_html_label=extra_label,
            prepared_assets=prepared_assets,
        )
        st.divider()


def page_browse(is_exam_mode=False, is_delete_mode=False, paper_type_scope=None, page_title=None):
    """Browse questions; default scope excludes WK, while the cloze library passes 'WK'."""
    is_cloze_library = paper_type_scope == "WK"
    page_title = page_title or ("🧩 挖空题库" if is_cloze_library else "🔍 全局浏览与编辑")
    _time_browse_ctx = None
    st.markdown('<span class="mc-browse-page-anchor"></span>', unsafe_allow_html=True)
    if is_exam_mode:
        st.markdown("""
        <style>
        [data-testid="stVerticalBlockBorderWrapper"]:has(.mc-exam-card-anchor) {
            border-color: var(--mc-border) !important;
            border-radius: 10px !important;
            background: transparent !important;
        }
        div[data-testid="stVerticalBlock"]:has(.mc-exam-card-anchor) {
            gap: 0.65rem !important;
        }
        div[data-testid="stVerticalBlock"]:has(.mc-exam-card-anchor) .mc-exam-card-title {
            color: var(--mc-text) !important;
            font-size: 1.08rem;
            font-weight: 700;
            line-height: 1.35;
            margin: 0 0 0.2rem;
        }
        div[data-testid="stVerticalBlock"]:has(.mc-exam-card-anchor) .mc-exam-card-state {
            display: inline-flex;
            align-items: center;
            padding: 0.16rem 0.48rem;
            border-radius: 999px;
            font-size: 0.75rem;
            line-height: 1.25;
        }
        div[data-testid="stVerticalBlock"]:has(.mc-exam-card-anchor) .mc-exam-card-state.available {
            color: var(--mc-muted);
            background: var(--mc-surface-soft);
            border: 1px solid var(--mc-border);
        }
        div[data-testid="stVerticalBlock"]:has(.mc-exam-card-anchor) .mc-exam-card-state.selected {
            color: #059669;
            background: rgba(16, 185, 129, 0.14);
            border: 1px solid rgba(16, 185, 129, 0.3);
        }
        div[data-testid="stVerticalBlock"]:has(.mc-exam-card-anchor) .mc-exam-card-meta {
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem 1.25rem;
            padding: 0.55rem 0.7rem;
            border-top: 1px solid var(--mc-border);
            border-bottom: 1px solid var(--mc-border);
            color: var(--mc-muted);
            font-size: 0.82rem;
            line-height: 1.4;
        }
        div[data-testid="stVerticalBlock"]:has(.mc-exam-card-anchor) .mc-exam-card-meta strong {
            color: var(--mc-text) !important;
            font-weight: 650;
        }
        div[data-testid="stVerticalBlock"]:has(.mc-exam-card-anchor) .mc-exam-card-preview-label {
            color: var(--mc-accent-dark) !important;
            font-size: 0.88rem;
            font-weight: 700;
            margin-top: 0.1rem;
        }
        div[data-testid="stVerticalBlock"]:has(.mc-exam-card-anchor) div[data-testid="stButton"] > button {
            min-height: 2.3rem !important;
            white-space: nowrap !important;
        }
        div[data-testid="stVerticalBlock"]:has(.mc-exam-card-anchor) div[data-testid="stMarkdownContainer"] p {
            line-height: 1.65 !important;
        }
        @media (max-width: 760px) {
            div[data-testid="stVerticalBlock"]:has(.mc-exam-card-anchor) .mc-exam-card-meta {
                gap: 0.35rem 0.8rem;
            }
        }
        </style>
        """, unsafe_allow_html=True)
    if is_delete_mode:
        st.markdown("""
        <style>
        div[data-testid="element-container"]:has(.red-btn-hook) + div[data-testid="stButton"] > button,
        div[data-testid="element-container"]:has(.red-btn-hook) + div[data-testid="element-container"] div[data-testid="stButton"] > button,
        div[class*="st-key-delete_mode_exit_btn_wrap"] button,
        div[class*="st-key-restore_deleted_close_wrap"] button,
        div[class*="st-key-backup_manager_close_wrap"] button,
        div[class*="st-key-backup_manager_clear_ok_wrap"] button,
        div[class*="st-key-backup_delete_wrap_"] button,
        div[class*="st-key-delete_mode_exit_btn"] button,
        div[class*="st-key-restore_deleted_close"] button,
        div[class*="st-key-backup_manager_close"] button,
        div[class*="st-key-backup_manager_clear_ok"] button,
        div[class*="st-key-backup_delete_"] button {
            background-color: #d73a49 !important;
            border-color: #d73a49 !important;
            color: #ffffff !important;
            font-weight: 700 !important;
        }
        div[data-testid="element-container"]:has(.red-btn-hook) + div[data-testid="stButton"] > button:hover,
        div[data-testid="element-container"]:has(.red-btn-hook) + div[data-testid="element-container"] div[data-testid="stButton"] > button:hover,
        div[class*="st-key-delete_mode_exit_btn_wrap"] button:hover,
        div[class*="st-key-restore_deleted_close_wrap"] button:hover,
        div[class*="st-key-backup_manager_close_wrap"] button:hover,
        div[class*="st-key-backup_manager_clear_ok_wrap"] button:hover,
        div[class*="st-key-backup_delete_wrap_"] button:hover,
        div[class*="st-key-delete_mode_exit_btn"] button:hover,
        div[class*="st-key-restore_deleted_close"] button:hover,
        div[class*="st-key-backup_manager_close"] button:hover,
        div[class*="st-key-backup_manager_clear_ok"] button:hover,
        div[class*="st-key-backup_delete_"] button:hover {
            background-color: #b92534 !important;
            border-color: #b92534 !important;
            color: #ffffff !important;
        }
        div[data-testid="column"]:has(.delete-exit-btn-hook) div[data-testid="stButton"] > button,
        div[data-testid="element-container"]:has(.delete-exit-btn-hook) + div[data-testid="stButton"] > button,
        div[data-testid="element-container"]:has(.delete-exit-btn-hook) + div[data-testid="element-container"] div[data-testid="stButton"] > button,
        div[class*="st-key-delete_mode_exit_btn_wrap"] button,
        div[class*="st-key-restore_deleted_close_wrap"] button,
        div[class*="st-key-backup_manager_close_wrap"] button {
            min-height: 58px !important;
            height: 58px !important;
            padding: 0.25rem 0.35rem !important;
            background-color: #d73a49 !important;
            border-color: #d73a49 !important;
            color: #ffffff !important;
            border-radius: 8px !important;
            font-weight: 750 !important;
            line-height: 1.15 !important;
            white-space: normal !important;
        }
        div[data-testid="column"]:has(.delete-exit-btn-hook) div[data-testid="stButton"] > button:hover,
        div[data-testid="element-container"]:has(.delete-exit-btn-hook) + div[data-testid="stButton"] > button:hover,
        div[data-testid="element-container"]:has(.delete-exit-btn-hook) + div[data-testid="element-container"] div[data-testid="stButton"] > button:hover,
        div[class*="st-key-delete_mode_exit_btn_wrap"] button:hover,
        div[class*="st-key-restore_deleted_close_wrap"] button:hover,
        div[class*="st-key-backup_manager_close_wrap"] button:hover {
            background-color: #b92534 !important;
            border-color: #b92534 !important;
            color: #ffffff !important;
        }
        div[data-testid="column"]:has(.delete-exit-btn-hook) div[data-testid="stButton"] > button p,
        div[data-testid="element-container"]:has(.delete-exit-btn-hook) + div[data-testid="stButton"] > button p,
        div[data-testid="element-container"]:has(.delete-exit-btn-hook) + div[data-testid="element-container"] div[data-testid="stButton"] > button p,
        div[class*="st-key-delete_mode_exit_btn_wrap"] button p,
        div[class*="st-key-restore_deleted_close_wrap"] button p,
        div[class*="st-key-backup_manager_close_wrap"] button p {
            white-space: pre-line !important;
            line-height: 1.15 !important;
        }
        div[data-testid="column"]:has(.delete-restore-btn-hook) div[data-testid="stButton"] > button,
        div[data-testid="element-container"]:has(.delete-restore-btn-hook) + div[data-testid="stButton"] > button,
        div[data-testid="element-container"]:has(.delete-restore-btn-hook) + div[data-testid="element-container"] div[data-testid="stButton"] > button,
        div[data-testid="element-container"]:has(.blue-restore-btn-hook) + div[data-testid="stButton"] > button,
        div[data-testid="element-container"]:has(.blue-restore-btn-hook) + div[data-testid="element-container"] div[data-testid="stButton"] > button,
        div[class*="st-key-delete_mode_restore_btn_wrap"] button,
        div[class*="st-key-restore_deleted_btn_wrap_"] button,
        div[class*="st-key-backup_restore_btn_wrap_"] button,
        div[class*="st-key-delete_mode_restore_btn"] button,
        div[class*="st-key-restore_deleted_"] button,
        div[class*="st-key-backup_restore_"] button {
            min-height: 58px !important;
            height: 58px !important;
            padding: 0.25rem 0.35rem !important;
            background-color: #CC785C !important;
            border-color: #CC785C !important;
            color: #ffffff !important;
            border-radius: 8px !important;
            font-weight: 750 !important;
            line-height: 1.15 !important;
            white-space: normal !important;
        }
        div[data-testid="column"]:has(.delete-restore-btn-hook) div[data-testid="stButton"] > button:hover,
        div[data-testid="element-container"]:has(.delete-restore-btn-hook) + div[data-testid="stButton"] > button:hover,
        div[data-testid="element-container"]:has(.delete-restore-btn-hook) + div[data-testid="element-container"] div[data-testid="stButton"] > button:hover,
        div[data-testid="element-container"]:has(.blue-restore-btn-hook) + div[data-testid="stButton"] > button:hover,
        div[data-testid="element-container"]:has(.blue-restore-btn-hook) + div[data-testid="element-container"] div[data-testid="stButton"] > button:hover,
        div[class*="st-key-delete_mode_restore_btn_wrap"] button:hover,
        div[class*="st-key-restore_deleted_btn_wrap_"] button:hover,
        div[class*="st-key-backup_restore_btn_wrap_"] button:hover,
        div[class*="st-key-delete_mode_restore_btn"] button:hover,
        div[class*="st-key-restore_deleted_"] button:hover,
        div[class*="st-key-backup_restore_"] button:hover {
            background-color: #B56345 !important;
            border-color: #B56345 !important;
            color: #ffffff !important;
        }
        div[data-testid="column"]:has(.delete-restore-btn-hook) div[data-testid="stButton"] > button p,
        div[data-testid="element-container"]:has(.delete-restore-btn-hook) + div[data-testid="stButton"] > button p,
        div[data-testid="element-container"]:has(.delete-restore-btn-hook) + div[data-testid="element-container"] div[data-testid="stButton"] > button p,
        div[class*="st-key-delete_mode_restore_btn_wrap"] button p,
        div[class*="st-key-restore_deleted_btn_wrap_"] button p,
        div[class*="st-key-backup_restore_btn_wrap_"] button p {
            white-space: pre-line !important;
            line-height: 1.15 !important;
        }
        div[data-testid="column"]:has(.backup-manage-btn-hook) div[data-testid="stButton"] > button,
        div[data-testid="element-container"]:has(.backup-manage-btn-hook) + div[data-testid="stButton"] > button,
        div[data-testid="element-container"]:has(.backup-manage-btn-hook) + div[data-testid="element-container"] div[data-testid="stButton"] > button,
        div[class*="st-key-delete_mode_backup_manager_btn_wrap"] button,
        div[class*="st-key-backup_manager_clear_all_wrap"] button,
        div[class*="st-key-delete_mode_backup_manager_btn"] button,
        div[class*="st-key-backup_manager_clear_all"] button {
            min-height: 58px !important;
            height: 58px !important;
            padding: 0.25rem 0.35rem !important;
            background-color: #E8C79A !important;
            border-color: #C89B62 !important;
            color: #1f2328 !important;
            border-radius: 8px !important;
            font-weight: 800 !important;
            line-height: 1.15 !important;
            white-space: normal !important;
        }
        div[data-testid="column"]:has(.backup-manage-btn-hook) div[data-testid="stButton"] > button:hover,
        div[data-testid="element-container"]:has(.backup-manage-btn-hook) + div[data-testid="stButton"] > button:hover,
        div[data-testid="element-container"]:has(.backup-manage-btn-hook) + div[data-testid="element-container"] div[data-testid="stButton"] > button:hover,
        div[class*="st-key-delete_mode_backup_manager_btn_wrap"] button:hover,
        div[class*="st-key-backup_manager_clear_all_wrap"] button:hover,
        div[class*="st-key-delete_mode_backup_manager_btn"] button:hover,
        div[class*="st-key-backup_manager_clear_all"] button:hover {
            background-color: #DDB98A !important;
            border-color: #B98A50 !important;
            color: #1f2328 !important;
        }
        div[data-testid="column"]:has(.backup-manage-btn-hook) div[data-testid="stButton"] > button p,
        div[data-testid="element-container"]:has(.backup-manage-btn-hook) + div[data-testid="stButton"] > button p,
        div[data-testid="element-container"]:has(.backup-manage-btn-hook) + div[data-testid="element-container"] div[data-testid="stButton"] > button p,
        div[class*="st-key-delete_mode_backup_manager_btn_wrap"] button p,
        div[class*="st-key-backup_manager_clear_all_wrap"] button p {
            white-space: pre-line !important;
            line-height: 1.15 !important;
        }
        </style>
        """, unsafe_allow_html=True)
        c_header, c_actions = st.columns([1, 1.4])
        with c_header:
            st.header("🗑️ 删除题库问题")
            st.subheader("删除模式")
            if "browse_mode" not in st.session_state:
                st.session_state["browse_mode"] = "按知识板块浏览"
            browse_mode = st.radio("浏览模式", ["按知识板块浏览", "按试卷浏览", "按录入顺序浏览"], horizontal=True, label_visibility="collapsed", key="browse_mode")

        with c_actions:
            st.write("")
            st.write("")
            exit_btn_col, restore_btn_col, backup_btn_col = st.columns([1, 1, 1])
            with exit_btn_col:
                st.markdown('<span class="delete-exit-btn-hook"></span>', unsafe_allow_html=True)
                if st.button("退出\n删除模式", key="delete_mode_exit_btn", type="secondary", use_container_width=True):
                    st.session_state["tools_subpage"] = None
                    st.session_state["adv_search_active"] = False
                    _clear_advanced_search_result_cache()
                    st.rerun()
            with restore_btn_col:
                st.markdown('<span class="delete-restore-btn-hook"></span>', unsafe_allow_html=True)
                if st.button("恢复\n误删题目", key="delete_mode_restore_btn", type="primary", use_container_width=True):
                    restore_deleted_questions_dialog()
            with backup_btn_col:
                st.markdown('<span class="backup-manage-btn-hook"></span>', unsafe_allow_html=True)
                if st.button("管理\n备份问题", key="delete_mode_backup_manager_btn", type="secondary", use_container_width=True):
                    manage_backup_questions_dialog()

        search_workspace_active = st.session_state.get("adv_search_active") and _adv_search_has_query()
        if not search_workspace_active:
            render_advanced_search_inline()

    elif not is_exam_mode:
        if "browse_mode" not in st.session_state:
            st.session_state["browse_mode"] = "按知识板块浏览"
        # 按录入顺序浏览：把「浏览设置」（排序 / 每页数量 / 翻页）收进标题行右侧
        if st.session_state.get("browse_mode") == "按录入顺序浏览":
            _time_browse_ctx = render_time_browse_header(page_title, paper_type_scope)
        else:
            st.header(page_title)
        # 浏览模式与检索模式同一行展示，省掉一行标题与一段纵向留白
        c_browse_mode, c_search_mode = st.columns([1.18, 1], gap="medium")
        with c_browse_mode:
            browse_mode = st.radio(
                "浏览模式",
                ["按知识板块浏览", "按试卷浏览", "按录入顺序浏览"],
                horizontal=True,
                label_visibility="collapsed",
                key="browse_mode",
            )

        search_workspace_active = st.session_state.get("adv_search_active") and _adv_search_has_query()
        if not search_workspace_active:
            render_advanced_search_inline(mode_container=c_search_mode)
            
    else:
        if "browse_mode" not in st.session_state:
            st.session_state["browse_mode"] = "按知识板块浏览"
        browse_mode = st.radio("浏览模式", ["按知识板块浏览", "按试卷浏览", "按录入顺序浏览"], horizontal=True, label_visibility="collapsed", key="browse_mode")
    
    # 根据红线截图，我们在这里画一条醒目的红线
    st.markdown('<hr class="mc-browse-divider" style="border-top: 1px solid #e1e4e8;">', unsafe_allow_html=True)
    if is_delete_mode:
        st.caption("删除会移除题目文件、CSV 索引记录和对应章节索引；原题目文件会自动备份到 .backups。若误删，可点右上角“恢复误删题目”恢复本次删除记录，或点“管理备份问题”查找历史备份；当前备份不会自动定期清理。")
    
    if not is_exam_mode and not is_delete_mode and st.session_state.get("recent_saved_active") and st.session_state.get("recent_saved_paths"):
        paths = [p for p in st.session_state.get("recent_saved_paths", []) if p and os.path.exists(p)]
        c1, c2 = st.columns([1, 1])
        with c1:
            st.subheader("🧾 本次录入的题目")
        with c2:
            def _clear_recent_saved():
                st.session_state["recent_saved_active"] = False
                st.session_state["recent_saved_paths"] = []
            st.button("返回正常浏览", type="secondary", use_container_width=True, on_click=_clear_recent_saved, key="recent_saved_back")
        
        if not paths:
            st.info("未找到可展示的文件（可能已移动/删除）。")
            return
        
        for fpath in paths:
            try:
                prepared_assets = load_question_editor_assets(fpath)
                content = prepared_assets["content"]
            except Exception as e:
                st.error(f"读取失败: {e}")
                continue
            
            fname = os.path.basename(fpath)
            q_label = format_question_title(fname)
            render_browse_question_editor_card(
                q_label,
                content,
                fpath,
                "recent_saved",
                paper_type_scope=paper_type_scope,
                rename_paths_key="recent_saved_paths",
                prepared_assets=prepared_assets,
            )
            st.divider()
        return
    
    # === 如果激活了搜索，优先显示搜索结果 ===
    if not is_exam_mode and st.session_state.get("adv_search_active"):
        if _adv_search_has_query():
            render_advanced_search_workspace(is_delete_mode=is_delete_mode, paper_type_scope=paper_type_scope)
            return  # 搜索状态下，不显示下方的常规浏览内容
        st.session_state["adv_search_active"] = False
    
    selected_file_path = None
    
    if browse_mode == "按知识板块浏览":
        # 顶部吸顶导航（原左栏内容整体上移）：知识板块 / 年份 / 文件列表
        # 用 nullcontext 让导航直接挂在页面块下（若包一层容器，sticky 会被父盒高度限制而失效）
        col_nav = st.container()
        col_content = st.container()
        st.markdown("""
            <style>
            /* 组卷页：标题/模式/模板条吸顶（压在导航条之上） */
            body:has(#mc-exam-top-anchor) div[data-testid="stVerticalBlockBorderWrapper"]:has(#mc-exam-top-anchor):not(:has(div[data-testid="stVerticalBlockBorderWrapper"] #mc-exam-top-anchor)) {
                position: sticky !important;
                top: 0.25rem !important;
                z-index: 46 !important;
                background: var(--mc-bg-base) !important;
                padding: 0.2rem 0.45rem 0.25rem !important;
                border-radius: 14px !important;
                border: 1px solid rgba(214, 202, 185, 0.72) !important;
                box-shadow: 0 14px 30px -24px rgba(120, 95, 60, 0.55) !important;
                margin-bottom: 0.25rem !important;
            }
            body:has(#mc-exam-top-anchor) div[data-testid="stVerticalBlockBorderWrapper"]:has(#mc-exam-top-anchor):not(:has(div[data-testid="stVerticalBlockBorderWrapper"] #mc-exam-top-anchor)) h1,
            body:has(#mc-exam-top-anchor) div[data-testid="stVerticalBlockBorderWrapper"]:has(#mc-exam-top-anchor):not(:has(div[data-testid="stVerticalBlockBorderWrapper"] #mc-exam-top-anchor)) h2 {
                font-size: 1.28rem !important;
                margin: 0 !important;
                line-height: 1.15 !important;
            }
            body:has(#mc-exam-top-anchor) div[data-testid="stVerticalBlockBorderWrapper"]:has(#mc-exam-top-anchor):not(:has(div[data-testid="stVerticalBlockBorderWrapper"] #mc-exam-top-anchor)) div[data-testid="stRadio"] label p {
                font-size: 12.6px !important;
            }
            /* 组卷页：下方导航条叠放在标题条下面 */
            body:has(#mc-exam-top-anchor) div[data-testid="stVerticalBlockBorderWrapper"]:has(#left-panel-anchor):not(:has(div[data-testid="stVerticalBlockBorderWrapper"] #left-panel-anchor)) {
                top: 104px !important;
            }

            /* 解除中间层的 overflow 裁剪，position: sticky 才能相对页面滚动生效 */
            div[data-testid="stMain"],
            div[data-testid="stMainBlockContainer"],
            div[data-testid="stAppViewBlockContainer"] {
                overflow: visible !important;
            }
            /* 顶部导航条：滚动时钉在视口顶部，只有题目源码与预览移动 */
            div[data-testid="stVerticalBlockBorderWrapper"]:has(#left-panel-anchor):not(:has(div[data-testid="stVerticalBlockBorderWrapper"] #left-panel-anchor)) {
                position: sticky !important;
                top: 0.3rem !important;
                z-index: 40 !important;
                background: var(--mc-bg-base) !important;
                padding: 0.4rem 0.55rem 0.5rem !important;
                border-radius: 14px !important;
                border: 1px solid rgba(214, 202, 185, 0.72) !important;
                box-shadow: 0 14px 30px -24px rgba(120, 95, 60, 0.55) !important;
                margin-bottom: 0.45rem !important;
            }
            /* 顶部条内的小标题 */
            .mc-browse-mini-label {
                font-size: 10.6px;
                font-weight: 700;
                color: #8A7F70;
                letter-spacing: 0.02em;
                margin: 0 0 0 2px;
                line-height: 1.15;
            }
            /* 顶部条内控件统一压矮 */
            div[data-testid="stVerticalBlockBorderWrapper"]:has(#left-panel-anchor):not(:has(div[data-testid="stVerticalBlockBorderWrapper"] #left-panel-anchor)) div[data-testid="stVerticalBlock"] {
                gap: 0.18rem !important;
            }
            div[data-testid="stVerticalBlockBorderWrapper"]:has(#left-panel-anchor):not(:has(div[data-testid="stVerticalBlockBorderWrapper"] #left-panel-anchor)) div[data-testid="stRadio"] label {
                min-height: 22px !important;
                padding: 0 !important;
            }
            div[data-testid="stVerticalBlockBorderWrapper"]:has(#left-panel-anchor):not(:has(div[data-testid="stVerticalBlockBorderWrapper"] #left-panel-anchor)) div[data-testid="stRadio"] label p {
                font-size: 12px !important;
            }
            div[data-testid="stVerticalBlockBorderWrapper"]:has(#left-panel-anchor):not(:has(div[data-testid="stVerticalBlockBorderWrapper"] #left-panel-anchor)) div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
                min-height: 32px !important;
            }
            body:has(#mc-exam-top-anchor) div[data-testid="stVerticalBlockBorderWrapper"]:has(#mc-exam-top-anchor):not(:has(div[data-testid="stVerticalBlockBorderWrapper"] #mc-exam-top-anchor)) div[data-testid="stVerticalBlock"] {
                gap: 0.2rem !important;
            }
            body:has(#mc-exam-top-anchor) div[data-testid="stVerticalBlockBorderWrapper"]:has(#mc-exam-top-anchor):not(:has(div[data-testid="stVerticalBlockBorderWrapper"] #mc-exam-top-anchor)) h1,
            body:has(#mc-exam-top-anchor) div[data-testid="stVerticalBlockBorderWrapper"]:has(#mc-exam-top-anchor):not(:has(div[data-testid="stVerticalBlockBorderWrapper"] #mc-exam-top-anchor)) h2 {
                font-size: 1.06rem !important;
                line-height: 1.1 !important;
            }
            /* 知识板块：单行横向滚动 */
            div[data-testid="stHorizontalBlock"]:has(#mc-subject-chip-start) {
                flex-wrap: nowrap !important;
                overflow-x: auto !important;
                overflow-y: hidden !important;
                padding-bottom: 3px !important;
                gap: 6px !important;
                scrollbar-width: thin !important;
            }
            div[data-testid="stHorizontalBlock"]:has(#mc-subject-chip-start) > div[data-testid="column"] {
                flex: 0 0 114px !important;
                min-width: 114px !important;
                max-width: 114px !important;
                width: 114px !important;
            }
            div[data-testid="stHorizontalBlock"]:has(#mc-subject-chip-start) div[data-testid="stButton"] > button {
                min-height: 28px !important;
                height: 28px !important;
                font-size: 12.2px !important;
                border-radius: 8px !important;
                padding: 0 4px !important;
                white-space: nowrap !important;
                overflow: hidden !important;
                text-overflow: ellipsis !important;
            }
            /* 其它导航按钮更矮 */
            div[data-testid="stVerticalBlockBorderWrapper"]:has(#left-panel-anchor):not(:has(div[data-testid="stVerticalBlockBorderWrapper"] #left-panel-anchor)) div[data-testid="stButton"] > button {
                min-height: 30px !important;
                height: 30px !important;
                font-size: 12.6px !important;
                border-radius: 9px !important;
                padding: 0 6px !important;
            }
            /* 隐藏旧的左栏样式锚点占位 */
            div[data-testid="element-container"]:has(#knowledge-subject-area) {
                display: none !important;
            }
            </style>
        """, unsafe_allow_html=True)

        with col_nav:
            st.markdown('<div id="knowledge-subject-area"></div>', unsafe_allow_html=True)
            st.markdown('<div id="left-panel-anchor"></div>', unsafe_allow_html=True)
            st.markdown('<span id="mc-subject-chips-row-anchor"></span>', unsafe_allow_html=True)
            
            # 使用自定义 CSS 优化按钮样式 (圆角、紧凑) 以及固定左栏
            st.markdown("""
                <style>
                /* 隐藏左侧栏的滚动条以便美观 */
                div[data-testid="column"]:has(#left-panel-anchor)::-webkit-scrollbar {
                    width: 4px;
                }
                div[data-testid="column"]:has(#left-panel-anchor)::-webkit-scrollbar-thumb {
                    background-color: rgba(0,0,0,0.1);
                    border-radius: 4px;
                }
                
                /* 精准定位知识板块区域的按钮 */
                div[data-testid="column"]:has(#knowledge-subject-area) div[data-testid="stButton"] {
                    display: flex !important;
                    justify-content: center !important;
                    width: 100% !important;
                }
                div[data-testid="column"]:has(#knowledge-subject-area) div[data-testid="stButton"] button {
                    width: 85% !important;  
                    min-width: 85% !important;
                    max-width: 85% !important;
                    border-radius: 6px !important;
                    padding: 0.1rem 0.15rem !important;
                    min-height: 32px !important;
                    height: 32px !important;
                    margin-bottom: 2px !important;
                }
                div[data-testid="column"]:has(#knowledge-subject-area) div[data-testid="stButton"] button p {
                    font-size: 13px !important;
                    line-height: 1.2 !important;
                }
                </style>
            """, unsafe_allow_html=True)
            
            # 使用 session_state 记录当前选中的板块
            if "browse_subject" not in st.session_state:
                st.session_state["browse_subject"] = SUBJECTS[0]

            # 顶部横排：单行横向滚动，22 个板块不再占两行
            chip_cols = st.columns(len(SUBJECTS), gap="small")
            for idx, subj in enumerate(SUBJECTS):
                with chip_cols[idx]:
                    if idx == 0:
                        st.markdown('<span id="mc-subject-chip-start"></span>', unsafe_allow_html=True)
                    btn_type = "primary" if st.session_state["browse_subject"] == subj else "secondary"
                    if st.button(subj, key=f"nav_subj_{subj}", type=btn_type, use_container_width=True):
                        st.session_state["browse_subject"] = subj
                        for state_key in list(st.session_state.keys()):
                            if isinstance(state_key, str) and state_key.startswith("browse_question_page_"):
                                st.session_state[state_key] = 1
                        st.rerun()
            
            subject = st.session_state["browse_subject"]

            years = get_years(subject, paper_type=paper_type_scope)
            files = []
            year = None
            selected_option = None
            SHOW_ALL_OPT = None
            ALL_YEARS_OPT = "显示所有年份"
            _c_year, _c_file = st.columns([1, 1.45], gap="medium")
            if years:
                with _c_year:
                    st.markdown('<div class="mc-browse-mini-label">📅 选择年份</div>', unsafe_allow_html=True)
                    year_options = [ALL_YEARS_OPT] + years
                    default_year_index = 0
                    year = st.radio(
                        "📅 选择年份",
                        options=year_options,
                        index=default_year_index,
                        key=f"browse_year_{subject}",
                        horizontal=True,
                        label_visibility="collapsed",
                    )
                
                selected_option = None
                SHOW_ALL_OPT = None
                
                if year == ALL_YEARS_OPT:
                    # 获取该板块下所有年份的所有文件
                    files = []
                    for y in years:
                        y_files = get_files(subject, y, paper_type=paper_type_scope)
                        if y_files:
                            # 为了区分不同年份，我们在文件名列表中带上年份信息
                            files.extend([(y, f) for f in y_files])
                            
                    if files:
                        with _c_file:
                            SHOW_ALL_OPT = "📂 展示该板块全部问题"
                            display_options = [f"{y}年 - {f}" for y, f in files]
                            file_options = [SHOW_ALL_OPT] + display_options
                            selected_option = st.selectbox(
                                "3. 选择文件 (支持输入搜索)",
                                options=file_options,
                                index=0,
                                key=f"browse_file_select_{subject}_all",
                                label_visibility="collapsed",
                            )
                else:
                    # 原来的单一年份逻辑
                    files = get_files(subject, year, paper_type=paper_type_scope)
                    if files:
                        with _c_file:
                            SHOW_ALL_OPT = "📂 展示该年份全部问题"
                            file_options = [SHOW_ALL_OPT] + files
                            selected_option = st.selectbox(
                                "3. 选择文件 (支持输入搜索)",
                                options=file_options,
                                index=0,
                                key=f"browse_file_select_{subject}_{year}",
                                label_visibility="collapsed",
                            )
            
            page_start = 0
            page_end = 0
            if files and selected_option == SHOW_ALL_OPT:
                page_size = 20
                page_scope = "all_years" if year == ALL_YEARS_OPT else year
                page_key = f"browse_question_page_{subject}_{page_scope}_{paper_type_scope or 'regular'}"
                total_pages = max(1, (len(files) + page_size - 1) // page_size)
                current_page = int(st.session_state.get(page_key, 1) or 1)
                current_page = max(1, min(total_pages, current_page))
                if st.session_state.get(page_key) != current_page:
                    st.session_state[page_key] = current_page
                if total_pages > 1:
                    _c_page, _c_page_info = st.columns([1.1, 3], gap="small")
                    with _c_page:
                        current_page = st.number_input(
                            f"页码（共 {total_pages} 页）",
                            min_value=1,
                            max_value=total_pages,
                            step=1,
                            key=page_key,
                        )
                    page_start = (current_page - 1) * page_size
                    page_end = min(len(files), page_start + page_size)
                    with _c_page_info:
                        st.caption(f"当前显示第 {page_start + 1}-{page_end} 题，每页最多 20 题")
                else:
                    page_start, page_end = 0, min(len(files), page_size)
        with col_content:
            st.markdown('<div id="right-panel-anchor"></div>', unsafe_allow_html=True)
            if years:
                if year == ALL_YEARS_OPT:
                    if files:
                        if selected_option == SHOW_ALL_OPT:
                            st.markdown(f"### {subject} - 所有年份所有题目")
                            
                            for i, (y, fname) in enumerate(files[page_start:page_end], start=page_start):
                                fpath = os.path.join(CHAPTERS_DIR, subject, y, fname)
                                if not os.path.exists(fpath):
                                    continue
                                prepared_assets = None
                                if not is_delete_mode and not is_exam_mode:
                                    prepared_assets = load_question_editor_assets(fpath)
                                    content = prepared_assets["content"]
                                else:
                                    content = read_question_text(fpath)
                                q_label = format_question_title(fname)
                                if is_delete_mode:
                                    render_delete_question_item(fpath, q_label, content, key_prefix="delete_subject_all_years")
                                    st.divider()
                                    continue
                                if is_exam_mode:
                                    render_exam_question_card(q_label, content, fpath, f"exam_action_subject_all_{fpath}")
                                    continue
                                render_browse_question_editor_card(
                                    q_label,
                                    content,
                                    fpath,
                                    "subj_all",
                                    paper_type_scope=paper_type_scope,
                                    prepared_assets=prepared_assets,
                                )
                                st.divider()
                        elif selected_option:
                            # 解析出真实的年份和文件名
                            sel_y = selected_option.split("年 - ")[0]
                            sel_f = selected_option.split("年 - ")[1]
                            selected_file_path = os.path.join(CHAPTERS_DIR, subject, sel_y, sel_f)
                    else:
                        st.info("该板块下暂无任何文件")
                else:
                    if files:
                        if selected_option == SHOW_ALL_OPT:
                            # 不显示底部的单文件编辑器
                            st.markdown(f"### {year}年 {subject} - 所有题目")
                            
                            for i, fname in enumerate(files[page_start:page_end], start=page_start):
                                fpath = os.path.join(CHAPTERS_DIR, subject, year, fname)
                                if not os.path.exists(fpath): continue
                                
                                # 读取内容
                                prepared_assets = None
                                if not is_delete_mode and not is_exam_mode:
                                    prepared_assets = load_question_editor_assets(fpath)
                                    content = prepared_assets["content"]
                                else:
                                    content = read_question_text(fpath)
                                
                                # 提取显示标签
                                q_label = format_question_title(fname)

                                if is_delete_mode:
                                    render_delete_question_item(fpath, q_label, content, key_prefix="delete_subject_year")
                                    st.divider()
                                    continue

                                if is_exam_mode:
                                    render_exam_question_card(q_label, content, fpath, f"exam_action_subject_year_{fpath}")
                                    continue

                                render_browse_question_editor_card(
                                    q_label,
                                    content,
                                    fpath,
                                    "subj_year",
                                    paper_type_scope=paper_type_scope,
                                    prepared_assets=prepared_assets,
                                )
                                st.divider()

                        elif selected_option and selected_option != SHOW_ALL_OPT:
                             selected_file_path = os.path.join(CHAPTERS_DIR, subject, year, selected_option)
                    else:
                        st.info("该目录下暂无文件")
            else:
                st.warning("该板块暂无年份数据")
                
    elif browse_mode == "按试卷浏览":
        # 采用一致的双栏独立滑动布局
        col_nav, col_content = st.columns([0.8, 3])
        
        with col_nav:
            st.markdown('<div id="paper-left-anchor"></div>', unsafe_allow_html=True)
            
            all_years = get_all_years_globally(paper_type=paper_type_scope)
            type_opts = ["WK"] if is_cloze_library else [key for key in PAPER_TYPES.keys() if key != "WK"]
            def _fmt_paper_type(x):
                if x == "全部类型":
                    return "全部类型"
                return PAPER_TYPES.get(x, x)
            def _on_paper_type_change():
                st.session_state.pop("paper_year", None)
                st.session_state.pop("paper_name", None)
            st.subheader("🗂️ 类型选择")
            paper_type = st.selectbox("题目类型", options=["全部类型"] + type_opts, format_func=_fmt_paper_type, key="paper_type", label_visibility="collapsed", on_change=_on_paper_type_change)
            
            if paper_type != "全部类型":
                years_for_type = get_all_years_by_paper_type(paper_type)
            else:
                years_for_type = all_years
            
            if not years_for_type:
                st.warning("题库中暂无任何年份数据")
            else:
                st.subheader("📅 选择年份")
                def _on_paper_year_change():
                    st.session_state.pop("paper_name", None)
                year = st.radio("📅 选择年份", options=years_for_type, key="paper_year", horizontal=True, label_visibility="collapsed", on_change=_on_paper_year_change)
                
                st.write("")
                st.subheader("📂 试卷选择")
                if paper_type != "全部类型":
                    papers = get_papers_by_year_and_type(year, paper_type)
                else:
                    papers = get_papers_by_year(year, paper_type=paper_type_scope)
                if papers:
                    paper_name = st.selectbox("选择试卷", options=papers, key="paper_name", label_visibility="collapsed")
                else:
                    paper_name = None
                    st.info("该年份下未找到试卷")
                    
                st.write("")
                st.subheader("👀 展示模式")
                view_mode = st.radio("展示模式", ["单题选择模式", "所有问题展示模式"], horizontal=False, label_visibility="collapsed")
                
                if all_years and year and paper_name:
                    if paper_type != "全部类型":
                        questions = get_questions_by_paper_and_type(year, paper_name, paper_type)
                    else:
                        questions = get_questions_by_paper(year, paper_name, paper_type=paper_type_scope)
                    if questions and view_mode == "单题选择模式":
                        st.write("")
                        st.subheader("选择题目进行删除" if is_delete_mode else "选择题目进行编辑")
                        
                        # 使用 session_state 记录当前选中的题目索引
                        select_key = f"selected_q_idx_{year}_{paper_name}"
                        if select_key not in st.session_state:
                            st.session_state[select_key] = 0
                        if st.session_state[select_key] >= len(questions):
                            st.session_state[select_key] = max(0, len(questions) - 1)
                        
                        # 按钮网格布局 (每行 3 个，适配左栏宽度)
                        num_cols = 3
                        rows = (len(questions) + num_cols - 1) // num_cols
                        
                        for r in range(rows):
                            cols = st.columns(num_cols)
                            for c in range(num_cols):
                                idx = r * num_cols + c
                                if idx < len(questions):
                                    q = questions[idx]
                                    q_num = q['file'].split('-')[3]
                                    btn_label = f"第{q_num}题"
                                    
                                    # 高亮当前选中的按钮
                                    is_selected = (idx == st.session_state[select_key])
                                    btn_type = "primary" if is_selected else "secondary"
                                    
                                    if cols[c].button(btn_label, key=f"q_btn_{year}_{paper_name}_{idx}", type=btn_type):
                                        st.session_state[select_key] = idx
                                        st.rerun()

                        selected_q_idx = st.session_state[select_key]
                        if selected_q_idx < len(questions):
                            selected_question = questions[selected_q_idx]
                            selected_file_path = selected_question["path"]
                        else:
                            selected_file_path = None
                    else:
                        selected_file_path = None
                        
        with col_content:
            st.markdown('<div id="paper-right-anchor"></div>', unsafe_allow_html=True)
            if all_years and year and paper_name:
                if not questions:
                     st.info("未找到该试卷的题目")
                else:
                    if view_mode == "单题选择模式":
                        # 单题模式下，右侧不展示列表，由外部逻辑(Split View)在最下方展示单题编辑
                        pass
                    else:
                        # 所有问题展示模式：逐题列出，左编辑右预览
                        for i, q in enumerate(questions):
                            q_path = q["path"]
                            if not os.path.exists(q_path): continue
                            
                            # 读取内容
                            prepared_assets = None
                            if not is_delete_mode and not is_exam_mode:
                                prepared_assets = load_question_editor_assets(q_path)
                                content = prepared_assets["content"]
                            else:
                                content = read_question_text(q_path)
                                
                            # 题目编号
                            q_label = format_question_title(q['file'])

                            if is_delete_mode:
                                render_delete_question_item(q_path, q_label, content, key_prefix="delete_paper_all")
                                st.divider()
                                continue

                            if is_exam_mode:
                                render_exam_question_card(q_label, content, q_path, f"exam_action_paper_{q_path}")
                                continue

                            render_browse_question_editor_card(
                                q_label,
                                content,
                                q_path,
                                "paper_all",
                                paper_type_scope=paper_type_scope,
                                prepared_assets=prepared_assets,
                            )
                            st.divider()
    
    elif browse_mode == "按录入顺序浏览":
        if _time_browse_ctx is not None:
            # 浏览设置已上移到页面标题行：左列塌缩为 0，内容区铺满整行
            # （保留 #time-left-anchor 让既有的分栏/卡片布局 CSS 继续生效）
            col_nav, col_content = st.columns([0.0001, 1])
            with col_nav:
                st.markdown('<div id="time-left-anchor"></div>', unsafe_allow_html=True)
            with col_content:
                st.markdown('<div id="time-right-anchor"></div>', unsafe_allow_html=True)
                render_time_browse_list(_time_browse_ctx, is_exam_mode, is_delete_mode, paper_type_scope)
        else:
            # 删除 / 组卷等模式：保留原双栏布局
            col_nav, col_content = st.columns([1, 2.5])

            with col_nav:
                st.markdown('<div id="time-left-anchor"></div>', unsafe_allow_html=True)
                st.markdown("### 🕒 浏览设置")

                # 排序选项
                st.subheader("排序方式")
                sort_order = st.radio("排序方式", ["最新录入在最前", "最早录入在最前"], horizontal=False, label_visibility="collapsed")

                st.divider()

                fallback_ctx = None
                try:
                    from utils.csv_ops import read_csv_index
                    csv_data = _filter_question_rows(read_csv_index(), paper_type_scope)
                except Exception as e:
                    csv_data = []
                    st.error(f"读取索引失败: {e}")

                if csv_data:
                    st.subheader("显示数量限制")
                    max_show = st.slider("最多展示题目数量", min_value=5, max_value=20, value=10, step=1, label_visibility="visible", key="time_max_show")
                    if st.session_state.get("time_max_show_prev") != max_show:
                        st.session_state["time_browse_page"] = 1
                        st.session_state["time_max_show_prev"] = max_show

                    sorted_data = sorted(csv_data, key=_time_browse_key, reverse=(sort_order == "最新录入在最前"))
                    total_count = len(sorted_data)
                    total_pages = max(1, (total_count + max_show - 1) // max_show)
                    current_page = int(st.session_state.get("time_browse_page", 1) or 1)
                    current_page = max(1, min(total_pages, current_page))
                    st.session_state["time_browse_page"] = current_page

                    st.divider()
                    st.markdown(f"第 {current_page} 页")
                    p1, p2 = st.columns(2)
                    with p1:
                        if st.button("⬅️ 上一页", key="time_browse_prev", disabled=(current_page <= 1), use_container_width=True):
                            st.session_state["time_browse_page"] = current_page - 1
                            st.rerun()
                    with p2:
                        if st.button("下一页 ➡️", key="time_browse_next", disabled=(current_page >= total_pages), use_container_width=True):
                            st.session_state["time_browse_page"] = current_page + 1
                            st.rerun()
                    fallback_ctx = {
                        "csv_data": csv_data,
                        "sorted_data": sorted_data,
                        "max_show": max_show,
                        "total_count": total_count,
                        "total_pages": total_pages,
                        "current_page": current_page,
                    }

            with col_content:
                st.markdown('<div id="time-right-anchor"></div>', unsafe_allow_html=True)
                if fallback_ctx is None:
                    st.info("题库为空或索引未建立，请先一键重建题库索引。")
                else:
                    render_time_browse_list(fallback_ctx, is_exam_mode, is_delete_mode, paper_type_scope)

    # 编辑区域 (Split View) - 仅在选择了文件时显示，并严格限制在右栏内容区内
    if selected_file_path and os.path.exists(selected_file_path):
        
        # 为了让单题模式下也能正确渲染在右侧容器中，需要判断我们现在是否还有 col_content 的上下文。
        # 如果我们在主循环外，我们需要重新打开右侧的 column 容器。
        try:
            target_container = col_content
        except NameError:
            target_container = st.container()

        with target_container:
            prepared_assets = None
            if not is_exam_mode and not is_delete_mode:
                prepared_assets = load_question_editor_assets(selected_file_path)
                current_content = prepared_assets["content"]
            else:
                current_content = read_question_text(selected_file_path)
                
            # 组卷模式使用聚焦卡片，普通浏览继续使用完整编辑头部。
            if "browse_mode" in locals():
                q_fname = os.path.basename(selected_file_path)
                q_label = format_question_title(q_fname)
                
            if is_exam_mode:
                render_exam_question_card(q_label, current_content, selected_file_path, f"exam_action_selected_{selected_file_path}")
            elif is_delete_mode:
                render_static_question_header(q_label, current_content, selected_file_path)
                render_delete_question_item(selected_file_path, q_label, current_content, key_prefix="delete_selected", show_header=False)
            else:
                selected_key = _question_key("selected", selected_file_path)
                render_browse_question_editor_card(
                    q_label,
                    current_content,
                    selected_file_path,
                    f"selected_{selected_key}",
                    paper_type_scope=paper_type_scope,
                    prepared_assets=prepared_assets,
                )
                        
            if not is_exam_mode and not is_delete_mode:
                with st.expander("查看文件路径"):
                    st.code(selected_file_path)

def page_exam_paper_generation():
    st.markdown('<span id="mc-exam-page-anchor"></span>', unsafe_allow_html=True)
    # 顶部固定区：标题 + 模块切换 + 模板/数量/AI（整体吸顶）
    _exam_top = st.container()
    with _exam_top:
        st.markdown('<span id="mc-exam-top-anchor"></span>', unsafe_allow_html=True)
        _c_exam_title, _c_exam_mode = st.columns([1.1, 3.0], gap="small")
        with _c_exam_title:
            st.header("🖨️ 组卷服务")
    
    # 注入全局按钮样式 CSS Hook
    st.markdown("""
    <style>
    /* 让按钮靠得更近，高度一致 */
    .stButton > button {
        height: 100% !important;
        min-height: 40px !important;
    }
    
    /* 使用更兼容的选择器，确保选中按钮外层的 div */
    div:has(> div > .blue-btn-hook) + div button[kind="secondary"],
    div[data-testid="column"]:has(.blue-btn-hook) button[kind="secondary"] {
        background-color: var(--mc-accent-soft) !important;
        border-color: var(--mc-accent) !important;
        color: var(--mc-accent-dark) !important;
        font-weight: bold !important;
        box-shadow: inset 0 1px 2px rgba(0,0,0,0.05) !important;
    }
    
    div:has(> div > .white-btn-hook) + div button[kind="secondary"],
    div[data-testid="column"]:has(.white-btn-hook) button[kind="secondary"] {
        background-color: var(--mc-surface) !important;
        border-color: var(--mc-border) !important;
        color: var(--mc-text) !important;
    }
    
    div[data-testid="element-container"]:has(.red-btn-hook) + div[data-testid="stButton"] > button {
        background-color: #ef4444 !important;
        border-color: #ef4444 !important;
        color: #ffffff !important;
    }
    
    /* 针对已选问题网格布局中的红色 X 按钮 */
    div[data-testid="element-container"]:has(.white-red-text-btn-hook) + div[data-testid="stButton"] > button {
        background-color: var(--mc-surface) !important;
        border-color: var(--mc-border) !important;
        color: #ef4444 !important;
        padding: 0 !important;
        font-weight: bold !important;
    }
    div[data-testid="element-container"]:has(.white-red-text-btn-hook) + div[data-testid="stButton"] > button:hover {
        background-color: rgba(239, 68, 68, 0.12) !important;
        border-color: #ef4444 !important;
    
    /* 取消 Streamlit 按钮点击时的下沉动画效果 */
    .stButton > button:active {
        transform: none !important;
    }
    
    /* 调整“选择组卷服务模块”的单选按钮字号，使其与 h3 (###) 差不多大 */
    div.big-radio-container + div[data-testid="stRadio"] label[data-baseweb="radio"] div {
        font-size: 1.5rem !important;
        font-weight: 600 !important;
        line-height: 1.2 !important;
    }
    </style>
    """, unsafe_allow_html=True)
    
    st.markdown('<div class="big-radio-container"></div>', unsafe_allow_html=True)

    with _c_exam_mode:
        exam_service_mode = st.radio(
            "选择组卷服务模块",
            ["🖨️ 试卷排版工作台", "📂 历史组卷浏览"],
            horizontal=True,
            label_visibility="collapsed",
        )

    if exam_service_mode == "📂 历史组卷浏览":
        # ================= 新增：历史组卷浏览 =================
        st.markdown("""
        <style>
        #mc-history-browser-anchor {
            display: none !important;
        }
        div[data-testid="stVerticalBlock"]:has(#mc-history-browser-anchor) {
            gap: 0.45rem !important;
        }
        div[data-testid="stVerticalBlock"]:has(#mc-history-browser-anchor) div[data-testid="stHorizontalBlock"] {
            gap: 0.65rem !important;
            margin: 0 !important;
        }
        div[data-testid="stVerticalBlock"]:has(#mc-history-browser-anchor) h3,
        div[data-testid="stVerticalBlock"]:has(#mc-history-browser-anchor) h4,
        div[data-testid="stVerticalBlock"]:has(#mc-history-browser-anchor) h5 {
            margin-top: 0.28rem !important;
            margin-bottom: 0.18rem !important;
        }
        div[data-testid="stVerticalBlock"]:has(#mc-history-browser-anchor) hr {
            margin: 0.35rem 0 !important;
        }
        div[data-testid="stVerticalBlock"]:has(#mc-history-browser-anchor) div[data-testid="stRadio"] {
            margin-bottom: 0 !important;
        }
        div[data-testid="stVerticalBlock"]:has(#mc-history-browser-anchor) div[data-testid="stRadio"] div[role="radiogroup"] {
            gap: 0.3rem !important;
        }
        div[data-testid="stVerticalBlock"]:has(#mc-history-browser-anchor) div[data-testid="stRadio"] label {
            padding-top: 0.18rem !important;
            padding-bottom: 0.18rem !important;
        }
        div[data-testid="stVerticalBlock"]:has(#mc-history-browser-anchor) div[data-testid="stSelectbox"] {
            margin-bottom: 0 !important;
        }
        div[data-testid="stVerticalBlock"]:has(#mc-history-browser-anchor) .mc-history-divider {
            height: 1px;
            margin: 0.45rem 0;
            background: var(--mc-border);
        }
        div[data-testid="stVerticalBlock"]:has(#mc-history-browser-anchor) .mc-history-question-number {
            color: var(--mc-accent-dark);
            font-size: 0.86rem;
            font-weight: 700;
            line-height: 1.3;
            margin: 0.35rem 0 0.12rem;
        }
        div[data-testid="stVerticalBlock"]:has(#mc-history-browser-anchor) pre {
            margin-top: 0.2rem !important;
            margin-bottom: 0.2rem !important;
        }
        @media (prefers-reduced-motion: reduce) {
            div[data-testid="stVerticalBlock"]:has(#mc-history-browser-anchor) * {
                scroll-behavior: auto !important;
            }
        }
        </style>
        <span id="mc-history-browser-anchor"></span>
        """, unsafe_allow_html=True)
        export_base_dir = os.path.join(BASE_DIR, "Test Paper Group", "导出文件")
        if not os.path.exists(export_base_dir):
            st.info("暂无组卷记录")
        else:
            years = sorted([d for d in os.listdir(export_base_dir) if os.path.isdir(os.path.join(export_base_dir, d))], reverse=True)
            if not years:
                st.info("暂无组卷记录")
            else:
                c_y1, c_y2 = st.columns([1, 6], vertical_alignment="center")
                with c_y1:
                    st.markdown("##### 📅 选择年份")
                with c_y2:
                    selected_year = st.radio("选择年份", ["显示所有年份"] + years, horizontal=True, label_visibility="collapsed")
                
                months = []
                if selected_year != "显示所有年份":
                    year_dir = os.path.join(export_base_dir, selected_year)
                    if os.path.exists(year_dir):
                        months = sorted([d for d in os.listdir(year_dir) if os.path.isdir(os.path.join(year_dir, d))], reverse=True)
                else:
                    for y in years:
                        y_dir = os.path.join(export_base_dir, y)
                        months.extend([d for d in os.listdir(y_dir) if os.path.isdir(os.path.join(y_dir, d))])
                    months = sorted(list(set(months)), reverse=True)
                
                c_m1, c_m2 = st.columns([1, 6], vertical_alignment="center")
                with c_m1:
                    st.markdown("##### 📅 选择月份")
                with c_m2:
                    if months:
                        selected_month = st.radio("选择月份", ["显示所有月份"] + months, horizontal=True, label_visibility="collapsed")
                    else:
                        st.info("该年份下暂无记录")
                        selected_month = "显示所有月份"
                
                # 收集试卷列表
                papers = []
                years_to_search = years if selected_year == "显示所有年份" else [selected_year]
                for y in years_to_search:
                    y_dir = os.path.join(export_base_dir, y)
                    months_to_search = [m for m in os.listdir(y_dir) if os.path.isdir(os.path.join(y_dir, m))] if selected_month == "显示所有月份" else [selected_month]
                    for m in months_to_search:
                        m_dir = os.path.join(y_dir, m)
                        if os.path.exists(m_dir):
                            for p in os.listdir(m_dir):
                                p_dir = os.path.join(m_dir, p)
                                if os.path.isdir(p_dir):
                                    variant_paths = {
                                        variant: os.path.join(p_dir, f"{p}-{variant}.tex")
                                        for variant in EXAM_OUTPUT_VARIANTS
                                        if os.path.exists(os.path.join(p_dir, f"{p}-{variant}.tex"))
                                    }
                                    # 兼容上一版已经生成的文件名，展示时统一映射到新名称。
                                    for variant, legacy_variant in EXAM_OUTPUT_LEGACY_VARIANTS.items():
                                        legacy_path = os.path.join(p_dir, f"{p}-{legacy_variant}.tex")
                                        if variant not in variant_paths and os.path.exists(legacy_path):
                                            variant_paths[variant] = legacy_path
                                    legacy_tex = os.path.join(p_dir, f"{p}.tex")
                                    if os.path.exists(legacy_tex) and "题目（含答案）" not in variant_paths:
                                        variant_paths["题目（含答案）"] = legacy_tex
                                    if variant_paths:
                                        papers.append({"name": p, "variants": variant_paths, "dir": p_dir, "year": y, "month": m})
                
                if not papers:
                    st.info("未找到符合条件的试卷")
                else:
                    st.markdown('<div class="mc-history-divider"></div>', unsafe_allow_html=True)
                    paper_names = [p["name"] for p in papers]
                    st.markdown("##### 📄 选择试卷")
                    selected_paper_name = st.selectbox("选择试卷", paper_names, label_visibility="collapsed")
                    selected_paper = next(p for p in papers if p["name"] == selected_paper_name)
                    available_variants = [
                        variant for variant in EXAM_OUTPUT_VARIANTS
                        if variant in selected_paper["variants"]
                    ]
                    selected_variant = st.radio(
                        "选择文件版本",
                        available_variants,
                        index=available_variants.index("题目（含答案）") if "题目（含答案）" in available_variants else 0,
                        horizontal=True,
                    )
                    selected_tex_path = selected_paper["variants"][selected_variant]
                    
                    st.markdown('<div class="mc-history-divider"></div>', unsafe_allow_html=True)
                    present_mode = st.radio("呈现形式", ["以题目组合形式呈现", "以整卷形式呈现"], horizontal=True, label_visibility="collapsed")
                    
                    if present_mode == "以整卷形式呈现":
                        c_src, c_pdf = st.columns(2)
                        with c_src:
                            st.markdown("##### 📜 LaTeX 源码")
                            with open(selected_tex_path, "r", encoding="utf-8") as f:
                                tex_content = f.read()
                            st.code(tex_content, language="latex", line_numbers=True)
                        with c_pdf:
                            st.markdown("##### 📑 PDF 预览")
                            pdf_path = os.path.splitext(selected_tex_path)[0] + ".pdf"
                            if os.path.exists(pdf_path):
                                import base64
                                with open(pdf_path, "rb") as f:
                                    base64_pdf = base64.b64encode(f.read()).decode('utf-8')
                                pdf_display = f'<iframe src="data:application/pdf;base64,{base64_pdf}" width="100%" height="800px" type="application/pdf"></iframe>'
                                st.markdown(pdf_display, unsafe_allow_html=True)
                            else:
                                st.warning("未找到生成的 PDF 文件。请确认该试卷是否已成功编译。")
                    
                    elif present_mode == "以题目组合形式呈现":
                        st.markdown("##### 🧩 题目组合排列")
                        with open(selected_tex_path, "r", encoding="utf-8") as f:
                            tex_content = f.read()
                        
                        # 简易解析：按 \section, \subsection, \chapter, \begin{problem}, \begin{question}, \begin{lanbox} 分块
                        import re
                        blocks = []
                        
                        # 使用正则提取所有的块
                        # 查找所有的开始标记位置
                        pattern = r'(\\chapter\{.*?\}|\\section\{.*?\}|\\subsection\{.*?\}|\\begin\{problem\}.*?\\end\{problem\}|\\begin\{question\}.*?\\end\{question\}|\\begin\{lanbox\}.*?\\end\{lanbox\})'
                        matches = re.finditer(pattern, tex_content, flags=re.DOTALL)
                        
                        for idx, match in enumerate(matches):
                            block_text = match.group(1)
                            if block_text.startswith(r'\chapter{'):
                                title = re.search(r'\\chapter\{(.*?)\}', block_text).group(1)
                                title = re.sub(r'\s+', ' ', title.replace('\n', ' ')).strip()
                                blocks.append({"type": "chapter", "content": title})
                            elif block_text.startswith(r'\section{'):
                                title = re.search(r'\\section\{(.*?)\}', block_text, re.DOTALL).group(1)
                                title = re.sub(r'\s+', ' ', title.replace('\n', ' ')).strip()
                                blocks.append({"type": "section", "content": title})
                            elif block_text.startswith(r'\subsection{'):
                                title = re.search(r'\\subsection\{(.*?)\}', block_text, re.DOTALL).group(1)
                                title = re.sub(r'\s+', ' ', title.replace('\n', ' ')).strip()
                                blocks.append({"type": "subsection", "content": title})
                            else:
                                # 清除可能会遗留的 \begin{lanbox} 和 \end{lanbox} 标记
                                clean_text = re.sub(r'\\begin\{lanbox\}', '', block_text)
                                clean_text = re.sub(r'\\end\{lanbox\}', '', clean_text)
                                blocks.append({"type": "question", "content": clean_text.strip()})
                                
                        if not blocks:
                            st.info("未能从源码中解析出具体的题目和章节，这可能是因为文件尚未插入任何题目，或者结构与预期不符。")
                        else:
                            q_count = 1
                            for b in blocks:
                                if b["type"] == "chapter":
                                    st.markdown(f"### 🗂️ {b['content']}")
                                elif b["type"] == "section":
                                    st.markdown(f"#### 🗂️ {b['content']}")
                                elif b["type"] == "subsection":
                                    st.markdown(f"##### 📝 {b['content']}")
                                elif b["type"] == "question":
                                    st.markdown(f'<div class="mc-history-question-number">第 {q_count} 题</div>', unsafe_allow_html=True)
                                    st.markdown(latex_to_markdown(b["content"]), unsafe_allow_html=True)
                                    q_count += 1
                                st.markdown('<div class="mc-history-divider"></div>', unsafe_allow_html=True)
        return

    # 1. 主题选择与组卷按钮
    template_dir = os.path.join(BASE_DIR, "Test Paper Group", "主题模板")
    theme_options = []
    if os.path.exists(template_dir):
        for d in os.listdir(template_dir):
            if os.path.isdir(os.path.join(template_dir, d)):
                theme_options.append(d)
    if not theme_options:
        theme_options = ["讲义类模板", "试卷类模板", "练习类模板"]
        
    if "exam_mode_stage" not in st.session_state:
        st.session_state["exam_mode_stage"] = "selection"
    if "exam_blocks" not in st.session_state:
        st.session_state["exam_blocks"] = []
        
    if "exam_theme_select" not in st.session_state:
        st.session_state["exam_theme_select"] = theme_options[0]
        
    if "exam_theme" not in st.session_state:
        st.session_state["exam_theme"] = st.session_state["exam_theme_select"]
        
    if "exam_q_count_input" not in st.session_state:
        st.session_state["exam_q_count_input"] = 15 if "试卷类" in st.session_state["exam_theme_select"] else 10
        
    if "exam_selected_qs" not in st.session_state:
        st.session_state["exam_selected_qs"] = []
        
    if "ai_exam_active" not in st.session_state:
        st.session_state["ai_exam_active"] = False
    if "ai_exam_modified" not in st.session_state:
        st.session_state["ai_exam_modified"] = False
        
    # 如果在排版阶段，跳过选题页面渲染
    if st.session_state["exam_mode_stage"] == "typesetting":
        render_typesetting_workspace()
        return

    # ================= 阶段一：选题购物车 =================
    # 提前处理状态同步，避免在 widget 渲染后修改其 session_state 导致 StreamlitAPIException
    # 这里非常关键，必须把用户选择的 theme 实时同步并保存到持久化变量中
    if "exam_theme_select" in st.session_state:
        if st.session_state.get("exam_theme_select") != st.session_state.get("exam_theme"):
            st.session_state["exam_theme"] = st.session_state["exam_theme_select"]
            # Only change the default value if the user hasn't actively modified the count
            if "试卷类" in st.session_state["exam_theme"]:
                st.session_state["exam_q_count_input"] = 15
            else:
                st.session_state["exam_q_count_input"] = 10
            st.session_state["_count_widget"] = st.session_state["exam_q_count_input"]

    selected_count = len(st.session_state.get("exam_selected_qs", []))
    if selected_count > st.session_state.get("exam_q_count_input", 10):
        st.session_state["exam_q_count_input"] = selected_count
        st.session_state["_count_widget"] = selected_count
        st.toast("当前新增问题数已超过预设定数，已为您新增题数上限", icon="⚠️")
    render_exam_floating_basket()

    # Sync state before widget to preserve value
    current_count = st.session_state.get("exam_q_count_input", 10)
    if "_count_widget" not in st.session_state:
        st.session_state["_count_widget"] = current_count

    with _exam_top:
        c_theme, c_num, c_ai = st.columns([3, 1, 3])
        with c_theme:
            theme = st.selectbox("选择组卷主题", options=theme_options, key="exam_theme_select", label_visibility="collapsed")
        with c_num:
            def _update_count():
                st.session_state["exam_q_count_input"] = st.session_state["_count_widget"]
            st.number_input("本次组卷数量", min_value=1, key="_count_widget", on_change=_update_count, label_visibility="collapsed")
        with c_ai:
            # 按钮状态逻辑：白底(未激活) -> 绿底(激活且未被修改) -> 蓝底(激活且被修改)
            ai_btn_type = "primary" if st.session_state["ai_exam_active"] else "secondary"
            if st.button("🤖 启用AI辅助预组卷", use_container_width=True, type=ai_btn_type):
                st.session_state["ai_exam_active"] = True
                st.session_state["ai_exam_modified"] = False # 重置修改状态
                st.rerun()
            
    # === 新增：AI 辅助预组卷面板 ===
    if st.session_state.get("ai_exam_active", False):
        st.markdown("---")
        st.markdown("### 🤖 智能组卷条件配置")
        with st.container(border=True):
            # 1. 知识板块约束
            st.markdown("##### 1. 考察知识板块")
            c_ai_subj, c_ai_diff = st.columns([1.5, 1])
            with c_ai_subj:
                extended_subjects = ["高考范围"] + SUBJECTS
                ai_subjects_raw = st.multiselect("选择本次组卷覆盖的知识板块", options=extended_subjects, default=["高考范围"], key="ai_exam_subjects")
            with c_ai_diff:
                # 2. 难度系数
                st.markdown("<div style='font-size: 14px; color: var(--mc-text); margin-bottom: 5px;'><b>目标平均难度星级</b></div>", unsafe_allow_html=True)
                from utils.star_rating import st_star_rating
                ai_difficulty = st_star_rating(label="", value=st.session_state.get("ai_exam_diff_val", 3.0), max_stars=6, key="star_ai_exam_diff")
                if ai_difficulty is not None and ai_difficulty != st.session_state.get("ai_exam_diff_val", 3.0):
                    st.session_state["ai_exam_diff_val"] = ai_difficulty
            
            # 新增：组卷意图与要求
            st.markdown("##### 2. 组卷意图与附加要求")
            c_intent_lbl, c_intent_btn = st.columns([4, 1], vertical_alignment="bottom")
            with c_intent_lbl:
                intent_text = st.text_area("请填写您的组卷想法（例如：侧重考查牛顿运动定律与能量守恒，最后一道综合题包含电磁感应，整体难度适中）", key="ai_exam_intent", height=100)
            with c_intent_btn:
                def do_polish():
                    txt = st.session_state.get("ai_exam_intent", "").strip()
                    if not txt:
                        st.toast("请先填写初步想法", icon="⚠️")
                        return
                    res = call_ai_for_polish(txt)
                    if res.startswith("❌"):
                        st.toast(res, icon="❌")
                    else:
                        st.session_state["ai_exam_intent"] = res
                        st.toast("润色成功！", icon="✨")
                st.button("✨ AI 润色想法", on_click=do_polish, use_container_width=True)
            
            # 3. 题目数量与题型（根据主题推断，如果是试卷类则固定结构）
            st.markdown("##### 3. 试卷结构约束")
            is_paper = "试卷" in theme
            if is_paper:
                st.info("💡 当前为“试卷类模板”，系统将按东北黑吉辽蒙物理卷结构（单选7+多选3+实验2+计算3=15题）抽取。")
                ai_q_count = 15
            else:
                ai_q_count = st.session_state.get("exam_q_count_input", 10)
                st.info(f"💡 当前为非试卷类模板，系统将按照您的预设，随机抽取 **{ai_q_count}** 道题目。")
            
            # 4. 执行生成按钮
            if st.button("🚀 开始智能抽题 (基于本地题库标签)", type="primary", use_container_width=True):
                if not ai_subjects_raw:
                    st.warning("请至少选择一个知识板块！")
                else:
                    with st.spinner("正在遍历题库并进行智能抽样..."):
                        # 执行基于规则的抽题算法
                        from utils.csv_ops import read_csv_index
                        csv_data = _filter_question_rows(read_csv_index())
                        
                        if not csv_data:
                            st.error("题库为空或索引未建立，请先在工具页一键重建题库索引。")
                        else:
                            import random
                            # 解析“高考范围”快捷选项
                            actual_subjects = set()
                            for s in ai_subjects_raw:
                                if s == "高考范围":
                                    actual_subjects.update(subject for subject in SUBJECTS if subject != "未分类")
                                else:
                                    actual_subjects.add(s)
                            
                            # 1. 第一轮硬过滤：过滤掉不属于所选板块的题目
                            candidates = []
                            for row in csv_data:
                                row_subj = row.get("知识板块", "")
                                # 只要该题的任何一个标签在用户选择的板块中，即可入选
                                if any(s in row_subj for s in actual_subjects):
                                    candidates.append(row)
                            
                            # 获取当前目标难度
                            current_diff = st.session_state.get("ai_exam_diff_val", 3.0)
                            intent_profile = _build_exam_intent_profile(st.session_state.get("ai_exam_intent", ""))
                            used_rel_paths = set()

                            def row_diff(row):
                                try:
                                    return float(row.get("难度星级") or 1.0)
                                except Exception:
                                    return 1.0

                            def row_rel_path(row):
                                return row.get("相对文件路径", "")

                            def pick_rows(filtered, target_count):
                                if target_count <= 0:
                                    return []
                                filtered = [q for q in filtered if row_rel_path(q) and row_rel_path(q) not in used_rel_paths]
                                if not filtered:
                                    return []
                                if intent_profile.get("active"):
                                    filtered = sorted(
                                        filtered,
                                        key=lambda q: (
                                            _exam_intent_score(q, intent_profile),
                                            -_safe_int(q.get("组卷引用次数", "0")),
                                            row_diff(q),
                                            random.random(),
                                        ),
                                        reverse=True,
                                    )
                                    picked = filtered[:target_count]
                                else:
                                    picked = random.sample(filtered, min(target_count, len(filtered)))
                                used_rel_paths.update(row_rel_path(q) for q in picked)
                                return picked
                            
                            # 辅助函数：根据难度范围过滤并随机抽取指定数量
                            def sample_questions(pool, target_count, q_type=None, diff_range=None, prefer_multi=None, prefer_subjects=None):
                                filtered = list(pool)
                                if q_type:
                                    filtered = [q for q in filtered if q_type in q.get("题型", "")]
                                if diff_range:
                                    # 如果没有星级，默认视为基础题 (1.0)
                                    filtered = [q for q in filtered if diff_range[0] <= row_diff(q) <= diff_range[1]]
                                if prefer_subjects:
                                    subject_filtered = [q for q in filtered if any(s in q.get("知识板块", "") for s in prefer_subjects)]
                                    if subject_filtered:
                                        filtered = subject_filtered
                                if prefer_multi is True:
                                    multi_filtered = [q for q in filtered if _row_looks_multi_choice(q)]
                                    if multi_filtered:
                                        filtered = multi_filtered
                                elif prefer_multi is False:
                                    single_filtered = [q for q in filtered if not _row_looks_multi_choice(q)]
                                    if single_filtered:
                                        filtered = single_filtered
                                
                                picked = pick_rows(filtered, target_count)
                                needed = target_count - len(picked)
                                if needed <= 0:
                                    return picked

                                # 如果指定难度或多选特征不够，放宽这些软限制补齐，但仍保持题型和去重。
                                backup = list(pool)
                                if q_type:
                                    backup = [q for q in backup if q_type in q.get("题型", "")]
                                picked += pick_rows(backup, needed)
                                return picked

                            selected_rows = []
                            if is_paper:
                                # 东北黑吉辽蒙高考物理卷：7 单选 + 3 多选 + 2 实验 + 3 计算。
                                # 单选 7 题 (基础3 + 中档3 + 难题1)
                                sq_base = sample_questions(candidates, 3, q_type="选择题", diff_range=(0.0, 2.5), prefer_multi=False)
                                sq_mid = sample_questions(candidates, 3, q_type="选择题", diff_range=(3.0, 4.0), prefer_multi=False)
                                sq_hard = sample_questions(candidates, 1, q_type="选择题", diff_range=(4.5, 6.0), prefer_multi=False)
                                
                                # 多选 3 题 (基础1 + 中档1 + 难题1)
                                mq_base = sample_questions(candidates, 1, q_type="选择题", diff_range=(0.0, 2.5), prefer_multi=True)
                                mq_mid = sample_questions(candidates, 1, q_type="选择题", diff_range=(3.0, 4.0), prefer_multi=True)
                                mq_hard = sample_questions(candidates, 1, q_type="选择题", diff_range=(4.5, 6.0), prefer_multi=True)
                                
                                # 实验 2 题：优先从力学实验、电学实验板块抽取。
                                experiment_rows = sample_questions(
                                    candidates,
                                    2,
                                    q_type="实验题",
                                    diff_range=(1.5, 4.5),
                                    prefer_subjects=("力学试验", "电学实验"),
                                )

                                # 计算 3 题 (基础1 + 中档1 + 难题1)
                                aq_base = sample_questions(candidates, 1, q_type="计算题", diff_range=(0.0, 2.5))
                                aq_mid = sample_questions(candidates, 1, q_type="计算题", diff_range=(3.0, 4.0))
                                aq_hard = sample_questions(candidates, 1, q_type="计算题", diff_range=(4.5, 6.0), prefer_subjects=intent_profile.get("final_subjects"))

                                selected_rows = sq_base + sq_mid + sq_hard + mq_base + mq_mid + mq_hard + experiment_rows + aq_base + aq_mid + aq_hard
                            else:
                                # 非试卷类：根据目标难度和总数进行大乱炖抽样
                                # 按照正态分布近似：60% 中档，20% 基础，20% 难题 (围绕目标难度上下浮动)
                                base_count = int(ai_q_count * 0.2)
                                hard_count = int(ai_q_count * 0.2)
                                mid_count = ai_q_count - base_count - hard_count
                                
                                # 根据用户设定的 current_diff 动态平移区间
                                mid_range = (max(0.0, current_diff - 1.0), min(6.0, current_diff + 1.0))
                                base_range = (0.0, max(0.0, current_diff - 1.5))
                                hard_range = (min(6.0, current_diff + 1.5), 6.0)
                                
                                r_base = sample_questions(candidates, base_count, diff_range=base_range)
                                r_mid = sample_questions(candidates, mid_count, diff_range=mid_range)
                                r_hard = sample_questions(candidates, hard_count, diff_range=hard_range)
                                
                                selected_rows = r_base + r_mid + r_hard
                            
                            # 最终检查并转换为路径
                            final_paths = []
                            for r in selected_rows:
                                p = os.path.join(CHAPTERS_DIR, r["相对文件路径"])
                                if os.path.exists(p) and p not in final_paths:
                                    final_paths.append(p)
                                    
                            if len(final_paths) < ai_q_count:
                                st.warning(f"题库中满足条件的题目不足，仅抽取到 {len(final_paths)} 题。")
                            else:
                                st.success(f"智能组卷完成！已成功抽取 {len(final_paths)} 道题目。")
                            if intent_profile.get("active"):
                                matched_subjects = "、".join(intent_profile.get("subjects") or []) or "无明确板块"
                                st.caption(f"已使用组卷意图参与筛选：匹配板块 {matched_subjects}，关键词 {len(intent_profile.get('tokens') or [])} 个。")
                                
                            # 强制覆盖当前购物车
                            st.session_state["exam_selected_qs"] = final_paths
                            st.session_state["exam_q_count_input"] = len(final_paths)
                            st.session_state["ai_exam_modified"] = False
                            time.sleep(1)
                            st.rerun()
            
    # 注入 CSS：美化 number_input 的边框使其明显，并隐藏原生上下箭头，以及根据状态设置 primary 按钮颜色
    css_injection = """
    <style>
    """
    
    if st.session_state["ai_exam_active"]:
        if st.session_state["ai_exam_modified"]:
            btn_color = "#CC785C" 
        else:
            btn_color = "#7C9A6E"
            
        css_injection += f"""
        div[data-testid="column"]:nth-child(3) button[kind="primary"] {{
            background-color: {btn_color} !important;
            border-color: {btn_color} !important;
            color: white !important;
        }}
        """
        
    css_injection += "</style>"
    st.markdown(css_injection, unsafe_allow_html=True)
        
    # 3. 复用浏览界面进行选题（说明文字已并入右侧试题篮面板，不再额外占行）
    page_browse(is_exam_mode=True)

EXAM_OUTPUT_VARIANTS = ("题目", "答案", "题目（含答案）")
EXAM_OUTPUT_LEGACY_VARIANTS = {
    "题目": "纯题目",
    "答案": "纯答案",
    "题目（含答案）": "题目带答案",
}


def _clean_exam_export_filename(export_filename: str) -> str:
    """统一移除输出名称中的“模板”字样，避免出现在文件名和文档标题中。"""
    cleaned = re.sub(r"模板", "", str(export_filename or ""))
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned or "物理组卷"


def _exam_output_tex_path(export_filename: str, export_dir: str, variant: str = "题目（含答案）") -> str:
    export_filename = _clean_exam_export_filename(export_filename)
    suffix = f"-{variant}" if variant else ""
    return os.path.join(export_dir, export_filename, f"{export_filename}{suffix}.tex")


def _strip_answer_solution_blocks(text: str) -> str:
    """删除答案与解析环境，只保留题目部分。"""
    pattern = (
        r'\\begin\{(?P<env>answer|solutions?)\}'
        r'(?:\s*\[[^\]]*\]){0,3}[\s\S]*?'
        r'\\end\{(?P=env)\}'
    )
    cleaned = re.sub(pattern, '', text, flags=re.IGNORECASE)
    return re.sub(r'\n{3,}', '\n\n', cleaned).strip()


def _extract_environment_body(text: str, environment_pattern: str) -> str:
    match = re.search(
        rf'\\begin\{{(?:{environment_pattern})\}}(?:\s*\[[^\]]*\]){{0,3}}([\s\S]*?)'
        rf'\\end\{{(?:{environment_pattern})\}}',
        text,
        flags=re.IGNORECASE,
    )
    return match.group(1).strip() if match else ""


def _build_answer_only_body(question_sources, theme_name: str) -> str:
    """按题号汇总答案与解析，不重复输出题干。"""
    heading = r'\chapter{参考答案与解析}' if theme_name == "讲义类模板" else r'\section{参考答案与解析}'
    parts = [heading]
    for index, source in enumerate(question_sources, start=1):
        answer = _extract_environment_body(source, "answer")
        solution = _extract_environment_body(source, "solutions?")
        parts.append(f"\\subsection*{{第 {index} 题}}")
        parts.append(r'\par\noindent\textbf{答案：}' + (answer or r'\textit{暂未录入答案。}'))
        parts.append(r'\par\noindent\textbf{解析：}' + (solution or r'\textit{暂未录入解析。}'))
    return "\n\n".join(parts)


def _set_generated_document_title(content: str, title: str) -> str:
    content = re.sub(
        r'\\title\{.*?\}',
        lambda _match: f'\\title{{{title}}}',
        content,
        count=1,
        flags=re.DOTALL,
    )
    content = re.sub(
        r'\\renewcommand\{\\mytitle\}\{.*?\}',
        lambda _match: f'\\renewcommand{{\\mytitle}}{{{title}}}',
        content,
        count=1,
        flags=re.DOTALL,
    )
    return content


def _write_exam_bundle(export_filename, export_dir, pre_content, generated_body, post_content, question_sources, theme_name):
    """写出题目、答案、题目（含答案）三份 LaTeX 文件。"""
    export_filename = _clean_exam_export_filename(export_filename)
    final_export_dir = os.path.join(export_dir, export_filename)
    ensure_dir(final_export_dir)

    bodies = {
        "题目": _strip_answer_solution_blocks(generated_body),
        "答案": _build_answer_only_body(question_sources, theme_name),
        "题目（含答案）": generated_body,
    }
    output_files = {}
    for variant in EXAM_OUTPUT_VARIANTS:
        variant_title = f"{export_filename}（{variant}）"
        variant_content = pre_content + bodies[variant] + "\n\n" + post_content
        variant_content = _set_generated_document_title(variant_content, variant_title)
        output_file = _exam_output_tex_path(export_filename, export_dir, variant)
        atomic_write_text(output_file, variant_content, backup=os.path.exists(output_file))
        output_files[variant] = output_file
    return output_files

def _next_exam_export_filename(export_dir: str, theme_name: str, today=None) -> str:
    import datetime

    today = today or datetime.date.today()
    clean_theme_name = _clean_exam_export_filename(theme_name)
    prefix = f"{today.strftime('%Y')}年{today.strftime('%m')}月{today.strftime('%d')}日 {clean_theme_name}组卷"
    max_index = 0

    if os.path.exists(export_dir):
        for name in os.listdir(export_dir):
            stem = os.path.splitext(name)[0] if name.endswith(".tex") else name
            stem = _clean_exam_export_filename(stem)
            if not stem.startswith(prefix):
                continue
            suffix = stem[len(prefix):]
            if suffix.isdigit():
                max_index = max(max_index, int(suffix))

    return f"{prefix}{max_index + 1}"

def _compile_exam_pdf(tex_path: str) -> dict:
    if not tex_path or not os.path.exists(tex_path):
        return {"ok": False, "error": "找不到待编译的 tex 文件。"}

    xelatex = shutil.which("xelatex")
    if not xelatex:
        return {"ok": False, "error": "未检测到 xelatex，已生成 tex 文件但无法自动编译 PDF。"}

    work_dir = os.path.dirname(tex_path)
    tex_name = os.path.basename(tex_path)
    last_output = ""

    try:
        for _ in range(2):
            completed = subprocess.run(
                [
                    xelatex,
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    "-file-line-error",
                    tex_name,
                ],
                cwd=work_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
            last_output = completed.stdout or ""
            if completed.returncode != 0:
                return {
                    "ok": False,
                    "error": "PDF 编译失败，请检查 LaTeX 日志。",
                    "log": last_output[-4000:],
                }

        pdf_path = os.path.splitext(tex_path)[0] + ".pdf"
        if os.path.exists(pdf_path):
            return {"ok": True, "pdf_path": pdf_path, "log": last_output[-2000:]}
        return {"ok": False, "error": "xelatex 已运行，但未找到生成的 PDF 文件。", "log": last_output[-4000:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "PDF 编译超时，tex 文件已保留，可稍后手动编译。", "log": last_output[-4000:]}
    except Exception as e:
        return {"ok": False, "error": f"PDF 编译异常：{e}", "log": last_output[-4000:]}

def _safe_int(value, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default

def _question_paths_from_exam_blocks(blocks) -> list:
    paths = []
    seen = set()
    for blk in blocks or []:
        if blk.get("type") != "question":
            continue
        path = blk.get("path")
        if path and path not in seen and os.path.exists(path):
            paths.append(path)
            seen.add(path)
    return paths

def _increment_exam_usage_counts(question_paths) -> dict:
    updated = 0
    skipped = []

    for fpath in question_paths:
        try:
            content = read_question_text(fpath)
            meta, _ = parse_meta_data(content)
            if not meta or not str(meta.get("ID", "")).strip():
                skipped.append(os.path.basename(fpath))
                continue

            meta["组卷引用次数"] = str(_safe_int(meta.get("组卷引用次数", "0")) + 1)
            new_content = inject_meta_data(content, meta)
            if new_content != content:
                atomic_write_text(fpath, new_content, backup=True)
                _update_csv_index_for_content_change(fpath, new_content)
            updated += 1
        except Exception:
            skipped.append(os.path.basename(fpath))

    if updated:
        _clear_advanced_search_result_cache()
        clear_statistics_cache()
    return {"updated": updated, "skipped": skipped}

def _build_exam_intent_profile(intent_text: str) -> dict:
    text = (intent_text or "").strip()
    if not text:
        return {"active": False, "text": "", "subjects": [], "tokens": [], "final_subjects": [], "difficulty": ""}

    subjects = [s for s in SUBJECTS if s and s in text]
    final_subjects = [
        s for s in subjects
        if re.search(rf"(最后|压轴|最后一题|最后一道).{{0,12}}{re.escape(s)}|{re.escape(s)}.{{0,12}}(最后|压轴|最后一题|最后一道)", text)
    ]

    difficulty = ""
    if any(word in text for word in ("压轴", "拔高", "难题", "综合", "挑战")):
        difficulty = "hard"
    if any(word in text for word in ("基础", "简单", "不要太难", "别太难", "中档", "适中")):
        difficulty = "medium_or_easy"

    stop_words = {
        "本次", "组卷", "试卷", "题目", "考察", "侧重", "希望", "需要", "可以", "必须",
        "不要", "最后", "一道", "最后一道", "最后一题", "比较", "适合", "学生", "高中",
        "物理", "训练", "练习", "讲义", "模拟", "高考", "范围", "题型", "难度",
    }
    chunks = re.split(r"[\s，。；、,.!?！？：:（）()\[\]【】\"'“”‘’]+", text)
    tokens = set(subjects)
    for chunk in chunks:
        chunk = chunk.strip()
        if len(chunk) < 2 or chunk in stop_words:
            continue
        if len(chunk) <= 8:
            tokens.add(chunk)
        else:
            for size in (2, 3, 4):
                for i in range(0, max(0, len(chunk) - size + 1)):
                    token = chunk[i:i + size]
                    if token not in stop_words:
                        tokens.add(token)

    return {
        "active": True,
        "text": text,
        "subjects": subjects,
        "tokens": sorted(tokens, key=lambda x: (-len(x), x))[:80],
        "final_subjects": final_subjects,
        "difficulty": difficulty,
    }

def _exam_intent_score(row: dict, profile: dict) -> float:
    if not profile.get("active"):
        return 0.0

    haystack = "".join(
        str(row.get(field, ""))
        for field in ("文件名称", "试卷名称", "知识板块", "标签", "知识点", "物理方法", "备注", "题干", "答案", "解析")
    )
    score = 0.0
    for subject in profile.get("subjects", []):
        if subject in str(row.get("知识板块", "")):
            score += 12
        elif subject in haystack:
            score += 6

    for token in profile.get("tokens", []):
        if token and token in haystack:
            score += min(8, max(2, len(token)))

    try:
        diff = float(row.get("难度星级") or 1.0)
    except Exception:
        diff = 1.0
    if profile.get("difficulty") == "hard":
        score += diff
    elif profile.get("difficulty") == "medium_or_easy":
        score += max(0.0, 6.0 - diff)

    return score

def _row_looks_multi_choice(row: dict) -> bool:
    if row.get("题型") in QUESTION_TYPES:
        return row["题型"] == "多项选择题"
    text = "".join(str(row.get(field, "")) for field in ("标签", "知识点", "物理方法", "备注", "题干", "答案", "解析"))
    answer = str(row.get("答案", ""))
    answer_letters = re.findall(r"(?<![A-Za-z])([A-Da-d])(?![A-Za-z])", answer)
    return "多选" in text or len(set(letter.upper() for letter in answer_letters)) >= 2

def _replace_choices_with_items(text: str) -> str:
    idx = 0
    while True:
        idx = text.find(r'\choice', idx)
        if idx == -1:
            break
        start_brace = text.find('{', idx)
        if start_brace == -1:
            idx += len(r'\choice')
            continue
        if text[idx + 7:start_brace].strip() != '':
            idx += len(r'\choice')
            continue
        next_char_idx = start_brace + 1
        while next_char_idx < len(text) and text[next_char_idx].isspace():
            next_char_idx += 1
        is_double = False
        if next_char_idx < len(text) and text[next_char_idx] == '{':
            is_double = True
            content_start = next_char_idx + 1
        else:
            content_start = start_brace + 1
        brace_count = 2 if is_double else 1
        match_end = -1
        content = ''
        for i in range(content_start, len(text)):
            if text[i] == '{':
                brace_count += 1
            elif text[i] == '}':
                brace_count -= 1
            if brace_count == 0:
                match_end = i + 1
                inner = text[content_start:i]
                if is_double:
                    last_brace_idx = inner.rfind('}')
                    content = inner[:last_brace_idx].strip() if last_brace_idx != -1 else inner.strip()
                else:
                    content = inner.strip()
                break
        if match_end != -1:
            prefix = text[:idx]
            suffix = text[match_end:]
            text = prefix + r'\item ' + content + suffix
            idx = len(prefix) + len(r'\item ') + len(content)
        else:
            idx += len(r'\choice')
    return text

def generate_exam_paper(export_filename, export_dir, blocks, theme_name):
    export_filename = _clean_exam_export_filename(export_filename)
    # 确保导出目录存在
    ensure_dir(export_dir)
    
    # 读取模板内容
    template_path = os.path.join(BASE_DIR, "Test Paper Group", "主题模板", theme_name, f"{theme_name}.tex")
    if not os.path.exists(template_path):
        return None
        
    with open(template_path, "r", encoding="utf-8") as f:
        template_content = f.read()
        
    # 生成要插入的 content
    body_lines = []
    question_sources = []
    for blk in blocks:
        if blk["type"] == "chapter":
            body_lines.append(f"\\chapter{{{blk['title']}}}")
            if blk.get("content"):
                body_lines.append(blk["content"])
        elif blk["type"] == "section":
            body_lines.append(f"\\section{{{blk['title']}}}")
            if blk.get("content"):
                body_lines.append(blk["content"])
        elif blk["type"] == "subsection":
            body_lines.append(f"\\subsection{{{blk['title']}}}")
            if blk.get("content"):
                body_lines.append(blk["content"])
        elif blk["type"] == "question":
            q_path = blk["path"]
            if os.path.exists(q_path):
                with open(q_path, "r", encoding="utf-8") as qf:
                    q_content = qf.read()
                    question_sources.append(q_content)
                    if theme_name == "讲义类模板":
                        body_lines.append("\\begin{lanbox}\n" + q_content + "\n\\end{lanbox}")
                    else:
                        body_lines.append(q_content)
                        
    # 如果是试卷类模板，需要对题目格式和分数进行二次加工
    if theme_name == "试卷类模板":
        import re
        q_index = 0
        current_section = 0
        new_body_lines = []
        for line in body_lines:
            if line.startswith(r"\section{"):
                current_section += 1
                new_body_lines.append(line)
            elif r"\begin{problem}" in line:
                q_index += 1
                
                # 第一步：增加题目序号注释 %*
                line = f"% {q_index}.\n" + line
                
                if current_section >= 2 or q_index >= 11:
                    # 东北物理卷第二大题（第 11--15 题）统一使用非选择题格式。
                    # 1. 替换为 \begin{problem} 并带上对应分数
                    # 2. 删除后面紧跟的5个参数括号 {...}
                    if q_index in (11, 12):
                        points = 8
                    elif q_index == 13:
                        points = 10
                    elif q_index == 14:
                        points = 12
                    elif q_index == 15:
                        points = 16
                    else:
                        points = 10 # fallback
                        
                    # 替换 \begin{problem}{...}{...}{...}{...}{...} -> \begin{problem}[points = xx]
                    # 容错：有些参数可能换行了或者有空格，用 \s* 和 dotall 处理
                    line = re.sub(r'\\begin\{problem\}\s*\{.*?\}\s*\{.*?\}\s*\{.*?\}\s*\{.*?\}\s*\{.*?\}', f'\\\\begin{{problem}}[points = {points}]', line, flags=re.DOTALL)
                else:
                    # 对于前三个 section (选择填空) 的题目
                    # 1. 替换为 \begin{question}
                    # 2. 删除后面紧跟的5个参数括号 {...}
                    # 3. 将对应的 \end{problem} 替换为 \end{question}
                    line = re.sub(r'\\begin\{problem\}\s*\{.*?\}\s*\{.*?\}\s*\{.*?\}\s*\{.*?\}\s*\{.*?\}', r'\\begin{question}', line, flags=re.DOTALL)
                    line = line.replace(r'\end{problem}', r'\end{question}')
                    
                # 【新增修复】：将 \begin{choices} 替换为没有方括号的形式（比如去除 \begin{choices}[2] 等，恢复为 exam-zh 默认选项）
                # 题库里带参数的 \begin{choices}[2] 可能会在试卷模板里报错或者不兼容
                # 用户要求类似原来模板的纯净 \begin{choices}
                # 但是实际上用户刚才提到的是 choices，而模板里使用的是 \begin{choices} \item ...
                # 题库中用的是 \choice{{...}}，模板中似乎需要 \item
                # 我们在这里将 \choice{{...}} 转换为 \item ... 
                # 同时将带参数的 \begin{choices}[2] 去除参数
                line = re.sub(r'\\begin\{choices\}\[.*?\]', r'\\begin{choices}', line)
                
                line = _replace_choices_with_items(line)
                    
                new_body_lines.append(line)
            else:
                # 处理可能散落在别的行的 \end{problem} 和 \choice 等
                if current_section < 2 and q_index < 11 and r'\end{problem}' in line:
                    line = line.replace(r'\end{problem}', r'\end{question}')
                    
                line = re.sub(r'\\begin\{choices\}\[.*?\]', r'\\begin{choices}', line)
                line = _replace_choices_with_items(line)
                
                new_body_lines.append(line)
        body_lines = new_body_lines

    generated_body = "\n\n".join(body_lines)
    
    # 替换标题（如果有的话）
    import re
    if theme_name == "试卷类模板":
        # 试卷类模板使用的是 \title{...}
        template_content = re.sub(r'\\title\{.*?\}', f'\\\\title{{{export_filename}}}', template_content)
    elif r'\renewcommand{\mytitle}' in template_content:
        template_content = re.sub(r'\\renewcommand\{\\mytitle\}\{.*?\}', f'\\\\renewcommand{{\\\\mytitle}}{{{export_filename}}}', template_content)
    
    # 查找 \begin{document} 之后的内容
    doc_idx = template_content.find(r'\begin{document}')
    if doc_idx != -1:
        # 寻找正文里第一个 \chapter 或者 \section 或者 \begin{problem} 或者 \begin{question} 作为切割点
        chap_idx = template_content.find(r'\chapter{', doc_idx)
        sec_idx = template_content.find(r'\section{', doc_idx)
        prob_idx = template_content.find(r'\begin{problem}', doc_idx)
        ques_idx = template_content.find(r'\begin{question}', doc_idx)
        
        candidates = [idx for idx in (chap_idx, sec_idx, prob_idx, ques_idx) if idx != -1]
        if candidates:
            insert_idx = min(candidates)
            end_idx = template_content.rfind(r'\end{document}')
            
            if end_idx != -1:
                # 头部内容保留（包括 \renewcommand{\mytitle}{...} 和所有前置的格式设置）
                pre_content = template_content[:insert_idx]
                # 尾部内容保留（\end{document}及以后）
                post_content = template_content[end_idx:]
                
                return _write_exam_bundle(
                    export_filename,
                    export_dir,
                    pre_content,
                    generated_body,
                    post_content,
                    question_sources,
                    theme_name,
                )
            
    return None

def render_typesetting_workspace():
    st.subheader("🖨️ 试卷排版工作台")
    
    # 动态生成默认的输出文件名
    import datetime
    today = datetime.date.today()
    y_str = today.strftime("%Y")
    m_str = today.strftime("%m")
    d_str = today.strftime("%d")
    
    theme_name = st.session_state.get("exam_theme", "练习类模板")
    export_dir = os.path.join(BASE_DIR, "Test Paper Group", "导出文件", y_str, m_str)
    
    default_filename = _next_exam_export_filename(export_dir, theme_name, today=today)
    
    # 返回按钮与生成按钮栏
    c_back, c_name, c_gen = st.columns([1, 1.5, 1])
    with c_back:
        def go_back_to_selection():
            st.session_state["exam_mode_stage"] = "selection"
        st.button("⬅️ 返回继续选题", on_click=go_back_to_selection, use_container_width=True)
    with c_name:
        export_filename = st.text_input("输出文件名", value=default_filename, label_visibility="collapsed")
    with c_gen:
        if st.button("🖨️ 确认生成试卷", type="primary", use_container_width=True):
            if theme_name in ("练习类模板", "讲义类模板", "试卷类模板"):
                export_filename = _clean_exam_export_filename(export_filename)
                expected_paths = [
                    _exam_output_tex_path(export_filename, export_dir, variant)
                    for variant in EXAM_OUTPUT_VARIANTS
                ]
                is_overwrite = any(os.path.exists(path) for path in expected_paths)
                output_files = generate_exam_paper(export_filename, export_dir, st.session_state["exam_blocks"], theme_name)
                if output_files:
                    st.success("三份 LaTeX 文件已生成：题目、答案、题目（含答案）。")
                    compile_failures = []
                    for variant in EXAM_OUTPUT_VARIANTS:
                        output_path = output_files.get(variant)
                        if not output_path:
                            compile_failures.append((variant, {"error": "未生成 LaTeX 文件。", "log": ""}))
                            continue
                        compile_result = _compile_exam_pdf(output_path)
                        if compile_result.get("ok"):
                            st.success(f"{variant} PDF：{compile_result.get('pdf_path')}")
                        else:
                            compile_failures.append((variant, compile_result))

                    for variant, compile_result in compile_failures:
                        st.warning(f"{variant}：{compile_result.get('error', 'PDF 编译失败。')}")
                        if compile_result.get("log"):
                            with st.expander(f"查看“{variant}”的 xelatex 编译日志"):
                                st.code(compile_result["log"])

                    if is_overwrite:
                        st.info("检测到本次为覆盖同名试卷，未重复增加题目组卷引用次数。")
                    else:
                        usage_result = _increment_exam_usage_counts(_question_paths_from_exam_blocks(st.session_state["exam_blocks"]))
                        if usage_result.get("updated", 0):
                            st.success(f"已更新 {usage_result['updated']} 道题目的组卷引用次数。")
                        if usage_result.get("skipped"):
                            st.caption("部分题目缺少完整元数据，未更新引用次数：" + "、".join(usage_result["skipped"][:5]))
                else:
                    st.error("生成失败，请检查模板文件是否存在或格式是否正确！")
            else:
                st.warning("暂不支持其他模板的生成，敬请期待！")
    
    st.markdown("---")
    
    st.subheader("📑 试卷结构与排版")
    
    # 计算当前试卷中有多少道题目（用于下拉菜单选位置）
    blocks = st.session_state["exam_blocks"]
    q_count = sum(1 for b in blocks if b["type"] == "question")
    
    # 构建插入位置选项
    # 例如: "第1题前", "第2题前", ..., "最后一题后"
    insert_positions = [f"第{i}题前" for i in range(1, q_count + 1)]
    insert_positions.append("最后一题后" if q_count > 0 else "列表最末尾")
    
    # 插入新章节/小节
    st.markdown("""
    <style>
    /* 移除表单的外边框和背景色 */
    div[data-testid="stForm"] {
        border: none !important;
        padding: 0 !important;
        background-color: transparent !important;
    }
    </style>
    """, unsafe_allow_html=True)
    
    # 构建表单处理逻辑的辅助函数
    def _insert_block(blk_type, title, pos_str):
        new_block = {"id": str(uuid.uuid4()), "type": blk_type, "title": title}
        if pos_str in ("最后一题后", "列表最末尾"):
            st.session_state["exam_blocks"].append(new_block)
        else:
            target_q_num = int(pos_str.replace("第", "").replace("题前", ""))
            current_q = 0
            insert_idx = len(blocks)
            for idx, b in enumerate(blocks):
                if b["type"] == "question":
                    current_q += 1
                    if current_q == target_q_num:
                        insert_idx = idx
                        break
            st.session_state["exam_blocks"].insert(insert_idx, new_block)
            
    # 动态渲染根据不同模板决定是 2 层还是 3 层结构
    if theme_name == "讲义类模板":
        # 讲义类有 章、节、小节 三层
        c_label_0, c_input_0, c_pos_0, c_submit_0 = st.columns([1.5, 3.5, 1.5, 1.5])
        with c_label_0:
            st.markdown("<div style='padding-top:8px;'><b>📚 插入章</b></div>", unsafe_allow_html=True)
        with c_input_0:
            chap_title = st.text_input("文本内容", placeholder="例如：第一章 运动的描述", label_visibility="collapsed", key="chap_title_input")
        with c_pos_0:
            chap_pos = st.selectbox("插入位置", insert_positions, index=0, label_visibility="collapsed", key="chap_pos")
        with c_submit_0:
            def on_chap_submit():
                t = st.session_state.get("chap_title_input", "")
                p = st.session_state.get("chap_pos", insert_positions[0])
                if t:
                    _insert_block("chapter", t, p)
                    st.session_state["chap_title_input"] = ""
            st.button("确认插入", key="chap_submit", on_click=on_chap_submit, use_container_width=True)

        # 节
        c_label_1, c_input_1, c_pos_1, c_submit_1 = st.columns([1.5, 3.5, 1.5, 1.5])
        with c_label_1:
            st.markdown("<div style='padding-top:8px; color: var(--mc-accent);'><b>🗂️ 插入节</b></div>", unsafe_allow_html=True)
        with c_input_1:
            sec_title = st.text_input("文本内容", placeholder="例如：第一节 质点和参考系", label_visibility="collapsed", key="sec_title_input")
        with c_pos_1:
            sec_pos = st.selectbox("插入位置", insert_positions, index=0, label_visibility="collapsed", key="sec_pos")
        with c_submit_1:
            def on_sec_submit():
                t = st.session_state.get("sec_title_input", "")
                p = st.session_state.get("sec_pos", insert_positions[0])
                if t:
                    _insert_block("section", t, p)
                    st.session_state["sec_title_input"] = ""
            st.button("确认插入", key="sec_submit", on_click=on_sec_submit, use_container_width=True)
                    
        # 小节
        c_label_2, c_input_2, c_pos_2, c_submit_2 = st.columns([1.5, 3.5, 1.5, 1.5])
        with c_label_2:
            st.markdown("<div style='padding-top:8px; color: var(--mc-muted);'><b>📝 插入小节</b></div>", unsafe_allow_html=True)
        with c_input_2:
            subsec_title = st.text_input("文本内容", placeholder="例如：考点一", label_visibility="collapsed", key="subsec_title_input")
        with c_pos_2:
            subsec_pos = st.selectbox("插入位置", insert_positions, index=0, label_visibility="collapsed", key="subsec_pos")
        with c_submit_2:
            def on_subsec_submit():
                t = st.session_state.get("subsec_title_input", "")
                p = st.session_state.get("subsec_pos", insert_positions[0])
                if t:
                    _insert_block("subsection", t, p)
                    st.session_state["subsec_title_input"] = ""
            st.button("确认插入", key="subsec_submit", on_click=on_subsec_submit, use_container_width=True)
            
    elif theme_name == "试卷类模板":
        # 东北黑吉辽蒙物理卷分为选择题、非选择题两个固定部分。
        st.markdown("<div style='color: var(--mc-muted); font-size: 0.9em; margin-bottom: 10px;'>💡 提示：系统已按东北黑吉辽蒙高考物理卷自动插入两个题型模块；下方按钮可用于缺失时补充。</div>", unsafe_allow_html=True)
        
        # 预设的两个大题区
        exam_presets = [
            {
                "label": "插入选择题部分",
                "default_title": NORTHEAST_PHYSICS_CHOICE_SECTION,
                "default_pos_index": 0 # 第1题前
            },
            {
                "label": "插入非选择题部分",
                "default_title": NORTHEAST_PHYSICS_NON_CHOICE_SECTION,
                "default_pos_index": min(10, len(insert_positions)-1) # 第11题前
            }
        ]
        
        for i, preset in enumerate(exam_presets):
            c_label, c_input, c_pos, c_submit = st.columns([1.5, 3.5, 1.5, 1.5])
            with c_label:
                st.markdown(f"<div style='padding-top:8px;'><b>🗂️ {preset['label']}</b></div>", unsafe_allow_html=True)
            with c_input:
                # 试卷模板的标题通常比较长，直接放入 content 中，把真正的 title 留空，或者将这段话当作 title
                # 按照用户的代码，这些其实是放在 \section{...} 里面的，所以还是算作 title
                sec_title = st.text_area("文本内容", value=preset["default_title"], height=68, label_visibility="collapsed", key=f"exam_sec_title_{i}")
            with c_pos:
                sec_pos = st.selectbox("插入位置", insert_positions, index=preset["default_pos_index"], label_visibility="collapsed", key=f"exam_sec_pos_{i}")
            with c_submit:
                def make_submit_callback(i_val):
                    def callback():
                        t = st.session_state.get(f"exam_sec_title_{i_val}", "")
                        p = st.session_state.get(f"exam_sec_pos_{i_val}", insert_positions[0])
                        if t:
                            _insert_block("section", t, p)
                    return callback
                
                # 垂直居中对齐
                st.markdown("<div style='padding-top:12px;'></div>", unsafe_allow_html=True)
                st.button("确认插入", key=f"exam_sec_submit_{i}", on_click=make_submit_callback(i), use_container_width=True)

    else:
        # 练习类及其他模板，仅保留 章节 和 小节
        c_label_1, c_input_1, c_pos_1, c_submit_1 = st.columns([1.5, 3.5, 1.5, 1.5])
        with c_label_1:
            st.markdown("<div style='padding-top:8px;'><b>🗂️ 插入章节</b></div>", unsafe_allow_html=True)
        with c_input_1:
            sec_title = st.text_input("文本内容", placeholder="例如：一、选择题", label_visibility="collapsed", key="sec_title_input")
        with c_pos_1:
            sec_pos = st.selectbox("插入位置", insert_positions, index=0, label_visibility="collapsed", key="sec_pos")
        with c_submit_1:
            def on_sec_submit():
                t = st.session_state.get("sec_title_input", "")
                p = st.session_state.get("sec_pos", insert_positions[0])
                if t:
                    _insert_block("section", t, p)
                    st.session_state["sec_title_input"] = ""
            st.button("确认插入", key="sec_submit", on_click=on_sec_submit, use_container_width=True)
                    
        c_label_2, c_input_2, c_pos_2, c_submit_2 = st.columns([1.5, 3.5, 1.5, 1.5])
        with c_label_2:
            st.markdown("<div style='padding-top:8px; color: #8b949e;'><b>📝 插入小节</b></div>", unsafe_allow_html=True)
        with c_input_2:
            subsec_title = st.text_input("文本内容", placeholder="例如：(一) 单选题", label_visibility="collapsed", key="subsec_title_input")
        with c_pos_2:
            subsec_pos = st.selectbox("插入位置", insert_positions, index=0, label_visibility="collapsed", key="subsec_pos")
        with c_submit_2:
            def on_subsec_submit():
                t = st.session_state.get("subsec_title_input", "")
                p = st.session_state.get("subsec_pos", insert_positions[0])
                if t:
                    _insert_block("subsection", t, p)
                    st.session_state["subsec_title_input"] = ""
            st.button("确认插入", key="subsec_submit", on_click=on_subsec_submit, use_container_width=True)
                    
    st.markdown("<br>", unsafe_allow_html=True)
    
    # 遍历显示 Blocks (单列流式布局，改为左右两栏)
    blocks = st.session_state["exam_blocks"]
    q_counter = 1
    chap_counter = 0
    sec_counter = 0
    subsec_counter = 0
    
    for i, blk in enumerate(blocks):
        # 每一行分为左右两列：左侧显示标题和控制按钮，右侧显示渲染结果
        c_left, c_right = st.columns([3, 7], gap="large")
        
        with c_left:
            if blk["type"] == "chapter":
                chap_counter += 1
                sec_counter = 0
                subsec_counter = 0
                # 允许动态修改章节标题
                col_l, col_r = st.columns([1.5, 3.5])
                with col_l:
                    st.markdown(f"<div style='padding-top:8px; white-space:nowrap;'><b>📚 第{chap_counter}章标题</b></div>", unsafe_allow_html=True)
                with col_r:
                    # 修复性能问题：不将 widget 的返回值直接硬塞回 blk 中，除非它发生了改变
                    # 使用 on_change 回调或直接依赖 session_state 来存储值
                    new_val = st.text_input("章标题", value=blk['title'], key=f"blk_title_{blk['id']}", label_visibility="collapsed")
                    if new_val != blk['title']: blk['title'] = new_val
                new_c = st.text_area("内容源码", value=blk.get("content", ""), key=f"blk_content_{blk['id']}", placeholder="在此输入章说明源码（可选）", label_visibility="collapsed")
                if new_c != blk.get("content", ""): blk["content"] = new_c
            elif blk["type"] == "section":
                sec_counter += 1
                subsec_counter = 0
                # 允许动态修改章节标题
                col_l, col_r = st.columns([1.5, 3.5])
                with col_l:
                    if theme_name == "讲义类模板":
                        st.markdown(f"<div style='padding-top:8px; color: var(--mc-accent); white-space:nowrap;'><b>🗂️ 第{chap_counter}.{sec_counter}节标题</b></div>", unsafe_allow_html=True)
                    else:
                        st.markdown(f"<div style='padding-top:8px; white-space:nowrap;'><b>🗂️ 第{sec_counter}章标题</b></div>", unsafe_allow_html=True)
                with col_r:
                    new_val = st.text_input("节/章标题", value=blk['title'], key=f"blk_title_{blk['id']}", label_visibility="collapsed")
                    if new_val != blk['title']: blk['title'] = new_val
                new_c = st.text_area("内容源码", value=blk.get("content", ""), key=f"blk_content_{blk['id']}", placeholder="在此输入节/章说明源码（可选）", label_visibility="collapsed")
                if new_c != blk.get("content", ""): blk["content"] = new_c
            elif blk["type"] == "subsection":
                subsec_counter += 1
                # 允许动态修改小节标题
                col_l, col_r = st.columns([1.5, 3.5])
                with col_l:
                    if theme_name == "讲义类模板":
                        st.markdown(f"<div style='padding-top:8px; color: var(--mc-muted); white-space:nowrap;'><b>📝 第{chap_counter}.{sec_counter}.{subsec_counter}小节标题</b></div>", unsafe_allow_html=True)
                    else:
                        st.markdown(f"<div style='padding-top:8px; color: var(--mc-muted); white-space:nowrap;'><b>📝 第{sec_counter}.{subsec_counter}小节标题</b></div>", unsafe_allow_html=True)
                with col_r:
                    new_val = st.text_input("小节标题", value=blk['title'], key=f"blk_title_{blk['id']}", label_visibility="collapsed")
                    if new_val != blk['title']: blk['title'] = new_val
                new_c = st.text_area("内容源码", value=blk.get("content", ""), key=f"blk_content_{blk['id']}", placeholder="在此输入小节说明源码（可选）", label_visibility="collapsed")
                if new_c != blk.get("content", ""): blk["content"] = new_c
            else:
                name = os.path.basename(blk['path']).replace('.tex', '')
                st.markdown(f"<h5 style='color: var(--mc-text); margin-top: 0;'>📄 {name}</h5>", unsafe_allow_html=True)
                
            # 按钮栏放在标题下方
            c_up, c_down, c_del = st.columns(3)
            with c_up:
                if st.button("⬆️", key=f"blk_up_{blk['id']}", disabled=(i==0), help="上移", use_container_width=True):
                    blocks[i], blocks[i-1] = blocks[i-1], blocks[i]
                    st.rerun()
            with c_down:
                if st.button("⬇️", key=f"blk_down_{blk['id']}", disabled=(i==len(blocks)-1), help="下移", use_container_width=True):
                    blocks[i], blocks[i+1] = blocks[i+1], blocks[i]
                    st.rerun()
            with c_del:
                if st.button("❌", key=f"blk_del_{blk['id']}", help="移除", use_container_width=True):
                    removed = blocks.pop(i)
                    if removed["type"] == "question" and removed["path"] in st.session_state["exam_selected_qs"]:
                        st.session_state["exam_selected_qs"].remove(removed["path"])
                    st.rerun()
                    
        with c_right:
            # 右侧渲染内容区
            if blk["type"] == "chapter":
                st.markdown(f"<h2 style='color: var(--mc-accent-dark); margin: 0;'>{blk['title']}</h2>", unsafe_allow_html=True)
                if blk.get("content"):
                    st.markdown(f"<div style='margin-top: 10px;'>{blk['content']}</div>", unsafe_allow_html=True)
            elif blk["type"] == "section":
                st.markdown(f"<h3 style='color: var(--mc-accent); margin: 0;'>{blk['title']}</h3>", unsafe_allow_html=True)
                if blk.get("content"):
                    st.markdown(f"<div style='margin-top: 10px;'>{blk['content']}</div>", unsafe_allow_html=True)
            elif blk["type"] == "subsection":
                st.markdown(f"<h4 style='color: var(--mc-muted); border-left: 4px solid var(--mc-accent); padding-left: 10px; margin: 0;'>{blk['title']}</h4>", unsafe_allow_html=True)
                if blk.get("content"):
                    st.markdown(f"<div style='margin-top: 10px;'>{blk['content']}</div>", unsafe_allow_html=True)
            else:
                if os.path.exists(blk["path"]):
                    with open(blk["path"], "r", encoding="utf-8") as f:
                        content = f.read()
                    try:
                        header_fields = extract_problem_header_fields(content) or {}
                        header_year = header_fields.get("year", "")
                        header_paper = header_fields.get("paper", "")
                        header_number = header_fields.get("number", "")
                        if header_year and header_paper and header_number:
                            workbench_title = f"\u3010{header_year} {header_paper}\uff0c{header_number}\u3011"
                        else:
                            workbench_title = format_question_title(os.path.basename(blk["path"]))
                        st.markdown(
                            f'<div style="font-weight:700; margin-bottom:0.45rem;">{q_counter}. {html.escape(workbench_title)}</div>',
                            unsafe_allow_html=True,
                        )
                        md_content = latex_to_markdown(content, show_title=False)
                        st.markdown(md_content, unsafe_allow_html=True)
                        q_counter += 1
                    except Exception as e:
                        st.error(f"渲染出错: {e}")
                else:
                    st.error(f"文件不存在: {blk['path']}")
                    
        st.divider()

# ================= 题目查重与操作日志 =================
def render_duplicate_check_page():
    st.header("题目查重")
    st.caption("按题干内容检查完全重复和高度相似题目。查重只提供核对建议，不会自动删除任何题目。")

    c_threshold, c_limit = st.columns([1, 1], gap="small")
    with c_threshold:
        threshold = st.slider("相似度阈值", min_value=0.80, max_value=0.99, value=0.88, step=0.01)
    with c_limit:
        max_pairs = st.number_input("最多显示组合数", min_value=20, max_value=500, value=200, step=20)

    if st.button("开始扫描题库", key="duplicate_scan_start", type="primary", use_container_width=True):
        with st.spinner("正在扫描题目内容，请稍候..."):
            results = scan_duplicate_pairs(
                rows=_csv_index_cached(_csv_index_cache_token()),
                similarity_threshold=threshold,
                max_pairs=int(max_pairs),
            )
        st.session_state["duplicate_scan_results"] = results
        st.session_state["duplicate_scan_threshold"] = threshold
        record_operation(
            "scan_duplicates",
            details=f"threshold={threshold:.2f}, pairs={len(results)}",
        )

    results = st.session_state.get("duplicate_scan_results") or []
    if not results:
        st.info("暂未发现重复组合，或尚未开始扫描。")
        return

    exact_count = sum(1 for item in results if item.get("kind") == "exact")
    similar_count = len(results) - exact_count
    st.success(f"共发现 {len(results)} 组候选：完全重复 {exact_count} 组，高度相似 {similar_count} 组。")

    for index, pair in enumerate(results, start=1):
        left = pair.get("left") or {}
        right = pair.get("right") or {}
        kind_label = "完全重复" if pair.get("kind") == "exact" else "高度相似"
        score_text = f"{float(pair.get('score', 0)) * 100:.1f}%"
        title = f"{index}. {kind_label} · {score_text}"
        with st.expander(title, expanded=index == 1):
            c_left, c_right = st.columns(2, gap="large")
            for container, item in ((c_left, left), (c_right, right)):
                with container:
                    st.markdown(f"**{html.escape(item.get('name') or '未命名题目')}**", unsafe_allow_html=True)
                    st.caption(
                        f"ID：{item.get('question_id') or '无'} · "
                        f"板块：{item.get('subject') or '未分类'}"
                    )
                    st.code(item.get("relative_path") or item.get("path") or "路径不可用", language="text")
                    path = item.get("path") or ""
                    if path and os.path.isfile(path):
                        try:
                            with open(path, "r", encoding="utf-8") as question_file:
                                render_question_preview(question_file.read())
                        except Exception as exc:
                            st.warning(f"题目预览失败：{exc}")
                    else:
                        st.warning("题目文件不存在，建议先重建题库索引。")

    st.caption("处理建议：保留信息更完整、来源更可靠的题目；确认后再通过现有删除/编辑功能处理另一份。")


def render_operation_log_panel():
    with st.expander("最近操作日志", expanded=False):
        operations = read_recent_operations(limit=100)
        if not operations:
            st.info("暂时没有题目操作日志。")
            return

        action_labels = {
            "create_question": "创建题目",
            "update_question_content": "修改题目内容",
            "update_question_meta": "修改题目属性",
            "rename_question": "重命名题目",
            "delete_question": "删除题目",
            "restore_question": "恢复题目",
            "permanently_delete_backup": "永久删除备份",
            "scan_duplicates": "题目查重",
            "rebuild_csv_index": "重建题库索引",
            "update_chapter_index": "更新章节索引",
        }
        display_rows = []
        for operation in operations:
            display_rows.append({
                "时间": operation.get("created_at", ""),
                "操作": action_labels.get(operation.get("action"), operation.get("action", "")),
                "状态": operation.get("status", ""),
                "题目 ID": operation.get("question_id", ""),
                "路径": operation.get("path", ""),
                "说明": operation.get("details", ""),
            })
        st.dataframe(display_rows, use_container_width=True, hide_index=True)


# ================= 页面：工具箱 =================
def page_tools():
    st.header("🛠️ 工具箱")

    if st.session_state.get("tools_subpage") == "tag_edit":
        if st.button("⬅️ 返回工具箱", type="secondary"):
            st.session_state["tools_subpage"] = None
            st.rerun()
        page_tag_edit()
        return
    if st.session_state.get("tools_subpage") == "delete_questions":
        page_browse(is_delete_mode=True)
        return
    if st.session_state.get("tools_subpage") == "cloze_generator":
        if st.button("⬅️ 返回工具箱", type="secondary"):
            st.session_state["tools_subpage"] = None
            st.rerun()
        page_entry(force_single_mode=True, cloze_mode=True)
        return
    if st.session_state.get("tools_subpage") == "cloze_library":
        if st.button("⬅️ 返回工具箱", type="secondary"):
            st.session_state["tools_subpage"] = "cloze_generator"
            st.session_state["adv_search_active"] = False
            _clear_advanced_search_result_cache()
            st.rerun()
        page_browse(paper_type_scope="WK", page_title="🧩 挖空题库")
        return
    if st.session_state.get("tools_subpage") == "duplicate_check":
        if st.button("返回工具箱", type="secondary"):
            st.session_state["tools_subpage"] = None
            st.rerun()
        render_duplicate_check_page()
        return
    
    st.markdown("""
    <style>
    /* 工具卡片网格布局：一行三个 */
    .tool-grid {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 12px;
        margin-top: 4px;
    }
    
    /* 单个工具卡片样式 */
    .tool-card {
        background-color: var(--mc-surface);
        border: 1px solid var(--mc-border);
        border-radius: 10px;
        padding: 15px 17px;
        box-shadow: var(--mc-shadow);
        transition: transform 0.2s ease, box-shadow 0.2s ease;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        min-height: 240px; /* 确保同一行的卡片高度一致 */
    }
    .tool-card:hover {
        transform: translateY(-4px);
        box-shadow: 0 8px 15px rgba(0,0,0,0.08);
        border-color: var(--mc-accent); /* 悬浮时边框变紫 */
    }
    
    /* 工具卡片标题 */
    .tool-title {
        font-size: 18px;
        font-weight: 700;
        color: var(--mc-text);
        margin-bottom: 10px;
        display: flex;
        align-items: center;
        gap: 8px;
    }
    
    /* 工具卡片描述文本 */
    .tool-desc {
        font-size: 14px;
        color: var(--mc-muted);
        line-height: 1.5;
        margin-bottom: 20px;
        flex-grow: 1; /* 让描述部分占据剩余空间，把按钮推到最底 */
    }
    
    /* 强力覆盖 Streamlit 按钮在工具卡片内的样式 */
    .tool-card div[data-testid="stButton"] > button {
        width: 100% !important;
        border-radius: 6px !important;
        font-weight: 600 !important;
    }
    
    /* 如果描述带有高亮底色 (例如第一个工具) */
    .tool-desc-highlight {
        background-color: var(--mc-surface-soft);
        border: 1px solid var(--mc-border);
        padding: 10px;
        border-radius: 4px;
        margin-bottom: 20px;
        font-size: 13px;
        color: var(--mc-text);
        flex-grow: 1;
    }
    </style>
    """, unsafe_allow_html=True)
    
    # 开启网格布局容器
    st.markdown('<div class="tool-grid">', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True) 
    
    # 第一行 3 个工具
    r1_c1, r1_c2, r1_c3 = st.columns([1, 1, 1])
    
    with r1_c1:
        st.markdown("""
        <div class="tool-card">
            <div class="tool-title">🗄️ 1. 数据库维护</div>
            <div class="tool-desc-highlight">
                如果您手动删除、外部复制等变动，导致与 CSV 索引不一致，或者统计数据异常，可以点击下方按钮进行一键重建。该操作会保留现有题目的 ID，并自动追加新题或删除不存在的死链接。
            </div>
        </div>
        """, unsafe_allow_html=True)
        # 负 margin 把按钮拉进卡片里
        st.markdown("<style>div:has(> button[key='btn_rebuild_db']) { margin-top: -65px; padding: 0 20px; position: relative; z-index: 10; }</style>", unsafe_allow_html=True)
        if st.button("🔄 一键重建/同步题库索引", key="btn_rebuild_db", use_container_width=True):
            with st.spinner("正在扫描所有目录并重建 CSV 索引..."):
                try:
                    init_script = os.path.join(BASE_DIR, "utils", "init_csv_index.py")
                    subprocess.run(
                        [sys.executable, init_script],
                        cwd=BASE_DIR,
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    clear_statistics_cache()
                    st.success("题库索引重建成功！")
                    record_operation("rebuild_csv_index", details="manual rebuild from chapters")
                    st.toast("题库索引同步完成！", icon="✅")
                    time.sleep(1)
                    st.rerun()
                except subprocess.CalledProcessError as e:
                    st.error(f"同步失败：\n{e.stderr}")
                except Exception as e:
                    st.error(f"发生错误：{str(e)}")
            
    with r1_c2:
        st.markdown("""
        <div class="tool-card">
            <div class="tool-title">📑 2. 更新板块题目索引</div>
            <div class="tool-desc">
                调用本地的脚本，自动扫描 chapters 目录下的所有题目，并为每个板块重新生成最新的 <code>content_*.tex</code> 索引文件。供主文件 <code>main.tex</code> 调用编译。
                <br><br><i>(当您新增、删除或重命名了题目文件后，请执行此操作以确保主文件目录同步)</i>
            </div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("<style>div:has(> button[key='btn_update_idx']) { margin-top: -65px; padding: 0 20px; position: relative; z-index: 10; }</style>", unsafe_allow_html=True)
        if st.button("⚡ 执行更新章节索引", key="btn_update_idx", use_container_width=True):
            with st.spinner("正在运行更新脚本..."):
                try:
                    if BASE_DIR not in sys.path:
                        sys.path.append(BASE_DIR)
                    import utils.batch_gen as batch_gen
                    batch_gen.update_chapter_contents()
                    st.success("章节索引更新完成！")
                    record_operation("update_chapter_index", details="manual chapter index update")
                except Exception as e:
                    st.error(f"执行失败: {e}")

    with r1_c3:
        st.markdown("""
        <div class="tool-card">
            <div class="tool-title">🎨 3. 提取并分离 TikZ 绘图</div>
            <div class="tool-desc">
                扫描题库中所有现存的 <code>.tex</code> 文件。如果发现未被分离的 <code>\\begin{tikzpicture} ... \\end{tikzpicture}</code> 代码，将会自动将其剥离到同级目录下的 <code>相关图</code> 文件夹中生成副本，同时在主文件中保留内联 TikZ 源码。
            </div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("<style>div:has(> button[key='btn_extract_tikz']) { margin-top: -65px; padding: 0 20px; position: relative; z-index: 10; }</style>", unsafe_allow_html=True)
        if st.button("✂️ 执行全库 TikZ 剥离", key="btn_extract_tikz", use_container_width=True):
            updated_files = batch_extract_tikz_all()
            if updated_files:
                st.success(f"操作完成，共处理了 {len(updated_files)} 个文件。")
                with st.expander("查看更新的文件名单", expanded=True):
                    for f in updated_files: st.write(f"- {f}")
            else:
                st.info("未发现需要处理的文件。")

    st.markdown("<br>", unsafe_allow_html=True)
    
    # 第二行 3 个工具
    r2_c1, r2_c2, r2_c3 = st.columns([1, 1, 1])
    
    with r2_c1:
        st.markdown("""
        <div class="tool-card">
            <div class="tool-title">✅ 4. 纠正选择题选项格式</div>
            <div class="tool-desc">
                扫描题库中所有现存的 <code>.tex</code> 文件。如果发现形如 <code>A. xxx B. xxx C. xxx D. xxx</code> 的非标准选择题格式，将自动尝试提取选项内容，并用规范的 <code>\\begin{choices} ... \\end{choices}</code> 指令进行替换。
            </div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("<style>div:has(> button[key='btn_fix_choices']) { margin-top: -65px; padding: 0 20px; position: relative; z-index: 10; }</style>", unsafe_allow_html=True)
        if st.button("🔧 执行全库选择题格式纠正", key="btn_fix_choices", use_container_width=True):
            updated_files = batch_fix_choice_formats()
            if updated_files:
                st.success(f"操作完成，共修复了 {len(updated_files)} 个文件。")
                with st.expander("查看已修复的文件名单", expanded=True):
                    for f in updated_files: st.write(f"- {f}")
            else:
                st.info("未发现需要修复的选择题格式文件。")

    with r2_c2:
        st.markdown("""
        <div class="tool-card">
            <div class="tool-title">🏷️ 5. 标签与属性修改</div>
            <div class="tool-desc">
                查找并修改某一道题或某一整套试卷的元数据：年份、试卷类别、试卷名称、题号、知识板块，并同步更新文件名与 CSV 索引。
            </div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("<style>div:has(> button[key='btn_tools_tag_edit']) { margin-top: -65px; padding: 0 20px; position: relative; z-index: 10; }</style>", unsafe_allow_html=True)
        if st.button("进入标签与属性修改", key="btn_tools_tag_edit", use_container_width=True):
            st.session_state["tools_subpage"] = "tag_edit"
            st.rerun()

    with r2_c3:
        st.markdown("""
        <div class="tool-card">
            <div class="tool-title">🗑️ 6. 删除题库问题</div>
            <div class="tool-desc">
                进入专用删除模式，沿用全局浏览和三级查找定位题目。删除时会先把题目文件及同名相关图备份到 <code>.backups</code>，再从题库目录移除，并同步 CSV 索引和章节索引；误删可在删除模式右上角“恢复误删题目”恢复本次删除记录，也可点“管理备份问题”查找历史备份。当前备份不会自动定期清理。
            </div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("<style>div:has(> button[key='btn_tools_delete_questions']) { margin-top: -65px; padding: 0 20px; position: relative; z-index: 10; }</style>", unsafe_allow_html=True)
        if st.button("开始删除选定问题", key="btn_tools_delete_questions", use_container_width=True):
            st.session_state["tools_subpage"] = "delete_questions"
            st.session_state["adv_search_active"] = False
            _clear_advanced_search_result_cache()
            st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)

    # 第三行工具
    r3_c1, r3_c2, r3_c3 = st.columns([1, 1, 1])

    with r3_c1:
        st.markdown("""
        <div class="tool-card">
            <div class="tool-title">🧩 7. 挖空题生成</div>
            <div class="tool-desc">
                上传或粘贴题目与解答，按预设要求生成挖空题。当前先沿用单题录入界面，支持实时预览、保存题目，并可导出当前挖空题图片；具体 AI 提示词与挖空生成策略后续补充。
            </div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("<style>div:has(> button[key='btn_tools_cloze_generator']) { margin-top: -65px; padding: 0 20px; position: relative; z-index: 10; }</style>", unsafe_allow_html=True)
        if st.button("进入挖空题生成界面", key="btn_tools_cloze_generator", use_container_width=True):
            st.session_state["tools_subpage"] = "cloze_generator"
            st.session_state["cloze_image_result"] = None
            st.session_state.setdefault("cloze_auto_solve", True)
            st.rerun()

    with r3_c2:
        try:
            semantic_status = semantic_index_status()
            semantic_status_error = ""
        except SemanticSearchError as exc:
            semantic_status = {"count": 0, "model_name": ""}
            semantic_status_error = str(exc)
        status_text = "未建立"
        if semantic_status.get("count"):
            status_text = f"{semantic_status['count']} 道题"
        st.markdown("""
        <div class="tool-card">
            <div class="tool-title">🧠 8. 语义搜索索引</div>
            <div class="tool-desc">
                使用可配置的 embedding 模型建立题干语义索引，供高级搜索中的“混合搜索”和“语义搜索”使用。索引是可重建的派生数据，不会修改题目文件或 CSV。
            </div>
        </div>
        """, unsafe_allow_html=True)
        st.caption(f"当前状态：{status_text} · 模型：{semantic_status.get('model_name') or '未配置'}")
        if semantic_status_error:
            st.warning(f"索引状态读取失败：{semantic_status_error}")
        force_rebuild = st.checkbox("强制重新生成已有向量", key="semantic_force_rebuild")
        st.markdown("<style>div:has(> button[key='btn_rebuild_semantic']) { margin-top: -24px; padding: 0 20px; position: relative; z-index: 10; }</style>", unsafe_allow_html=True)
        if st.button("🔄 更新语义索引", key="btn_rebuild_semantic", use_container_width=True):
            config = _semantic_api_config()
            progress_bar = st.progress(0)

            def _semantic_progress(done, total):
                progress_bar.progress(1.0 if total <= 0 else min(1.0, done / total))

            try:
                rows = _csv_index_cached(_csv_index_cache_token())
                with st.spinner("正在生成题目 embedding，请稍候..."):
                    result = build_semantic_index(
                        rows,
                        config["base_url"],
                        config["api_key"],
                        config["model_name"],
                        force=force_rebuild,
                        progress=_semantic_progress,
                    )
                progress_bar.empty()
                _clear_advanced_search_result_cache()
                st.success(f"语义索引已更新：处理 {result['updated']} 道，当前共 {result['total']} 道。")
            except SemanticSearchError as exc:
                progress_bar.empty()
                st.error(str(exc))
            except Exception as exc:
                progress_bar.empty()
                st.error(f"语义索引更新失败：{exc}")

    with r3_c3:
        st.markdown("""
        <div class="tool-card">
            <div class="tool-title">🔎 9. 搜索模式说明</div>
            <div class="tool-desc">
                精确筛选适合题号、年份、题型等明确条件；混合搜索在语义相关度基础上优先展示关键词命中的题目；语义搜索适合用自然语言描述考点。
            </div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    r4_c1, r4_c2, r4_c3 = st.columns([1, 1, 1])
    with r4_c1:
        st.markdown("""
        <div class="tool-card">
            <div class="tool-title">🔎 10. 题目查重</div>
            <div class="tool-desc">
                按题干内容扫描题库，识别完全重复和高度相似的题目，提供题目 ID、来源路径和并排预览，方便人工确认后处理。
            </div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("<style>div:has(> button[key='btn_tools_duplicate_check']) { margin-top: -65px; padding: 0 20px; position: relative; z-index: 10; }</style>", unsafe_allow_html=True)
        if st.button("进入题目查重", key="btn_tools_duplicate_check", use_container_width=True):
            st.session_state["tools_subpage"] = "duplicate_check"
            st.session_state.pop("duplicate_scan_results", None)
            st.rerun()

    with r4_c2:
        st.markdown("""
        <div class="tool-card">
            <div class="tool-title">🧾 11. 操作记录</div>
            <div class="tool-desc">
                查看题目创建、编辑、重命名、删除、恢复和查重记录，便于追踪最近的数据变更。
            </div>
        </div>
        """, unsafe_allow_html=True)

    render_operation_log_panel()


def batch_fix_choice_formats():
    import re
    updated_files = []
    
    for root, dirs, files in os.walk(CHAPTERS_DIR):
        for file in files:
            if not file.endswith(".tex"): continue
            if file.startswith("content_"): continue
            if " 相关图" in root or " 图" in file: continue
            
            file_path = os.path.join(root, file)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # 寻找 A. B. C. D. 模式 (支持全半角和换行)
                pattern = r'(?:A|Ａ)[\.．]\s*(.*?)\s*(?:B|Ｂ)[\.．]\s*(.*?)\s*(?:C|Ｃ)[\.．]\s*(.*?)\s*(?:D|Ｄ)[\.．]\s*(.*?)(?=\\end\{problem\}|\\begin\{solutions?\}|$)'
                
                def replace_choices(match):
                    opt_a = match.group(1).strip()
                    opt_b = match.group(2).strip()
                    opt_c = match.group(3).strip()
                    opt_d = match.group(4).strip()
                    
                    # 移除选项末尾可能多余的 \quad, \qquad 和 \\ 等
                    def clean_opt(opt):
                        opt = re.sub(r'\\quad\s*$', '', opt).strip()
                        opt = re.sub(r'\\qquad\s*$', '', opt).strip()
                        opt = re.sub(r'\\\\$', '', opt).strip() # 去除换行符 \\
                        return opt
                        
                    opt_a = clean_opt(opt_a)
                    opt_b = clean_opt(opt_b)
                    opt_c = clean_opt(opt_c)
                    opt_d = clean_opt(opt_d)
                    
                    return f"\n\\begin{{choices}}\n\\choice{{{{{opt_a}}}}}\n\\choice{{{{{opt_b}}}}}\n\\choice{{{{{opt_c}}}}}\n\\choice{{{{{opt_d}}}}}\n\\end{{choices}}\n"
                
                new_content, count = re.subn(pattern, replace_choices, content, flags=re.DOTALL)
                
                # 检查 \begin{choices} 前面是否有 (\hspace{1cm})
                if r'\begin{choices}' in new_content:
                    parts = new_content.split(r'\begin{choices}')
                    for i in range(len(parts) - 1):
                        prefix = parts[i]
                        stripped_prefix = prefix.rstrip()
                        
                        # 检查是否已经有 (\hspace{1cm}) 或者类似的占位符 (支持全角半角括号和空格)
                        has_hspace = re.search(r'[\(（]\s*\\hspace\{1cm\}\s*[\)）]$', stripped_prefix)
                        
                        if not has_hspace:
                            # 检查是否有空的括号 () 或 （），有的话直接替换掉
                            if stripped_prefix.endswith('()') or stripped_prefix.endswith('（）'):
                                stripped_prefix = stripped_prefix[:-2] + r'(\hspace{1cm})'
                            else:
                                stripped_prefix += r' (\hspace{1cm})'
                                
                        parts[i] = stripped_prefix + '\n'
                        
                    new_content = r'\begin{choices}'.join(parts)
                
                if new_content != content:
                    atomic_write_text(file_path, new_content, backup=True)
                    updated_files.append(file)
            except Exception as e:
                print(f"Error processing {file_path}: {e}")
                
    return updated_files

def batch_extract_tikz_all():
    updated_files = []
    for root, dirs, files in os.walk(CHAPTERS_DIR):
        for file in files:
            if not file.endswith(".tex"): continue
            # 跳过已经被提取出来的图文件
            if " 图" in file and " 相关图" in root: continue
            
            file_path = os.path.join(root, file)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # 如果包含原生的 tikzpicture 才需要处理
                if r'\begin{tikzpicture}' in content:
                    save_dir = root
                    filename = file
                    # 复用核心抽取函数
                    new_content = extract_and_replace_tikz(content, filename, save_dir)
                    if new_content != content:
                        atomic_write_text(file_path, new_content, backup=True)
                        # 触发一次预渲染生成PNG
                        latex_to_markdown(new_content)
                        updated_files.append(file_path)
            except Exception as e:
                print(f"Error processing {file_path}: {e}")
                
    return updated_files

def add_blank_lines_to_all():
    count = 0
    for root, dirs, files in os.walk(CHAPTERS_DIR):
        for file in files:
            if not file.endswith(".tex"): continue
            
            file_path = os.path.join(root, file)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # 使用简单的正则或字符串处理
                # 这里复用之前的逻辑：查找 \begin{problem}... 到 \end{problem}
                # 简单起见，我们假设文件就是标准的 problem 结构
                
                lines = content.split('\n')
                new_lines = []
                in_problem = False
                modified = False
                
                for i, line in enumerate(lines):
                    if "\\begin{problem}" in line:
                        in_problem = True
                        new_lines.append(line)
                        continue
                    if "\\end{problem}" in line:
                        in_problem = False
                        new_lines.append(line)
                        continue
                        
                    if in_problem:
                        # 如果当前行不空，且上一行不空，且不是环境开始，则加空行
                        # 但要小心不要破坏数学公式块 $ ... $
                        # 这是一个简化的处理，主要针对文本段落
                        
                        # 简单策略：如果当前行是非空文本，且上一行也是非空文本，插入空行
                        # 但为了安全，我们只处理显式的中文段落结尾？
                        # 或者复用之前的逻辑：每行后面加一个空行，如果已经有空行则不加
                        
                        # 更稳健的策略：读取内容，如果发现没有空行分隔的段落，则插入
                        # 这里我们采用保守策略：如果当前行有内容，且下一行也有内容，中间插入空行
                        # 并不容易完美自动化。
                        # 让我们回退到最安全的方式：不做复杂语法分析，仅提示用户
                        # 或者，只处理显式的文字段落。
                        
                        # 实际上，之前的 update_doc.py 逻辑比较复杂。
                        # 在这里，我们实现一个简化版本：确保 \end{problem} 前有一行空行，
                        # 以及 \begin{problem} 后有一行空行（如果不为空的话）。
                        # 真正的段落间空行最好人工确认。
                        
                        # 重新考虑：用户之前的需求是“分行加空行”。
                        # 我们可以简单地将非空行之间插入空行。
                        
                        stripped = line.strip()
                        if stripped:
                            new_lines.append(line)
                            # 如果下一行不是空行，也不是 end problem，则添加空行
                            if i + 1 < len(lines):
                                next_line = lines[i+1].strip()
                                if next_line and "\\end{problem}" not in next_line:
                                    new_lines.append("") # 插入空行
                                    modified = True
                        else:
                            new_lines.append(line)
                    else:
                        new_lines.append(line)
                
                if modified:
                    new_content = "\n".join(new_lines)
                    if new_content != content:
                        atomic_write_text(file_path, new_content, backup=True)
                        count += 1
            except Exception as e:
                print(f"Error processing {file}: {e}")
                
    return count


def standardize_national_papers():
    # 这里集成之前的重命名逻辑
    count = 0
    local_keywords = [
        "北京", "上海", "天津", "重庆", "浙江", "江苏", "江西", "山东", 
        "湖北", "湖南", "广东", "福建", "辽宁", "吉林", "黑龙江", 
        "河北", "河南", "山西", "陕西", "四川", "云南", "贵州", 
        "安徽", "广西", "海南", "内蒙古", "西藏", "青海", "宁夏", 
        "新疆", "甘肃", "港", "澳", "台"
    ]
    
    for root, dirs, files in os.walk(CHAPTERS_DIR):
        for file in files:
            if not file.endswith(".tex"): continue
            
            parts = file[:-4].split('-')
            if len(parts) != 5: continue
            
            year_str, type_str, paper_name, number, subject = parts
            try:
                year = int(year_str)
            except:
                continue
                
            # 过滤地方卷和甲乙卷
            is_local = any(kw in paper_name for kw in local_keywords)
            if is_local or "甲卷" in paper_name or "乙卷" in paper_name:
                continue
                
            new_paper_name = paper_name
            # 规则匹配
            if 2020 <= year <= 2022:
                if "新课标" in new_paper_name: new_paper_name = new_paper_name.replace("新课标", "新高考")
                if "新高考全国" in new_paper_name: new_paper_name = new_paper_name.replace("新高考全国", "新高考")
            elif 2023 <= year <= 2025:
                if "新高考" in new_paper_name: new_paper_name = new_paper_name.replace("新高考", "新课标")
                if "新课标全国" in new_paper_name: new_paper_name = new_paper_name.replace("新课标全国", "新课标")
            
            if new_paper_name != paper_name:
                # 重命名文件
                new_filename = f"{year_str}-{type_str}-{new_paper_name}-{number}-{subject}.tex"
                old_path = os.path.join(root, file)
                new_path = os.path.join(root, new_filename)
                
                # 更新内容中的标签
                try:
                    with open(old_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    # 替换 {paper_name} 为 {new_paper_name}
                    # 简单的字符串替换可能误伤，使用比较精确的替换
                    old_tag = f"{{{paper_name}}}"
                    new_tag = f"{{{new_paper_name}}}"
                    content = content.replace(old_tag, new_tag, 1) # 只替换第一个匹配（通常是标签）
                    
                    atomic_write_text(old_path, content, backup=True)
                        
                    os.rename(old_path, new_path)
                    st.write(f"已重命名: {file} -> {new_filename}")
                    count += 1
                except Exception as e:
                    st.error(f"处理 {file} 时出错: {e}")
    return count

# ================= 页面：标签与属性修改 (含搜索) =================
def page_tag_edit():
    st.header("🏷️ 标签与属性修改")
    st.info("在此模式下，您可以修改题目的元数据（年份、试卷名、题号、板块）以及文件名。")
    
    # Session state for selected file in this tab
    if "tag_edit_file" not in st.session_state:
        st.session_state["tag_edit_file"] = None

    # 定义匹配函数 (局部使用)
    # def is_match(path, s_type, s_query): ... (Use global check_search_match instead)
        
    c_left, c_right = st.columns([1, 1.5])  # 调整比例，使右侧搜索栏宽度缩小
    
    with c_left:
        st.markdown("""
        <style>
        #tag-edit-dir-label {
            font-size: 16px;
            font-weight: 700;
            margin: 0.35rem 0 0.15rem 0;
        }
        #tag-edit-dir-box div[data-testid="stSelectbox"] div[data-baseweb="select"] * {
            font-size: 16px !important;
            font-weight: 700 !important;
        }
        </style>
        """, unsafe_allow_html=True)
        st.markdown('<div id="tag-edit-dir-box"></div>', unsafe_allow_html=True)
        st.subheader("📂 目录选择")
        csv_rows = []
        all_years = get_all_years_globally()
        st.markdown('<div id="tag-edit-dir-label">年份</div>', unsafe_allow_html=True)
        year = st.selectbox("年份", options=all_years, key="te_year", label_visibility="collapsed")

        type_opts = ["全部试卷类别"] + _editable_paper_type_options()
        st.markdown('<div id="tag-edit-dir-label">试卷类别筛选</div>', unsafe_allow_html=True)
        ptype_filter = st.selectbox("试卷类别筛选", options=type_opts, key="te_ptype_filter", label_visibility="collapsed", format_func=lambda x: x if x == "全部试卷类别" else f"{x} ({PAPER_TYPES.get(x, '')})")

        paper_name = None
        if year:
            csv_token = _csv_index_cache_token()
            try:
                csv_rows = _csv_index_cached(csv_token)
            except Exception:
                from utils.csv_ops import read_csv_index
                csv_rows = _filter_question_rows(read_csv_index())

            papers_set = set()
            for row in csv_rows:
                if (row.get("年份", "") or "").strip() != str(year):
                    continue
                if ptype_filter != "全部试卷类别" and (row.get("试卷类型", "") or "").strip() != ptype_filter:
                    continue
                pname = (row.get("试卷名称", "") or "").strip()
                if pname:
                    papers_set.add(pname)
            papers = sorted(papers_set) if papers_set else get_papers_by_year(year)
            if papers:
                st.markdown('<div id="tag-edit-dir-label">试卷</div>', unsafe_allow_html=True)
                paper_name = st.selectbox("试卷", options=papers, key="te_paper", label_visibility="collapsed")
        
        if year and paper_name:
            questions = get_questions_by_paper(year, paper_name)
            if questions:
                by_path = {}
                for row in csv_rows if year else []:
                    relp = (row.get("相对文件路径", "") or "").strip()
                    if not relp:
                        continue
                    by_path[os.path.join(CHAPTERS_DIR, relp)] = row

                filtered_questions = []
                for q in questions:
                    qpath = q.get("path")
                    r = by_path.get(qpath)
                    if ptype_filter != "全部试卷类别":
                        if not r or (r.get("试卷类型", "") or "").strip() != ptype_filter:
                            continue
                    filtered_questions.append(q)

                questions = filtered_questions
                q_options = ["（所有题目：本试卷）"] + [f"第{q['file'].split('-')[3]}题 ({q['subject']})" for q in questions]
                st.markdown('<div id="tag-edit-dir-label">题目</div>', unsafe_allow_html=True)
                sel_idx = st.selectbox("题目", range(len(q_options)), format_func=lambda i: q_options[i], key="te_q_select", label_visibility="collapsed")
                
                if st.button("⬇️ 加载选中题目", key="btn_load_hierarchy", use_container_width=True):
                    if sel_idx == 0:
                        st.session_state["tag_edit_file"] = None
                        st.session_state["tag_edit_bulk_paths"] = [q.get("path") for q in questions if q.get("path")]
                        st.session_state["tag_edit_bulk_meta"] = {"year": str(year), "paper": str(paper_name)}
                    else:
                        st.session_state["tag_edit_bulk_paths"] = []
                        st.session_state["tag_edit_bulk_meta"] = None
                        st.session_state["tag_edit_file"] = questions[sel_idx - 1]["path"]
                    st.rerun()

    with c_right:
        st.subheader("🔍 搜索选择")
        search_opts = ["全文内容", "题目类型", "题目内容", "解答内容", "难度星级", "其他自由标签", "知识点", "物理方法", "备注"]
        # 因为需要级联更新 UI（selectbox -> text_input/selectbox），不能将包含动态类型的输入框直接放进 form
        # 我们改用普通的容器，最后加一个搜索按钮
        c1a, c1b = st.columns([1, 2])
        with c1a: 
            t1 = st.selectbox("一级类型", search_opts, index=0, key="te_s_t1", label_visibility="collapsed")
        with c1b: 
            if t1 == "题目类型":
                q1 = st.selectbox("一级检索", QUESTION_TYPES, key="te_s_q1_sel", label_visibility="collapsed")
            else:
                q1 = st.text_input("一级检索", placeholder="一级关键词", key="te_s_q1", label_visibility="collapsed")
        
        # Level 2
        c2a, c2b = st.columns([1, 2])
        with c2a: 
            t2 = st.selectbox("二级类型", search_opts, index=0, key="te_s_t2", label_visibility="collapsed")
        with c2b: 
            if t2 == "题目类型":
                q2 = st.selectbox("二级检索", QUESTION_TYPES, key="te_s_q2_sel", label_visibility="collapsed")
            else:
                q2 = st.text_input("二级检索", placeholder="筛选词", key="te_s_q2", label_visibility="collapsed")
        
        # Level 3
        c3a, c3b = st.columns([1, 2])
        with c3a: 
            t3 = st.selectbox("三级类型", search_opts, index=0, key="te_s_t3", label_visibility="collapsed")
        with c3b: 
            if t3 == "题目类型":
                q3 = st.selectbox("三级检索", QUESTION_TYPES, key="te_s_q3_sel", label_visibility="collapsed")
            else:
                q3 = st.text_input("三级检索", placeholder="筛选词", key="te_s_q3", label_visibility="collapsed")
        
        submitted = st.button("🔍 搜索", type="primary", use_container_width=True)
             
        if submitted:
            st.session_state["te_search_active"] = True
            
        if st.session_state.get("te_search_active"):
            def _row_match(row, s_type, s_query):
                s_query = (s_query or "").strip()
                if not s_query:
                    return True
                if s_type == "题目类型":
                    return s_query == (row.get("题型", "") or "").strip()
                if s_type == "题目内容":
                    return s_query in (row.get("题干", "") or "")
                if s_type == "解答内容":
                    return s_query in (row.get("解析", "") or "")
                if s_type == "难度星级":
                    return s_query in (row.get("难度星级", "") or "")
                if s_type in ("标签", "其他自由标签"):
                    return s_query in (row.get("标签", "") or "")
                if s_type == "知识点":
                    return s_query in (row.get("知识点", "") or "")
                if s_type == "物理方法":
                    return s_query in (row.get("物理方法", "") or "")
                if s_type == "备注":
                    return s_query in (row.get("备注", "") or "")
                if s_type == "全文内容":
                    hay = "\n".join((row.get(field, "") or "") for field in ("题干", "答案", "解析", "标签", "知识点", "物理方法", "备注"))
                    return s_query in hay
                return False

            csv_token = _csv_index_cache_token()
            try:
                csv_rows = _csv_index_cached(csv_token)
            except Exception:
                from utils.csv_ops import read_csv_index
                csv_rows = _filter_question_rows(read_csv_index())

            results = []
            for row in csv_rows:
                if q1 and not _row_match(row, t1, q1):
                    continue
                if q2 and not _row_match(row, t2, q2):
                    continue
                if q3 and not _row_match(row, t3, q3):
                    continue
                relp = (row.get("相对文件路径", "") or "").strip()
                if not relp:
                    continue
                absp = os.path.join(CHAPTERS_DIR, relp)
                if not os.path.exists(absp):
                    continue
                fname = (row.get("文件名称", "") or "").strip()
                if fname and not fname.lower().endswith(".tex"):
                    fname = fname + ".tex"
                results.append({"file": fname or os.path.basename(absp), "path": absp})
            
            if results:
                st.success(f"找到 {len(results)} 个结果")
                res_options = [r["file"] for r in results]
                sel_res_idx = st.selectbox("选择搜索结果", range(len(results)), format_func=lambda i: res_options[i], key="te_res_select")
                
                if st.button("⬇️ 加载搜索结果", key="btn_load_search", use_container_width=True):
                    st.session_state["tag_edit_file"] = results[sel_res_idx]["path"]
                    st.rerun()
            else:
                st.warning("未找到匹配项")

    st.divider()
    
    # 编辑区域
    bulk_paths = st.session_state.get("tag_edit_bulk_paths") or []
    bulk_meta = st.session_state.get("tag_edit_bulk_meta") or {}
    if bulk_paths:
        st.subheader("🧩 批量修改（本试卷所有题目）")
        st.caption("仅修改：年份、试卷类别、试卷名称；题号与知识板块保持不变。")

        cur_year = (bulk_meta.get("year") or "").strip()
        cur_paper = (bulk_meta.get("paper") or "").strip()
        st.info(f"当前试卷：{cur_year} 年｜{cur_paper}｜共 {len(bulk_paths)} 题")

        type_opts = _editable_paper_type_options()
        with st.form("te_bulk_update_form"):
            new_year = st.text_input("统一年份", value=cur_year)
            new_type = st.selectbox("统一试卷类别", options=type_opts, format_func=lambda x: f"{x} ({PAPER_TYPES[x]})")
            new_name = st.text_input("统一试卷名称", value=cur_paper)
            submitted = st.form_submit_button("执行批量更新", type="primary")

        if submitted:
            ok, fail = 0, 0
            log_lines = []
            for old_path in bulk_paths:
                try:
                    if not old_path or not os.path.exists(old_path):
                        fail += 1
                        continue
                    base = os.path.basename(old_path).replace(".tex", "")
                    parts = base.split("-")
                    if len(parts) < 5:
                        fail += 1
                        continue
                    old_year, old_ptype, old_pname, old_pnum, old_subj = parts[0], parts[1], parts[2], parts[3], parts[4]
                    new_filename = generate_filename(new_year, new_type, new_name, old_pnum, old_subj)
                    primary_subj = old_subj.split("，")[0] if old_subj else ""
                    target_dir = os.path.join(CHAPTERS_DIR, primary_subj, str(new_year))
                    ensure_dir(target_dir)
                    new_path = os.path.join(target_dir, new_filename)
                    with open(old_path, "r", encoding="utf-8") as f:
                        old_content = f.read()
                    new_header = f"\\begin{{problem}}{{{new_year}}}{{{new_type}}}{{{new_name}}}{{{old_pnum}}}{{{old_subj}}}"
                    new_content = re.sub(r"\\begin\{problem\}\{.*?\}\{.*?\}\{.*?\}\{.*?\}\{.*?\}", lambda _m: new_header, old_content, count=1)
                    if new_content == old_content and "\\begin{problem}" in old_content:
                        new_content = re.sub(r"\\begin\{problem\}", lambda _m: new_header, old_content, count=1)
                    atomic_write_text(new_path, new_content, backup=os.path.exists(new_path))
                    if os.path.abspath(new_path) != os.path.abspath(old_path):
                        os.remove(old_path)
                    update_csv_index_for_edit(old_path, new_path, new_content, str(new_year), new_type, new_name, old_pnum, old_subj)
                    ok += 1
                    log_lines.append(f"✅ {os.path.basename(old_path)} -> {os.path.basename(new_path)}")
                except Exception as e:
                    fail += 1
                    log_lines.append(f"❌ {os.path.basename(old_path) if old_path else ''}: {e}")

            clear_statistics_cache()
            st.success(f"批量更新完成：成功 {ok}，失败 {fail}")
            with st.expander("查看日志", expanded=(fail > 0)):
                for line in log_lines:
                    st.write(line)
            st.session_state["tag_edit_bulk_paths"] = []
            st.session_state["tag_edit_bulk_meta"] = None
            time.sleep(0.5)
            st.rerun()

    file_path = st.session_state.get("tag_edit_file")
    if file_path and os.path.exists(file_path):
        # 读取文件内容
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # 解析当前元数据
        current_meta = {}
        parts = os.path.basename(file_path)[:-4].split('-')
        if len(parts) >= 5:
            current_meta = {
                "year": parts[0],
                "type": parts[1],
                "name": parts[2],
                "num": parts[3],
                "subject": parts[4]
            }
        
        c_edit_left, c_edit_right = st.columns([1, 1])
        
        with c_edit_left:
            st.subheader("LaTeX 源码预览")
            est_height = get_editor_height(content)
            mtime_token = int(os.path.getmtime(file_path)) if os.path.exists(file_path) else 0
            st.text_area("源码", value=content, height=est_height, disabled=True, key=f"te_preview_left_{file_path}_{mtime_token}")
            st.caption(f"文件路径: {file_path}")
            
        with c_edit_right:
            st.subheader("修改元数据")
            with st.form("te_meta_update_form"):
                new_year = st.text_input("年份", value=current_meta.get("year", ""))
                
                type_opts = _editable_paper_type_options()
                default_type_idx = 0
                if current_meta.get("type") in type_opts:
                    default_type_idx = type_opts.index(current_meta.get("type"))
                new_type = st.selectbox("试卷类型", options=type_opts, index=default_type_idx, format_func=lambda x: f"{x} ({PAPER_TYPES[x]})")
                
                new_name = st.text_input("试卷名称", value=current_meta.get("name", ""))
                new_num = st.text_input("题号", value=current_meta.get("num", ""))
                
                # 多板块处理
                current_subjects = current_meta.get("subject", "").split("，")
                valid_current_subjects = [s for s in current_subjects if s in SUBJECTS]
                if not valid_current_subjects:
                    valid_current_subjects = [SUBJECTS[0]] if SUBJECTS else []
                
                new_subjects = st.multiselect("知识板块 (首个为主)", options=SUBJECTS, default=valid_current_subjects)
                new_subject_str = "，".join(new_subjects) if new_subjects else (SUBJECTS[0] if SUBJECTS else "")
                new_question_type = st.selectbox("题型（必选）", QUESTION_TYPES,
                                                index=QUESTION_TYPES.index(infer_question_type(content, current_meta.get("subject", ""))))
                
                st.caption("注意：修改元数据将重命名文件并更新文件内容的 problem 头部信息。主板块(第一个)决定文件存储位置。")
                
                if st.form_submit_button("执行重命名与标签更新", type="primary"):
                    new_filename = generate_filename(new_year, new_type, new_name, new_num, new_subject_str)
                    
                    primary_subj = new_subject_str.split("，")[0] if new_subject_str else ""
                    current_primary = current_meta.get("subject", "").split("，")[0]
                    
                    target_dir = os.path.join(CHAPTERS_DIR, primary_subj, new_year)
                    if primary_subj != current_primary or new_year != current_meta.get("year"):
                        ensure_dir(target_dir)
                    new_path = os.path.join(target_dir, new_filename)

                    try:
                        require_question_type(new_question_type)
                        new_header = f"\\begin{{problem}}{{{new_year}}}{{{new_type}}}{{{new_name}}}{{{new_num}}}{{{new_subject_str}}}"
                        new_full_text = re.sub(r"\\begin\{problem\}\{.*?\}\{.*?\}\{.*?\}\{.*?\}\{.*?\}", lambda _m: new_header, content, count=1)
                        if new_full_text == content and "\\begin{problem}" in content:
                            new_full_text = re.sub(r"\\begin\{problem\}", lambda _m: new_header, content, count=1)
                        new_label_meta, _ = _parse_question_meta(new_full_text)
                        new_label_meta["题型"] = new_question_type
                        new_full_text = _inject_question_meta(new_full_text, new_label_meta)
                        atomic_write_text(new_path, new_full_text, backup=True)
                        
                        if new_path != file_path:
                            os.remove(file_path)
                            
                        # 同步更新到 CSV 索引
                        update_csv_index_for_edit(file_path, new_path, new_full_text, new_year, new_type, new_name, new_num, new_subject_str)
                        _invalidate_semantic_for_file(file_path)
                        _invalidate_semantic_for_file(new_path)
                            
                        st.success(f"更新成功！\n旧: {os.path.basename(file_path)}\n新: {new_filename}")
                        clear_statistics_cache()
                        st.session_state["tag_edit_file"] = new_path
                        time.sleep(1)
                        st.rerun()
                    except Exception as e:
                        st.error(f"更新失败: {e}")

def update_question_meta_fields(fpath, updates):
    from utils.latex_ops import parse_meta_data, inject_meta_data
    updates = dict(updates)
    if "题型" in updates:
        require_question_type(updates["题型"])
    for tag_field in ("标签", "知识点", "物理方法"):
        if tag_field in updates:
            updates[tag_field] = _merge_tag_texts(updates[tag_field])
    with open(fpath, "r", encoding="utf-8") as f:
        fc = f.read()
    fm, _ = parse_meta_data(fc)
    fm.update(updates)
    new_fc = inject_meta_data(fc, fm)
    atomic_write_text(fpath, new_fc, backup=True)
    record_operation(
        "update_question_meta",
        path=os.path.abspath(fpath),
        details=f"fields={','.join(updates.keys())}",
    )
    _invalidate_semantic_for_file(fpath)
    try:
        from utils.csv_ops import update_csv_index_for_edit
        # 从文件名解析基础信息
        basename = os.path.basename(fpath).replace(".tex", "")
        parts = basename.split("-")
        if len(parts) >= 5:
            new_year = parts[0]
            new_ptype = parts[1]
            new_pname = parts[2]
            new_pnum = parts[3]
            new_subj = parts[4]
            update_csv_index_for_edit(fpath, fpath, new_fc, new_year, new_ptype, new_pname, new_pnum, new_subj)
            _clear_advanced_search_result_cache()
        else:
            print("Update CSV failed: Invalid filename format.")
    except Exception as e:
        print("Update CSV failed:", e)


def update_question_meta(fpath, key, value):
    update_question_meta_fields(fpath, {key: value})

def _split_tag_values(tag_text: str) -> list[str]:
    tags = []
    seen = set()
    for raw in re.split(r"[，,]", tag_text or ""):
        tag = raw.strip()
        if tag and tag not in seen:
            tags.append(tag)
            seen.add(tag)
    return tags


def _merge_tag_texts(*tag_texts: str) -> str:
    """合并任意数量的自由标签文本，兼容中英文逗号并保持录入顺序。"""
    merged = []
    seen = set()
    for tag_text in tag_texts:
        for tag in _split_tag_values(tag_text):
            if tag not in seen:
                merged.append(tag)
                seen.add(tag)
    return "，".join(merged)

def _parse_tag_history_time(value: str) -> float:
    text = (value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text, fmt).timestamp()
        except ValueError:
            pass
    return 0.0

@st.cache_data(show_spinner=False)
def _tag_history_suggestions_cached(csv_token, limit=5):
    from utils.csv_ops import read_csv_index

    stats = {}
    for row in _filter_question_rows(read_csv_index()):
        row_time = max(
            _parse_tag_history_time(row.get("最后修改时间", "")),
            _parse_tag_history_time(row.get("初次录入的时间", "")),
        )
        for tag in _split_tag_values(row.get("标签", "")):
            item = stats.setdefault(tag, {"tag": tag, "count": 0, "last_seen": 0.0})
            item["count"] += 1
            item["last_seen"] = max(item["last_seen"], row_time)

    ranked = sorted(
        stats.values(),
        key=lambda item: (-item["count"], -item["last_seen"], item["tag"]),
    )
    return ranked[:limit]

def get_tag_history_suggestions(limit=5):
    return _tag_history_suggestions_cached(file_change_token(CSV_INDEX_PATH), limit)

def _append_tag_text(current_tags: str, tag: str) -> str:
    tags = _split_tag_values(current_tags)
    if tag not in tags:
        tags.append(tag)
    return "，".join(tags)

def _apply_tag_suggestion(input_key: str, tag: str):
    st.session_state[input_key] = _append_tag_text(st.session_state.get(input_key, ""), tag)


@st.cache_data(show_spinner=False)
def _cloze_source_lookup_cached(source_question_id: str, csv_token: str) -> dict:
    """Resolve a WK source ID through the existing CSV index without exposing WK as a source."""
    from utils.csv_ops import read_csv_index

    source_question_id = str(source_question_id or "").strip()
    if not source_question_id:
        return {}

    for row in read_csv_index():
        if (row.get("试卷类型", "") or "").strip() == "WK":
            continue
        if str(row.get("题目ID", "") or "").strip() != source_question_id:
            continue
        rel_path = (row.get("相对文件路径", "") or "").strip()
        source_path = os.path.join(CHAPTERS_DIR, rel_path) if rel_path else ""
        return {
            "path": source_path,
            "label": _cloze_source_label(row),
            "exists": bool(source_path and os.path.exists(source_path)),
        }
    return {}


def render_cloze_source_trace(content: str, fpath: str, meta: dict):
    """Show an on-demand source preview only for generated WK questions."""
    fields = extract_problem_header_fields(content) or {}
    if fields.get("p_type") != "WK":
        return

    source_question_id = str(meta.get("来源题目ID", "") or "").strip()
    if not source_question_id:
        st.caption("来源原题：未关联")
        return

    trace_key = f"cloze_source_trace_open_{hashlib.md5(fpath.encode()).hexdigest()[:12]}"
    lookup = _cloze_source_lookup_cached(source_question_id, file_change_token(CSV_INDEX_PATH))
    trace_label, trace_action = st.columns([4, 1])
    with trace_label:
        source_label = lookup.get("label") or f"题目 ID：{source_question_id}"
        st.caption(f"来源原题：{source_label}")
    with trace_action:
        action_label = "收起原题" if st.session_state.get(trace_key) else "查看原题"
        if st.button(action_label, key=f"btn_{trace_key}", use_container_width=True):
            st.session_state[trace_key] = not st.session_state.get(trace_key, False)

    if not st.session_state.get(trace_key):
        return
    if not lookup:
        st.warning(f"未在题库索引中找到来源题目 ID：{source_question_id}")
        return
    if not lookup.get("exists"):
        st.warning(f"来源题目文件已不存在：{lookup.get('label') or source_question_id}")
        return

    try:
        with open(lookup["path"], "r", encoding="utf-8") as f:
            source_content = f.read()
        with st.expander(f"原题预览：{lookup['label']}", expanded=True):
            st.markdown(latex_to_markdown(source_content, show_title=False), unsafe_allow_html=True)
    except Exception as e:
        st.warning(f"打开来源原题失败：{e}")


def _static_difficulty_stars_html(diff_val, max_stars=6):
    rounded_value = max(0, min(max_stars, int(round(float(diff_val or 0)))))
    stars = "".join(
        f"<span class='{'is-filled' if star_index <= rounded_value else 'is-empty'}'>★</span>"
        for star_index in range(1, max_stars + 1)
    )
    return f"{stars}<span class='mc-static-diff-value'>{float(diff_val or 0):g}</span>"


_MC_COMPACT_META_CSS = """
        <style>
        .mc-compact-meta-anchor {
            display: none !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.mc-compact-meta-anchor) {
            display: grid !important;
            grid-template-columns: minmax(218px, 230px) repeat(4, minmax(118px, 1fr)) minmax(0, 0.2fr) !important;
            align-items: center !important;
            column-gap: 1rem !important;
            row-gap: 0.35rem !important;
            margin: 0.1rem 0 0.45rem !important;
            width: 100% !important;
            min-width: 0 !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.mc-compact-meta-anchor) > div[data-testid="column"] {
            width: auto !important;
            min-width: 0 !important;
            max-width: none !important;
            flex: initial !important;
            padding: 0 !important;
        }
        div[data-testid="stMarkdownContainer"]:has(.mc-compact-meta-anchor) {
            display: none !important;
        }
        div[data-testid="element-container"]:has(.mc-compact-meta-anchor) + div[data-testid="element-container"] iframe {
            width: 218px !important;
            min-width: 218px !important;
            max-width: 218px !important;
            height: 35px !important;
            display: block !important;
        }
        .mc-compact-meta-row {
            display: flex;
            align-items: center;
            gap: 0.45rem 0.65rem;
            flex-wrap: nowrap;
            margin: 0;
            white-space: nowrap;
            min-width: 0;
        }
        .mc-compact-meta-row .meta-title {
            color: var(--mc-muted) !important;
            font-size: 14px;
            font-weight: 700;
            line-height: 1.2;
        }
        .mc-compact-meta-row .badge-tag,
        .mc-compact-meta-row .badge-rem {
            display: inline-flex;
            align-items: center;
            padding: 2px 8px;
            font-size: 14px;
            font-weight: 700;
            line-height: 1.45;
            border-radius: 999px;
            max-width: 190px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .mc-compact-meta-row .badge-tag {
            color: var(--mc-accent-dark) !important;
            background-color: var(--mc-accent-soft) !important;
            border: 1px solid var(--mc-border) !important;
        }
        .mc-compact-meta-row .badge-rem {
            color: var(--mc-text) !important;
            background-color: var(--mc-surface-soft) !important;
            border: 1px solid var(--mc-border) !important;
        }
        .mc-static-diff-row {
            display: flex;
            align-items: center;
            gap: 0.3rem;
            min-height: 35px;
            white-space: nowrap;
        }
        .mc-static-diff-stars {
            display: inline-flex;
            align-items: center;
            gap: 1px;
            color: #d8dee9;
            font-size: 1.15rem;
            line-height: 1;
            letter-spacing: -0.02em;
        }
        .mc-static-diff-stars .is-filled {
            color: #f2b94b;
        }
        .mc-static-diff-stars .is-empty {
            color: #e5e7eb;
        }
        .mc-static-diff-value {
            margin-left: 0.25rem;
            color: var(--mc-muted) !important;
            font-size: 0.82rem;
            font-weight: 700;
        }
        </style>
        """


def render_question_header(q_label, content, fpath, extra_html_label="", compact=False, prepared_meta=None, interactive_difficulty=True):
    st.markdown(f"### {q_label} {extra_html_label}", unsafe_allow_html=True)
    
    meta = prepared_meta if prepared_meta is not None else _cached_question_meta(content)
    question_type = meta.get("题型") or infer_question_type(content)
    st.caption(f"题型：{question_type}")
    diff = meta.get("难度星级", "").strip()
    tags = meta.get("标签", "").strip()
    knowledge_points = meta.get("知识点", "").strip()
    physics_methods = meta.get("物理方法", "").strip()
    remark = meta.get("备注", "").strip()
    render_cloze_source_trace(content, fpath, meta)

    try:
        diff_val = float(diff)
    except:
        diff_val = 0.0

    from utils.star_rating import st_star_rating
    
    pending_key = f"pending_diff_{fpath}"
    version_key = f"star_key_version_{fpath}"
    if compact:
        from utils.star_rating import st_star_rating
        compact_hash = hashlib.md5(f"compact:{fpath}:{q_label}".encode()).hexdigest()[:8]
        # Keep relational layout rules local to pages that actually render cards.
        st.markdown(_MC_COMPACT_META_CSS, unsafe_allow_html=True)
        compact_diff, compact_tag, compact_points, compact_methods, compact_remark, compact_spacer = st.columns([1.15, 0.55, 0.55, 0.55, 0.5, 2.8], gap="small", vertical_alignment="center")
        with compact_diff:
            st.markdown(f'<span class="mc-compact-meta-anchor" data-key="{compact_hash}"></span>', unsafe_allow_html=True)
            if interactive_difficulty:
                new_diff = st_star_rating(label="难度星级：", value=diff_val, max_stars=6, key=f"compact_star_{compact_hash}_{st.session_state.get(version_key, 0)}")
                if new_diff is not None and new_diff != diff_val:
                    update_question_meta(fpath, "难度星级", str(new_diff))
                    st.session_state[version_key] = st.session_state.get(version_key, 0) + 1
                    _request_question_scroll_restore(fpath)
                    st.rerun()
            else:
                st.markdown(
                    f"<div class='mc-static-diff-row'><span class='meta-title'>难度星级：</span>"
                    f"<span class='mc-static-diff-stars'>{_static_difficulty_stars_html(diff_val)}</span></div>",
                    unsafe_allow_html=True,
                )
        with compact_tag:
            tag_text = tags or "无标签"
            st.markdown(f"<div class='mc-compact-meta-row'><span class='meta-title'>其他标签：</span><span class='badge-tag'>{html.escape(tag_text)}</span></div>", unsafe_allow_html=True)
        with compact_points:
            point_text = knowledge_points or "未填写"
            st.markdown(f"<div class='mc-compact-meta-row'><span class='meta-title'>知识点：</span><span class='badge-tag'>{html.escape(point_text)}</span></div>", unsafe_allow_html=True)
        with compact_methods:
            method_text = physics_methods or "未填写"
            st.markdown(f"<div class='mc-compact-meta-row'><span class='meta-title'>物理方法：</span><span class='badge-tag'>{html.escape(method_text)}</span></div>", unsafe_allow_html=True)
        with compact_remark:
            remark_text = remark or "无备注"
            st.markdown(f"<div class='mc-compact-meta-row'><span class='meta-title'>备注：</span><span class='badge-rem'>{html.escape(remark_text)}</span></div>", unsafe_allow_html=True)
        with compact_spacer:
            st.empty()
        return
    # --- 注入 CSS 实现紧凑同行布局与徽章样式 ---
    st.markdown("""
    <style>
    .meta-cell {
        display: flex;
        align-items: center;
        min-height: 40px;
        gap: 5px;
        white-space: nowrap;
    }
    .meta-cell .meta-title {
        color: #1f2328;
        font-size: 14px;
        font-weight: 700;
        line-height: 1;
        flex: 0 0 auto;
    }
    .meta-cell .meta-empty {
        color: #3f3a46;
        font-size: 14px;
        font-weight: 600;
        line-height: 1;
    }
    div[data-testid="stHorizontalBlock"]:has(.meta-row-marker) {
        display: grid !important;
        grid-template-columns: 190px minmax(72px, max-content) 36px minmax(72px, max-content) 36px minmax(0, 1fr) !important;
        gap: 4px !important;
        align-items: center !important;
        width: 100% !important;
        min-width: 0 !important;
        flex-wrap: nowrap !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.meta-row-marker) > div[data-testid="column"] {
        width: auto !important;
        min-width: 0 !important;
        max-width: none !important;
        flex: initial !important;
        padding: 0 !important;
    }
    div[data-testid="column"]:has(.meta-star-cell) {
        width: 190px !important;
        min-width: 190px !important;
        max-width: 190px !important;
        margin-right: 0 !important;
    }
    div[data-testid="column"]:has(.meta-tag-cell),
    div[data-testid="column"]:has(.meta-remark-cell) {
        flex: 0 1 auto !important;
        width: fit-content !important;
        min-width: 72px !important;
        max-width: 180px !important;
    }
    div[data-testid="column"]:has(.meta-action-cell),
    .mc-meta-action-column {
        flex: 0 0 36px !important;
        width: 36px !important;
        min-width: 36px !important;
        max-width: 36px !important;
        justify-content: flex-start !important;
    }
    div[data-testid="column"]:has(.meta-tag-action-cell) {
        margin-right: 0 !important;
    }
    div[data-testid="column"]:has(.meta-filler-cell) {
        flex: 1 1 auto !important;
        min-width: 0 !important;
    }
    div[data-testid="column"]:has(.meta-star-cell),
    div[data-testid="column"]:has(.meta-tag-cell),
    div[data-testid="column"]:has(.meta-remark-cell),
    div[data-testid="column"]:has(.meta-action-cell) {
        display: flex !important;
        align-items: center !important;
        min-height: 44px !important;
    }
    div[data-testid="element-container"]:has(.meta-star-cell) + div[data-testid="element-container"] iframe {
        display: block !important;
        width: 190px !important;
        min-width: 190px !important;
        max-width: 190px !important;
        height: 35px !important;
        margin: 0 !important;
        padding: 0 !important;
        transform: translateY(1px);
    }
    div[data-testid="column"]:has(.meta-text-cell) p {
        margin: 0 !important;
        padding: 0 !important;
        line-height: 1 !important;
    }
    /* 淡灰色 + 按钮 */
    div[data-testid="column"]:has(.meta-action-cell) div[data-testid="stPopover"] > button,
    .mc-meta-action-popover button,
    .mc-meta-action-button {
        color: var(--mc-action-text) !important;
        background: var(--mc-action-bg) !important;
        border: 1px solid var(--mc-action-border) !important;
        padding: 0 !important;
        min-height: 36px !important;
        height: 36px !important;
        width: 36px !important;
        min-width: 36px !important;
        max-width: 36px !important;
        border-radius: 10px !important;
        font-size: 22px !important;
        font-weight: 800 !important;
        margin: 0 !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        position: relative !important;
        overflow: hidden !important;
        gap: 0 !important;
        text-shadow: none !important;
        box-shadow: var(--mc-action-shadow) !important;
        transform: translateY(0);
        transition: transform 0.14s ease, box-shadow 0.14s ease, background 0.14s ease !important;
    }
    div[data-testid="column"]:has(.meta-action-cell) div[data-testid="stPopover"] > button::after,
    .mc-meta-action-button::after {
        content: "";
        position: absolute;
        top: 1px;
        left: 2px;
        right: 2px;
        height: 45%;
        border-radius: 8px 8px 6px 6px;
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.36), rgba(255, 255, 255, 0));
        pointer-events: none;
    }
    div[data-testid="column"]:has(.meta-action-cell) div[data-testid="stPopover"],
    .mc-meta-action-popover {
        width: 36px !important;
        min-width: 36px !important;
        max-width: 36px !important;
    }
    div[data-testid="column"]:has(.meta-action-cell) div[data-testid="stPopover"] > button svg,
    .mc-meta-action-popover button svg,
    .mc-meta-action-popover button [data-testid="stIconMaterial"],
    .mc-meta-action-button svg,
    .mc-meta-action-button [data-testid="stIconMaterial"] {
        display: none !important;
    }
    div[data-testid="column"]:has(.meta-action-cell) div[data-testid="stPopover"] > button p,
    .mc-meta-action-popover button p,
    .mc-meta-action-button p {
        margin: 0 !important;
        line-height: 1 !important;
        font-size: 22px !important;
        color: var(--mc-action-text) !important;
    }
    div[data-testid="column"]:has(.meta-action-cell) div[data-testid="stPopover"] > button:hover,
    .mc-meta-action-popover button:hover,
    .mc-meta-action-button:hover {
        background: var(--mc-action-bg-hover) !important;
        border-color: var(--mc-action-border) !important;
        box-shadow: 0 8px 18px rgba(91, 33, 182, 0.16) !important;
        transform: translateY(-1px);
    }
    .mc-meta-action-button[aria-expanded="true"],
    .mc-meta-action-button:active {
        background: var(--mc-action-bg) !important;
        border-color: var(--mc-action-border) !important;
        box-shadow: var(--mc-action-shadow) !important;
        transform: translateY(0);
    }
    .mc-meta-plus {
        display: block;
        position: relative;
        z-index: 1;
        color: var(--mc-action-text);
        font-size: 22px;
        font-weight: 800;
        line-height: 1;
        transform: translateY(-1px);
    }
    .tag-suggestion-title {
        color: var(--mc-muted) !important;
        font-size: 13px;
        font-weight: 700;
        margin: 8px 0 6px 0;
    }

    /* 现代徽章样式 (Badge) */
    .badge-tag {
        display: inline-flex;
        align-items: center;
        padding: 2px 8px;
        font-size: 14px;
        font-weight: 700;
        line-height: 1.5;
        color: var(--mc-accent-dark);
        background-color: var(--mc-accent-soft);
        border: 1px solid var(--mc-border);
        border-radius: 2em;
        margin-right: 4px;
    }
    .badge-rem {
        display: inline-flex;
        align-items: center;
        padding: 2px 8px;
        font-size: 14px;
        font-weight: 700;
        line-height: 1.5;
        color: var(--mc-text);
        background-color: var(--mc-surface-soft);
        border: 1px solid var(--mc-border);
        border-radius: 4px;
        margin-right: 4px;
    }
    </style>
    """, unsafe_allow_html=True)

    components.html(
        """
        <script>
        (() => {
            const w = window.parent;
            const d = w.document;

            function closestColumn(node) {
                return node ? node.closest('div[data-testid="column"]') : null;
            }

            function paintActionButton(button) {
                if (!button) {
                    return;
                }
                const text = (button.textContent || "").trim();
                if (!text.includes("+") && !text.includes("＋")) {
                    return;
                }
                button.classList.add("mc-meta-action-button");
                if (!button.querySelector(".mc-meta-plus")) {
                    button.innerHTML = '<span class="mc-meta-plus">＋</span>';
                }
                Object.assign(button.style, {
                    color: "#332442",
                    background: "#d6c4ee",
                    border: "1px solid #b49ad2",
                    width: "36px",
                    minWidth: "36px",
                    maxWidth: "36px",
                    height: "36px",
                    minHeight: "36px",
                    padding: "0",
                    borderRadius: "10px",
                    display: "inline-flex",
                    alignItems: "center",
                    justifyContent: "center",
                    position: "relative",
                    overflow: "hidden",
                    gap: "0",
                    textShadow: "none",
                    boxShadow: "0 5px 14px rgba(91, 33, 182, 0.12)",
                    transition: "transform 0.14s ease, box-shadow 0.14s ease, background 0.14s ease"
                });
                button.querySelectorAll('svg, [data-testid="stIconMaterial"]').forEach((icon) => {
                    icon.style.setProperty("display", "none", "important");
                });
                button.querySelectorAll("p, span").forEach((textNode) => {
                    if ((textNode.textContent || "").includes("+") || (textNode.textContent || "").includes("＋")) {
                        textNode.style.setProperty("color", "#332442", "important");
                        textNode.style.setProperty("font-size", "22px", "important");
                        textNode.style.setProperty("font-weight", "800", "important");
                        textNode.style.setProperty("line-height", "1", "important");
                        textNode.style.setProperty("margin", "0", "important");
                    }
                });
            }

            function applyMetaActionClasses() {
                d.querySelectorAll(".meta-action-cell").forEach((marker) => {
                    const column = closestColumn(marker);
                    if (!column) {
                        return;
                    }
                    column.classList.add("mc-meta-action-column");
                    column.querySelectorAll("button").forEach(paintActionButton);
                    column.querySelectorAll('div[data-testid="stPopover"]').forEach((popover) => {
                        popover.classList.add("mc-meta-action-popover");
                        popover.querySelectorAll("button").forEach((button) => {
                            button.classList.add("mc-meta-action-button");
                            paintActionButton(button);
                        });
                    });
                });
            }

            if (w.__mcMetaActionObserver) {
                w.__mcMetaActionObserver.disconnect();
            }
            let frame = 0;
            let observer;
            const stats = w.__mcObserverStats ||= {meta:0,basket:0};
            const apply = () => {
                frame = 0;
                stats.meta++;
                applyMetaActionClasses();
            };
            const schedule = () => { if (!frame) frame=w.requestAnimationFrame(apply); };
            const timer = w.setTimeout(() => {
                apply();
                const scope=d.querySelector('.meta-action-cell')?.closest('[data-testid="stVerticalBlockBorderWrapper"]');
                if (scope) {
                    observer=new w.MutationObserver(schedule);
                    observer.observe(scope,{childList:true,subtree:true});
                    w.__mcMetaActionObserver=observer;
                }
            }, 80);
            window.addEventListener('pagehide', () => {
                w.clearTimeout(timer);
                if(frame)w.cancelAnimationFrame(frame);
                observer?.disconnect();
            }, {once:true});
        })();
        </script>
        """,
        height=0,
    )

    with st.container(border=True):
        # === 统一放在同一行：星级 | 标签 + 按钮 | 备注 + 按钮 ===
        c_star, c_tag_lbl, c_tag_btn, c_rem_lbl, c_rem_btn, c_filler = st.columns([1.65, 1.05, 0.5, 1.05, 0.5, 7.25], vertical_alignment="center", gap="small")

        with c_star:
            st.markdown("<span class='meta-row-marker meta-star-cell'></span>", unsafe_allow_html=True)
            # 使用更全局唯一的 key，防止在不同页面（全局浏览 vs 搜索结果）复用同一个文件时产生冲突
            unique_hash = hashlib.md5(f"{fpath}_{q_label}_{extra_html_label}".encode()).hexdigest()[:8]
            comp_key = f"star_rating_{unique_hash}_{st.session_state.get(version_key, 0)}"
            new_diff = st_star_rating(label="难度星级：", value=diff_val, max_stars=6, key=comp_key)

            if new_diff is not None and new_diff != diff_val:
                if diff_val == 0.0:
                    update_question_meta(fpath, "难度星级", str(new_diff))
                    st.session_state[version_key] = st.session_state.get(version_key, 0) + 1
                    _request_question_scroll_restore(fpath)
                    st.rerun()
                else:
                    st.session_state[pending_key] = new_diff

        with c_tag_lbl:
            if tags:
                # 把逗号分隔的标签拆分成多个小徽章
                tag_html = "".join([f"<span class='badge-tag'>🏷️ {t.strip()}</span>" for t in tags.split("，") if t.strip()])
                st.markdown(f"<div class='meta-cell meta-text-cell meta-tag-cell'><span class='meta-title'>标签：</span>{tag_html}</div>", unsafe_allow_html=True)
            else:
                st.markdown("<div class='meta-cell meta-text-cell meta-tag-cell'><span class='meta-title'>标签：</span><span class='meta-empty'>无标签</span></div>", unsafe_allow_html=True)

        with c_tag_btn:
            st.markdown("<span class='meta-action-cell meta-tag-action-cell'></span>", unsafe_allow_html=True)
            tag_popover_key = f"tag_popover_{fpath}_{st.session_state.get(f'tag_version_{fpath}', 0)}"
            with st.popover("＋", help="修改标签"):
                tag_input_key = f"tag_input_{tag_popover_key}"
                new_tags_str = st.text_input("编辑标签（逗号“，”分隔）", value=tags, key=tag_input_key)
                tag_suggestions = get_tag_history_suggestions(limit=5)
                if tag_suggestions:
                    st.markdown("<div class='tag-suggestion-title'>历史热门标签</div>", unsafe_allow_html=True)
                    for idx, item in enumerate(tag_suggestions):
                        tag = item["tag"]
                        count = item["count"]
                        st.button(
                            f"🏷️ {tag}  ×{count}",
                            key=f"tag_suggest_{idx}_{tag_popover_key}",
                            help="点击添加到标签输入框",
                            use_container_width=True,
                            on_click=_apply_tag_suggestion,
                            args=(tag_input_key, tag),
                        )
                if not tags:
                    if st.button("直接保存", key=f"tag_save_{tag_popover_key}", type="primary"):
                        update_question_meta(fpath, "标签", new_tags_str)
                        st.session_state[f'tag_version_{fpath}'] = st.session_state.get(f'tag_version_{fpath}', 0) + 1
                        _request_question_scroll_restore(fpath)
                        st.rerun()
                else:
                    tc1, tc2 = st.columns(2)
                    with tc1:
                        if st.button("确认", key=f"tag_ok_{tag_popover_key}", type="primary"):
                            update_question_meta(fpath, "标签", new_tags_str)
                            st.session_state[f'tag_version_{fpath}'] = st.session_state.get(f'tag_version_{fpath}', 0) + 1
                            _request_question_scroll_restore(fpath)
                            st.rerun()
                    with tc2:
                        if st.button("取消", key=f"tag_cancel_{tag_popover_key}", type="secondary"):
                            st.session_state[f'tag_version_{fpath}'] = st.session_state.get(f'tag_version_{fpath}', 0) + 1
                            st.rerun()

        with c_rem_lbl:
            if remark:
                st.markdown(f"<div class='meta-cell meta-text-cell meta-remark-cell'><span class='meta-title'>备注：</span><span class='badge-rem'>📝 {remark}</span></div>", unsafe_allow_html=True)
            else:
                st.markdown("<div class='meta-cell meta-text-cell meta-remark-cell'><span class='meta-title'>备注：</span><span class='meta-empty'>无备注</span></div>", unsafe_allow_html=True)

        with c_rem_btn:
            st.markdown("<span class='meta-action-cell meta-rem-action-cell'></span>", unsafe_allow_html=True)
            rem_popover_key = f"rem_popover_{fpath}_{st.session_state.get(f'rem_version_{fpath}', 0)}"
            with st.popover("＋", help="修改备注"):
                new_rem_str = st.text_input("编辑备注", value=remark, key=f"rem_input_{rem_popover_key}")
                if not remark:
                    if st.button("直接保存", key=f"rem_save_{rem_popover_key}", type="primary"):
                        update_question_meta(fpath, "备注", new_rem_str)
                        st.session_state[f'rem_version_{fpath}'] = st.session_state.get(f'rem_version_{fpath}', 0) + 1
                        st.rerun()
                else:
                    rc1, rc2 = st.columns(2)
                    with rc1:
                        if st.button("确认", key=f"rem_ok_{rem_popover_key}", type="primary"):
                            update_question_meta(fpath, "备注", new_rem_str)
                            st.session_state[f'rem_version_{fpath}'] = st.session_state.get(f'rem_version_{fpath}', 0) + 1
                            st.rerun()
                    with rc2:
                        if st.button("取消", key=f"rem_cancel_{rem_popover_key}", type="secondary"):
                            st.session_state[f'rem_version_{fpath}'] = st.session_state.get(f'rem_version_{fpath}', 0) + 1
                            st.rerun()

        with c_filler:
            st.markdown("<span class='meta-filler-cell'></span>", unsafe_allow_html=True)

        # 处理未保存的星级变更弹窗（放到最后，避免打乱单行布局）
        if pending_key in st.session_state:
            st.warning(f"确认修改为 {st.session_state[pending_key]} 星吗？")
            bc1, bc2 = st.columns(2)
            with bc1:
                if st.button("✅ 确认", key=f"diff_ok_{fpath}", type="primary"):
                    final_diff = st.session_state[pending_key]
                    update_question_meta(fpath, "难度星级", str(final_diff))
                    del st.session_state[pending_key]
                    st.session_state[version_key] = st.session_state.get(version_key, 0) + 1
                    _request_question_scroll_restore(fpath)
                    st.rerun()
            with bc2:
                if st.button("❌ 取消", key=f"diff_cancel_{fpath}", type="secondary"):
                    del st.session_state[pending_key]
                    st.session_state[version_key] = st.session_state.get(version_key, 0) + 1
                    st.rerun()

# ================= 辅助函数：搜索匹配 =================
import datetime

def clear_statistics_cache():
    _get_statistics_cached.clear()


def _filter_question_rows(rows, paper_type_scope=None):
    """Keep WK indexed, but isolate it from ordinary question-bank workflows."""
    filtered_rows = []
    for row in rows or []:
        paper_type = (row.get("试卷类型", "") or "").strip()
        if paper_type_scope:
            if paper_type != paper_type_scope:
                continue
        elif paper_type == "WK":
            continue
        filtered_rows.append(row)
    return filtered_rows

def _csv_index_cache_token():
    from utils.core_config import CSV_INDEX_PATH
    return file_change_token(CSV_INDEX_PATH)

@st.cache_data(show_spinner=False)
def _csv_index_cached(csv_token):
    from utils.csv_ops import read_csv_index
    return _filter_question_rows(read_csv_index())

@st.cache_data(show_spinner=False)
def _advanced_search_index_cached(csv_token, paper_type_scope=None):
    from utils.csv_ops import read_csv_index

    index_rows = []
    for row in _filter_question_rows(read_csv_index(), paper_type_scope):
        rel_path = (row.get("相对文件路径", "") or "").strip()
        abs_path = os.path.join(CHAPTERS_DIR, rel_path) if rel_path else ""
        filename = (row.get("文件名称", "") or "").strip()
        if filename and not filename.lower().endswith(".tex"):
            filename = filename + ".tex"

        stem = row.get("题干", "") or ""
        answer = row.get("答案", "") or ""
        solution = row.get("解析", "") or ""
        tags = row.get("标签", "") or ""
        knowledge_points = row.get("知识点", "") or ""
        physics_methods = row.get("物理方法", "") or ""
        remark = row.get("备注", "") or ""

        index_rows.append({
            "row": row,
            "file": filename,
            "path": abs_path,
            "type": (row.get("题型", "") or "").strip(),
            "stem": stem,
            "answer": answer,
            "solution": solution,
            "difficulty": row.get("难度星级", "") or "",
            "tags": tags,
            "knowledge_points": knowledge_points,
            "physics_methods": physics_methods,
            "remark": remark,
            "full_text": "\n".join([stem, answer, solution, tags, knowledge_points, physics_methods, remark]),
        })
    return index_rows

def get_statistics():
    return _get_statistics_cached(_csv_index_cache_token(), datetime.date.today().isoformat())


@st.cache_data(show_spinner=False, max_entries=4)
def _get_statistics_cached(csv_token, day):
    stats = {
        "total_questions": 0,
        "total_tikz": 0,
        "today_new_questions": 0,
        "today_mod_questions": 0,
        "today_new_tikz": 0,
        "today_mod_tikz": 0,
        "daily_activity": {},
        "hourly_activity_by_day": {},
        "subject_counts": {},
        "type_counts": {},
        "difficulty_dist": {},
        "tag_counts": {},
        "total_difficulty": 0.0,
        "difficulty_count": 0
    }

    # 优先尝试从 CSV 索引表读取（性能提升 100 倍）
    try:
        from utils.csv_ops import read_csv_index
        csv_data = _filter_question_rows(read_csv_index())
        if csv_data is not None:
            stats["total_questions"] = len(csv_data)
            stats.update(sync_question_activity(csv_data))

            for row in csv_data:
                # 统计包含 TikZ 的题目
                if row.get("包含TikZ绘图") == "是":
                    stats["total_tikz"] += 1

                # 新增统计：各板块分布
                subj = row.get("知识板块", "").split("，")[0] if row.get("知识板块") else "未分类"
                stats["subject_counts"][subj] = stats["subject_counts"].get(subj, 0) + 1

                # 新增统计：各题型分布
                q_type = row.get("题型", "未知")
                stats["type_counts"][q_type] = stats["type_counts"].get(q_type, 0) + 1

                # 新增统计：平均难度与分布
                diff_str = row.get("难度星级", "")
                if diff_str and diff_str.replace('.', '', 1).isdigit():
                    diff_val = float(diff_str)
                    stats["total_difficulty"] += diff_val
                    stats["difficulty_count"] += 1

                    # 划分难度区间
                    if diff_val <= 2.0:
                        diff_label = "0-2星 (基础)"
                    elif diff_val <= 4.0:
                        diff_label = "3-4星 (中档)"
                    else:
                        diff_label = "5-6星 (压轴)"
                    stats["difficulty_dist"][diff_label] = stats["difficulty_dist"].get(diff_label, 0) + 1

                # 新增统计：标签词云数据
                tags = row.get("标签", "")
                if tags:
                    for tag in tags.split("，"):
                        tag = tag.strip()
                        if tag:
                            stats["tag_counts"][tag] = stats["tag_counts"].get(tag, 0) + 1

            return stats
    except Exception as e:
        # 如果 CSV 读取失败，回退到遍历文件夹的旧逻辑
        pass

    # ================= 降级：文件夹遍历统计 =================
    today_start = datetime.datetime.combine(datetime.date.today(), datetime.time.min).timestamp()

    if not os.path.exists(CHAPTERS_DIR):
        return stats

    for root, dirs, files in os.walk(CHAPTERS_DIR):
        is_tikz_dir = "相关图" in root

        for file in files:
            if not file.endswith(".tex"):
                continue
            if file.startswith("content_"):
                continue
            if "-WK-" in file:
                continue

            file_path = os.path.join(root, file)
            try:
                stat_info = os.stat(file_path)
                ctime = stat_info.st_ctime
                mtime = stat_info.st_mtime

                # 修改点：不再仅依赖文件的创建时间，而是统计每天所有题目的最后修改时间
                # 作为活跃度的依据（或者是创建时间也可以，这里我们把最后修改时间也算进去）
                c_date = datetime.datetime.fromtimestamp(ctime).date().isoformat()
                m_date = datetime.datetime.fromtimestamp(mtime).date().isoformat()

                c_hour = datetime.datetime.fromtimestamp(ctime).strftime('%H')
                m_hour = datetime.datetime.fromtimestamp(mtime).strftime('%H')

                if c_date not in stats["hourly_activity_by_day"]:
                    stats["hourly_activity_by_day"][c_date] = {str(i).zfill(2): 0 for i in range(24)}
                stats["hourly_activity_by_day"][c_date][c_hour] += 1

                if m_hour != c_hour or m_date != c_date:
                    if m_date not in stats["hourly_activity_by_day"]:
                        stats["hourly_activity_by_day"][m_date] = {str(i).zfill(2): 0 for i in range(24)}
                    stats["hourly_activity_by_day"][m_date][m_hour] += 1

                is_today_created = ctime >= today_start
                is_today_modified = mtime >= today_start and not is_today_created

                # 新增逻辑：无论是新创建还是修改，都记录到热力图的活跃度中
                if not is_tikz_dir and " 图" not in file:
                    stats["daily_activity"][c_date] = stats["daily_activity"].get(c_date, 0) + 1
                    if m_date != c_date:
                        stats["daily_activity"][m_date] = stats["daily_activity"].get(m_date, 0) + 1

                if is_tikz_dir or " 图" in file:
                    stats["total_tikz"] += 1
                    if is_today_created:
                        stats["today_new_tikz"] += 1
                    elif is_today_modified:
                        stats["today_mod_tikz"] += 1
                else:
                    stats["total_questions"] += 1
                    if is_today_created:
                        stats["today_new_questions"] += 1
                    elif is_today_modified:
                        stats["today_mod_questions"] += 1

            except Exception:
                pass

    return stats

def render_statistics_dashboard():
    from utils.charts import generate_heatmap_html, generate_activity_curve_html, generate_echarts_bar_html, generate_echarts_pie_html
    stats = get_statistics()

    st.markdown("### 📊 数据统计")

    # 统计页视觉层：只调整展示质感，不改统计数据。
    st.markdown("""
    <style>
    @keyframes statsFadeUp {
        from {
            opacity: 0;
            transform: translateY(8px);
        }
        to {
            opacity: 1;
            transform: translateY(0);
        }
    }
    div[data-testid="stMetric"],
    .stats-chart-title {
        animation: statsFadeUp 0.38s ease both;
    }
    div[data-testid="stMetric"] {
        position: relative;
        min-height: 112px;
        background: var(--mc-surface) !important;
        border: var(--mc-card-border, 1px solid var(--mc-border)) !important;
        padding: 14px 18px 14px 20px;
        border-radius: 12px;
        box-shadow: var(--mc-shadow) !important;
        backdrop-filter: var(--mc-backdrop, none) !important;
        -webkit-backdrop-filter: var(--mc-backdrop, none) !important;
        display: flex;
        flex-direction: column;
        justify-content: center;
        overflow: hidden;
        transition: transform 0.16s ease, box-shadow 0.16s ease, border-color 0.16s ease, background 0.16s ease;
    }
    div[data-testid="stMetric"]::before {
        content: "";
        position: absolute;
        left: 0;
        top: 14px;
        bottom: 14px;
        width: 3px;
        border-radius: 0 999px 999px 0;
        background: var(--mc-accent, #6366f1);
        opacity: 0.9;
    }
    div[data-testid="stMetric"]:hover {
        transform: translateY(-2px);
        border-color: var(--mc-accent) !important;
        box-shadow: 0 14px 34px rgba(0, 0, 0, 0.12) !important;
    }

    /* ===== 指标卡浅色氛围：四色柔和分组（参考复盘软件的浅粉/奶黄/天青/薄荷） ===== */
    div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(1) div[data-testid="stMetric"] {
        background: linear-gradient(158deg, #FDF1F3 0%, #FBE7EB 100%) !important;
        border-color: rgba(199, 132, 146, 0.30) !important;
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(1) div[data-testid="stMetric"]::before {
        background: linear-gradient(180deg, #D98A96 0%, #C9737F 100%) !important;
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(1) div[data-testid="stMetric"] [data-testid="stMetricLabel"] * {
        color: #B75F6D !important;
    }

    div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(2) div[data-testid="stMetric"] {
        background: linear-gradient(158deg, #FCF7E8 0%, #F8EFD8 100%) !important;
        border-color: rgba(196, 160, 92, 0.32) !important;
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(2) div[data-testid="stMetric"]::before {
        background: linear-gradient(180deg, #D9B463 0%, #C69F4A 100%) !important;
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(2) div[data-testid="stMetric"] [data-testid="stMetricLabel"] * {
        color: #A47C2A !important;
    }

    div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(3) div[data-testid="stMetric"] {
        background: linear-gradient(158deg, #EFF5FB 0%, #E5EEF8 100%) !important;
        border-color: rgba(126, 160, 194, 0.30) !important;
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(3) div[data-testid="stMetric"]::before {
        background: linear-gradient(180deg, #7FA6CC 0%, #6B93BC 100%) !important;
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(3) div[data-testid="stMetric"] [data-testid="stMetricLabel"] * {
        color: #4A729B !important;
    }

    div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(4) div[data-testid="stMetric"] {
        background: linear-gradient(158deg, #EEF7F1 0%, #E3F1E8 100%) !important;
        border-color: rgba(122, 166, 138, 0.30) !important;
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(4) div[data-testid="stMetric"]::before {
        background: linear-gradient(180deg, #7CAC8D 0%, #679A79 100%) !important;
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(4) div[data-testid="stMetric"] [data-testid="stMetricLabel"] * {
        color: #4A7D5E !important;
    }

    /* 卡片更圆润、留白更舒展 */
    div[data-testid="stMetric"] {
        border-radius: 16px !important;
        min-height: 108px !important;
        padding: 16px 18px 16px 20px !important;
    }
    div[data-testid="stMetric"]:hover {
        transform: translateY(-3px) !important;
        box-shadow: 0 16px 36px rgba(120, 95, 60, 0.14) !important;
    }

    div[data-testid="stMetric"] label,
    div[data-testid="stMetric"] [data-testid="stMetricLabel"],
    div[data-testid="stMetric"] [data-testid="stMetricLabel"] *,
    div[data-testid="stMetric"] label * {
        color: var(--mc-muted) !important;
        font-size: 0.9rem !important;
        font-weight: 650 !important;
    }
    div[data-testid="stMetric"] [data-testid="stMetricValue"],
    div[data-testid="stMetric"] [data-testid="stMetricValue"] * {
        color: var(--mc-text) !important;
        font-weight: 750 !important;
        letter-spacing: 0 !important;
    }
    .stats-chart-title {
        margin: 20px 0 10px 4px;
        color: var(--mc-text) !important;
        font-size: 1.05rem;
        font-weight: 720;
        letter-spacing: 0;
    }
    div[data-testid="stVerticalBlock"]:has(.stats-chart-title) iframe {
        border-radius: 12px !important;
        box-shadow: 0 10px 30px rgba(31, 35, 48, 0.065) !important;
        transition: transform 0.16s ease, box-shadow 0.16s ease, filter 0.16s ease;
    }
    div[data-testid="stVerticalBlock"]:has(.stats-chart-title) iframe:hover {
        transform: translateY(-2px);
        box-shadow: 0 16px 40px rgba(31, 35, 48, 0.10) !important;
        filter: saturate(1.03);
    }
    </style>
    """, unsafe_allow_html=True)

    # 计算平均难度
    avg_diff = 0.0
    if stats.get("difficulty_count", 0) > 0:
        avg_diff = stats["total_difficulty"] / stats["difficulty_count"]

    # 指标数据 - 恢复8个卡片两行排列
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("题库总题目数", stats["total_questions"])
    c2.metric("题库Tikz总数", stats["total_tikz"])
    c3.metric("今日新增题目", stats["today_new_questions"])
    c4.metric("今日改动题目", stats["today_mod_questions"])

    st.write("")
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("今日新增Tikz", stats["today_new_tikz"])
    c6.metric("今日改动Tikz", stats["today_mod_tikz"])
    c7.metric("平均难度星级", f"{avg_diff:.1f} ★" if avg_diff > 0 else "N/A")

    # 获取最高频的三个标签
    top_tags = sorted(stats.get("tag_counts", {}).items(), key=lambda x: x[1], reverse=True)[:3]
    top_tags_str = "、".join([t[0] for t in top_tags]) if top_tags else "暂无"
    c8.metric("热门标签", top_tags_str)

    st.write("")

    # 热力图与代码活跃时段曲线
    r2_c1, r2_c2 = st.columns([1, 1])
    cur_skin = _get_current_theme_skin()
    cur_mode = _get_current_theme_mode()
    with r2_c1:
        heatmap_html = generate_heatmap_html(stats["daily_activity"], _get_current_theme_skin(), _get_current_theme_mode())
        st.markdown(heatmap_html, unsafe_allow_html=True)

    with r2_c2:
        import streamlit.components.v1 as components
        hourly_activity_by_day = stats.get("hourly_activity_by_day", {})
        components.html(generate_activity_curve_html(hourly_activity_by_day, skin=cur_skin, mode=cur_mode), height=300)

    st.write("")

    # 更多有趣的数据统计：图表区（自适应当前皮肤色盘与深浅对比度）
    r3_c1, r3_c2 = st.columns([1, 1])
    with r3_c1:
        st.markdown('<div class="stats-chart-title">📈 各知识板块题目分布</div>', unsafe_allow_html=True)
        subj_counts = stats.get("subject_counts", {})
        components.html(generate_echarts_bar_html(subj_counts, "各知识板块题目分布", skin=cur_skin, mode=cur_mode), height=370)

    with r3_c2:
        st.markdown('<div class="stats-chart-title">🍰 题型占比分布 & 难度分布</div>', unsafe_allow_html=True)
        type_counts = stats.get("type_counts", {})
        diff_counts = stats.get("difficulty_dist", {})
        components.html(generate_echarts_pie_html(type_counts, diff_counts, "题型与难度分布", skin=cur_skin, mode=cur_mode), height=370)

# ================= 页面：规范说明 =================
def page_manual():
    st.header("📖 题库规范说明")
    st.markdown("""
    **📂 一、 文件命名规范**

    所有题目的 `.tex` 文件必须严格按照以下 **“五段式”** 结构命名，各部分之间使用英文连字符 `-` 连接，格式为：
    `<font color="red">**年份-试卷类别-试卷名称-题号-知识板块.tex**</font>`
    *示例：`2024-G-新高考I卷-12-牛顿运动定律，功与能量.tex`*
    - **年份**：四位纯数字（如 `2024`）；
    - **试卷类别**：必须是系统预设的缩写代码，仅限 `G`(高考题)、`M`(模拟题)、`W`(外国题)、`XK`(学考题)、`XS`(线上联考)；
    - **试卷名称**：明确试卷全称，如 `新课标I卷`、`浙江学考` 等，尽量避免包含特殊符号；
    - **题号**：纯数字（如 `12`）；
    - **知识板块**：如涉及多个板块，必须用**中文全角逗号 `，`** 分隔，且将**最核心的主板块放在最前**（如 `牛顿运动定律，功与能量`）。

    **📝 二、 LaTeX 源码书写格式**

    每个题目文件内部必须且仅包含一个完整的 `problem` 环境：
    ```latex
    \\begin{problem}{年份}{试卷类别}{试卷名称}{题号}{知识板块}
    这里是具体的题目题干内容...
    \\end{problem}
    ```
    如果题目附带详细解析，请使用 `\\begin{answer}` 和 `\\begin{solutions}` 环境：
    ```latex
    \\begin{answer}
    这里是答案...
    \\end{answer}

    \\begin{solutions}
    这里是解析...
    \\end{solutions}
    ```

    **💡 三、 附加规范**
    - **选择题**：请使用 `\\begin{choices}` 与 `\\choice{{选项内容}}` 宏包结构，务必确保选项内容被**两层大括号**包裹。
    - **TikZ 绘图**：直接在题干中插入 `\\begin{tikzpicture}...\\end{tikzpicture}` 代码，系统会自动提取。
    - **插图颜色**：模板已内置 `primaryblue`、`meterblue`、`accentorange` 等物理插图语义色别名（完整清单见 `UtopiaPhyCyclus_book.cls` 颜色定义段），也可使用 xcolor 基础色（red/blue/green/orange/cyan/gray 等）。AI 生成的其他未定义颜色会导致编译失败；TeX 录入的「编译诊断与修复建议」会给出来源行号，按提示改用已定义颜色或先 `\\definecolor` 即可。
    """, unsafe_allow_html=True)

def page_system_intro():
    st.header("📘 项目体系介绍（录入 · 浏览 · 标签 · 组卷）")
    st.markdown("""
### 🎯 这套系统解决什么问题？

这是一套面向高中物理的题库项目管理系统：以 `.tex` 为数据单元，把“题目内容、标签元数据、索引检索、批量维护、组卷导出”统一在一个可视化工作流里，做到：

- 🧱 题目文件结构规范、可长期维护
- 🔎 检索与定位迅速（多维筛选/全文查找/三级查找）
- 🏷️ 标签与元数据可视化修改（并自动同步文件名/索引）
- 🖨️ 支持按模板组卷导出
- 🧠 可选的 AI 辅助：图片转写、标签提取、解答生成

---

### 📚 0) 关于本项目

**GitHub 项目链接：** [UtopiaPhyCyclus（物理版）](https://github.com/YJW2000/UtopiaPhyCyclus)

**原作与致谢：** 本项目基于 JinLingxi（槿灵兮）开发的 [MathCyclus 数学题库助手](https://github.com/JinLingxi/MathCyclus---Lingxi-Question-Bank-Assistant) 改造，保留原作的 MIT 许可与署名。物理版由 YJW2000 维护。

**许可与源码：** 项目代码采用 MIT；包含 PyMuPDF/MuPDF 的完整发行版遵循其 AGPL-3.0 许可要求。许可全文、依赖源码入口与构建脚本见仓库的 LICENSE、COPYING-AGPL-3.0、THIRD_PARTY_NOTICES.md 和 tools/release.py。发行版不包含内部题库。

**历史资料：** [原 MathCyclus 数学版 AI Works 页面](https://www.zhihu.com/project/detail/180133)

**原作者创作感想文章：** [MathCyclus 原作者的创作感想](https://www.zhihu.com/question/2052717294719956381/answer/2053305696041677288)

欢迎加入用户群沟通交流。
    """, unsafe_allow_html=True)

    user_group_img_path = os.path.join(BASE_DIR, "fig", "用户群.png")
    if os.path.exists(user_group_img_path):
        st.image(user_group_img_path, caption="用户群", width=260)

    st.markdown("""
### 🗂️ 1) 数据与目录结构（“文件即数据库”）

**核心原则：`.tex` 文件是单一事实来源（Source of Truth）。**

- 每道题对应一个 `.tex` 文件
- 物理归档采用 “知识板块/年份/文件.tex” 的层级组织
- 题内的标签与元数据会被写入专用的 Label Data 注释块，确保题目“自描述”

---

### 🧾 2) 文件命名规则（五段式，强约束）

文件名必须严格使用：

`年份-试卷类别-试卷名称-题号-知识板块.tex`

示例：`2024-G-全国I卷-12-电磁感应，电路.tex`

- 📅 年份：四位数字（如 `2024`）
- 🧩 试卷类别：系统预设代码（如 `G/M/W/XK/XS`）
- 🧾 试卷名称：建议使用普通文字，避免特殊符号
- 🔢 题号：纯数字（如 `12`）
- 🧠 知识板块：多标签用中文全角逗号 `，` 分隔；**首个为主板块**（决定题目归档目录）

---

### ✍️ 3) LaTeX 内容结构（题干、答案、解析）

每个文件内部必须且仅包含一个 `problem` 环境，且 5 个参数与文件名五段信息一致：

```latex
\\begin{problem}{年份}{试卷类别}{试卷名称}{题号}{知识板块}
题干内容...
\\end{problem}
```

如果有答案与解析（推荐），放在 `\\end{problem}` 之后：

```latex
\\begin{answer}
最终答案
\\end{answer}

\\begin{solutions}
解析步骤...
\\end{solutions}
```

补充约定：

- 🧩 选择题：用 `choices/choice` 结构（选项内容必须双层大括号）
- 🧑‍🎨 TikZ：可直接写在题干内；系统会在保存/维护过程中自动处理相关图资源

---

### 🏷️ 4) ID / 难度 / 标签：Label Data 元数据机制

为了让题目文件可长期维护与可追溯，系统把元数据写在题目文件内部的注释块中：

- 🆔 ID：每题唯一标识（用于跨文件移动/重命名时保持引用稳定）
- ⭐ 难度星级：0–6（支持 0.5 步长）
- 🏷️ 标签：自定义标签（与知识板块不同，偏“属性标签”）
- 📝 备注：人工补充说明
- 📌 组卷引用次数：用于统计与推荐

这些字段存储在题目文件里的 `% === Begin Label Data === ... % === End Label Data ===` 区块中。

---

### ⚡ 5) CSV 索引：加速检索的缓存层

系统会维护一个 `utils/题库索引表.csv` 作为“高速缓存索引”来提升检索速度：

- 🔁 索引可通过扫描全库重新生成（以 `.tex` 为准）
- ✅ 即便 CSV 丢失，也能依靠 `.tex` 里的 Label Data 重新构建
- 🚀 浏览、三级查找、统计面板优先读取 CSV，避免每次全量扫描 `.tex`
- 🧯 CSV 写入前会做基础校验，降低重复 ID、关键字段缺失导致索引损坏的风险

（对应脚本：`utils/init_csv_index.py`）

---

### 🧱 6) 工程模块分工（给开源读者的快速地图）

如果你是第一次阅读这个项目，可以按下面的层次理解代码：

- `utopia_phycyclus_app.py`：主应用入口，负责 Streamlit 页面、交互状态、业务流程串联
- `utils/core_config.py`：全局路径、试卷类型、知识板块等基础配置
- `utils/csv_ops.py`：题库索引的读取、写入、增量更新与字段解析
- `utils/latex_ops.py`：LaTeX 题目结构处理、TikZ 提取、题目重命名与保存辅助
- `utils/tikz_ops.py`：TikZ 编译、缓存与预览渲染
- `utils/charts.py`：数据统计页面的图表渲染
- `services/file_service.py`：原子写入、覆盖备份等文件安全能力
- `services/ai_service.py`：AI 接口地址规范化、请求封装与 JSON 提取
- `services/question_service.py`：题目创建、查重指纹和重复题扫描
- `services/operation_log.py`：批量操作记录与维护审计
- `Test Paper Group/主题模板/`：组卷导出的 LaTeX 模板来源

整体设计思路是：主应用负责“把流程跑通”，工具层负责“把单个动作做稳”，题目数据始终落回 `.tex` 文件。

---

### 🛡️ 7) 稳定性与数据安全设计

题库项目的核心风险不是页面显示，而是“误覆盖、索引错乱、题目 ID 重复、批量操作难回退”。因此系统内部做了几层保护：

- `.tex` 是最终可信数据源，CSV 只是可重建的高速索引
- 覆盖保存时使用原子写入，避免写到一半导致文件损坏
- 修改既有题目或批量处理时，会尽量在 `.backups/` 中保留覆盖前副本
- CSV 写入前会检查关键字段与重复 ID，发现异常时阻止写入
- 搜索缓存会跟随 CSV 文件变化自动失效，减少“刚保存但搜索不到”的情况

如果出现搜索结果异常、统计不准确、题目移动后找不到，优先执行“工具箱”中的一键重建/同步题库索引。

---

### 🧭 8) 日常使用工作流（给协作者的最短路径）

**📝 AI 录入 / 📄 TeX 录入**

- 支持单题/批量/同卷录入
- 单题录入支持实时预览、查找替换、AI 自动打标签
- 可选 “本次录入同时生成解答”，并可选 “快速模式”

**🔍 全局浏览与编辑**

- 面向“找题 + 改题”的主工作台
- 支持预览、源码编辑保存、AI 生成解答、加入/移除组卷
- 在标签修改面板中可改年份/试卷类别/试卷名/题号/知识板块，并自动同步文件名与索引

**🔎 三级查找**

- 面向“多条件精确过滤”的检索入口
- 适合做专题筛选、交叉检索与快速定位

**🛠️ 工具箱**

- 面向“全库/批量维护”的工具集合
- 适合做批量规范化、批量修复、批量结构调整、题目查重和操作记录查看

**🖨️ 组卷服务**

- 以模板为核心的排版与导出流程
- 读取 `Test Paper Group/主题模板/` 下的主题模板，并在导出目录生成成品

---

### 🧩 9) 推荐的维护习惯

为了让题库长期可维护，建议按下面的方式协作：

- 新题录入后先检查预览，再补充难度、标签、备注
- 不手动复制已有题目的 ID；ID 应保持唯一
- 不直接把 CSV 当主数据库编辑；需要修复时优先重建索引
- 批量改名、批量修复前，先确认目标范围
- 导出文件、LaTeX 编译产物、临时缓存不应作为题库核心数据维护
- 修改规则类代码后，至少检查一次录入、搜索、标签修改和组卷导出主流程

---

### 🔭 10) 适合二次开发的方向

这个项目的后续扩展可以围绕“题库质量”和“教研效率”展开：

- 更细的标签体系：考点、方法、易错点、能力层级
- 更稳定的批量导入校验：录入前预检文件名、题号、ID、题型
- 更智能的组卷策略：按难度、知识点覆盖率、近年频次进行约束
- 更清晰的题目版本记录：记录每道题的修改历史与来源变化
- 更完整的本地部署方案：后续可再考虑封装为桌面应用或 exe

""", unsafe_allow_html=False)


# ================= 页面：三级查找 =================
# ================= 页面：三级查找嵌入组件 =================
def render_advanced_search_inline(compact=False, search_result_count=None, mode_container=None):
    results_page_size_options = [5, 10, 15, 20]
    default_results_page_size = 10
    if st.session_state.get("adv_results_page_size") not in (None, *results_page_size_options):
        del st.session_state["adv_results_page_size"]
    st.markdown("""
    <style>
    div[data-testid="stMarkdownContainer"]:has(#adv-search-inputs-anchor),
    div[data-testid="stMarkdownContainer"]:has(#adv-search-compact-inputs-anchor),
    div[data-testid="stMarkdownContainer"]:has(#adv-search-btn-anchor),
    div[data-testid="stMarkdownContainer"]:has(#adv-search-submit-anchor),
    div[data-testid="stMarkdownContainer"]:has(#adv-search-info-anchor),
    div[data-testid="stMarkdownContainer"]:has(#adv-search-compact-btn-anchor),
    div[data-testid="stMarkdownContainer"]:has(#adv-search-compact-info-anchor),
    div[data-testid="stMarkdownContainer"]:has(#adv-search-pager-anchor) {
        display: none !important;
        margin: 0 !important;
        padding: 0 !important;
        height: 0 !important;
    }
    div[data-testid="stHorizontalBlock"]:has(#adv-search-inputs-anchor) {
        display: grid !important;
        grid-template-columns: minmax(0, 1.55fr) minmax(340px, 1fr) !important;
        width: 100% !important;
        min-width: 0 !important;
        align-items: stretch !important;
        gap: 1rem !important;
    }
    div[data-testid="stHorizontalBlock"]:has(#adv-search-inputs-anchor) > div[data-testid="column"] {
        min-width: 0 !important;
        width: auto !important;
        max-width: none !important;
        flex: initial !important;
    }
    div[data-testid="column"]:has(#adv-search-inputs-anchor) {
        overflow: hidden !important;
        margin-top: 0 !important;
        padding-top: 0 !important;
    }
    div[data-testid="column"]:has(#adv-search-inputs-anchor) div[data-testid="stHorizontalBlock"] {
        display: grid !important;
        grid-template-columns: minmax(0, 1fr) minmax(0, 2fr) !important;
        width: 100% !important;
        min-width: 0 !important;
        align-items: center !important;
        gap: 0.75rem !important;
        margin: 0 0 0.75rem !important;
    }
    div[data-testid="column"]:has(#adv-search-inputs-anchor) div[data-testid="stHorizontalBlock"] > div[data-testid="column"],
    div[data-testid="column"]:has(#adv-search-inputs-anchor) div[data-testid="stElementContainer"] {
        min-width: 0 !important;
        width: auto !important;
        max-width: none !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    div[data-testid="column"]:has(#adv-search-inputs-anchor) div[data-testid="stSelectbox"],
    div[data-testid="column"]:has(#adv-search-inputs-anchor) div[data-testid="stTextInput"],
    div[data-testid="column"]:has(#adv-search-inputs-anchor) div[data-baseweb="select"] {
        width: 100% !important;
        max-width: none !important;
    }
    div[data-testid="column"]:has(#adv-search-inputs-anchor) div[data-baseweb="select"] > div,
    div[data-testid="column"]:has(#adv-search-inputs-anchor) div[data-testid="stTextInput"] input {
        min-height: 42px !important;
        height: 42px !important;
    }
    div[data-testid="column"]:has(#adv-search-inputs-anchor) div[data-testid="stTextInput"] input {
        color: var(--mc-text) !important;
        caret-color: var(--mc-text) !important;
    }
    div[data-testid="column"]:has(#adv-search-inputs-anchor) div[data-testid="stButton"] > button {
        width: 100% !important;
        min-width: 0 !important;
        max-width: none !important;
        height: 44px !important;
        min-height: 44px !important;
        max-height: 44px !important;
        margin-top: 0.1rem !important;
        padding: 0 1rem !important;
        display: flex !important;
        flex-direction: row !important;
        align-items: center !important;
        justify-content: center !important;
        white-space: nowrap !important;
    }
    div[data-testid="column"]:has(#adv-search-btn-anchor) {
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        height: 196px !important;
        min-height: 196px !important;
        max-height: 196px !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    div[data-testid="column"]:has(#adv-search-btn-anchor) > div[data-testid="stVerticalBlock"] {
        width: 100% !important;
        height: 100% !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        gap: 0 !important;
    }
    div[data-testid="column"]:has(#adv-search-btn-anchor) button {
        width: 96px !important;
        min-width: 96px !important;
        max-width: 96px !important;
        height: 176px !important;
        min-height: 176px !important;
        max-height: 176px !important;
        padding: 0.75rem !important;
        display: flex !important;
        flex-direction: column !important;
        align-items: center !important;
        justify-content: center !important;
        border-radius: 10px !important;
        white-space: normal !important;
    }
    div[data-testid="column"]:has(#adv-search-btn-anchor) button p {
        white-space: pre-wrap !important;
        word-break: keep-all !important;
        text-align: center !important;
        line-height: 1.35 !important;
        margin: 0 !important;
    }
    div[data-testid="column"]:has(#adv-search-info-anchor) {
        display: flex !important;
        align-items: flex-start !important;
        height: 196px !important;
        min-height: 196px !important;
        max-height: 196px !important;
        padding: 0 !important;
    }
    div[data-testid="column"]:has(#adv-search-info-anchor) div[data-testid="stAlert"] {
        width: 100% !important;
        min-height: 64px !important;
        height: 64px !important;
        max-height: 64px !important;
        box-sizing: border-box !important;
        margin: 0 !important;
        box-shadow: none !important;
        filter: none !important;
        overflow: hidden !important;
    }
    div[data-testid="column"]:has(#adv-search-info-anchor) div[data-testid="stAlert"] > div {
        box-shadow: none !important;
        filter: none !important;
    }
    div[data-testid="column"]:has(#adv-search-left-anchor) div[data-testid="column"]:has(#adv-search-btn-anchor),
    div[data-testid="column"]:has(#adv-search-left-anchor) div[data-testid="column"]:has(#adv-search-info-anchor) {
        height: auto !important;
        min-height: 0 !important;
        max-height: none !important;
    }
    div[data-testid="column"]:has(#adv-search-left-anchor) div[data-testid="column"]:has(#adv-search-btn-anchor) button {
        width: 100% !important;
        min-width: 0 !important;
        max-width: none !important;
        height: 48px !important;
        min-height: 48px !important;
        max-height: 48px !important;
    }
    div[data-testid="column"]:has(#adv-search-left-anchor) div[data-testid="column"]:has(#adv-search-info-anchor) div[data-testid="stAlert"] {
        height: auto !important;
        min-height: 64px !important;
        max-height: none !important;
    }
    div[data-testid="column"]:has(#adv-search-left-anchor) div[data-testid="stButton"] > button {
        width: 100% !important;
        min-width: 0 !important;
        max-width: none !important;
        min-height: 44px !important;
        height: 44px !important;
        max-height: 44px !important;
        padding: 0 1rem !important;
        display: flex !important;
        flex-direction: row !important;
        align-items: center !important;
        justify-content: center !important;
        border-radius: 10px !important;
        white-space: nowrap !important;
    }
    div[data-testid="column"]:has(#adv-search-left-anchor) div[data-testid="stButton"] > button p {
        white-space: nowrap !important;
        word-break: keep-all !important;
        line-height: 1.2 !important;
        margin: 0 !important;
    }
    div[data-testid="column"]:has(#adv-search-left-anchor) div[data-testid="stRadio"] div[role="radiogroup"] {
        display: flex !important;
        flex-direction: row !important;
        flex-wrap: nowrap !important;
        align-items: center !important;
        gap: 0.35rem !important;
    }
    div[data-testid="column"]:has(#adv-search-left-anchor) div[data-testid="stRadio"] label {
        width: auto !important;
        min-width: 0 !important;
        margin: 0 !important;
        padding-right: 0.2rem !important;
        white-space: nowrap !important;
    }
    div[data-testid="column"]:has(#adv-search-left-anchor) div[data-testid="stRadio"] label p {
        white-space: nowrap !important;
        font-size: 0.92rem !important;
        line-height: 1.15 !important;
    }
    .mc-adv-result-summary {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 0.75rem;
        margin: 1rem 0 0.75rem;
    }
    .mc-adv-result-summary h3 {
        margin: 0;
        font-size: 1.16rem;
        line-height: 1.25;
    }
    .mc-adv-result-summary span {
        color: #4c1d95;
        font-weight: 750;
        font-size: 0.98rem;
        white-space: nowrap;
    }
    div[data-testid="stHorizontalBlock"]:has(#adv-search-pager-anchor) {
        display: grid !important;
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) !important;
        gap: 0.75rem !important;
        align-items: end !important;
    }
    div[data-testid="stHorizontalBlock"]:has(#adv-search-pager-anchor) > div[data-testid="column"] {
        width: auto !important;
        min-width: 0 !important;
        max-width: none !important;
        flex: initial !important;
    }
    div[data-testid="stHorizontalBlock"]:has(#adv-search-pager-anchor) div[data-testid="stSelectbox"],
    div[data-testid="stHorizontalBlock"]:has(#adv-search-pager-anchor) div[data-testid="stNumberInput"],
    div[data-testid="stHorizontalBlock"]:has(#adv-search-pager-anchor) div[data-baseweb="select"] {
        width: 100% !important;
        min-width: 0 !important;
    }
    @media (max-width: 900px) {
        div[data-testid="stHorizontalBlock"]:has(#adv-search-inputs-anchor) {
            grid-template-columns: minmax(0, 1fr) !important;
            gap: 0.75rem !important;
        }
        div[data-testid="column"]:has(#adv-search-btn-anchor),
        div[data-testid="column"]:has(#adv-search-info-anchor) {
            height: auto !important;
            min-height: 0 !important;
            max-height: none !important;
        }
        div[data-testid="column"]:has(#adv-search-btn-anchor) button {
            width: 100% !important;
            min-width: 0 !important;
            max-width: none !important;
            height: 48px !important;
            min-height: 48px !important;
            max-height: 48px !important;
        }
    }
    </style>
    """, unsafe_allow_html=True)

    def on_adv_search():
        if not _adv_search_has_query():
            st.session_state["adv_search_active"] = False
            st.toast("请输入至少一个关键词后再开始查找。", icon="⚠️")
            return
        st.session_state["adv_search_active"] = True
        st.session_state["adv_results_page"] = 1
        st.session_state["adv_results_page_size"] = 10

    if compact:
        if st.button("❌ 退出检索", use_container_width=True):
            st.session_state["adv_search_active"] = False
            st.rerun()

    if mode_container is not None:
        # 浏览页：与「浏览模式」同一行，使用行内小标签，节省一行高度
        with mode_container:
            _lbl, _opts = st.columns([0.22, 1], gap="small")
            with _lbl:
                st.markdown('<div class="mc-inline-mode-label">检索模式</div>', unsafe_allow_html=True)
            with _opts:
                search_mode = st.radio(
                    "检索模式",
                    ["精确筛选", "混合搜索", "语义搜索"],
                    horizontal=True,
                    label_visibility="collapsed",
                    key="adv_search_mode",
                )
    else:
        # 检索工作区（窄侧栏）：保持标签在上方的原始形态，避免换行
        search_mode = st.radio(
            "检索模式",
            ["精确筛选", "混合搜索", "语义搜索"],
            horizontal=True,
            key="adv_search_mode",
        )
    if search_mode != "精确筛选":
        st.text_input(
            "语义描述",
            placeholder="例如：传送带模型中的临界条件与能量转化",
            key="adv_semantic_query",
            on_change=on_adv_search,
        )

    search_opts = ["全文内容", "题目类型", "题目内容", "解答内容", "难度星级", "其他自由标签", "知识点", "物理方法"]

    if compact:
        st.markdown('<div id="adv-search-compact-inputs-anchor"></div>', unsafe_allow_html=True)
        t1 = st.selectbox("一级类型", search_opts, index=0, key="adv_t1")
        if t1 == "题目类型":
            q1 = st.selectbox("一级关键词", QUESTION_TYPES, key="adv_q1_sel", on_change=on_adv_search)
        else:
            q1 = st.text_input("一级关键词", placeholder="输入一级关键词...", key="adv_q1", on_change=on_adv_search)

        t2 = st.selectbox("二级类型", search_opts, index=0, key="adv_t2")
        if t2 == "题目类型":
            q2 = st.selectbox("二级关键词", QUESTION_TYPES, key="adv_q2_sel", on_change=on_adv_search)
        else:
            q2 = st.text_input("二级关键词", placeholder="输入二级关键词...", key="adv_q2", on_change=on_adv_search)

        t3 = st.selectbox("三级类型", search_opts, index=0, key="adv_t3")
        if t3 == "题目类型":
            q3 = st.selectbox("三级关键词", QUESTION_TYPES, key="adv_q3_sel", on_change=on_adv_search)
        else:
            q3 = st.text_input("三级关键词", placeholder="输入三级关键词...", key="adv_q3", on_change=on_adv_search)

        st.markdown('<div id="adv-search-compact-btn-anchor"></div>', unsafe_allow_html=True)
        st.button("🔎 再次搜索", use_container_width=True, type="primary", on_click=on_adv_search)

        st.markdown('<span id="adv-search-compact-info-anchor"></span>', unsafe_allow_html=True)
        semantic_query = st.session_state.get("adv_semantic_query", "") if search_mode != "精确筛选" else ""
        if not (st.session_state.get("adv_search_active") and (q1 or q2 or q3 or semantic_query)):
            st.info("输入查找条件后开始查找。")
            return

        search_info = []
        if q1: search_info.append(f"{t1}: `{q1}`")
        if q2: search_info.append(f"{t2}: `{q2}`")
        if q3: search_info.append(f"{t3}: `{q3}`")
        if semantic_query: search_info.append(f"语义: `{semantic_query}`")
        search_info.append(f"模式: `{search_mode}`")
        st.markdown(f"**检索条件**: {' | '.join(search_info)}")

        if search_result_count is not None:
            st.markdown(
                f"""
                <div class="mc-adv-result-summary">
                    <h3>🎯 查找结果</h3>
                    <span>找到 {search_result_count} 个匹配题目</span>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if search_result_count:
                pager_size_col, pager_page_col = st.columns([1, 1], gap="small")
                with pager_size_col:
                    st.markdown('<span id="adv-search-pager-anchor"></span>', unsafe_allow_html=True)
                    if "adv_results_page_size" not in st.session_state:
                        st.session_state["adv_results_page_size"] = results_page_size_options[1]
                    page_size = st.selectbox("每页显示", options=results_page_size_options, key="adv_results_page_size")
                total_pages = max(1, (search_result_count + page_size - 1) // page_size)
                current_results_page = int(st.session_state.get("adv_results_page", 1) or 1)
                current_results_page = max(1, min(total_pages, current_results_page))
                st.session_state["adv_results_page"] = current_results_page
                with pager_page_col:
                    page = st.number_input("页码", min_value=1, max_value=total_pages, step=1, key="adv_results_page")
                start = (page - 1) * page_size
                end = min(search_result_count, start + page_size)
                st.caption(f"当前显示：第 {start + 1}–{end} 条 / 共 {search_result_count} 条")
            else:
                st.warning("未找到匹配的题目。")
        return

    # —— 紧凑检索区：三组「类型 + 关键词」并排，按钮紧跟在同一行 ——
    st.markdown(
        """
        <style>
          /* 页面标题与分区标题压缩，减少顶部空白 */
          div[data-testid="stMainBlockContainer"] h1,
          div[data-testid="stMainBlockContainer"] h2 {
            margin: 0.1rem 0 0.35rem !important;
            font-size: 1.62rem !important;
            line-height: 1.25 !important;
          }
          div[data-testid="stMainBlockContainer"] h3 {
            margin: 0.1rem 0 0.25rem !important;
            font-size: 1.28rem !important;
            line-height: 1.3 !important;
          }
          /* 浏览模式 / 检索模式：行内紧凑，去掉多余上下留白 */
          div[data-testid="stHorizontalBlock"]:has(#adv-search-inputs-anchor) {
            align-items: end !important;
            gap: 0.55rem !important;
            margin-top: 0.15rem !important;
          }
          div[data-testid="stHorizontalBlock"]:has(#adv-search-inputs-anchor) div[data-testid="stRadio"] > div[role="radiogroup"] {
            gap: 0.55rem !important;
          }
          div[data-testid="stHorizontalBlock"]:has(#adv-search-inputs-anchor) div[data-testid="stRadio"] label {
            margin: 0 !important;
            padding: 0 !important;
          }
          div[data-testid="stHorizontalBlock"]:has(#adv-search-inputs-anchor) div[data-testid="stRadio"] label p {
            font-size: 0.9rem !important;
            line-height: 1.2 !important;
          }
          /* 输入行内部：类型下拉与关键词框并排，行高统一 38px */
          div[data-testid="stHorizontalBlock"]:has(#adv-search-inputs-anchor) div[data-testid="stSelectbox"] > div,
          div[data-testid="stHorizontalBlock"]:has(#adv-search-inputs-anchor) div[data-testid="stTextInput"] input {
            min-height: 38px !important;
            height: 38px !important;
          }
          div[data-testid="column"]:has(#adv-search-submit-anchor) div[data-testid="stButton"] > button {
            height: 38px !important;
            min-height: 38px !important;
            margin: 0 !important;
          }
          /* 模式行与检索区之间的分隔线收窄 */
          div[data-testid="stMainBlockContainer"] hr {
            margin: 0.4rem 0 0.5rem !important;
          }
          /* 行内小标题（检索模式） */
          .mc-inline-mode-label {
            font-size: 0.86rem;
            line-height: 38px;
            color: rgba(38, 35, 30, 0.62);
            white-space: nowrap;
          }
          /* 锚点容器本身不占高度，避免多加一层留白 */
          div[data-testid="element-container"]:has(#adv-search-inputs-anchor),
          div[data-testid="element-container"]:has(#adv-search-info-anchor),
          div[data-testid="element-container"]:has(#adv-search-submit-anchor) {
            display: none !important;
          }
          /* 整页纵向间距压缩，信息密度更高 */
          div[data-testid="stMainBlockContainer"] div[data-testid="stVerticalBlock"] {
            gap: 0.55rem !important;
          }
          div[data-testid="stMainBlockContainer"] div[data-testid="stHorizontalBlock"] {
            gap: 0.5rem !important;
          }
          div[data-testid="stMainBlockContainer"] div[data-testid="element-container"] {
            margin-bottom: 0 !important;
          }
          div[data-testid="stMainBlockContainer"] div[data-testid="stCaptionContainer"] {
            margin: 0 !important;
          }
        </style>
        <div id="adv-search-inputs-anchor"></div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3, c_btn = st.columns([1.08, 1.08, 1.08, 0.6], gap="small")

    def _render_filter_group(column, level: int):
        tc, tq = column.columns([1, 1.55], gap="small")
        tkey = f"adv_t{level}"
        with tc:
            t_val = st.selectbox(
                f"{['一', '二', '三'][level - 1]}级类型",
                search_opts,
                index=0,
                key=tkey,
                label_visibility="collapsed",
            )
        with tq:
            if t_val == "题目类型":
                q_val = st.selectbox(
                    f"{['一', '二', '三'][level - 1]}级关键词",
                    QUESTION_TYPES,
                    key=f"adv_q{level}_sel",
                    label_visibility="collapsed",
                    on_change=on_adv_search,
                )
            else:
                q_val = st.text_input(
                    f"{['一', '二', '三'][level - 1]}级关键词",
                    placeholder=f"输入{['一', '二', '三'][level - 1]}级关键词...",
                    key=f"adv_q{level}",
                    label_visibility="collapsed",
                    on_change=on_adv_search,
                )
        return t_val, q_val

    with c1:
        t1, q1 = _render_filter_group(c1, 1)
    with c2:
        t2, q2 = _render_filter_group(c2, 2)
    with c3:
        t3, q3 = _render_filter_group(c3, 3)

    with c_btn:
        st.markdown('<div id="adv-search-submit-anchor"></div>', unsafe_allow_html=True)
        st.button("🔎 开始查找", type="primary", use_container_width=True, on_click=on_adv_search)

    st.markdown('<span id="adv-search-info-anchor"></span>', unsafe_allow_html=True)
    q1 = st.session_state.get("adv_q1_sel" if t1 == "题目类型" else "adv_q1", "")
    q2 = st.session_state.get("adv_q2_sel" if t2 == "题目类型" else "adv_q2", "")
    q3 = st.session_state.get("adv_q3_sel" if t3 == "题目类型" else "adv_q3", "")
    semantic_query = st.session_state.get("adv_semantic_query", "") if search_mode != "精确筛选" else ""

    if not (st.session_state.get("adv_search_active") and (q1 or q2 or q3 or semantic_query)):
        st.caption("👈 填好条件后点「开始查找」（或在输入框里按回车）即可在下方看到结果。")
        return

    # 动态生成搜索信息提示
    search_info = []
    if q1:
        search_info.append(f"{t1}: `{q1}`")
    if q2:
        search_info.append(f"{t2}: `{q2}`")
    if q3:
        search_info.append(f"{t3}: `{q3}`")
    if semantic_query:
        search_info.append(f"语义: `{semantic_query}`")
    search_info.append(f"模式: `{search_mode}`")
    search_str = " | ".join(search_info)
    st.markdown(f"**检索条件**: {search_str}")
    if st.button("❌ 退出搜索状态"):
        st.session_state["adv_search_active"] = False
        st.rerun()

def render_advanced_search_workspace(is_delete_mode=False, paper_type_scope=None):
    results = _get_advanced_search_results(is_delete_mode=is_delete_mode, paper_type_scope=paper_type_scope)
    c_search, c_results = st.columns([0.72, 2.58], gap="large")
    with c_search:
        st.markdown('<div id="adv-search-left-anchor"></div>', unsafe_allow_html=True)
        render_advanced_search_inline(compact=True, search_result_count=len(results))
    with c_results:
        st.markdown('<div id="adv-search-right-anchor"></div>', unsafe_allow_html=True)
        render_advanced_search_results(is_delete_mode=is_delete_mode, paper_type_scope=paper_type_scope, results=results, controls_in_sidebar=True)

def _get_advanced_search_results(is_delete_mode=False, paper_type_scope=None):
    search_mode = st.session_state.get("adv_search_mode", "精确筛选")
    semantic_query = (st.session_state.get("adv_semantic_query", "") or "").strip() if search_mode != "精确筛选" else ""

    t1 = st.session_state.get("adv_t1", "全文内容")
    t2 = st.session_state.get("adv_t2", "全文内容")
    t3 = st.session_state.get("adv_t3", "全文内容")

    q1 = st.session_state.get("adv_q1_sel" if t1 == "题目类型" else "adv_q1", "")
    q2 = st.session_state.get("adv_q2_sel" if t2 == "题目类型" else "adv_q2", "")
    q3 = st.session_state.get("adv_q3_sel" if t3 == "题目类型" else "adv_q3", "")

    def _row_match(item, s_type, s_query):
        s_query = (s_query or "").strip()
        if not s_query:
            return True
        if s_type == "题目类型":
            return s_query == item["type"]
        if s_type == "题目内容":
            return s_query in item["stem"]
        if s_type == "解答内容":
            return s_query in item["solution"]
        if s_type == "难度星级":
            return s_query in item["difficulty"]
        if s_type in ("标签", "其他自由标签"):
            return s_query in item["tags"]
        if s_type == "知识点":
            return s_query in item["knowledge_points"]
        if s_type == "物理方法":
            return s_query in item["physics_methods"]
        if s_type == "备注":
            return s_query in item["remark"]
        if s_type == "全文内容":
            return s_query in item["full_text"]
        return False

    query_key = (
        search_mode,
        semantic_query,
        t1,
        q1,
        t2,
        q2,
        t3,
        q3,
        paper_type_scope or "regular",
        "delete" if is_delete_mode else "edit",
    )
    if st.session_state.get("adv_last_query") == query_key and st.session_state.get("adv_last_results") is not None:
        results = st.session_state.get("adv_last_results") or []
    else:
        search_rows = _advanced_search_index_cached(_csv_index_cache_token(), paper_type_scope)

        results = []
        with st.spinner("正在全库检索中..."):
            filtered_items = []
            for item in search_rows:
                if q1 and not _row_match(item, t1, q1):
                    continue
                if q2 and not _row_match(item, t2, q2):
                    continue
                if q3 and not _row_match(item, t3, q3):
                    continue
                fpath = item["path"]
                if not fpath or not os.path.exists(fpath):
                    continue
                filtered_items.append(item)

            if search_mode == "精确筛选" or not semantic_query:
                results = [
                    {
                        "file": item["file"] or os.path.basename(item["path"]),
                        "path": item["path"],
                        "reason": "字段匹配",
                    }
                    for item in filtered_items
                ]
            elif filtered_items:
                config = _semantic_api_config()
                row_map = {
                    (item["row"].get("相对文件路径", "") or "").replace("/", "\\"): item
                    for item in filtered_items
                }
                try:
                    semantic_matches = semantic_search(
                        semantic_query,
                        [item["row"] for item in filtered_items],
                        config["base_url"],
                        config["api_key"],
                        config["model_name"],
                        top_k=max(1, len(filtered_items)),
                    )
                    for match in semantic_matches:
                        relative_path = (match.get("path") or "").replace("/", "\\")
                        item = row_map.get(relative_path)
                        if not item:
                            continue
                        boost = _semantic_lexical_boost(_semantic_item_text(item), semantic_query) if search_mode == "混合搜索" else 0.0
                        results.append({
                            "file": item["file"] or os.path.basename(item["path"]),
                            "path": item["path"],
                            "score": match["score"] + boost,
                            "semantic_score": match["score"],
                            "reason": "语义 + 关键词" if boost else "语义相似",
                        })
                    results.sort(key=lambda result: result.get("score", 0.0), reverse=True)
                except SemanticSearchError as exc:
                    if search_mode == "混合搜索":
                        st.warning(f"语义检索暂不可用，已退回关键词匹配：{exc}")
                        for item in filtered_items:
                            fallback_score = _semantic_lexical_boost(_semantic_item_text(item), semantic_query)
                            if fallback_score <= 0:
                                continue
                            results.append({
                                "file": item["file"] or os.path.basename(item["path"]),
                                "path": item["path"],
                                "score": fallback_score,
                                "reason": "关键词回退",
                            })
                        results.sort(key=lambda result: result.get("score", 0.0), reverse=True)
                    else:
                        st.error(f"语义检索不可用：{exc}")

        st.session_state["adv_last_query"] = query_key
        st.session_state["adv_last_results"] = results

    return results


def render_advanced_search_results(is_delete_mode=False, paper_type_scope=None, results=None, controls_in_sidebar=False):
    results_page_size_options = [5, 10, 15, 20]
    default_results_page_size = 10
    if not controls_in_sidebar and st.session_state.get("adv_results_page_size") not in (None, *results_page_size_options):
        del st.session_state["adv_results_page_size"]
    results = _get_advanced_search_results(is_delete_mode=is_delete_mode, paper_type_scope=paper_type_scope) if results is None else results
    if not controls_in_sidebar:
        st.markdown(
            f"""
            <div style="display:flex;align-items:center;justify-content:space-between;gap:1rem;margin:0 0 0.75rem;">
                <h3 style="margin:0;">🎯 查找结果</h3>
                <span style="font-weight:700;color:var(--mc-accent-dark);">找到 {len(results)} 个匹配题目</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    if results:
        if controls_in_sidebar:
            page_size = int(st.session_state.get("adv_results_page_size", default_results_page_size) or default_results_page_size)
            if page_size not in results_page_size_options:
                page_size = default_results_page_size
        else:
            if "adv_results_page_size" not in st.session_state:
                st.session_state["adv_results_page_size"] = results_page_size_options[1]
            page_size = st.selectbox("每页显示", options=results_page_size_options, key="adv_results_page_size")
        total_pages = (len(results) + page_size - 1) // page_size
        current_results_page = int(st.session_state.get("adv_results_page", 1) or 1)
        current_results_page = max(1, min(max(1, total_pages), current_results_page))
        if controls_in_sidebar:
            page = current_results_page
        else:
            st.session_state["adv_results_page"] = current_results_page
            page = st.number_input("页码", min_value=1, max_value=max(1, total_pages), step=1, key="adv_results_page")

        start = (page - 1) * page_size
        end = min(len(results), start + page_size)
        if not controls_in_sidebar:
            st.caption(f"每页 {page_size} 题 · 当前第 {page} 页 · 显示第 {start + 1}–{end} 条 / 共 {len(results)} 条")

        for i, res in enumerate(results[start:end], start=start):
            fpath = res["path"]
            fname = res["file"]

            prepared_assets = None
            if is_delete_mode:
                content = read_question_text(fpath)
            else:
                prepared_assets = load_question_editor_assets(fpath)
                content = prepared_assets["content"]

            q_label = format_question_title(fname)
            if res.get("semantic_score") is not None:
                st.caption(f"{res.get('reason', '语义相似')} · 相关度 {max(-1.0, min(1.0, res['semantic_score'])):.0%}")
            elif res.get("reason") == "关键词回退":
                st.caption("关键词回退匹配")

            if is_delete_mode:
                render_delete_question_item(fpath, q_label, content, key_prefix="delete_search")
                st.divider()
                continue

            render_browse_question_editor_card(
                q_label,
                content,
                fpath,
                "adv_search",
                paper_type_scope=paper_type_scope,
                prepared_assets=prepared_assets,
                interactive_difficulty=True,
            )
            st.divider()
    else:
        st.warning("未找到匹配的题目。")

def page_advanced_search():
    st.header("🔎 三级查找")
    st.caption("一级先选检索字段再填关键词；二、三级可留空，也可继续缩小范围。")

    if st.session_state.get("adv_search_active") and _adv_search_has_query():
        render_advanced_search_workspace()
        return

    render_advanced_search_inline()
    st.markdown('<hr style="border-top: 1px solid #e1e4e8; margin-top: 10px; margin-bottom: 20px;">', unsafe_allow_html=True)
    if st.session_state.get("adv_search_active") and _adv_search_has_query():
        render_advanced_search_results()

# ================= 主程序 =================
def _mc_prof_enabled() -> bool:
    return os.getenv("MC_PROFILE", "") == "1"


def _mc_prof(label: str, started_at: float):
    if _mc_prof_enabled():
        try:
            import sys as _sys
            print(f"[MC_PROF] {label}: {(time.perf_counter() - started_at) * 1000:.0f} ms", file=_sys.stderr, flush=True)
        except Exception:
            pass


@st.cache_data(show_spinner=False, max_entries=4)
def _cached_logo_base64(logo_path: str, mtime: float = None) -> str:
    """侧边栏 Logo 的 base64 只读一次，避免每次 rerun 重复读取编码。"""
    try:
        if not os.path.exists(logo_path):
            return ""
        with open(logo_path, "rb") as img_f:
            return base64.b64encode(img_f.read()).decode()
    except Exception:
        return ""


def _format_nav_label(option: str) -> str:
    """侧边栏导航标签：图标 + 名称单行显示（对齐复盘软件侧栏排版）。

    含二级功能的入口（智能录题 / TeX 录入）在右侧追加展开箭头：
    当前就在该入口下时显示向上箭头，否则向下。
    """
    match = re.match(r"^(\S+)\s+(.+)$", option)
    if not match:
        return option
    icon, name = match.group(1), match.group(2).strip()
    if name in ("智能录题", "TeX录入", "设置"):
        # 箭头与页面状态无关，保证 format_func 是纯函数（AppTest 与 Streamlit 都要求）
        return f"{icon}  {name}  ⌄"
    return f"{icon}  {name}"


@st.cache_data(show_spinner=False)
def _cached_boot_icon_data_uri() -> str:
    """加载动画用的西西弗斯图标 data URI（与桌面快捷方式同一个 .ico/.png）。

    优先输出 WebP（体积小一个数量级），浏览器不支持时回退 PNG。
    每个会话只计算一次并缓存，避免每次 rerun 都读 2MB 大图。
    """
    candidates = [
        os.path.join(BASE_DIR, "fig", "UtopiaPhyCyclus_sisyphus_icon.png"),
        os.path.join(BASE_DIR, "fig", "UtopiaPhyCyclus_sisyphus_icon.ico"),
    ]
    try:
        from PIL import Image as _PILImage
        from io import BytesIO

        for path in candidates:
            if not os.path.exists(path):
                continue
            try:
                with _PILImage.open(path) as img:
                    rgba = img.convert("RGBA")
                    rgba.thumbnail((192, 192), _PILImage.LANCZOS)
                    for fmt, mime, kwargs in (
                        ("WEBP", "image/webp", {"quality": 86, "method": 6}),
                        ("PNG", "image/png", {"optimize": True}),
                    ):
                        try:
                            buf = BytesIO()
                            rgba.save(buf, format=fmt, **kwargs)
                            payload = base64.b64encode(buf.getvalue()).decode("ascii")
                            return f"data:{mime};base64,{payload}"
                        except Exception:
                            continue
            except Exception:
                continue
    except Exception:
        return ""
    return ""


_MC_BOOT_CSS = r"""
#mc-boot-loader {
    position: fixed !important;
    top: 0 !important;
    left: 0 !important;
    right: 0 !important;
    bottom: 0 !important;
    width: 100vw !important;
    height: 100vh !important;
    z-index: 2147483000 !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    background-color: #F7F4EC !important;
    background-image:
        radial-gradient(at 18% 12%, rgba(204, 120, 92, 0.16) 0px, transparent 58%),
        radial-gradient(at 82% 88%, rgba(214, 202, 185, 0.6) 0px, transparent 60%) !important;
    background-repeat: no-repeat !important;
    background-size: cover !important;
    transition: opacity 0.45s ease, visibility 0.45s ease !important;
    animation: mcBootFallback 0.4s ease 20s forwards !important;
}
body:has(#mc-boot-ready) #mc-boot-loader {
    opacity: 0 !important;
    visibility: hidden !important;
    pointer-events: none !important;
    transition: opacity 0.15s ease, visibility 0.15s ease !important;
}
.mc-boot-card {
    width: min(420px, 86vw);
    padding: 24px 26px 20px;
    border-radius: 26px;
    background: #FFFDF9;
    border: 1px solid rgba(214, 202, 185, 0.85);
    box-shadow: 0 30px 70px -24px rgba(120, 95, 60, 0.38);
    text-align: center;
    font-family: "Charter", "Georgia", "Noto Serif SC", "Songti SC", -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
}
.mc-boot-scene {
    position: relative;
    height: 168px;
    margin-bottom: 14px;
    overflow: hidden;
    border-radius: 18px;
    background: linear-gradient(180deg, #FFFDF9 0%, #F6EEE3 100%);
    border: 1px solid rgba(214, 202, 185, 0.55);
}
/* 山坡：右低左高，和图标上行方向一致 */
.mc-boot-hill {
    position: absolute;
    inset: 0;
    background: linear-gradient(180deg, #EFE0D0 0%, #E0CBB4 100%);
    clip-path: polygon(0% 100%, 46% 58%, 100% 20%, 100% 100%);
}
.mc-boot-glow {
    position: absolute;
    right: 40px;
    top: 8px;
    width: 98px;
    height: 98px;
    border-radius: 50%;
    background: radial-gradient(circle, rgba(245, 200, 122, 0.6) 0%, rgba(245, 200, 122, 0) 68%);
    animation: mcBootGlowPulse 3.2s ease-in-out infinite;
}
.mc-boot-slope {
    position: absolute;
    left: 4px;
    bottom: 34px;
    width: 330px;
    height: 2px;
    background-image: linear-gradient(90deg, #B98A6A 55%, transparent 0);
    background-size: 14px 2px;
    background-repeat: repeat-x;
    transform: rotate(-17deg);
    transform-origin: left center;
    opacity: 0.7;
}
/* 图标（桌面快捷方式同款）沿山坡上行 */
.mc-boot-hero {
    position: absolute;
    left: 26px;
    bottom: 20px;
    width: 88px;
    height: 88px;
    animation: mcBootClimb 5.6s cubic-bezier(0.45, 0, 0.55, 1) infinite;
}
.mc-boot-icon {
    display: block;
    width: 100%;
    height: 100%;
    object-fit: contain;
    /* 桌面快捷方式图标本身是深色方图，这里做成圆角 App 图标观感 */
    border-radius: 22px;
    background: #10182B;
    box-shadow:
        0 14px 26px -12px rgba(38, 35, 30, 0.55),
        0 0 0 1px rgba(255, 253, 249, 0.55),
        inset 0 0 0 1px rgba(255, 255, 255, 0.06);
    animation: mcBootPushBob 0.9s ease-in-out infinite;
}
.mc-boot-shadow {
    position: absolute;
    left: 6px;
    right: 6px;
    bottom: -6px;
    height: 10px;
    border-radius: 50%;
    background: radial-gradient(ellipse at center, rgba(90, 70, 48, 0.28) 0%, rgba(90, 70, 48, 0) 70%);
}
.mc-boot-boulder {
    width: 74px;
    height: 74px;
    margin: 8px auto 0;
    border-radius: 50%;
    background: radial-gradient(circle at 32% 28%, #9C8B77 0%, #6B5D4C 45%, #3B332A 100%);
    box-shadow: 0 6px 12px rgba(120, 95, 60, 0.35), inset -4px -5px 8px rgba(0, 0, 0, 0.25);
}
.mc-boot-dust {
    position: absolute;
    left: 30px;
    bottom: 22px;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: rgba(191, 169, 143, 0.9);
    animation: mcBootDust 5.6s ease-out infinite;
}
.mc-boot-title {
    font-size: 17px;
    font-weight: 800;
    letter-spacing: 0.4px;
    color: #26231E;
}
.mc-boot-sub {
    margin-top: 5px;
    font-size: 12px;
    color: #8A7F70;
    letter-spacing: 0.3px;
}
.mc-boot-bar {
    position: relative;
    height: 8px;
    margin: 16px 0 10px;
    border-radius: 9999px;
    background: rgba(214, 202, 185, 0.5);
    overflow: hidden;
}
.mc-boot-fill {
    position: absolute;
    inset: 0 auto 0 0;
    width: 0%;
    border-radius: 9999px;
    background: linear-gradient(90deg, #E3B29B 0%, #CC785C 55%, #B56345 100%);
    animation: mcBootFill 3.6s cubic-bezier(0.3, 0.1, 0.25, 1) infinite;
}
.mc-boot-fill::after {
    content: "";
    position: absolute;
    inset: 0;
    background: linear-gradient(90deg, transparent, rgba(255, 253, 249, 0.65), transparent);
    animation: mcBootShimmer 1.4s linear infinite;
}
.mc-boot-hint {
    font-size: 12px;
    color: #9A8E7E;
    letter-spacing: 0.4px;
}
.mc-boot-dots::after {
    content: "";
    animation: mcBootDots 1.6s steps(4, end) infinite;
}
@keyframes mcBootClimb {
    0%   { transform: translate(0, 0) rotate(0deg); opacity: 1; }
    70%  { transform: translate(190px, -58px) rotate(-3deg); opacity: 1; }
    80%  { transform: translate(196px, -60px) rotate(-3deg); opacity: 1; }
    88%  { transform: translate(196px, -60px) rotate(-3deg); opacity: 0.55; }
    100% { transform: translate(0, 0) rotate(0deg); opacity: 0; }
}
@keyframes mcBootPushBob {
    0%, 100% { transform: translateY(0) rotate(-1deg); }
    50%      { transform: translateY(-3px) rotate(1.2deg); }
}
@keyframes mcBootGlowPulse {
    0%, 100% { opacity: 0.65; }
    50%      { opacity: 1; }
}
@keyframes mcBootDust {
    0%, 60% { opacity: 0; transform: translate(0, 0) scale(0.6); }
    72%     { opacity: 0.55; transform: translate(-12px, 3px) scale(1); }
    100%    { opacity: 0; transform: translate(-28px, 9px) scale(1.7); }
}
@keyframes mcBootFill {
    0%   { width: 4%; }
    55%  { width: 68%; }
    82%  { width: 88%; }
    100% { width: 96%; }
}
@keyframes mcBootShimmer {
    from { transform: translateX(-100%); }
    to   { transform: translateX(100%); }
}
@keyframes mcBootDots {
    0%   { content: ""; }
    25%  { content: "."; }
    50%  { content: ".."; }
    75%  { content: "..."; }
}
@keyframes mcBootFallback {
    to { opacity: 0; visibility: hidden; }
}
"""

_MC_BOOT_MARKUP = """
<div id="mc-boot-loader">
  <div class="mc-boot-card">
    <div class="mc-boot-scene">
      <div class="mc-boot-hill"></div>
      <div class="mc-boot-glow"></div>
      <div class="mc-boot-slope"></div>
      <div class="mc-boot-hero">
        <div class="mc-boot-shadow"></div>
        <!--MC_BOOT_HERO-->
      </div>
      <div class="mc-boot-dust"></div>
    </div>
    <div class="mc-boot-title">乌托邦高中物理题库</div>
    <div class="mc-boot-sub">西西弗斯推石 · 力与功的永恒循环</div>
    <div class="mc-boot-bar"><div class="mc-boot-fill"></div></div>
    <div class="mc-boot-hint">正在装配题库引擎<span class="mc-boot-dots"></span></div>
  </div>
</div>
"""


def _mc_boot_loader_html() -> str:
    """首屏加载动画 HTML：桌面快捷方式同款西西弗斯图标沿山坡上行 + 进度条。"""
    icon_uri = _cached_boot_icon_data_uri()
    if icon_uri:
        hero = (
            '<img class="mc-boot-icon" alt="西西弗斯推石" '
            f'src="{icon_uri}" />'
        )
    else:
        hero = '<div class="mc-boot-boulder"></div>'
    return "<style>" + _MC_BOOT_CSS + "</style>" + _MC_BOOT_MARKUP.replace("<!--MC_BOOT_HERO-->", hero)


def main():
    _mc_main_t0 = time.perf_counter()
    page_icon_path = os.path.join(BASE_DIR, "static", "favicon.png")
    page_icon = None
    if os.path.exists(page_icon_path):
        try:
            from PIL import Image
            with Image.open(page_icon_path) as icon_image:
                page_icon = icon_image.copy()
        except Exception:
            page_icon = None
    st.set_page_config(
        page_title="乌托邦高中物理题库助手",
        page_icon=page_icon,
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # 首屏加载动画：西西弗斯推石 + 进度条（每个浏览器会话仅一次，不影响后续切页速度）
    _mc_show_boot = not st.session_state.get("_mc_boot_done", False)
    if _mc_show_boot:
        st.markdown(_mc_boot_loader_html(), unsafe_allow_html=True)

    # Voice starts on first use, without competing for ordinary startup resources.

    # Appearance is persisted through Settings, not transient URL parameters.
    if "skin" in st.query_params or "theme" in st.query_params:
        _remove_query_param("skin")
        _remove_query_param("theme")

    _mc_t = time.perf_counter()
    inject_custom_css()
    _mc_prof("inject_custom_css", _mc_t)

    stats_nav_option = "📊  数据统计"
    entry_nav_option = "📝  智能录题"
    tex_entry_nav_option = "📄  TeX录入"
    browse_nav_option = "🔍  全局浏览"
    exam_nav_option = "🖨️  组卷排版"
    tools_nav_option = "🛠️  维护工具"
    advanced_nav_option = "🔎  精确查找"
    intro_nav_option = "📘  架构介绍"
    manual_nav_option = "📖  规范指南"
    settings_nav_option = "⚙️  设置"
    legacy_browse_nav_option = "🔍\n全局浏览与编辑"

    nav_options = [
        stats_nav_option,
        entry_nav_option,
        tex_entry_nav_option,
        browse_nav_option,
        exam_nav_option,
        tools_nav_option,
        advanced_nav_option,
        intro_nav_option,
        manual_nav_option,
        settings_nav_option,
    ]
    default_nav_option = stats_nav_option

    legacy_nav_aliases = {
        'UI设置':settings_nav_option,
        '🎨 UI设置':settings_nav_option,
        '设置':settings_nav_option,
        "⚙️  API设置": settings_nav_option,
        "API设置": settings_nav_option,
        "⚙️  系统与API设置": settings_nav_option,
        "⚙️  系统API设置": settings_nav_option,
        "系统API设置": settings_nav_option,
        "🔑\nAPI设置": settings_nav_option,
        "🔑\n设置": settings_nav_option,
        "数据统计": stats_nav_option,
        "📊  数据统计分析": stats_nav_option,
        "📊\n数据统计": stats_nav_option,
        "📊\n统计": stats_nav_option,
        "录入新题": entry_nav_option,
        "AI录入": entry_nav_option,
        "📝  AI智能录题": entry_nav_option,
        "📝\nAI录入": entry_nav_option,
        "📝\n录题": entry_nav_option,
        "TeX录入": tex_entry_nav_option,
        "📄  TeX代码录入": tex_entry_nav_option,
        "📄\nTeX录入": tex_entry_nav_option,
        "📄\nTeX": tex_entry_nav_option,
        "全局浏览与编辑": browse_nav_option,
        "🔍  全局浏览与编辑": browse_nav_option,
        "全局浏览\n与编辑": browse_nav_option,
        "🔍\n全局浏览\n与编辑": browse_nav_option,
        "🔍\n浏览": browse_nav_option,
        "组卷服务": exam_nav_option,
        "🖨️  组卷排版服务": exam_nav_option,
        "🖨️\n组卷服务": exam_nav_option,
        "🖨️\n组卷": exam_nav_option,
        "工具箱": tools_nav_option,
        "🛠️  题库维护工具箱": tools_nav_option,
        "🛠️\n工具箱": tools_nav_option,
        "🛠️\n工具": tools_nav_option,
        "三级查找": advanced_nav_option,
        "🔎  三级精确查找": advanced_nav_option,
        "🔎\n三级查找": advanced_nav_option,
        "🔎\n查找": advanced_nav_option,
        "项目介绍": intro_nav_option,
        "📘  项目架构介绍": intro_nav_option,
        "📘\n项目介绍": intro_nav_option,
        "📘\n介绍": intro_nav_option,
        "规范说明": manual_nav_option,
        "📖  LaTeX规范指南": manual_nav_option,
        "📖\n规范说明": manual_nav_option,
        "📖\n规范": manual_nav_option,
        # 新四字短标签
        "📊  数据统计": stats_nav_option,
        "📝  智能录题": entry_nav_option,
        "📄  TeX录入": tex_entry_nav_option,
        "🔍  全局浏览": browse_nav_option,
        "🖨️  组卷排版": exam_nav_option,
        "🛠️  维护工具": tools_nav_option,
        "🔎  精确查找": advanced_nav_option,
        "📘  架构介绍": intro_nav_option,
        "📖  规范指南": manual_nav_option,
        legacy_browse_nav_option: browse_nav_option,
    }

    for state_key in ("main_nav_selection", "main_sidebar_radio"):
        saved_value = st.session_state.get(state_key)
        if saved_value and ('API' in str(saved_value) or saved_value == '🔑\n设置'):
            st.session_state['settings_subpage']='🔑 API设置'
        elif saved_value and 'UI设置' in str(saved_value):
            st.session_state['settings_subpage']='🎨 UI设置'
        if saved_value in legacy_nav_aliases:
            st.session_state[state_key] = legacy_nav_aliases[saved_value]
    if "main_nav_selection" not in st.session_state or st.session_state["main_nav_selection"] not in nav_options:
        st.session_state["main_nav_selection"] = default_nav_option
    if "main_sidebar_radio" not in st.session_state or st.session_state["main_sidebar_radio"] not in nav_options:
        st.session_state["main_sidebar_radio"] = st.session_state["main_nav_selection"]

    def _select_main_navigation(selection):
        if selection not in nav_options:
            return

        st.session_state["main_nav_selection"] = selection
        st.session_state["main_sidebar_radio"] = selection
        if selection == browse_nav_option:
            st.session_state["adv_search_active"] = False
            st.session_state["browse_mode"] = "按知识板块浏览"
        elif selection != advanced_nav_option:
            st.session_state["adv_search_active"] = False
        if selection != tools_nav_option:
            st.session_state["tools_subpage"] = None

    pending_nav = st.session_state.get("main_sidebar_radio")
    if (
        pending_nav in nav_options
        and pending_nav != st.session_state.get("main_nav_selection")
    ):
        _select_main_navigation(pending_nav)

    def _on_main_sidebar_nav_change():
        _select_main_navigation(st.session_state.get("main_sidebar_radio"))

    def _on_nav_group_a_change():
        picked = st.session_state.get("nav_group_a")
        if picked:
            _select_main_navigation(picked)

    def _on_nav_group_b_change():
        picked = st.session_state.get("nav_group_b")
        if picked:
            _select_main_navigation(picked)

    if _query_param_enabled("utopiaphycyclus_intro"):
        st.session_state["utopiaphycyclus_intro_requested"] = True
        _remove_query_param("utopiaphycyclus_intro")
    if st.session_state.pop("utopiaphycyclus_intro_requested", False):
        show_utopiaphycyclus_intro()

    # --- 左侧：纯粹全局导航侧边栏（暖陶高对比度胶囊设计，带平滑收起展开功能） ---
    _mc_t = time.perf_counter()
    with st.sidebar:
        logo_b64 = _cached_logo_base64(os.path.join(BASE_DIR, "static", "sidebar.webp"))

        avatar_html = (
            f'<img src="data:image/webp;base64,{logo_b64}" class="mc-sidebar-avatar" style="width: 62px !important; height: 62px !important; min-width: 62px !important; min-height: 62px !important; max-width: 62px !important; max-height: 62px !important; border-radius: 18px; object-fit: cover; box-shadow: 0 6px 16px rgba(120, 95, 60, 0.15); margin-bottom: 10px; border: 1.5px solid rgba(214, 202, 185, 0.85); display: block;">'
            if logo_b64
            else '<div class="mc-sidebar-avatar" style="width: 62px; height: 62px; min-width: 62px; min-height: 62px; display:flex;align-items:center;justify-content:center;font-size:28px;background:#F5EFE6;border-radius:18px;border:1.5px solid rgba(214,202,185,0.8);margin-bottom:10px;">🏛️</div>'
        )
        st.markdown(f"""
            <div class="mc-sidebar-brand-box" style="display:flex; flex-direction:column; align-items:center; justify-content:center; padding: 0.5rem 0 1rem; margin-bottom: 0.75rem; border-bottom: 1px solid rgba(214, 202, 185, 0.65); text-align: center;">
                {avatar_html}
                <div class="mc-brand-line1">乌托邦</div>
                <div class="mc-brand-line2">高中物理题库</div>
                <div class="mc-brand-line3">物理命题与</div>
                <div class="mc-brand-line4">教研工作台</div>
            </div>
        """, unsafe_allow_html=True)

        # === 导航：分组渲染，使二级功能紧跟在其一级功能下方 ===
        # 有二级导航的一级项 → 允许插入的位置（索引）
        _subnav_parents = {entry_nav_option: 1, tex_entry_nav_option: 2, settings_nav_option: 9}
        _active_nav = st.session_state.get("main_nav_selection", default_nav_option)
        _split_at = _subnav_parents.get(_active_nav)

        def _render_nav_suboptions(parent_option: str):
            if parent_option == settings_nav_option:
                if st.session_state.get('settings_subpage') not in ('🎨 UI设置','🔑 API设置'):
                    st.session_state['settings_subpage']='🎨 UI设置'
                st.radio('设置子功能',['🎨 UI设置','🔑 API设置'],key='settings_subpage',label_visibility='collapsed')
            elif parent_option == entry_nav_option:
                if st.session_state.get("entry_mode_sidebar") not in ("单题录入", "批量试题录入", "同卷试题录入"):
                    st.session_state["entry_mode_sidebar"] = "单题录入"
                st.radio(
                    "录入模式",
                    ["单题录入", "批量试题录入", "同卷试题录入"],
                    label_visibility="collapsed",
                    key="entry_mode_sidebar",
                )
            elif parent_option == tex_entry_nav_option:
                if st.session_state.get("tex_workspace_mode") not in ("单题录入", "批量试题录入", "同卷试题录入"):
                    st.session_state["tex_workspace_mode"] = "单题录入"
                st.radio(
                    "录入模式",
                    ["单题录入", "批量试题录入", "同卷试题录入"],
                    label_visibility="collapsed",
                    key="tex_workspace_mode",
                )

        if _split_at is None:
            # 普通页面：一整组导航
            st.session_state["nav_group_a"] = _active_nav
            st.radio(
                "工作流导航",
                nav_options,
                format_func=_format_nav_label,
                label_visibility="collapsed",
                key="nav_group_a",
                on_change=_on_nav_group_a_change,
            )
        else:
            # 展开含二级功能的一级项：上半组 → 二级 → 下半组
            _group_a = nav_options[:_split_at + 1]
            _group_b = nav_options[_split_at + 1:]
            st.session_state["nav_group_a"] = _active_nav
            st.radio(
                "工作流导航",
                _group_a,
                format_func=_format_nav_label,
                label_visibility="collapsed",
                key="nav_group_a",
                on_change=_on_nav_group_a_change,
            )
            _render_nav_suboptions(_active_nav)
            if _group_b:
                # No inactive route may appear selected; clicking its first item
                # must emit a change event, including AI entry -> TeX entry.
                st.session_state["nav_group_b"] = None
                st.radio(
                    "工作流导航（续）",
                    _group_b,
                    index=None,
                    format_func=_format_nav_label,
                    label_visibility="collapsed",
                    key="nav_group_b",
                    on_change=_on_nav_group_b_change,
                )

        st.markdown("""
            <div class="mc-sidebar-status">🟢 服务在线 · 题库引擎正常</div>
        """, unsafe_allow_html=True)

        # UI and API settings share one parent navigation item.
        from services.sidebar_navigation import render_sidebar_flyouts
        render_sidebar_flyouts(nav_options, _select_main_navigation, {
            entry_nav_option: ('entry_mode_sidebar', ['单题录入', '批量试题录入', '同卷试题录入']),
            tex_entry_nav_option: ('tex_workspace_mode', ['单题录入', '批量试题录入', '同卷试题录入']),
            settings_nav_option: ('settings_subpage', ['🎨 UI设置', '🔑 API设置']),
        })

# Flyouts use one scoped event bridge; native widgets retain routing ownership.

    _mc_prof("sidebar_build", _mc_t)

    selected_nav = st.session_state.get("main_nav_selection", default_nav_option)

    # TeX 录入临时编译目录：应用启动清扫上次异常退出的残留；离开录题页时立即清理
    if not st.session_state.get("_tex_entry_stale_cache_swept", False):
        _cleanup_tex_entry_temp(min_age_seconds=TEX_ENTRY_STALE_CACHE_SECONDS)
        st.session_state["_tex_entry_stale_cache_swept"] = True
    _sync_tex_entry_cache_lifecycle(selected_nav, tex_entry_nav_option)

    # 注入全局统一视觉主题系统
    _mc_t = time.perf_counter()
    inject_unified_visual_system_css()
    _mc_prof("theme_css_1", _mc_t)
    st.markdown(f'<span id="mc-appearance-state" data-skin="{_get_current_theme_skin()}" data-mode="{_get_current_theme_mode()}" style="display:none"></span>',unsafe_allow_html=True)

    # --- 主内容区路由（顶部无多余条带，纯净进入对应功能工作台） ---
    _mc_t = time.perf_counter()
    if selected_nav == stats_nav_option:
        render_statistics_dashboard()
    elif selected_nav == entry_nav_option:
        page_entry()
    elif selected_nav == tex_entry_nav_option:
        page_tex_entry()
    elif selected_nav == browse_nav_option:
        page_browse()
    elif selected_nav == exam_nav_option:
        page_exam_paper_generation()
    elif selected_nav == tools_nav_option:
        page_tools()
    elif selected_nav == advanced_nav_option:
        page_advanced_search()
    elif selected_nav == intro_nav_option:
        page_system_intro()
    elif selected_nav == manual_nav_option:
        page_manual()
    elif selected_nav == settings_nav_option:
        if st.session_state.get('settings_subpage') == '🔑 API设置':
            page_api_settings()
        else:
            page_ui_settings()
    _mc_prof(f"route[{selected_nav}]", _mc_t)
    st.markdown(f'<span id="mc-route-ready" data-route="{html.escape(selected_nav, quote=True)}" style="display:none"></span>', unsafe_allow_html=True)

    # 首屏内容已就绪：放置就绪标记，加载动画会自动淡出（仅首轮执行）
    if _mc_show_boot:
        st.markdown('<div id="mc-boot-ready" style="display:none"></div>', unsafe_allow_html=True)
        st.session_state["_mc_boot_done"] = True

    # 主题 CSS 已在路由前统一注入一次；此处不再重复注入整份样式表
    # （重复注入会让每次 rerun 多出约 30KB 样式元素，直接拖慢样式重算与渲染）
    _mc_prof("TOTAL main()", _mc_main_t0)


if __name__ == "__main__":
    main()
