@echo off
chcp 65001 >nul
cd /d D:\Javis

echo ════════════════════════════════
echo  Javis Skill Builder
echo ════════════════════════════════
echo.
echo  Using: %PYTHON_CMD%
echo  Date:  %DATE% %TIME%
echo.

echo Step 1: Installing PyInstaller...
pip install pyinstaller -q

echo.
echo Step 2: Building all skills...
python build_skills.py %*

if %errorlevel% equ 0 (
    echo.
    echo ✅ Build complete!
    echo Skills are in: D:\Javis\skills\
    echo.
    echo Usage:
    echo   python build_skills.py         - Build all skills
    echo   python build_skills.py 全功能   - Build specific skill
    echo.
    echo Edit skills/?.py, then rebuild to update .exe
) else (
    echo.
    echo ❌ Build failed
)

pause
