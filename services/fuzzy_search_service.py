"""Local, deterministic physics search across question text and metadata."""
import datetime
import math
import re
import unicodedata
from dataclasses import dataclass


# Related terms expand recall; literal matches get the largest match factor.
RELATED_TERMS = (
    ("动量守恒", "动量守恒定律", "碰撞", "反冲", "爆炸"),
    ("机械能守恒", "机械能守恒定律", "自由落体", "单摆"),
    ("动能定理", "功能关系", "功与能", "功与能量"),
    ("能量守恒", "能量转化", "能量守恒定律"),
    ("牛顿第二定律", "牛二", "动力学", "加速度"),
    ("牛顿第三定律", "牛三", "作用力与反作用力"),
    ("受力分析", "隔离法", "整体法", "整体隔离法"),
    ("力的分解", "正交分解", "正交分解法", "合成与分解"),
    ("平衡", "共点力平衡", "静力学"),
    ("匀变速直线运动", "匀加速", "匀减速", "运动学"),
    ("平抛", "平抛运动", "运动的合成与分解"),
    ("圆周运动", "向心力", "向心加速度"),
    ("万有引力", "天体运动", "卫星", "航天", "开普勒"),
    ("电场", "静电场", "库仑力", "电场力"),
    ("电势", "电势差", "电势能", "等势面"),
    ("洛伦兹力", "洛仑兹力", "磁场运动", "带电粒子", "粒子偏转"),
    ("质谱仪", "速度选择器", "回旋加速器"),
    ("安培力", "通电导线", "左手定则"),
    ("电磁感应", "感应电流", "感应电动势", "法拉第", "楞次定律"),
    ("交流电", "交变电流", "变压器", "正弦交流电"),
    ("闭合电路", "欧姆定律", "内阻", "电源电动势"),
    ("电表改装", "多用电表", "欧姆表", "伏安法"),
    ("简谐运动", "弹簧振子", "机械振动"),
    ("机械波", "波动", "波的传播", "波长"),
    ("光的折射", "折射率", "全反射", "光学"),
    ("干涉", "衍射", "波动光学"),
    ("热力学", "内能", "热力学第一定律", "理想气体"),
    ("光电效应", "光子", "爱因斯坦光电方程"),
    ("核反应", "核能", "质量亏损", "质能方程"),
    ("图像法", "图象法", "图像分析", "图象分析"),
    ("等效法", "等效替代", "等效替代法"),
    ("极限法", "极限分析", "临界条件", "临界问题"),
    ("控制变量", "控制变量法", "实验探究"),
    ("传送带", "传送带模型", "皮带"),
    ("板块模型", "滑块木板", "木板滑块"),
)
FIELD_WEIGHTS = {
    "题干": 5.0, "标签": 6.0, "知识点": 6.0, "物理方法": 6.0,
    "知识板块": 4.0, "解析": 3.0, "答案": 2.0, "备注": 2.0,
    "题型": 2.0, "试卷名称": 2.0, "文件名称": 1.0, "年份": 1.0,
}
SORT_OPTIONS = ("相关度", "录入时间：由新到旧", "录入时间：由旧到新", "难度：由低到高", "难度：由高到低")
QUERY_WORDS = ("小球", "斜面", "滑块", "木板", "弹簧", "摩擦力", "重力", "速度", "位移", "时间", "电阻", "电容", "磁感应强度")


def normalize(text):
    return "".join(re.findall(r"[\w]+", unicodedata.normalize("NFKC", str(text or "")).casefold()))


def _plain_text(text):
    # Remove LaTeX control words but keep their argument text and formulas.
    text = re.sub(r"(?m)(?<!\\)%.*$", "", str(text or ""))
    text = re.sub(r"\\(?:begin|end)\{[^}]*\}|\\[a-zA-Z]+\*?", " ", text)
    return normalize(text)


def _grams(text):
    return frozenset(text[i:i + 2] for i in range(len(text) - 1))


_RELATED = {}
for _group in RELATED_TERMS:
    _normalized_group = {normalize(term) for term in _group}
    for _term in _normalized_group:
        _RELATED.setdefault(_term, set()).update(_normalized_group - {_term})


@dataclass(frozen=True)
class SearchDocument:
    row: dict
    fields: tuple


