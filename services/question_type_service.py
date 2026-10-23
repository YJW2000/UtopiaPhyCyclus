"""Canonical physics question types shared by editors and the derived index."""
import re

QUESTION_TYPES = ("单项选择题", "多项选择题", "实验题", "计算题")


def require_question_type(value):
    if value not in QUESTION_TYPES:
        raise ValueError("请为每道题选择题型：单项选择题、多项选择题、实验题或计算题。")
    return value


def infer_question_type(content, subject=""):
    """Suggest a type from explicit labels, option layout and answer evidence.

    Manual canonical metadata always wins. Suggestions for new imports remain
    editable; experimental keywords in a multiple-choice stem do not turn it
    into a free-response experiment question.
    """
    from utils.latex_ops import parse_meta_data

    meta, clean = parse_meta_data(content)
    if meta.get("题型") in QUESTION_TYPES:
        return meta["题型"]
    # Ignore drawings and comments: their coordinate names are not options.
    text = re.sub(r"\\begin\{tikzpicture\}[\s\S]*?\\end\{tikzpicture\}", "", clean)
    text = re.sub(r"(?m)^\s*%.*$", "", text)
    stem = re.split(r"\\begin\{(?:answer|solutions?)\}|\\textbf\{解析[：:]?\}", text)[0]
    has_choices = bool(
        re.search(r"\\begin\{choices\}|\\choice\b", stem)
        or re.search(r"\\begin\{enumerate\}\[[^\]]*\\Alph", stem)
        or (re.search(r"(?:^|\n)\s*A[.．、]", stem) and re.search(r"(?:^|\n)\s*D[.．、]", stem))
    )
    hints = str(meta.get("标签", "")) + " " + stem
    if has_choices or re.search(r"单[项选]|多[项选]", hints):
        if re.search(r"多项选择|多选|不定项|多个选项|两项或", hints):
            return QUESTION_TYPES[1]
        if re.search(r"单项选择|单选|只有一[项个]", hints):
            return QUESTION_TYPES[0]
        answer_match = re.search(r"\\begin\{answer\}([\s\S]*?)\\end\{answer\}", text)
        answer = answer_match.group(1) if answer_match else ""
        # Contiguous BC/ACD, or separated A、C, in the answer environment.
        compact_answer = re.sub(r"\\[A-Za-z]+|[{}$\s、，,和及]", "", answer)
        groups = re.findall(r"(?<![A-Za-z])[A-D]{1,4}(?![A-Za-z])", compact_answer)
        # Some imported files keep the answer inside the problem's explanation.
        if not groups:
            groups = re.findall(r"(?:答案|故选|选项为)[：:\s]*[${\\\s]*(?:text\{|boxed\{|textbf\{)*\s*([A-D]{1,4})(?![A-Za-z])", text)
        if any(len(set(group)) > 1 for group in groups):
            return QUESTION_TYPES[1]
        return QUESTION_TYPES[0]
    if "实验" in subject or "试验" in subject or re.search(
        r"实验题|实验步骤|实验器材|实验数据|某(?:同学|小组).*?实验|做[“\"\s]*.*?实验|测量.*?(?:实验|装置)|实验.*?(?:测量|探究|验证)", stem, re.S
    ):
        return QUESTION_TYPES[2]
    return QUESTION_TYPES[3]
