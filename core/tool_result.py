"""工具返回结果类型"""

import json
from dataclasses import dataclass


@dataclass
class ToolResult:
    success: bool
    data: str = ""
    error: str = ""
    screenshot: str = ""
    image: str = ""

    @staticmethod
    def success(data: str = "") -> "ToolResult":
        return ToolResult(success=True, data=str(data))

    @staticmethod
    def failure(error: str) -> "ToolResult":
        return ToolResult(success=False, error=error)

    def to_json(self) -> str:
        return json.dumps({
            "success": self.success,
            "data": self.data[:2000],            # 限制长度
            "error": self.error,
        }, ensure_ascii=False)
