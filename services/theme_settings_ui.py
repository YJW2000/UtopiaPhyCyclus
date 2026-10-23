"""Review-system-style appearance showcase with native, accessible actions."""
import html
import streamlit as st
from utils.theme_palettes import SKINS, palette, theme_tokens


def render_theme_settings(skin,mode,set_skin,set_mode):
    st.markdown('''<style>
    .mc-themes-intro{margin:0 0 24px}.mc-themes-eyebrow{font-size:11px;font-weight:800;letter-spacing:3px;color:var(--mc-accent);margin-bottom:10px}
    .mc-themes-heading{font-size:1.6rem;font-weight:800;margin:0 0 10px;color:var(--mc-text)}
    .mc-themes-description{color:var(--mc-muted);line-height:1.8;font-size:.9rem}
    .mc-skin-preview{padding:18px;min-height:195px;box-sizing:border-box;border-radius:18px;position:relative;display:flex;flex-direction:column;justify-content:center;gap:25px}
    .mc-skin-eyebrow{font-size:10px;letter-spacing:1.8px;font-weight:800;line-height:1.6}
    .mc-skin-demo{border-radius:16px;padding:18px 15px;border:1px solid;box-shadow:0 8px 24px -4px rgba(0,0,0,.1)}
    .mc-skin-demo-title{font-size:14px;font-weight:800;line-height:1.6}.mc-skin-demo-line{height:7px;border-radius:99px;margin-top:9px;width:75%;opacity:.15}.mc-skin-demo-line.short{width:40%;opacity:.08}
    .mc-skin-details{padding:20px 3px 8px;min-height:165px;box-sizing:border-box}
    .mc-skin-name{font-size:1rem;font-weight:800;line-height:1.7;margin-bottom:12px;color:var(--mc-text)}
    .mc-skin-desc{font-size:.85rem;line-height:1.9;color:var(--mc-muted)}
    .mc-skin-active{display:inline-flex;font-size:11px;padding:3px 8px;border-radius:99px;color:var(--mc-text);background:var(--mc-accent-soft);margin-bottom:8px}
    @media(max-width:1150px){div[data-testid="stHorizontalBlock"]:has(.mc-skin-preview){display:grid!important;grid-template-columns:repeat(2,minmax(0,1fr))!important}div[data-testid="stHorizontalBlock"]:has(.mc-skin-preview)>div[data-testid="column"]{width:auto!important;min-width:0!important}}
    @media(max-width:640px){div[data-testid="stHorizontalBlock"]:has(.mc-skin-preview){grid-template-columns:minmax(0,1fr)!important}}
    </style>''',unsafe_allow_html=True)
    st.markdown('''<div class="mc-themes-intro" id="mc-ui-settings-root">
      <div class="mc-themes-eyebrow">VISUAL SYSTEM &amp; THEMES</div>
      <div class="mc-themes-heading">视觉风格与主题切换</div>
      <div class="mc-themes-description">四套视觉语言，与复盘系统保持一致；每套都支持浅色与深色显示。</div>
    </div>''',unsafe_allow_html=True)
    st.caption('✓ 外观偏好保存在本机，重启后自动恢复；API 配置独立保留。')

    def change_mode():
        try:set_mode(st.session_state['ui_color_mode'])
        except OSError:
            st.session_state['ui_color_mode']=mode
            st.toast('外观偏好保存失败，请检查项目目录权限。',icon='❌')

    st.session_state['ui_color_mode']=mode
    st.radio('显示模式',['light','dark'],format_func=lambda m:'☀️ 浅色' if m=='light' else '🌙 深色',
        key='ui_color_mode',horizontal=True,on_change=change_mode)

    def apply_skin(value):
        try:
            set_skin(value)
            st.toast('已应用 '+SKINS[value]['badge'],icon='🎨')
        except OSError:st.toast('外观偏好保存失败，请检查项目目录权限。',icon='❌')

    columns=st.columns(4,gap='medium')
    for index,(key,info) in enumerate(SKINS.items()):
        # Black/gold's showcase stays dark as in the review system screenshot.
        showcase_mode='dark' if key=='liquid_glass' and mode=='light' else mode
        c=palette(key,showcase_mode)
        tokens=theme_tokens(key,showcase_mode)
        action=theme_tokens(key,'light')
        selected=key==skin
        owner='div[data-testid="stVerticalBlockBorderWrapper"]:has(>div>div>div[data-testid="element-container"] .mc-skin-card--'+key+')'
        with columns[index],st.container(border=True):
            st.markdown(f'''<style>
            {owner}{{border:2px solid {c['accent'] if selected else 'var(--mc-border)'}!important;border-radius:24px!important;overflow:hidden;box-shadow:var(--mc-shadow)!important;background:var(--mc-surface)!important}}
            {owner} button:not(:disabled){{background:{action['--mc-action-bg']}!important;color:#FFFFFF!important;border-color:{action['--mc-action-bg']}!important;border-radius:999px!important}}
            {owner} button:not(:disabled) :is(p,span,div){{color:#FFFFFF!important;-webkit-text-fill-color:#FFFFFF!important}}
            </style>''',unsafe_allow_html=True)
            preview=f'''<div class="mc-skin-card--{key}">
              <div class="mc-skin-preview" style="background:{tokens['--mc-bg']};color:{c['text']}">
                <div class="mc-skin-eyebrow" style="color:{c['accent']}">0{index+1} / {info['subtitle']}</div>
                <div class="mc-skin-demo" style="background:{c['surface']};border-color:{c['border']};color:{c['text']}">
                  <div class="mc-skin-demo-title">✦ {html.escape(info['badge'])} <span style="float:right;font-size:10px;color:{c['accent']}">物理工作台</span></div>
                  <div class="mc-skin-demo-line" style="background:{c['text']}"></div>
                  <div class="mc-skin-demo-line short" style="background:{c['text']}"></div>
                </div>
              </div>
              <div class="mc-skin-details">
                <div class="mc-skin-name">{html.escape(info['name'])}</div>
                {'<span class="mc-skin-active">✓ 当前使用中</span>' if selected else ''}
                <div class="mc-skin-desc">{html.escape(info['description'])}</div>
              </div></div>'''
            # No blank, indented lines: CommonMark otherwise turns the unselected
            # card's description into a code block after the optional badge.
            st.markdown(''.join(line.strip() for line in preview.splitlines()),unsafe_allow_html=True)
            st.button('已应用当前风格' if selected else '切换至 '+info['badge'],key='ui_apply_'+key,
                disabled=selected,use_container_width=True,on_click=apply_skin,args=(key,))
