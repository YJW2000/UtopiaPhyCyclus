# -*- coding: utf-8 -*-
"""统计页双环饼图标签换行回归测试。

用户反馈：难度分布标签显示成 `5-6星 (压轴)\\n1道` 这样的字面 `\\n`。
根因是 f-string 模板里写成了 `\\\\n`，最终 HTML 中的 JS 字符串是 `'...\\\\n...'`，
JS 解析后得到的是反斜杠 + n 两个字符，ECharts 自然原样显示。
"""
import re
import unittest

from utils.charts import generate_echarts_pie_html


class PieChartLabelNewlineTests(unittest.TestCase):
    def render(self):
        return generate_echarts_pie_html(
            {'单项选择题': 30, '多项选择题': 4, '实验题': 4},
            {'0-2星 (基础)': 17, '3-4星 (中档)': 20, '5-6星 (压轴)': 1},
            '题型占比分析及难度分布',
        )

    def test_outer_donut_formatter_uses_real_js_newline(self):
        html = self.render()
        formatter_lines = [line for line in html.splitlines()
                           if 'formatter:' in line and '{c|' in line]
        self.assertEqual(1, len(formatter_lines), '应找到难度分布标签 formatter')
        line = formatter_lines[0]
        # f-string 模板必须输出单个 \n（JS 转义后是真正换行），不能是 \\n（字面反斜杠+n）
        self.assertIn(r"{b|{b}}\n{c|{c}道}", line)
        self.assertNotIn(r"{b|{b}}\\n{c|{c}道}", line)

    def test_labels_show_count_on_second_line(self):
        html = self.render()
        # 模拟浏览器 JS 字符串解析后的 formatter 内容
        decoded = re.sub(r'\\\\', '\x00', html).replace('\\n', '\n')
        match = re.search(r"'{b\|{b}}\n{c\|{c}道}'", decoded)
        self.assertIsNotNone(match, '标签应在名称与“N道”之间换行')

    def test_pie_data_still_embedded(self):
        import json

        html = self.render()
        # json.dumps 默认把中文转成 \uXXXX，用同样的序列化结果断言
        for name in ('0-2星 (基础)', '5-6星 (压轴)', '实验题'):
            self.assertIn(json.dumps(name), html)


if __name__ == '__main__':
    unittest.main()
