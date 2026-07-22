"""@tool 装饰器 (P0-5) — 简化工具注册

标准用法:
    @tool(description="搜索互联网", category="web")
    def web_search(query: str, count: int = 10) -> str: ...

自动:
1. 从函数签名提取 JSON Schema (type hints + defaults)
2. 从 docstring 或 description 参数获取描述
3. 返回 ToolDef 对象,可直接 register()

支持的类型映射: str, int, float, bool, list[str], list[int], dict
"""

import inspect
import json
from typing import Callable, get_type_hints, get_origin, get_args
from core.tool_registry import ToolDef
from core.tool_result import ToolResult

# ── 类型 → JSON Schema ──
_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    dict: "object",
    list: "array",
}


def _type_to_schema(py_type) -> dict:
    """将 Python 类型转为 JSON Schema"""
    origin = get_origin(py_type)
    if origin is list:
        args = get_args(py_type)
        item_type = args[0] if args else str
        return {"type": "array", "items": _type_to_schema(item_type)}
    if origin is dict:
        return {"type": "object"}
    schema_type = _TYPE_MAP.get(py_type, "string")
    return {"type": schema_type}


def _build_schema(func: Callable) -> dict:
    """从函数签名构建 JSON Schema"""
    sig = inspect.signature(func)
    hints = get_type_hints(func) if hasattr(func, '__annotations__') else {}
    props = {}
    required = []

    for name, param in sig.parameters.items():
        if name == "self":
            continue
        py_type = hints.get(name, str)
        prop = _type_to_schema(py_type)

        # 描述
        prop.setdefault("description", name)

        # 默认值
        if param.default is not inspect.Parameter.empty:
            prop["default"] = param.default
        else:
            required.append(name)

    return {
        "type": "object",
        "properties": props,
        "required": required,
    }


class ToolDecorator:
    """@tool 装饰器的实现类"""

    def __init__(self, func: Callable = None, *, name: str = "", description: str = "", category: str = "general"):
        self.func = func
        self._name = name
        self._description = description
        self._category = category

    def __call__(self, func: Callable = None):
        """支持 @tool 和 @tool(name=..., desc=...) 两种形式"""
        if func is not None:
            return self._wrap(func)
        # 使用参数调用
        self.func = None
        return self

    def _wrap(self, func: Callable) -> "ToolDecorator":
        self.func = func
        # 延迟到 to_tool_def() 时才构建 schema
        if not self._name:
            self._name = func.__name__
        return self

    def to_tool_def(self) -> ToolDef:
        """生成 ToolDef 对象"""
        if self.func is None:
            raise ValueError("装饰器未绑定函数")

        name = self._name or self.func.__name__
        desc = self._description or (self.func.__doc__ or "").strip().split("\n")[0]
        params = _build_schema(self.func)
        category = self._category

        return ToolDef(
            name=name,
            description=desc,
            parameters=params,
            handler=self._make_handler(),
            category=category,
        )

    def _make_handler(self) -> Callable:
        """包装原始函数，确保返回 ToolResult"""
        func = self.func

        def handler(**kwargs):
            # 过滤掉函数不接受的 kwargs
            sig = inspect.signature(func)
            valid = {k: v for k, v in kwargs.items() if k in sig.parameters}
            try:
                result = func(**valid)
                if isinstance(result, ToolResult):
                    return result
                if isinstance(result, dict):
                    return ToolResult.success(json.dumps(result, ensure_ascii=False))
                return ToolResult.success(str(result))
            except Exception as e:
                return ToolResult.failure(str(e))

        return handler


def tool(
    func: Callable = None,
    *,
    name: str = "",
    description: str = "",
    category: str = "general",
):
    """工具注册装饰器

    支持两种形式:
        @tool
        def my_func(...): ...

        @tool(name="custom_name", description="...", category="my_cat")
        def my_func(...): ...

    使用时:
        from core.tool_decorator import tool
        reg.register(my_func.to_tool_def())
    """
    if func is not None:
        return ToolDecorator()(func)
    return ToolDecorator(name=name, description=description, category=category)


# ── 便捷方法: 从 @tool 装饰的函数批量注册 ──
def register_decorated(reg, *decorated_funcs: ToolDecorator):
    """批量注册用 @tool 装饰的函数

    Example:
        @tool(category="file")
        def file_read(path: str) -> str: ...

        @tool(category="file")
        def file_write(path: str, content: str) -> str: ...

        register_decorated(reg, file_read, file_write)
    """
    for d in decorated_funcs:
        if not isinstance(d, ToolDecorator):
            raise TypeError(f"{d} 未使用 @tool 装饰")
        reg.register(d.to_tool_def())
