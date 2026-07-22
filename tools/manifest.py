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


def register_search_tools(reg):
    """注册搜索三件套 (P0-2) — grep/glob_search/file_edit"""
    from tools.search import grep, glob_search, file_edit
    reg.register_many([
        ToolDef("grep","内容搜索: 正则匹配文件内容,支持上下文/多行/文件名过滤",{"type":"object","properties":{"pattern":{"type":"string"},"path":{"type":"string","default":"."},"glob":{"type":"string","default":""},"output_mode":{"type":"string","enum":["content","files_with_matches","count"],"default":"content"},"head_limit":{"type":"integer","default":50},"case_insensitive":{"type":"boolean","default":False},"context":{"type":"integer","default":0},"multiline":{"type":"boolean","default":False}},"required":["pattern"]},grep,"search"),
        ToolDef("glob_search","文件名搜索: 支持通配符(**/*.py, src/*.ts等)",{"type":"object","properties":{"pattern":{"type":"string"},"path":{"type":"string","default":"."},"max_results":{"type":"integer","default":100}},"required":["pattern"]},glob_search,"search"),
        ToolDef("file_edit","精确字符串替换编辑: 查找old_string替换为new_string",{"type":"object","properties":{"file_path":{"type":"string"},"old_string":{"type":"string"},"new_string":{"type":"string"},"replace_all":{"type":"boolean","default":False}},"required":["file_path","old_string","new_string"]},file_edit,"search"),
    ])

def register_git_tools(reg):
    """注册 Git 工具集 (P0-1) — 9 个函数"""
    from tools.git_tools import (
        git_status, git_log, git_diff, git_push, git_pull,
        git_save, git_clone, git_branch, git_init,
    )
    reg.register_many([
        ToolDef("git_status","查看仓库状态: 分支、分类文件、远程",{"type":"object","properties":{"repo_path":{"type":"string","default":"."}},"required":[]},git_status,"git"),
        ToolDef("git_log","查看最近N条提交记录",{"type":"object","properties":{"repo_path":{"type":"string","default":"."},"count":{"type":"integer","default":10}},"required":[]},git_log,"git"),
        ToolDef("git_diff","查看差异，超3000字符自动切--stat",{"type":"object","properties":{"repo_path":{"type":"string","default":"."},"staged":{"type":"boolean","default":False}},"required":[]},git_diff,"git"),
        ToolDef("git_push","推送: 前置检查+推送+友好错误翻译",{"type":"object","properties":{"repo_path":{"type":"string","default":"."},"remote":{"type":"string","default":"origin"},"branch":{"type":"string","default":""},"force":{"type":"boolean","default":False}},"required":[]},git_push,"git"),
        ToolDef("git_pull","拉取远程更新",{"type":"object","properties":{"repo_path":{"type":"string","default":"."},"remote":{"type":"string","default":"origin"},"branch":{"type":"string","default":""},"rebase":{"type":"boolean","default":False}},"required":[]},git_pull,"git"),
        ToolDef("git_save","保存变更: add+commit原子操作",{"type":"object","properties":{"repo_path":{"type":"string","default":"."},"message":{"type":"string","default":""},"files":{"type":"array","items":{"type":"string"}},"add_all":{"type":"boolean","default":False}},"required":[]},git_save,"git"),
        ToolDef("git_clone","克隆仓库",{"type":"object","properties":{"url":{"type":"string"},"target_dir":{"type":"string","default":""},"shallow":{"type":"boolean","default":False}},"required":["url"]},git_clone,"git"),
        ToolDef("git_branch","分支管理: list/create/switch/merge",{"type":"object","properties":{"repo_path":{"type":"string","default":"."},"action":{"type":"string","enum":["list","create","switch","merge"],"default":"list"},"name":{"type":"string","default":""}},"required":[]},git_branch,"git"),
        ToolDef("git_init","初始化仓库+远程关联",{"type":"object","properties":{"repo_path":{"type":"string","default":"."},"remote_url":{"type":"string","default":""},"branch":{"type":"string","default":"main"}},"required":[]},git_init,"git"),
    ])


