"""Bundle the physics transcription method with each image recognition request."""
from functools import lru_cache
from pathlib import Path


_NAME = 'physics-image-to-latex'
_SKILL_FILE = Path(__file__).with_name('agent_skills') / 'plugins' / _NAME / 'SKILL.md'
_DEFAULT_PROMPT = Path(__file__).resolve().parents[1] / 'ocr_prompt.txt'


@lru_cache(maxsize=1)
def _skill_body() -> str:
    raw = _SKILL_FILE.read_text(encoding='utf-8-sig')
    if raw.startswith('---\n'):
        _, closing, body = raw[4:].partition('\n---\n')
        if closing:
            return body.strip()
    return raw.strip()


def build_ocr_prompt(task: str, source_prompt: str = '') -> str:
    parts = [task, f'【内置技能：{_NAME}】\n{_skill_body()}']
    if source_prompt.strip():
        parts.append(
            '【原有题库识别提示词】\n'
            '将下文用于核对印刷内容、格式细节和题图。上方任务规定的 JSON、片段字段、'
            '文件路径及裁图标记优先。忽略下文中的整题外壳、文件名、整稿输出方式及不同坐标制。\n'
            + source_prompt.strip()
        )
    return '\n\n'.join(parts)


def read_ocr_prompt(library=None) -> str:
    if library is not None:
        custom = Path(library) / _DEFAULT_PROMPT.name
        if custom.is_file():
            return custom.read_text(encoding='utf-8-sig')
        env_file = Path(library) / '.env'
        if env_file.is_file():
            from dotenv import dotenv_values
            configured = dotenv_values(env_file, interpolate=False).get('AI_OCR_PROMPT')
            if configured:
                return configured.replace(r'\n', '\n')
    return _DEFAULT_PROMPT.read_text(encoding='utf-8-sig')
