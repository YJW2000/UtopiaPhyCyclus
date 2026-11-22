"""Crop a visible PDF region at the resolution of its placed images."""
from __future__ import annotations

import math


def page_selection_rect(page, rect):
    import pymupdf

    if (not isinstance(rect, (list, tuple)) or len(rect) != 4
            or not all(type(v) in (int, float) and math.isfinite(v) for v in rect)):
        raise ValueError("选区坐标无效，请重新框选。")
    x0, y0, x1, y1 = rect
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError("选区越过页面或尺寸为零，请重新框选。")
    return pymupdf.Rect(x0 * page.rect.width, y0 * page.rect.height,
                        x1 * page.rect.width, y1 * page.rect.height)


def crop_pdf_region(pdf_bytes: bytes, page_number: int, rect: list[float]) -> bytes:
    """Crop the visible composite, including masks, overlays and page rotation."""
    import pymupdf

    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
        if type(page_number) is not int or not 1 <= page_number <= len(document):
            raise ValueError("图片所在 PDF 页码无效。")
        page = document[page_number - 1]
        visible = page_selection_rect(page, rect)
        unrotated = visible * page.derotation_matrix
        native_scale = 0.0
        for info in page.get_image_info():
            if not pymupdf.Rect(info["bbox"]).intersects(unrotated):
                continue
            a, b, c, d, _, _ = info["transform"]
            width, height = math.hypot(a, b), math.hypot(c, d)
            if width and height:
                native_scale = max(native_scale, info["width"] / width, info["height"] / height)
        # Vectors have no native DPI. For scans, match source pixel density
        # and render the displayed crop, including actual masks and overlays.
        scale = native_scale or (300 / 72)
        if math.ceil(visible.width * scale) * math.ceil(visible.height * scale) > 48_000_000:
            raise ValueError("按原始清晰度裁剪的图片超过 4800 万像素，请缩小图片选区。")
        pixels = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=visible, alpha=False)
        if not pixels.width or not pixels.height:
            raise ValueError("图片区域太小，请扩大裁剪范围。")
        return pixels.tobytes("png")