def register_search_tools(reg):
    """注册搜索工具集 (P0-2) — grep / glob_find / file_edit"""
    from tools.search import grep, glob_find, file_edit
    reg.register_many([
        ToolDef("grep","在文件中搜索正则表达式（基于 ripgrep）。支持 output_mode: content/files_with_matches/count",{"type":"object","properties":{"pattern":{"type":"string","description":"正则表达式模式"},"path":{"type":"string","default":"."},"glob":{"type":"string","default":"","description":"文件名 glob 过滤"},"output_mode":{"type":"string","enum":["content","files_with_matches","count"],"default":"content"},"max_count":{"type":"integer","default":200},"context":{"type":"integer","default":0},"ignore_case":{"type":"boolean","default":False},"multiline":{"type":"boolean","default":False},"include_hidden":{"type":"boolean","default":False}},"required":["pattern"]},grep,"search"),
        ToolDef("glob","按 glob 模式查找文件（如 **/*.py, src/**/*.tsx）",{"type":"object","properties":{"pattern":{"type":"string","description":"glob 模式"},"path":{"type":"string","default":"."},"max_results":{"type":"integer","default":100},"include_hidden":{"type":"boolean","default":False}},"required":["pattern"]},glob_find,"search"),
        ToolDef("file_edit","精确字符串替换编辑文件（与 Claude Code Edit 工具行为一致）",{"type":"object","properties":{"file_path":{"type":"string","description":"文件绝对路径"},"old_string":{"type":"string","description":"要替换的字符串（必须完全匹配，包括缩进）"},"new_string":{"type":"string","description":"替换后的字符串"},"replace_all":{"type":"boolean","default":False}},"required":["file_path","old_string","new_string"]},file_edit,"search"),
    ])


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

def register_task_tools(reg):
    """注册任务管理工具集 (P0-3) — TaskCreate / TaskGet / TaskList / TaskUpdate / TaskStop"""
    from tools.task_tools import task_create, task_get, task_list, task_update, task_stop
    reg.register_many([
        ToolDef("task_create","创建结构化任务，支持依赖关系。状态: pending→in_progress→completed",{"type":"object","properties":{"subject":{"type":"string","description":"任务标题"},"description":{"type":"string","default":"","description":"任务详情"},"active_form":{"type":"string","default":"","description":"执行中的显示文案"},"status":{"type":"string","enum":["pending","in_progress","completed"],"default":"pending"},"blocks":{"type":"array","items":{"type":"string"},"default":[],"description":"被此任务阻塞的其他任务ID"},"blocked_by":{"type":"array","items":{"type":"string"},"default":[],"description":"阻塞此任务的前置任务ID"},"owner":{"type":"string","default":"","description":"任务负责人"},"metadata":{"type":"object","default":{},"description":"自定义元数据"}},"required":["subject"]},task_create,"task"),
        ToolDef("task_get","根据ID获取任务详情（含依赖和元数据）",{"type":"object","properties":{"task_id":{"type":"string","description":"任务ID"}},"required":["task_id"]},task_get,"task"),
        ToolDef("task_list","列出所有非删除状态的任务，按ID排序。支持按status/owner过滤",{"type":"object","properties":{"status":{"type":"string","enum":["pending","in_progress","completed"],"default":""},"owner":{"type":"string","default":""}},"required":[]},task_list,"task"),
        ToolDef("task_update","更新任务: 状态流转/修改字段/管理依赖(增删blocks和blockedBy)",{"type":"object","properties":{"task_id":{"type":"string","description":"任务ID"},"status":{"type":"string","enum":["pending","in_progress","completed","deleted"],"default":""},"subject":{"type":"string","default":""},"description":{"type":"string","default":""},"active_form":{"type":"string","default":""},"owner":{"type":"string","default":""},"metadata":{"type":"object","default":{}},"add_blocks":{"type":"array","items":{"type":"string"},"default":[]},"add_blocked_by":{"type":"array","items":{"type":"string"},"default":[]},"remove_blocks":{"type":"array","items":{"type":"string"},"default":[]},"remove_blocked_by":{"type":"array","items":{"type":"string"},"default":[]}},"required":["task_id"]},task_update,"task"),
        ToolDef("task_stop","停止/取消任务 — 标记为deleted并清理所有依赖引用",{"type":"object","properties":{"task_id":{"type":"string","description":"任务ID"}},"required":["task_id"]},task_stop,"task"),
    ])

def register_web_tools(reg):
    """注册 Web 搜索工具集 (P0-4) — web_search / web_fetch"""
    from tools.web_search import web_search, web_fetch
    reg.register_many([
        ToolDef("web_search","搜索互联网 (DuckDuckGo)，返回标题+URL+摘要",{"type":"object","properties":{"query":{"type":"string","description":"搜索关键词"},"count":{"type":"integer","default":10,"description":"返回结果数量"},"allowed_domains":{"type":"array","items":{"type":"string"},"default":[],"description":"只保留这些域名"},"blocked_domains":{"type":"array","items":{"type":"string"},"default":[],"description":"排除这些域名"}},"required":["query"]},web_search,"web"),
        ToolDef("web_fetch","抓取指定URL的网页内容，解析为纯文本返回",{"type":"object","properties":{"url":{"type":"string","description":"网页URL (http/https)"},"max_chars":{"type":"integer","default":8000,"description":"最大返回字符数"},"timeout":{"type":"integer","default":15,"description":"请求超时(秒)"},"raw_html":{"type":"boolean","default":False,"description":"返回原始HTML"}},"required":["url"]},web_fetch,"web"),
    ])
