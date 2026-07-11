"""错误翻译模块"""
ERROR_MAP = {
    "FileNotFoundError": "文件不存在", "PermissionError": "权限不足",
    "ConnectionError": "网络连接失败", "TimeoutError": "超时",
    "ImportError": "模块未安装", "ModuleNotFoundError": "模块未安装",
    "ValueError": "参数无效", "TypeError": "参数类型错误",
    "KeyError": "配置缺失", "IndexError": "索引超出",
    "json.JSONDecodeError": "JSON解析失败", "yaml.YAMLError": "配置文件格式错误",
    "UnicodeDecodeError": "编码格式错误", "MemoryError": "内存不足",
    "FailSafeException": "鼠标安全模式触发",
    "subprocess.TimeoutExpired": "命令执行超时",
    "generic": "操作失败",
}
def friendly_error(exception):
    exc_type = type(exception).__name__
    return ERROR_MAP.get(exc_type) or ERROR_MAP.get(f"{type(exception).__module__}.{exc_type}") or ERROR_MAP["generic"]

def translate_file_error(path: str, exception: Exception) -> str:
    base = friendly_error(exception)
    if "不存在" in base:
        return f"{base}: {path}"
    return base
