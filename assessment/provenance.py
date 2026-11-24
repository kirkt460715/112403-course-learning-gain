"""测量映射与答案键的版本化、有效期判断及更正影响追踪。

映射身份：(form_version, item_id, valid_from)。
映射指纹：对映射的全部判定字段做规范化哈希，用于判断“结论形成后映射是否被更正”。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Tuple


def parse_date(value: str) -> Optional[date]:
    value = (value or "").strip()
    if not value:
        return None
    return date.fromisoformat(value[:10])


def within_window(day: Optional[date], valid_from: str, valid_to: str) -> bool:
    """day 为空（作答没有可解析日期）时，无法确认是否在有效期 -> False。"""
    if day is None:
        return False
    start = parse_date(valid_from)
    end = parse_date(valid_to)
    if start and day < start:
        return False
    if end and day > end:
        return False
    return True


@dataclass(frozen=True)
class Mapping:
    form_version: str
    item_id: str
    topic: str
    valid_from: str
    valid_to: str

    @property
    def mid(self) -> Tuple[str, str, str]:
        return (self.form_version, self.item_id, self.valid_from)

    def fingerprint(self) -> str:
        payload = json.dumps(
            {"form_version": self.form_version, "item_id": self.item_id,
             "topic": self.topic, "valid_from": self.valid_from,
             "valid_to": self.valid_to},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class KeyEntry:
    form_version: str
    item_id: str
    correct_answer: str
    key_version: str
    valid_from: str
    valid_to: str

    def matches(self, day: Optional[date]) -> bool:
        return within_window(day, self.valid_from, self.valid_to)


def build_mappings(rows: List[Dict[str, str]]) -> List[Mapping]:
    return [
        Mapping(r["form_version"].strip(), r["item_id"].strip(),
                r["topic"].strip(), r["valid_from"].strip(), r["valid_to"].strip())
        for r in rows
    ]


def build_keys(rows: List[Dict[str, str]]) -> List[KeyEntry]:
    return [
        KeyEntry(r["form_version"].strip(), r["item_id"].strip(),
                 r["correct_answer"].strip(), r.get("key_version", "").strip(),
                 r["valid_from"].strip(), r["valid_to"].strip())
        for r in rows
    ]


def mapping_for(mappings: List[Mapping], form_version: str, item_id: str,
                day: Optional[date]) -> Tuple[Optional[Mapping], bool]:
    """返回 (映射, 是否存在同题映射但全部不在有效期)。

    若恰有一条在有效期，返回它；多条同时有效属于输入冲突，取第一条并由
    调用方记录 Issue（见 classify）。
    """
    candidates = [m for m in mappings
                  if m.form_version == form_version and m.item_id == item_id]
    active = [m for m in candidates if within_window(day, m.valid_from, m.valid_to)]
    if active:
        return active[0], len(active) > 1
    if candidates:
        return None, False  # 有映射但过期/未生效
    return None, False


def key_for(keys: List[KeyEntry], form_version: str, item_id: str,
            day: Optional[date]) -> Optional[KeyEntry]:
    active = [k for k in keys
              if k.form_version == form_version and k.item_id == item_id
              and k.matches(day)]
    return active[0] if active else None


def mappings_fingerprint(mappings: List[Mapping]) -> str:
    """整份测量映射的规范指纹，写入结论来源。"""
    payload = [
        {"id": list(m.mid), "topic": m.topic, "to": m.valid_to,
         "fp": m.fingerprint()}
        for m in sorted(mappings, key=lambda m: m.mid)
    ]
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def diff_mappings(baseline: List[Mapping],
                  corrected: List[Mapping]) -> Dict[str, Any]:
    """比较两版映射，指出被更正/新增/删除的映射身份。

    返回的 changed_ids 即“哪些结论与改版建议需要复查”的索引依据。
    """
    old = {m.mid: m for m in baseline}
    new = {m.mid: m for m in corrected}
    changed: List[Dict[str, Any]] = []
    for mid in sorted(set(old) & set(new)):
        if old[mid].fingerprint() != new[mid].fingerprint():
            changed.append({
                "mapping_id": list(mid),
                "kind": "corrected",
                "before": {"topic": old[mid].topic,
                           "valid_to": old[mid].valid_to,
                           "fingerprint": old[mid].fingerprint()},
                "after": {"topic": new[mid].topic,
                          "valid_to": new[mid].valid_to,
                          "fingerprint": new[mid].fingerprint()},
            })
    added = [{"mapping_id": list(mid), "kind": "added",
              "topic": new[mid].topic, "fingerprint": new[mid].fingerprint()}
             for mid in sorted(set(new) - set(old))]
    removed = [{"mapping_id": list(mid), "kind": "removed",
                "topic": old[mid].topic, "fingerprint": old[mid].fingerprint()}
               for mid in sorted(set(old) - set(new))]
    return {"changed": changed, "added": added, "removed": removed,
            "baseline_fingerprint": mappings_fingerprint(baseline),
            "corrected_fingerprint": mappings_fingerprint(corrected)}
