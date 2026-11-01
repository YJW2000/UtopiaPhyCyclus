# -*- coding: utf-8 -*-
"""运行时冒烟测试：启动无异常、侧栏导航覆盖所有一级功能、每条路由都能渲染。

注意：导航按「分组」渲染（nav_group_a / nav_group_b），且使用 format_func
（图标一行 + 名称两行），AppTest 里 radio.options 返回的是格式化后的文本，
断言统一做「去空白归一化」再比较。
"""

import os
import re
import sys

# 保证控制台支持 UTF-8 打印包含 emoji 的选项
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

from streamlit.testing.v1 import AppTest

EXPECTED_OPTIONS = [
    "📊  数据统计",
    "📝  智能录题",
    "📄  TeX录入",
    "🔍  全局浏览",
    "🖨️  组卷排版",
    "🛠️  维护工具",
    "🔎  题目查找",
    "📘  架构介绍",
    "📖  规范指南",
    "⚙️  设置",
]

NAV_KEYS = ("nav_group_a", "nav_group_b")


def _norm(text) -> str:
    """去掉空白与分组展开箭头，便于和期望选项比较。"""
    cleaned = str(text or "")
    for mark in ("▴", "▾", "⌄", "⌃"):
        cleaned = cleaned.replace(mark, "")
    return "".join(cleaned.split())


def _nav_groups(at):
    return [r for r in at.sidebar.radio if r.key in NAV_KEYS]

def _nav_name(formatted) -> str:
    """去掉 emoji 前缀，取出功能名（用于校验长度）。"""
    cleaned = _norm(formatted)
    match = re.search(r"([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9]*)$", cleaned)
    return match.group(1) if match else cleaned



def test_app_startup_and_short_sidebar():
    print("Initializing AppTest from utopia_phycyclus_app.py...")
    at = AppTest.from_file(os.path.join(APP_ROOT, "utopia_phycyclus_app.py"), default_timeout=60)
    at.run()
    if at.exception:
        print("Startup exception:", at.exception)
        sys.exit(1)
    print("Startup success without exception!")

    groups = _nav_groups(at)
    if not groups:
        print("ERROR: nav_group_a / nav_group_b not found in sidebar!")
        sys.exit(1)

    nav_norm = []
    for group in groups:
        print(f"Sidebar nav group {group.key}:", group.options)
        nav_norm.extend(_norm(opt) for opt in group.options)

    for opt in EXPECTED_OPTIONS:
        assert _norm(opt) in nav_norm, f"{opt} must be in nav options!"

    for opt in nav_norm:
        name = _nav_name(opt)
        assert len(name) <= 6, f"{opt} 功能名过长（{name}），应压缩到 4 个字左右"

    buttons = [b.label for b in at.button]
    print("Buttons present:", buttons)
    assert not any("切换至" in b for b in buttons), "皮肤切换按钮应已移除"

    for nav_item in EXPECTED_OPTIONS:
        print(f"Testing route: {nav_item}...")
        wanted = _norm(nav_item)
        target = None
        for _attempt in range(3):
            for group in _nav_groups(at):
                if any(_norm(opt) == wanted for opt in group.options):
                    target = group
                    break
            if target is not None:
                break
            # 目标可能在另一分组里：先切回第一组首项触发分组重组
            first = _nav_groups(at)[0]
            first.set_value(EXPECTED_OPTIONS[0]).run()

        assert target is not None, f"{nav_item} 不在任何导航分组里"
        target.set_value(nav_item).run()  # 传原始选项值，format_func 会自行格式化
        if at.exception:
            print(f"Exception on route {nav_item}:", at.exception)
            sys.exit(1)
        print(f"Route {nav_item} rendered cleanly!")

    print("All warm terracotta runtime checks passed!")


if __name__ == "__main__":
    test_app_startup_and_short_sidebar()
