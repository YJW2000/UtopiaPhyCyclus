"""Shared colors for expanded rail, collapsed rail and body-mounted flyouts."""
SIDEBAR_CSS = '''
html body .stApp section[data-testid="stSidebar"][aria-expanded] {
 background:var(--mc-sidebar)!important;color:var(--mc-text)!important;
 border-right:1px solid var(--mc-border-strong)!important;backdrop-filter:var(--mc-sidebar-backdrop,none)!important;
 box-shadow:var(--mc-shadow)!important;
}
html body .stApp section[data-testid="stSidebar"][aria-expanded]>div,
html body .stApp section[data-testid="stSidebar"][aria-expanded] :is([data-testid="stSidebarContent"],[data-testid="stSidebarUserContent"]) {background:transparent!important;}
/* Callbacks remain native widgets, but their bridge does not occupy sidebar space. */
html body .stApp [data-testid="stVerticalBlock"]:has(>[data-testid="element-container"] #mc-hover-nav-bridge) {display:none!important;}
html body .stApp section[data-testid="stSidebar"] [data-testid="stRadio"]:has([role="radiogroup"]:is([aria-label="录入模式"],[aria-label="设置子功能"])) {display:none!important;}
html body .stApp section[data-testid="stSidebar"][aria-expanded] [role="radiogroup"]>label {background:transparent!important;color:var(--mc-text)!important;border-color:transparent!important;}
html body .stApp section[data-testid="stSidebar"][aria-expanded="false"] [role="radiogroup"]>label {background:var(--mc-surface)!important;border-color:var(--mc-border-strong)!important;box-shadow:var(--mc-shadow)!important;}
html body .stApp section[data-testid="stSidebar"][aria-expanded] [role="radiogroup"]>label:hover {background:var(--mc-accent-soft)!important;color:var(--mc-text)!important;border-color:var(--mc-border-strong)!important;}
html body .stApp section[data-testid="stSidebar"][aria-expanded] [role="radiogroup"]>label:has(input:checked) {background:var(--mc-action-bg)!important;color:var(--mc-action-text)!important;border-color:var(--mc-action-border)!important;}
html body .stApp section[data-testid="stSidebar"][aria-expanded] [role="radiogroup"]>label:has(input:checked) p {color:var(--mc-action-text)!important;}
html body .stApp section[data-testid="stSidebar"][aria-expanded="false"] [role="radiogroup"]>label[data-mc-flyout]:hover p {
 position:static!important;transform:none!important;width:22px!important;height:22px!important;overflow:hidden!important;
 padding:0!important;background:transparent!important;box-shadow:none!important;line-height:22px!important;font-size:16px!important;
 color:inherit!important;text-align:center!important;
}
html body .stApp section[data-testid="stSidebar"] label[data-mc-flyout]:focus-visible {outline:2px solid var(--mc-accent)!important;outline-offset:3px;}
#mc-sidebar-flyout {position:fixed;z-index:10060;min-width:194px;max-width:calc(100vw - 88px);padding:7px;
 box-sizing:border-box;background:var(--mc-surface);color:var(--mc-text);border:1px solid var(--mc-border-strong);border-radius:16px;
 box-shadow:var(--mc-shadow);backdrop-filter:var(--mc-sidebar-backdrop,none);font-family:var(--mc-font-family);}
#mc-sidebar-flyout[hidden] {display:none!important;}
#mc-sidebar-flyout .mc-flyout-title {font-size:13px;font-weight:750;padding:9px 11px;color:var(--mc-text);border-bottom:1px solid var(--mc-border);margin-bottom:5px;}
#mc-sidebar-flyout.mc-flyout-tooltip {min-width:0;}
#mc-sidebar-flyout.mc-flyout-tooltip .mc-flyout-title {border:0;margin:0;padding:4px 8px;}
#mc-sidebar-flyout .mc-flyout-item {display:block;width:100%;text-align:left;padding:10px 12px;margin:3px 0;
 background:transparent!important;color:var(--mc-text)!important;border:1px solid transparent!important;border-radius:10px;
 font:inherit;font-size:13px;font-weight:600;cursor:pointer;line-height:1.4;}
#mc-sidebar-flyout .mc-flyout-item:is(:hover,:focus-visible) {background:var(--mc-accent-soft)!important;outline:2px solid var(--mc-accent);outline-offset:-2px;}
#mc-sidebar-flyout .mc-flyout-item.is-current {background:var(--mc-action-bg)!important;color:var(--mc-action-text)!important;}
'''
