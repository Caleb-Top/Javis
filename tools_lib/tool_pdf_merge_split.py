"""Javis自创: pdf_merge_split — PDF合并/拆分/旋转/提取"""
TOOL_NAME="pdf_merge_split"
TOOL_DESC="PDF操作工具: 合并多个PDF、拆分PDF、提取指定页、旋转页面。参数: action(merge/split/extract/rotate/info), pdf_paths(逗号分隔的PDF路径列表), output_path(输出路径), pages(页数如'1-5,8,10'), rotation(旋转角度90/180/270)"
TOOL_CATEGORY="document_convert"
TOOL_PARAMS={"type":"object","properties":{},"required":[]}

def handler(**kwargs):
    import os, json
    from pathlib import Path

    def convert(action="info", pdf_paths=None, output_path=None, pages=None, rotation=90, **kw):
        try:
            import fitz  # PyMuPDF
        except ImportError:
            try:
                from pikepdf import Pdf
                return _handle_with_pikepdf(action, pdf_paths, output_path, pages, rotation)
            except:
                return {"success": False, "output": "", "message": "需要安装PyMuPDF或pikepdf: pip install PyMuPDF"}

        if action == "info":
            if not pdf_paths:
                return {"success": False, "message": "请提供pdf_paths"}
            files = [f.strip() for f in pdf_paths.split(',')]
            info_list = []
            for fp in files:
                if not os.path.exists(fp):
                    info_list.append({"file": fp, "error": "文件不存在"})
                    continue
                doc = fitz.open(fp)
                info_list.append({
                    "file": fp,
                    "pages": doc.page_count,
                    "size_kb": os.path.getsize(fp) / 1024,
                    "metadata": doc.metadata
                })
                doc.close()
            return {"success": True, "output": json.dumps(info_list, indent=2, ensure_ascii=False),
                    "message": f"已分析 {len(info_list)} 个PDF文件"}

        elif action == "merge":
            if not pdf_paths:
                return {"success": False, "message": "请提供pdf_paths（逗号分隔）"}
            files = [f.strip() for f in pdf_paths.split(',') if os.path.exists(f.strip())]
            if len(files) < 2:
                return {"success": False, "message": "至少需要2个PDF文件进行合并"}
            if not output_path:
                output_path = str(Path(files[0]).parent / "merged.pdf")

            merged = fitz.open()
            for fp in files:
                doc = fitz.open(fp)
                merged.insert_pdf(doc)
                doc.close()
            merged.save(output_path)
            page_cnt = merged.page_count
            merged.close()
            return {"success": True, "output": output_path,
                    "message": f"已合并 {len(files)} 个PDF ({page_cnt}页)"}

        elif action == "split":
            if not pdf_paths:
                return {"success": False, "message": "请提供pdf_paths"}
            fp = pdf_paths.split(',')[0].strip()
            if not os.path.exists(fp):
                return {"success": False, "message": f"文件不存在: {fp}"}
            doc = fitz.open(fp)
            out_dir = output_path or str(Path(fp).parent / Path(fp).stem + "_split")
            os.makedirs(out_dir, exist_ok=True)
            saved = []
            for i, page in enumerate(doc):
                new_doc = fitz.open()
                new_doc.insert_pdf(doc, from_page=i, to_page=i)
                out_path = os.path.join(out_dir, f"page_{i+1:03d}.pdf")
                new_doc.save(out_path)
                new_doc.close()
                saved.append(out_path)
            doc.close()
            return {"success": True, "output": out_dir,
                    "message": f"已拆分为 {len(saved)} 个单页PDF"}

        elif action == "extract":
            if not pdf_paths or not pages:
                return {"success": False, "message": "请提供pdf_paths和pages参数（如'1-3,5,7-9'）"}
            fp = pdf_paths.split(',')[0].strip()
            if not os.path.exists(fp):
                return {"success": False, "message": f"文件不存在: {fp}"}

            # 解析页码
            page_nums = set()
            for part in pages.split(','):
                part = part.strip()
                if '-' in part:
                    a, b = part.split('-', 1)
                    page_nums.update(range(int(a), int(b)+1))
                else:
                    page_nums.add(int(part))

            doc = fitz.open(fp)
            if not output_path:
                output_path = str(Path(fp).parent / f"{Path(fp).stem}_extracted.pdf")

            new_doc = fitz.open()
            for pn in sorted(page_nums):
                if 1 <= pn <= doc.page_count:
                    new_doc.insert_pdf(doc, from_page=pn-1, to_page=pn-1)
            new_doc.save(output_path)
            new_doc.close()
            doc.close()
            return {"success": True, "output": output_path,
                    "message": f"已提取 {len(page_nums)} 页"}

        elif action == "rotate":
            if not pdf_paths:
                return {"success": False, "message": "请提供pdf_paths"}
            fp = pdf_paths.split(',')[0].strip()
            if not os.path.exists(fp):
                return {"success": False, "message": f"文件不存在: {fp}"}
            if not output_path:
                output_path = str(Path(fp).parent / f"{Path(fp).stem}_rotated.pdf")

            doc = fitz.open(fp)
            for page in doc:
                page.set_rotation(rotation)
            doc.save(output_path)
            doc.close()
            return {"success": True, "output": output_path,
                    "message": f"已旋转 {rotation}°"}

        else:
            return {"success": False, "message": f"不支持的操作: {action}。支持: info/merge/split/extract/rotate"}

    def _handle_with_pikepdf(action, pdf_paths, output_path, pages, rotation):
        from pikepdf import Pdf
        if action == "info":
            files = [f.strip() for f in pdf_paths.split(',')]
            info_list = []
            for fp in files:
                pdf = Pdf.open(fp)
                info_list.append({"file": fp, "pages": len(pdf.pages), "size_kb": os.path.getsize(fp)/1024})
                pdf.close()
            return {"success": True, "output": json.dumps(info_list, indent=2, ensure_ascii=False)}
        elif action == "merge":
            files = [f.strip() for f in pdf_paths.split(',') if os.path.exists(f.strip())]
            merged = Pdf.new()
            for fp in files:
                src = Pdf.open(fp)
                merged.pages.extend(src.pages)
                src.close()
            merged.save(output_path or "merged.pdf")
            merged.close()
            return {"success": True, "output": output_path or "merged.pdf", "message": f"已合并 {len(files)} 个PDF"}
        return {"success": False, "message": "pikepdf仅支持info/merge操作"}

    result = convert(**kwargs)
    return json.dumps(result, ensure_ascii=False)
