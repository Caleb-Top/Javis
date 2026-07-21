"""Javis自创: pdf_to_images"""
TOOL_NAME="pdf_to_images"
TOOL_DESC="将PDF每一页转换为图片。参数: pdf_path(输入路径), output_dir(输出目录), format(图片格式png/jpg,默认png), dpi(分辨率,默认200)"
TOOL_CATEGORY="document_convert"
TOOL_PARAMS={"type":"object","properties":{},"required":[]}

def handler(**kwargs):
    import os, sys, importlib, json
    from pathlib import Path


    def convert(pdf_path=None, output_dir=None, format="png", dpi=200, **kw):
        if not pdf_path or not os.path.exists(pdf_path):
            return {"success": False, "output": "", "message": f"文件不存在: {pdf_path}"}
        if not output_dir:
            output_dir = str(Path(pdf_path).parent / Path(pdf_path).stem + "_images")
        os.makedirs(output_dir, exist_ok=True)
        try:
            import pdfplumber
            images = []
            with pdfplumber.open(pdf_path) as pdf:
                for i, page in enumerate(pdf.pages):
                    img = page.to_image(resolution=int(dpi))
                    ext = "png" if format.lower() == "png" else "jpg"
                    img_path = os.path.join(output_dir, f"page_{i+1}.{ext}")
                    img.save(img_path)
                    images.append(img_path)
            return {"success": True, "output": output_dir, "message": f"成功生成 {len(images)} 张图片", "images": images}
        except Exception as e:
            return {"success": False, "output": "", "message": f"转换失败: {str(e)}"}
    

    # 从kwargs获取参数
    result = convert(**kwargs)
    return json.dumps(result, ensure_ascii=False)
