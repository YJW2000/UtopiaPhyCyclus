"""Evaluate extracted high-school physics reasoning evidence on a fixed rubric."""
from collections import Counter
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
import re
import unicodedata

from services.question_metadata import normalize_question_metadata


RUBRIC_VERSION = "1.0"
EVIDENCE_INSTRUCTION = """
分析一道高中物理题，从题干、答案和解析提取可核对的事实。忽略原有标签和难度。
只返回以下 JSON，不直接给星级。单问题也写一个必答问。label 在题内唯一，按设问顺序排列。
{
  "parts": [{"label":"(1)", "required":true, "routes":[{
    "core_subject":"核心所求所属的候选板块",
    "points":[{"name":"必要且具体的知识点标准名称","subject":"所属候选板块"}],
    "steps":[{"id":"s1","conclusion":"应用的物理规律及本步得到的结论","depends_on":[]}],
    "coupling":0, "novelty":0, "inferred":false,
    "reason":"解析中能核对的要点、规律间的依赖及与常见教材模型相比的具体变化",
    "physics_methods":["实际用到的物理方法"]
  ]}], "tags":[], "insufficient_reason":""
}
一个完整合法的高中解法对应一条 route。列出解析中不同的完整解法，保留它们各自的必要知识点。
若题目指定解法，仅列允许的解法。若题目要求必须同时给出两种证明/解法，把两种任务分别列成必答问，
如 "(2)能量法" 和 "(2)动力学法"，不能把题目明确要求的任务放入二选一的 routes 中。必答问全部列出。
仅供延伸的选做问可省略或设 required=false。选择题按得出完整正确选项所需的推理分析。
知识点写必要的细分概念/规律，使用同一教材名称。同义词、上下位概念、同一规律的变形合并。
题干背景、干扰项中单纯出现的术语和数学运算不列为知识点。核心及辅助板块均须来自候选列表。
steps 只列必要的物理判断、建模、关系建立、转换、分类或推导。直接识记填空 steps=[]；
独立推理间不连边。连续代数、单位换算、题中已给的中间结论以及重复应用同一规则不得拆步。
step.id 在该解法内唯一；depends_on 填写直接用到的本解法步骤 id，跨问引用用 "@前问label"
表示该步需要前一必答问的完整结果（如 "@(1)"），无需重复写该前问的点或步骤。
若仅复用前问的一个局部中间结论，在本解法里单列该结论必要的知识点和步骤，避免卷入前问其余分支。
不能自依赖或成环。
coupling 根据该完整解法（含依赖前问）选 0–4：0 单点或彼此独立；
同一个知识点及其等价表述、单一物理模型中的代数与几何推导都不能产生跨知识点耦合。
返回前确认：若 coupling 大于 0，points 和被引用的前问合计至少要有两个不同的必要物理知识点。
1 两个知识点一次传递中间量；2 不同知识点连续传递条件或多个中间结果共同用于下一步；
3 多条规律联立决定同一状态；4 多个约束需要共同满足并反复切换模型或判定临界条件。
novelty 以高中教材及常见高考题中的典型模型作参照：0 教材与高考常见原型或仅换数值/情境；
1 常规变式；2 需主动识别或迁移常见模型；3 必须改造模型假设或使用非惯常表征；
4 必须构造非常规关系或新的模型。题干已给的提示不算创新；题库出现频率不作为证据。
解析完备时 inferred=false。缺解析或解析只能由题干明确推断时 inferred=true，在 reason 说明依据。
不足以判断某个必答问时请给出具体的 insufficient_reason，只列可可靠判断的完整问；
已明确识别的知识点可放在顶层 known_points（与 points 同结构），实际方法和自由标签可为空数组。
"""

