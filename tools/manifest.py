"""工具清单 — 集中注册函数, 技能文件一行调用"""

from core.tool_registry import ToolDef


def register_desktop(reg, d):
    """注册所有桌面工具"""
    reg.register_many([
        ToolDef("screenshot","截取屏幕(已缩放1024px)",{"type":"object","properties":{},"required":[]},d.screenshot,"desktop"),
        ToolDef("mouse_click","鼠标点击坐标",{"type":"object","properties":{"x":{"type":"integer"},"y":{"type":"integer"},"button":{"type":"string","enum":["left","right"]}},"required":["x","y"]},d.mouse_click,"desktop"),
        ToolDef("mouse_drag","拖拽(start→end)",{"type":"object","properties":{"start_x":{"type":"integer"},"start_y":{"type":"integer"},"end_x":{"type":"integer"},"end_y":{"type":"integer"},"button":{"type":"string","enum":["left","right"],"default":"left"}},"required":["start_x","start_y","end_x","end_y"]},d.mouse_drag,"desktop"),
        ToolDef("mouse_scroll","鼠标滚轮",{"type":"object","properties":{"amount":{"type":"integer","default":3}},"required":[]},d.mouse_scroll,"desktop"),
        ToolDef("mouse_move","移动鼠标",{"type":"object","properties":{"x":{"type":"integer"},"y":{"type":"integer"}},"required":["x","y"]},d.mouse_move,"desktop"),
        ToolDef("mouse_double_click","鼠标双击",{"type":"object","properties":{"x":{"type":"integer"},"y":{"type":"integer"}},"required":["x","y"]},d.mouse_double_click,"desktop"),
        ToolDef("keyboard_type","键盘输入",{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]},d.keyboard_type,"desktop"),
        ToolDef("keyboard_press","快捷键,支持数组['ctrl','v']",{"type":"object","properties":{"keys":{"type":"string"},"keys_array":{"type":"array","items":{"type":"string"}}},"required":[]},d.keyboard_press,"desktop"),
        ToolDef("set_volume","设置音量0-100",{"type":"object","properties":{"level":{"type":"integer"}},"required":["level"]},d.set_volume,"desktop"),
        ToolDef("wait","等待秒数",{"type":"object","properties":{"seconds":{"type":"number","default":2}},"required":[]},d.wait,"desktop"),
    ])

def register_window(reg, d):
    """注册窗口管理工具"""
    reg.register_many([
        ToolDef("focus_window","聚焦窗口到前台",{"type":"object","properties":{"name":{"type":"string"}},"required":["name"]},d.focus_window,"desktop"),
        ToolDef("list_windows","列出可见窗口",{"type":"object","properties":{},"required":[]},d.list_windows,"desktop"),
        ToolDef("get_foreground_window","当前前台窗口",{"type":"object","properties":{},"required":[]},d.get_foreground_window,"desktop"),
        ToolDef("read_ui_window","读取窗口控件文字",{"type":"object","properties":{"title_filter":{"type":"string","default":""}},"required":[]},d.read_ui_window,"desktop"),
        ToolDef("get_window_state","截图+UI树+边界",{"type":"object","properties":{"window_id":{"type":"string","default":""}},"required":[]},d.get_window_state,"desktop"),
        ToolDef("click_element","按控件索引点击",{"type":"object","properties":{"element_index":{"type":"integer"},"title_filter":{"type":"string","default":""}},"required":["element_index"]},d.click_element,"desktop"),
    ])

