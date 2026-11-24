"""CSV/JSON 输入加载、结构校验与输入来源指纹。

设计原则：
- 清单（manifest.json）声明表与列；optional 表缺失时跳过而不是报错，
  这样“一个活动的观察缺失不会抹掉其他主题的结论”。
- 结构问题（缺列、主键重复、清单内必填文件缺失）直接抛出，避免静默误算。
- 数据内容问题（孤儿作答、未知状态等）不在这里拦，交给分析层记为 Issue。
"""
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


class InputContractError(ValueError):
    pass


@dataclass
class InputData:
    data_dir: Path
    manifest: Dict[str, Any]
    tables: Dict[str, List[Dict[str, str]]] = field(default_factory=dict)
    present_files: List[str] = field(default_factory=list)
    missing_optional_files: List[str] = field(default_factory=list)
    file_hashes: Dict[str, str] = field(default_factory=dict)
    file_row_counts: Dict[str, int] = field(default_factory=dict)

    def rows(self, filename: str) -> List[Dict[str, str]]:
        return self.tables.get(filename, [])


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_inputs(data_dir: str | Path) -> InputData:
    base = Path(data_dir)
    manifest_path = base / "manifest.json"
    if not manifest_path.exists():
        raise InputContractError(f"找不到清单文件: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    data = InputData(data_dir=base, manifest=manifest)
    # 清单本身也纳入来源指纹
    data.file_hashes["manifest.json"] = _sha256_of(manifest_path)

    for table in manifest["tables"]:
        filename = table["file"]
        path = base / filename
        optional = bool(table.get("optional"))
        if not path.exists():
            if optional:
                data.missing_optional_files.append(filename)
                continue
            raise InputContractError(f"缺少必填输入文件: {filename}")

        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            columns = reader.fieldnames or []
            if columns != table["columns"]:
                raise InputContractError(
                    f"{filename}: 列不匹配\n  期望 {table['columns']}\n  实际 {columns}"
                )
            rows = list(reader)

        key = table.get("key")
        if key:
            values = [row[key] for row in rows]
            if len(values) != len(set(values)):
                dupes = sorted({v for v in values if values.count(v) > 1})
                raise InputContractError(f"{filename}: 主键 {key} 重复: {dupes}")

        data.tables[filename] = rows
        data.present_files.append(filename)
        data.file_hashes[filename] = _sha256_of(path)
        data.file_row_counts[filename] = len(rows)

    return data
