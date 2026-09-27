class CompileError(Exception):
    """编译期错误：携带行列位置，便于定位配置问题。"""

    def __init__(self, message, line=None, col=None):
        self.message = message
        self.line = line
        self.col = col
        location = ""
        if line is not None:
            location = f" (line {line}"
            if col is not None:
                location += f", col {col}"
            location += ")"
        super().__init__(f"{message}{location}")


class PlanInputError(Exception):
    """执行计划时输入参数缺失或类型不符。"""
