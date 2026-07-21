"""Javis自创: images_to_pdf"""
TOOL_NAME="images_to_pdf"
TOOL_DESC="将图片合并转换为PDF。参数: image_paths(图片路径列表/逗号分隔字符串/目录), output_path(输出路径)"
TOOL_CATEGORY="document_convert"
TOOL_PARAMS={"type":"object","properties":{},"required":[]}

def handler(**kwargs):
    import os, sys, importlib, json
    from pathlib import Path


    def convert(image_paths=None, output_path=None, **kw):
        try:
            if isinstance(image_paths, str):
                if os.path.isdir(image_paths):
                    supported = ['.png', '.jpg', '.jpeg', '.bmp', '.tiff']
                    image_paths = sorted([os.path.join(image_paths, f) for f in os.listdir(image_paths) 
                                        if os.path.splitext(f)[1].lower() in supported])
                else:
                    image_paths = [p.strip() for p in image_paths.split(',') if p.strip()]
            if not image_paths or len(image_paths) == 0:
                return {"success": False, "output": "", "message": "未提供有效的图片路径"}
            if not output_path:
                output_path = str(Path(image_paths[0]).parent / "merged.pdf")
            try:
                import img2pdf
                with open(output_path, 'wb') as f:
                    f.write(img2pdf.convert([p for p in image_paths if os.path.exists(p)]))
            except:
                from PIL import Image
                images = [Image.open(p).convert('RGB') for p in image_paths if os.path.exists(p)]
                if images:
                    images[0].save(output_path, 'PDF', save_all=True, append_images=images[1:])
            return {"success": True, "output": output_path, "message": f"已合并 {len(image_paths)} 张图片"} if os.path.exists(output_path) else {"success": False}
        except Exception as e:
            return {"success": False, "output": "", "message": f"转换失败: {str(e)}"}
    

    # 从kwargs获取参数
    result = convert(**kwargs)
    return json.dumps(result, ensure_ascii=False)
