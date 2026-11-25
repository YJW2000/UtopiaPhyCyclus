"""PDF intake state across Streamlit reruns, using only temporary resources."""
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import unittest
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest


def compile_source(source, project, defaults, engine, resources):
    if st.session_state.get("fail_compile"):
        return {"ok": False, "error": "bad math"}
    return {"ok": True, "pdf": b"compiled " + source.encode(), "engine": engine}


SCRIPT = r'''
from unittest.mock import patch
import streamlit as st
import services.pdf_import_ui as ui

class Upload:
    name = "两页试题.pdf"
    def getvalue(self):
        return b"fixture"

def recognize(pdf, *, resource_dir, first_page, last_page, defaults, on_progress, prompt_library=None):
    assert prompt_library == st.session_state["project"]
    st.session_state.setdefault("recognitions", []).append((first_page, last_page, str(resource_dir)))
    (resource_dir / "figure.png").write_bytes(b"figure")
    on_progress(first_page, last_page - first_page + 1)
    if st.session_state.get("fail_import"):
        raise ValueError("unreadable page")
    return "original source"

def show_edit(preview, *, compile_candidate, on_accept, disabled, **kwargs):
    st.caption("candidate " + preview["pdf"].decode())
    if st.button("采用测试修改", key="test_accept", disabled=disabled):
        on_accept("accepted source", compile_candidate("accepted source"))

def show_original(pdf, *, disabled, initial_page, **kwargs):
    assert disabled and pdf == b"fixture"
    st.caption(f"original page {initial_page}")

def save(source, defaults, resources):
    st.session_state.setdefault("saved", []).append((source, defaults, resources))
    return [dict(status="success", file="q.tex", message="已保存")]

with patch.object(st, "file_uploader", return_value=Upload()), patch.object(ui, "_page_count", return_value=2), \
     patch.object(ui, "import_pdf", recognize), \
     patch.object(ui, "render_pdf_edit", show_edit), patch.object(ui, "st_pdf_selection", show_original):
    ui.render_pdf_import(st.session_state["project"], save)
'''


class PdfImportUiTest(unittest.TestCase):
    def setUp(self):
        compiler = patch("services.pdf_import_ui.compile_tex_preview", compile_source)
        compiler.start()
        self.addCleanup(compiler.stop)
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        script_path = self.root / "fixture_ui.py"
        script_path.write_text(SCRIPT, encoding="utf-8")
        self.app = AppTest.from_file(str(script_path), default_timeout=12)
        self.app.session_state["project"] = tmp.name
        self.app.run()
        self.assertFalse(self.app.exception)

    def recognize(self):
        self.app.button(key="pdf_import_run").click().run()
        self.assertFalse(self.app.exception)

    def current_key(self):
        return "pdf_draft_" + self.app.session_state["pdf_import_selected"]

    def test_separate_resources_and_unsaved_source_survive_switches(self):
        self.app.number_input[0].set_value(2).run()
        self.recognize()
        app = self.app
        first_id = app.session_state["pdf_import_selected"]
        first = self.current_key()
        app.radio(key=first + "_view").set_value("LaTeX 源码").run()
        app.text_area(key=first + "_source").set_value("manual changes").run()
        self.assertTrue(app.button(key=first + "_save").disabled)
        app.radio(key=first + "_view").set_value("原 PDF 对照").run()
        self.assertTrue(any(c.value == "original page 2" for c in app.caption))
        self.recognize()
        second = self.current_key()
        self.assertNotEqual(first, second)
        self.assertEqual(app.radio(key=second + "_view").value, "PDF 框选修改")
        app.selectbox(key="pdf_import_selected").set_value(first_id).run()
        app.radio(key=first + "_view").set_value("LaTeX 源码").run()
        self.assertEqual(app.text_area(key=first + "_source").value, "manual changes")
        app.button(key=first + "_compile").click().run()
        self.assertEqual(app.radio(key=first + "_view").value, "PDF 框选修改")
        self.assertFalse(app.button(key=first + "_save").disabled)
        calls = app.session_state["recognitions"]
        self.assertEqual([(a, b) for a, b, _ in calls], [(2, 2), (2, 2)])
        self.assertNotEqual(calls[0][2], calls[1][2])
        for _, _, path in calls:
            self.assertEqual(Path(path).parent, self.root / ".runtime" / "pdf-imports")
            self.assertTrue((Path(path) / "figure.png").exists())

    def test_failed_import_and_compile_retry_keep_previous_work(self):
        app = self.app
        app.session_state["fail_compile"] = True
        self.recognize()
        key = self.current_key()
        self.assertTrue(any("bad math" in e.value for e in app.error))
        self.assertTrue(app.button(key=key + "_save").disabled)
        app.session_state["fail_import"] = True
        self.recognize()
        self.assertTrue(any("unreadable page" in e.value for e in app.error))
        self.assertEqual(len(app.session_state["pdf_import_drafts"]), 1)
        self.assertEqual(len(list((self.root / ".runtime" / "pdf-imports").iterdir())), 1)
        app.session_state["fail_compile"] = False
        app.button(key=key + "_compile").click().run()
        self.assertFalse(app.button(key=key + "_save").disabled)
        app.button(key="test_accept").click().run()
        self.assertNotIn("saved", app.session_state)
        app.button(key=key + "_save").click().run()
        self.assertEqual(app.session_state["saved"][0][0], "accepted source")
        app.run()
        self.assertEqual(len(app.session_state["saved"]), 1)
        self.assertEqual(len(app.session_state["recognitions"]), 2)

    def test_pending_candidate_only_blocks_saving_its_own_revision(self):
        self.recognize()
        app = self.app
        first_id = app.session_state["pdf_import_selected"]
        first = self.current_key()
        preview = app.session_state["pdf_import_drafts"][first_id]["preview"]
        identity = preview["fingerprint"] + ":" + hashlib.sha256(preview["pdf"]).hexdigest()
        proposal_key = first + "_edit_proposal"
        app.session_state[proposal_key] = {"base": identity}
        app.run()
        self.assertTrue(app.button(key=first + "_save").disabled)
        self.assertTrue(app.button(key=first + "_fill_solution").disabled)
        self.assertTrue(any("采用或放弃" in c.value for c in app.caption))
        self.recognize()
        self.assertFalse(app.button(key=self.current_key() + "_save").disabled)
        app.selectbox(key="pdf_import_selected").set_value(first_id).run()
        self.assertTrue(app.button(key=first + "_save").disabled)
        app.session_state[proposal_key] = {"base": "stale"}
        app.run()
        self.assertFalse(app.button(key=first + "_save").disabled)

    def test_fill_solution_updates_source_and_preview_before_saving(self):
        self.recognize()
        app = self.app
        key = self.current_key()
        app.radio(key=key + "_view").set_value("LaTeX 源码").run()
        app.text_area(key=key + "_source").set_value("manually edited question").run()
        updated = r"manually edited question\begin{answer}A\end{answer}"
        with patch("services.question_solution_service.fill_missing_solution", return_value=updated) as generate:
            app.button(key=key + "_fill_solution").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(generate.call_args.args, ("manually edited question",))
        self.assertTrue(Path(generate.call_args.kwargs["source_dir"], "figure.png").is_file())
        self.assertEqual(app.text_area(key=key + "_source").value, updated)
        self.assertNotIn("saved", app.session_state)
        app.button(key=key + "_save").click().run()
        self.assertEqual(app.session_state["saved"][0][0], updated)


if __name__ == "__main__":
    unittest.main()
