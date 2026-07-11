"""Javis自创: pdf_to_markdown — PDF转Markdown，保留标题/表格/列表结构"""
TOOL_NAME="pdf_to_markdown"
TOOL_DESC="将PDF文件转换为Markdown格式。智能识别标题、表格、列表，保留文档结构。参数: pdf_path(输入路径), output_path(可选输出路径), preserve_tables(是否保留表格,默认true)"
TOOL_CATEGORY="document_convert"
TOOL_PARAMS={"type":"object","properties":{},"required":[]}

def handler(**kwargs):
    import os, json, re
    from pathlib import Path

    def convert(pdf_path=None, output_path=None, preserve_tables=True, **kw):
        if not pdf_path or not os.path.exists(pdf_path):
            return {"success": False, "output": "", "message": f"文件不存在: {pdf_path}"}
        if not output_path:
            output_path = str(Path(pdf_path).with_suffix('.md'))

        try:
            md_lines = []
            md_lines.append(f"# {Path(pdf_path).stem}\n")

            # 方案1: 使用 PyMuPDF (fitz) — 最佳结构保留
            try:
                import fitz
                doc = fitz.open(pdf_path)
                for page_num, page in enumerate(doc):
                    # 尝试用 blocks 提取结构化内容
                    blocks = page.get_text("blocks")
                    for block in blocks:
                        x0, y0, x1, y1, text, block_type, block_no = block
                        text = text.strip()
                        if not text:
                            continue

                        # 判断是否为标题（短文本 + 字号较大）
                        if block_type == 0:  # text block
                            # 简单启发式: 短文本可能是标题
                            lines_in_block = text.split('\n')
                            for line in lines_in_block:
                                line = line.strip()
                                if not line:
                                    continue
                                # 全大写短文本 → 标题
                                if len(line) < 80 and line.isupper() and len(line) > 3:
                                    md_lines.append(f"\n## {line.title()}\n")
                                # 很短的行可能是节标题
                                elif len(line) < 60 and not line.endswith('.') and len(line.split()) < 15:
                                    if any(kw in line.lower() for kw in ['abstract','introduction','conclusion','references','method','result','discussion','摘要','引言','结论','参考文献','方法','结果','讨论']):
                                        md_lines.append(f"\n## {line}\n")
                                    else:
                                        md_lines.append(f"\n### {line}\n")
                                else:
                                    md_lines.append(f"{line}\n\n")
                        elif block_type == 1:  # image block
                            md_lines.append(f"\n> [图片: Page {page_num+1}]\n\n")

                    # 提取表格
                    if preserve_tables:
                        tables = page.find_tables()
                        for table in tables:
                            data = table.extract()
                            if data and len(data) > 0:
                                md_lines.append("\n")
                                # 表头
                                header = data[0]
                                md_lines.append("| " + " | ".join(str(c) if c else "" for c in header) + " |")
                                md_lines.append("| " + " | ".join("---" for _ in header) + " |")
                                # 数据行
                                for row in data[1:]:
                                    md_lines.append("| " + " | ".join(str(c) if c else "" for c in row) + " |")
                                md_lines.append("\n")

                doc.close()
            except ImportError:
                # 方案2: 使用 pdfplumber
                import pdfplumber
                with pdfplumber.open(pdf_path) as pdf:
                    for i, page in enumerate(pdf.pages):
                        text = page.extract_text()
                        if text:
                            for line in text.split('\n'):
                                line = line.strip()
                                if not line:
                                    continue
                                if len(line) < 60 and not line.endswith('.') and not line.endswith(',') and len(line.split()) < 15:
                                    if any(kw in line.lower() for kw in ['abstract','introduction','conclusion','references','method','result','discussion']):
                                        md_lines.append(f"\n## {line}\n")
                                    else:
                                        md_lines.append(f"\n### {line}\n")
                                else:
                                    md_lines.append(f"{line}\n\n")

                        if preserve_tables:
                            tables = page.extract_tables()
                            for table in tables:
                                if table and len(table) > 0:
                                    md_lines.append("\n")
                                    header = table[0]
                                    md_lines.append("| " + " | ".join(str(c) if c else "" for c in header) + " |")
                                    md_lines.append("| " + " | ".join("---" for _ in header) + " |")
                                    for row in table[1:]:
                                        md_lines.append("| " + " | ".join(str(c) if c else "" for c in row) + " |")
                                    md_lines.append("\n")

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(''.join(md_lines))

            size_kb = os.path.getsize(output_path) / 1024
            return {"success": True, "output": output_path, "message": f"PDF已转换为Markdown ({size_kb:.1f}KB): {output_path}"}
        except Exception as e:
            return {"success": False, "output": "", "message": f"转换失败: {str(e)}"}

    result = convert(**kwargs)
    return json.dumps(result, ensure_ascii=False)
