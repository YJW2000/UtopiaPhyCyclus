import unittest
from streamlit.testing.v1 import AppTest
from utils.theme_palettes import theme_tokens
from utils.theme_system import generate_theme_css


class SidebarFlyoutTests(unittest.TestCase):
    def test_native_bridge_routes_directly_to_children(self):
        at=AppTest.from_file('utopia_phycyclus_app.py',default_timeout=40).run()
        for key,route,state,value in (
            ('mc-nav-bridge-1-1','📝  智能录题','entry_mode_sidebar','批量试题录入'),
            ('mc-nav-bridge-2-2','📄  TeX录入','tex_workspace_mode','同卷试题录入'),
            ('mc-nav-bridge-6-1','🔎  题目查找','search_subpage','模糊查找'),
            ('mc-nav-bridge-6-0','🔎  题目查找','search_subpage','精确查找'),
            ('mc-nav-bridge-9-1','⚙️  设置','settings_subpage','🔑 API设置'),
            ('mc-nav-bridge-9-0','⚙️  设置','settings_subpage','🎨 UI设置'),
        ):
            at.button(key=key).click().run()
            self.assertFalse(at.exception)
            self.assertEqual(at.session_state['main_nav_selection'],route)
            self.assertEqual(at.session_state[state],value)
            if state == 'search_subpage':
                self.assertTrue(any(value in header.value for header in at.header))

    def test_flyout_and_sidebar_receive_all_eight_palettes(self):
        backgrounds=set()
        for skin in ('default','liquid_glass','claude_warm','crystal_glass'):
            for mode in ('light','dark'):
                tokens=theme_tokens(skin,mode)
                backgrounds.add(tokens['--mc-sidebar'])
                css=generate_theme_css(skin,mode)
                self.assertIn('#mc-sidebar-flyout',css)
                self.assertIn('--mc-sidebar-backdrop',css)
                self.assertIn('label[data-mc-flyout]:hover p',css)
                self.assertIn(tokens['--mc-sidebar'],css)
        self.assertEqual(len(backgrounds),8)


if __name__=='__main__':unittest.main()
