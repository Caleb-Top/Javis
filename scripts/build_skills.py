"""技能打包脚本 — 将每个 skills/*.py 编译为独立 .exe

用法:
  python build_skills.py              # 打包所有技能
  python build_skills.py 全功能        # 打包指定技能

要求: pip install pyinstaller
编译后的 .exe 位于 skills/ 目录下, 命名 skill_全功能.exe

修改技能后重新运行此脚本即可更新 .exe:
  1. 编辑 skills/全功能.py
  2. python build_skills.py 全功能
  3. 重启 Javis
"""

import os, sys, shutil, subprocess, glob, re
from pathlib import Path

ROOT = Path(__file__).parent
SKILLS_DIR = ROOT / "skills"
RUNNER = ROOT / "skill_runner.py"
ICON = ROOT / "web" / "favicon.ico"


def patch_runner(skill_id: str) -> str:
    """创建 skill_runner 的临时副本, 注入 SKILL_MODULE"""
    content = RUNNER.read_text(encoding="utf-8")
    content = content.replace('SKILL_MODULE = ""', f'SKILL_MODULE = "{skill_id}"')
    # 写入临时文件
    tmp = ROOT / f"_build_{skill_id}.py"
    tmp.write_text(content, encoding="utf-8")
    return str(tmp)


def build_one(skill_id: str):
    """编译单个技能为 .exe"""
    print(f"\n🔨 编译技能: {skill_id}")
    entry = patch_runner(skill_id)
    output_name = f"skill_{skill_id}"

    cmd = [
        "pyinstaller",
        "--onefile",               # 单文件 exe
        "--console",               # 控制台程序
        "--clean",                 # 清理缓存
        "--noconfirm",             # 覆盖确认
        "--distpath", str(SKILLS_DIR),
        "--workpath", str(ROOT / "_build_temp"),
        "--specpath", str(ROOT / "_build_temp"),
        "--name", output_name,
        entry,
    ]

    # 添加图标 (如果有)
    if ICON.exists():
        cmd.extend(["--icon", str(ICON)])

    print(f"  运行: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    # 清理临时文件
    tmp_py = ROOT / f"_build_{skill_id}.py"
    if tmp_py.exists():
        tmp_py.unlink()

    # 清理构建目录
    temp_dir = ROOT / "_build_temp"
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)

    # 清理 .spec 文件
    for spec in ROOT.glob(f"*.spec"):
        spec.unlink()

    if result.returncode == 0:
        exe_path = SKILLS_DIR / f"{output_name}.exe"
        if exe_path.exists():
            size = exe_path.stat().st_size // 1024
            print(f"  ✅ {skill_id} → {exe_path.name} ({size}KB)")
            return True
    else:
        print(f"  ❌ 编译失败:")
        print(result.stderr[:500])
        return False


def discover_skills() -> list[str]:
    """发现所有技能"""
    skills = []
    for f in sorted(SKILLS_DIR.glob("*.py")):
        if f.name == "__init__.py":
            continue
        skill_id = f.stem
        skills.append(skill_id)
    return skills


def main():
    # 检查 pyinstaller
    try:
        subprocess.run(["pyinstaller", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ 需要安装 PyInstaller: pip install pyinstaller")
        sys.exit(1)

    # 获取要编译的技能
    targets = sys.argv[1:] if len(sys.argv) > 1 else discover_skills()

    if not targets:
        print("没有找到技能文件")
        sys.exit(1)

    success = 0
    fail = 0
    for skill_id in targets:
        if build_one(skill_id):
            success += 1
        else:
            fail += 1

    print(f"\n{'='*40}")
    print(f"✅ 成功: {success}   ❌ 失败: {fail}")
    print(f"技能 .exe 位置: {SKILLS_DIR}/")


if __name__ == "__main__":
    main()
