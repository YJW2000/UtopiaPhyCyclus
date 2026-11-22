"""Batch scoring through the actual AI entry screen, using a local model fixture."""
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import utopia_phycyclus_app as app
from utils.latex_ops import inject_meta_data, parse_meta_data


def draft(number, text="求带电粒子的速度"):
    source = (
        rf"\begin{{problem}}{{2026}}{{M}}{{测试卷}}{{{number}}}{{未分类}}{text}。\end{{problem}}"
        + "\n" + r"\begin{solutions}由电场力做功及动能定理求解。\end{solutions}"
    )
    source = inject_meta_data(source, {"题型": "实验题", "备注": "教师备注"})
    return f"---2026-M-测试卷-{number}-未分类.tex---\n{source}"


def evidence():
    route = {
        "core_subject": "电场", "points": [{"name": "电场力做功", "subject": "电场"},
                                        {"name": "动能定理", "subject": "功与能量"}],
        "steps": [{"id": "a", "conclusion": "电场力做功使动能增加", "depends_on": []}],
        "coupling": 1, "novelty": 0, "inferred": False, "physics_methods": [], "reason": "从电场力做功得到动能变化。",
    }
    return {"parts": [{"label": "全题", "required": True, "routes": [route]}], "tags": ["带电粒子"]}


class BatchAssessmentUITests(unittest.TestCase):
    def prepare(self, mode, drafts, response):
        stack = self.enterContext(ExitStack())
        root = Path(stack.enter_context(TemporaryDirectory()))
        request = stack.enter_context(patch("services.batch_assessment_service._request_json", side_effect=response))
        stack.enter_context(patch.object(app, "CHAPTERS_DIR", str(root)))
        stack.enter_context(patch("utils.csv_ops.get_next_id", return_value=90))
        writes = []

        def write(path, content, year, category, paper, number, subject, **_):
            Path(path).write_text(content, encoding="utf-8")
            writes.append((Path(path), year, category, paper, number, subject))
            return True

        stack.enter_context(patch.object(app, "_save_batch_question_if_new", side_effect=write))
        view = AppTest.from_string("import utopia_phycyclus_app as app\napp.page_entry()", default_timeout=30)
        view.session_state["entry_mode_sidebar"] = mode
        if drafts:
            view.session_state["batch_content"] = "\n".join(drafts)
        view.run()
        self.assertFalse(view.exception)
        return view, request, writes

    def test_each_mode_uses_reviewed_metadata_and_canonical_location(self):
        for mode, save_key in (("批量试题录入", "batch_save_btn"), ("同卷试题录入", "same_paper_save_btn")):
            with self.subTest(mode=mode):
                view, request, writes = self.prepare(mode, [draft(1)], lambda *_a, **_kw: evidence())
                self.assertEqual(request.call_count, 1)
                self.assertTrue(any('主板块：电场' in m.value for m in view.markdown))
                self.assertTrue(any('查看各小问评分' in m.value for m in view.markdown))
                view.text_input(key="batch_label_0_难度星级").set_value("3.5").run()
                view.multiselect(key="batch_label_0_知识板块").set_value(["功与能量", "电场"]).run()
                view.text_input(key="batch_knowledge_points").set_value("综合测试").run()
                if mode == "同卷试题录入":
                    view.text_input(key="u_batch_paper").set_value("另一套卷").run()
                view.button(key=save_key).click().run()
                self.assertFalse(view.exception)
                self.assertEqual(request.call_count, 1)
                self.assertEqual(len(writes), 1)
                path, year, category, paper, number, subject = writes[0]
                self.assertEqual(subject, "功与能量，电场")
                self.assertEqual(paper, "另一套卷" if mode == "同卷试题录入" else "测试卷")
                self.assertEqual(path.name, f"{year}-{category}-{paper}-{number}-{subject}.tex")
                self.assertEqual(path.parent.parent.name, "功与能量")
                meta, body = parse_meta_data(path.read_text(encoding="utf-8"))
                self.assertEqual(app.extract_problem_header_fields(body)["subject_str"], subject)
                self.assertEqual((meta["难度星级"], meta["题型"], meta["备注"]), ("3.5", "实验题", "教师备注"))
                self.assertEqual(set(meta["知识点"].split("，")), {"电场力做功", "动能定理", "综合测试"})
                self.assertIn("评分明细", meta)
                self.assertIn("难度星级", meta["评分人工字段"])
                view.button(key=save_key).click().run()
                self.assertEqual(len(writes), 1)

    def test_failure_stays_in_draft_and_retries_only_on_request(self):
        attempts = []

        def response(_config, _instruction, source, **_):
            if "另一道题" in source and not attempts:
                attempts.append(source)
                raise ValueError("暂时不可用")
            return evidence()

        view, request, writes = self.prepare("批量试题录入", [draft(1), draft(2, "另一道题")], response)
        self.assertEqual(request.call_count, 2)
        view.run()
        view.button(key="batch_save_btn").click().run()
        self.assertFalse(view.exception)
        self.assertEqual(request.call_count, 2)
        self.assertEqual([r[4] for r in writes], ["1"])
        self.assertTrue(any("暂时不可用" in w.value for w in view.warning))
        view.button(key="batch_assessment_retry_1").click().run()
        self.assertEqual(request.call_count, 3)
        view.button(key="batch_save_btn").click().run()
        self.assertEqual([r[4] for r in writes], ["1", "2"])

    def test_paste_and_direct_save_auto_splits_and_scores(self):
        view, request, writes = self.prepare("批量试题录入", [], lambda *_a, **_kw: evidence())
        view.text_area(key="batch_content_fallback").set_value(draft(4) + "\n" + draft(5, "求电场强度")).run()
        view.button(key="batch_save_btn").click().run()
        self.assertFalse(view.exception)
        self.assertEqual(request.call_count, 2)
        self.assertEqual([r[4] for r in writes], ["4", "5"])

    def test_changing_the_referenced_image_retries_a_failed_draft(self):
        from PIL import Image
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'diagram.png'
            Image.new('RGB', (4, 4), 'white').save(path)
            source = draft(1, r'由图求加速度\includegraphics{' + path.as_posix() + '}')
            view, api, _ = self.prepare('批量试题录入', [source], [ValueError('图像服务暂不可用'), evidence()])
            self.assertEqual(api.call_count, 1)
            view.run()
            self.assertEqual(api.call_count, 1)
            Image.new('RGB', (4, 4), 'black').save(path)
            view.run()
            self.assertFalse(view.exception)
            self.assertEqual(api.call_count, 2)
            self.assertIn('评分明细', view.session_state['batch_content'])