def build_documents(rows):
    return tuple(SearchDocument(dict(row), tuple(
        (field, _plain_text(row.get(field, "")), weight)
        for field, weight in FIELD_WEIGHTS.items() if row.get(field)
    )) for row in rows)


def metadata_terms(row):
    return {normalize(term) for field in ("知识点", "物理方法", "标签", "知识板块")
            for term in re.split(r"[\s,，、;；/|]+", str(row.get(field, "") or ""))
            if len(normalize(term)) >= 2}


def _query_terms(query, documents=(), vocabulary=None):
    vocabulary = set(vocabulary or ()) | {normalize(term) for group in RELATED_TERMS for term in group} | set(QUERY_WORDS)
    for doc in documents:
        vocabulary.update(metadata_terms(doc.row))
    ordered = sorted(vocabulary, key=lambda word: (-len(word), word))
    terms = []
    # Common natural-language wrappers carry no subject information.
    query = re.sub(r"帮我(?:找一下|查找|找|查)|有关|相关题目|相关的题目|的题目|题目|关于", " ", query)
    def add_fragment(fragment):
        terms.extend(term for term in re.split(r"以及|并且|和|与|的", fragment) if term)

    for part in re.split(r"[\s,，、;；/|]+", query):
        part = normalize(part)
        while part:
            found = next(((i, word) for i in range(len(part))
                          for word in ordered if part.startswith(word, i)), None)
            if found is None:
                add_fragment(part)
                break
            offset, word = found
            if offset:
                add_fragment(part[:offset])
            terms.append(word)
            part = part[offset + len(word):]
    terms = list(dict.fromkeys(term for term in terms if term))
    if len(terms) > 1:
        stop_words = {"在", "上", "下", "中", "时", "是", "有", "用", "求", "请", "找", "查", "一下"}
        terms = [term for term in terms if term not in stop_words]
    return tuple(terms)


def _term_match(term, fields):
    related = _RELATED.get(term, ())
    grams = _grams(term) if len(term) >= 3 else ()
    best, reason = 0.0, ""
    for field, value, weight in fields:
        strength, kind = 0.0, ""
        if term in value:
            strength, kind = 1.0, "命中"
        elif any(word in value for word in related):
            strength, kind = 0.55, "关联"
        elif len(term) >= 3:
            overlap = sum(gram in value for gram in grams)
            coverage = overlap / len(grams)
            if overlap >= 2 and coverage >= 0.6:
                strength, kind = 0.4 * coverage, "近似"
        score = weight * strength
        if score > best:
            best, reason = score, f"{field}{kind}「{term}」"
    return best, reason


def search(documents, query, *, terms=None):
    """Return all matching rows; sorting and pagination never truncate recall."""
    query = str(query or "").strip()
    if not query or not normalize(query):
        return []
    terms = _query_terms(query, documents) if terms is None else terms
    if not terms:
        return []
    results = []
    phrase = normalize(query)
    for doc in documents:
        matches = [_term_match(term, doc.fields) for term in terms]
        matched = [(score, reason) for score, reason in matches if score > 0]
        if not matched:
            continue
        coverage = len(matched) / len(terms)
        # Complete phrases and covering more query terms lead the ranking.
        phrase_bonus = 6.0 if any(phrase in value for _, value, _ in doc.fields) else 0.0
        score = sum(value for value, _ in matched) * (0.5 + coverage) + phrase_bonus
        results.append({"row": doc.row, "score": score,
                        "reason": "；".join(reason for _, reason in matched)})
    return sort_results(results)


def _entry_time(row):
    try:
        return datetime.datetime.fromisoformat(str(row.get("初次录入的时间", "")).strip()).timestamp()
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def _difficulty(row):
    try:
        value = float(row.get("难度星级", ""))
        return value if math.isfinite(value) and 0 <= value <= 6 else None
    except (TypeError, ValueError):
        return None


def sort_results(results, order="相关度"):
    def key(result):
        row = result["row"]
        tie = (-result["score"], str(row.get("相对文件路径", "")), str(row.get("题目ID", "")))
        if order.startswith("录入时间"):
            value = _entry_time(row)
            direction = -1 if order == "录入时间：由新到旧" else 1
        elif order.startswith("难度"):
            value = _difficulty(row)
            direction = -1 if order == "难度：由高到低" else 1
        else:
            return tie
        return (value is None, direction * value if value is not None else 0, *tie)
    return sorted(results, key=key)
