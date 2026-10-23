"""Four visual families ported from the local review system's theme.ts matrix."""
import re

SKINS = {
    'default': dict(name='极简金融 · 浅灰白', subtitle='FINTECH MINIMAL', badge='极简金融', accent='#14161B',
        description='柔和冷灰白与黑曜石胶囊交互，纯净卡片与清晰层级，让题目录入、检索与教研更专注。'),
    'liquid_glass': dict(name='液态玻璃 · 流光黑金', subtitle='BLACK GOLD LIQUID GLASS', badge='流光黑金', accent='#D97706',
        description='沉浸式黑曜金底色，深色磨砂琥珀面板与晶莹金边，温润护眼、沉静耐看。'),
    'claude_warm': dict(name='人文雅致 · 暖烘陶', subtitle='WARM TERRACOTTA', badge='暖烘陶', accent='#CC785C',
        description='暖陶土色与温润羊皮纸质感，典雅衬线排版，为物理思考与长时间阅读留一份宁静。'),
    'crystal_glass': dict(name='晶莹水感 · 水晶液态', subtitle='CRYSTAL AQUA GLASS', badge='水晶液态', accent='#0891B2',
        description='晶莹水感半透高光面板，青碧微光与轻盈胶囊交互，通透清爽而不牺牲文字清晰度。'),
}

FIELDS = ('bg','surface','text','muted','accent','hover','border','input','input_border','sidebar','action','action_text','shadow')
_ROWS = {
 'default': {
  'light': ('#F5F7FA','#FFFFFF','#1E293B','#64748B','#14161B','#2D3139','rgba(15,23,42,.07)','#F8FAFC','rgba(15,23,42,.09)','rgba(255,255,255,.94)','#14161B','#FFFFFF','0 10px 30px -8px rgba(15,23,42,.05)'),
  'dark': ('#0F172A','#1E293B','#F8FAFC','#94A3B8','#6366F1','#818CF8','rgba(148,163,184,.14)','rgba(15,23,42,.6)','rgba(148,163,184,.2)','rgba(30,41,59,.92)','#6366F1','#FFFFFF','0 14px 36px -12px rgba(0,0,0,.6)'),
 },
 'liquid_glass': {
  'light': ('#EBE2D5','rgba(255,255,255,.86)','#2A2219','#78716C','#D97706','#F59E0B','rgba(217,119,6,.25)','rgba(255,255,255,.75)','rgba(217,119,6,.3)','rgba(255,255,255,.88)','#2A2219','#FFFFFF','0 16px 36px -10px rgba(120,95,65,.09)'),
  'dark': ('#151310','rgba(32,26,20,.86)','#FAF4EC','#C4B5A5','#D97706','#F59E0B','rgba(245,158,11,.22)','rgba(22,18,14,.75)','rgba(245,158,11,.25)','rgba(28,22,17,.92)','#D97706','#151310','0 16px 38px -10px rgba(0,0,0,.65),inset 0 1px 1px rgba(245,158,11,.15)'),
 },
 'claude_warm': {
  'light': ('#F7F4EC','#FFFDF9','#26231E','#756E63','#CC785C','#B56345','rgba(214,202,185,.75)','#FDFBF7','rgba(214,202,185,.8)','#F4EFE5','#CC785C','#FFFDF9','0 8px 24px -4px rgba(120,95,60,.05),0 1px 3px rgba(120,95,60,.02)'),
  'dark': ('#1B1916','#26231E','#F5EFE6','#A89F91','#D97757','#E59275','rgba(217,119,87,.24)','rgba(27,25,22,.8)','rgba(217,119,87,.28)','rgba(38,35,30,.92)','#D97757','#1B1916','0 16px 40px -12px rgba(0,0,0,.65)'),
 },
 'crystal_glass': {
  'light': ('#EDF7F7','rgba(255,255,255,.84)','#0F172A','#475569','#0891B2','#06B6D4','rgba(255,255,255,.9)','rgba(255,255,255,.75)','rgba(6,182,212,.3)','rgba(255,255,255,.88)','#0891B2','#FFFFFF','0 14px 38px -10px rgba(6,182,212,.12),inset 0 1px 2px rgba(255,255,255,.95)'),
  'dark': ('#0A131C','rgba(16,29,44,.85)','#F0FDFA','#94A3B8','#22D3EE','#67E8F9','rgba(34,211,238,.28)','rgba(10,20,30,.8)','rgba(34,211,238,.32)','rgba(16,29,44,.92)','#22D3EE','#0A131C','0 16px 40px -12px rgba(0,0,0,.75),inset 0 1px 2px rgba(34,211,238,.15)'),
 },
}


def normalize_skin(skin=None):
    aliases={'fintech':'default','black_gold':'liquid_glass','glass':'liquid_glass','claude':'claude_warm','crystal_aqua':'crystal_glass'}
    value=aliases.get(str(skin or '').lower(),str(skin or '').lower())
    return value if value in SKINS else 'claude_warm'


def palette(skin=None,mode='light'):
    return dict(zip(FIELDS,_ROWS[normalize_skin(skin)]['dark' if mode=='dark' else 'light']))


