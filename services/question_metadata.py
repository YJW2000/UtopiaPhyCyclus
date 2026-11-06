"""Shared metadata preferences for classification, import and editing."""
import re


def is_one_star(value):
    try:
        return float(value) == 1.0
    except (TypeError, ValueError):
        return False


def normalize_question_metadata(metadata):
    result = dict(metadata)
    if "标签" in result:
        tags = [tag.strip() for tag in re.split(r"[，,；;]", str(result["标签"] or ""))]
        result["标签"] = "，".join(dict.fromkeys(
            tag for tag in tags if tag and re.sub(r"\s+", "", tag).casefold() != "ai原创"
        ))
    if is_one_star(result.get("难度星级")) and "物理方法" in result:
        result["物理方法"] = ""
    return result
