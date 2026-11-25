"""UTF-8 JSON-RPC transport for the native desktop host."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def serve(handlers, reader, writer):
    for line in reader:
        ident = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError('请求应为一个 JSON 对象。')
            ident = request.get('id')
            if type(ident) not in (str, int):
                ident = None
                raise ValueError('请求编号无效。')
            method = request.get('method')
            if not isinstance(method, str) or method not in handlers:
                raise ValueError(f'未知的桌面方法 {method!r}。')
            params = request.get('params', {})
            if not isinstance(params, dict):
                raise ValueError('请求参数应为一个 JSON 对象。')
            response = {'id': ident, 'result': handlers[method](params)}
            encoded = json.dumps(response, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        except Exception as error:
            encoded = json.dumps({'id': ident, 'error': str(error) or type(error).__name__},
                                 ensure_ascii=False, separators=(',', ':'))
        writer.write(encoded + '\n')
        writer.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    library = args.library.resolve()
    state = args.state_dir.resolve()
    if not (library / 'chapters').is_dir():
        parser.error('当前题库缺少 chapters 目录，请重新选择。')
    if any(state.is_relative_to(library / name) for name in ('chapters', 'utils')):
        parser.error('桌面状态目录必须独立于题库和索引。')
    os.environ['UTOPIA_DESKTOP_LIBRARY'] = str(library)
    os.environ['UTOPIA_DESKTOP_STATE'] = str(state)

    from services.desktop_library import create_library_handlers
    from services.desktop_model import selected_provider
    from services.desktop_workspace import DesktopWorkspace
    from services.tex_environment_check import check_latex_environment

    workspace = DesktopWorkspace(library, state)
    handlers = {**create_library_handlers(library, state), **workspace.handlers}
    handlers.update({'workspace.fileInfo': workspace.file_info, 'workspace.saveFileInfo': workspace.save_file_info})

    def check_latex(_params):
        def test_layout(job):
            job.progress('正在测试 XeLaTeX 中文排版')
            result = check_latex_environment(state, workspace.runtime, workspace.pdf_lock)
            job.publish(lambda: result)

        return workspace.jobs.submit(test_layout)

    handlers['system.check_latex'] = check_latex
    if 'library.imageInput' in handlers:
        image_input = handlers['library.imageInput']

        def convert_image(params):
            with workspace.pdf_lock:
                return image_input(params)

        handlers['library.imageInput'] = convert_image
    if 'context.chat' in handlers:
        chat = handlers['context.chat']

        def context_chat(params):
            provider = selected_provider(state)

            def answer(job):
                job.progress('正在分析当前页面')
                result = chat(params, provider=provider)
                job.publish(lambda: result)

            return workspace.jobs.submit(answer)

        handlers['context.chat'] = context_chat
    handlers['system.ping'] = lambda _params: {'version': 1, 'library': str(library), 'stateDir': str(state)}
    sys.stdin.reconfigure(encoding='utf-8', errors='replace')
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace', newline='\n')
    try:
        serve(handlers, sys.stdin, sys.stdout)
    finally:
        workspace.jobs.close()


if __name__ == '__main__':
    main()
