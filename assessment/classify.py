"""把签到、作答、版本化测量映射和答案键整理成逐格证据。

关键规则（对应教研问题）：
1. 未参加优先：没有有效 share 到场（或缺席登记）的人，不产生“未作答/答错”，
   而是 NOT_ATTENDED —— 不能把签到人数当成完成整套课程的人。
2. 参加了但没有提交 / 答案为空 -> NOT_ANSWERED。
3. 有非空答案且能在有效期内找到答案键 -> CORRECT / WRONG。
4. 其余有材料但不能判定的情况（映射过期、无答案键、重复提交、
   有作答无签到、阶段未知等）-> INDETERMINATE，并附原因码。
5. 题目映射主题与当场主题不一致时，照判对错，但加 topic_mismatch，
   该证据只进入“题目实际主题”的分析，不进入当场主题的学习变化。

证据格覆盖：所有在签到或作答中出现过的人 × 每个场次实际使用过的题目
× pre/post。这样“未参加 / 未作答”都有明确的格子，而不是沉默的缺行。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    ASSISTED, ATTENDED, CORRECT, INDETERMINATE, NOT_ANSWERED, NOT_ATTENDED,
    R_ABSENT_RECORD, R_ASSISTED, R_BLANK_ANSWER, R_DUPLICATE_RESPONSE,
    R_MAPPING_OUT_OF_WINDOW, R_NO_ANSWER_KEY, R_NO_ATTENDANCE, R_NO_MEASUREMENT, R_NO_RESPONSE,
    R_PHASE_UNKNOWN, R_RESPONSE_WITHOUT_ATTENDANCE, R_TOPIC_MISMATCH,
    WRONG, Evidence, Issue,
)
from .provenance import (
    KeyEntry, build_keys, build_mappings, mapping_for, key_for, parse_date,
)

KNOWLEDGE_STAGE = "share"


def stage_for_topic(session_topic: str) -> str:
    from .models import PRACTICE_TOPIC, STAGE_RUBBING
    return STAGE_RUBBING if session_topic == PRACTICE_TOPIC else KNOWLEDGE_STAGE


def classify_all(data) -> Dict[str, Any]:
    """data: assessment.io.InputData。"""
    sessions = {r["session_id"].strip(): r for r in data.rows("sessions.csv")}
    mappings = build_mappings(data.rows("measurements.csv"))
    keys = build_keys(data.rows("answer_keys.csv")) if "answer_keys.csv" in data.tables else []

    issues: List[Issue] = []

    # ---------- 家庭归属 ----------
    household_of: Dict[str, Optional[str]] = {}
    for row in data.rows("attendance.csv"):
        household_of[row["person_token"].strip()] = row["household_token"].strip() or None
    for row in data.rows("responses.csv"):
        household_of.setdefault(row["person_token"].strip(), None)

    # ---------- 签到索引 ----------
    attendance: Dict[Tuple[str, str, str], Dict[str, str]] = {}
    for row in data.rows("attendance.csv"):
        person = row["person_token"].strip()
        key = (person, row["session_id"].strip(), row["stage"].strip())
        if key in attendance:
            issues.append(Issue(
                "duplicate_attendance", "warning",
                f"{person} 在 {key[1]} 的 {key[2]} 环节有多条签到记录",
                {"key": list(key)}))
        attendance[key] = row

    # ---------- 作答索引 ----------
    responses_by_key: Dict[Tuple[str, str, str, str, str], List[Dict[str, str]]] = defaultdict(list)
    unknown_phase_rows: List[Dict[str, str]] = []
    for row in data.rows("responses.csv"):
        person = row["person_token"].strip()
        phase = row["phase"].strip()
        key = (person, row["session_id"].strip(), phase,
               row["form_version"].strip(), row["item_id"].strip())
        responses_by_key[key].append(row)
        if phase not in ("pre", "post"):
            unknown_phase_rows.append(row)

    for row in unknown_phase_rows:
        issues.append(Issue(
            "unknown_response_phase", "warning",
            f"作答 {row['response_id']} 的 phase={row['phase']} 不是 pre/post，"
            "已保留但不进入前后测比较",
            {"response_id": row["response_id"]}))

    # 每个场次实际出现过的 (form,item)
    items_in_session: Dict[str, set] = defaultdict(set)
    for (person, sid, phase, form, item), rows in responses_by_key.items():
        if phase in ("pre", "post"):
            items_in_session[sid].add((form, item))

    # 每个场次还应纳入“映射到当场主题”的题目（即使无人作答），
    # 这样未参加/未作答格也带有正确主题，不会在主题分组时分裂。
    for sid, session in sessions.items():
        day = parse_date(session["start_date"].strip())
        topic = session["topic"].strip()
        for m in mappings:
            if m.topic == topic and (day is None or
                                     (parse_date(m.valid_from) and day >= parse_date(m.valid_from)
                                      and (not m.valid_to or day <= parse_date(m.valid_to)))):
                items_in_session[sid].add((m.form_version, m.item_id))

    # 人的全集：签到 + 作答（用于引用完整性等）
    all_people = set(household_of.keys())

    evidence: List[Evidence] = []
    graded_response_ids = set()

    # ---------- 主网格：场次相关人 × 该场题目 × pre/post ----------
    # “场次相关人”= 该场对应环节有签到记录的人 ∪ 该场有作答的人。
    # 不把从未出现在该场的人铺成“未参加”，避免未参加计数虚高；
    # 完全查无此人到场但有作答的，仍以 response_without_attendance 标无法判断。
    for sid, session in sessions.items():
        topic = session["topic"].strip()
        course_version = session["course_version"].strip()
        stage = stage_for_topic(topic)
        item_refs = sorted(items_in_session.get(sid, set()))

        relevant_people = set()
        for (person, s, st) in attendance.keys():
            if s == sid and st == stage:
                relevant_people.add(person)
        for (person, s, phase, form, item) in responses_by_key.keys():
            if s == sid:
                relevant_people.add(person)

        for person in sorted(relevant_people):
            att = attendance.get((person, sid, stage))
            att_status = att["status"].strip() if att else None
            household = household_of.get(person)
            if att and att.get("household_token", "").strip():
                household = att["household_token"].strip()

            for form_version, item_id in item_refs:
                for phase in ("pre", "post"):
                    rows = responses_by_key.get(
                        (person, sid, phase, form_version, item_id), [])
                    ev = _classify_cell(
                        person=person, household=household, sid=sid,
                        course_version=course_version, topic=topic, stage=stage,
                        form_version=form_version, item_id=item_id, phase=phase,
                        att=att, att_status=att_status, rows=rows,
                        mappings=mappings, keys=keys, issues=issues,
                        sessions=sessions)
                    evidence.append(ev)
                    for r in rows:
                        graded_response_ids.add(r["response_id"].strip())

            # 没有任何题目记录时（如 S02 晋商信用），仍在场次层面留参与状态
            if not item_refs:
                evidence.append(_session_level_status(
                    person, household, sid, course_version, topic, stage,
                    att, att_status))

    # ---------- 未知 phase 的作答：保留为 INDETERMINATE 证据 ----------
    for row in unknown_phase_rows:
        rid = row["response_id"].strip()
        if rid in graded_response_ids:
            continue
        sid = row["session_id"].strip()
        session = sessions.get(sid)
        topic = session["topic"].strip() if session else "(未知场次)"
        ev = Evidence(
            person_token=row["person_token"].strip(),
            household_token=household_of.get(row["person_token"].strip()),
            session_id=sid,
            course_version=session["course_version"].strip() if session else "(未知版次)",
            session_topic=topic, stage=stage_for_topic(topic) if session else "unknown",
            item_id=row["item_id"].strip(), form_version=row["form_version"].strip(),
            phase=row["phase"].strip(), response_id=rid, raw_answer=row["answer"],
            submitted_at=row["submitted_at"].strip(), status=INDETERMINATE,
            reasons=[R_PHASE_UNKNOWN])
        _attach_mapping_and_grade(ev, row, mappings, keys, issues, allow_grade=False)
        evidence.append(ev)

    # ---------- 引用完整性 ----------
    session_ids = set(sessions)
    for row in data.rows("responses.csv"):
        if row["session_id"].strip() not in session_ids:
            issues.append(Issue(
                "unknown_session_reference", "error",
                f"作答 {row['response_id']} 引用了不存在的场次 {row['session_id']}",
                {"response_id": row["response_id"]}))
    for row in data.rows("attendance.csv"):
        if row["session_id"].strip() not in session_ids:
            issues.append(Issue(
                "unknown_session_reference", "error",
                f"签到记录引用了不存在的场次 {row['session_id']}",
                {"person_token": row["person_token"]}))

    known_att_status = {ATTENDED, ASSISTED, "absent"}
    for row in data.rows("attendance.csv"):
        status = row["status"].strip()
        if status not in known_att_status:
            issues.append(Issue(
                "unknown_attendance_status", "warning",
                f"未知签到状态 “{status}”（{row['person_token']} / "
                f"{row['session_id']} / {row['stage']}），按非独立有效到场处理",
                {"row_ref": {"person_token": row["person_token"],
                             "session_id": row["session_id"]}}))

    return {
        "evidence": evidence,
        "issues": issues,
        "mappings": mappings,
        "keys": keys,
        "household_of": household_of,
        "sessions": sessions,
        "attendance": attendance,
    }


def _session_level_status(person, household, sid, course_version, topic,
                          stage, att, att_status) -> Evidence:
    if att is None:
        return Evidence(
            person_token=person, household_token=household, session_id=sid,
            course_version=course_version, session_topic=topic, stage=stage,
            item_id="(session)", form_version="", phase="session",
            status=NOT_ATTENDED, reasons=[R_NO_ATTENDANCE])
    if att_status == "absent":
        reasons = [R_ABSENT_RECORD]
    elif att_status != ATTENDED:
        reasons = [R_ASSISTED if att_status == ASSISTED else R_NO_ATTENDANCE]
    else:
        # 到场但该场没有任何测量题目：无法判断学习变化
        return Evidence(
            person_token=person, household_token=household, session_id=sid,
            course_version=course_version, session_topic=topic, stage=stage,
            item_id="(session)", form_version="", phase="session",
            status=INDETERMINATE, reasons=["no_instrument_for_session"],
            attendance_status=att_status)
    return Evidence(
        person_token=person, household_token=household, session_id=sid,
        course_version=course_version, session_topic=topic, stage=stage,
        item_id="(session)", form_version="", phase="session",
        status=NOT_ATTENDED, reasons=reasons, attendance_status=att_status)


def _classify_cell(*, person, household, sid, course_version, topic, stage,
                   form_version, item_id, phase, att, att_status, rows,
                   mappings, keys, issues, sessions=None) -> Evidence:
    base = dict(person_token=person, household_token=household, session_id=sid,
                course_version=course_version, session_topic=topic, stage=stage,
                item_id=item_id, form_version=form_version, phase=phase,
                attendance_status=att_status)

    # 1) 没有签到记录
    if att is None:
        if rows:
            # 有作答但查无签到：无法判断参与范围，即使答案可判也不把它当独立掌握证据
            row = rows[0]
            ev = Evidence(status=INDETERMINATE,
                          reasons=[R_RESPONSE_WITHOUT_ATTENDANCE],
                          response_id=row["response_id"].strip(),
                          raw_answer=row["answer"],
                          submitted_at=row["submitted_at"].strip(), **base)
            if len(rows) > 1:
                ev.reasons.append(R_DUPLICATE_RESPONSE)
            return _attach_mapping_and_grade(ev, row, mappings, keys, issues,
                                             allow_grade=False)
        ev = Evidence(status=NOT_ATTENDED, reasons=[R_NO_ATTENDANCE], **base)
        return _attach_expected_mapping(ev, sid, sessions, mappings)

    # 2) 明确缺席 / 非独立参与
    if att_status == "absent":
        ev = Evidence(status=NOT_ATTENDED, reasons=[R_ABSENT_RECORD], **base)
        return _attach_expected_mapping(ev, sid, sessions, mappings)
    if att_status != ATTENDED:
        reason = R_ASSISTED if att_status == ASSISTED else R_NO_ATTENDANCE
        ev = Evidence(status=NOT_ATTENDED, reasons=[reason], **base)
        return _attach_expected_mapping(ev, sid, sessions, mappings)

    # 3) 已独立到场：处理作答
    if len(rows) > 1:
        chosen = rows[0]
        ev = Evidence(status=INDETERMINATE, reasons=[R_DUPLICATE_RESPONSE],
                      response_id=";".join(r["response_id"] for r in rows),
                      raw_answer=chosen["answer"],
                      all_raw_answers=[r["answer"] for r in rows],
                      submitted_at=chosen["submitted_at"].strip(), **base)
        # 只附映射信息，不再判对错：多条作答取值不确定
        _attach_mapping_only(ev, chosen, mappings)
        return ev

    if len(rows) == 0:
        ev = Evidence(status=NOT_ANSWERED, reasons=[R_NO_RESPONSE], **base)
        return _attach_expected_mapping(ev, sid, sessions, mappings)

    row = rows[0]
    ev = Evidence(response_id=row["response_id"].strip(),
                  raw_answer=row["answer"],
                  submitted_at=row["submitted_at"].strip(),
                  status=INDETERMINATE, **base)
    return _attach_mapping_and_grade(ev, row, mappings, keys, issues)


def _attach_mapping_only(ev: Evidence, row, mappings) -> Evidence:
    day = parse_date(row.get("submitted_at", ""))
    mapping, _ = mapping_for(mappings, ev.form_version, ev.item_id, day)
    if mapping:
        ev.mapped_topic = mapping.topic
        ev.mapping_valid_from = mapping.valid_from
        ev.mapping_valid_to = mapping.valid_to or None
    else:
        candidates = [m for m in mappings
                      if m.form_version == ev.form_version and m.item_id == ev.item_id]
        if candidates:
            m0 = candidates[0]
            ev.mapped_topic = m0.topic
            ev.mapping_valid_from = m0.valid_from
            ev.mapping_valid_to = m0.valid_to or None
            ev.reasons.append(R_MAPPING_OUT_OF_WINDOW)
        else:
            ev.reasons.append(R_NO_MEASUREMENT)
    return ev


def _attach_expected_mapping(ev: Evidence, sid, sessions, mappings) -> Evidence:
    """给没有作答日期的格子（未参加/未作答）按场次日期附上映射主题。"""
    start = sessions.get(sid, {}).get("start_date", "") if sessions else ""
    session_day = parse_date(start or "")
    mapping, _ = mapping_for(mappings, ev.form_version, ev.item_id, session_day)
    if mapping:
        ev.mapped_topic = mapping.topic
        ev.mapping_valid_from = mapping.valid_from
        ev.mapping_valid_to = mapping.valid_to or None
    else:
        candidates = [m for m in mappings
                      if m.form_version == ev.form_version and m.item_id == ev.item_id]
        if candidates:
            m0 = candidates[0]
            ev.mapped_topic = m0.topic
            ev.mapping_valid_from = m0.valid_from
            ev.mapping_valid_to = m0.valid_to or None
    return ev


def _attach_mapping_and_grade(ev: Evidence, row, mappings, keys, issues,
                              allow_grade: bool = True) -> Evidence:
    day = parse_date(ev.submitted_at or "")
    mapping, overlapping = mapping_for(mappings, ev.form_version, ev.item_id, day)

    if mapping is None:
        candidates = [m for m in mappings
                      if m.form_version == ev.form_version and m.item_id == ev.item_id]
        if candidates:
            ev.reasons.append(R_MAPPING_OUT_OF_WINDOW)
            m0 = candidates[0]
            ev.mapped_topic = m0.topic
            ev.mapping_valid_from = m0.valid_from
            ev.mapping_valid_to = m0.valid_to or None
        else:
            ev.reasons.append(R_NO_MEASUREMENT)
        ev.status = INDETERMINATE
        return ev

    ev.mapped_topic = mapping.topic
    ev.mapping_valid_from = mapping.valid_from
    ev.mapping_valid_to = mapping.valid_to or None

    if overlapping:
        issues.append(Issue(
            "overlapping_mapping_windows", "warning",
            f"{ev.form_version}/{ev.item_id} 在 {ev.submitted_at} 命中多条重叠有效期映射",
            {"item_id": ev.item_id}))

    if mapping.topic != ev.session_topic:
        ev.reasons.append(R_TOPIC_MISMATCH)

    if not (ev.raw_answer or "").strip():
        ev.reasons.append(R_BLANK_ANSWER)
        ev.status = NOT_ANSWERED
        return ev

    key = key_for(keys, ev.form_version, ev.item_id, day)
    if key is None:
        ev.reasons.append(R_NO_ANSWER_KEY)
        ev.status = INDETERMINATE
        return ev

    graded = CORRECT if ev.raw_answer.strip().upper() == key.correct_answer.strip().upper() else WRONG
    ev.graded_answer = graded

    if not allow_grade:
        # 无签到等情形：对错仅作参考字段，状态仍为无法判断
        return ev

    ev.status = graded
    return ev
