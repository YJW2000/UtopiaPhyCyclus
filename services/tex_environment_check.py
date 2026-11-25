"""Compile a disposable question with the desktop's current LaTeX environment."""
from __future__ import annotations

from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unicodedata

import pymupdf
from PIL import Image

from services.tex_compile_service import compile_tex_preview


_SAMPLE = r'''
\begin{problem}{2026}{M}{排版测试}{1}{电学实验}
中文排版测试：电磁感应、动量守恒。
力 $F=ma$，电阻 $10\,\Omega$，电流 $12\,\mu\mathrm{A}$。
\includegraphics[width=20mm]{电路示意.png}
\end{problem}
'''


def check_latex_environment(state: Path, runtime: Path, pdf_lock) -> dict[str, str]:
    """Exercise the same template, compiler and PDF reader as question previews."""
    with TemporaryDirectory(prefix='tex-check-', dir=state) as directory:
        work = Path(directory)
        for name in ('main.tex', 'UtopiaPhyCyclus_book.cls', 'warm-physics.sty'):
            template = runtime / name
            if template.is_file():
                shutil.copyfile(template, work / name)
        Image.new('RGB', (32, 16), 'white').save(work / '电路示意.png')
        result = compile_tex_preview(_SAMPLE, str(work), {}, cache_dir=work / 'cache')
        if not result['ok']:
            raise RuntimeError(result['error'])

        with pdf_lock, pymupdf.open(stream=result['pdf'], filetype='pdf') as document:
            text = ''.join(page.get_text() for page in document)
            has_image = any(page.get_images() for page in document)
        compact = ''.join(unicodedata.normalize('NFKC', text).split())
        expected = ('中文排版测试', '电磁感应', '动量守恒', 'F=ma', '10Ω', '12μA')
        if not has_image or any(item not in compact for item in expected) or any(
            character in compact for character in ('\ufffd', '\uffff')
        ):
            raise RuntimeError('PDF 的中文、物理单位、公式或插图不完整。请检查 XeLaTeX 和题库模板字体。')
    return {'message': '中文、物理公式、单位和插图均通过排版测试。'}
