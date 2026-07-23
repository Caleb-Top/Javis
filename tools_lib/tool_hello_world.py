"""Javis自创: hello_world"""
TOOL_NAME="hello_world"
TOOL_DESC="My first saved tool"
TOOL_CATEGORY="general"
TOOL_PARAMS={"type":"object","properties":{},"required":[]}

def handler(**kwargs):
    return {"success": True, "output": "Hello from hello_world!"}
