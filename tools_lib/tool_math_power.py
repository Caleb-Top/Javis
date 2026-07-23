"""Javis自创: math_power"""
TOOL_NAME="math_power"
TOOL_DESC="Quick math: power function. Params: base (default 2), exp (default 10)"
TOOL_CATEGORY="math"
TOOL_PARAMS={"type":"object","properties":{"base":{"type":"number","default":2},"exp":{"type":"number","default":10}},"required":[]}

def handler(**kwargs):
    base = kwargs.get("base", 2)
    exp = kwargs.get("exp", 10)
    return {"success": True, "output": str(base ** exp)}
