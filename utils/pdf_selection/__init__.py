"""Selectable, paginated preview of a locally compiled PDF."""
import base64
import hashlib
import json
from pathlib import Path

import fitz
import streamlit as st
import streamlit.components.v1 as components

from services.tex_compile_service import render_pdf_page
from utils.theme_palettes import palette


_component = components.declare_component("pdf_selection", path=str(Path(__file__).parent.resolve()))


@st.cache_data(show_spinner=False, max_entries=16)
def _page_count(pdf: bytes) -> int:
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        return len(doc)


@st.cache_data(show_spinner=False, max_entries=16)
def _page_image(pdf: bytes, page: int) -> str:
    png, _ = render_pdf_page(pdf, page, scale=2.0)
    return base64.b64encode(png).decode("ascii")


def _selection(value, document_id: str, page: int) -> dict | None:
    if not isinstance(value, dict) or value.get("document_id") != document_id or value.get("page") != page:
        return None
    rect = value.get("rect")
    if not isinstance(rect, list) or len(rect) != 4:
        return None
    if any(type(n) not in (float, int) or not 0 <= n <= 1 for n in rect):
        return None
    if rect[0] >= rect[2] or rect[1] >= rect[3]:
        return None
    selection_id = hashlib.sha256(json.dumps([document_id, page, rect]).encode()).hexdigest()
    return dict(document_id=document_id, page=page, rect=rect, selection_id=selection_id)


def st_pdf_selection(
    pdf: bytes, *, key: str, document_id: str, disabled: bool = False, initial_page: int = 1
) -> dict | None:
    """Return the visible selection. Page is one-based; rect is normalized from the top left.

    The caller supplies a document_id covering the source revision and its preview.
    """
    page_key = f"{key}_{document_id}_page"
    count = _page_count(pdf)
    page = 1
    if count > 1:
        page = st.number_input(
            f"页码（共 {count} 页）", min_value=1, max_value=count,
            value=max(1, min(initial_page, count)), key=page_key,
        )

    component_key = f"{key}_selection_{document_id}_{page}"
    if disabled:
        component_key += "_readonly"
    prior = _selection(st.session_state.get(component_key), document_id, page)
    colors = palette(st.session_state.get("app_theme_skin"), st.session_state.get("app_theme_mode", "light"))
    value = _component(
        key=component_key, default=None, document_id=document_id, page=page, image=_page_image(pdf, page),
        initial_rect=prior["rect"] if prior and not disabled else None, readonly=disabled,
        colors={name: colors[name] for name in ("surface", "text", "muted", "border", "accent")},
    )
    return None if disabled else _selection(value, document_id, page)
