"""Named external images shared by question editing, previews and paper export."""
from __future__ import annotations

import base64
import hashlib
import html
import io
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMAGE_SUFFIXES = ('.pdf', '.png', '.jpg', '.jpeg')
GRAPHICS_RE = re.compile(
    r'\\includegraphics\*?\s*(?:\[[^\]]*\]\s*)?'
    r'\{(?P<path>\\detokenize\{[^{}]*\}|[^{}]*)\}'
)


def _uncomment(source: str) -> str:
    # Preserve offsets so replacing only a filename leaves surrounding TeX intact.
    return re.sub(r'(?m)(?<!\\)%[^\r\n]*', lambda m: ' ' * len(m[0]), source)


def graphics_references(source: str) -> list[dict]:
    refs = []
    for match in GRAPHICS_RE.finditer(_uncomment(source or '')):
        value = match['path'].strip()
        if value.startswith(r'\detokenize{'):
            value = value[len(r'\detokenize{'):-1]
        refs.append(dict(path=value.strip('"'), start=match.start(), end=match.end(),
                         path_start=match.start('path'), path_end=match.end('path'),
                         code=source[match.start():match.end()]))
    return refs


def resolve_image(path: str, project_dir=PROJECT_ROOT, source_dir=None, source='') -> Path:
    """Resolve explicit filenames, extensionless graphics and local graphicspaths."""
    if not path or re.search(r'[\x00-\x1f{}%#\\]', path) or '://' in path:
        raise ValueError(f'图片路径无效：{path}')
    root = Path(project_dir).resolve()
    bases = list(dict.fromkeys([Path(source_dir or root).resolve(), root]))
    graphic_dirs = []
    for match in re.finditer(r'\\graphicspath\s*\{((?:\s*\{[^{}]*\}\s*)+)\}', _uncomment(source)):
        graphic_dirs.extend(re.findall(r'\{([^{}]*)\}', match[1]))
    candidates = []
    for base in bases:
        candidates.append(base / path)
        candidates.extend(base / directory / path for directory in graphic_dirs)
    for candidate in candidates:
        choices = [candidate] if candidate.suffix else [candidate.with_suffix(ext) for ext in IMAGE_SUFFIXES]
        for choice in choices:
            if choice.suffix.lower() in IMAGE_SUFFIXES and choice.is_file():
                return choice.resolve()
    raise FileNotFoundError(f'找不到图片文件：{path}。请上传图片或检查资源目录。')


def _named_file(directory: Path, name: str, data: bytes) -> Path:
    """Keep original names; different bytes under the same name never overwrite."""
    directory.mkdir(parents=True, exist_ok=True)
    directory = directory.resolve()
    name = Path(str(name).replace('\\', '/')).name
    name = re.sub(r'[<>:"/\\|?*{}%#$^~&\x00-\x1f]', '_', name).strip(' .') or 'image.png'
    if name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *('COM%d' % n for n in range(1, 10)), *('LPT%d' % n for n in range(1, 10))}:
        name = '_' + name
    destination = directory / name
    if destination.exists() and destination.read_bytes() != data:
        stem, suffix = Path(name).stem, Path(name).suffix
        destination = directory / f'{stem}-{hashlib.sha256(data).hexdigest()}{suffix}'
    if destination.resolve().parent != directory:
        raise ValueError('图片保存位置超出目标目录。')
    if not destination.exists():
        # Exclusive creation also prevents concurrent uploads from replacing a file.
        try:
            with destination.open('xb') as handle:
                handle.write(data)
        except FileExistsError:
            if destination.read_bytes() != data:
                return _named_file(directory, Path(name).stem + '-' + hashlib.sha256(data).hexdigest() + Path(name).suffix, data)
    return destination


def save_uploaded_image(data: bytes, name: str, project_dir=PROJECT_ROOT) -> str:
    """Persist an upload before inserting its TeX reference; preserve PNG/JPEG bytes."""
    if not data or len(data) > 25_000_000:
        raise ValueError('图片为空或超过 25 MB。')
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:
        image.verify()
        fmt = image.format
    suffix = {'PNG': '.png', 'JPEG': '.jpg'}.get(fmt)
    if suffix is None:
        with Image.open(io.BytesIO(data)) as image:
            buffer = io.BytesIO()
            image.convert('RGBA').save(buffer, format='PNG')
            data = buffer.getvalue()
        suffix = '.png'
    name = Path(str(name).replace('\\', '/')).name or 'image'
    if Path(name).suffix.lower() not in ({'.jpg', '.jpeg'} if fmt == 'JPEG' else {suffix}):
        name = Path(name).stem + suffix
    root = Path(project_dir).resolve()
    saved = _named_file(root / '.runtime' / 'question-images', name, data)
    return saved.relative_to(root).as_posix()


def save_reference_images(images: list[dict], project_dir=PROJECT_ROOT) -> list[dict]:
    saved = []
    for image in images:
        raw = base64.b64decode(image['data_url'].split(',', 1)[1], validate=True)
        saved.append(dict(name=image['name'], path=save_uploaded_image(raw, image['name'], project_dir)))
    return saved


def copy_graphics(source: str, destination: Path, project_dir=PROJECT_ROOT, source_dir=None, reference_base=None) -> str:
    """Copy dependencies and rewrite only includegraphics filename arguments."""
    replacements = []
    for ref in graphics_references(source):
        original = resolve_image(ref['path'], project_dir, source_dir, source)
        saved = _named_file(destination, original.name, original.read_bytes())
        replacements.append((ref, saved))
    base = Path(reference_base or destination.parent).resolve()
    for ref, saved in reversed(replacements):
        filename = saved.relative_to(base).as_posix()
        source = source[:ref['path_start']] + filename + source[ref['path_end']:]
    return source


def package_question_images(source: str, filename: str, save_dir, project_dir=PROJECT_ROOT, source_dir=None) -> str:
    root = Path(project_dir).resolve()
    directory = Path(save_dir).resolve()
    base = root if directory.is_relative_to(root) else directory
    return copy_graphics(source, directory / (Path(filename).stem + ' 相关图'), root,
                         source_dir or save_dir, reference_base=base)


def image_preview_markdown(path: str, project_dir=PROJECT_ROOT, source_dir=None, source='') -> str:
    try:
        resolved = resolve_image(path, project_dir, source_dir, source)
        data = resolved.read_bytes()
        if resolved.suffix.lower() == '.pdf':
            import fitz
            with fitz.open(stream=data, filetype='pdf') as document:
                data = document[0].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).tobytes('png')
            mime = 'image/png'
        else:
            mime = 'image/jpeg' if resolved.suffix.lower() in ('.jpg', '.jpeg') else 'image/png'
        encoded = base64.b64encode(data).decode('ascii')
        return f'\n\n<img src="data:{mime};base64,{encoded}" alt="{html.escape(path, quote=True)}" style="max-width:100%;max-height:480px;object-fit:contain" />\n\n'
    except (OSError, ValueError) as exc:
        return '\n\n> ⚠️ ' + html.escape(str(exc)) + '\n\n'


def graphics_fingerprint(source: str, project_dir=PROJECT_ROOT, source_dir=None) -> tuple:
    tokens = []
    for ref in graphics_references(source):
        try:
            image = resolve_image(ref['path'], project_dir, source_dir, source)
            stat = image.stat()
            tokens.append((str(image), stat.st_mtime_ns, stat.st_size))
        except (OSError, ValueError):
            tokens.append((ref['path'], 0, 0))
    return tuple(tokens)
