import os
from dotenv import load_dotenv

# ================= 配置与常量 =================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Only read this application's configuration, never a parent directory's .env.
load_dotenv(os.path.join(BASE_DIR, ".env"))
CHAPTERS_DIR = os.path.join(BASE_DIR, "chapters")
CSV_INDEX_PATH = os.path.join(BASE_DIR, "utils", "题库索引表.csv")

# AI 配置
AI_API_KEY = os.getenv("AI_API_KEY")
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://api.openai.com/v1")
AI_MODEL_NAME = os.getenv("AI_MODEL_NAME", "gpt-4o")

# 读取 OCR 提示词 (优先从文件读取，否则从环境变量)
ocr_prompt_file = os.path.join(BASE_DIR, "ocr_prompt.txt")
if os.path.exists(ocr_prompt_file):
    with open(ocr_prompt_file, "r", encoding="utf-8") as f:
        AI_OCR_PROMPT = f.read()
else:
    # 处理 .env 中的换行符转义
    AI_OCR_PROMPT = os.getenv(
        "AI_OCR_PROMPT",
        "请识别图片中的高中物理题，并严格按照 LaTeX 格式输出；多道题必须分别输出完整结构，禁止合并。",
    ).replace('\\n', '\n')


SUBJECTS = [
    "运动的描述", "匀变速直线运动", "平衡力学", "牛顿运动定律", "曲线运动",
    "万有引力与航天", "功与能量", "动量", "电场", "电路", "磁场运动", "电磁感应",
    "交流电", "机械振动与机械波", "光学", "热学", "近代物理", "电学实验", "力学试验", "选修实验", "未分类"
]

PAPER_TYPES = {"G": "高考题", "M": "模拟题", "W": "外国题", "XK": "学考题", "XS": "线上联考", "QJ": "强基计划题", "JS": "竞赛题", "WK": "挖空题"}
