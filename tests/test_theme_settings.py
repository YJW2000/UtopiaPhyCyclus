import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from services import ui_preferences
from utils import core_config
from utils.theme_palettes import SKINS,palette,theme_tokens
from utils.theme_system import generate_theme_css,normalize_skin_id


class ThemeMatrixTests(unittest.TestCase):
    def test_four_skins_eight_variants(self):
        self.assertEqual(len(SKINS),4)
        backgrounds=set()
        for skin in SKINS:
            self.assertEqual(normalize_skin_id(skin),skin)
            for mode in ('light','dark'):
                tokens=theme_tokens(skin,mode)
                css=generate_theme_css(skin,mode)
                self.assertIn('--mc-input-bg',css)
                self.assertIn(tokens['--mc-text'],css)
                self.assertIn('color-scheme:'+mode,css)
                self.assertNotEqual(tokens['--mc-text'],tokens['--mc-bg-base'])
                backgrounds.add(tokens['--mc-bg-base'])
        self.assertEqual(len(backgrounds),8)
        self.assertEqual(normalize_skin_id('fintech'),'default')

    def test_preferences_preserve_fallback_and_recover_invalid_file(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(ui_preferences,'PREFERENCES_PATH',Path(folder)/'appearance.json'):
            self.assertEqual(ui_preferences.load_preferences()['skin'],'claude_warm')
            ui_preferences.save_preferences('crystal_glass','dark')
            self.assertEqual(ui_preferences.load_preferences(),dict(skin='crystal_glass',mode='dark'))
            ui_preferences.PREFERENCES_PATH.write_text('{bad json',encoding='utf-8')
            self.assertEqual(ui_preferences.load_preferences(dict(APP_THEME_SKIN='fintech',APP_THEME_MODE='light'))['skin'],'default')


class ThemeSettingsUITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.override=patch.object(ui_preferences,'PREFERENCES_PATH',Path(self.temp.name)/'appearance.json')
        self.override.start()
        self.prompt_file = Path(self.temp.name) / 'ocr_prompt.txt'
        self.prompt_override = patch.object(core_config, 'ocr_prompt_file', str(self.prompt_file))
        self.prompt_override.start()

    def tearDown(self):
        self.prompt_override.stop()
        self.override.stop()
        self.temp.cleanup()

    def make_app(self,legacy=False):
        from streamlit.testing.v1 import AppTest
        at=AppTest.from_file('utopia_phycyclus_app.py',default_timeout=40)
        at.session_state['main_nav_selection']='⚙️  API设置' if legacy else '⚙️  设置'
        at.session_state['main_sidebar_radio']='⚙️  API设置' if legacy else '⚙️  设置'
        at.run()
        self.assertFalse(at.exception)
        return at

    def test_settings_persist_all_skins_without_api_controls(self):
        at=self.make_app()
        self.assertFalse(any(radio.key == 'settings_subpage' for radio in at.radio))
        self.assertNotIn('⚙️  API设置',at.radio(key='nav_group_a').options)
        self.assertFalse(any('API' in widget.label for widget in at.text_input))
        self.assertEqual(len([b for b in at.button if str(b.key).startswith('ui_apply_')]),4)
        for skin in ('default','liquid_glass','crystal_glass','claude_warm'):
            at.button(key='ui_apply_'+skin).click().run()
            self.assertFalse(at.exception)
            self.assertEqual(at.session_state['app_theme_skin'],skin)
        at.radio(key='ui_color_mode').set_value('dark').run()
        self.assertFalse(at.exception)
        self.assertEqual(ui_preferences.load_preferences()['mode'],'dark')
        restarted=self.make_app()
        self.assertEqual(restarted.session_state['app_theme_mode'],'dark')

    def test_legacy_api_route_opens_settings_with_ocr_prompt(self):
        at=self.make_app(legacy=True)
        self.assertEqual(at.session_state['main_nav_selection'],'⚙️  设置')
        self.assertFalse(any('API' in header.value for header in at.header))
        at.text_area[0].set_value('识别时请保留读数图。')
        next(button for button in at.button if button.label == '保存识别提示词').click().run()
        self.assertFalse(at.exception)
        self.assertEqual(self.prompt_file.read_text(encoding='utf-8'), '识别时请保留读数图。\n')


if __name__=='__main__':unittest.main()
