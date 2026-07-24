"""
Javis 格式转换工具初始�?
将所有工具路径统一指向 D:\Javis\tools\ �?
"""
import os
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
TESSERACT_DIR = os.path.join(TOOLS_DIR, "Tesseract-OCR")
IMAGEMAGICK_DIR = os.path.join(TOOLS_DIR, "ImageMagick")
NODEJS_DIR = os.path.join(TOOLS_DIR, "nodejs")
GH_DIR = os.path.join(os.path.dirname(TOOLS_DIR), "tools", "gh")
PANDOC_DIR = os.path.join(os.environ.get("LOCALAPPDATA", "C:/Users/34247/AppData/Local"), "Pandoc")
MINGW_DIR = os.path.join(os.path.dirname(TOOLS_DIR), "tools", "mingw32", "bin")
TESSERACT_EXE = os.path.join(TESSERACT_DIR, "tesseract.exe")
MAGICK_EXE = os.path.join(IMAGEMAGICK_DIR, "magick.exe")
NODE_EXE = os.path.join(NODEJS_DIR, "node.exe")
NPM_CMD = os.path.join(NODEJS_DIR, "npm.cmd")
GCC_EXE = os.path.join(MINGW_DIR, "gcc.exe")
GXX_EXE = os.path.join(MINGW_DIR, "g++.exe")

def setup():
    """初始化所有工具路径"""
    results = {}

    # 1. pytesseract
    try:
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_EXE
        os.environ['TESSDATA_PREFIX'] = os.path.join(TESSERACT_DIR, 'tessdata')
        results['pytesseract'] = f'OK -> {TESSERACT_EXE}'
    except Exception as e:
        results['pytesseract'] = f'FAIL: {e}'

    # 2. Wand (ImageMagick)
    try:
        os.environ['MAGICK_HOME'] = IMAGEMAGICK_DIR
        results['wand'] = f'OK -> {IMAGEMAGICK_DIR}'
    except Exception as e:
        results['wand'] = f'FAIL: {e}'

    # 3. Node.js
    if os.path.isfile(NODE_EXE):
        results['nodejs'] = f'OK -> {NODE_EXE}'
    else:
        results['nodejs'] = 'SKIP: node.exe not found'

    # 4. C/C++ (llvm-mingw)
    if os.path.isfile(GCC_EXE):
        results['mingw'] = f'OK -> {GCC_EXE}'
    else:
        results['mingw'] = 'SKIP: gcc not found'

    # 5. GitHub CLI (gh)
    GH_EXE = os.path.join(GH_DIR, "gh.exe")
    if os.path.isfile(GH_EXE):
        results['gh'] = f'OK -> {GH_EXE}'
    else:
        results['gh'] = 'SKIP: gh not found'

    # 6. Catch2 include 路径
    CATCH2_DIR = os.path.join(os.path.dirname(TOOLS_DIR), "tools", "catch2")
    os.environ['CATCH2_INCLUDE'] = CATCH2_DIR
    results['catch2'] = f'OK -> {CATCH2_DIR}'
    if os.path.isdir(PANDOC_DIR) and os.path.isfile(os.path.join(PANDOC_DIR, 'pandoc.exe')):
        results['pandoc'] = f'OK -> {os.path.join(PANDOC_DIR, "pandoc.exe")}'
    try:
        os.environ['GH_TOKEN'] = open(os.path.join(os.path.dirname(TOOLS_DIR), 'tools', 'gh', '.token'), encoding='utf-8').read().strip()
        results['gh_token'] = 'OK'
    except Exception:
        results['gh_token'] = 'SKIP: .token not found'

    # 7. 添加�?PATH (进程级别)
    os.environ['PATH'] = (
        TESSERACT_DIR + os.pathsep +
        IMAGEMAGICK_DIR + os.pathsep +
        NODEJS_DIR + os.pathsep +
        MINGW_DIR + os.pathsep +
        GH_DIR + os.pathsep +
        PANDOC_DIR + os.pathsep +
        os.environ.get('PATH', '')
    )
    results['PATH'] = 'OK'

    return results

if __name__ == '__main__':
    for k, v in setup().items():
        print(f"  {k}: {v}")