def theme_tokens(skin=None,mode='light'):
    skin=normalize_skin(skin)
    c=palette(skin,mode)
    # Small UI labels must remain readable on both the page and card surfaces.
    if skin=='default' and mode!='dark':c['muted']='#5B6B80'
    if skin=='liquid_glass' and mode!='dark':c['muted']='#665D55'
    if skin=='claude_warm' and mode!='dark':c['muted']='#6B6359'
    actions={
        ('default','light'):('#14161B','#2D3139','#FFFFFF'),
        ('default','dark'):('#5757D5','#4949BB','#FFFFFF'),
        ('liquid_glass','light'):('#92400E','#78350F','#FFFFFF'),
        ('liquid_glass','dark'):('#D97706','#F59E0B','#151310'),
        ('claude_warm','light'):('#A9563E','#90452F','#FFFDF9'),
        ('claude_warm','dark'):('#D97757','#E59275','#1B1916'),
        ('crystal_glass','light'):('#0E7490','#155E75','#FFFFFF'),
        ('crystal_glass','dark'):('#22D3EE','#67E8F9','#0A131C'),
    }
    action,action_hover,action_text=actions[(skin,'dark' if mode=='dark' else 'light')]
    if skin=='liquid_glass':
        c['sidebar']='linear-gradient(165deg,rgba(52,40,28,.97),rgba(21,19,16,.97))' if mode=='dark' else 'linear-gradient(165deg,rgba(248,239,220,.96),rgba(235,226,213,.94))'
    elif skin=='crystal_glass':
        c['sidebar']='linear-gradient(165deg,rgba(13,49,60,.96),rgba(10,19,28,.96))' if mode=='dark' else 'linear-gradient(165deg,rgba(212,244,246,.95),rgba(237,247,247,.94))'
    rgb=','.join(str(int(c['accent'][i:i+2],16)) for i in (1,3,5))
    gradient = f'radial-gradient(at 15% 15%,rgba({rgb},.08),transparent 60%),{c["bg"]}'
    if skin=='liquid_glass' and mode=='dark':
        gradient='linear-gradient(135deg,#151310 0%,#1F1A14 30%,#34281C 60%,#261D15 100%)'
    if skin=='crystal_glass':
        gradient=f'radial-gradient(at 15% 15%,rgba({rgb},.18),transparent 60%),radial-gradient(at 85% 85%,rgba(16,185,129,.12),transparent 55%),{c["bg"]}'
    font='"Charter","Georgia","Noto Serif SC","Songti SC","Microsoft YaHei",serif' if skin=='claude_warm' else '-apple-system,BlinkMacSystemFont,"Plus Jakarta Sans","PingFang SC","Microsoft YaHei",sans-serif'
    return {
        'color-scheme':'dark' if mode=='dark' else 'light','--mc-bg':gradient,'--mc-bg-base':c['bg'],
        '--mc-surface':c['surface'],'--mc-surface-soft':c['input'],'--mc-surface-hover':c['input'],
        '--mc-input-bg':c['input'],'--mc-sidebar':c['sidebar'],'--mc-sidebar-backdrop':'blur(22px) saturate(150%)' if skin in ('liquid_glass','crystal_glass') else 'none','--mc-border':c['border'],
        '--mc-border-strong':c['input_border'],'--mc-text':c['text'],'--mc-muted':c['muted'],
        '--mc-accent':c['accent'],'--mc-accent-dark':c['hover'],'--mc-accent-soft':f'rgba({rgb},.12)',
        '--mc-action-bg':action,'--mc-action-bg-hover':action_hover,'--mc-action-border':action,
        '--mc-action-text':action_text,'--mc-action-shadow':f'0 6px 18px rgba({rgb},.2)',
        '--mc-focus':f'rgba({rgb},.24)','--mc-shadow':c['shadow'],'--mc-backdrop':'none',
        '--mc-card-border':'1px solid '+c['border'],'--mc-tooltip-bg':c['text'],'--mc-tooltip-text':c['bg'],
        '--mc-radius':'22px','--mc-control-radius':'12px','--mc-font-family':font,
        '--mc-font-mono':'"Cascadia Code",Consolas,"Courier New",monospace',
    }


def retint_warm_markup(markup,skin=None,mode='light'):
    """Recolor legacy warm literals without touching semantic red/green or images."""
    if normalize_skin(skin)=='claude_warm' and mode!='dark':return markup
    c=palette(skin,mode)
    colors={'#F7F4EC':c['bg'],'#FFFDF9':c['surface'],'#26231E':c['text'],'#756E63':c['muted'],
        '#CC785C':c['accent'],'#B56345':c['hover'],'#E0937A':c['hover'],'#F5EFE6':c['input'],
        '#F4EFE5':c['sidebar'],'#FDFBF7':c['input'],'#FDF8F2':c['input'],'#EDE5D8':c['input'],'#F7F0E6':c['input']}
    markup=re.sub('|'.join(colors),lambda m:colors[m.group().upper()],markup,flags=re.I)
    rgb=','.join(str(int(c['accent'][i:i+2],16)) for i in (1,3,5))
    markup=re.sub(r'rgba\(204,\s*120,\s*92,\s*([.\d]+)\)',lambda m:f'rgba({rgb},{m[1]})',markup,flags=re.I)
    markup=re.sub(r'rgba\(214,\s*202,\s*185,\s*[.\d]+\)',c['border'],markup,flags=re.I)
    markup=re.sub(r'rgba\(255,\s*253,\s*249,\s*[.\d]+\)',c['surface'],markup,flags=re.I)
    markup=re.sub(r'rgba\(247,\s*244,\s*236,\s*[.\d]+\)',c['bg'],markup,flags=re.I)
    return markup
