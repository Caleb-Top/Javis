"""
测试脚本 — 验证 Agent 核心流程
"""
import sys, os, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

from agent_distill.core.types import AgentConfig, Message, ToolCall, ToolDef, ToolResult, Role, MemoryEntry, MemoryType, TaskStatus
from agent_distill.core.tool_registry import ToolRegistry
from agent_distill.core.system_prompt import SystemPromptBuilder
from agent_distill.core.agent import Agent, AgentResult
from agent_distill.core.memory_manager import MemoryManager
from agent_distill.core.skill_manager import SkillManager
from agent_distill.core.artifacts import ArtifactManager, build_artifact_html


def test_types():
    """测试核心数据类型"""
    print("=== 测试: 核心数据类型 ===")

    # Message
    msg = Message(role=Role.USER, content="Hello")
    assert msg.to_api_dict()["role"] == "user"

    # ToolCall
    tc = ToolCall(id="tc1", name="bash", arguments={"command": "ls"})
    api = tc.to_api_dict()
    assert api["id"] == "tc1"
    assert api["function"]["name"] == "bash"

    # AgentConfig
    config = AgentConfig(workspace_dir="/tmp/test_agent", model="test-model")
    assert config.model == "test-model"
    assert config.memory_dir.endswith("memory")

    # MemoryEntry
    mem = MemoryEntry(
        name="test_memory",
        description="测试记忆",
        type=MemoryType.USER,
        content="用户偏好: 短句回复",
        file_path="/tmp/test.md",
    )
    assert "name: test_memory" in mem.frontmatter

    print("✓ 核心数据类型 通过")


def test_tool_registry():
    """测试工具注册表"""
    print("\n=== 测试: 工具注册表 ===")

    reg = ToolRegistry()

    # 注册工具
    def greet(name: str) -> ToolResult:
        return ToolResult(content=f"Hello, {name}!")

    reg.register(ToolDef(
        name="greet",
        description="打招呼",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        handler=greet,
        category="test",
    ))

    assert reg.tool_count == 1
    assert reg.get("greet") is not None

    # 执行
    result = reg.execute("greet", {"name": "World"})
    assert result.content == "Hello, World!"
    assert not result.is_error

    # 未知工具
    result = reg.execute("nonexistent", {})
    assert result.is_error

    # API 定义
    api_defs = reg.get_api_definitions()
    assert len(api_defs) == 1
    assert api_defs[0]["function"]["name"] == "greet"

    print("✓ 工具注册表 通过")


def test_system_prompt():
    """测试系统提示构建"""
    print("\n=== 测试: 系统提示构建 ===")

    builder = SystemPromptBuilder(username="测试用户", date_str="2026-07-21")

    prompt = builder.build()
    assert "Claude Agent" in prompt
    assert "测试用户" in prompt
    assert "2026-07-21" in prompt
    assert "产出物规范" in prompt
    assert "语调与格式" in prompt

    print(f"  系统提示长度: {len(prompt)} 字符")
    print("✓ 系统提示构建 通过")


def test_memory_manager():
    """测试 Memory 系统"""
    print("\n=== 测试: Memory 系统 ===")
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = MemoryManager(tmpdir)

        # 写入
        entry = MemoryEntry(
            name="test_user_pref",
            description="用户偏好测试",
            type=MemoryType.USER,
            content="用户喜欢短回复",
            file_path="",
        )
        assert mgr.save(entry)

        # 加载
        entries = mgr.load_all()
        assert len(entries) == 1
        loaded = entries[0]
        assert loaded.name == "test_user_pref"
        assert loaded.type == MemoryType.USER
        assert "短回复" in loaded.content

        # 检查 should_write (初始应该为 True, 因为刚初始化)
        # 写入后应该为 False (刚写过)
        assert not mgr.should_write()

        print("✓ Memory 系统 通过")


def test_skill_manager():
    """测试 Skills 管理器"""
    print("\n=== 测试: Skills 管理器 ===")
    import tempfile, os

    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建一个 Skill 目录
        skill_dir = os.path.join(tmpdir, "my-skill")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write("""---
name: my-skill
description: "Word 文档创建编辑模板 触发 word doc 报告备忘录"
---
# My Skill
这是一个测试 Skill。
""")

        mgr = SkillManager([tmpdir])
        assert "my-skill" in mgr.list_names()

        skill = mgr.get("my-skill")
        assert skill is not None
        assert "这是一个测试 Skill" in skill.instructions

        # 匹配测试 — desc 中的关键词: word, doc, 文档, 创建, 编辑, 模板, 报告, 备忘录
        # "帮我创建一个Word文档报告" 命中: word, 文档, 创建, 报告 (> 2)
        match = mgr.match("帮我创建一个Word文档报告")
        assert match is not None, f"Expected match. Skills: {mgr.list_names()}"
        assert match.name == "my-skill"

        # 无匹配 (没有 2 个关键词命中)
        match = mgr.match("今天天气怎么样")
        assert match is None

        print("✓ Skills 管理器 通过")


