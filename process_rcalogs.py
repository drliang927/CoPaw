#!/usr/bin/env python3
"""
处理 rcalogs 目录下的 JSON 格式日志

功能：
1. 保留字段: stepName, treeId, status, action, stepLog
2. 精简 stepLog: 只保留 traceId, content, logLevel, createTime
3. 对 treeId 和 traceId 进行归一化（相同 ID 映射到相同序号，从 0 开始）
"""

import json
import os
from pathlib import Path


def normalize_id(id_value, id_mapping):
    """
    将原始 ID 映射到归一化的序号
    如果 ID 已存在映射，返回已有的序号
    否则分配新的序号
    """
    id_str = str(id_value)
    if id_str not in id_mapping:
        id_mapping[id_str] = len(id_mapping)
    return id_mapping[id_str]


def process_step_log(step_logs, trace_id_mapping):
    """
    处理 stepLog 列表，精简字段并归一化 traceId
    """
    processed_logs = []
    for log_entry in step_logs:
        processed_log = {
            "traceId": normalize_id(log_entry.get("traceId", ""), trace_id_mapping),
            "content": log_entry.get("content", ""),
            "logLevel": log_entry.get("logLevel", ""),
            "createTime": log_entry.get("createTime", "")
        }
        processed_logs.append(processed_log)
    return processed_logs


def process_step_entry(entry, tree_id_mapping, trace_id_mapping):
    """
    处理单个步骤条目
    """
    # 归一化 treeId
    normalized_tree_id = normalize_id(entry.get("treeId", ""), tree_id_mapping)

    # 处理 stepLog
    step_logs = entry.get("stepLog", [])
    processed_step_log = process_step_log(step_logs, trace_id_mapping)

    # 构建保留字段的结果
    result = {
        "stepName": entry.get("stepName", ""),
        "treeId": normalized_tree_id,
        "status": entry.get("status", ""),
        "action": entry.get("action", ""),
        "stepLog": processed_step_log
    }

    # 递归处理 stepChildren（如果存在）
    if "stepChildren" in entry and entry["stepChildren"]:
        result["stepChildren"] = [
            process_step_entry(child, tree_id_mapping, trace_id_mapping)
            for child in entry["stepChildren"]
        ]

    return result


def process_log_file(input_file_path, output_file_path=None):
    """
    处理单个日志文件
    """
    print(f"处理文件: {input_file_path}")

    # 读取原始日志
    try:
        with open(input_file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"  警告: 读取文件错误 ({e})，跳过该文件")
        return None, {}, {}

    # 预处理：移除 Windows 换行符
    content = content.replace('\r\n', '\n').replace('\r', '\n')

    # 尝试直接解析
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        # 尝试修复损坏的 JSON
        import re
        fixed_content = content

        # 模式1: "key": "***敏感信息系统已自动屏蔽***数字" -> "key": "数字"
        fixed_content = re.sub(
            r'"\*+[^*]*\*+(\d+)"',
            r'"\1"',
            fixed_content
        )

        # 模式2: "key": ***敏感信息系统已自动屏蔽***数字, -> "key": 数字,
        # 匹配中文的敏感信息标记
        fixed_content = re.sub(
            r':\s*\*+[^,\n\]]*\*+(\d+)',
            r': \1',
            fixed_content
        )

        # 修复前导零数字问题 (如 00 -> 0, 01 -> 1)
        fixed_content = re.sub(r':\s+0+(\d+)([,\]\n}])', r': \1\2', fixed_content)

        # 模式3: key 中包含敏感信息 "***xxx***敏感信息系统已自动屏蔽***xxx"
        fixed_content = re.sub(
            r'"([^"]*)\*+[^*]*\*+([^"]*)"',
            r'"\1\2"',
            fixed_content
        )

        try:
            data = json.loads(fixed_content)
            print(f"  修复后成功解析")
        except json.JSONDecodeError as e:
            # 打印问题位置附近的内容帮助调试
            error_pos = e.pos if hasattr(e, 'pos') and e.pos else 0
            start = max(0, error_pos - 50)
            end = min(len(fixed_content), error_pos + 50)
            print(f"  警告: JSON 解析错误 ({e})")
            print(f"  问题位置内容: ...{repr(fixed_content[start:end])}...")
            print(f"  跳过该文件")
            return None, {}, {}

    # 创建 ID 映射字典
    tree_id_mapping = {}  # treeId -> 归一化序号
    trace_id_mapping = {}  # traceId -> 归一化序号

    # 处理每一条记录
    processed_data = []
    for entry in data:
        processed_entry = process_step_entry(entry, tree_id_mapping, trace_id_mapping)
        processed_data.append(processed_entry)

    # 如果没有指定输出文件，生成默认输出文件名
    if output_file_path is None:
        input_path = Path(input_file_path)
        output_file_path = input_path.parent / f"{input_path.stem}_processed{input_path.suffix}"

    # 写入处理后的日志
    with open(output_file_path, 'w', encoding='utf-8') as f:
        json.dump(processed_data, f, ensure_ascii=False, indent=2)

    print(f"处理完成，输出文件: {output_file_path}")
    print(f"  - treeId 映射数量: {len(tree_id_mapping)}")
    print(f"  - traceId 映射数量: {len(trace_id_mapping)}")

    return processed_data, tree_id_mapping, trace_id_mapping


def process_all_logs(input_dir="rcalogs", output_dir="rcalogs_processed"):
    """
    处理 input_dir 下的所有 JSON 日志文件，结果输出到 output_dir
    """
    input_path = Path(input_dir)
    output_path = Path(output_dir)

    # 创建输出目录
    output_path.mkdir(parents=True, exist_ok=True)

    # 获取所有 JSON 文件
    json_files = sorted(input_path.glob("*.json"))

    print(f"找到 {len(json_files)} 个日志文件")
    print("=" * 50)

    all_tree_mappings = {}
    all_trace_mappings = {}

    for json_file in json_files:
        output_file = output_path / f"{json_file.stem}_processed{json_file.suffix}"
        result, tree_mapping, trace_mapping = process_log_file(json_file, output_file)

        if result is None:
            continue

        # 合并映射（跨文件保持一致的归一化）
        for k, v in tree_mapping.items():
            if k not in all_tree_mappings:
                all_tree_mappings[k] = v
        for k, v in trace_mapping.items():
            if k not in all_trace_mappings:
                all_trace_mappings[k] = v

    print("=" * 50)
    print(f"所有文件处理完成，结果保存在: {output_path}")
    print(f"总 treeId 映射数量: {len(all_tree_mappings)}")
    print(f"总 traceId 映射数量: {len(all_trace_mappings)}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="处理 RCA 日志文件")
    parser.add_argument("-i", "--input", default="rcalogs", help="输入目录 (默认: rcalogs)")
    parser.add_argument("-o", "--output", default="rcalogs_processed", help="输出目录 (默认: rcalogs_processed)")
    parser.add_argument("-f", "--file", help="处理单个文件")

    args = parser.parse_args()

    if args.file:
        # 处理单个文件
        process_log_file(args.file)
    else:
        # 处理目录下所有文件
        process_all_logs(args.input, args.output)
