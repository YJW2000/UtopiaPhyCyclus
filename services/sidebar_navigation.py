"""Hover/focus flyouts backed by native Streamlit callbacks, never URL reloads."""
import json
from pathlib import Path
import streamlit as st
import streamlit.components.v1 as components


def render_sidebar_flyouts(nav_options, select_navigation, children):
    def choose(nav, state_key, value):
        select_navigation(nav)
        if state_key:
            st.session_state[state_key] = value

    items=[]
    with st.container():
        st.markdown('<span id="mc-hover-nav-bridge"></span>',unsafe_allow_html=True)
        for index,nav in enumerate(nav_options):
            child=children.get(nav)
            options=[]
            if child:
                state_key,values=child
                for j,value in enumerate(values):
                    token=f'mc-nav-bridge-{index}-{j}'
                    st.button(token,key=token,on_click=choose,args=(nav,state_key,value))
                    options.append(dict(label=value,bridge=token,selected=st.session_state.get(state_key)==value and st.session_state.get('main_nav_selection')==nav))
            items.append(dict(label=nav,match=nav.split('  ',1)[-1],children=options))
    script=(Path(__file__).resolve().parents[1]/'static'/'sidebar_flyout.js').read_text(encoding='utf-8')
    components.html('<script>'+script.replace('__MC_NAV_CONFIG__',json.dumps(items,ensure_ascii=False))+'</script>',height=0)
