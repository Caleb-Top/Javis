"""Javis自创: pdf_to_excel"""
TOOL_NAME="pdf_to_excel"
TOOL_DESC="从PDF中提取表格并转换为Excel文件。支持pdfplumber引擎。参数: pdf_path(输入路径), output_path(可选输出路径)"
TOOL_CATEGORY="document_convert"
TOOL_PARAMS={"type":"object","properties":{},"required":[]}

def handler(**kwargs):
    import os, sys, importlib, json
    from pathlib import Path


    def convert(pdf_path=None, output_path=None, **kw):
        if not pdf_path or not os.path.exists(pdf_path):
            return {"success": False, "output": "", "message": f"文件不存在: {pdf_path}"}
        if not output_path:
            output_path = str(Path(pdf_path).with_suffix('.xlsx'))
        try:
            import pdfplumber
            import pandas as pd
            all_tables = []
            with pdfplumber.open(pdf_path) as pdf:
                for i, page in enumerate(pdf.pages):
                    tables = page.extract_tables()
                    for j, table in enumerate(tables):
                        if table and len(table) > 1:
                            df = pd.DataFrame(table[1:], columns=table[0])
                            all_tables.append((f"Page{i+1}_T{j+1}", df))
            if not all_tables:
                text = ""
                with pdfplumber.open(pdf_path) as pdf:
                    for page in pdf.pages:
                        text += page.extract_text() + "\n"
                if text.strip():
                    lines = [l.split() for l in text.split('\n') if l.strip()]
                    if lines:
                        df = pd.DataFrame(lines)
                        all_tables = [("ExtractedText", df)]
            if all_tables:
                with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
                    for sn, df in all_tables:
                        df.to_excel(writer, sheet_name=sn[:31], index=False)
                return {"success": True, "output": output_path, "message": f"成功提取 {len(all_tables)} 个表格"}
            return {"success": False, "output": "", "message": "未找到表格数据"}
        except Exception as e:
            return {"success": False, "output": "", "message": f"转换失败: {str(e)}"}
    

    # 从kwargs获取参数
    result = convert(**kwargs)
    return json.dumps(result, ensure_ascii=False)
