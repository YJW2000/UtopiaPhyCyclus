# -*- coding: utf-8 -*-
"""TeX 录入预览临时编译目录的生命周期测试。

需求：临时编译文件在「题目录入后」与「退出录题界面后」必须删除；应用启动时
清扫上次异常退出留下的旧目录。``cleanup_preview_cache`` 是这三条路径共用的
安全清理函数（只处理 ``.tikz_cache/tex-entry/preview-*``）。
"""
import os
import shutil
import time
import unittest
import uuid
from pathlib import Path

from services.tex_compile_service import cleanup_preview_cache

ROOT = Path(__file__).resolve().parents[1]
CACHE_ROOT = ROOT / '.tikz_cache'


class PreviewCacheCleanupTests(unittest.TestCase):
    def setUp(self):
        self.project = CACHE_ROOT / ('_cleanup_test_' + uuid.uuid4().hex[:10])
        self.entry = self.project / '.tikz_cache' / 'tex-entry'
        self.entry.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.project, ignore_errors=True)

    def _make_preview_dir(self, name, age_seconds=0):
        path = self.entry / name
        path.mkdir()
        (path / 'preview.tex').write_text('x', encoding='utf-8')
        (path / 'preview.pdf').write_bytes(b'%PDF-1.7')
        if age_seconds:
            stamp = time.time() - age_seconds
            os.utime(path, (stamp, stamp))
        return path

    def test_removes_only_preview_directories(self):
        first = self._make_preview_dir('preview-aaa')
        second = self._make_preview_dir('preview-bbb')
        keep_dir = self._make_preview_dir('keep-me')
        keep_file = self.entry / 'preview-not-a-dir.txt'
        keep_file.write_text('keep', encoding='utf-8')

        removed = cleanup_preview_cache(str(self.project))

        self.assertEqual(2, removed)
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        self.assertTrue(keep_dir.exists(), '非 preview-* 目录不能删除')
        self.assertTrue(keep_file.exists(), '同名普通文件不能删除')

    def test_min_age_skips_fresh_directories(self):
        fresh = self._make_preview_dir('preview-fresh')
        stale = self._make_preview_dir('preview-stale', age_seconds=7200)

        removed = cleanup_preview_cache(str(self.project), min_age_seconds=3600)

        self.assertEqual(1, removed)
        self.assertTrue(fresh.exists(), '应用启动清扫不能误删其它会话刚创建的目录')
        self.assertFalse(stale.exists())
        # 保存题目 / 离开录题页时是全量清理，fresh 也应被删除
        self.assertEqual(1, cleanup_preview_cache(str(self.project)))
        self.assertFalse(fresh.exists())

    def test_missing_cache_root_is_noop(self):
        project = CACHE_ROOT / ('_cleanup_missing_' + uuid.uuid4().hex[:8])
        self.assertFalse(project.exists())
        self.assertEqual(0, cleanup_preview_cache(str(project)))

    def test_does_not_touch_other_cache_directories(self):
        outside = self.project / '.tikz_cache' / 'other-cache' / 'preview-outside'
        outside.mkdir(parents=True)
        self.assertEqual(0, cleanup_preview_cache(str(self.project)))
        self.assertTrue(outside.exists(), '清理范围必须限定在 .tikz_cache/tex-entry')

    def test_never_raises_on_unusual_input(self):
        for project in ('', str(ROOT), None):
            self.assertIsInstance(cleanup_preview_cache(project), int)


if __name__ == '__main__':
    unittest.main()
