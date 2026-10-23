"""Client-side scroll bookmarks: no server rerun, polling or scroll-time writes."""
import hashlib
import json
from pathlib import Path

import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parents[1]


def render_scroll_bookmark(state):
    mode = state.get("browse_mode", "")
    # Only include the current view's controls, not previously visited hidden views.
    if mode == "按录入顺序浏览":
        context = [mode, state.get("time_sort_order"), state.get("time_max_show"), state.get("time_browse_page")]
    elif mode == "按试卷浏览":
        year, paper = state.get("paper_year"), state.get("paper_name")
        context = [mode, state.get("paper_type"), year, paper, state.get("paper_view_mode"), state.get(f"selected_q_idx_{year}_{paper}")]
    else:
        subject = state.get("browse_subject")
        year = state.get(f"browse_year_{subject}")
        scope = "all" if year == "显示所有年份" else year
        context = [mode, subject, year, state.get(f"browse_file_select_{subject}_{scope}"),
                   state.get(f"browse_question_page_{subject}_{'all_years' if scope == 'all' else year}_regular", 1)]
    if state.get("adv_search_active") or state.get("recent_saved_active"):
        # Search/recent-import results must not replace the ordinary bookmark.
        components.html('<script>window.parent.__mcBrowseScroll?.cleanup();</script>', height=0)
        return
    config = {
        "key": "utopiaphycyclus:browse-scroll:" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:16],
        "context": hashlib.sha256(json.dumps(context, ensure_ascii=False).encode()).hexdigest(),
    }
    script = (ROOT / "static" / "browse_scroll.js").read_text(encoding="utf-8")
    components.html('<script>' + script.replace('__MC_SCROLL_CONFIG__', json.dumps(config)) + '</script>', height=0)
