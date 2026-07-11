@echo off
chcp 65001 >nul
echo ════════════════════════════════════════════
echo  迁移 Ollama 模型到 D:\Javis\ollama_models\
echo ════════════════════════════════════════════
echo.

:: 1. 停止 Ollama 服务 (否则文件被占用)
echo [1/5] 停止 Ollama 服务...
taskkill /f /im ollama.exe 2>nul
taskkill /f /im ollama_app.exe 2>nul
timeout /t 3 /nobreak >nul

:: 2. 创建目标目录
echo [2/5] 创建 D:\Javis\ollama_models\...
if not exist "D:\Javis\ollama_models" mkdir D:\Javis\ollama_models
if not exist "D:\Javis\ollama_models\blobs" mkdir D:\Javis\ollama_models\blobs
if not exist "D:\Javis\ollama_models\manifests" mkdir D:\Javis\ollama_models\manifests

:: 3. 复制模型文件 (保留原文件作为备份)
echo [3/5] 复制模型文件到 D:\Javis\ollama_models\...
if exist "%USERPROFILE%\.ollama\models" (
    echo   找到 Ollama 模型目录, 正在复制...
    xcopy /E /I /Y "%USERPROFILE%\.ollama\models\*.*" "D:\Javis\ollama_models\"
    echo   ✅ 复制完成
) else (
    echo   未找到 %USERPROFILE%\.ollama\models
    echo   尝试其他安装位置...
    if exist "C:\Program Files\Ollama\models" (
        xcopy /E /I /Y "C:\Program Files\Ollama\models\*.*" "D:\Javis\ollama_models\"
        echo   ✅ 复制完成
    ) else (
        echo   ⚠️ 未找到 Ollama 模型目录
        echo   请确认 Ollama 已安装或手动复制模型文件
    )
)

:: 4. 设置环境变量 OLLAMA_MODELS (用户级别, 永久生效)
echo [4/5] 设置环境变量 OLLAMA_MODELS = D:\Javis\ollama_models...
setx OLLAMA_MODELS "D:\Javis\ollama_models" /M
echo   ✅ 环境变量已设置 (需重启 Ollama 生效)

:: 5. 创建 Ollama 启动快捷方式指向新目录
echo [5/5] 创建启动脚本...
(
echo @echo off
echo set OLLAMA_MODELS=D:\Javis\ollama_models
echo echo [Ollama] 模型目录: %%OLLAMA_MODELS%%
echo start /B "" "%LOCALAPPDATA%\Ollama\ollama.exe" serve
echo echo [Ollama] 服务已启动
echo pause
) > "D:\Javis\启动Ollama.bat"

echo.
echo ════════════════════════════════════════════
echo ✅ 迁移完成!
echo.
echo   模型位置: D:\Javis\ollama_models\
echo   原文件保留在: %%USERPROFILE%%\.ollama\models\
echo.
echo   后续操作:
echo   1. 重启电脑 或
echo   2. 双击 D:\Javis\启动Ollama.bat (使用新目录启动)
echo   3. 双击 D:\Javis\启动Javis.bat 启动 Javis
echo.
echo   验证: ollama list 应显示 deepseek-r1:8b
echo ════════════════════════════════════════════
pause
