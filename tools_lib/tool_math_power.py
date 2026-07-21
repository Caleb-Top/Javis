"""Javis自创: math_power"""
TOOL_NAME="math_power"
TOOL_DESC="Quick math: power function"
TOOL_CATEGORY="math"
TOOL_PARAMS={"type":"object","properties":{},"required":[]}

def handler(**kwargs):
    def handler(**kwargs):
        import math
        base = kwargs.get("base", 2)
        exp = kwargs.get("exp", 10)
        return {"success": True, "output": str(base ** exp)}
