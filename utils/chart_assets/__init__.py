"""Vendored JS served by Streamlit's component handler with JavaScript MIME."""
from pathlib import Path
import streamlit as st
import streamlit.components.v1 as components

_asset = components.declare_component('echarts', path=str(Path(__file__).resolve().parents[2]/'static'))

def echarts_script():
    prefix = st.get_option('server.baseUrlPath').strip('/')
    base = '/' + (prefix + '/' if prefix else '')
    return f'<script src="{base}component/{_asset.name}/echarts-5.5.0.min.js"></script>'


