from threading import Lock

import pymupdf
import pytest

from services import tex_environment_check


def test_layout_check_rejects_an_incomplete_pdf(tmp_path, monkeypatch):
    with pymupdf.open() as document:
        document.new_page()
        blank_pdf = document.tobytes()

    def compile_blank(source, project_dir, defaults, **options):
        assert '中文排版测试' in source
        assert options['cache_dir'].parent.parent == tmp_path
        return {'ok': True, 'pdf': blank_pdf}

    monkeypatch.setattr(tex_environment_check, 'compile_tex_preview', compile_blank)
    with pytest.raises(RuntimeError, match='PDF 的中文、物理单位、公式或插图不完整'):
        tex_environment_check.check_latex_environment(tmp_path, tmp_path, Lock())
    assert not list(tmp_path.iterdir())
