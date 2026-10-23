"""Keep browse source panes the same visible length as their rendered preview."""
import json
from pathlib import Path

import streamlit.components.v1 as components


def render_editor_height_sync(question_hash):
    script = (Path(__file__).resolve().parents[1] / 'static' / 'editor_height.js').read_text(encoding='utf-8')
    components.html('<script>' + script.replace('__MC_EDITOR_HASH__', json.dumps(question_hash)) + '</script>', height=0)