def register_system(reg, s):
    reg.register_many([
        ToolDef("system_info","系统状态CPU/内存/磁盘",{"type":"object","properties":{},"required":[]},s.system_info,"system"),
        ToolDef("system_execute","执行系统命令",{"type":"object","properties":{"command":{"type":"string"}},"required":["command"]},s.system_execute,"system"),
        ToolDef("open_file","打开文件/文件夹",{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]},s.open_file,"system"),
        ToolDef("open_app","搜索并打开应用",{"type":"object","properties":{"name":{"type":"string"}},"required":["name"]},s.open_app,"system"),
        ToolDef("launch_app","打开应用(别名)",{"type":"object","properties":{"name":{"type":"string"}},"required":["name"]},s.open_app,"system"),
        ToolDef("find_app","搜索已安装应用",{"type":"object","properties":{"name":{"type":"string"}},"required":["name"]},s.find_app,"system"),
        ToolDef("brain_status","检视自身大脑: 知识库/记忆/经验状态、分类分布、高优先级知识",{"type":"object","properties":{},"required":[]},s.brain_status,"system"),
        ToolDef("memory_status","检视四层记忆系统: 工作/情景/语义/程序记忆",{"type":"object","properties":{},"required":[]},s.memory_status,"system"),
        ToolDef("github_search","搜索 GitHub 仓库（带 token 认证，限额 5000/时）",{"type":"object","properties":{"query":{"type":"string","description":"搜索关键词"},"sort":{"type":"string","enum":["stars","forks","updated"],"default":"stars"},"per_page":{"type":"integer","default":10}},"required":["query"]},s.github_search,"system"),
    ])

def register_file(reg, f):
    reg.register_many([
        ToolDef("file_read","读取文件",{"type":"object","properties":{"path":{"type":"string"},"offset":{"type":"integer"},"limit":{"type":"integer"}},"required":["path"]},f.file_read,"file"),
        ToolDef("file_write","写入文件",{"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"}},"required":["path","content"]},f.file_write,"file"),
        ToolDef("file_list","列出目录",{"type":"object","properties":{"directory":{"type":"string","default":"."}},"required":[]},f.file_list,"file"),
    ])

def register_camera(reg, c):
    reg.register_many([
        ToolDef("camera_snapshot","摄像头拍照",{"type":"object","properties":{},"required":[]},c.camera_snapshot,"camera"),
        ToolDef("camera_list","列出可用摄像头",{"type":"object","properties":{},"required":[]},c.camera_list,"camera"),
    ])

def register_code_exec(reg, ce):
    reg.register(ToolDef("run_code","★执行代码,AI自己编程控制电脑",{"type":"object","properties":{"code":{"type":"string"},"language":{"type":"string","enum":["python","powershell","cmd"],"default":"python"}},"required":["code"]},ce.run_code,"system"))


def register_workspace(reg, w):
    """注册工作区自我管理工具"""
    reg.register_many([
        ToolDef("create_workspace_file","在工作区创建文件并自动注册到清单",
                {"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"},"purpose":{"type":"string","default":""},"category":{"type":"string","enum":["temp","project","thought"],"default":"thought"}},"required":["path","content"]},
                w.create_workspace_file,"workspace"),
        ToolDef("create_temp_file","创建临时文件(自动命名放入workspace/temp/),任务结束会提醒清理",
                {"type":"object","properties":{"content":{"type":"string"},"purpose":{"type":"string","default":""}},"required":["content"]},
                w.create_temp_file,"workspace"),
        ToolDef("list_workspace","列出工作区中所有由AI创建的文件",
                {"type":"object","properties":{},"required":[]},
                w.list_workspace,"workspace"),
        ToolDef("cleanup_temp","清理临时文件。需要用户确认(confirmed=true)后执行",
                {"type":"object","properties":{"confirmed":{"type":"boolean","default":False}},"required":[]},
                w.cleanup_temp,"workspace"),
        ToolDef("organize_workspace","自动整理工作区文件到正确的子目录结构",
                {"type":"object","properties":{},"required":[]},
                w.organize_workspace,"workspace"),
        ToolDef("reflect_on_workspace","分析工作区状态,给出整理建议和统计信息",
                {"type":"object","properties":{},"required":[]},
                w.reflect_on_workspace,"workspace"),
        ToolDef("file_delete","删除文件或目录(高风险,需要用户确认)",
                {"type":"object","properties":{"path":{"type":"string"}},"required":["path"]},
                w.delete_file_handler,"file"),
    ])


def register_agent_tools(reg):
    """注册 Agent 内部工具（end_turn 等永久工具）"""
    from core.tool_result import ToolResult
    def _end_turn(**kwargs):
        return ToolResult.success("end_turn")
    reg.register(ToolDef(
        "end_turn",
        "主动结束当前任务回合。当你认为任务已经完成、用户意图已满足，或需要等待用户进一步指示时，调用此工具结束推理。",
        {"type": "object", "properties": {}, "required": []},
        _end_turn,
        "agent",
    ))
