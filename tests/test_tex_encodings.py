from codecs import BOM_UTF8, BOM_UTF16_BE

import pytest

from services.tex_import_service import decode_tex, read_tex
from utils.index_rebuild import collect_rows


def test_explicit_unicode_encoding_never_falls_back_to_gb18030():
    assert decode_tex(BOM_UTF16_BE + '物理题'.encode('utf-16-be')) == '物理题'
    with pytest.raises(ValueError, match='解码'):
        decode_tex(BOM_UTF8 + b'\xfd')
    with pytest.raises(ValueError, match='二进制'):
        decode_tex('物理\x00题'.encode('utf-16'))
    with pytest.raises(ValueError, match='UTF-32'):
        decode_tex('物理题'.encode('utf-32'))


def test_index_rebuild_reads_the_same_text_as_classic_preview(tmp_path):
    originals = {}
    for number, encoding in enumerate(('utf-8', 'utf-8-sig', 'gbk', 'gb18030', 'utf-16'), 1):
        path = tmp_path / f'2026-M-测试卷-{number}-电学实验.tex'
        source = rf'\begin{{problem}}{{2026}}{{M}}{{测试卷}}{{{number}}}{{电学实验}}' + '\n'
        source += r'电流表和电压表。\end{problem}'
        data = source.replace('\n', '\r\n').encode(encoding)
        originals[path] = data
        path.write_bytes(data)
        assert read_tex(path) == source
    assert [row['题干'] for row in collect_rows(tmp_path, [])] == ['电流表和电压表。'] * 5
    assert all(path.read_bytes() == data for path, data in originals.items())
