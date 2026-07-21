"""Javis自创: cat_converter — CAT翻译格式转换器(TMX/TBX/XLIFF/SDLXLIFF)"""
TOOL_NAME="cat_converter"
TOOL_DESC="计算机辅助翻译(CAT)格式文件转换器。支持TMX/TBX/XLIFF/SDLXLIFF → CSV/XLSX/TXT/JSON。参数: cat_path(输入路径), output_format(csv/xlsx/txt/json,默认xlsx), output_path(可选输出路径)"
TOOL_CATEGORY="document_convert"
TOOL_PARAMS={"type":"object","properties":{},"required":[]}

def handler(**kwargs):
    import os, json
    from pathlib import Path

    def convert(cat_path=None, output_format="xlsx", output_path=None, **kw):
        if not cat_path or not os.path.exists(cat_path):
            return {"success": False, "output": "", "message": f"文件不存在: {cat_path}"}

        ext = os.path.splitext(cat_path)[1].lower()
        if not output_path:
            output_path = str(Path(cat_path).with_suffix(f'.{output_format.lower()}'))

        try:
            import xml.etree.ElementTree as ET

            entries = []  # 统一结构: [{source, target, context, metadata}]

            # === TMX (Translation Memory eXchange) ===
            if ext in ['.tmx']:
                tree = ET.parse(cat_path)
                root = tree.getroot()
                ns = {'xml': 'http://www.w3.org/XML/1998/namespace'}
                for tu in root.iter('tu'):
                    src = ''
                    tgt = ''
                    for tuv in tu.iter('tuv'):
                        lang = tuv.get('{http://www.w3.org/XML/1998/namespace}lang', '')
                        seg = tuv.find('seg')
                        text = ''.join(seg.itertext()).strip() if seg is not None else ''
                        if not src:
                            src = text
                        else:
                            tgt = text
                    if src or tgt:
                        entries.append({'source': src, 'target': tgt, 'context': '', 'metadata': ''})

            # === TBX (TermBase eXchange) ===
            elif ext in ['.tbx', '.tbx.xml']:
                tree = ET.parse(cat_path)
                root = tree.getroot()
                for term_entry in root.iter('termEntry'):
                    tid = term_entry.get('id', '')
                    terms = {}
                    for lang_set in term_entry.iter('langSet'):
                        lang = lang_set.get('{http://www.w3.org/XML/1998/namespace}lang', '')
                        term_el = lang_set.find('tig/term')
                        if term_el is not None:
                            terms[lang] = term_el.text.strip() if term_el.text else ''
                    # 取前两个语言作为源/目标
                    langs = list(terms.keys())
                    src = terms.get(langs[0], '') if langs else ''
                    tgt = terms.get(langs[1], '') if len(langs) > 1 else ''
                    entries.append({'source': src, 'target': tgt, 'context': f'ID:{tid}', 'metadata': str(terms)})

            # === XLIFF (XML Localization Interchange File Format) ===
            elif ext in ['.xliff', '.xlf']:
                tree = ET.parse(cat_path)
                root = tree.getroot()
                # XLIFF 1.2
                for trans_unit in root.iter('trans-unit'):
                    sid = trans_unit.get('id', '')
                    source_el = trans_unit.find('source')
                    target_el = trans_unit.find('target')
                    src = ''.join(source_el.itertext()).strip() if source_el is not None else ''
                    tgt = ''.join(target_el.itertext()).strip() if target_el is not None else ''
                    entries.append({'source': src, 'target': tgt, 'context': f'ID:{sid}', 'metadata': ''})
                # XLIFF 2.0
                for unit in root.iter('unit'):
                    uid = unit.get('id', '')
                    seg = unit.find('segment')
                    if seg is not None:
                        source_el = seg.find('source')
                        target_el = seg.find('target')
                        src = ''.join(source_el.itertext()).strip() if source_el is not None else ''
                        tgt = ''.join(target_el.itertext()).strip() if target_el is not None else ''
                        entries.append({'source': src, 'target': tgt, 'context': f'ID:{uid}', 'metadata': ''})

            # === SDLXLIFF (SDL Trados Studio) ===
            elif ext in ['.sdlxliff']:
                tree = ET.parse(cat_path)
                root = tree.getroot()
                ns = {'sdl': 'http://sdl.com/FileTypes/SdlXliff/1.0'}
                for trans_unit in root.iter('trans-unit'):
                    sid = trans_unit.get('id', '')
                    source_el = trans_unit.find('source')
                    target_el = trans_unit.find('target')
                    src = ''.join(source_el.itertext()).strip() if source_el is not None else ''
                    tgt = ''.join(target_el.itertext()).strip() if target_el is not None else ''
                    entries.append({'source': src, 'target': tgt, 'context': f'ID:{sid}', 'metadata': 'SDL Trados'})

            else:
                return {"success": False, "output": "",
                        "message": f"不支持的CAT格式: {ext}。支持: TMX/TBX/XLIFF/SDLXLIFF"}

            if not entries:
                return {"success": False, "output": "", "message": "未找到翻译条目，文件可能为空或格式不正确"}

            # 输出
            output_format = output_format.lower()
            if output_format == 'xlsx':
                import pandas as pd
                df = pd.DataFrame(entries)
                df.to_excel(output_path, index=False, engine='openpyxl')
            elif output_format == 'csv':
                import csv
                with open(output_path, 'w', encoding='utf-8-sig', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=['source', 'target', 'context', 'metadata'])
                    writer.writeheader()
                    writer.writerows(entries)
            elif output_format == 'json':
                with open(output_path, 'w', encoding='utf-8') as f:
                    json.dump(entries, f, ensure_ascii=False, indent=2)
            elif output_format in ['txt', 'text']:
                with open(output_path, 'w', encoding='utf-8') as f:
                    for e in entries:
                        f.write(f"源: {e['source']}\n")
                        f.write(f"译: {e['target']}\n")
                        if e['context']:
                            f.write(f"   [{e['context']}]\n")
                        f.write("\n")

            return {"success": True, "output": output_path,
                    "message": f"CAT文件转换完成: {len(entries)}条翻译对 → {output_format.upper()}",
                    "entry_count": len(entries), "format": ext}

        except ET.ParseError as e:
            return {"success": False, "output": "", "message": f"XML解析失败: {str(e)}"}
        except Exception as e:
            return {"success": False, "output": "", "message": f"转换失败: {str(e)}"}

    result = convert(**kwargs)
    return json.dumps(result, ensure_ascii=False)
