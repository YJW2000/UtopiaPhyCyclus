"""Apply appearance tokens inside isolated HTML components (not inherited from host CSS)."""
from .theme_palettes import theme_tokens, retint_warm_markup


def theme_agent_capsule(markup, skin='claude_warm', mode='light'):
    markup = retint_warm_markup(markup, skin, mode)
    tokens = theme_tokens(skin, mode)
    variables = ';'.join(f'{key}:{value}' for key, value in tokens.items())
    css = '''
    body {color:var(--mc-text);background:transparent;}
    .agent-capsule {background:var(--mc-surface);color:var(--mc-text);}
    .prompt-textarea {color:var(--mc-text);}
    .prompt-textarea::placeholder,.plus-icon,.sync-toggle-label,.icon-tool-btn {color:var(--mc-muted);}
    .model-pill {max-width:100%;overflow:hidden;text-overflow:ellipsis;}
    .model-pill span.bolt {color:var(--mc-muted)!important;}
    .capsule-toolbar,.toolbar-left,.toolbar-right {flex-wrap:wrap;gap:7px;min-width:0;}
    .toolbar-right {justify-content:flex-end;margin-left:auto;}
    .send-btn-circle {background:var(--mc-action-bg);color:var(--mc-action-text);}
    .send-btn-circle:hover {background:var(--mc-action-bg-hover);}
    '''
    return markup.replace('</head>', '<style>:root{' + variables + '}' + css + '</style></head>')


def theme_activity_chart(markup, skin='claude_warm', mode='light'):
    tokens=theme_tokens(skin,mode)
    variables=';'.join(f'{k}:{v}' for k,v in tokens.items())
    scheme_style='<style>html,body{color-scheme:'+tokens['color-scheme']+'!important}</style>'
    return markup + scheme_style + '<style>:root{'+variables+'''}
    .day-selector-shell {background:var(--mc-surface-soft)!important;box-shadow:none!important;}
    .day-selector-btn {color:var(--mc-text)!important;}
    .day-selector-indicator {background:var(--mc-action-bg)!important;}
    .day-selector-btn.active {background:var(--mc-action-bg)!important;color:var(--mc-action-text)!important;text-shadow:none!important;}
    </style>'''
