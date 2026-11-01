# -*- coding: utf-8 -*-
"""
UtopiaPhyCyclus · 乌托邦高中物理题库助手
暖陶专属视觉系统、纯侧边栏与折叠功能端到端自动化测试
"""

import os
import sys
import unittest

# 将项目根目录加入模块检索路径
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

from utils.theme_system import (
    normalize_skin_id,
    generate_theme_css,
    WARM_TERRACOTTA_TOKENS,
)
from utils.charts import get_chart_theme_colors


class TestThemeAndSidebarUI(unittest.TestCase):
    def test_skin_normalization(self):
        """测试系统固定为暖陶设计语言"""
        self.assertEqual(normalize_skin_id("claude_warm"), "claude_warm")
        self.assertEqual(normalize_skin_id(""), "claude_warm")
        self.assertEqual(normalize_skin_id(None), "claude_warm")

    def test_warm_terracotta_tokens(self):
        """测试暖陶核心设计令牌的完整性与排版色彩"""
        required_tokens = [
            "--mc-bg",
            "--mc-surface",
            "--mc-surface-soft",
            "--mc-sidebar",
            "--mc-border",
            "--mc-border-strong",
            "--mc-text",
            "--mc-muted",
            "--mc-accent",
            "--mc-accent-dark",
            "--mc-action-bg",
            "--mc-action-text",
            "--mc-radius",
            "--mc-control-radius",
            "--mc-shadow",
        ]
        for token in required_tokens:
            self.assertIn(token, WARM_TERRACOTTA_TOKENS, f"Missing token {token} in WARM_TERRACOTTA_TOKENS")

        # 验证文本为焦木黑高对比
        self.assertEqual(WARM_TERRACOTTA_TOKENS["--mc-text"], "#26231E")
        self.assertEqual(WARM_TERRACOTTA_TOKENS["--mc-accent"], "#CC785C")

    def test_generate_theme_css(self):
        """测试暖陶 CSS 生成与侧边栏胶囊和折叠控件规则"""
        css = generate_theme_css()
        self.assertIn(":root", css)
        self.assertIn("#F7F4EC", css)  # 暖陶象牙暖底
        self.assertIn("#26231E", css)  # 焦木黑文字
        self.assertIn("data-testid=\"stSidebar\"", css)
        self.assertIn("stSidebarCollapseButton", css)
        self.assertIn("collapsedControl", css)
        self.assertIn("mc-sidebar-brand-box", css)
        self.assertIn("mc-sidebar-status", css)

    def test_charts_palette_integration(self):
        """测试 ECharts 图表暖陶专属色盘"""
        colors = get_chart_theme_colors(skin="claude_warm", mode="light")
        self.assertIn("light_palette", colors)
        self.assertIn("light_card_bg", colors)
        self.assertEqual(colors["light_card_bg"], "#FFFDF9")
        self.assertEqual(colors["light_text"], "#26231E")

    def test_main_app_top_bar_removal_and_sidebar_only(self):
        """测试主应用彻底移除了顶部条带，仅保留纯净纯侧边栏与收起功能"""
        app_file = os.path.join(APP_ROOT, "utopia_phycyclus_app.py")
        with open(app_file, "r", encoding="utf-8") as f:
            code = f.read()

        # 验证移除了顶部布局切换开关和顶栏控制条
        self.assertNotIn("navigation_layout == \"top\"", code)
        self.assertNotIn("col_hdr_left, col_hdr_date", code)
        self.assertNotIn("btn_top_bar_skin_select", code)
        self.assertNotIn("btn_top_bar_theme_toggle", code)

        # 验证侧边栏导航与收起态胶囊
        self.assertIn("mc-sidebar-status", code)
        self.assertIn('data-testid="stSidebar"][aria-expanded="false"]', generate_theme_css())

        # 验证导航名称已压缩到 4 字左右
        for short_label in (
            "📊  数据统计",
            "📝  智能录题",
            "📄  TeX录入",
            "🔍  全局浏览",
            "🖨️  组卷排版",
            "🛠️  维护工具",
            "🔎  题目查找",
            "📘  架构介绍",
            "📖  规范指南",
        ):
            self.assertIn(short_label, code)


if __name__ == "__main__":
    unittest.main()
