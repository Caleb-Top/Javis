"""Javis自创: markdown_to_pdf"""
TOOL_NAME="markdown_to_pdf"
TOOL_DESC="将Markdown文件直接转换为PDF。参数: md_path(输入路径), output_path(可选输出路径)"
TOOL_CATEGORY="document_convert"
TOOL_PARAMS={"type":"object","properties":{},"required":[]}

def handler(**kwargs):
    import os, sys, importlib, json
    from pathlib import Path


    def convert(md_path=None, output_path=None, **kw):
        if not md_path or not os.path.exists(md_path):
            return {"success": False, "output": "", "message": f"文件不存在: {md_path}"}
        if not output_path:
            output_path = str(Path(md_path).with_suffix('.pdf'))
        try:
            import markdown
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas
            from reportlab.lib.units import mm
            from reportlab.lib.styles import getSampleStyleSheet

            with open(md_path, 'r', encoding='utf-8') as f:
                md_text = f.read()

            html = markdown.markdown(md_text, extensions=['extra'])

            # 简单解析HTML标签并渲染到PDF
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')

            c = canvas.Canvas(output_path, pagesize=A4)
            width, height = A4
            y = height - 30*mm

            for element in soup.find_all(['h1','h2','h3','p','li','pre']):
                if y < 25*mm:
                    c.showPage()
                    y = height - 30*mm

                text = element.get_text()
                if element.name == 'h1':
                    c.setFont("Helvetica-Bold", 18)
                    c.drawString(25*mm, y, text)
                    y -= 12*mm
                elif element.name == 'h2':
                    c.setFont("Helvetica-Bold", 14)
                    c.drawString(25*mm, y, text)
                    y -= 10*mm
                elif element.name == 'h3':
                    c.setFont("Helvetica-Bold", 12)
                    c.drawString(25*mm, y, text)
                    y -= 8*mm
                elif element.name == 'pre':
                    c.setFont("Courier", 8)
                    for line in text.split('\n'):
                        if y < 20*mm:
                            c.showPage()
                            y = height - 30*mm
                        c.drawString(25*mm, y, line[:95])
                        y -= 4*mm
                    y -= 3*mm
                else:
                    c.setFont("Helvetica", 11)
                    c.drawString(25*mm, y, text[:95])
                    y -= 7*mm

            c.save()
            return {"success": True, "output": output_path, "message": f"Markdown已转换为PDF: {output_path}"}
        except Exception as e:
            return {"success": False, "output": "", "message": f"转换失败: {str(e)}"}
    

    result = convert(**kwargs)
    return json.dumps(result, ensure_ascii=False)
