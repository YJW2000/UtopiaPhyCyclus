import os
import datetime
import re
from .core_config import CSV_INDEX_PATH, CHAPTERS_DIR
from .latex_ops import parse_meta_data
from services.question_type_service import infer_question_type

from utils.index_schema import CSV_HEADERS
from utils import question_index


def read_csv_index():
    """Compatibility API: read the SQLite index; CSV is an explicit export."""
    return question_index.read_rows(csv_path=CSV_INDEX_PATH)


def ensure_csv_index():
    """Compatibility name retained for plugins and older maintenance scripts."""
    return question_index.ensure_index(CSV_INDEX_PATH)


def write_csv_index(data):
    """Transactional bulk replacement, reserved for imports and maintenance."""
    rows = normalize_csv_rows(data)
    issues = validate_csv_rows(rows)
    if issues:
        raise ValueError(f"Index validation failed before write: {issues[:5]}")
    question_index.replace_rows(rows, csv_path=CSV_INDEX_PATH)


def normalize_csv_rows(data):
    """Return rows containing exactly the managed CSV headers."""
    normalized = []
    for row in data:
        normalized.append({field: row.get(field, "") for field in CSV_HEADERS})
    return normalized

def find_duplicate_ids(data):
    """只读检查：返回重复的题目ID及其行号，不修改CSV数据。"""
    id_field = CSV_HEADERS[0]
    seen = {}
    duplicates = []
    for row_num, row in enumerate(data, start=2):
        qid = str(row.get(id_field, "")).strip()
        if not qid:
            continue
        if qid in seen:
            duplicates.append({"题目ID": qid, "首次行号": seen[qid], "重复行号": row_num})
        else:
            seen[qid] = row_num
    return duplicates

def validate_csv_rows(data, required_fields=None):
    """只读检查：返回缺少关键字段或重复ID的问题列表，不修改CSV数据。"""
    if required_fields is None:
        required_fields = CSV_HEADERS[:3]

    issues = []
    for row_num, row in enumerate(data, start=2):
        for field in required_fields:
            if not str(row.get(field, "")).strip():
                issues.append({"行号": row_num, "字段": field, "问题": "缺少必填值"})

    for duplicate in find_duplicate_ids(data):
        issues.append({"行号": duplicate["重复行号"], "字段": CSV_HEADERS[0], "问题": f"重复ID：{duplicate['题目ID']}"})

    return issues

def get_next_id():
    """获取下一个可用的全局ID"""
    return question_index.next_id(CSV_INDEX_PATH)


def _parse_tex_content(content, pname, subject=""):
    """解析 tex 内容，提取题干、答案、解析、题型、Meta Data 等信息"""
    meta, clean_content = parse_meta_data(content)

    has_tikz = "是" if "\\begin{tikzpicture}" in clean_content else "否"

    # Extract problem content (题干部分)
    # 兼容多种写法： \begin{problem}{...} 或者没有参数的 \begin{problem}
    prob_match = re.search(
        r'\\begin\{problem\}(?:\[[^\]]*\])?(?:\s*\{[^\}]*\}){0,5}\s*([\s\S]*?)\\end\{problem\}',
        clean_content,
        re.DOTALL,
    )
    stem_text = prob_match.group(1).strip() if prob_match else ""

    # Extract solution (解析部分) - 独立于 problem 之外
    sol_match = re.search(r'\\begin\{solutions?\}(.*?)\\end\{solutions?\}', clean_content, re.DOTALL)
    sol_text = sol_match.group(1).strip() if sol_match else ""

    # Extract answer (答案部分)
    ans_match = re.search(r'\\begin\{answer\}(.*?)\\end\{answer\}', clean_content, re.DOTALL)
    ans_text = ans_match.group(1).strip() if ans_match else ""

    has_solution = "是" if sol_text else "否"

    # 如果旧格式中，solution 嵌套在了 problem 内部，需要从 stem 中剔除它
    if sol_match and sol_match.group(0) in stem_text:
        stem_text = stem_text.replace(sol_match.group(0), "")
    if ans_match and ans_match.group(0) in stem_text:
        stem_text = stem_text.replace(ans_match.group(0), "")
    stem_text = stem_text.strip()

    q_type = infer_question_type(content, subject)

    return has_tikz, q_type, has_solution, stem_text, ans_text, sol_text, meta

def add_to_csv_index(file_path, content, year, ptype, pname, pnum, subj):
    """Create one SQLite index row; allocate its ID inside the transaction."""
    name_body = os.path.basename(file_path).replace(".tex", "")
    rel_path = os.path.relpath(file_path, CHAPTERS_DIR)

    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    has_tikz, q_type, has_solution, stem_text, ans_text, sol_text, meta = _parse_tex_content(content, pname, subj)

    # 优先使用文件中记录的 ID，如果没有则生成新的
    new_id = meta.get("ID")
    if not new_id:
        new_id = ""

    new_row = {
        "题目ID": new_id,
        "文件名称": name_body,
        "相对文件路径": rel_path,
        "年份": year,
        "试卷类型": ptype,
        "试卷名称": pname,
        "原卷题号": pnum,
        "知识板块": subj,
        "标签": meta.get("标签", ""),
        "知识点": meta.get("知识点", ""),
        "物理方法": meta.get("物理方法", ""),
        "包含TikZ绘图": has_tikz,
        "题型": q_type,
        "难度星级": meta.get("难度星级", ""),
        "包含解析": has_solution,
        "组卷引用次数": meta.get("组卷引用次数", "0"),
        "备注": meta.get("备注", ""),
        "来源题目ID": meta.get("来源题目ID", ""),
        "挖空类型": meta.get("挖空类型", ""),
        "生成时间": meta.get("生成时间", ""),
        "初次录入的时间": now_str,
        "最后修改时间": now_str,
        "题干": stem_text,
        "答案": ans_text,
        "解析": sol_text
    }

    return question_index.put_row(new_row, csv_path=CSV_INDEX_PATH)

def update_csv_index_for_edit(old_file_path, new_file_path, new_content, new_year, new_ptype, new_pname, new_pnum, new_subj):
    """Update one SQLite row by its old path, preserving ID and entry time."""
    old_rel_path = os.path.relpath(old_file_path, CHAPTERS_DIR)
    old = question_index.get_row(old_rel_path, csv_path=CSV_INDEX_PATH)
    if old is None:
        return add_to_csv_index(new_file_path, new_content, new_year, new_ptype, new_pname, new_pnum, new_subj)
    has_tikz, q_type, has_solution, stem, answer, solution, meta = _parse_tex_content(new_content, new_pname, new_subj)
    old.update({
        "文件名称": os.path.splitext(os.path.basename(new_file_path))[0],
        "相对文件路径": os.path.relpath(new_file_path, CHAPTERS_DIR),
        "年份": new_year, "试卷类型": new_ptype, "试卷名称": new_pname,
        "原卷题号": new_pnum, "知识板块": new_subj, "包含TikZ绘图": has_tikz,
        "题型": q_type, "包含解析": has_solution, "题干": stem, "答案": answer, "解析": solution,
        "最后修改时间": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    })
    for field in ("标签", "知识点", "物理方法", "难度星级", "备注", "组卷引用次数",
                  "来源题目ID", "挖空类型", "生成时间"):
        old[field] = meta.get(field, old.get(field, ""))
    return question_index.put_row(old, old_path=old_rel_path, csv_path=CSV_INDEX_PATH)
