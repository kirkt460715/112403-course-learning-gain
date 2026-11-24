"""把分析结果渲染成教研人员可直接复用的中文 Markdown 报告。

报告结构对应教研决策顺序：
1. 本轮来源与适用范围（输入文件、哈希、课程版次、人群）
2. 数据质量与不确定性提示
3. 每个知识主题：变化依据 / 四状态计数 / 配对 / 可比性 / 建议
4. 拓印观察与家庭问卷（与知识证据分开）
5. 跨版次可比性
6. 映射更正影响追踪（若提供旧映射基线）
7. 附录：状态与原因码释义
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import REASON_LABELS_ZH, STATUS_LABELS_ZH
from .provenance import diff_mappings
from .stats import PAIR_LABELS_ZH


def build_mapping_impact(result: Dict[str, Any], baseline_mappings,
                         corrected_mappings) -> Dict[str, Any]:
    """比较映射版本，标出受影响的结论与改版建议。"""
    diff = diff_mappings(baseline_mappings, corrected_mappings)
    changed_ids = {tuple(c["mapping_id"]) for c in diff["changed"]}
    added_ids = {tuple(c["mapping_id"]) for c in diff["added"]}
    removed_ids = {tuple(c["mapping_id"]) for c in diff["removed"]}

    affected = []
    for c in result["conclusions"]:
        hits = []
        for fp in c.get("mapping_fingerprints", []):
            form, item, vfrom, vto = fp.split("|", 3)
            mid = (form, item, vfrom)
            if mid in changed_ids:
                hits.append({"mapping_id": list(mid), "impact": "映射内容被更正"})
            elif mid in removed_ids:
                hits.append({"mapping_id": list(mid), "impact": "映射被删除"})
        # 新增映射可能把题目改挂到本主题
        for mid in added_ids:
            if mid[1] in c["provenance"]["item_ids"]:
                hits.append({"mapping_id": list(mid), "impact": "出现新增映射，主题归属需复核"})
        if hits:
            c["recommendation"]["affected_by_mapping_change"] = True
            c["recommendation"].setdefault("mapping_impacts", []).extend(hits)
            affected.append({"topic": c["topic"],
                             "course_version": c["course_version"],
                             "session_ids": c["session_ids"],
                             "impacts": hits,
                             "action_required": "复查该主题的变化依据与保留/重排/重讲建议"})
    diff["affected_conclusions"] = affected
    return diff


def to_json(result: Dict[str, Any], *, input_meta: Dict[str, Any],
            mapping_diff: Optional[Dict[str, Any]] = None) -> str:
    payload = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "tool_version": _version(),
        "input_provenance": input_meta,
        "mapping_correction_impact": mapping_diff,
        **result,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _version() -> str:
    try:
        from . import __version__
        return __version__
    except Exception:
        return "unknown"


def render_markdown(result: Dict[str, Any], *, input_meta: Dict[str, Any],
                    mapping_diff: Optional[Dict[str, Any]] = None) -> str:
    lines: List[str] = []
    add = lines.append

    add("# 钱币专题研学 · 学习评估报告")
    add("")
    add(f"- 生成时间：{to_json_time()}")
    add(f"- 工具版本：{_version()}")
    add(f"- 输入目录：`{input_meta.get('data_dir')}`")
    add(f"- 输入指纹（SHA-256 前 12 位）：")
    for name, fp in input_meta.get("file_hashes", {}).items():
        add(f"  - `{name}` `{fp[:12]}`（{input_meta.get('row_counts', {}).get(name, '—')} 行/条）")
    if input_meta.get("missing_optional_files"):
        add("- 缺失的可选输入（不影响其他主题结论）："
            + "、".join(f"`{n}`" for n in input_meta["missing_optional_files"]))
    add("")
    add("> 状态口径：**未参加 / 未作答 / 答错 / 无法判断 / 答对** 严格区分。"
        "只有同一人、同一场次、同一量表版本且前后测均可判对错的配对，"
        "才用于说明真实学习变化。")
    add("")

    # ---- 参与范围 ----
    add("## 1. 参与范围（不要把签到人数当成完成整套课程的人数）")
    add("")
    add("| 场次 | 课程版次 | 主题 | 名单人数 | 独立到场 | 协助参与 | 缺席登记 |")
    add("|---|---|---|---:|---:|---:|---:|")
    for s in result["participation"]["sessions"]:
        add(f"| {s['session_id']} | {s['course_version']} | {s['topic']} | "
            f"{s['n_distinct_people_on_roster']} | {s['n_attended_any_stage']} | "
            f"{s['n_assisted']} | {s['n_absent_records']} |")
    add("")

    # ---- 数据质量 ----
    add("## 2. 数据质量与解释限制")
    add("")
    if not result["issues"]:
        add("- 未发现结构性数据问题。")
    else:
        sev_zh = {"error": "错误", "warning": "提醒", "info": "说明"}
        for i in result["issues"]:
            add(f"- **[{sev_zh.get(i['severity'], i['severity'])}] {i['message']}** "
                f"（`{i['code']}`）")
    add("")

    # ---- 家庭证据去重 ----
    add("## 3. 家庭与共同作答的证据口径")
    add("")
    add("同一家庭共同填写的内容按 **一户一条** 处理，不能当成多份独立证据；"
        "只有能归属到不同个人、且本人独立到场的作答，才作为不同个人的证据流。")
    add("")
    if result.get("household_rollup"):
        add("| 家庭 | 独立到场成员 | 有可归属作答的个人 | 个人证据流数 | 可判配对 | 配对来自人数 | 代填/合填问卷 |")
        add("|---|---|---|---:|---:|---:|---:|")
        for h in result["household_rollup"]:
            add(f"| {h['household_token']} | "
                f"{', '.join(h['attended_members']) or '—'} | "
                f"{', '.join(h['persons_with_attributable_answers']) or '—'} | "
                f"{h['n_individual_answer_streams']} | {h['n_gradeable_pairs']} | "
                f"{h['n_distinct_persons_with_pairs']} | {h['proxy_feedback_entries']} |")
        add("")
    if result["household_conflicts"]:
        add("**无法取值确定的家庭/个人答案冲突：**")
        for c in result["household_conflicts"]:
            add(f"- ⚠️ `{c['scope']}`（{c['kind']}）：答案 "
                + "；".join(f"{a}←{','.join(ps)}" for a, ps in c["answers"].items()))
        add("")
    else:
        add("- 未发现同一作答格内无法取值的答案冲突；不同家庭成员各自的答案按不同个人证据处理。")
        add("")

    # ---- 主题结论 ----
    add("## 4. 各知识主题的变化依据与改课建议")
    add("")
    for idx, c in enumerate(result["conclusions"], 1):
        _render_conclusion(add, idx, c)

    # ---- 配对明细 ----
    add("## 5. 前后测配对明细（可追溯到 response_id）")
    add("")
    all_pairs = [p for c in result["conclusions"] for p in c["pairs"]]
    if not all_pairs:
        add("本轮没有形成可判定的前后测配对。")
    else:
        add("| 人 | 家庭 | 场次 | 题目(量表) | 前测 | 后测 | 结果 | 家庭证据 | 记录 |")
        add("|---|---|---|---|---|---|---|---|---|")
        from .stats import PAIR_LABELS_ZH
        for p in all_pairs:
            outcome = PAIR_LABELS_ZH.get(p["outcome"], "不可判") if p["outcome"] else "不可判"
            indep = "独立" if p["household_independent"] else "家庭冲突/降级"
            add(f"| {p['person_token']} | {p['household_token'] or ''} | {p['session_id']} | "
                f"{p['item_id']}({p['form_version']}) | "
                f"{STATUS_LABELS_ZH[p['pre_status']]} | "
                f"{STATUS_LABELS_ZH[p['post_status']]} | {outcome} | {indep} | "
                f"{p['pre_response_id'] or '—'}→{p['post_response_id'] or '—'} |")
    add("")
    if result["pair_findings"]:
        add("**未纳入比较的配对线索：**")
        for f in result["pair_findings"]:
            add(f"- {f['person_token']} / {f['session_id']} / {f['item_id']}：{f['note']}")
        add("")

    # ---- 拓印观察 ----
    add("## 6. 拓印环节观察（操作证据，独立于知识题）")
    add("")
    obs = result["observations"]
    if not obs.get("available"):
        add(obs.get("note", "无观察数据。"))
    else:
        for sid, s in obs["sessions"].items():
            if s["status"] == "missing":
                add(f"- ⚠️ **{sid} · {s['topic']}**：{s['note']}")
            elif s["status"] == "not_applicable":
                add(f"- **{sid} · {s['topic']}**：{s['note']}")
            else:
                add(f"- **{sid} · {s['topic']}**：观察结果 {s['result_counts']}")
                for o in s["observations"]:
                    add(f"  - {o['person_token']}"
                        f"（{o['household_token'] or '无家庭'}）：`{o['result']}` —— {o['note']}")
    add("")

    # ---- 问卷 ----
    add("## 7. 活动反馈（仅家庭层面，不作学习证据）")
    add("")
    fb = result["feedback"]
    if not fb.get("available"):
        add(fb.get("note", "无反馈数据。"))
    else:
        add(fb["note"])
        if fb.get("mean_rating") is not None:
            add(f"- 家庭条数：{fb['n_household_responses']}；评分均值：{fb['mean_rating']:.2f}（不与个人成绩合并）")
        for e in fb["household_entries"]:
            add(f"  - {e['household_token']} / {e['session_id']} / {e['submitted_by']}："
                f"评分 {e['rating'] if e['rating'] is not None else '非数值'}，"
                f"把握度 {e['confidence'] or '未填'}。{e['note']}")
    add("")

    # ---- 映射更正影响 ----
    add("## 8. 课程主题映射更正的影响追踪")
    add("")
    if mapping_diff is None:
        add("本次运行未提供更正后的映射基线对比（使用 `--mapping-baseline` 可生成影响分析）。")
    else:
        add(f"- 基线映射指纹：`{mapping_diff['baseline_fingerprint']}`")
        add(f"- 当前映射指纹：`{mapping_diff['corrected_fingerprint']}`")
        if not (mapping_diff["changed"] or mapping_diff["added"] or mapping_diff["removed"]):
            add("- 两版映射完全一致，没有结论因映射更正而受影响。")
        for ch in mapping_diff["changed"]:
            mid = "/".join(ch["mapping_id"])
            add(f"- **更正** `{mid}`：主题 "
                f"“{ch['before']['topic']}”→“{ch['after']['topic']}”，"
                f"有效期至 {ch['before']['valid_to'] or '至今'}→{ch['after']['valid_to'] or '至今'}")
        for a in mapping_diff["added"]:
            add(f"- **新增** `{'/'.join(a['mapping_id'])}` → 主题“{a['topic']}”")
        for r in mapping_diff["removed"]:
            add(f"- **删除** `{'/'.join(r['mapping_id'])}`（原主题“{r['topic']}”）")
        if mapping_diff["affected_conclusions"]:
            add("")
            add("**以下已形成的学习结论与改版建议需要复查：**")
            for a in mapping_diff["affected_conclusions"]:
                add(f"- ⚠️ {a['course_version']} / “{a['topic']}” / 场次 {a['session_ids']}：{a['action_required']}")
                for imp in a["impacts"]:
                    add(f"  - `{'/'.join(imp['mapping_id'])}`：{imp['impact']}")
        else:
            add("- 没有已形成的主题结论直接引用被更正的映射。")
    add("")

    # ---- 附录 ----
    add("## 附录 A：状态与原因码释义")
    add("")
    add("| 状态 | 含义 |")
    add("|---|---|")
    for k, zh in STATUS_LABELS_ZH.items():
        desc = {
            "correct": "有效答案且在有效期答案键下判定正确",
            "wrong": "有效答案且可判定为错误——是真实的答错证据",
            "not_answered": "独立到场，但无提交或答案为空",
            "not_attended": "缺席、无该环节签到，或仅为协助参与",
            "indeterminate": "有材料但映射/答案键/签到/重复提交等导致不能判断",
        }[k]
        add(f"| {zh}（`{k}`） | {desc} |")
    add("")
    add("| 原因码 | 中文说明 |")
    add("|---|---|")
    for code, zh in REASON_LABELS_ZH.items():
        add(f"| `{code}` | {zh} |")
    add("| `no_instrument_for_session` | 该场没有任何测量题目，学习变化无工具可测 |")
    add("")
    add("## 附录 B：复用方式")
    add("")
    add("1. 新一轮资料按 `data/manifest.json` 的列约定放入新目录（建议 `rounds/YYYY-roundN/`）。")
    add("2. 运行：`python3 -m assessment.cli --data <资料目录> --out reports/`。")
    add("3. 若课程主题映射被更正，保存旧版 `measurements.csv`，加 "
        "`--mapping-baseline <旧measurements.csv>`，报告第 8 节会指出受影响结论。")
    add("4. 阈值可用 `--min-pairs` 调整（默认 3；低于阈值只给谨慎建议）。")
    add("")
    return "\n".join(lines)


def _render_conclusion(add, idx, c: Dict[str, Any]):
    rec = c["recommendation"]
    flag = " ⚠️**引用了已更正映射，需复查**" if rec.get("affected_by_mapping_change") else ""
    add(f"### 4.{idx} 【{c['course_version']}】{c['topic']}{flag}")
    add("")
    taught = "是" if c.get("is_formally_taught_this_version") else "否（本版次无同主题场次）"
    add(f"- 映射证据场次：{', '.join(c['session_ids'])}；对应环节：`{c['stage']}`；"
        f"本版次正式排课讲授该主题：**{taught}**")
    pop = c["population"]
    add(f"- 适用人群：独立到场 **{pop['n_attended_independent']}** 人"
        f"（{', '.join(pop['attended_independent_persons']) or '无'}），"
        f"协助参与 {pop['n_assisted']} 人，覆盖家庭 {pop['n_households']} 户")
    sc = c["status_counts_zh"]
    add("- 证据格状态：" + "，".join(f"{zh} **{sc[zh]}**" for zh in sc))
    ps = c["pair_summary"]
    add(f"- 可判定前后测配对：**{ps['n_gradeable_pairs']}** 对；"
        f"结果分布 {ps['outcomes']}")
    if ps["improvement_rate"] is not None:
        ci = ps.get("wilson_improvement")
        ci_text = f"，Wilson95% 区间 {ci['low']:.0%}–{ci['high']:.0%}" if ci else ""
        add(f"- 错→对改善率：**{ps['improvement_rate']:.0%}**{ci_text}")
    else:
        add("- 错→对改善率：**无法计算**（没有两侧均可判的配对）")

    mm_pairs = c.get("topic_mismatch_pairs", [])
    if mm_pairs:
        gradeable_mm = [p for p in mm_pairs if p.get("outcome")]
        add(f"- 另有 **{len(mm_pairs)}** 个“题目测本主题、作答却发生在其他主题场次”"
            f"的错位配对线索（其中 {len(gradeable_mm)} 对两侧可判），均不计入上方变化率：")
        for p in mm_pairs:
            outcome_zh = PAIR_LABELS_ZH.get(p["outcome"], "至少一侧不可判") if p.get("outcome") else "至少一侧不可判"
            add(f"  - {p['person_token']} / {p['session_id']} / {p['item_id']}："
                f"{STATUS_LABELS_ZH[p['pre_status']]}→{STATUS_LABELS_ZH[p['post_status']]}"
                f"（{outcome_zh}）")

    add("- 测量来源：")
    for w in c["provenance"]["mapping_windows"] or ["（无有效映射窗口）"]:
        add(f"  - `{w}`")
    for ic in c["item_coverage"]:
        add(f"  - 题目 `{ic['item_id']}` / 量表 `{ic['form_version']}` / "
            f"映射有效期 {ic['mapping_valid_from'] or '?'}~{ic['mapping_valid_to'] or '至今'}："
            f"{ic['status_counts_zh']}")

    comp = c["comparability"]
    scope_zh = ('当前存在可做版内比较的配对' if comp['comparable_within_version']
                else '当前没有可判定配对，尚不能做版内变化比较')
    add(f"- 可比较范围：{comp['scope_label']}；{scope_zh}")
    for n in comp["notes"]:
        add(f"  - 可比性说明：{n}")

    if c["uncertainties"]:
        add("- 不确定性与排除依据：")
        for u in c["uncertainties"]:
            add(f"  - {u}")

    add(f"- **教研建议：{rec['label_zh']}**（`{rec['action']}`）")
    for b in rec["basis"]:
        add(f"  - 依据：{b}")
    for s in rec.get("secondary_signals", []):
        add(f"  - 次要线索（不足以单独决策）：{s}")
    if rec.get("mapping_impacts"):
        for m in rec["mapping_impacts"]:
            add(f"  - 映射影响：`{'/'.join(m['mapping_id'])}` {m['impact']}")
    add("")


def to_json_time() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
