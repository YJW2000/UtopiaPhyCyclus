# -*- coding: utf-8 -*-
"""
UtopiaPhyCyclus · 乌托邦高中物理题库助手
视觉系统与界面风格规范：暖陶 (Warm Terracotta)
专属浅色暖陶美学：象牙羊皮纸暖底、典雅深焦木黑、Anthropic经典暖陶土色与高对比胶囊排版。
"""

import functools
import os
import textwrap
import streamlit as st
import streamlit.components.v1 as components
from .theme_palettes import normalize_skin, theme_tokens, retint_warm_markup
from .theme_overrides import component_overrides

# 暖陶 (Warm Terracotta) 核心色彩规范
WARM_TERRACOTTA_TOKENS = {
    "color-scheme": "light",
    "--mc-bg": "radial-gradient(at 15% 15%, rgba(204, 120, 92, 0.08) 0px, transparent 60%), #F7F4EC",
    "--mc-surface": "#FFFDF9",
    "--mc-surface-soft": "#F5EFE6",
    "--mc-surface-hover": "#EDE5D8",
    "--mc-sidebar": "#F4EFE5",
    "--mc-border": "rgba(214, 202, 185, 0.75)",
    "--mc-border-strong": "rgba(188, 172, 150, 0.90)",
    "--mc-text": "#26231E",
    "--mc-muted": "#756E63",
    "--mc-accent": "#CC785C",
    "--mc-accent-dark": "#B56345",
    "--mc-accent-soft": "rgba(204, 120, 92, 0.10)",
    "--mc-action-bg": "#CC785C",
    "--mc-action-bg-hover": "#B56345",
    "--mc-action-border": "#CC785C",
    "--mc-action-text": "#FFFDF9",
    "--mc-action-shadow": "0 6px 18px rgba(204, 120, 92, 0.28)",
    "--mc-focus": "rgba(204, 120, 92, 0.24)",
    "--mc-radius": "20px",
    "--mc-control-radius": "12px",
    "--mc-shadow": "0 8px 24px -4px rgba(120, 95, 60, 0.05), 0 1px 3px rgba(120, 95, 60, 0.02)",
    "--mc-backdrop": "none",
    "--mc-card-border": "1px solid rgba(214, 202, 185, 0.75)",
    "--mc-tooltip-bg": "#26231E",
    "--mc-tooltip-text": "#FFFDF9",
    # 与「复盘软件 · 暖陶」的 --font 完全一致：Charter/Georgia 正文 + 思源宋体中文
    "--mc-font-family": '"Charter", "Georgia", "Noto Serif SC", "Songti SC", -apple-system, BlinkMacSystemFont, "Plus Jakarta Sans", "PingFang SC", "Microsoft YaHei", sans-serif',
    "--mc-font-mono": '"Cascadia Code", "Cascadia Mono", Consolas, "JetBrains Mono", "Courier New", monospace',
}


def normalize_skin_id(skin: str = None) -> str:
    return normalize_skin(skin)


def _theme_css_revision() -> int:
    """本文件的修改时间：样式改动后让缓存自动失效，无需重启 Streamlit 进程。"""
    try:
        return max(os.stat(path).st_mtime_ns for path in (
            __file__, os.path.join(os.path.dirname(__file__), 'theme_palettes.py'),
            os.path.join(os.path.dirname(__file__), 'theme_overrides.py'),
            os.path.join(os.path.dirname(__file__), 'sidebar_theme.py')))
    except OSError:
        return 0


