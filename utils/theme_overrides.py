"""Small token-driven layer for native Streamlit controls and portal surfaces."""
from .sidebar_theme import SIDEBAR_CSS

CONTROL_CSS = '''
html body .stApp, html body [data-testid="stAppViewContainer"] {background:var(--mc-bg)!important;color:var(--mc-text)!important;}
html {background:var(--mc-bg-base)!important;color-scheme:var(--mc-color-scheme,light)!important;}
html body .stApp [data-testid="stSidebar"], html body .stApp [data-testid="stSidebarContent"] {background:var(--mc-sidebar)!important;color:var(--mc-text)!important;border-color:var(--mc-border)!important;}
html body .stApp :is(h1,h2,h3,h4,h5,h6,p,label,[data-testid="stMetricValue"],[data-testid="stMetricLabel"],[data-testid="stWidgetLabel"]) {color:var(--mc-text)!important;}
html body .stApp :is([data-testid="stCaptionContainer"],small) {color:var(--mc-muted)!important;}
html body .stApp :is([data-testid="stVerticalBlockBorderWrapper"],[data-testid="stForm"]) {border-color:var(--mc-border)!important;}
html body .stApp :is(input,textarea,[data-baseweb="input"],[data-baseweb="input"]>div,[data-baseweb="textarea"],[data-baseweb="textarea"]>div,[data-baseweb="select"]>div) {
 background:var(--mc-input-bg)!important;color:var(--mc-text)!important;caret-color:var(--mc-text)!important;border-color:var(--mc-border-strong)!important;
}
html body .stApp :is(input,textarea)::placeholder {color:var(--mc-muted)!important;opacity:1!important;}
html body .stApp :is(input,textarea):focus {color:var(--mc-text)!important;background:var(--mc-input-bg)!important;box-shadow:0 0 0 3px var(--mc-focus)!important;}
html body .stApp :is(input,textarea):disabled {color:var(--mc-muted)!important;-webkit-text-fill-color:var(--mc-muted)!important;opacity:1!important;}
html body .stApp :is([data-testid="stButton"],[data-testid="stDownloadButton"],[data-testid="stFormSubmitButton"],[data-testid="stPopover"]) button {
 background:var(--mc-surface)!important;color:var(--mc-text)!important;border-color:var(--mc-border-strong)!important;
}
html body .stApp button :is(p,span) {color:inherit!important;}
html body .stApp button[kind="primary"], html body .stApp [data-testid="stButton"] button[kind="primary"] {background:var(--mc-action-bg)!important;color:var(--mc-action-text)!important;border-color:var(--mc-action-border)!important;}
html body .stApp button[kind="primary"]:hover {background:var(--mc-action-bg-hover)!important;}
html body .stApp button:disabled {background:var(--mc-surface-soft)!important;color:var(--mc-muted)!important;opacity:.75!important;}
html body .stApp :is([data-testid="stFileUploaderDropzone"],[data-testid="stExpander"]>details,[data-testid="stChatMessage"]) {background:var(--mc-surface)!important;border-color:var(--mc-border)!important;color:var(--mc-text)!important;}
html body .stApp :is([data-testid="stAlert"],[data-testid="stNotification"]) {background:var(--mc-surface-soft)!important;border-color:var(--mc-border)!important;color:var(--mc-text)!important;}
html body :is([role="dialog"],[data-testid="stPopoverBody"],[data-baseweb="popover"],[data-baseweb="menu"],[role="listbox"],[role="option"],[data-testid="stToast"]) {background:var(--mc-surface)!important;color:var(--mc-text)!important;border-color:var(--mc-border)!important;}
html body [role="dialog"] :is(input,textarea) {background:var(--mc-input-bg)!important;color:var(--mc-text)!important;}
html body [role="option"]:hover {background:var(--mc-accent-soft)!important;}
html body .stApp [data-baseweb="tag"] {background:var(--mc-accent-soft)!important;color:var(--mc-text)!important;}
html body .stApp [data-testid="stCode"] {background:var(--mc-surface-soft)!important;color:var(--mc-text)!important;}
html body .stApp .mc-question-preview-anchor {color:var(--mc-text)!important;}
/* Portals are mounted outside .stApp; style their controls and labels explicitly. */
html body :is([role="dialog"],[data-testid="stPopoverBody"],[data-baseweb="popover"],[data-baseweb="menu"]) :is(p,label,h1,h2,h3,h4,li,[role="option"],[role="option"] *) {color:var(--mc-text)!important;}
html body :is([role="dialog"],[data-testid="stPopoverBody"]) :is(input,textarea,[data-baseweb="input"],[data-baseweb="input"]>div,[data-baseweb="select"]>div) {background:var(--mc-input-bg)!important;color:var(--mc-text)!important;border-color:var(--mc-border-strong)!important;}
html body :is([role="dialog"],[data-testid="stPopoverBody"]) button {background:var(--mc-surface)!important;color:var(--mc-text)!important;border-color:var(--mc-border-strong)!important;}
html body :is(.stApp,[role="dialog"],[data-testid="stPopoverBody"]) button[kind="primary"] :is(p,span) {color:var(--mc-action-text)!important;}
html body :is([role="dialog"],[data-testid="stPopoverBody"]) button[kind="primary"] {background:var(--mc-action-bg)!important;color:var(--mc-action-text)!important;}
html body :is([role="dialog"],[data-testid="stPopoverBody"]) button:not([kind="primary"]) :is(p,span) {color:inherit!important;}
html body .stApp [data-baseweb="select"] :is(input,span,div) {color:var(--mc-text)!important;}
html body .stApp [data-baseweb="select"] input::placeholder {color:var(--mc-muted)!important;}
html body .stApp .mc-theme-fixed-note {background:var(--mc-surface-soft)!important;color:var(--mc-muted)!important;border-color:var(--mc-border)!important;}
html body .stApp div[data-testid="stHorizontalBlock"]>div[data-testid="column"] div[data-testid="stMetric"] {background:var(--mc-surface)!important;}
html body .stApp div[data-testid="stHorizontalBlock"]>div[data-testid="column"] div[data-testid="stMetric"] [data-testid="stMetricLabel"] * {color:var(--mc-muted)!important;}
/* Keep portal content inside the viewport, including short laptop screens. */
html body [role="dialog"] {max-width:calc(100vw - 32px)!important;max-height:calc(100dvh - 32px)!important;overflow-y:auto!important;box-sizing:border-box;}
html body [data-testid="stPopoverBody"] {max-width:calc(100vw - 24px)!important;max-height:calc(100dvh - 48px)!important;overflow:auto!important;}
html body .stApp [data-testid="column"] {min-width:0;}
html body .stApp button {white-space:normal;overflow-wrap:anywhere;}
html body .stApp :is(.mc-browse-mini-label,.mc-inline-mode-label,.mc-entry-attribute-label,.mc-entry-strip-title,.mc-entry-strip-hint,.meta-title,[data-testid="stImageCaption"]) {color:var(--mc-muted)!important;}
html body .stApp .mc-browse-mini-label {background:var(--mc-surface-soft)!important;}
html body .stApp :is(.static-meta-row,.mc-compact-meta-row) {flex-wrap:wrap!important;white-space:normal!important;row-gap:6px!important;}
html body .stApp :is(.badge-tag,.static-meta-tag) {color:var(--mc-text)!important;background:var(--mc-accent-soft)!important;}
html body .stApp [data-testid="stTextInput"] button {background:var(--mc-input-bg)!important;color:var(--mc-text)!important;}
html body .stApp [data-testid="stFileUploaderDropzone"] button {background:var(--mc-action-bg)!important;color:var(--mc-action-text)!important;}
html body .stApp [data-testid="stFileUploaderDropzone"] button :is(p,span,div) {color:var(--mc-action-text)!important;}
html body .stApp [data-testid="stSidebar"] [role="radiogroup"]>label p {color:var(--mc-text)!important;}
html body .stApp [data-testid="stSidebar"] [role="radiogroup"]:is([aria-label="录入模式"],[aria-label="设置子功能"])>label:has(input:checked) {background:var(--mc-action-bg)!important;color:var(--mc-action-text)!important;}
html body .stApp [data-testid="stSidebar"] [role="radiogroup"]:is([aria-label="录入模式"],[aria-label="设置子功能"])>label:has(input:checked) p {color:var(--mc-action-text)!important;}
html body .stApp [data-testid="stNumberInput"] button {background:var(--mc-input-bg)!important;color:var(--mc-text)!important;}
html body .stApp div[data-testid="stVerticalBlock"]:has(.mc-exam-card-anchor) .mc-exam-card-preview-label {color:var(--mc-text)!important;}
html body .stApp [data-testid="stTooltipIcon"] {color:var(--mc-muted)!important;}
html body .stApp [data-testid="stTooltipHoverTarget"] svg {color:var(--mc-muted)!important;fill:currentColor!important;}
html body :is(.stApp,[role="dialog"]) button svg {color:inherit!important;fill:currentColor!important;}
html body .stApp .mc-sidebar-status {color:var(--mc-text)!important;background:var(--mc-accent-soft)!important;border-color:var(--mc-border-strong)!important;}
html body .stApp [data-baseweb="select"] svg {color:var(--mc-text)!important;fill:currentColor!important;}
html body .stApp :is(.mc-brand-line3,.mc-brand-line4) {color:var(--mc-muted)!important;}
html body [data-baseweb="popover"] [role="option"] {background:var(--mc-surface)!important;color:var(--mc-text)!important;}
html body [data-baseweb="popover"] [role="option"] :is(div,span) {background:transparent!important;color:var(--mc-text)!important;}
html body [data-baseweb="popover"] [role="option"]:hover {background:var(--mc-accent-soft)!important;}
html body .stApp [data-testid="stSlider"] [class*="StyledThumbValue"] {color:var(--mc-text)!important;background:var(--mc-surface)!important;}
html body .stApp div[data-testid="element-container"]:has(.red-btn-hook)+div[data-testid="stButton"]>button,
html body .stApp div[data-testid="element-container"]:has(.red-btn-hook)+div[data-testid="element-container"] div[data-testid="stButton"]>button {background:#B92534!important;color:#FFFFFF!important;border-color:#B92534!important;}
html body .stApp div[data-testid="element-container"]:has(.red-btn-hook)+div[data-testid="stButton"]>button :is(p,span),
html body .stApp div[data-testid="element-container"]:has(.red-btn-hook)+div[data-testid="element-container"] div[data-testid="stButton"]>button :is(p,span) {color:#FFFFFF!important;}
html body :is(.stApp,[role="dialog"]) div[data-testid="column"]:has(.delete-exit-btn-hook) div[data-testid="stButton"]>button {background:#B92534!important;color:#FFFFFF!important;border-color:#B92534!important;}
html body :is(.stApp,[role="dialog"]) div[data-testid="column"]:has(.delete-exit-btn-hook) div[data-testid="stButton"]>button :is(p,span) {color:#FFFFFF!important;}
html body :is(.stApp,[role="dialog"]) div[data-testid="column"]:has(.delete-restore-btn-hook) div[data-testid="stButton"]>button {background:var(--mc-action-bg)!important;color:var(--mc-action-text)!important;border-color:var(--mc-action-border)!important;}
html body :is(.stApp,[role="dialog"]) div[data-testid="column"]:has(.delete-restore-btn-hook) div[data-testid="stButton"]>button :is(p,span) {color:var(--mc-action-text)!important;}
/* Keep the desktop editor/preview split, but stack controls before laptop/mobile
   columns become narrower than their inputs. Subject chips retain their scroller. */
@media(max-width:1100px) {
 html body .stApp :is(section.main,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) div[data-testid="stHorizontalBlock"]:not(:has(.mc-skin-preview)):not(:has(#mc-subject-chip-start)) {flex-wrap:wrap!important;gap:.65rem!important;}
 html body .stApp :is(section.main,[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]) div[data-testid="stHorizontalBlock"]:not(:has(.mc-skin-preview)):not(:has(#mc-subject-chip-start))>div[data-testid="column"] {flex:1 1 100%!important;width:100%!important;min-width:0!important;max-width:100%!important;}
 html body .stApp :is(input,textarea,[data-baseweb="select"]) {min-width:0!important;max-width:100%!important;}
 html body .stApp div[data-testid="stHorizontalBlock"]:has([data-testid="stFileUploader"])>div[data-testid="column"]:has(h2) {border-right:0!important;min-height:45px!important;margin-bottom:6px!important;}
}
'''

def component_overrides(tokens):
    variables=';'.join(f'{k}:{v}' for k,v in tokens.items())
    variables+=';--mc-color-scheme:'+tokens['color-scheme']
    return '\n<style>\n:root{'+variables+'}\n'+CONTROL_CSS+'\n'+SIDEBAR_CSS+'\n</style>\n'
