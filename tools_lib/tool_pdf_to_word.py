"""Javis自创: pdf_to_word"""
TOOL_NAME="pdf_to_word"
TOOL_DESC="将PDF文件转换为Word文档。支持pdf2docx引擎，自动降级到PyPDF2方案。参数: pdf_path(输入路径), output_path(可选输出路径)"
TOOL_CATEGORY="document_convert"
TOOL_PARAMS={"type":"object","properties":{},"required":[]}

def handler(**kwargs):
    import os, sys, importlib, json
    from pathlib import Path

    def pdf_to_word(pdf_path, output_path=None):
        """
        将PDF文件转换为Word文档

        参数:
            pdf_path: PDF文件路径
            output_path: 输出Word文件路径(可选，默认同目录同名)

        返回: {"success": bool, "output": "路径", "message": "说明"}
        """
        if not os.path.exists(pdf_path):
            return {"success": False, "output": "", "message": f"文件不存在: {pdf_path}"}

        if not output_path:
            output_path = str(Path(pdf_path).with_suffix('.docx'))

        try:
            # 使用 pdf2docx 转换
            from pdf2docx import Converter
            cv = Converter(pdf_path)
            cv.convert(output_path, start=0, end=None)
            cv.close()

            if os.path.exists(output_path):
                return {"success": True, "output": output_path, "message": f"PDF已成功转换为Word: {output_path}"}
            else:
                return {"success": False, "output": "", "message": "转换失败，输出文件未生成"}
        except ImportError:
            # 备用方案：使用 PyPDF2 + python-docx
            try:
                from PyPDF2 import PdfReader
                from docx import Document

                reader = PdfReader(pdf_path)
                doc = Document()

                for page in reader.pages:
                    text = page.extract_text()
                    if text.strip():
                        doc.add_paragraph(text)

                doc.save(output_path)

                if os.path.exists(output_path):
                    return {"success": True, "output": output_path, "message": f"PDF已成功转换为Word(备用方案): {output_path}"}
            except Exception as e2:
                return {"success": False, "output": "", "message": f"所有转换方法均失败: {str(e2)}"}
        except Exception as e:
            return {"success": False, "output": "", "message": f"转换失败: {str(e)}"}

    # 如果是直接调用
    if __name__ == "__main__":
        import sys
        args = sys.argv[1:]
        if len(args) >= 1:
            result = pdf_to_word(args[0], args[1] if len(args) > 1 else None)
            print(json.dumps(result, ensure_ascii=False))
