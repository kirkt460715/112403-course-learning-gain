"""命令行入口：python3 -m assessment.cli --data data --out reports

每轮新资料只需复制/更新一个资料目录并重新运行；输出同时包含
Markdown（教研阅读）与 JSON（后续工具复用/留档）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from .analyze import analyze, DEFAULT_MIN_PAIRS
from .classify import classify_all
from .io import InputContractError, load_inputs
from .provenance import build_mappings
from .report import build_mapping_impact, render_markdown, to_json


def run(data_dir: str | Path, out_dir: Optional[str | Path] = None,
        mapping_baseline: Optional[str | Path] = None,
        min_pairs: int = DEFAULT_MIN_PAIRS):
    data = load_inputs(data_dir)
    classified = classify_all(data)
    classified["data"] = data
    result = analyze(classified, min_pairs=min_pairs)

    input_meta = {
        "data_dir": str(data.data_dir.resolve()),
        "present_files": data.present_files,
        "missing_optional_files": data.missing_optional_files,
        "file_hashes": data.file_hashes,
        "row_counts": {**{n: c for n, c in data.file_row_counts.items()},
                       "manifest.json": len(data.manifest["tables"])},
        "manifest": data.manifest,
    }

    mapping_diff = None
    if mapping_baseline:
        baseline_path = Path(mapping_baseline)
        baseline_rows = _read_csv_rows(baseline_path)
        baseline = build_mappings(baseline_rows)
        mapping_diff = build_mapping_impact(result, baseline, classified["mappings"])

    markdown = render_markdown(result, input_meta=input_meta,
                               mapping_diff=mapping_diff)
    payload = to_json(result, input_meta=input_meta, mapping_diff=mapping_diff)

    outputs = {}
    if out_dir:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        md_path = out / "learning_report.md"
        json_path = out / "learning_report.json"
        md_path.write_text(markdown, encoding="utf-8")
        json_path.write_text(payload, encoding="utf-8")
        outputs["markdown"] = str(md_path)
        outputs["json"] = str(json_path)

    return {"markdown": markdown, "json": payload, "outputs": outputs,
            "result": result, "mapping_diff": mapping_diff}


def _read_csv_rows(path: Path):
    import csv
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="钱币专题研学学习评估工具（本地、无第三方依赖）")
    parser.add_argument("--data", default="data", help="资料目录（含 manifest.json）")
    parser.add_argument("--out", default="reports",
                        help="报告输出目录；传空字符串可只打印 Markdown")
    parser.add_argument("--mapping-baseline", default=None,
                        help="更正前的旧版 measurements.csv，用于影响追踪")
    parser.add_argument("--min-pairs", type=int, default=DEFAULT_MIN_PAIRS,
                        help=f"形成较强结论所需的最小可判配对数（默认 {DEFAULT_MIN_PAIRS}）")
    args = parser.parse_args(argv)

    try:
        run_result = run(args.data, args.out or None,
                         args.mapping_baseline, args.min_pairs)
    except InputContractError as exc:
        print(f"输入契约错误：{exc}", file=sys.stderr)
        return 2

    if not args.out:
        print(run_result["markdown"])
    else:
        print(f"Markdown 报告：{run_result['outputs']['markdown']}")
        print(f"结构化结果：{run_result['outputs']['json']}")
        n_conc = len(run_result["result"]["conclusions"])
        n_iss = len(run_result["result"]["issues"])
        print(f"主题结论 {n_conc} 条；数据问题 {n_iss} 项。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
