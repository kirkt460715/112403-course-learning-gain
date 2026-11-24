# 示例：课程主题映射更正后的影响追踪

当前 `data/measurements.csv` 是**更正后**的映射：Q2 仍挂在“晋商信用”。
`mapping_baseline_v1.csv` 演示一次映射更正前的留档版本。

运行：

```bash
python3 -m assessment.cli --data data --out reports \
  --mapping-baseline examples/mapping_baseline_v1.csv
```

如果教研组把 Q2 从“晋商信用”更正为“先秦货币”，应：

1. 把旧文件另存（例如本轮的 `mapping_baseline_v1.csv`，其内容即更正前状态）；
2. 修改 `data/measurements.csv` 中 F2/Q2 的 topic；
3. 重新运行并传入 `--mapping-baseline`。

报告第 8 节与 JSON 的 `mapping_correction_impact` 会列出：

- 哪条 `(form_version, item_id, valid_from)` 映射被更正；
- 哪些主题结论与保留/重排/重讲建议引用了它，需要复查；
- 哪些结论不受影响（例如 S01 基于 F1/Q1 的结论不会被 F2/Q2 的更正抹掉）。
