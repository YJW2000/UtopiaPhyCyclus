"""Theme regression guards for actions, isolated components and portal surfaces."""
import unittest
from utils.theme_palettes import SKINS, theme_tokens
from utils.component_theme import theme_agent_capsule, theme_activity_chart
from utils.charts import generate_echarts_bar_html, generate_echarts_pie_html, generate_heatmap_html
from utils.theme_system import generate_theme_css


def luminance(color):
    rgb=[int(color[i:i+2],16)/255 for i in (1,3,5)]
    return sum((v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4)*w for v,w in zip(rgb,(.2126,.7152,.0722)))


class ThemeAccessibilityTests(unittest.TestCase):
    def test_primary_actions_meet_normal_text_contrast_including_hover(self):
        for skin in SKINS:
            for mode in ('light','dark'):
                t=theme_tokens(skin,mode)
                for background in ('--mc-action-bg','--mc-action-bg-hover'):
                    a,b=sorted([luminance(t[background]),luminance(t['--mc-action-text'])])
                    self.assertGreaterEqual((b+.05)/(a+.05),4.5,(skin,mode,background))

    def test_renaming_keeps_saved_skin_identifier_compatible(self):
        self.assertEqual(SKINS['claude_warm']['badge'],'暖烘陶')
        self.assertNotIn('Claude',SKINS['claude_warm']['name'])

    def test_isolated_components_receive_theme_and_responsive_rules(self):
        for skin in SKINS:
            for mode in ('light','dark'):
                markup=theme_agent_capsule('<html><head></head><body></body></html>',skin,mode)
                self.assertIn('--mc-input-bg',markup)
                self.assertIn('flex-wrap:wrap',markup)
                activity=theme_activity_chart('',skin,mode)
                self.assertIn('.day-selector-btn.active',activity)
                self.assertIn('--mc-action-text',activity)

    def test_charts_do_not_force_light_mode(self):
        for factory,args in ((generate_echarts_bar_html,({'力学':3},'知识点')), (generate_echarts_pie_html,({'单选':3},{'3':3},'题型'))):
            self.assertIn('var isDark = true;',factory(*args,skin='crystal_glass',mode='dark'))
        heatmap=generate_heatmap_html({},skin='claude_warm',mode='dark')
        self.assertIn('#26231E',heatmap)
        self.assertNotIn('#8b949e',heatmap)

    def test_shared_controls_and_portals_are_themed(self):
        css=generate_theme_css('crystal_glass','dark')
        for marker in ('stPopoverBody','stTextInput','stFileUploaderDropzone','mc-sidebar-status','100dvh','mc-exam-card-preview-label'):
            self.assertIn(marker,css)


if __name__=='__main__':unittest.main()
