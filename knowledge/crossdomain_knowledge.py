"""跨领域基础知识注入 — Windows系统管理 · Git工作流 · 数据格式 · 网络HTTP · 基础安全

本模块包含 JARVIS 作为 Windows AI 助手应具备的"常识"知识。
通过 brain.learn_fact() 注入到大脑系统，每个知识点带分类、来源和优先级。
"""

import logging
logger = logging.getLogger("crossdomain")

FACTS = {
    "win_admin": [
        # Windows 服务管理
        ("Windows服务通过services.msc管理后台服务, sc命令可创建/删除/查询服务, net start/stop控制启停", "knowledge.win_admin.service"),
        # Windows 注册表
        ("Windows注册表用regedit或reg命令操作, HKLM存机器配置, HKCU存用户配置, 修改前必须导备份(.reg)", "knowledge.win_admin.registry"),
        # 任务计划
        ("Windows任务计划用schtasks创建定时任务, 支持触发器(时间/事件)/操作(程序/脚本)/条件(空闲/电源)", "knowledge.win_admin.scheduler"),
        # 防火墙
        ("Windows防火墙用netsh advfirewall管理规则, 分入站/出站规则, 支持域/专用/公用三种配置文件", "knowledge.win_admin.firewall"),
        # 事件查看器
        ("Windows事件查看器用eventvwr或wevtutil, 日志分系统/应用/安全三类, 事件级别: 信息/警告/错误/关键", "knowledge.win_admin.eventlog"),
        # 进程管理
        ("Windows进程管理: tasklist查看进程, taskkill /F强制终止, wmic process可查询进程详情和命令行参数", "knowledge.win_admin.process"),
        # 磁盘管理
        ("Windows磁盘管理: diskpart分区工具, chkdsk磁盘检查, fsutil查询文件系统信息, defrag整理碎片(HDD)", "knowledge.win_admin.disk"),
        # 网络诊断
        ("Windows网络诊断: ipconfig查看IP, ping测试连通, tracert路由跟踪, netstat查看端口连接, nslookup DNS查询", "knowledge.win_admin.network"),
    ],
    "git_workflow": [
        # 基本工作流
        ("Git基本工作流: add将修改加入暂存区, commit提交到本地库, push推送到远程, pull拉取并合并, fetch仅拉取不合并", "knowledge.git.basics"),
        # 分支策略
        ("Git分支策略: feature分支开发新功能, develop集成分支做集成测试, main/master发布分支保持稳定, hotfix紧急修复", "knowledge.git.branching"),
        # merge vs rebase
        ("Git合并: merge保留历史拓扑结构(会产生合并提交), rebase变基使历史线性化(改写提交哈希), 公共分支用merge, 私有分支可rebase", "knowledge.git.merge_rebase"),
        # 冲突解决
        ("Git冲突解决: diff查看冲突内容, mergetool图形化合并工具, 手动编辑冲突标记后add标记已解决, rebase --skip跳过当前提交", "knowledge.git.conflict"),
        # stash
        ("Git stash: git stash暂存未完成修改, stash list列出所有暂存, stash pop恢复并删除, stash drop删除不恢复, stash apply恢复但不删除", "knowledge.git.stash"),
        # reset vs revert
        ("Git reset/revert: reset移动HEAD指针(危险, 可能丢失修改, --hard强制丢弃), revert创建反提交撤销更改(安全, 适合公共分支)", "knowledge.git.reset_revert"),
        # cherry-pick
        ("Git cherry-pick: 将指定提交的更改应用到当前分支, 适合选择性移植修复, cherry-pick --continue解决冲突后继续", "knowledge.git.cherry_pick"),
        # submodule
        ("Git submodule: 在仓库中嵌入其他仓库, clone需加--recursive, submodule update拉取子模块更新, 适合依赖管理", "knowledge.git.submodule"),
    ],
    "data_formats": [
        # JSON
        ("JSON是轻量数据交换格式, 支持嵌套对象/数组/基本类型, 严格双引号, 适合API通信和配置文件", "knowledge.data.json"),
        # CSV
        ("CSV是表格数据通用格式, 无类型信息, 首行通常为表头, 编码(UTF-8 BOM)/引号转义/行内换行是常见问题点", "knowledge.data.csv"),
        # XML
        ("XML是带元数据的标记语言, XPath用于查询节点, XSLT用于转换, Schema/XSD定义结构约束, 适合配置和文档", "knowledge.data.xml"),
        # YAML
        ("YAML是人类可读的序列化格式, 缩进敏感(空格不允许Tab), 支持锚点&和别名*复用, 适合conda env/docker-compose/GitHub Actions配置", "knowledge.data.yaml"),
        # Markdown
        ("Markdown是轻量标记语言, GFM扩展了表格/任务列表/删除线/围栏代码块, README和文档的标准格式", "knowledge.data.markdown"),
        # LaTeX
        ("LaTeX是学术排版标准, $...$行内公式/$$...$$独立公式, \\cite引用文献, \\label/\\ref交叉引用, \\begin{document}文档体", "knowledge.data.latex"),
        # TOML
        ("TOML是人类可读的配置文件格式, 用[section]分组, 支持日期类型, 比YAML简单, Cargo.toml/pyproject.toml使用", "knowledge.data.toml"),
        # Protocol Buffers
        ("Protocol Buffers(protobuf)是Google的高效序列化格式, .proto定义schema, 二进制编码体积小, 适合高性能RPC通信", "knowledge.data.protobuf"),
    ],
    "network_http": [
        # HTTP状态码
        ("HTTP状态码分类: 2xx成功, 3xx重定向, 4xx客户端错误, 5xx服务端错误; 常见: 200 OK, 301永久重定向, 302临时重定向, 401未认证, 403禁止, 404未找到, 500内部错误", "knowledge.network.status_codes"),
        # HTTP方法
        ("HTTP方法: GET查询资源(幂等), POST创建资源(非幂等), PUT全量替换(幂等), PATCH部分更新, DELETE删除资源", "knowledge.network.methods"),
        # DNS
        ("DNS解析: A记录域名→IPv4, AAAA域名→IPv6, CNAME域名别名, MX邮件交换, TXT文本记录; TTL控制缓存时间", "knowledge.network.dns"),
        # HTTPS
        ("HTTPS: TLS加密传输层, 证书链验证(CA根→中间→服务器), 防范中间人攻击, HTTP/2多路复用/头部压缩", "knowledge.network.https"),
        # RESTful
        ("RESTful API: 资源URL路径设计(/users/{id}/orders), 无状态通信(每次请求携带所有信息), HATEOAS超媒体驱动响应包含后续操作链接", "knowledge.network.rest"),
        # API认证
        ("API认证方式: API Key简单直接(适合服务间), Bearer Token标准方案(JWT自包含), OAuth2授权码流程(第三方授权, 分授权码/令牌/刷新三步)", "knowledge.network.auth"),
        # WebSocket
        ("WebSocket: 全双工通信协议, ws://非加密/wss://加密, 通过HTTP Upgrade握手建立, 适合实时推送/聊天/协作编辑", "knowledge.network.websocket"),
        # CORS
        ("CORS跨域资源共享: 浏览器安全策略, Origin请求头/Access-Control-Allow-Origin响应头, 预检OPTIONS请求处理复杂跨域", "knowledge.network.cors"),
    ],
    "security": [
        # 沙箱隔离
        ("JARVIS沙箱隔离: sandbox_check_path保护系统目录免受误写入, 关键目录(Windows/Program Files/等)禁止修改", "knowledge.security.sandbox"),
        # 权限最小化
        ("权限最小化原则: 只授予完成任务所需的最小权限, 用完后立即回收, 定期审计权限清单", "knowledge.security.min_privilege"),
        # 数据脱敏
        ("数据脱敏: API Key/Token/密码/PII等敏感信息不能写入日志, 传输必须用HTTPS, 显示时用***掩盖", "knowledge.security.sanitize"),
        # 输入验证
        ("输入验证: 路径遍历(../)必须拒绝, 命令注入用参数化API而非字符串拼接, 文件上传限制类型和大小", "knowledge.security.input_validate"),
        # 速率限制
        ("API速率限制: GitHub 60 req/h未认证 vs 5000 req/h带token, OpenAI按tier/模型限速, 429 Too Many Requests需退避重试", "knowledge.security.rate_limit"),
        # SQL注入
        ("SQL注入防范: 永远使用参数化查询(?)或ORM, 永远不要拼接SQL字符串, 存储过程可增加一层安全", "knowledge.security.sql_injection"),
        # XSS
        ("XSS跨站脚本: 反射型(URL参数注入)/存储型(数据库持久化)/DOM型(客户端); 防范: 输出HTML转义, CSP内容安全策略, HttpOnly Cookie", "knowledge.security.xss"),
        # CSRF
        ("CSRF跨站请求伪造: 攻击者诱导用户执行非预期操作; 防范: CSRF Token, SameSite Cookie属性(Strict/Lax), Referer/Origin验证", "knowledge.security.csrf"),
    ],
}


def inject_to_brain(brain):
    """将所有跨领域基础知识注入大脑"""
    total = 0
    category_counts = {}
    for category_key, facts in FACTS.items():
        cat_name = {
            "win_admin": "Windows System Administration",
            "git_workflow": "Git Workflow",
            "data_formats": "Data Formats",
            "network_http": "Network & HTTP",
            "security": "Basic Security",
        }.get(category_key, category_key)

        count = 0
        for content, category in facts:
            brain.learn_fact(
                content,
                category=category,
                source="knowledge_base",
                priority=2,
            )
            count += 1
        category_counts[cat_name] = count
        total += count

    # Print counts per category
    print("=" * 60)
    print("Cross-domain knowledge injection complete")
    print("=" * 60)
    for cat, cnt in category_counts.items():
        print(f"  {cat}: {cnt} facts")
    print(f"  ─────────────────────────")
    print(f"  TOTAL: {total} facts")
    print("=" * 60)

    logger.info(f"跨领域知识注入完成: {total} 条, 分类: {list(category_counts.keys())}")
    return total


if __name__ == "__main__":
    # Standalone mode: create brain and inject
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from knowledge.brain import Brain
    b = Brain()
    inject_to_brain(b)
    b._flush()
    print("Brain flushed to disk.")
