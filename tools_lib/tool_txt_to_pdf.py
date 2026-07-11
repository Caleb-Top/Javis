"""Javis自创: txt_to_pdf — 纯文本转PDF，支持中文"""
TOOL_NAME="txt_to_pdf"
TOOL_DESC="将TXT纯文本文件转换为PDF文档。自动处理换行、中文编码、分页。参数: txt_path(输入路径), output_path(可选输出路径), font_size(字号,默认12), encoding(文件编码,默认utf-8)"
TOOL_CATEGORY="document_convert"
TOOL_PARAMS={"type":"object","properties":{},"required":[]}

def handler(**kwargs):
    import os, json
    from pathlib import Path

    def convert(txt_path=None, output_path=None, font_size=12, encoding="utf-8", **kw):
        if not txt_path or not os.path.exists(txt_path):
            return {"success": False, "output": "", "message": f"文件不存在: {txt_path}"}
        if not output_path:
            output_path = str(Path(txt_path).with_suffix('.pdf'))

        try:
            # 读取文本（尝试多种编码）
            content = None
            for enc in [encoding, 'utf-8', 'gbk', 'gb2312', 'latin-1']:
                try:
                    with open(txt_path, 'r', encoding=enc) as f:
                        content = f.read()
                    break
                except (UnicodeDecodeError, UnicodeError):
                    continue

            if content is None:
                return {"success": False, "output": "", "message": "无法识别文件编码"}

            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas
            from reportlab.lib.units import mm
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont

            # 尝试注册中文字体
            cjk_font = "Helvetica"
            cjk_font_bold = "Helvetica-Bold"
            font_paths = [
                ("C:/Windows/Fonts/simhei.ttf", "SimHei"),
                ("C:/Windows/Fonts/simsun.ttc", "SimSun"),
                ("C:/Windows/Fonts/msyh.ttc", "MicrosoftYaHei"),
                ("C:/Windows/Fonts/simkai.ttf", "KaiTi"),
            ]
            for fp, fn in font_paths:
                if os.path.exists(fp):
                    try:
                        pdfmetrics.registerFont(TTFont(fn, fp))
                        cjk_font = fn
                        cjk_font_bold = fn
                        break
                    except:
                        pass

            c = canvas.Canvas(output_path, pagesize=A4)
            width, height = A4
            margin = 25 * mm
            line_height = font_size * 1.5 * mm
            usable_width = width - 2 * margin

            y = height - margin
            c.setFont(cjk_font, font_size)

            # 字符宽度估算
            char_width_estimate = font_size * 0.6 * mm
            chars_per_line = int(usable_width / char_width_estimate)

            for line in content.split('\n'):
                if not line.strip():
                    y -= line_height
                    if y < margin:
                        c.showPage()
                        c.setFont(cjk_font, font_size)
                        y = height - margin
                    continue

                # 长行自动换行
                while line:
                    if y < margin:
                        c.showPage()
                        c.setFont(cjk_font, font_size)
                        y = height - margin

                    # 估算截断位置
                    if len(line) > chars_per_line:
                        segment = line[:chars_per_line]
                        line = line[chars_per_line:]
                    else:
                        segment = line
                        line = ""

                    c.drawString(margin, y, segment)
                    y -= line_height

            c.save()
            size_kb = os.path.getsize(output_path) / 1024
            return {"success": True, "output": output_path,
                    "message": f"TXT已转换为PDF ({size_kb:.1f}KB): {output_path}"}
        except Exception as e:
            return {"success": False, "output": "", "message": f"转换失败: {str(e)}"}

    result = convert(**kwargs)
    return json.dumps(result, ensure_ascii=False)
