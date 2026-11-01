import tempfile
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest
from services.fuzzy_search_service import build_documents, search, sort_results


class FuzzySearchTests(unittest.TestCase):
    def test_each_text_and_metadata_field_is_searchable(self):
        for field in ("题干", "标签", "知识点", "物理方法", "知识板块", "解析", "答案", "备注", "试卷名称"):
            with self.subTest(field=field):
                rows = [{field: r"\textbf{自定义关联术语}", "题目ID": "1"}, {"题干": "无关内容"}]
                self.assertEqual([hit["row"]["题目ID"] for hit in search(build_documents(rows), "自定义关联术语")], ["1"])

    def test_alias_partial_typo_and_chinese_compound_queries(self):
        docs = build_documents([
            {"题目ID": "force", "知识点": "牛顿第二定律"},
            {"题目ID": "impact", "物理方法": "动量守恒定律", "标签": "碰撞"},
            {"题目ID": "split", "物理方法": "运动的合成与分解"},
            {"题目ID": "other", "题干": "光的折射"},
        ])
        for query in ("牛二", "牛顿第二", "牛顿第而定律", "帮我查找关于牛二的题目"):
            self.assertEqual(search(docs, query)[0]["row"]["题目ID"], "force")
        self.assertEqual(search(docs, "动量守恒碰撞")[0]["row"]["题目ID"], "impact")
        self.assertEqual(search(docs, "运动的合成与分解")[0]["row"]["题目ID"], "split")
        for query in ("", "  ", "！？", "没有任何匹配的关键词"):
            self.assertEqual(search(docs, query), [])
        docs = build_documents([{"题干": "上方有一盏灯"}, {"题干": "小球放在斜面"}])
        self.assertEqual(len(search(docs, "小球在斜面上")), 1)

    def test_multi_field_coverage_and_literal_matches_rank_first(self):
        docs = build_documents([
            {"题目ID": "related", "标签": "反冲"},
            {"题目ID": "literal", "标签": "碰撞"},
            {"题目ID": "both", "题干": "两个小球碰撞", "物理方法": "能量守恒"},
        ])
        hits = search(docs, "碰撞 能量守恒")
        self.assertEqual([hit["row"]["题目ID"] for hit in hits], ["both", "literal", "related"])
        self.assertIn("物理方法", hits[0]["reason"])

    def test_all_sorts_unknown_values_last_and_stable_ties(self):
        rows = [
            {"题目ID": "old", "初次录入的时间": "2025-01-01 12:00:00", "难度星级": "1.5"},
            {"题目ID": "new", "初次录入的时间": "2026-01-01 12:00:00", "难度星级": "5"},
            {"题目ID": "unknown", "初次录入的时间": "bad", "难度星级": "NaN"},
        ]
        hits = [{"row": row, "score": i} for i, row in enumerate(rows)]
        for order, expected in (
            ("相关度", ["unknown", "new", "old"]),
            ("录入时间：由新到旧", ["new", "old", "unknown"]),
            ("录入时间：由旧到新", ["old", "new", "unknown"]),
            ("难度：由高到低", ["new", "old", "unknown"]),
            ("难度：由低到高", ["old", "new", "unknown"]),
        ):
            self.assertEqual([h["row"]["题目ID"] for h in sort_results(hits, order)], expected)
        self.assertEqual([h["row"]["题目ID"] for h in hits], ["old", "new", "unknown"])

    def test_page_search_sort_paging_edit_refresh_and_empty_query(self):
        with tempfile.TemporaryDirectory() as folder:
            for i in range(23):
                Path(folder, f"{i}.tex").write_text("test", encoding="utf-8")
            script = '''
import os
import streamlit as st
from unittest.mock import patch
import utopia_phycyclus_app as app
folder = FOLDER
route = st.radio('Test route', ['search', 'other'])
version = st.radio('Index version', [1, 2])
rows = [{'题目ID': str(i), '题干': '碰撞', '物理方法': '动量守恒',
         '文件名称': str(i), '相对文件路径': f'{i}.tex',
         '初次录入的时间': f'2026-01-{i+1:02d} 12:00:00', '难度星级': str(i % 6)}
        for i in range(23 if version == 1 else 2)]
def show(label, content, path, *args, **kwargs):
    st.write('result:' + os.path.basename(path))
if route == 'search':
    with patch.object(app, 'CHAPTERS_DIR', folder), patch.object(app, '_csv_index_cache_token', return_value=(folder, version)), patch.object(app, '_csv_index_cached', return_value=rows), patch.object(app, 'load_question_editor_assets', return_value={'content': 'test'}), patch.object(app, 'render_browse_question_editor_card', side_effect=show):
        app.page_fuzzy_search()
'''.replace("FOLDER", repr(folder))
            script_path = Path(folder, "fuzzy_test_app.py")
            script_path.write_text(script, encoding="utf-8")
            at = AppTest.from_file(str(script_path), default_timeout=40).run()
            self.assertFalse(at.exception, str(at.exception))
            at.text_input(key="fuzzy_query").set_value("碰撞").run()
            self.assertFalse(at.exception)
            self.assertIn("查找结果 · 23 题", [h.value for h in at.subheader])
            at.radio(key="fuzzy_sort_order").set_value("录入时间：由新到旧").run()
            self.assertIn("result:22.tex", [m.value for m in at.markdown])
            at.number_input(key="fuzzy_results_page").set_value(3).run()
            self.assertEqual(len([m for m in at.markdown if m.value.startswith("result:")]), 3)
            at.radio(key="fuzzy_sort_order").set_value("难度：由低到高").run()
            self.assertEqual(at.session_state["fuzzy_results_page"], 1)
            at.number_input(key="fuzzy_results_page").set_value(3).run()
            at.radio[1].set_value(2).run()
            self.assertFalse(at.exception)
            self.assertIn("查找结果 · 2 题", [h.value for h in at.subheader])
            self.assertEqual(at.session_state["fuzzy_results_page"], 1)
            at.radio[0].set_value("other").run()
            at.radio[0].set_value("search").run()
            self.assertEqual(at.text_input(key="fuzzy_query").value, "碰撞")
            Path(folder, "1.tex").unlink()
            at.run()
            self.assertIn("查找结果 · 1 题", [h.value for h in at.subheader])
            at.text_input(key="fuzzy_query").set_value("   ").run()
            self.assertFalse(at.exception)
            self.assertFalse(at.subheader)


if __name__ == "__main__":
    unittest.main()
