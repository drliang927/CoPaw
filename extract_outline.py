#!/usr/bin/env python3
"""
大纲提取算法 - 从处理后的日志中提取关键结构信息

功能：
1. 提取 stepName, treeId, status, action 字段
2. 输出 JSON 和 YAML 两种格式用于对比
3. 提取 ERROR 日志，并关联 invoke python 中的 componentName
"""

import json
import os
import re
from pathlib import Path


def compress_traceback(content: str) -> str:
    """
    压缩超长的 Python Traceback 堆栈信息
    
    压缩策略：
    1. 去掉文件完整路径，只保留文件名
    2. 去掉代码片段行
    3. 去掉 ^^^^^ 指示符
    4. 简化请求对象，去掉 session、内存地址等无用信息
    """
    if not content or "Traceback" not in content:
        return content
    
    result = content
    
    # 去掉 session 对象
    result = re.sub(r"'session': <requests\.sessions\.Session object at 0x[0-9a-fA-F]+>,?\s*", '', result)
    # 简化 RequestMethod
    result = re.sub(r"<RequestMethod\.(\w+): '[^']+'>", r"'\1'", result)
    # 简化 Response
    result = re.sub(r"'response': <Response \[(\d+)\]>", r"'status_code': \1", result)
    
    # 去掉代码片段行：\n    代码内容（紧跟在 File 行后面，有缩进但不是 File 开头）
    # 匹配 \n + 2空格以上 + 非File开头 + 代码特征，去掉整行
    result = re.sub(r'\\n    [a-zA-Z_\(\[][^\\]*?(?=\\n)', '', result)
    
    # 去掉 ^^^^^ 指示符行
    result = re.sub(r'\\n\s+\^+', '', result)
    
    # 压缩文件路径：File "完整路径" -> File "文件名"
    def shorten_path(match):
        full_path = match.group(1)
        line_num = match.group(2)
        func_name = match.group(3)
        # 处理 Windows 路径
        filename = os.path.basename(full_path.replace('\\\\', '/').replace('\\', '/'))
        return f'File "{filename}", line {line_num}, in {func_name}'
    
    result = re.sub(r'File "([^"]+)", line (\d+), in (\S+)', shorten_path, result)
    
    return result


def deep_unescape_json(data):
    """
    递归解包深度嵌套的转义 JSON
    
    利用 json.loads() 自动处理转义字符的特性，逐层脱壳
    绝不使用 string.replace() 或正则手动替换
    """
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            result[key] = deep_unescape_json(value)
        return result
    elif isinstance(data, list):
        return [deep_unescape_json(item) for item in data]
    elif isinstance(data, str):
        stripped = data.strip()
        # 检查是否看起来像 JSON 对象或数组
        if (stripped.startswith('{') and stripped.endswith('}')) or \
           (stripped.startswith('[') and stripped.endswith(']')):
            try:
                parsed = json.loads(stripped, strict=False)
                # 递归处理解析后的结果
                return deep_unescape_json(parsed)
            except json.JSONDecodeError:
                # 解析失败，说明只是普通文本，保持原样
                return data
        return data
    else:
        return data


def extract_invoke_python_info(step_logs):
    """
    从 stepLog 中提取 invoke python 的信息
    
    返回: 包含 traceId 和 content 的列表
    """
    invoke_info_list = []
    
    for log in step_logs:
        content = log.get("content", "")
        trace_id = log.get("traceId", "")
        
        # 查找 invoke python: 开头的内容
        if content.startswith("invoke python:"):
            # 第一层提取：剥离非 JSON 外壳
            json_start = content.find('{')
            json_end = content.rfind('}')
            
            if json_start != -1 and json_end != -1 and json_end > json_start:
                json_str = content[json_start:json_end + 1]
                try:
                    # 第一次解析，开启 strict=False 兼容控制字符
                    parsed = json.loads(json_str, strict=False)
                    # 递归深度解包
                    parsed = deep_unescape_json(parsed)
                    
                    invoke_info_list.append({
                        "traceId": trace_id,
                        "content": parsed
                    })
                except json.JSONDecodeError:
                    pass
    
    return invoke_info_list


def extract_step_outline(step_entry):
    """
    从单个步骤条目中提取大纲信息
    """
    outline = {
        "stepName": step_entry.get("stepName", ""),
        "treeId": step_entry.get("treeId", ""),
        "status": step_entry.get("status", ""),
        "action": step_entry.get("action", "")
    }

    step_logs = step_entry.get("stepLog", [])
    
    # 提取 invoke python 信息
    invoke_info = extract_invoke_python_info(step_logs)
    
    # 提取 logLevel 为 ERROR 的 stepLog，并压缩堆栈信息
    error_logs = [
        {
            "traceId": log.get("traceId", ""),
            "content": compress_traceback(log.get("content", "")),
            "logLevel": log.get("logLevel", ""),
            "createTime": log.get("createTime", "")
        }
        for log in step_logs
        if log.get("logLevel") == "ERROR"
    ]
    
    # 在 action 后面添加 component 字段（包含 traceId 和 content）
    if invoke_info:
        outline["component"] = invoke_info
    
    if error_logs:
        outline["errorLogs"] = error_logs

    # 递归处理 stepChildren
    if "stepChildren" in step_entry and step_entry["stepChildren"]:
        outline["stepChildren"] = [
            extract_step_outline(child) for child in step_entry["stepChildren"]
        ]

    return outline


def extract_outline_from_file(input_file_path):
    """
    从处理后的日志文件中提取大纲
    """
    print(f"提取文件: {input_file_path}")

    with open(input_file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 提取所有步骤的大纲
    outlines = [extract_step_outline(entry) for entry in data]

    return outlines


def save_as_json(data, output_file_path):
    """
    保存为 JSON 格式
    """
    with open(output_file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  JSON: {output_file_path}")


def save_as_yaml(data, output_file_path):
    """
    保存为 YAML 格式
    """
    try:
        import yaml
        with open(output_file_path, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, indent=2, sort_keys=False)
        print(f"  YAML: {output_file_path}")
    except ImportError:
        print("  警告: 未安装 PyYAML，跳过 YAML 输出")
        print("  可运行: pip install pyyaml")


def extract_all_outlines(input_dir="rcalogs_processed", output_dir="outline_extracted"):
    """
    提取 input_dir 下所有处理后的日志文件的大纲
    """
    input_path = Path(input_dir)
    output_path = Path(output_dir)

    # 创建输出目录
    output_path.mkdir(parents=True, exist_ok=True)

    # 获取所有 JSON 文件
    json_files = sorted(input_path.glob("*.json"))

    print(f"找到 {len(json_files)} 个处理后的日志文件")
    print("=" * 50)

    for json_file in json_files:
        try:
            outlines = extract_outline_from_file(json_file)

            # 生成输出文件名
            base_name = json_file.stem.replace("_processed", "")

            # 保存 JSON 格式
            json_output = output_path / f"{base_name}_outline.json"
            save_as_json(outlines, json_output)

            # 保存 YAML 格式
            yaml_output = output_path / f"{base_name}_outline.yaml"
            save_as_yaml(outlines, yaml_output)

        except Exception as e:
            print(f"  错误: {e}")

    print("=" * 50)
    print(f"提取完成，结果保存在: {output_path}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="提取日志大纲")
    parser.add_argument("-i", "--input", default="rcalogs_processed", help="输入目录")
    parser.add_argument("-o", "--output", default="outline_extracted", help="输出目录")

    args = parser.parse_args()
    extract_all_outlines(args.input, args.output)
