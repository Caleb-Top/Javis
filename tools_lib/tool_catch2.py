# -*- coding: utf-8 -*-
"""Catch2 单元测试 — 轻量集成 JARVIS 工作流"""
import sys, os, json, subprocess, textwrap, logging
from pathlib import Path

TOOLS_DIR = 'D:/Javis/tools/catch2'
CATCH_HPP = os.path.join(TOOLS_DIR, 'catch.hpp')
_BRAIN = None

def set_brain(brain):
    global _BRAIN
    _BRAIN = brain

# ── C++ 测试模板 ──

TEST_TEMPLATES = {
    "basic": '''#define CATCH_CONFIG_MAIN
#include "catch.hpp"

TEST_CASE("快速测试", "[basic]") {
    REQUIRE(1 + 1 == 2);
    CHECK(2 * 2 == 4);
    REQUIRE_FALSE(1 > 2);
}

TEST_CASE("字符串测试", "[string]") {
    std::string s = "Hello Catch2";
    REQUIRE(s.size() == 12);
    CHECK(s.find("Catch") != std::string::npos);
}
''',

    "tdd": '''#define CATCH_CONFIG_MAIN
#include "catch.hpp"

// ── 被测函数 ──
int factorial(int n) {
    return n <= 1 ? 1 : n * factorial(n - 1);
}

// ── 测试用例 (TDD: 先写测试, 再实现) ──
TEST_CASE("阶乘计算", "[math][factorial]") {
    SECTION("边界值") {
        REQUIRE(factorial(0) == 1);
        REQUIRE(factorial(1) == 1);
    }
    SECTION("正常值") {
        REQUIRE(factorial(5) == 120);
        REQUIRE(factorial(10) == 3628800);
    }
}

TEST_CASE("性能标杆: 阶乘", "[!benchmark]") {
    BENCHMARK("factorial(15)") {
        return factorial(15);
    };
}
''',

    "bdd": '''#define CATCH_CONFIG_MAIN
#include "catch.hpp"
#include <vector>
#include <algorithm>

TEST_CASE("BDD 风格: vector 操作", "[vector][bdd]") {
    std::vector<int> v;

    GIVEN("一个空 vector") {
        REQUIRE(v.empty());

        WHEN("插入 3 个元素") {
            v.push_back(42);
            v.push_back(17);
            v.push_back(99);

            THEN("size 变为 3") {
                REQUIRE(v.size() == 3);
            }

            THEN("元素能被找到") {
                REQUIRE(std::find(v.begin(), v.end(), 42) != v.end());
            }
        }
    }
}
''',
}

# ── 生成测试文件并编译运行 ──

def run_catch2_test(template: str = "basic", test_name: str = "", extra_code: str = "",
                     extra_includes: str = "") -> str:
    """生成 Catch2 测试 → 编译 → 运行 (一键)"""
    source = TEST_TEMPLATES.get(template)
    if not source:
        return f"未知模板: {template}, 可选: {', '.join(TEST_TEMPLATES.keys())}"
    if extra_code:
        source = source.replace("// ── 被测函数 ──", extra_code + "\n// ── 被测函数 ──")

    # 写入临时文件
    tag = f"catch2_{template}_{abs(hash(test_name or template)) % 10000}"
    src_path = f"D:/Javis/workspace/temp/{tag}.cpp"
    exe_path = f"D:/Javis/workspace/temp/{tag}.exe"
    Path("D:/Javis/workspace/temp").mkdir(parents=True, exist_ok=True)

    with open(src_path, "w", encoding="utf-8") as f:
        f.write(source)

    # 编译 (链接 catch2 头文件路径)
    mingw_gxx = "D:/Javis/tools/mingw32/bin/g++.exe"
    flags = f"-std=c++17 -O1 -I{D:/Javis/tools/catch2} -static"
    cr = subprocess.run(
        f'"{mingw_gxx}" "{src_path}" -o "{exe_path}" {flags}',
        shell=True, capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    if cr.returncode != 0:
        return f"[编译失败]\n{cr.stderr.strip()[:1500]}"

    # 运行
    rr = subprocess.run([exe_path], capture_output=True, text=True, timeout=30,
                        encoding="utf-8", errors="replace")

    # 清理
    try: os.unlink(src_path)
    except: pass
    try: os.unlink(exe_path)
    except: pass

    out = rr.stdout.strip() or rr.stderr.strip() or f"(exit:{rr.returncode})"
    return out[:3000]


def list_templates() -> str:
    lines = ["Catch2 测试模板:"]
    for name in TEST_TEMPLATES:
        preview = TEST_TEMPLATES[name].split("\n")[0].replace("#", "").strip()[:60]
        lines.append(f"  [{name}] {preview}")
    return "\n".join(lines)


# ── 注入大脑 ──

def inject_to_brain(brain=None):
    global _BRAIN
    if brain: _BRAIN = brain
    if not _BRAIN: return 0
    count = 0
    try:
        _BRAIN.learn_fact(
            "Catch2: C++ 单元测试框架 (header-only v3 合并版). 支持 TDD、BDD、基准测试。"
            "用 run_catch2_test(template) 生成并执行测试。模板: basic/tdd/bdd。"
            "头文件位置: D:/Javis/tools/catch2/catch.hpp",
            category="catch2.intro", source="catch2", priority=3)
        count += 1
        _BRAIN.learn_fact(
            "Catch2 TDD 流程: 1) 选 template='tdd' 生成模板; "
            "2) extra_code 传入被测函数; "
            "3) 编译运行自动输出测试结果; "
            "4) 根据失败用例修改代码后再测。",
            category="catch2.workflow", source="catch2", priority=2)
        count += 1
        logger.info(f"Catch2 注入大脑: {count} 条")
    except: pass
    return count


# ── 工具注册 ──

def tools_for_registry():
    from core.tool_registry import ToolDef
    inject_to_brain()
    return [
        ToolDef("catch2_test", "生成并运行 C++ Catch2 单元测试。"
                "template=basic/tdd/bdd, extra_code=被测函数源码, test_name=测试名",
                {"type": "object", "properties": {
                    "template": {"type": "string", "enum": list(TEST_TEMPLATES.keys()),
                                "description": "模板类型"},
                    "test_name": {"type": "string", "description": "测试名(可选)"},
                    "extra_code": {"type": "string", "description": "被测函数C++源码"},
                }, "required": []},
                lambda **kw: ToolResult.success(
                    run_catch2_test(kw.get("template", "basic"), kw.get("test_name", ""),
                                    kw.get("extra_code", ""))),
                "catch2"),
        ToolDef("catch2_templates", "列出 Catch2 可用的测试模板",
                {"type": "object", "properties": {}, "required": []},
                lambda **kw: ToolResult.success(list_templates()), "catch2"),
    ]


# ── loader.py 兼容 ──
TOOL_NAME = "catch2"
TOOL_DESC = "C++ Catch2 单元测试框架 — 模板生成/编译/运行一键流程"
TOOL_CATEGORY = "catch2"
TOOL_PARAMS = {"type": "object", "properties": {}, "required": []}

def handler(**kwargs):
    a = kwargs.get("action", "list")
    if a == "test":
        r = run_catch2_test(kwargs.get("template", "basic"), kwargs.get("test_name", ""), kwargs.get("extra_code", ""))
        return {"success": True, "output": r}
    if a == "templates":
        return {"success": True, "output": list_templates()}
    return {"success": True, "output": "Catch2 就绪。用 catch2_test 写 C++ 测试。"}
