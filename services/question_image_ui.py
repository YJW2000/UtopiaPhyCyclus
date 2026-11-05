"""External-image controls shared by the question and TeX editors."""
import streamlit as st
from pathlib import Path

from utils.image_assets import graphics_references, save_uploaded_image


def apply_pending_diagram_update(content_state_key: str):
    """Apply Agent output before Streamlit instantiates the source widget."""
    pending_key = content_state_key + '_pending_diagram_update'
    if pending_key in st.session_state:
        st.session_state[content_state_key] = st.session_state.pop(pending_key)


def queue_diagram_update(content_state_key: str, content: str):
    st.session_state[content_state_key + '_pending_diagram_update'] = content
    st.rerun()


def insert_image_code(content: str, code: str, position: str = '题干末尾（选项前）') -> str:
    marker = '% 插图位置'
    if position == '标记位置（% 插图位置）':
        if marker not in content:
            raise ValueError('请先在源码需要插图的位置写入 % 插图位置。')
        return content.replace(marker, code, 1)
    anchors = [r'\end{solutions}', r'\end{solution}'] if position == '解析末尾' else [r'\begin{choices}', r'\end{problem}']
    for anchor in anchors:
        if anchor in content:
            offset = content.find(anchor)
            return content[:offset].rstrip() + '\n\n' + code + '\n\n' + content[offset:]
    anchor = r'\end{document}'
    offset = content.rfind(anchor)
    if offset >= 0:
        return content[:offset].rstrip() + '\n\n' + code + '\n' + content[offset:]
    return content.rstrip() + '\n\n' + code + '\n'


def render_question_image_upload(content_state_key: str, key_prefix: str, project_dir: str, on_change=None):
    with st.expander('🖼️ 插入外部图片', expanded=False):
        st.caption('图片单独保存，源码使用文件名引用。可在任意位置写入 % 插图位置，再选择“标记位置”插入。')
        uploads = st.file_uploader('选择插图文件', type=['png', 'jpg', 'jpeg', 'webp'],
                                   accept_multiple_files=True, key=key_prefix + '_image_files')
        position = st.selectbox('插入位置', ['题干末尾（选项前）', '解析末尾', '标记位置（% 插图位置）', '关联源码中的同名图片'],
                                key=key_prefix + '_image_position')
        width = st.slider('图片宽度（正文宽度百分比）', 10, 100, 65, key=key_prefix + '_image_width')
        if st.button('插入到源码', key=key_prefix + '_image_insert', disabled=not uploads):
            try:
                paths = [save_uploaded_image(item.getvalue(), item.name, project_dir) for item in uploads]
                code = '\n\n'.join(r'\par\noindent\includegraphics[width=%.2f\linewidth]{%s}\par' % (width / 100, path) for path in paths)
                source = st.session_state.get(content_state_key, '')
                if position == '关联源码中的同名图片':
                    names = {item.name: path for item, path in zip(uploads, paths)}
                    matched = False
                    for ref in reversed(graphics_references(source)):
                        name = Path(ref['path']).name
                        if name in names:
                            source = source[:ref['path_start']] + names[name] + source[ref['path_end']:]
                            matched = True
                    if not matched:
                        raise ValueError('源码中没有与所选图片同名的 includegraphics 引用。')
                    st.session_state[content_state_key] = source
                else:
                    st.session_state[content_state_key] = insert_image_code(source, code, position)
                if on_change:
                    on_change()
                st.rerun()
            except (OSError, ValueError) as exc:
                st.error(str(exc))