# Only equivalent names belong here. Related searchable terms may describe different concepts.
_POINT_ALIASES = {
    "牛二": "牛顿第二定律", "牛顿第二运动定律": "牛顿第二定律",
    "牛三": "牛顿第三定律", "牛顿第三运动定律": "牛顿第三定律",
    "洛仑兹力": "洛伦兹力", "洛仑茲力": "洛伦兹力",
    "动量守恒": "动量守恒定律", "机械能守恒": "机械能守恒定律",
    "欧姆定理": "欧姆定律", "平抛": "平抛运动",
    "右手螺旋定则": "安培定则", "右手螺旋法则": "安培定则", "安培右手定则": "安培定则",
}


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"评分证据缺少有效的{field}。")
    return value.strip()


def _array(value, field):
    if not isinstance(value, list):
        raise ValueError(f"评分证据的{field}必须是数组。")
    return value


def _object(value, field):
    if not isinstance(value, dict):
        raise ValueError(f"评分证据的{field}必须是对象。")
    return value


def _names(values, field):
    return list(dict.fromkeys(_text(item, field) for item in _array(values, field)))


def _subject(value, allowed):
    name = _text(value, "知识板块")
    if name not in allowed:
        raise ValueError(f"评分证据含无效知识板块“{name}”。")
    return name


def _points(values, allowed):
    result = {}
    for item in _array(values, "知识点"):
        item = _object(item, "知识点")
        name = unicodedata.normalize("NFKC", _text(item.get("name"), "知识点名称"))
        name = re.sub(r"\s+", "", name) if re.fullmatch(r"[\u3400-\u9fff\s]+", name) else name
        name = _POINT_ALIASES.get(name, name)
        result.setdefault(name, _subject(item.get("subject"), allowed))
    return result


def _level(value, field):
    if type(value) is not int or not 0 <= value <= 4:
        raise ValueError(f"评分证据的{field}必须是 0–4 的整数。")
    return value


def _boolean(value, field):
    if type(value) is not bool:
        raise ValueError(f"评分证据的{field}必须是布尔值。")
    return value


def _longest_chain(steps, previous):
    """Compute the critical path, retaining its conclusions as the teacher's audit trail."""
    conclusions, local, external = {}, {}, {}
    previous_labels = []
    for step in _array(steps, "推理步骤"):
        step = _object(step, "推理步骤")
        identifier = _text(step.get("id"), "步骤编号")
        if identifier in conclusions or identifier.startswith("@"):
            raise ValueError(f"步骤编号“{identifier}”重复或含有保留前缀。")
        conclusions[identifier] = _text(step.get("conclusion"), "步骤结论")
        local[identifier], external[identifier] = [], []
        for target in _names(step.get("depends_on"), "步骤依赖"):
            if target.startswith("@"):
                label = target[1:]
                if label not in previous:
                    raise ValueError(f"无法引用前面必答问“{label}”的结果。")
                external[identifier].append(label)
                previous_labels.append(label)
            else:
                local[identifier].append(target)
    for targets in local.values():
        for target in targets:
            if target not in conclusions:
                raise ValueError(f"步骤依赖引用了不存在的步骤“{target}”。")
    chains = {}
    try:
        for identifier in TopologicalSorter(local).static_order():
            chains_in = [chains[target] for target in local[identifier]]
            chains_in.extend(previous[label].chain for label in external[identifier])
            chains[identifier] = max(chains_in, key=len, default=()) + (conclusions[identifier],)
    except CycleError:
        raise ValueError("推理步骤的依赖关系含有循环。") from None
    return max(chains.values(), key=len, default=()), list(dict.fromkeys(previous_labels))


@dataclass
class _Route:
    core: str
    points: dict
    subjects: tuple
    chain: tuple
    methods: tuple
    inferred: bool
    total: int
    details: dict


