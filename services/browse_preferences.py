"""Non-secret, project-local browsing preference (independent of API/theme data)."""
import json
from pathlib import Path

from services.file_service import atomic_write_text

BROWSE_MODES = ("按知识板块浏览", "按试卷浏览", "按录入顺序浏览")
SORT_ORDERS = ("最新录入在最前", "最早录入在最前")
PREFERENCES_PATH = Path(__file__).resolve().parents[1] / ".runtime" / "browse_preferences.json"


def _read_preferences():
    try:
        data = json.loads(PREFERENCES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_browse_preferences():
    data = _read_preferences()
    size, page = data.get("page_size"), data.get("page")
    return {
        "mode": data.get("mode") if data.get("mode") in BROWSE_MODES else BROWSE_MODES[0],
        "sort_order": data.get("sort_order") if data.get("sort_order") in SORT_ORDERS else SORT_ORDERS[0],
        "page_size": size if type(size) is int and 5 <= size <= 20 else 10,
        "page": page if type(page) is int and page >= 1 else 1,
    }


def load_browse_mode():
    return load_browse_preferences()["mode"]


def _save_changes(changes):
    current = _read_preferences()
    data = {**current, **changes}
    if data == current:
        return  # Ordinary reruns and editing do not rewrite the preference file.
    PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(str(PREFERENCES_PATH), json.dumps(data, ensure_ascii=False) + "\n", backup=False)


def save_browse_mode(mode):
    if mode not in BROWSE_MODES:
        raise ValueError("Unknown browse mode")
    _save_changes({"mode": mode})


def prepare_time_browse(state):
    # The shadow dictionary survives widget cleanup when this page is unmounted.
    if "global_time_browse_position" not in state:
        data = load_browse_preferences()
        state["global_time_browse_position"] = {key: data[key] for key in ("sort_order", "page_size", "page")}
    position = state["global_time_browse_position"]
    for widget, field in (("time_sort_order", "sort_order"), ("time_max_show", "page_size")):
        # Exam/delete views may use the same temporary widget keys. They must
        # not replace the global browser's independently remembered position.
        state[widget] = position[field]
    state["time_browse_page"] = position["page"]


def remember_time_browse_position(state):
    position = {
        "sort_order": state["time_sort_order"],
        "page_size": state["time_max_show"],
        "page": state["time_browse_page"],
    }
    state["global_time_browse_position"] = position
    _save_changes(position)


def prepare_browse_mode(state):
    """Restore widget state after Streamlit has cleaned up an unmounted radio."""
    if "global_browse_mode" not in state:
        state["global_browse_mode"] = load_browse_mode()
    if state.get("browse_mode") not in BROWSE_MODES:
        state["browse_mode"] = state["global_browse_mode"]


def remember_browse_mode(state):
    mode = state["browse_mode"]
    state["global_browse_mode"] = mode
    save_browse_mode(mode)


def _view_key(key):
    return isinstance(key, str) and (key in ("browse_subject", "paper_type", "paper_year", "paper_name", "paper_view_mode") or key.startswith(("browse_year_", "browse_file_select_", "browse_question_page_", "selected_q_idx_")))


def _view_value(key, value):
    if key.startswith(("browse_question_page_", "selected_q_idx_")):
        return type(value) is int and value >= 0
    return isinstance(value, str) and len(value) <= 1024 or key == "paper_year" and type(value) is int and value > 0


def prepare_browse_view(state):
    if "global_browse_view" not in state:
        data = _read_preferences().get("view_state", {})
        state["global_browse_view"] = {
            key: value for key, value in (data.items() if isinstance(data, dict) else [])
            if _view_key(key) and _view_value(key, value)
        }
    for key, value in state["global_browse_view"].items():
        if key not in state:
            state[key] = value


def remember_browse_view(state):
    view = dict(state.get("global_browse_view", {}))
    view.update({key: state[key] for key in state if _view_key(key)})
    state["global_browse_view"] = view
    _save_changes({"view_state": view})
    return view
