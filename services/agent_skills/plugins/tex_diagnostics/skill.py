# -*- coding: utf-8 -*-
"""
插件技能：LaTeX 编译日志诊断 (latex-compile-diagnostics)

来源与许可：本项目原创实现（MIT）。日志类别划分参考 TeX Live 内置 texlogsieve
（https://ctan.org/pkg/texlogsieve，GPLv3，仅作外部进程调用，未复制其源码）与
docs/open_source_skills_research.md 的实测结论。

能力：
1. 激活后向模型注入「如何读真实 XeLaTeX 日志」的指引；
2. 提供 diagnose(log) 便捷方法（内部调用 utils.tex_log_tools，不存在时优雅降级）；
3. 不修改代码（代码修复由 tikz-error-doctor / diagram_validate 负责）。
"""

from typing import Any, Dict, List, Tuple

from services.agent_skills.base_skill import BaseDiagramSkill


class TexDiagnosticsSkill(BaseDiagramSkill):
    @property
    def skill_id(self) -> str:
        return "latex-compile-diagnostics"

    @property
    def name(self) -> str:
        return "LaTeX 编译日志诊断专家"

    @property
    def description(self) -> str:
        return "解析真实 XeLaTeX 日志，输出文件:行号 + 错误类别 + 中文修复建议，驱动 Agent 精准自愈。"

    @property
    def tags(self) -> List[str]:
        return ["diagnostics", "compiler", "tex-log", "plugin"]

    def match_intent(self, user_instruction: str, question_context: str) -> Tuple[bool, float]:
        kw = ["报错", "编译失败", "无法编译", "编译错误", "日志", "诊断", "红线",
              "显示不出来", "报红", "error", "emergency"]
        if any(k in (user_instruction or "") for k in kw):
            return True, 0.86
        return False, 0.0

    def get_system_instructions(self) -> str:
        return r"""【LaTeX 编译日志诊断技能（插件）】:
1. 编译失败时，先看真实日志而不是猜测：优先定位 `文件名:行号: 错误信息`（-file-line-error 格式）
   与 `l.NNN` 所在行；取**第一条** `!` 开头的致命错误，后面往往是连锁反应。
2. 常见致命错误 → 修复动作：
   - `Missing $ inserted` → node 文本中的 ^ _ \\circ 等数学符号没包 $...$；
   - `Package tikz Error: Giving up on this path. Did you forget a semi colon?` → 该行 \draw/\node 语句漏分号；
   - `No shape named 'A' is known` → 具名坐标 (A) 使用前未定义；
   - `Undefined control sequence` → 命令拼写错误，或用了模板未加载的宏包命令；
   - `Package pgfkeys Error: I do not know the key '/tikz/...'` → 未定义 style/选项；
   - `Runaway argument?` → 花括号、$ 或语句结束符不配对；
   - `Dimension too large` → 坐标量级过大，缩放到 ±10 内；
   - `Environment ... undefined` → 使用了未加载的图形环境（如 circuitikz）。
3. texlogsieve 只用于**外部进程**过滤/摘要；其好坏日志退出码均为 0，必须看摘要文本，
   不得用退出码判断编译成败。
4. 修复后必须重新编译并复验；同一错误连续出现两次时，优先回到上一版可用代码做最小改动。"""

    def pre_process(self, user_instruction: str, current_tikz: str) -> Dict[str, Any]:
        actions: List[str] = []
        instruction = user_instruction or ""
        if any(k in instruction for k in ("报错", "编译", "日志", "诊断", "红线", "显示不出来")):
            actions.append("优先阅读 XeLaTeX 原始日志：定位第一条致命错误的文件:行号与错误类别，再针对性修复")
        return {"detected_actions": actions}

    def post_process_tikz(self, tikz_code: str) -> Tuple[str, List[str]]:
        # 代码修复由 tikz-error-doctor 与 diagram_validate.autofix 统一负责
        return tikz_code, []

    @staticmethod
    def diagnose(error_log: str) -> str:
        if not error_log:
            return "编译失败，但没有拿到日志；请检查 xelatex 是否可用与临时目录权限。"
        try:
            from utils.tex_log_tools import diagnose_tex_error
            return diagnose_tex_error(error_log)
        except Exception:
            return f"编译器反馈：{str(error_log)[:400]}"
