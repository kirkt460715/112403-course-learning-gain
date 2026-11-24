import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def inspect_inputs(data_dir=None):
    base = Path(data_dir) if data_dir else ROOT / "data"
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    counts = {}
    for table in manifest["tables"]:
        path = base / table["file"]
        if table.get("optional") and not path.exists():
            continue
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != table["columns"]:
                raise ValueError(f"{table['file']}: unexpected columns")
            rows = list(reader)
        key = table.get("key")
        if key:
            values = [row[key] for row in rows]
            if len(values) != len(set(values)):
                raise ValueError(f"{table['file']}: duplicate {key}")
        counts[table["file"]] = len(rows)
    return counts


if __name__ == "__main__":
    for filename, count in inspect_inputs().items():
        print(f"{filename}: {count} rows")