@functools.lru_cache(maxsize=8)
def _generate_theme_css_cached(skin_id: str = None, mode: str = None, revision: int = 0) -> str:
    """生成专属『暖陶』浅色视觉系统 CSS（结果缓存，避免每次 rerun 重复拼接大字符串）"""
    tokens_css = "\n".join(f"        {k}: {v};" for k, v in WARM_TERRACOTTA_TOKENS.items())

    return f"""
    <style>
    :root {{
{tokens_css}
    }}

    /* 全局画布与平滑渲染 */
    html {{
        scrollbar-gutter: stable;
        scroll-behavior: smooth;
        background: #F7F4EC !important;
    }}
    body,
    .stApp {{
        overflow-x: hidden;
        background: radial-gradient(at 15% 15%, rgba(204, 120, 92, 0.08) 0px, transparent 60%), #F7F4EC !important;
        color: #26231E !important;
        font-family: var(--mc-font-family) !important;
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
    }}

    /* ===== 字体体系：与复盘软件「暖陶」一致，全站控件统一到同一套字族 =====
       Streamlit 默认给按钮/输入框/下拉塞了自己的 Source Sans，这里统一改为继承；
       注意：不覆盖 span/div，避免打断 KaTeX 公式与代码高亮。 */
    body,
    .stApp,
    [data-testid="stAppViewContainer"],
    [data-testid="stSidebar"],
    [data-testid="stSidebarContent"],
    [data-testid="stMain"],
    h1, h2, h3, h4, h5, h6, p, label, li, a, th, td, small, blockquote,
    button, input, select, textarea,
    [data-baseweb="select"],
    [data-baseweb="input"],
    [data-baseweb="menu"],
    [data-baseweb="menu"] li,
    [data-baseweb="tag"],
    [data-testid="stMarkdownContainer"],
    [data-testid="stCaptionContainer"],
    [data-testid="stWidgetLabel"],
    [data-testid="stExpander"] summary,
    [data-testid="stTabs"] button,
    [data-testid="stAlert"],
    [data-testid="stToast"],
    [data-testid="stMetricValue"],
    [data-testid="stMetricLabel"],
    [data-testid="stMetricDelta"],
    [data-testid="stFileUploaderDropzone"],
    [data-testid="stFileUploaderDropzoneInstructions"],
    [data-testid="stTooltipContent"],
    [data-testid="stPopoverBody"],
    [data-testid="stNotification"],
    [data-testid="stStatusWidget"],
    [data-testid="stTable"],
    [data-testid="stDataFrame"],
    [data-testid="stForm"] {{
        font-family: var(--mc-font-family) !important;
    }}

    /* 代码 / TeX 源码编辑区保持等宽，便于对齐与排错 */
    code, kbd, samp, pre,
    [data-testid="stCode"] pre,
    [data-testid="stCode"] code,
    textarea[aria-label="TeX 源码"],
    textarea[aria-label="内容源码"],
    textarea[aria-label="批量内容编辑"],
    textarea[aria-label="本题 LaTeX 源码"],
    textarea[aria-label="源码"],
    textarea[aria-label="解答源码"],
    textarea[aria-label="题目内容 (LaTeX)"],
    textarea[aria-label="题目源码"],
    textarea[aria-label="文本内容"] {{
        font-family: var(--mc-font-mono) !important;
    }}

    /* 彻底隐藏原生顶栏，保留纯净侧边栏 */
    header[data-testid="stHeader"] {{
        background: transparent !important;
        height: 0 !important;
        min-height: 0 !important;
        overflow: visible !important;
        z-index: 9999 !important;
        border: none !important;
        box-shadow: none !important;
    }}
    div[data-testid="stToolbar"],
    div[data-testid="stDecoration"],
    #MainMenu,
    footer {{
        display: none !important;
        visibility: hidden !important;
        height: 0 !important;
    }}

    /* 主内容区优雅版心 */
    .block-container,
    div[data-testid="stAppViewBlockContainer"] {{
        max-width: 1600px !important;
        padding-top: 0.85rem !important;
        padding-bottom: 2.25rem !important;
        padding-left: 1.6rem !important;
        padding-right: 1.6rem !important;
    }}
    @media (max-width: 768px) {{
        .block-container {{
            padding-left: 1rem !important;
            padding-right: 1rem !important;
        }}
    }}

    /* ===== 全局信息密度：所有页面统一收紧标题、间距与表单高度 ===== */
    div[data-testid="stMainBlockContainer"] h1,
    div[data-testid="stMainBlockContainer"] h2 {{
        margin: 0.1rem 0 0.45rem !important;
        font-size: 1.7rem !important;
        line-height: 1.25 !important;
        letter-spacing: -0.01em !important;
    }}
    div[data-testid="stMainBlockContainer"] h3 {{
        margin: 0.15rem 0 0.35rem !important;
        font-size: 1.22rem !important;
        line-height: 1.3 !important;
    }}
    div[data-testid="stMainBlockContainer"] h4 {{
        margin: 0.1rem 0 0.25rem !important;
        font-size: 1.02rem !important;
        line-height: 1.3 !important;
    }}
    div[data-testid="stMainBlockContainer"] h5 {{
        margin: 0.1rem 0 0.2rem !important;
        font-size: 0.95rem !important;
        line-height: 1.3 !important;
    }}
    div[data-testid="stMainBlockContainer"] [data-testid="stHeadingWithActionElements"] {{
        margin-bottom: 0.1rem !important;
    }}
    div[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlock"] {{
        gap: 0.5rem !important;
    }}
    div[data-testid="stMainBlockContainer"] [data-testid="stHorizontalBlock"] {{
        gap: 0.6rem !important;
    }}
    /* 卡片 / 边框容器内边距收紧 */
    div[data-testid="stVMainBlockContainer"] [data-testid="stVerticalBlockBorderWrapper"],
    div[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlockBorderWrapper"] {{
        padding: 0.65rem 0.8rem !important;
    }}
    div[data-testid="stMainBlockContainer"] [data-testid="element-container"] {{
        margin-bottom: 0 !important;
    }}
    div[data-testid="stMainBlockContainer"] [data-testid="stCaptionContainer"] {{
        margin: 0.1rem 0 0 !important;
    }}
    div[data-testid="stMainBlockContainer"] [data-testid="stCaptionContainer"] p {{
        font-size: 0.78rem !important;
        line-height: 1.35 !important;
    }}
    div[data-testid="stMainBlockContainer"] [data-testid="stWidgetLabel"] p {{
        font-size: 0.84rem !important;
        margin-bottom: 0.15rem !important;
    }}
    div[data-testid="stMainBlockContainer"] hr {{
        margin: 0.35rem 0 !important;
    }}
    div[data-testid="stMainBlockContainer"] [data-testid="stMarkdownContainer"] p {{
        margin-bottom: 0.35rem !important;
    }}
    div[data-testid="stMainBlockContainer"] [data-testid="stForm"] {{
        padding: 0.75rem !important;
        border-radius: 12px !important;
    }}
    div[data-testid="stMainBlockContainer"] [data-testid="stExpander"] summary {{
        padding: 0.35rem 0.6rem !important;
        font-size: 0.88rem !important;
    }}
    div[data-testid="stMainBlockContainer"] [data-testid="stExpander"] [data-testid="stExpanderDetails"] {{
        padding: 0.25rem 0.6rem 0.5rem !important;
    }}
    /* ===== 表单控件质感：清晰边界 + 大圆角 + 柔和内阴影 ===== */
    div[data-testid="stMainBlockContainer"] [data-testid="stTextInput"] div[data-baseweb="input"],
    div[data-testid="stMainBlockContainer"] [data-testid="stNumberInput"] div[data-baseweb="input"],
    div[data-testid="stMainBlockContainer"] [data-testid="stTextArea"] div[data-baseweb="textarea"],
    div[data-testid="stMainBlockContainer"] [data-testid="stSelectbox"] div[data-baseweb="select"] > div,
    div[data-testid="stMainBlockContainer"] [data-testid="stMultiSelect"] div[data-baseweb="select"] > div,
    div[data-testid="stMainBlockContainer"] [data-testid="stDateInput"] div[data-baseweb="input"] {{
        border-radius: 11px !important;
        border: 1px solid rgba(160, 136, 105, 0.42) !important;
        background: #FFFDF9 !important;
        box-shadow:
            inset 0 1px 2px rgba(120, 95, 60, 0.07),
            0 1px 0 rgba(255, 255, 255, 0.65) !important;
        transition: border-color 0.16s ease, box-shadow 0.16s ease, background 0.16s ease !important;
    }}
    div[data-testid="stMainBlockContainer"] [data-testid="stTextInput"] div[data-baseweb="input"]:hover,
    div[data-testid="stMainBlockContainer"] [data-testid="stNumberInput"] div[data-baseweb="input"]:hover,
    div[data-testid="stMainBlockContainer"] [data-testid="stTextArea"] div[data-baseweb="textarea"]:hover,
    div[data-testid="stMainBlockContainer"] [data-testid="stSelectbox"] div[data-baseweb="select"] > div:hover,
    div[data-testid="stMainBlockContainer"] [data-testid="stMultiSelect"] div[data-baseweb="select"] > div:hover {{
        border-color: rgba(160, 136, 105, 0.62) !important;
    }}
    div[data-testid="stMainBlockContainer"] [data-testid="stTextInput"] div[data-baseweb="input"]:focus-within,
    div[data-testid="stMainBlockContainer"] [data-testid="stNumberInput"] div[data-baseweb="input"]:focus-within,
    div[data-testid="stMainBlockContainer"] [data-testid="stTextArea"] div[data-baseweb="textarea"]:focus-within,
    div[data-testid="stMainBlockContainer"] [data-testid="stSelectbox"] div[data-baseweb="select"] > div:focus-within,
    div[data-testid="stMainBlockContainer"] [data-testid="stMultiSelect"] div[data-baseweb="select"] > div:focus-within {{
        border-color: #CC785C !important;
        box-shadow:
            0 0 0 3px rgba(204, 120, 92, 0.14),
            inset 0 1px 2px rgba(120, 95, 60, 0.06) !important;
        background: #FFFDF9 !important;
    }}
    /* 输入框内部的 base-input 不再重复画边框 */
    div[data-testid="stMainBlockContainer"] [data-testid="stTextInput"] div[data-baseweb="base-input"],
    div[data-testid="stMainBlockContainer"] [data-testid="stNumberInput"] div[data-baseweb="base-input"] {{
        border: none !important;
        background: transparent !important;
        box-shadow: none !important;
        border-radius: 11px !important;
    }}
    div[data-testid="stMainBlockContainer"] [data-testid="stTextInput"] input,
    div[data-testid="stMainBlockContainer"] [data-testid="stNumberInput"] input {{
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        padding-left: 0.15rem !important;
    }}

    /* 表单控件统一压到 38px，页面更紧凑 */
    div[data-testid="stMainBlockContainer"] [data-testid="stTextInput"] input,
    div[data-testid="stMainBlockContainer"] [data-testid="stNumberInput"] input,
    div[data-testid="stMainBlockContainer"] [data-baseweb="select"] > div,
    div[data-testid="stMainBlockContainer"] [data-testid="stButton"] > button:not([kind="primary"]) {{
        min-height: 38px !important;
    }}
    div[data-testid="stMainBlockContainer"] [data-testid="stButton"] > button {{
        padding-left: 0.85rem !important;
        padding-right: 0.85rem !important;
    }}

    /* 侧边栏整体质感：温润羊皮纸底、精巧圆角与细边框 */
    section[data-testid="stSidebar"] {{
        background: #F4EFE5 !important;
        border-right: 1px solid rgba(214, 202, 185, 0.8) !important;
        box-shadow: 2px 0 16px rgba(120, 95, 60, 0.04) !important;
        transition: transform 0.28s cubic-bezier(0.4, 0, 0.2, 1), width 0.28s ease !important;
    }}

    /* 设置面板里的固定皮肤提示条 */
    .mc-theme-fixed-note {{
        display: inline-flex;
        align-items: center;
        gap: 6px;
        font-size: 12px;
        font-weight: 600;
        color: #8A6B52;
        background: rgba(204, 120, 92, 0.09);
        border: 1px solid rgba(204, 120, 92, 0.22);
        border-radius: 9px;
        padding: 6px 11px;
        margin: 0.2rem 0 0.55rem;
        line-height: 1.3;
    }}
    /* 侧边栏二级导航（智能录题的录入模式）：缩进 + 小一号字重，视觉上从属于一级导航 */
    .mc-subnav-title {{
        font-size: 10.5px;
        letter-spacing: 0.12em;
        color: #8A8175;
        font-weight: 750;
        margin: 0.55rem 0 0.15rem 0.9rem;
        padding-left: 0.15rem;
        border-left: 2px solid rgba(204, 120, 92, 0.35);
        line-height: 1.2;
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"][aria-label="录入模式"] {{
        gap: 2px !important;
        padding-left: 0.75rem !important;
        margin-top: 0.1rem !important;
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"][aria-label="录入模式"] > label {{
        padding: 0.3rem 0.55rem !important;
        border-radius: 8px !important;
        margin: 0 !important;
        transition: background 0.15s ease !important;
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"][aria-label="录入模式"] > label:hover {{
        background: rgba(204, 120, 92, 0.08) !important;
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"][aria-label="录入模式"] > label p,
    section[data-testid="stSidebar"] div[role="radiogroup"][aria-label="录入模式"] > label div {{
        font-size: 0.82rem !important;
        font-weight: 600 !important;
        line-height: 1.25 !important;
        color: #4A453D !important;
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"][aria-label="录入模式"] > label:has(input:checked) {{
        background: rgba(204, 120, 92, 0.14) !important;
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"][aria-label="录入模式"] > label:has(input:checked) p {{
        color: #B56345 !important;
    }}
    section[data-testid="stSidebar"] > div:first-child {{
        background: transparent !important;
        padding: 1rem 0.8rem 2rem !important;
    }}

    /* ===== 窄侧栏（宽度减半）：336px → 176px =====
       Streamlit 用内联 style 写死宽度，必须用 !important 覆盖；
       折叠态由下方 [aria-expanded="false"] 的 68px 规则接管。 */
    section[data-testid="stSidebar"][aria-expanded="true"] {{
        width: 176px !important;
        min-width: 176px !important;
        max-width: 176px !important;
        flex-basis: 176px !important;
    }}
    section[data-testid="stSidebar"][aria-expanded="true"] > div:first-child {{
        width: 176px !important;
        min-width: 176px !important;
        max-width: 176px !important;
        padding: 0.85rem 0.5rem 1.6rem !important;
    }}
    /* 窄栏内排版：图标/字号/内边距统一收一档 */
    section[data-testid="stSidebar"] .mc-sidebar-brand-box {{
        padding: 0.25rem 0 0.8rem !important;
        margin-bottom: 0.55rem !important;
    }}
    section[data-testid="stSidebar"] .mc-sidebar-avatar {{
        width: 66px !important;
        height: 66px !important;
        min-width: 66px !important;
        min-height: 66px !important;
        max-width: 66px !important;
        max-height: 66px !important;
        border-radius: 18px !important;
        margin-bottom: 10px !important;
    }}
    /* 下半组（当前页面在它上方）不显示选中高亮，避免出现两个"当前页" */
    section[data-testid="stSidebar"][aria-expanded="true"] div[role="radiogroup"][aria-label="工作流导航（续）"] > label:has(input:checked) {{
        background: transparent !important;
        color: #26231E !important;
        box-shadow: none !important;
        border-color: transparent !important;
    }}
    section[data-testid="stSidebar"][aria-expanded="true"] div[role="radiogroup"][aria-label="工作流导航（续）"] > label:has(input:checked) p {{
        color: #26231E !important;
        font-weight: 650 !important;
    }}
    /* 二级（录入模式）单行显示，避免六字换行 */
    section[data-testid="stSidebar"] div[role="radiogroup"][aria-label="录入模式"] > label {{
        justify-content: flex-start !important;
        padding-left: 16px !important;
        min-height: 31px !important;
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"][aria-label="录入模式"] > label p {{
        white-space: nowrap !important;
        font-size: 12.6px !important;
        text-align: left !important;
    }}

    /* ===== 侧栏排版对齐「复盘软件」：单行功能项 ===== */
    section[data-testid="stSidebar"] div[role="radiogroup"] {{
        gap: 7px !important;
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"] > label {{
        width: 100% !important;
        min-height: 46px !important;
        padding: 0 12px !important;
        gap: 11px !important;
        align-items: center !important;
        justify-content: flex-start !important;
        border-radius: 13px !important;
        flex: 0 0 auto !important;
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"] > label > div:last-child {{
        width: 100% !important;
        justify-content: flex-start !important;
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"] > label p {{
        font-size: 14.8px !important;
        font-weight: 640 !important;
        letter-spacing: -0.005em !important;
        line-height: 1.3 !important;
        white-space: nowrap !important;
        text-align: left !important;
        width: 100% !important;
    }}

    /* 二级选中态：深墨胶囊 + 白字 */
    section[data-testid="stSidebar"] div[role="radiogroup"][aria-label="录入模式"] > label:has(input:checked) {{
        background: #26231E !important;
        color: #FFFFFF !important;
        border-radius: 9px !important;
        box-shadow: 0 3px 10px rgba(38, 35, 30, 0.20) !important;
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"][aria-label="录入模式"] > label:has(input:checked) p {{
        color: #FFFFFF !important;
        font-weight: 700 !important;
    }}
    /* 二级（录入模式）：缩进 + 左侧竖线，与复盘软件的分组子项一致 */
    section[data-testid="stSidebar"] div[data-testid="stRadio"]:has(div[role="radiogroup"][aria-label="录入模式"]) {{
        position: relative !important;
        margin: 1px 0 3px 0 !important;
        padding-left: 19px !important;
    }}
    section[data-testid="stSidebar"] div[data-testid="stRadio"]:has(div[role="radiogroup"][aria-label="录入模式"])::before {{
        content: "" !important;
        position: absolute !important;
        left: 9px !important;
        top: 5px !important;
        bottom: 5px !important;
        width: 2px !important;
        border-radius: 2px !important;
        background: rgba(38, 35, 30, 0.10) !important;
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"][aria-label="录入模式"] > label {{
        min-height: 33px !important;
        padding: 0 9px !important;
        border-radius: 9px !important;
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"][aria-label="录入模式"] > label p {{
        font-size: 12.2px !important;
        font-weight: 560 !important;
        white-space: nowrap !important;
        text-align: left !important;
    }}
    section[data-testid="stSidebar"] .mc-subnav-title {{
        margin: 0.4rem 0 0.1rem 0.55rem !important;
        font-size: 9.5px !important;
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"][aria-label="录入模式"] {{
        padding-left: 0.4rem !important;
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"][aria-label="录入模式"] > label {{
        padding: 0.22rem 0.4rem !important;
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"][aria-label="录入模式"] > label p {{
        font-size: 11.6px !important;
    }}
    section[data-testid="stSidebar"] .mc-sidebar-status {{
        padding: 6px 8px !important;
        margin-top: 14px !important;
        margin-bottom: 8px !important;
        font-size: 10.6px !important;
        line-height: 1.3 !important;
        gap: 2px !important;
        white-space: nowrap !important;
        letter-spacing: -0.01em !important;
        text-align: center !important;
        border-radius: 9999px !important;
    }}

    /* 侧边栏折叠/展开按钮微晶胶囊（实际结构：#stSidebarCollapseButton > button） */
    [data-testid="stSidebarCollapseButton"],
    [data-testid="collapsedControl"],
    [data-testid="stSidebarCollapsedControl"] {{
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        visibility: visible !important;
    }}
    [data-testid="stSidebarCollapseButton"] button,
    [data-testid="collapsedControl"] button,
    [data-testid="stSidebarCollapsedControl"] button {{
        background: #FFFDF9 !important;
        border: 1px solid rgba(214, 202, 185, 0.9) !important;
        border-radius: 9999px !important;
        color: #26231E !important;
        box-shadow: 0 4px 14px rgba(120, 95, 60, 0.10) !important;
        transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
        cursor: pointer !important;
        width: 34px !important;
        height: 34px !important;
        min-height: 34px !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        padding: 0 !important;
    }}
    [data-testid="stSidebarCollapseButton"] button svg,
    [data-testid="collapsedControl"] button svg,
    [data-testid="stSidebarCollapsedControl"] button svg {{
        fill: currentColor !important;
        width: 18px !important;
        height: 18px !important;
    }}
    [data-testid="stSidebarCollapseButton"] button:hover,
    [data-testid="collapsedControl"] button:hover,
    [data-testid="stSidebarCollapsedControl"] button:hover {{
        background: #EDE5D8 !important;
        border-color: #CC785C !important;
        color: #CC785C !important;
        transform: scale(1.08) !important;
        box-shadow: 0 6px 20px rgba(204, 120, 92, 0.2) !important;
    }}
    [data-testid="collapsedControl"],
    [data-testid="stSidebarCollapsedControl"] {{
        top: 14px !important;
        left: 14px !important;
        z-index: 10001 !important;
        visibility: visible !important;
    }}

    /* 侧边栏品牌区域 */
    .mc-sidebar-brand-box {{
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        padding: 0.4rem 0 1.1rem;
        margin-bottom: 0.8rem;
        border-bottom: 1px solid rgba(214, 202, 185, 0.65);
        text-align: center;
    }}
    .mc-sidebar-avatar {{
        width: 62px !important;
        height: 62px !important;
        min-width: 62px !important;
        min-height: 62px !important;
        max-width: 62px !important;
        max-height: 62px !important;
        border-radius: 18px !important;
        box-shadow: 0 4px 14px rgba(120, 95, 60, 0.12) !important;
        margin-bottom: 8px !important;
        object-fit: cover !important;
        border: 1.5px solid rgba(214, 202, 185, 0.8) !important;
        display: block !important;
    }}
    .mc-sidebar-brand-title {{
        font-size: 15.5px !important;
        font-weight: 800 !important;
        color: #26231E !important;
        letter-spacing: -0.01em !important;
        line-height: 1.3 !important;
    }}
    .mc-sidebar-brand-sub {{
        font-size: 11.5px !important;
        color: #756E63 !important;
        margin-top: 3px !important;
        font-weight: 550 !important;
    }}
    /* 品牌区四行排版：乌托邦 / 高中物理题库 / 物理命题与 / 教研工作台 */
    .mc-brand-line1 {{
        font-size: 23px !important;
        font-weight: 800 !important;
        color: #26231E !important;
        letter-spacing: 0.06em !important;
        line-height: 1.15 !important;
        white-space: nowrap !important;
    }}
    .mc-brand-line2 {{
        font-size: 17.6px !important;
        font-weight: 800 !important;
        color: #26231E !important;
        letter-spacing: 0.005em !important;
        line-height: 1.25 !important;
        margin-top: 1px !important;
        white-space: nowrap !important;
    }}
    .mc-brand-line3 {{
        font-size: 13px !important;
        font-weight: 600 !important;
        color: #756E63 !important;
        letter-spacing: 0.24em !important;
        line-height: 1.5 !important;
        margin-top: 8px !important;
        padding-left: 0.24em !important;
        white-space: nowrap !important;
    }}
    .mc-brand-line4 {{
        font-size: 13px !important;
        font-weight: 600 !important;
        color: #756E63 !important;
        letter-spacing: 0.24em !important;
        line-height: 1.5 !important;
        padding-left: 0.24em !important;
        white-space: nowrap !important;
    }}

    /* ================= 侧边栏导航列表（彻底消灭原生单选圆圈，打造高对比度胶囊） ================= */
    [data-testid="stSidebar"] div[role="radiogroup"] {{
        display: flex !important;
        flex-direction: column !important;
        gap: 8px !important;
        width: 100% !important;
        padding: 0 !important;
        margin: 0 !important;
    }}
    [data-testid="stSidebar"] div[data-testid="stRadio"] > div {{
        width: 100% !important;
        padding: 0 !important;
    }}
    /* 彻底隐藏所有原生的单选圆圈、SVG 及占位 */
    [data-testid="stSidebar"] div[role="radiogroup"] input[type="radio"],
    [data-testid="stSidebar"] div[role="radiogroup"] [data-baseweb="radio"] > div:first-child,
    [data-testid="stSidebar"] div[role="radiogroup"] label > div:first-child,
    [data-testid="stSidebar"] div[role="radiogroup"] div[class*="radio"] {{
        display: none !important;
        width: 0 !important;
        height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        opacity: 0 !important;
        position: absolute !important;
        pointer-events: none !important;
    }}
    /* 一级导航项：整行、圆角 12px（对齐复盘软件侧栏） */
    section[data-testid="stSidebar"][aria-expanded="true"] div[role="radiogroup"] > label {{
        display: flex !important;
        align-items: center !important;
        justify-content: flex-start !important;
        width: 100% !important;
        min-width: 0 !important;
        max-width: 100% !important;
        min-height: 46px !important;
        margin: 2px 0 !important;
        padding: 0 12px !important;
        border-radius: 13px !important;
        background: transparent !important;
        border: 1px solid transparent !important;
        cursor: pointer !important;
        gap: 11px !important;
        transition: all 0.18s cubic-bezier(0.4, 0, 0.2, 1) !important;
    }}
    /* 文字排版：高清晰度深焦木黑 */
    [data-testid="stSidebar"] div[role="radiogroup"] label p,
    [data-testid="stSidebar"] div[role="radiogroup"] label span {{
        color: #26231E !important;
        font-size: 14.8px !important;
        font-weight: 650 !important;
        letter-spacing: 0.1px !important;
        margin: 0 !important;
        padding: 0 !important;
        opacity: 1 !important;
        white-space: nowrap !important;
        transition: color 0.15s ease !important;
    }}
    /* 未选中时悬浮态 */
    [data-testid="stSidebar"] div[role="radiogroup"] > label:not(:has(input:checked)):hover {{
        background: rgba(204, 120, 92, 0.12) !important;
        transform: translateX(2px) !important;
    }}
    [data-testid="stSidebar"] div[role="radiogroup"] > label:not(:has(input:checked)):hover p {{
        color: #CC785C !important;
    }}
    /* 激活选中态：沉稳深焦木黑药丸胶囊（参考复盘软件高对比黑金/黑曜石选中态） */
    [data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) {{
        background: #26231E !important;
        color: #FFFFFF !important;
        border-radius: 12px !important;
        box-shadow: 0 4px 14px rgba(38, 35, 30, 0.22) !important;
        border-color: #26231E !important;
        transform: translateX(0) !important;
    }}
    [data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) p,
    [data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) span {{
        color: #FFFFFF !important;
        font-weight: 800 !important;
    }}

    /* 侧边栏服务在线状态胶囊（同款绿色药丸） */
    .mc-sidebar-status {{
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        gap: 6px !important;
        padding: 7px 14px !important;
        margin-top: 18px !important;
        margin-bottom: 10px !important;
        border-radius: 9999px !important;
        background: rgba(16, 185, 129, 0.10) !important;
        color: #059669 !important;
        font-size: 12px !important;
        font-weight: 700 !important;
        border: 1px solid rgba(16, 185, 129, 0.25) !important;
    }}

    /* 标题与文字层级 */
    h1, h2, h3, h4, h5, h6 {{
        color: #26231E !important;
        font-family: var(--mc-font-family) !important;
        font-weight: 750 !important;
        letter-spacing: -0.015em !important;
    }}
    p, span, label, li {{
        color: #26231E;
        letter-spacing: 0;
    }}
    hr {{
        border: 0 !important;
        border-top: 1px solid rgba(214, 202, 185, 0.75) !important;
        margin: 1.5rem 0 !important;
    }}

    /* 现代卡片与容器：暖白羊皮纸质感 */
    div[data-testid="stExpander"],
    div[data-testid="stForm"],
    div[data-testid="stPopover"] > div,
    div[data-testid="stAlert"] {{
        border: 1px solid rgba(214, 202, 185, 0.75) !important;
        border-radius: 20px !important;
        background: #FFFDF9 !important;
        box-shadow: 0 8px 24px -4px rgba(120, 95, 60, 0.05), 0 1px 3px rgba(120, 95, 60, 0.02) !important;
        transition: box-shadow 0.25s ease, border-color 0.25s ease, transform 0.25s ease !important;
    }}

    /* 提示条（info / success / warning / error）统一改为暖陶纸质卡，消除蓝色信息框 */
    div[data-testid="stAlertContainer"],
    div[data-testid="stAlert"] > div,
    div[data-baseweb="notification"],
    div[data-testid="stAlertContentInfo"],
    div[data-testid="stAlertContentSuccess"],
    div[data-testid="stAlertContentWarning"],
    div[data-testid="stAlertContentError"] {{
        background: #F5EFE6 !important;
        border: 1px solid rgba(204, 120, 92, 0.32) !important;
        border-radius: 14px !important;
        box-shadow: none !important;
        color: #26231E !important;
    }}
    div[data-testid="stAlert"] p,
    div[data-testid="stAlert"] span,
    div[data-baseweb="notification"] p,
    div[data-baseweb="notification"] span,
    div[data-baseweb="notification"] div {{
        color: #26231E !important;
    }}
    div[data-testid="stAlert"] svg,
    [data-testid*="stAlert"] svg,
    div[data-baseweb="notification"] svg {{
        fill: #B56345 !important;
        color: #B56345 !important;
    }}
    div[data-testid="stAlert"] code,
    div[data-baseweb="notification"] code {{
        background: rgba(204, 120, 92, 0.14) !important;
        color: #B56345 !important;
        border-radius: 5px !important;
        padding: 1px 5px !important;
    }}

    /* 指标统计卡片 Metric */
    div[data-testid="stMetric"] {{
        background: #FFFDF9 !important;
        border: 1px solid rgba(214, 202, 185, 0.75) !important;
        border-radius: 20px !important;
        box-shadow: 0 8px 24px -4px rgba(120, 95, 60, 0.05) !important;
        padding: 18px 22px !important;
        transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease !important;
    }}
    div[data-testid="stMetric"]:hover {{
        transform: translateY(-2px);
        border-color: #CC785C !important;
        box-shadow: 0 14px 34px -6px rgba(204, 120, 92, 0.16) !important;
    }}
    div[data-testid="stMetricLabel"] p {{
        font-size: 0.88rem !important;
        font-weight: 650 !important;
        color: #756E63 !important;
    }}
    div[data-testid="stMetricValue"] {{
        font-weight: 800 !important;
        color: #26231E !important;
        letter-spacing: -0.02em !important;
    }}

    /* 输入框与选择器：圆角与微暖聚焦圈 */
    div[data-testid="stTextInput"] input,
    div[data-testid="stNumberInput"] input,
    div[data-testid="stTextArea"] textarea,
    div[data-baseweb="select"] > div {{
        border: 1px solid rgba(214, 202, 185, 0.85) !important;
        border-radius: 12px !important;
        background: #FFFDF9 !important;
        color: #26231E !important;
        transition: all 0.2s ease !important;
    }}
    div[data-testid="stTextInput"] input:focus,
    div[data-testid="stNumberInput"] input:focus,
    div[data-testid="stTextArea"] textarea:focus,
    div[data-baseweb="select"] > div:focus-within {{
        border-color: #CC785C !important;
        box-shadow: 0 0 0 3px rgba(204, 120, 92, 0.20) !important;
        background: #FFFDF9 !important;
    }}

    /* 现代胶囊按钮（同时覆盖带 help 提示的按钮：它们被包在 stTooltipHoverTarget 里） */
    div[data-testid="stButton"] button,
    div[data-testid="stButton"] > button,
    div[data-testid="stDownloadButton"] button,
    div[data-testid="stDownloadButton"] > button,
    div[data-testid="stPopover"] button,
    div[data-testid="stPopover"] > button,
    div[data-testid="stTooltipHoverTarget"] button,
    div[data-testid="stFormSubmitButton"] button,
    div[data-testid="stFormSubmitButton"] > button {{
        min-height: 2.35rem !important;
        border-radius: 9999px !important;
        border: 1px solid rgba(214, 202, 185, 0.85) !important;
        background: #FFFDF9 !important;
        color: #26231E !important;
        font-weight: 650 !important;
        font-size: 0.86rem !important;
        box-shadow: 0 2px 6px rgba(120, 95, 60, 0.04) !important;
        transition: all 0.18s cubic-bezier(0.4, 0, 0.2, 1) !important;
        padding: 6px 18px !important;
        opacity: 1 !important;
    }}
    /* 按钮内文字/图标一律使用暖陶墨色，避免继承到深色主题的浅色或反色后看不清 */
    div[data-testid="stButton"] button p,
    div[data-testid="stButton"] button span,
    div[data-testid="stDownloadButton"] button p,
    div[data-testid="stDownloadButton"] button span,
    div[data-testid="stPopover"] button p,
    div[data-testid="stPopover"] button span,
    div[data-testid="stTooltipHoverTarget"] button p,
    div[data-testid="stTooltipHoverTarget"] button span,
    div[data-testid="stFormSubmitButton"] button p,
    div[data-testid="stFormSubmitButton"] button span {{
        color: inherit !important;
    }}
    div[data-testid="stButton"] button:hover,
    div[data-testid="stButton"] > button:hover,
    div[data-testid="stDownloadButton"] button:hover,
    div[data-testid="stDownloadButton"] > button:hover,
    div[data-testid="stPopover"] button:hover,
    div[data-testid="stPopover"] > button:hover,
    div[data-testid="stTooltipHoverTarget"] button:hover,
    div[data-testid="stFormSubmitButton"] button:hover,
    div[data-testid="stFormSubmitButton"] > button:hover {{
        border-color: #CC785C !important;
        background: rgba(204, 120, 92, 0.08) !important;
        color: #CC785C !important;
        transform: translateY(-1.5px) !important;
        box-shadow: 0 6px 18px rgba(204, 120, 92, 0.12) !important;
    }}
    div[data-testid="stButton"] button:active,
    div[data-testid="stButton"] > button:active,
    div[data-testid="stDownloadButton"] button:active,
    div[data-testid="stDownloadButton"] > button:active,
    div[data-testid="stPopover"] button:active,
    div[data-testid="stPopover"] > button:active,
    div[data-testid="stTooltipHoverTarget"] button:active,
    div[data-testid="stFormSubmitButton"] button:active {{
        transform: translateY(0) !important;
    }}

    /* 主要行动按键 (Primary) 胶囊：暖陶主色 */
    button[kind="primary"],
    div[data-testid="stButton"] > button[kind="primary"],
    div[data-testid="stButton"] button[kind="primary"],
    div[data-testid="stTooltipHoverTarget"] button[kind="primary"],
    div[data-testid="stFormSubmitButton"] button[kind="primary"] {{
        background: var(--mc-action-bg) !important;
        border-color: var(--mc-action-border) !important;
        color: var(--mc-action-text) !important;
        box-shadow: var(--mc-action-shadow) !important;
        border-radius: 9999px !important;
    }}
    button[kind="primary"] p,
    button[kind="primary"] span,
    div[data-testid="stButton"] > button[kind="primary"] p,
    div[data-testid="stButton"] > button[kind="primary"] span {{
        color: var(--mc-action-text) !important;
    }}
    button[kind="primary"]:hover,
    div[data-testid="stButton"] > button[kind="primary"]:hover {{
        background: var(--mc-action-bg-hover) !important;
        border-color: var(--mc-action-bg-hover) !important;
        color: var(--mc-action-text) !important;
        box-shadow: 0 10px 26px rgba(204, 120, 92, 0.30) !important;
        transform: translateY(-1.5px) !important;
    }}

    /* ============================================================
       原生组件全面暖陶化：对话框 / 文件上传 / 聊天输入 / 菜单 / 代码块
       彻底消除系统深色模式下出现的"雷霆大黑框"
       ============================================================ */
    div[data-testid="stDialog"],
    div[data-testid="stDialog"] > div,
    div[role="dialog"] {{
        background: #FFFDF9 !important;
        color: #26231E !important;
        border: 1px solid rgba(214, 202, 185, 0.9) !important;
        border-radius: 24px !important;
        box-shadow: 0 30px 72px -20px rgba(120, 95, 60, 0.35) !important;
    }}
    div[data-testid="stDialog"] *:not(svg):not(path),
    div[role="dialog"] *:not(svg):not(path) {{
        color: #26231E !important;
    }}
    div[data-testid="stDialog"] h1,
    div[data-testid="stDialog"] h2,
    div[data-testid="stDialog"] h3,
    div[data-testid="stDialog"] h4,
    div[role="dialog"] h1,
    div[role="dialog"] h2,
    div[role="dialog"] h3 {{
        color: #26231E !important;
        font-weight: 800 !important;
    }}
    div[data-testid="stDialog"] button[aria-label="Close"],
    div[role="dialog"] button[aria-label="Close"],
    div[data-testid="stDialog"] button[kind="header"] {{
        background: transparent !important;
        color: #26231E !important;
        border: none !important;
        border-radius: 9999px !important;
    }}
    div[data-testid="stDialog"] button[aria-label="Close"]:hover,
    div[role="dialog"] button[aria-label="Close"]:hover {{
        background: rgba(204, 120, 92, 0.12) !important;
        color: #CC785C !important;
    }}
    div[data-testid="stDialog"] div[data-testid="stAlert"],
    div[role="dialog"] div[data-testid="stAlert"] {{
        background: #F5EFE6 !important;
        border: 1px solid rgba(204, 120, 92, 0.35) !important;
        border-radius: 14px !important;
        color: #26231E !important;
    }}
    div[data-testid="stDialog"] code,
    div[role="dialog"] code {{
        background: #F5EFE6 !important;
        color: #B56345 !important;
        border-radius: 5px !important;
        padding: 1px 5px !important;
    }}

    /* 文件上传控件 */
    div[data-testid="stFileUploader"] section,
    div[data-testid="stFileUploaderDropzone"],
    div[data-testid="stFileUploadDropzone"] {{
        background: #FFFDF9 !important;
        border: 1.5px dashed rgba(204, 120, 92, 0.45) !important;
        border-radius: 18px !important;
        color: #26231E !important;
        transition: border-color 0.2s ease, background 0.2s ease !important;
    }}
    div[data-testid="stFileUploader"] section:hover,
    div[data-testid="stFileUploaderDropzone"]:hover {{
        border-color: #CC785C !important;
        background: #FDF8F2 !important;
    }}
    div[data-testid="stFileUploader"] section *,
    div[data-testid="stFileUploaderDropzone"] * {{
        color: #26231E !important;
    }}
    div[data-testid="stFileUploader"] section small,
    div[data-testid="stFileUploaderDropzone"] small {{
        color: #756E63 !important;
    }}
    div[data-testid="stFileUploader"] section button,
    div[data-testid="stFileUploaderDropzone"] button {{
        background: #CC785C !important;
        border: 1px solid #CC785C !important;
        border-radius: 9999px !important;
        color: #FFFDF9 !important;
        font-weight: 650 !important;
        box-shadow: 0 4px 12px rgba(204, 120, 92, 0.25) !important;
    }}
    div[data-testid="stFileUploader"] section button:hover,
    div[data-testid="stFileUploaderDropzone"] button:hover {{
        background: #B56345 !important;
        border-color: #B56345 !important;
    }}

    /* 上传列表与已上传文件卡片 */
    div[data-testid="stFileUploaderFile"],
    li[data-testid="stFileUploaderFile"] {{
        background: #F5EFE6 !important;
        border: 1px solid rgba(214, 202, 185, 0.8) !important;
        border-radius: 12px !important;
        color: #26231E !important;
    }}

    /* 聊天输入胶囊（含底部悬浮输入栏） */
    div[data-testid="stChatInput"],
    div[data-testid="stChatInput"] > div,
    div[data-testid="stChatInput"] div[data-baseweb="textarea"],
    div[data-testid="stChatInput"] div[data-baseweb="base-input"],
    div[data-testid="stChatInput"] div[data-baseweb="textarea"] > div {{
        background: #FFFDF9 !important;
        color: #26231E !important;
        border: 1px solid rgba(214, 202, 185, 0.9) !important;
        border-radius: 18px !important;
    }}
    div[data-testid="stChatInput"] textarea {{
        background: transparent !important;
        color: #26231E !important;
    }}
    div[data-testid="stChatInput"] textarea::placeholder {{
        color: #A89F91 !important;
    }}
    div[data-testid="stChatInput"] button {{
        background: #CC785C !important;
        border: 1px solid #CC785C !important;
        border-radius: 9999px !important;
        color: #FFFDF9 !important;
    }}
    div[data-testid="stChatInput"] button:hover {{
        background: #B56345 !important;
        border-color: #B56345 !important;
    }}
    [data-testid="stBottom"],
    [data-testid="stBottom"] > div,
    [data-testid="stBottomBlockContainer"] {{
        background: transparent !important;
        border: none !important;
    }}

    /* 会话消息气泡 */
    div[data-testid="stChatMessage"] {{
        background: #FFFDF9 !important;
        border: 1px solid rgba(214, 202, 185, 0.8) !important;
        border-radius: 18px !important;
        box-shadow: 0 6px 18px -6px rgba(120, 95, 60, 0.10) !important;
    }}

    /* 代码块与行内代码 */
    div[data-testid="stCode"] pre,
    div[data-testid="stCode"] div,
    pre,
    pre code {{
        background: #F5EFE6 !important;
        color: #3B342C !important;
        border: 1px solid rgba(214, 202, 185, 0.8) !important;
        border-radius: 14px !important;
    }}
    code:not(pre code) {{
        background: #F5EFE6 !important;
        color: #B56345 !important;
        border: 1px solid rgba(214, 202, 185, 0.7) !important;
        border-radius: 6px !important;
        padding: 0.12em 0.4em !important;
    }}

    /* 下拉菜单 / 弹层 / 提示 */
    div[data-baseweb="popover"],
    div[data-baseweb="menu"],
    ul[role="listbox"],
    div[data-baseweb="select"] div[role="listbox"] {{
        background: #FFFDF9 !important;
        border: 1px solid rgba(214, 202, 185, 0.9) !important;
        border-radius: 14px !important;
        box-shadow: 0 16px 40px -12px rgba(120, 95, 60, 0.25) !important;
    }}
    li[role="option"],
    div[data-baseweb="menu"] li {{
        color: #26231E !important;
    }}
    li[role="option"]:hover,
    li[role="option"][aria-selected="true"] {{
        background: rgba(204, 120, 92, 0.12) !important;
        color: #B56345 !important;
    }}
    div[data-testid="stTooltipContent"],
    div[data-baseweb="tooltip"] {{
        background: #26231E !important;
        color: #FFFDF9 !important;
        border-radius: 10px !important;
        padding: 6px 10px !important;
        box-shadow: 0 10px 24px rgba(38, 35, 30, 0.25) !important;
    }}
    div[data-testid="stToast"],
    div[data-testid="stToast"] > div {{
        background: #FFFDF9 !important;
        color: #26231E !important;
        border: 1px solid rgba(214, 202, 185, 0.9) !important;
        border-radius: 14px !important;
        box-shadow: 0 16px 40px -12px rgba(120, 95, 60, 0.25) !important;
    }}
    div[data-testid="stExpander"] summary,
    div[data-testid="stExpander"] summary p,
    div[data-testid="stExpander"] summary span {{
        color: #26231E !important;
    }}
    div[data-testid="stTabs"] button {{
        color: #756E63 !important;
    }}
    div[data-testid="stTabs"] button[aria-selected="true"] {{
        color: #B56345 !important;
    }}
    div[data-testid="stTabs"] div[data-baseweb="tab-highlight"] {{
        background: #CC785C !important;
    }}
    div[data-testid="stDataFrame"],
    div[data-testid="stTable"] {{
        border: 1px solid rgba(214, 202, 185, 0.85) !important;
        border-radius: 16px !important;
        box-shadow: 0 8px 24px -6px rgba(120, 95, 60, 0.10) !important;
    }}
    div[data-testid="stSlider"] div[role="slider"] {{
        background: #CC785C !important;
        border-color: #CC785C !important;
    }}
    div[data-testid="stCheckbox"] span[aria-checked="true"],
    div[data-testid="stToggle"] label[data-baseweb="checkbox"] span[aria-checked="true"] {{
        background: #CC785C !important;
        border-color: #CC785C !important;
    }}

    /* ============================================================
       侧边栏收起态：纯 CSS 图标栏（零 JS、零 iframe，切换零额外开销）
       收起后侧栏固定为 68px，仅显示 emoji 图标；悬浮时向右浮出完整文字
       ============================================================ */
    section[data-testid="stSidebar"][aria-expanded="false"] {{
        transform: none !important;
        width: 68px !important;
        min-width: 68px !important;
        max-width: 68px !important;
        overflow: visible !important;
        border-right: 1px solid rgba(214, 202, 185, 0.85) !important;
    }}
    section[data-testid="stSidebar"][aria-expanded="false"] > div,
    section[data-testid="stSidebar"][aria-expanded="false"] [data-testid="stSidebarContent"],
    section[data-testid="stSidebar"][aria-expanded="false"] [data-testid="stSidebarUserContent"] {{
        width: 68px !important;
        min-width: 68px !important;
        max-width: 68px !important;
        padding-left: 6px !important;
        padding-right: 6px !important;
        overflow: visible !important;
    }}
    /* 收起时隐藏品牌区 / 状态条 / 设置按钮，只留导航图标 */
    section[data-testid="stSidebar"][aria-expanded="false"] .mc-sidebar-brand-box,
    section[data-testid="stSidebar"][aria-expanded="false"] .mc-sidebar-status,
    section[data-testid="stSidebar"][aria-expanded="false"] div[data-testid="stButton"],
    section[data-testid="stSidebar"][aria-expanded="false"] div[data-testid="stLogoSpacer"] {{
        display: none !important;
    }}
    /* 收起态：二级导航（录入模式）整块隐藏，避免文字被裁成「单题/批量/同卷」 */
    section[data-testid="stSidebar"][aria-expanded="false"] div[data-testid="stRadio"]:has(div[role="radiogroup"][aria-label="录入模式"]),
    section[data-testid="stSidebar"][aria-expanded="false"] div[data-testid="stRadio"]:has(div[role="radiogroup"][aria-label="设置子功能"]) {{
        display: none !important;
    }}
    section[data-testid="stSidebar"][aria-expanded="false"] div[role="radiogroup"][aria-label="工作流导航（续）"] {{
        margin-top: 2px !important;
    }}
    /* 收起态：容器与按钮都严格居中，留出 1px 边框空间避免被裁 */
    section[data-testid="stSidebar"][aria-expanded="false"] [data-testid="stSidebarUserContent"] {{
        padding-left: 6px !important;
        padding-right: 6px !important;
    }}
    section[data-testid="stSidebar"][aria-expanded="false"] div[role="radiogroup"] {{
        align-items: center !important;
        overflow: visible !important;
    }}

    /* 收起时每个导航项变成 44×44 图标按钮 */
    section[data-testid="stSidebar"][aria-expanded="false"] div[role="radiogroup"] {{
        gap: 8px !important;
        padding-top: 4px !important;
    }}
    section[data-testid="stSidebar"][aria-expanded="false"] div[role="radiogroup"] > label {{
        position: relative !important;
        width: 44px !important;
        min-width: 44px !important;
        height: 44px !important;
        min-height: 44px !important;
        padding: 0 !important;
        margin: 0 auto !important;
        border-radius: 14px !important;
        border: 1px solid rgba(214, 202, 185, 0.85) !important;
        background: #FFFDF9 !important;
        justify-content: center !important;
        box-shadow: 0 3px 10px rgba(120, 95, 60, 0.08) !important;
    }}
    section[data-testid="stSidebar"][aria-expanded="false"] div[role="radiogroup"] > label:hover {{
        background: #EDE5D8 !important;
        border-color: #CC785C !important;
        transform: none !important;
    }}
    section[data-testid="stSidebar"][aria-expanded="false"] div[role="radiogroup"] > label:has(input:checked) {{
        background: #26231E !important;
        border-color: #26231E !important;
        box-shadow: 0 4px 14px rgba(38, 35, 30, 0.24) !important;
    }}
    /* 图标：只露出 emoji，其余文字裁掉 */
    section[data-testid="stSidebar"][aria-expanded="false"] div[role="radiogroup"] > label p {{
        display: block !important;
        width: 22px !important;
        height: 22px !important;
        overflow: hidden !important;
        white-space: nowrap !important;
        font-size: 16px !important;
        line-height: 22px !important;
        text-align: center !important;
        color: #26231E !important;
    }}
    section[data-testid="stSidebar"][aria-expanded="false"] div[role="radiogroup"] > label:has(input:checked) p {{
        color: #FFFDF9 !important;
    }}
    /* 悬浮：向右浮出完整功能名（纯 CSS tooltip） */
    section[data-testid="stSidebar"][aria-expanded="false"] div[role="radiogroup"] > label:hover p {{
        position: absolute !important;
        left: 54px !important;
        top: 50% !important;
        transform: translateY(-50%) !important;
        width: auto !important;
        height: auto !important;
        overflow: visible !important;
        padding: 6px 12px !important;
        border-radius: 9px !important;
        background: #26231E !important;
        color: #FFFDF9 !important;
        font-size: 12.5px !important;
        font-weight: 650 !important;
        line-height: 1.2 !important;
        white-space: nowrap !important;
        box-shadow: 0 10px 24px rgba(38, 35, 30, 0.28) !important;
        z-index: 10050 !important;
        pointer-events: none !important;
    }}

    /* 滚动条 */
    ::-webkit-scrollbar {{
        width: 8px;
        height: 8px;
    }}
    ::-webkit-scrollbar-track {{
        background: transparent;
    }}
    ::-webkit-scrollbar-thumb {{
        background: rgba(214, 202, 185, 0.85);
        border-radius: 9999px;
    }}
    ::-webkit-scrollbar-thumb:hover {{
        background: #756E63;
    }}
    ::selection {{
        background: rgba(204, 120, 92, 0.20) !important;
        color: #26231E !important;
    }}
    </style>
    """

def generate_theme_css(skin_id: str = None, mode: str = None) -> str:
    """对外接口：缓存键包含本文件版本，样式改动后自动取到新 CSS（无需重启进程）。"""
    return _assemble_theme_css(normalize_skin_id(skin_id), 'dark' if mode=='dark' else 'light', _theme_css_revision())


@functools.lru_cache(maxsize=8)
def _assemble_theme_css(skin, mode, revision):
    original=textwrap.dedent(_generate_theme_css_cached('claude_warm','light',revision)).strip()
    themed=retint_warm_markup(original,skin,mode)
    return themed+'\n\n'+component_overrides(theme_tokens(skin,mode))+'\n'
