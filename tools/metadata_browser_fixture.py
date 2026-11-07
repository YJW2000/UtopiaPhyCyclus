"""Run the real browse page against an isolated, synthetic question bank."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import streamlit as st
import utopia_phycyclus_app as app
from services import browse_preferences, operation_log, question_service
from utils import core_config, csv_ops, file_ops, latex_ops

st.set_page_config(page_title="单题文件信息浏览器验收", layout="wide")
fixture = ROOT / ".runtime" / "metadata-browser-verification"
chapters = fixture / "chapters"
index = fixture / "utils" / "index.csv"
index.parent.mkdir(parents=True, exist_ok=True)

# Assign process-local paths permanently: fragment reruns happen after the
# top-level script returns, so a temporary `with patch(...)` is insufficient.
for module in (app, core_config, csv_ops, file_ops, latex_ops, question_service):
    module.CHAPTERS_DIR = str(chapters)
for module in (core_config, csv_ops):
    module.CSV_INDEX_PATH = str(index)
operation_log.OPERATION_LOG_PATH = str(fixture / "utils" / "operations.sqlite3")
browse_preferences.PREFERENCES_PATH = fixture / "browse.json"
app._invalidate_semantic_for_file = lambda *_: None
app.render_ai_solution_panel = lambda *_, **__: None
app.render_ai_solution_image_ocr_section = lambda *_, **__: None

if not list(chapters.rglob("*.tex")):
    directory = chapters / "磁场运动" / "2026"
    directory.mkdir(parents=True, exist_ok=True)
    for number in range(1, 4):
        content = latex_ops.inject_meta_data(
            rf"\begin{{problem}}{{2026}}{{M}}{{浏览器验收卷}}{{{number}}}{{磁场运动}}" + "\n"
            + rf"\textbf{{浏览器验收第 {number} 题}}\par" + "\n"
            + r"带电粒子在匀强磁场中运动，已知质量 $m$、电荷量 $q$、速率 $v$ 和磁感应强度 $B$，求轨迹半径。"
            + "\n" + r"\end{problem}\begin{answer}$r=\dfrac{mv}{qB}$。\end{answer}"
            + r"\begin{solutions}由 $qvB=\dfrac{mv^2}{r}$ 得结果。\end{solutions}",
            {"ID": str(9000 + number), "题型": "计算题", "难度星级": "1.0", "标签": "浏览器验收，基础", "知识点": "洛伦兹力", "物理方法": ""},
        )
        path = directory / f"2026-M-浏览器验收卷-{number}-磁场运动.tex"
        path.write_text(content, encoding="utf-8")
        csv_ops.add_to_csv_index(str(path), content, "2026", "M", "浏览器验收卷", str(number), "磁场运动")

mode = st.query_params.get("browse", "按试卷浏览")
st.session_state.setdefault("browse_mode", mode)
st.session_state.setdefault("global_browse_mode", mode)
st.session_state.setdefault("paper_type", "M")
st.session_state.setdefault("paper_year", "2026")
st.session_state.setdefault("paper_name", "浏览器验收卷")
st.session_state.setdefault("paper_view_mode", "单题选择模式")
st.session_state.setdefault("selected_q_idx_2026_浏览器验收卷", 1)
st.session_state.setdefault("app_theme_skin", "claude_warm")
st.session_state.setdefault("app_theme_mode", "light")
app.inject_custom_css()
app.inject_unified_visual_system_css()
app.page_browse()
