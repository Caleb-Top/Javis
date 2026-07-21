"""Javis自创: batch_convert"""
TOOL_NAME="batch_convert"
TOOL_DESC="批量文件格式转换器。可一次性转换整个目录或指定文件列表。参数: source_dir(源目录) 或 file_list(文件列表逗号分隔), target_format(目标格式), output_dir(输出目录,可选)"
TOOL_CATEGORY="document_convert"
TOOL_PARAMS={"type":"object","properties":{},"required":[]}

def handler(**kwargs):
    import os, sys, importlib, json
    from pathlib import Path


    def convert(source_dir=None, file_list=None, target_format="pdf", output_dir=None, **kw):
        try:
            files_to_convert = []

            if source_dir and os.path.isdir(source_dir):
                supported = ['.pdf','.docx','.doc','.xlsx','.xls','.pptx','.ppt','.md','.txt','.png','.jpg','.jpeg']
                files_to_convert = [os.path.join(source_dir, f) for f in os.listdir(source_dir) 
                                   if os.path.splitext(f)[1].lower() in supported]
            elif file_list:
                if isinstance(file_list, str):
                    files_to_convert = [f.strip() for f in file_list.split(',')]
                else:
                    files_to_convert = list(file_list)

            if not files_to_convert:
                return {"success": False, "output": "", "message": "未找到可转换的文件"}

            if not output_dir:
                output_dir = os.path.join(os.path.dirname(files_to_convert[0]), f"converted_{target_format}")
            os.makedirs(output_dir, exist_ok=True)

            results = []
            success_count = 0
            fail_count = 0

            for src in files_to_convert:
                if not os.path.exists(src):
                    results.append({"file": src, "status": "skip", "message": "文件不存在"})
                    continue

                stem = Path(src).stem
                out_path = os.path.join(output_dir, f"{stem}.{target_format.lower().replace('.','')}")

                # 调用万能转换器
                from tool_convert_format import handler as converter
                try:
                    result_str = converter(source_path=src, target_format=target_format, output_path=out_path)
                    result = json.loads(result_str)
                    if result.get("success"):
                        success_count += 1
                        results.append({"file": src, "status": "success", "output": out_path})
                    else:
                        fail_count += 1
                        results.append({"file": src, "status": "fail", "message": result.get("message", "未知错误")})
                except Exception as e:
                    fail_count += 1
                    results.append({"file": src, "status": "fail", "message": str(e)})

            return {"success": True, "output": output_dir, "message": f"批量转换完成: {success_count}成功, {fail_count}失败, 共{len(files_to_convert)}个文件", "details": results}

        except Exception as e:
            return {"success": False, "output": "", "message": f"批量转换失败: {str(e)}"}
    

    result = convert(**kwargs)
    return json.dumps(result, ensure_ascii=False)
