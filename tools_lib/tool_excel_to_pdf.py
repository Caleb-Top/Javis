"""Javis自创: excel_to_pdf"""
TOOL_NAME="excel_to_pdf"
TOOL_DESC="将Excel文件转换为PDF表格报告。参数: excel_path(输入路径), output_path(可选输出路径), sheet_name(可选工作表名)"
TOOL_CATEGORY="document_convert"
TOOL_PARAMS={"type":"object","properties":{},"required":[]}

def handler(**kwargs):
    import os, sys, importlib, json
    from pathlib import Path


    def convert(excel_path=None, output_path=None, sheet_name=None, **kw):
        if not excel_path or not os.path.exists(excel_path):
            return {"success": False, "output": "", "message": f"文件不存在: {excel_path}"}
        if not output_path:
            output_path = str(Path(excel_path).with_suffix('.pdf'))
        try:
            import pandas as pd
            from reportlab.lib.pagesizes import A4, landscape
            from reportlab.pdfgen import canvas
            from reportlab.lib.units import mm
            from reportlab.lib import colors
            from reportlab.platypus import Table, TableStyle, SimpleDocTemplate

            if sheet_name:
                dfs = {sheet_name: pd.read_excel(excel_path, sheet_name=sheet_name)}
            else:
                dfs = pd.read_excel(excel_path, sheet_name=None)

            doc = SimpleDocTemplate(output_path, pagesize=A4)
            elements = []

            for sn, df in dfs.items():
                data = [df.columns.tolist()] + df.astype(str).values.tolist()
                table = Table(data)
                style = TableStyle([
                    ('BACKGROUND', (0,0), (-1,0), colors.grey),
                    ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
                    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                    ('FONTSIZE', (0,0), (-1,0), 10),
                    ('FONTSIZE', (0,1), (-1,-1), 8),
                    ('GRID', (0,0), (-1,-1), 0.5, colors.black),
                    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.lightgrey]),
                ])
                table.setStyle(style)
                elements.append(table)

            doc.build(elements)
            return {"success": True, "output": output_path, "message": f"Excel已转换为PDF: {output_path}"}
        except Exception as e:
            return {"success": False, "output": "", "message": f"转换失败: {str(e)}"}
    

    # 从kwargs获取参数
    result = convert(**kwargs)
    return json.dumps(result, ensure_ascii=False)