def _route(data, label, allowed, previous):
    data = _object(data, "解法")
    core = _subject(data.get("core_subject"), allowed)
    points = _points(data.get("points"), allowed)
    chain, dependencies = _longest_chain(data.get("steps"), previous)
    methods = _names(data.get("physics_methods", []), "物理方法")
    inferred = _boolean(data.get("inferred", False), "推断标记")
    required_subjects = [core, *points.values()]
    coupling = _level(data.get("coupling"), "知识耦合度")
    novelty = _level(data.get("novelty"), "新颖度")
    reason = _text(data.get("reason"), "解法依据")
    for dependency in dependencies:
        source = previous[dependency]
        for point, subject in source.points.items():
            points.setdefault(point, subject)
        required_subjects.extend(source.subjects)
        methods.extend(source.methods)
        inferred = inferred or source.inferred
        coupling = max(coupling, source.details["C"])
        novelty = max(novelty, source.details["N"])
    if not points:
        raise ValueError(f"必答问“{label}”的完整解法缺少必要知识点。")
    if len(points) == 1 and coupling:
        raise ValueError(f"必答问“{label}”仅含一个知识点，却报告了跨知识点耦合。")
    breadth = min(len(points) - 1, 4)
    depth = len(chain)
    complexity = min(depth, 3) if depth < 5 else 4
    total = 20 * breadth + 35 * complexity + 30 * coupling + 15 * novelty
    # Round the 0.5–6.0 scale to the nearest half-star with ties upwards, using integers.
    stars = (1 + (11 * total + 200) // 400) / 2
    details = dict(label=label, K=breadth, S=complexity, C=coupling, N=novelty, stars=stars,
                   reason=reason, points=list(points), chain=list(chain), dependencies=dependencies)
    return _Route(core, points, tuple(dict.fromkeys(required_subjects)), chain,
                  tuple(dict.fromkeys(methods)), inferred, total, details)


def score_evidence(evidence, subjects, *, has_solution):
    """Score validated facts. Explicitly insufficient evidence is distinct from malformed JSON."""
    evidence = _object(evidence, "整题")
    allowed = set(subjects) - {"未分类"}
    insufficient = evidence.get("insufficient_reason", "")
    if not isinstance(insufficient, str):
        raise ValueError("依据不足原因必须是文字。")
    insufficient = insufficient.strip()
    tags = _names(evidence.get("tags", []), "自由标签")
    known = _points(evidence.get("known_points", []), allowed)
    chosen, labels = {}, set()
    for part in _array(evidence.get("parts"), "小问"):
        part = _object(part, "小问")
        label = _text(part.get("label"), "小问编号")
        if label in labels or label.startswith("@"):
            raise ValueError(f"小问编号“{label}”重复或含有保留前缀。")
        labels.add(label)
        if not _boolean(part.get("required"), "必答标记"):
            continue
        routes = [_route(data, label, allowed, chosen)
                  for data in _array(part.get("routes"), "备选解法")]
        if not routes:
            if insufficient:
                continue
            raise ValueError(f"必答问“{label}”缺少完整解法。")
        chosen[label] = min(routes, key=lambda route: (route.total, route.inferred, len(route.chain)))
    if not chosen and not insufficient:
        raise ValueError("评分证据既未包含可评价的必答问，也未给出依据不足原因。")

    coverage = Counter(subject for route in chosen.values() for subject in route.subjects)
    ordered_subjects = list(coverage)
    points, methods = {}, []
    for route in chosen.values():
        points.update(route.points)
        methods.extend(route.methods)
    if insufficient:
        points.update(known)
        ordered_subjects.extend(known.values())
    difficulty, status, reason = "", "待评", insufficient
    if chosen:
        hardest = max(chosen.values(), key=lambda route: (route.details['stars'], coverage[route.core]))
        ordered_subjects = [hardest.core, *ordered_subjects]
        if not insufficient:
            difficulty = str(hardest.details["stars"])
            status = "正式" if has_solution and not any(r.inferred for r in chosen.values()) else "暂评"
            details = hardest.details
            reason = f'最难必答问 {details["label"]}，{details["reason"]}'
    meta = normalize_question_metadata({"难度星级": difficulty, "物理方法": "，".join(dict.fromkeys(methods)),
                                        "标签": "，".join(tags)})
    return dict(difficulty=difficulty, subject="，".join(dict.fromkeys(ordered_subjects)),
                knowledge_points="，".join(points), physics_methods=meta["物理方法"], tags=meta["标签"],
                status=status, reason=reason, parts=[route.details for route in chosen.values()])
