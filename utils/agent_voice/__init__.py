import os

import streamlit.components.v1 as components

# 本地自定义组件：作为「语音 / 文本」从浏览器到 Python 的可靠回传通道。
_component_func = components.declare_component(
    "agent_voice",
    path=os.path.dirname(os.path.abspath(__file__)),
)


def agent_voice_bridge(key=None):
    """渲染一个 0 高度的回传桥组件。

    返回值是最近一次 emit 的 payload（dict），形如：
        {"kind": "text", "text": "...", "id": "..."}
        {"kind": "audio", "mime": "audio/webm;codecs=opus", "audio": "<base64>", "id": "..."}
    没有新消息时返回上次的值（因此调用方需按 id 去重）。
    """
    return _component_func(key=key, default=None, height=0)
