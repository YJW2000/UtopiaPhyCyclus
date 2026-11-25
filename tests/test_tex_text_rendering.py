from subprocess import CompletedProcess

from services import tex_compile_service


def test_preview_rejects_pdf_when_compiler_silently_drops_glyphs(tmp_path, monkeypatch):
    def run(command, *, cwd, **_):
        output = next(arg.split('=', 1)[1] for arg in command if arg.startswith('-output-directory='))
        directory = cwd / output
        (directory / 'preview.pdf').write_bytes(b'PDF with a dropped character')
        (directory / 'preview.log').write_bytes(
            'Missing character: There is no 答 (U+7B54) in font Example!'.encode('cp936'))
        return CompletedProcess(command, 0, b'', b'')

    monkeypatch.setattr(tex_compile_service, 'find_desktop_tool', lambda _: 'xelatex')
    monkeypatch.setattr(tex_compile_service, 'run', run)
    result = tex_compile_service.compile_tex_preview('答案', str(tmp_path), {})

    assert not result['ok'] and 'pdf' not in result
    assert 'U+7B54' in result['diagnosis'] and '答' in result['log']


def test_preview_compiles_normalized_main_with_utf8_without_rewriting_legacy_sources(tmp_path, monkeypatch):
    original = (
        '\\XeTeXinputencoding "GBK"\n'
        '\\XeTeXdefaultencoding "GBK"\n'
        '\\documentclass{ctexart}\n'
        '\\begin{document}电路\\end{document}'
    )
    saved = tmp_path / '旧原稿.tex'
    saved.write_bytes(original.encode('gb18030'))

    def run(command, *, cwd, **_):
        staged = cwd / command[-1]
        compiled_source = staged.read_text(encoding='utf8')
        assert compiled_source == original.replace('inputencoding "GBK"', 'inputencoding "UTF-8"')
        (staged.parent / 'preview.pdf').write_bytes(b'%PDF-1.4')
        return CompletedProcess(command, 0, b'')

    monkeypatch.setattr(tex_compile_service, 'find_desktop_tool', lambda _: 'xelatex')
    monkeypatch.setattr(tex_compile_service, 'run', run)
    result = tex_compile_service.compile_tex_preview(original, str(tmp_path), {})
    assert result['ok']
    assert saved.read_bytes() == original.encode('gb18030')
