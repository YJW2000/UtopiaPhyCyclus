"""物理插图语义色别名表（模板兼容层，纯标准库、无第三方依赖）。

背景
----
AI / OCR 生成的 TikZ 插图经常使用 ``primaryblue``、``meterblue``、
``accentorange`` 这类「语义色名」。题库模板 ``UtopiaPhyCyclus_book.cls``
（ctexbook + tikz/xcolor）与 standalone 插图模板历史上并没有定义这些名字，
xelatex 在解析 TikZ 选项时就会报：

    Package pgfkeys Error: I do not know the key '/tikz/meterblue'

本模块是这组别名的唯一数据源，供三处共用：

1. ``UtopiaPhyCyclus_book.cls``：文档类导言区内联同一组 ``\\definecolor``；
2. ``utils/tikz_ops.py`` 的 ``_build_standalone_document``：插图 Agent 的
   standalone 模板在 ``\\tikzset`` 之前注入；
3. 题目图片导出模板：复用 :func:`as_tex_definitions` 的返回值。

约定
----
``as_tex_definitions()`` 只返回 ``\\definecolor{name}{RGB}{r,g,b}`` 行
（每行一个、无注释、末尾换行），可直接拼进 TeX 导言区。cls 不能 import
Python 模块，因此新增/修改颜色时，必须同步 ``UtopiaPhyCyclus_book.cls``
中的字面量定义块（测试会校验两处一致）。
"""

from __future__ import annotations

__all__ = ["DIAGRAM_COLOR_ALIASES", "as_tex_definitions"]

#: 语义色名 -> ((R, G, B), 中文含义)。
#: 键的插入顺序即 as_tex_definitions() 的生成顺序；cls 内联块须与之逐行一致。
DIAGRAM_COLOR_ALIASES = {
    "primaryblue": ((0, 90, 200), "主蓝（曲线/主线条）"),
    "secondaryblue": ((0, 174, 247), "次蓝"),
    "accentblue": ((31, 186, 190), "强调青蓝"),
    "meterblue": ((0, 120, 190), "电表蓝"),
    "lightblue": ((135, 206, 235), "浅蓝"),
    "darkblue": ((0, 0, 128), "深蓝"),
    "primaryorange": ((255, 134, 24), "主橙"),
    "accentorange": ((240, 120, 20), "强调橙"),
    "lightorange": ((255, 208, 181), "浅橙"),
    "primaryred": ((244, 105, 102), "主红"),
    "accentred": ((220, 20, 60), "强调红"),
    "primarygreen": ((0, 166, 82), "主绿"),
    "accentgreen": ((34, 139, 34), "强调绿"),
    "primaryyellow": ((255, 215, 0), "主黄"),
    "primarypurple": ((138, 43, 226), "主紫"),
    "accentpurple": ((128, 0, 128), "强调紫"),
    "primarycyan": ((0, 188, 212), "主青"),
    "primarygray": ((128, 128, 128), "主灰"),
    "accentgray": ((169, 169, 169), "强调灰"),
}


def as_tex_definitions() -> str:
    """把 DIAGRAM_COLOR_ALIASES 渲染成 xcolor 的 ``\\definecolor`` 定义块。

    返回值是纯定义行（不含注释、每行一个、末尾带换行），可直接插入
    TeX 导言区或 standalone 模板的 ``\\tikzset`` 之前。
    """
    lines = [
        f"\\definecolor{{{name}}}{{RGB}}{{{red},{green},{blue}}}"
        for name, ((red, green, blue), _meaning) in DIAGRAM_COLOR_ALIASES.items()
    ]
    return "\n".join(lines) + "\n"
