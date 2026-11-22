"""Open the original specialized tools inside the desktop workspace."""
import os
from pathlib import Path
import shutil

import streamlit as st
from dotenv import load_dotenv
from services.desktop_model import read_settings

st.set_page_config(page_title="乌托邦题库", layout="wide", initial_sidebar_state="collapsed")
library = Path(os.environ["UTOPIA_DESKTOP_LIBRARY"])
os.environ["UTOPIA_LEGACY_ROOT"] = str(library)
load_dotenv(library / ".env", override=True)
if read_settings().get("provider") != "api":
    os.environ.update(AI_BASE_URL="local://codex", AI_API_KEY="desktop-session", AI_MODEL_NAME="codex", AI_SOLVER_MODEL_NAME="codex")

# The original exporters expect class files and paper templates in the library.
# Copy missing public assets once and preserve customized templates.
resources = Path(__file__).resolve().parent
if resources != library.resolve() and not st.session_state.get("_desktop_assets_ready"):
    assets = [resources / name for name in ("UtopiaPhyCyclus_book.cls", "main.tex", "ocr_prompt.txt")]
    for name in ("fig", "static", "Test Paper Group/主题模板"):
        assets.extend(path for path in (resources / name).rglob("*") if path.is_file())
    for source in assets:
        target = library / source.relative_to(resources)
        if source.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    st.session_state["_desktop_assets_ready"] = True

import utopia_phycyclus_app as app
from services.desktop_library import create_library_handlers
from services.pdf_import_ui import render_pdf_import

appearance = create_library_handlers(library, Path(os.environ["UTOPIA_DESKTOP_STATE"]))["ui.load"]({})
st.session_state.update(app_theme_skin=appearance["skin"], app_theme_mode=appearance["mode"], _appearance_preferences_loaded=True)
app.inject_custom_css()
app.inject_unified_visual_system_css()
st.markdown("<style>header[data-testid=stHeader],section[data-testid=stSidebar]{display:none}.block-container{padding-top:1rem}</style>", unsafe_allow_html=True)
route = st.query_params.get("desktop_page", "browse")
if route in ("entry", "tex"):
    st.radio("录入模式", ["单题录入", "批量试题录入", "同卷试题录入"], horizontal=True,
             key="entry_mode_sidebar" if route == "entry" else "tex_workspace_mode")
handlers = {
    "entry": app.page_entry, "tex": app.page_tex_entry, "pdf": lambda: render_pdf_import(str(library), app._save_pdf_questions),
    "browse": app.page_browse, "exam": app.page_exam_paper_generation, "search": app.page_advanced_search,
    "settings": app.page_api_settings, "tools": app.page_tools, "tags": app.page_tag_edit,
    "stats": app.render_statistics_dashboard, "manual": app.page_manual, "intro": app.page_system_intro,
}
handlers.get(route, app.page_browse)()
