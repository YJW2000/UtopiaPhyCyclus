"""Locate OCR illustrations in model output and crop the original source pixels."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import io
import math
import re

from PIL import Image, ImageOps

from utils.image_assets import PROJECT_ROOT, graphics_references, resolve_image, save_uploaded_image


OCR_CROP_INSTRUCTIONS = r"""
【自动保留原图插图协议（优先于要求手工上传或全部 TikZ 重绘的旧规则）】
照片、复杂实验装置、仪器读数/刻度、纹理细节、无法可靠重绘的曲线或图形，直接从对应原图裁剪，不能猜画。
在题干、某个选项、答案或解析中原本应出现该插图的位置，输出：
\UtopiaImageCrop{原图编号}{左,上,右,下}
例如 \UtopiaImageCrop{1}{120,250,780,610}。
需要调整排版宽度时可写 \UtopiaImageCrop[0.65]{1}{120,250,780,610}，0.65 表示所在正文/选项宽度的 65%。
原图编号从 1 开始，严格对应本次请求中【原图 1】、【原图 2】的顺序。
坐标必须使用 0 到 1000 的归一化坐标；左上为 (0,0)，右下为 (1000,1000)，顺序固定为左、上、右、下。
框住完整插图及其编号、图注、刻度、单位和相关标签，留少量边缘；排除周围题干、其他题目、页眉页脚。
同一题多图分别给标记；跨页或多题时各自使用正确的原图编号，并把标记放回各自题目对应位置。
标记由程序在原始分辨率上裁图、保存为 PNG 并替换成真实 includegraphics 引用。不要编造文件路径，
不要只写“% 插图位置”或要求用户再上传；标记不得写进注释、数学模式或 tikzpicture 内，也不额外输出坐标清单。
简单且能准确还原的物理示意图仍可使用 TikZ；裁图与 TikZ 可以在同一道题中混排。
输出前核对每个框的原图编号、横纵方向、边界及归属题目，确保未切掉关键刻度和文字。
"""

_CROP_NAME = r'\UtopiaImageCrop'
_CROP_RE = re.compile(
    r'\\UtopiaImageCrop\s*(?:\[\s*(?P<width>[^\]]*)\s*\]\s*)?'
    r'\{\s*(?P<image>\d+)\s*\}\s*\{(?P<box>[^{}]*)\}'
)


class OcrCropError(ValueError):
    """Invalid localization is retried, never silently published as broken TeX."""


@dataclass
class OcrCropResult:
    latex: str
    crops: list[dict]


def prepare_ocr_images(images, prompt: str, max_image_size: int = 1600):
    """Use one orientation for model input and original-resolution crop sources."""
    if not images:
        raise ValueError('没有提供图片')
    if max_image_size < 1:
        raise ValueError('识别图片尺寸必须大于零')
    originals = []
    parts = [{'type': 'text', 'text': prompt + '\n\n' + OCR_CROP_INSTRUCTIONS}]
    for index, image in enumerate(images, 1):
        oriented = ImageOps.exif_transpose(image)
        # Match the visible white page, including screenshots with transparency.
        if 'A' in oriented.getbands() or 'transparency' in oriented.info:
            rgba = oriented.convert('RGBA')
            background = Image.new('RGBA', rgba.size, 'white')
            original = Image.alpha_composite(background, rgba).convert('RGB')
        else:
            original = oriented.convert('RGB')
        originals.append(original)
        longest = max(original.size)
        scale = min(2.0, max_image_size / longest) if longest < 1000 else min(1.0, max_image_size / longest)
        sent = original
        if scale != 1:
            sent = original.resize(tuple(max(1, int(side * scale)) for side in original.size), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        sent.save(buffer, format='PNG', optimize=True)
        parts.append({'type': 'text', 'text': f'【原图 {index}】阅读图尺寸 {sent.width}×{sent.height}；裁图坐标统一按 0—1000 归一化。'})
        parts.append({'type': 'image_url', 'image_url': {
            'url': 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii'),
        }})
    return originals, parts


def _crop_specifications(latex: str, images, padding_ratio: float):
    matches = list(_CROP_RE.finditer(latex))
    if len(matches) != latex.count(_CROP_NAME):
        raise OcrCropError('自动裁图标记不完整；请按 \\UtopiaImageCrop{原图编号}{左,上,右,下} 返回。')
    if '% 插图位置' in latex or re.search(r'\[原题图无法可靠转写\]', latex):
        raise OcrCropError('待保留的插图缺少原图编号和裁剪坐标；请在原位置给出自动裁图标记。')
    if len(matches) > 64:
        raise OcrCropError('一次识别的裁图数量超过 64 张，请减少本批次页面。')
    specs = []
    for match in matches:
        index = int(match['image'])
        if not 1 <= index <= len(images):
            raise OcrCropError(f'裁图引用原图 {index}，但本次只有 {len(images)} 张原图。')
        prefix = latex[latex.rfind('\n', 0, match.start()) + 1:match.start()]
        if re.search(r'(?<!\\)%', prefix):
            raise OcrCropError('裁图标记被写进注释，无法显示；请移到正文对应位置。')
        if latex[:match.start()].rfind(r'\begin{tikzpicture}') > latex[:match.start()].rfind(r'\end{tikzpicture}'):
            raise OcrCropError('裁图标记位于 tikzpicture 内，请作为独立插图放在对应正文或选项中。')
        try:
            box = [float(value.strip()) for value in match['box'].strip().strip('[]').replace('，', ',').split(',')]
            width = float(match['width']) if match['width'] is not None else 0.65
        except (ValueError, TypeError):
            raise OcrCropError('裁图坐标和排版宽度必须是数字。') from None
        if len(box) != 4 or any(not math.isfinite(value) or not 0 <= value <= 1000 for value in box):
            raise OcrCropError('裁图坐标必须是四个 0—1000 的有限数字，顺序为左、上、右、下。')
        if max(box) <= 1:
            raise OcrCropError('裁图坐标请使用 0—1000，不是 0—1 的比例。')
        left, top, right, bottom = box
        if left >= right or top >= bottom:
            raise OcrCropError('裁图区域为空或边界顺序错误，必须左 < 右、上 < 下。')
        if not math.isfinite(width) or not 0.1 <= width <= 1:
            raise OcrCropError('插图排版宽度必须在 0.1 到 1 之间。')
        original = images[index - 1]
        pixels = (math.floor(left * original.width / 1000), math.floor(top * original.height / 1000),
                  math.ceil(right * original.width / 1000), math.ceil(bottom * original.height / 1000))
        if pixels[2] - pixels[0] < 2 or pixels[3] - pixels[1] < 2:
            raise OcrCropError('裁图区域过小，请重新定位完整插图。')
        padding = max(0, round(min(original.size) * padding_ratio))
        pixels = (max(0, pixels[0] - padding), max(0, pixels[1] - padding),
                  min(original.width, pixels[2] + padding), min(original.height, pixels[3] + padding))
        specs.append((match, index, box, pixels, width))
    return specs


def materialize_ocr_crops(latex: str, images, project_dir=PROJECT_ROOT, padding_ratio: float = 0.002) -> OcrCropResult:
    """Validate every box before saving; replace only the matching inline markers."""
    latex = str(latex or '')
    specs = _crop_specifications(latex, images, padding_ratio)
    for ref in graphics_references(latex):
        try:
            resolve_image(ref['path'], project_dir)
        except (OSError, ValueError):
            raise OcrCropError(f"插图 {ref['path']} 没有对应文件，请改用原图编号和裁剪坐标。") from None
    crops, replacements, saved_regions = [], [], {}
    for match, index, box, pixels, width in specs:
        region = (index, pixels)
        path = saved_regions.get(region)
        if path is None:
            crop = images[index - 1].crop(pixels)
            buffer = io.BytesIO()
            crop.save(buffer, format='PNG', optimize=True)
            data = buffer.getvalue()
            digest = hashlib.sha256(data).hexdigest()[:16]
            try:
                path = save_uploaded_image(data, f'ocr-page{index}-{digest}.png', project_dir)
            except (OSError, ValueError) as exc:
                raise OcrCropError(f'裁剪图片保存失败：{exc}') from exc
            saved_regions[region] = path
            crops.append(dict(path=path, image_index=index, normalized_box=box,
                              pixel_box=list(pixels), size=list(crop.size)))
        reference = r'\includegraphics[width=%.2f\linewidth]{%s}' % (width, path)
        replacements.append((match, reference))
    for match, reference in reversed(replacements):
        latex = latex[:match.start()] + reference + latex[match.end():]
    return OcrCropResult(latex=latex, crops=crops)


def crop_retry_messages(messages: list, reply: str, error: OcrCropError) -> list:
    """Keep the numbered image inputs when asking for one localization repair."""
    return messages + [
        {'role': 'assistant', 'content': reply},
        {'role': 'user', 'content': '自动裁图定位检查失败：' + str(error) + '\n请核对上面的编号原图，只修正插图位置的裁图标记；'
         '保留全部已识别题干、选项、答案、解析与题目顺序，重新输出完整识别结果，保持上一轮要求的输出格式。'},
    ]
