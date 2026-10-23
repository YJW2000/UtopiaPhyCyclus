"""Non-secret local appearance preferences, independent of API credentials."""
import json
from pathlib import Path
from services.file_service import atomic_write_text
from utils.theme_palettes import normalize_skin

PREFERENCES_PATH=Path(__file__).resolve().parents[1]/'.runtime'/'ui_preferences.json'

def load_preferences(fallback=None):
    fallback=fallback or {}
    try:
        data=json.loads(PREFERENCES_PATH.read_text(encoding='utf-8'))
        if not isinstance(data,dict):data={}
    except (OSError,ValueError):data={}
    skin=normalize_skin(data.get('skin',fallback.get('APP_THEME_SKIN')))
    mode=data.get('mode',fallback.get('APP_THEME_MODE','light'))
    return {'skin':skin,'mode':mode if mode in ('light','dark') else 'light'}

def save_preferences(skin,mode):
    data={'skin':normalize_skin(skin),'mode':'dark' if mode=='dark' else 'light'}
    PREFERENCES_PATH.parent.mkdir(parents=True,exist_ok=True)
    atomic_write_text(str(PREFERENCES_PATH),json.dumps(data,ensure_ascii=False,indent=2)+'\n',backup=False)
    return data
