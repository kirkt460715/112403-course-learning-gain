"""主题级学习变化分析。

输出的每个主题结论都带：
- 来源：场次、课程版次、测量/答案键版本与映射指纹
- 适用人群：该场该环节签到人数、独立到场人数、家庭数
- 可比较范围：同量表版本的可判配对；跨版本只作描述性并列
- 不确定性：四状态计数、排除原因、样本量、Wilson 区间
- 后续建议：保留 / 重排 / 重讲 / 补证据，以及触发理由
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from .models import (
    CORRECT, INDETERMINATE, NOT_ANSWERED, NOT_ATTENDED,
    R_CROSS_FORM_PAIR, R_HOUSEHOLD_CONFLICT, R_RESPONSE_WITHOUT_ATTENDANCE,
    R_TOPIC_MISMATCH,
    STATUS_LABELS_ZH, WRONG, Evidence, Issue,
)
from .stats import pair_outcome, summarize_pairs

DEFAULT_MIN_PAIRS = 3


def analyze(classified: Dict[str, Any], *, min_pairs: int = DEFAULT_MIN_PAIRS) -> Dict[str, Any]:
    evidence: List[Evidence] = classified["evidence"]
    issues: List[Issue] = list(classified["issues"])
    sessions = classified["sessions"]
    attendance = classified["attendance"]
    household_of = classified["household_of"]
    data = classified.get("data")

    # 把证据格中的关键排除原因提升为报告开头的显式数据质量问题
    for ev in evidence:
        if R_RESPONSE_WITHOUT_ATTENDANCE in ev.reasons and ev.response_id:
            issues.append(Issue(
                "response_without_attendance", "warning",
                f"作答 {ev.response_id}（{ev.person_token} / {ev.session_id} / "
                f"{ev.item_id} / {ev.phase}）查无对应环节签到，已标为无法判断，"
                "不计入独立学习变化",
                {"response_id": ev.response_id, "person_token": ev.person_token}))

    # ---------- 家庭共同作答冲突检测 ----------
    household_conflicts = _detect_household_conflicts(evidence)
    for conflict in household_conflicts:
        issues.append(Issue(
            "household_answer_conflict", "warning",
            f"家庭 {conflict['household_token']} 在 {conflict['scope']} "
            "出现不同答案，不能视为多份一致独立证据",
            conflict))

    # ---------- 构建配对 ----------
    pairs, pair_findings = _build_pairs(evidence, household_conflicts, issues)

    # ---------- 主题结论 ----------
    conclusions = _build_conclusions(
        evidence, pairs, pair_findings, sessions, attendance,
        household_of, min_pairs, issues)

    # ---------- 拓印观察 ----------
    observations = _build_observations(data, sessions)

    # ---------- 问卷（家庭层去重）----------
    feedback = _build_feedback(data)

    # ---------- 家庭证据汇总（依赖已构建的 pairs/feedback）----------
    all_pairs_flat = [p for c in conclusions for p in c["pairs"]]
    household_rollup = _household_rollup(evidence, all_pairs_flat, feedback)

    # ---------- 全局参与漏斗 ----------
    participation = _participation_overview(sessions, attendance, evidence)

    return {
        "conclusions": conclusions,
        "issues": [i.to_dict() for i in issues],
        "pairs": pairs,
        "pair_findings": pair_findings,
        "household_conflicts": household_conflicts,
        "household_rollup": household_rollup,
        "observations": observations,
        "feedback": feedback,
        "participation": participation,
        "thresholds": {"min_pairs_for_strong_claim": min_pairs},
    }


# ---------------------------------------------------------------- 家庭

def _detect_household_conflicts(evidence: List[Evidence]) -> List[Dict[str, Any]]:
    """检测“无法当作多份独立证据”的家庭作答。

    两种真冲突：
    1. 同一个人在同场同题同阶段给出多个不同非空答案（取值不确定）；
    2. 作答无法归属到具体个人（person_token 为空）却以家庭共同名义出现，
       且同一格存在不同答案。

    注意：不同家庭成员（如 P01、P02）各自提交的不同答案属于不同个人的
    独立证据流，只是结论中按“户”去重呈现，不在此判为冲突。
    """
    # 同一人同格多答案
    person_slot: Dict[tuple, set] = defaultdict(set)
    person_meta: Dict[tuple, Dict[str, Any]] = {}
    for ev in evidence:
        # 重复作答格把多条原始答案保存在 all_raw_answers 中
        raw_values = ev.all_raw_answers or ([ev.raw_answer] if (ev.raw_answer or "").strip() else [])
        raw_values = [v.strip().upper() for v in raw_values if (v or "").strip()]
        if not raw_values:
            continue
        slot = (ev.person_token, ev.session_id, ev.item_id, ev.phase)
        for value in raw_values:
            person_slot[slot].add(value)
        person_meta[slot] = {"household_token": ev.household_token,
                             "form_version": ev.form_version}

    conflicts = []
    for (person, sid, item, phase), answers in person_slot.items():
        if len(answers) > 1:
            conflicts.append({
                "household_token": person_meta[(person, sid, item, phase)]["household_token"],
                "session_id": sid, "item_id": item, "phase": phase,
                "scope": f"{sid}/{item}/{phase}/{person}",
                "kind": "same_person_multiple_answers",
                "answers": {a: [person] for a in sorted(answers)},
                "persons": [person],
                "form_version": person_meta[(person, sid, item, phase)]["form_version"],
            })
    return conflicts


def _household_rollup(evidence, pairs, feedback) -> List[Dict[str, Any]]:
    """按户汇总证据，落实“同一家庭共同作答不算多份独立证据”。

    - individual_streams：该户有多少个不同个人产生了可归属作答；
    - gradeable_pairs：可判配对数（个人层面）；
    - distinct_persons_with_pairs：这些配对来自多少不同个人；
      若同一户的“多条改善证据”其实来自同一个人，只算一条个人证据；
    - proxy_feedback：代填/合填问卷按户一条。
    """
    households = sorted({e.household_token for e in evidence if e.household_token})
    proxy_by_hh: Dict[str, int] = defaultdict(int)
    if feedback.get("available"):
        for entry in feedback["household_entries"]:
            proxy_by_hh[entry["household_token"]] += 1

    rollup = []
    for hh in households:
        hh_ev = [e for e in evidence if e.household_token == hh]
        persons_with_answers = sorted({
            e.person_token for e in hh_ev
            if (e.raw_answer or "").strip()})
        attended_members = sorted({
            e.person_token for e in hh_ev
            if e.attendance_status == "attended"})
        hh_pairs = [p for p in pairs
                    if p.get("household_token") == hh and p.get("outcome")]
        pair_persons = sorted({p["person_token"] for p in hh_pairs})
        rollup.append({
            "household_token": hh,
            "attended_members": attended_members,
            "n_attended_members": len(attended_members),
            "persons_with_attributable_answers": persons_with_answers,
            "n_individual_answer_streams": len(persons_with_answers),
            "n_gradeable_pairs": len(hh_pairs),
            "n_distinct_persons_with_pairs": len(pair_persons),
            "pairs_count_as_independent_person_evidence": len(pair_persons),
            "proxy_feedback_entries": proxy_by_hh.get(hh, 0),
            "rule": "家庭共同/代填内容按一户一条；可归属到不同个人的作答才作为不同个人证据",
        })
    return rollup


# ---------------------------------------------------------------- 配对

def _build_pairs(evidence, household_conflicts, issues):
    # 冲突按 (person, session, item, phase) 定位
    conflict_scopes = {
        (c["persons"][0], c["session_id"], c["item_id"], c["phase"])
        for c in household_conflicts
    }

    # 按 (person, session, item) 聚合 pre/post 的全部证据
    grouped: Dict[tuple, Dict[str, List[Evidence]]] = defaultdict(lambda: {"pre": [], "post": []})
    for ev in evidence:
        if ev.phase not in ("pre", "post") or ev.item_id == "(session)":
            continue
        grouped[(ev.person_token, ev.session_id, ev.item_id)][ev.phase].append(ev)

    pairs: List[Dict[str, Any]] = []
    findings: List[Dict[str, Any]] = []
    cross_form_seen = set()

    for (person, sid, item), phases in sorted(grouped.items()):
        pres, posts = phases["pre"], phases["post"]
        if not pres or not posts:
            continue

        pre = _select_phase_evidence(pres)
        post = _select_phase_evidence(posts)

        # 跨量表版本：不能直接比较
        if pre.form_version != post.form_version:
            _record_cross_form(findings, cross_form_seen,
                               person, sid, item, pre, post)
            continue

        pairs.append(_make_pair(person, sid, item, pre, post, conflict_scopes))

    return pairs, findings


def _select_phase_evidence(cells: List[Evidence]) -> Evidence:
    """同一人同题同一阶段可能因量表版本铺出多个格。

    选择优先级：有真实 response_id 的格 > 可判定状态 > 其他。
    这样“F1 有真实前测、F2 只有铺出的空前测格”时会选 F1，
    从而正确识别为跨版本，而不是拿空格硬配。
    """
    def rank(e: Evidence):
        has_response = 0 if e.response_id else 1
        judgeable = 0 if e.status in (CORRECT, WRONG) else 1
        return (has_response, judgeable)
    return sorted(cells, key=rank)[0]


def _record_cross_form(findings, seen, person, sid, item, pre, post):
    sig = (person, sid, item, pre.form_version, post.form_version)
    if sig in seen or pre.form_version == post.form_version:
        return
    seen.add(sig)
    findings.append({
        "kind": R_CROSS_FORM_PAIR,
        "person_token": person, "session_id": sid, "item_id": item,
        "pre_form_version": pre.form_version,
        "post_form_version": post.form_version,
        "note": "前后测题目复用但量表版本不同，含义可能已变化，不计算变化",
    })


def _make_pair(person, sid, item, pre, post, conflict_scopes):
    outcome = pair_outcome(pre.status, post.status)
    hh = post.household_token or pre.household_token
    in_conflict = (person, sid, item, "pre") in conflict_scopes or \
                  (person, sid, item, "post") in conflict_scopes
    return {
        "person_token": person,
        "household_token": hh,
        "session_id": sid,
        "course_version": post.course_version,
        "session_topic": post.session_topic,
        "item_id": item,
        "form_version": post.form_version,
        "mapped_topic": post.mapped_topic or pre.mapped_topic,
        "pre_status": pre.status,
        "post_status": post.status,
        "pre_response_id": pre.response_id,
        "post_response_id": post.response_id,
        "outcome": outcome,
        "household_independent": not in_conflict,
        "excluded_reason": None if outcome else
            "至少一侧不是可判定的答对/答错状态",
        "topic_mismatch": R_TOPIC_MISMATCH in post.reasons
                          or R_TOPIC_MISMATCH in pre.reasons,
    }


# ---------------------------------------------------------------- 主题结论

def _build_conclusions(evidence, pairs, pair_findings, sessions, attendance,
                       household_of, min_pairs, issues):
    # 结论分组：(course_version, 主题)。
    # 有有效映射的证据严格归入 mapped_topic（防止 Q2 串进先秦货币）；
    # 完全没有映射的证据退回当场主题，并在不确定性中说明“映射缺失”。
    groups: Dict[tuple, List[Evidence]] = defaultdict(list)
    for ev in evidence:
        if ev.phase not in ("pre", "post") or ev.item_id == "(session)":
            continue
        if ev.mapped_topic:
            groups[(ev.course_version, ev.mapped_topic)].append(ev)
        else:
            groups[(ev.course_version, f"{ev.session_topic}（题目缺有效映射）")].append(ev)

    conclusions = []
    for (course_version, topic), evs in sorted(groups.items()):
        mapped_topic_name = topic.replace("（题目缺有效映射）", "")
        session_ids = sorted({e.session_id for e in evs})
        # 组内证据天然属于本映射主题；再区分“当场教的就是该主题”与“主题错位”
        on_topic = list(evs)
        off_topic = []
        mismatch = [e for e in evs if "topic_mismatch" in e.reasons]

        group_pairs = [p for p in pairs
                       if p["course_version"] == course_version
                       and p["mapped_topic"] == mapped_topic_name
                       and not p["topic_mismatch"]]
        mismatch_pairs = [p for p in pairs
                          if p["course_version"] == course_version
                          and p["mapped_topic"] == mapped_topic_name
                          and p["topic_mismatch"]]
        pair_summary = summarize_pairs(group_pairs)

        # 适用人群：实际讲授该主题的场次 × 对应环节
        from .classify import stage_for_topic
        stage = stage_for_topic(mapped_topic_name)
        scope_sessions = [sid for sid in session_ids
                          if sessions.get(sid, {}).get("topic", "").strip() == mapped_topic_name]
        attended_persons = set()
        assisted_persons = set()
        households = set()
        for sid in scope_sessions:
            for (person, s, st), row in attendance.items():
                if s == sid and st == stage:
                    if row["status"].strip() == "attended":
                        attended_persons.add(person)
                        if row["household_token"].strip():
                            households.add(row["household_token"].strip())
                    elif row["status"].strip() == "assisted":
                        assisted_persons.add(person)

        status_counts = Counter(e.status for e in on_topic)
        reason_counts = Counter(r for e in on_topic for r in e.reasons)

        # 每个题目的覆盖
        items = sorted({(e.form_version, e.item_id) for e in on_topic})
        item_coverage = []
        for form, item in items:
            iev = [e for e in on_topic if e.form_version == form and e.item_id == item]
            item_coverage.append({
                "form_version": form, "item_id": item,
                "mapping_valid_from": next((e.mapping_valid_from for e in iev if e.mapping_valid_from), None),
                "mapping_valid_to": next((e.mapping_valid_to for e in iev if e.mapping_valid_to), None),
                "status_counts_zh": dict(Counter(
                    STATUS_LABELS_ZH[e.status] for e in iev)),
                "n_evidence_cells": len(iev),
            })

        # 跨版本/跨量表的可比性提示
        comparability = _comparability(course_version, mapped_topic_name, on_topic,
                                       group_pairs, pair_findings)

        uncertainty = _uncertainties(on_topic, group_pairs, pair_summary,
                                     reason_counts, mismatch, mismatch_pairs)

        recommendation = _recommend(
            topic=mapped_topic_name, pair_summary=pair_summary,
            status_counts=status_counts, reason_counts=reason_counts,
            n_attended=len(attended_persons),
            n_taught_sessions=len(scope_sessions),
            n_evidence_sessions=len(session_ids),
            n_mismatch_pairs=len([p for p in mismatch_pairs if p.get("outcome")]),
            item_coverage=item_coverage,
            comparability=comparability,
            min_pairs=min_pairs)

        conclusions.append({
            "course_version": course_version,
            "topic": mapped_topic_name,
            "topic_label": topic,
            "taught_in_sessions": scope_sessions,
            "evidence_sessions": session_ids,
            "session_ids": scope_sessions or session_ids,
            "stage": stage,
            "is_formally_taught_this_version": len(scope_sessions) > 0,
            "provenance": {
                "input_scope": [f"session:{s}" for s in session_ids],
                "form_versions": sorted({e.form_version for e in on_topic if e.form_version}),
                "item_ids": sorted({e.item_id for e in on_topic if e.item_id}),
                "mapping_windows": sorted({
                    f"{e.form_version}/{e.item_id}:"
                    f"{e.mapping_valid_from or '?'}..{e.mapping_valid_to or '至今'}"
                    for e in on_topic if e.mapping_valid_from}),
            },
            "population": {
                "attended_independent_persons": sorted(attended_persons),
                "n_attended_independent": len(attended_persons),
                "assisted_persons": sorted(assisted_persons),
                "n_assisted": len(assisted_persons),
                "n_households": len(households),
            },
            "status_counts": {s: status_counts.get(s, 0)
                              for s in ("correct", "wrong", "not_answered",
                                        "not_attended", "indeterminate")},
            "status_counts_zh": {STATUS_LABELS_ZH[s]: status_counts.get(s, 0)
                                 for s in ("correct", "wrong", "not_answered",
                                           "not_attended", "indeterminate")},
            "reason_counts": dict(reason_counts),
            "pairs": group_pairs,
            "topic_mismatch_pairs": mismatch_pairs,
            "pair_summary": pair_summary,
            "item_coverage": item_coverage,
            "comparability": comparability,
            "uncertainties": uncertainty,
            "topic_mismatch_evidence_count": len(mismatch),
            "recommendation": recommendation,
            "mapping_fingerprints": sorted({
                _ev_mapping_fp(e) for e in on_topic if _ev_mapping_fp(e)}),
        })
    return conclusions


def _ev_mapping_fp(ev: Evidence) -> Optional[str]:
    # 用映射窗口作为轻量指纹线索；真正的映射指纹在 report 层用 provenance.diff 补
    if ev.mapping_valid_from:
        return f"{ev.form_version}|{ev.item_id}|{ev.mapping_valid_from}|{ev.mapping_valid_to or ''}"
    return None


def _comparability(course_version, topic, on_topic, group_pairs, pair_findings):
    forms = sorted({e.form_version for e in on_topic if e.form_version})
    item_ids = {e.item_id for e in on_topic}
    cross = [f for f in pair_findings
             if f["kind"] == R_CROSS_FORM_PAIR
             and f.get("item_id") in item_ids]
    # 只有真正两侧可判的配对才算“可做版内变化比较”
    gradeable = [p for p in group_pairs if p.get("outcome")]

    notes = []
    comparable = len(gradeable) > 0
    if len(forms) > 1:
        notes.append(f"该主题出现多个量表版本 {forms}，跨版次分数不能直接比较")
    if cross:
        notes.append(f"有 {len(cross)} 对前后测来自不同量表版本，已排除出变化计算")
    if not comparable:
        notes.append("没有同一量表版本、两侧均可判的配对，不能声称观察到学习变化")
    return {
        "comparable_within_version": comparable,
        "form_versions": forms,
        "cross_form_excluded_pairs": cross,
        "notes": notes,
        "scope_label": "同场次+同人+同量表版本的可判前后测配对",
    }


def _uncertainties(on_topic, pairs, pair_summary, reason_counts,
                   mismatch, mismatch_pairs):
    items = []
    n = pair_summary["n_gradeable_pairs"]
    if n == 0:
        items.append("没有可判定的前后测配对，结论只能描述参与与作答状态，不能描述学习变化量")
    elif n < 3:
        items.append(f"可判配对仅 {n} 人，个体差异影响大，不宜据此做确定性改课决策")
    ci = pair_summary.get("wilson_improvement")
    if ci and n < 8:
        items.append("改善率置信区间很宽，比例仅供参考")
    if reason_counts.get("no_answer_key"):
        items.append(f"{reason_counts['no_answer_key']} 个证据格缺少有效期内答案键，对错未判")
    if reason_counts.get("mapping_out_of_window"):
        items.append(f"{reason_counts['mapping_out_of_window']} 个证据格落在映射有效期外，题目含义可能已变化")
    if reason_counts.get("response_without_attendance"):
        items.append(f"{reason_counts['response_without_attendance']} 份作答查无签到，未计入独立学习证据")
    if reason_counts.get("topic_mismatch"):
        items.append(f"{reason_counts['topic_mismatch']} 个证据格的题目主题与当场课程主题不一致")
    if mismatch:
        items.append(f"其中 {len(mismatch)} 格属于“题目测的是本主题、但在其他主题场次出现”的错位证据，"
                     "不能用来评价该当场内容的教学")
    gradeable_mm_pairs = [p for p in mismatch_pairs if p.get("outcome")]
    if gradeable_mm_pairs:
        items.append(f"有 {len(gradeable_mm_pairs)} 对两侧可判的错位配对未纳入本主题变化率，"
                     "仅作为该题所测主题的旁证")
    return items


def _recommend(*, topic, pair_summary, status_counts, reason_counts,
               n_attended, n_taught_sessions, n_evidence_sessions,
               n_mismatch_pairs, item_coverage, comparability, min_pairs):
    n_pairs = pair_summary["n_gradeable_pairs"]
    outcomes = pair_summary["outcomes"]

    # 只统计“后测”格中的答错集中程度，避免把前测错误误算成教学后问题
    post_cells = [ic for ic in item_coverage]  # item_coverage 是题目级，下面用单独字段更稳
    rec = {"action": "collect_more_evidence",
           "label_zh": "证据不足：保留内容并继续收集配对",
           "basis": [], "secondary_signals": [],
           "affected_by_mapping_change": False}

    measurement_problems = (
        reason_counts.get("mapping_out_of_window", 0)
        + reason_counts.get("no_answer_key", 0)
        + reason_counts.get("no_measurement_mapping", 0)
    )

    # 1) 本轮课程版次根本没有安排讲授该主题
    if n_taught_sessions == 0:
        if n_evidence_sessions > 0:
            rec.update(action="not_in_curriculum_this_version",
                       label_zh="本版次未安排该主题：外部作答不能评价教学")
            rec["basis"].append(
                "该课程版次没有以本主题命名的场次；现有对错证据来自主题错位或无签到作答，"
                "只能说明有人会/不会这道题，不能作为本轮教学效果，也不产生重讲结论。"
                "教研组应先决定是否把该主题正式排入课程，再配置对应前后测。")
        else:
            rec.update(action="no_delivery", label_zh="本轮未覆盖该主题")
            rec["basis"].append("没有该主题的场次与证据。")
        return rec

    # 2) 讲了，但没有独立到场者
    if n_attended == 0:
        rec.update(action="no_independent_attendance",
                   label_zh="无独立到场证据，不能评价教学效果")
        rec["basis"].append("该主题场次没有状态为 attended 的独立参与者（可能只有协助/缺席记录）。")
        return rec

    # 3) 讲了、有人到，但测量工具链不完整导致零配对
    if n_pairs == 0 and measurement_problems > 0:
        rec.update(action="revise_measurement_or_schedule",
                   label_zh="先修测量/重排测评时点，暂不判定内容得失")
        rec["basis"].append(
            "有人参加但测量映射、有效期或答案键不完整，无法判断是否学会；"
            "应先稳定题目版本与测评安排（保证同一量表的前后测），而不是直接判定内容没讲懂。")
        return rec

    # 4) 配对不足阈值：主建议始终谨慎，错误集中只作为“次要信号”
    if n_pairs < min_pairs:
        rec["basis"].append(
            f"可判前后测配对 {n_pairs} 对，低于建议阈值 {min_pairs}；"
            "结论只能作为线索，建议保留内容并在下一轮继续收集同量表配对证据。")
        if outcomes["improved"] > 0 and outcomes["regressed"] == 0:
            rec["secondary_signals"].append(
                f"已出现 {outcomes['improved']} 例错→对改善且无回退，方向积极，但样本不足")
        if outcomes["stable_wrong"] > 0:
            rec["secondary_signals"].append(
                f"有 {outcomes['stable_wrong']} 对持续错误，可作为下一轮重点访谈/观察线索")
        if n_mismatch_pairs:
            rec["secondary_signals"].append(
                f"另有 {n_mismatch_pairs} 对主题错位配对，提示题目与排课需要重新对齐")
        return rec

    # 5) 样本足够：按改善/回退决策
    imp = pair_summary["improvement_rate"]
    ci = pair_summary.get("wilson_improvement")
    if outcomes["regressed"] > outcomes["improved"] and outcomes["regressed"] > 0:
        rec.update(action="resequence_or_reteach", label_zh="建议重排/重讲")
        rec["basis"].append(
            f"可判配对 {n_pairs} 人中回退 {outcomes['regressed']}、"
            f"改善 {outcomes['improved']}，可能存在前后概念混淆或讲解顺序问题")
        return rec

    if imp is not None and imp >= 0.5 and outcomes["regressed"] == 0:
        rec.update(action="keep", label_zh="建议保留")
        ci_text = (f"，Wilson95% 区间约 {ci['low']:.0%}–{ci['high']:.0%}" if ci else "")
        rec["basis"].append(
            f"改善率 {imp:.0%}（{outcomes['improved']}/{n_pairs}），无回退{ci_text}")
        return rec

    if imp is not None and imp < 0.5 and (outcomes["stable_wrong"] + outcomes["regressed"]) >= n_pairs / 2:
        rec.update(action="reteach", label_zh="建议重讲")
        rec["basis"].append(
            f"改善率仅 {imp:.0%}，持续错误 {outcomes['stable_wrong']} 人、"
            f"回退 {outcomes['regressed']} 人，需重新设计讲解或增加实物观察")
        return rec

    rec["basis"].append(
        f"配对结果 {outcomes}，改善率 "
        + (f"{imp:.0%}" if imp is not None else "不可算")
        + "，信号不明确，建议保留并补充下一轮证据")
    return rec


# ---------------------------------------------------------------- 观察/问卷

def _build_observations(data, sessions):
    from .models import PRACTICE_TOPIC
    if data is None or "observations.csv" not in data.tables:
        return {"available": False,
                "note": "本轮没有 observations.csv；拓印操作掌握情况无法判断，"
                        "但不影响知识主题基于前后测的结论。",
                "sessions": {}}
    rows = data.rows("observations.csv")
    by_session: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for r in rows:
        by_session[r["session_id"].strip()].append(r)

    result = {"available": True, "sessions": {}}
    for sid, session in sessions.items():
        topic = session["topic"].strip()
        if topic != PRACTICE_TOPIC:
            # 知识分享场次不要求拓印观察，不把“没有观察”报成缺失
            result["sessions"][sid] = {
                "topic": topic, "status": "not_applicable",
                "note": "知识分享场次，掌握情况以前后测为准，不要求拓印观察",
                "observations": []}
            continue
        obs = by_session.get(sid, [])
        if not obs:
            result["sessions"][sid] = {
                "topic": topic,
                "status": "missing",
                "note": "拓印实践场次缺少观察记录：操作掌握无法判断；"
                        "此缺失不影响其他知识主题的结论",
                "observations": []}
            continue
        counts = Counter(o["result"].strip() for o in obs)
        result["sessions"][sid] = {
            "topic": topic,
            "status": "observed",
            "result_counts": dict(counts),
            "observations": [{
                "person_token": o["person_token"].strip(),
                "household_token": o["household_token"].strip() or None,
                "result": o["result"].strip(),
                "note": o["note"].strip(),
            } for o in obs],
        }
    return result


def _build_feedback(data):
    if data is None or "feedback.csv" not in data.tables:
        return {"available": False,
                "note": "本轮没有 feedback.csv；活动反馈缺失不影响学习变化结论。",
                "household_entries": [], "mean_rating": None}
    rows = data.rows("feedback.csv")
    entries = []
    ratings = []
    for r in rows:
        rating_raw = r["rating"].strip()
        try:
            rating = float(rating_raw)
            ratings.append(rating)
        except ValueError:
            rating = None
        entries.append({
            "feedback_id": r["feedback_id"].strip(),
            "household_token": r["household_token"].strip(),
            "session_id": r["session_id"].strip(),
            "submitted_by": r["submitted_by"].strip(),
            "rating": rating,
            "confidence": r["confidence"].strip() or None,
            "submitted_at": r["submitted_at"].strip(),
            "evidence_role": "household_level_perception_only",
            "note": "家庭代填/合填问卷：按一户一条处理，不计为多名参与者的独立证据，"
                    "也不用于推断学习变化。",
        })
    return {
        "available": True,
        "household_entries": entries,
        "n_household_responses": len({e["household_token"] + e["session_id"] for e in entries}),
        "mean_rating": (sum(ratings) / len(ratings)) if ratings else None,
        "note": "反馈仅表示家庭层面的主观感受，与知识掌握证据分开呈现。",
    }


# ---------------------------------------------------------------- 参与总览

def _participation_overview(sessions, attendance, evidence):
    per_session = []
    for sid, s in sorted(sessions.items()):
        rows = [(p, st, row["status"].strip())
                for (p, xsid, st), row in attendance.items() if xsid == sid]
        persons = {p for p, _, _ in rows}
        attended = {p for p, st, status in rows if status == "attended"}
        assisted = {p for p, st, status in rows if status == "assisted"}
        absent = {p for p, st, status in rows if status == "absent"}
        per_session.append({
            "session_id": sid,
            "course_version": s["course_version"].strip(),
            "topic": s["topic"].strip(),
            "n_distinct_people_on_roster": len(persons),
            "n_attended_any_stage": len(attended),
            "n_assisted": len(assisted),
            "n_absent_records": len(absent),
            "warning": ("不能用签到人数代替完成整套课程人数：各环节到场分别统计，"
                        "且只有配对作答能支持学习变化") if len({st for _, st, _ in rows}) > 1 or True else None,
        })
    return {"sessions": per_session}
