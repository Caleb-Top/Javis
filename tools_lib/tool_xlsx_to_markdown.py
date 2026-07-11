"""Javis自创: xlsx_to_markdown — Excel表格转Markdown表格"""
TOOL_NAME="xlsx_to_markdown"
TOOL_DESC="将Excel文件转换为Markdown表格格式。支持多工作表、保留数据对齐。参数: excel_path(输入路径), output_path(可选输出路径), sheet_name(可选工作表名,默认全部)"
TOOL_CATEGORY="document_convert"
TOOL_PARAMS={"type":"object","properties":{},"required":[]}

def handler(**kwargs):
    import os, json
    from pathlib import Path

    def convert(excel_path=None, output_path=None, sheet_name=None, **kw):
        if not excel_path or not os.path.exists(excel_path):
            return {"success": False, "output": "", "message": f"文件不存在: {excel_path}"}
        if not output_path:
            output_path = str(Path(excel_path).with_suffix('.md'))

        try:
            import pandas as pd
            import numpy as np

            md_lines = []
            md_lines.append(f"# {Path(excel_path).stem}\n")

            if sheet_name:
                dfs = {sheet_name: pd.read_excel(excel_path, sheet_name=sheet_name)}
            else:
                dfs = pd.read_excel(excel_path, sheet_name=None)

            for sn, df in dfs.items():
                if df.empty:
                    md_lines.append(f"## {sn}\n\n*（空工作表）*\n\n")
                    continue

                md_lines.append(f"## {sn}\n")

                # 处理NaN
                df = df.fillna('')

                # 生成Markdown表格
                cols = [str(c) for c in df.columns]
                md_lines.append("| " + " | ".join(cols) + " |")
                md_lines.append("| " + " | ".join("---" for _ in cols) + " |")

                for _, row in df.iterrows():
                    values = [str(v).replace('\n', ' ').replace('|', '\\|') for v in row.values]
                    md_lines.append("| " + " | ".join(values) + " |")

                md_lines.append(f"\n*{len(df)} 行 × {len(df.columns)} 列*\n\n")

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(md_lines))

            size_kb = os.path.getsize(output_path) / 1024
            return {"success": True, "output": output_path,
                    "message": f"Excel已转换为Markdown ({len(dfs)}个工作表, {size_kb:.1f}KB): {output_path}"}
        except Exception as e:
            return {"success": False, "output": "", "message": f"转换失败: {str(e)}"}

    result = convert(**kwargs)
    return json.dumps(result, ensure_ascii=False)