def test_task_system():
    """测试 Task 系统"""
    print("\n=== 测试: Task 系统 ===")

    config = AgentConfig(workspace_dir="/tmp/test")
    agent = Agent(config)

    # 创建
    t1 = agent.create_task("安装依赖", "pip install -r requirements.txt")
    t2 = agent.create_task("运行测试", "python -m pytest")
    t3 = agent.create_task("部署", "等待 t1 和 t2 完成")

    assert len(agent.list_tasks()) == 3

    # 依赖 — 使用正确的 task ID
    agent.update_task(t3.id, status=TaskStatus.PENDING, blocked_by=[t1.id, t2.id])
    print(f"  t3 blocked_by after update: {agent.get_task(t3.id).blocked_by}")
    agent.update_task(t1.id, blocks=[t3.id])
    agent.update_task(t2.id, blocks=[t3.id])

    t3_after = agent.get_task(t3.id)
    assert t3_after.blocked_by == [t1.id, t2.id]

    # 状态流转
    agent.update_task(t1.id, status=TaskStatus.IN_PROGRESS)
    agent.update_task(t2.id, status=TaskStatus.IN_PROGRESS)
    assert agent.get_ready_tasks() == []  # t3 仍被阻塞

    agent.update_task(t1.id, status=TaskStatus.COMPLETED)
    agent.update_task(t2.id, status=TaskStatus.COMPLETED)
    # t3 现在应该就绪
    ready = agent.get_ready_tasks()
    assert len(ready) == 1
    assert ready[0].id == t3.id

    print("✓ Task 系统 通过")


def test_artifact_html():
    """测试 Artifact HTML 生成"""
    print("\n=== 测试: Artifact HTML ===")

    html = build_artifact_html(
        title="测试 Artifact",
        body_html="<h1>Hello World</h1>",
        inline_js="console.log('ready');",
        use_chartjs=True,
    )

    assert "<!DOCTYPE html>" in html
    assert "chart.js" in html
    assert "Hello World" in html
    assert "console.log('ready')" in html
    assert "color-scheme: light" in html

    print("✓ Artifact HTML 通过")


def test_path_mapper():
    """测试路径映射"""
    print("\n=== 测试: 路径映射 ===")

    from agent_distill.core.types import PathMapper

    mapper = PathMapper(
        session_id="abc123",
        mounts={"D:\\Javis": "Javis"},
    )

    vm_path = mapper.win_to_vm("D:\\Javis\\main.py")
    assert "abc123" in vm_path
    assert "Javis" in vm_path

    # VM → Windows
    import platform
    win_path = mapper.vm_to_win("/sessions/abc123/mnt/Javis/main.py")
    # 在 Linux VM 中 os.path.join 用 / 分隔
    assert "Javis" in win_path and "main.py" in win_path

    print("✓ 路径映射 通过")


def test_agent_basic_flow():
    """测试 Agent 基本流程 (不调用 LLM)"""
    print("\n=== 测试: Agent 基本流程 ===")

    config = AgentConfig(workspace_dir="/tmp/test_agent")
    agent = Agent(config)

    # 注册一个测试工具
    def echo(**kwargs) -> ToolResult:
        return ToolResult(content=f"Echo: {kwargs}")

    agent.tools.register(ToolDef(
        name="echo",
        description="回显",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=echo,
    ))

    # 测试手动工具执行
    result = agent.tools.execute("echo", {"msg": "hi"})
    assert result.content == "Echo: {'msg': 'hi'}"

    # 测试 Task
    task = agent.create_task("测试任务", "描述")
    assert task.status == TaskStatus.PENDING

    # 测试 Skill 加载
    from agent_distill.core.types import SkillDef
    agent.load_skill(SkillDef(
        name="test-skill",
        description="测试技能",
        instructions="# Test",
    ))
    assert agent.get_skill("test-skill") is not None

    # 测试统计
    stats = agent.stats()
    assert stats["skills_loaded"] == 1
    assert stats["tasks"] == 1

    print("✓ Agent 基本流程 通过")


# ═══════════════════════════════════════════════
# 主测试入口
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    tests = [
        test_types,
        test_tool_registry,
        test_system_prompt,
        test_memory_manager,
        test_skill_manager,
        test_task_system,
        test_artifact_html,
        test_path_mapper,
        test_agent_basic_flow,
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            print(f"✗ {test_func.__name__} 失败: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*50}")
    print(f"结果: {passed} 通过, {failed} 失败")
    sys.exit(1 if failed > 0 else 0)
