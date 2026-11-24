"""学习评估工具的行为测试。

这些测试把教研关心的口径固定下来：
四状态区分、配对变化、家庭去重、观察缺失隔离、跨量表不可比、
映射更正影响、可选输入缺失、空答案/重复作答、端到端 CLI 与确定性。
"""
from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from assessment.analyze import analyze
from assessment.classify import classify_all
from assessment.io import load_inputs
from assessment.models import (
    CORRECT, INDETERMINATE, NOT_ANSWERED, NOT_ATTENDED, WRONG,
)
from assessment.provenance import build_mappings
from assessment.report import build_mapping_impact, render_markdown
from assessment.stats import wilson_interval
from assessment.cli import run

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "data"

COLUMNS = {
    "sessions.csv": ["session_id", "course_version", "topic", "start_date", "end_date"],
    "attendance.csv": ["person_token", "household_token", "session_id", "stage", "status"],
    "responses.csv": ["response_id", "person_token", "session_id", "phase",
                      "form_version", "item_id", "answer", "submitted_at"],
    "measurements.csv": ["form_version", "item_id", "topic", "valid_from", "valid_to"],
    "answer_keys.csv": ["form_version", "item_id", "correct_answer",
                        "key_version", "valid_from", "valid_to"],
    "observations.csv": ["observation_id", "person_token", "household_token",
                         "session_id", "stage", "result", "note"],
    "feedback.csv": ["feedback_id", "household_token", "session_id",
                     "submitted_by", "rating", "confidence", "submitted_at"],
}
KEYS = {"sessions.csv": "session_id", "responses.csv": "response_id",
        "observations.csv": "observation_id", "feedback.csv": "feedback_id"}


def write_dataset(tmp: Path, tables: dict, optional=("answer_keys.csv",
                                                      "observations.csv",
                                                      "feedback.csv")):
    manifest_tables = []
    for filename, rows in tables.items():
        with (tmp / filename).open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS[filename])
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        entry = {"file": filename, "columns": COLUMNS[filename]}
        if filename in KEYS:
            entry["key"] = KEYS[filename]
        if filename in optional:
            entry["optional"] = True
        manifest_tables.append(entry)
    (tmp / "manifest.json").write_text(
        json.dumps({"tables": manifest_tables}, ensure_ascii=False),
        encoding="utf-8")


def base_tables():
    return {
        "sessions.csv": [
            {"session_id": "S1", "course_version": "C1", "topic": "先秦货币",
             "start_date": "2026-09-02", "end_date": "2026-09-02"},
        ],
        "attendance.csv": [
            {"person_token": "P1", "household_token": "H1", "session_id": "S1",
             "stage": "share", "status": "attended"},
            {"person_token": "P2", "household_token": "H1", "session_id": "S1",
             "stage": "share", "status": "attended"},
            {"person_token": "P3", "household_token": "H3", "session_id": "S1",
             "stage": "share", "status": "absent"},
        ],
        "responses.csv": [
            {"response_id": "R1", "person_token": "P1", "session_id": "S1",
             "phase": "pre", "form_version": "F1", "item_id": "Q1",
             "answer": "A", "submitted_at": "2026-09-02T09:00:00"},
            {"response_id": "R2", "person_token": "P1", "session_id": "S1",
             "phase": "post", "form_version": "F1", "item_id": "Q1",
             "answer": "B", "submitted_at": "2026-09-02T10:00:00"},
            {"response_id": "R3", "person_token": "P2", "session_id": "S1",
             "phase": "post", "form_version": "F1", "item_id": "Q1",
             "answer": "B", "submitted_at": "2026-09-02T10:05:00"},
        ],
        "measurements.csv": [
            {"form_version": "F1", "item_id": "Q1", "topic": "先秦货币",
             "valid_from": "2026-08-01", "valid_to": "2026-09-30"},
        ],
        "answer_keys.csv": [
            {"form_version": "F1", "item_id": "Q1", "correct_answer": "B",
             "key_version": "K1", "valid_from": "2026-08-01", "valid_to": "2026-09-30"},
        ],
    }


def analyze_dir(path: Path, **kw):
    data = load_inputs(path)
    classified = classify_all(data)
    classified["data"] = data
    return analyze(classified, **kw)


class FixtureEndToEndTests(unittest.TestCase):
    def test_shipped_fixture_key_results(self):
        result = run(FIXTURE, out_dir=None)["result"]
        topics = {(c["course_version"], c["topic"]): c for c in result["conclusions"]}

        xq_c1 = topics[("C1", "先秦货币")]
        # P01 错→对是唯一可判配对
        self.assertEqual(xq_c1["pair_summary"]["outcomes"]["improved"], 1)
        self.assertEqual(xq_c1["pair_summary"]["n_gradeable_pairs"], 1)
        # P03 缺席必须是未参加
        self.assertGreaterEqual(xq_c1["status_counts"]["not_attended"], 1)
        # 小样本不得给出确定性“重讲/保留”强结论
        self.assertEqual(xq_c1["recommendation"]["action"], "collect_more_evidence")

        # 晋商信用：本版次未排课，Q2 答对也不能评价教学
        js = topics[("C2", "晋商信用")]
        self.assertEqual(js["status_counts"]["correct"], 1)
        self.assertFalse(js["is_formally_taught_this_version"])
        self.assertEqual(js["recommendation"]["action"],
                         "not_in_curriculum_this_version")

    def test_report_and_json_artifacts_written(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "reports"
            run(FIXTURE, out_dir=out)
            self.assertTrue((out / "learning_report.md").exists())
            payload = json.loads((out / "learning_report.json").read_text("utf-8"))
            self.assertIn("input_provenance", payload)
            self.assertEqual(len(payload["input_provenance"]["file_hashes"]), 8)

    def test_cli_module_runs(self):
        with tempfile.TemporaryDirectory() as td:
            code = run_cli(["--data", str(FIXTURE), "--out", td])
            self.assertEqual(code, 0)


def run_cli(argv):
    from assessment.cli import main
    return main(argv)


class FourStateClassificationTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _status_map(self, tables):
        write_dataset(self.tmp, tables)
        data = load_inputs(self.tmp)
        classified = classify_all(data)
        return {(e.person_token, e.item_id, e.phase): e
                for e in classified["evidence"]}

    def test_correct_wrong_not_answered_not_attended(self):
        smap = self._status_map(base_tables())
        self.assertEqual(smap[("P1", "Q1", "pre")].status, WRONG)
        self.assertEqual(smap[("P1", "Q1", "post")].status, CORRECT)
        self.assertEqual(smap[("P2", "Q1", "pre")].status, NOT_ANSWERED)
        self.assertEqual(smap[("P3", "Q1", "pre")].status, NOT_ATTENDED)
        self.assertEqual(smap[("P3", "Q1", "post")].status, NOT_ATTENDED)

    def test_blank_answer_is_not_answered(self):
        tables = base_tables()
        tables["responses.csv"].append(
            {"response_id": "R4", "person_token": "P2", "session_id": "S1",
             "phase": "pre", "form_version": "F1", "item_id": "Q1",
             "answer": "  ", "submitted_at": "2026-09-02T09:10:00"})
        smap = self._status_map(tables)
        self.assertEqual(smap[("P2", "Q1", "pre")].status, NOT_ANSWERED)
        self.assertIn("blank_answer", smap[("P2", "Q1", "pre")].reasons)

    def test_missing_answer_key_is_indeterminate_not_wrong(self):
        tables = base_tables()
        del tables["answer_keys.csv"]
        smap = self._status_map(tables)
        self.assertEqual(smap[("P1", "Q1", "post")].status, INDETERMINATE)
        self.assertIn("no_answer_key", smap[("P1", "Q1", "post")].reasons)

    def test_mapping_out_of_window_is_indeterminate(self):
        tables = base_tables()
        tables["measurements.csv"][0]["valid_to"] = "2026-08-31"
        smap = self._status_map(tables)
        ev = smap[("P1", "Q1", "post")]
        self.assertEqual(ev.status, INDETERMINATE)
        self.assertIn("mapping_out_of_window", ev.reasons)

    def test_response_without_attendance_is_indeterminate(self):
        tables = base_tables()
        tables["responses.csv"].append(
            {"response_id": "R9", "person_token": "P9", "session_id": "S1",
             "phase": "post", "form_version": "F1", "item_id": "Q1",
             "answer": "B", "submitted_at": "2026-09-02T10:20:00"})
        smap = self._status_map(tables)
        ev = smap[("P9", "Q1", "post")]
        self.assertEqual(ev.status, INDETERMINATE)
        self.assertIn("response_without_attendance", ev.reasons)

    def test_duplicate_responses_are_indeterminate(self):
        tables = base_tables()
        tables["responses.csv"].append(
            {"response_id": "R2b", "person_token": "P1", "session_id": "S1",
             "phase": "post", "form_version": "F1", "item_id": "Q1",
             "answer": "A", "submitted_at": "2026-09-02T10:30:00"})
        smap = self._status_map(tables)
        self.assertEqual(smap[("P1", "Q1", "post")].status, INDETERMINATE)
        self.assertIn("duplicate_response", smap[("P1", "Q1", "post")].reasons)

    def test_assisted_attendance_not_independent(self):
        tables = base_tables()
        tables["attendance.csv"].append(
            {"person_token": "P5", "household_token": "H5", "session_id": "S1",
             "stage": "share", "status": "assisted"})
        smap = self._status_map(tables)
        self.assertEqual(smap[("P5", "Q1", "post")].status, NOT_ATTENDED)
        self.assertIn("assisted_participation",
                      smap[("P5", "Q1", "post")].reasons)


class PairAndRecommendationTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _dataset_n_pairs(self, n_improved=4, n_stable_wrong=0):
        tables = base_tables()
        tables["attendance.csv"] = []
        tables["responses.csv"] = []
        pid = 0
        rid = 0
        for i in range(n_improved):
            pid += 1
            p = f"P{pid}"
            tables["attendance.csv"].append(
                {"person_token": p, "household_token": f"H{pid}",
                 "session_id": "S1", "stage": "share", "status": "attended"})
            rid += 1
            tables["responses.csv"].append(
                {"response_id": f"R{rid}", "person_token": p, "session_id": "S1",
                 "phase": "pre", "form_version": "F1", "item_id": "Q1",
                 "answer": "A", "submitted_at": "2026-09-02T09:00:00"})
            rid += 1
            tables["responses.csv"].append(
                {"response_id": f"R{rid}", "person_token": p, "session_id": "S1",
                 "phase": "post", "form_version": "F1", "item_id": "Q1",
                 "answer": "B", "submitted_at": "2026-09-02T10:00:00"})
        for i in range(n_stable_wrong):
            pid += 1
            p = f"P{pid}"
            tables["attendance.csv"].append(
                {"person_token": p, "household_token": f"H{pid}",
                 "session_id": "S1", "stage": "share", "status": "attended"})
            for phase, hh in (("pre", rid + 1), ("post", rid + 2)):
                rid += 1
                tables["responses.csv"].append(
                    {"response_id": f"R{rid}", "person_token": p, "session_id": "S1",
                     "phase": phase, "form_version": "F1", "item_id": "Q1",
                     "answer": "A", "submitted_at": "2026-09-02T10:00:00"})
        write_dataset(self.tmp, tables)
        return analyze_dir(self.tmp)

    def test_strong_improvement_yields_keep(self):
        result = self._dataset_n_pairs(n_improved=4)
        c = next(c for c in result["conclusions"] if c["topic"] == "先秦货币")
        self.assertEqual(c["pair_summary"]["outcomes"]["improved"], 4)
        self.assertEqual(c["recommendation"]["action"], "keep")

    def test_persistent_errors_yield_reteach(self):
        # 1 改善 + 4 持续错误：改善率 0.2，错误/回退占多数
        result = self._dataset_n_pairs(n_improved=1, n_stable_wrong=4)
        c = next(c for c in result["conclusions"] if c["topic"] == "先秦货币")
        self.assertEqual(c["recommendation"]["action"], "reteach")

    def test_cross_form_pair_not_compared(self):
        tables = base_tables()
        tables["responses.csv"] = [
            {"response_id": "R1", "person_token": "P1", "session_id": "S1",
             "phase": "pre", "form_version": "F1", "item_id": "Q1",
             "answer": "A", "submitted_at": "2026-09-02T09:00:00"},
            {"response_id": "R2", "person_token": "P1", "session_id": "S1",
             "phase": "post", "form_version": "F2", "item_id": "Q1",
             "answer": "B", "submitted_at": "2026-10-02T10:00:00"},
        ]
        tables["measurements.csv"].append(
            {"form_version": "F2", "item_id": "Q1", "topic": "先秦货币",
             "valid_from": "2026-10-01", "valid_to": ""})
        tables["answer_keys.csv"].append(
            {"form_version": "F2", "item_id": "Q1", "correct_answer": "B",
             "key_version": "K2", "valid_from": "2026-10-01", "valid_to": ""})
        write_dataset(self.tmp, tables)
        result = analyze_dir(self.tmp)
        c = next(c for c in result["conclusions"] if c["topic"] == "先秦货币")
        self.assertEqual(c["pair_summary"]["n_gradeable_pairs"], 0)
        self.assertTrue(any(f["kind"] == "pre_post_different_form_version"
                            for f in result["pair_findings"]))


class HouseholdTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_distinct_family_members_are_distinct_person_evidence(self):
        write_dataset(self.tmp, base_tables())
        result = analyze_dir(self.tmp)
        h01 = next(h for h in result["household_rollup"]
                   if h["household_token"] == "H1")
        # P01 与 P02 两名独立到场成员，且都留下可归属作答；只有 P01 形成可判配对
        self.assertEqual(h01["n_attended_members"], 2)
        self.assertEqual(h01["n_individual_answer_streams"], 2)
        self.assertEqual(h01["n_gradeable_pairs"], 1)
        self.assertEqual(h01["n_distinct_persons_with_pairs"], 1)
        p01_pair = next(p for p in result["pairs"] if p["person_token"] == "P1")
        self.assertTrue(p01_pair["household_independent"])

    def test_same_person_conflicting_answers_flagged(self):
        tables = base_tables()
        tables["responses.csv"].append(
            {"response_id": "R2b", "person_token": "P1", "session_id": "S1",
             "phase": "post", "form_version": "F1", "item_id": "Q1",
             "answer": "A", "submitted_at": "2026-09-02T10:30:00"})
        write_dataset(self.tmp, tables)
        result = analyze_dir(self.tmp)
        self.assertTrue(result["household_conflicts"])
        p01 = [p for p in result["pairs"] if p["person_token"] == "P1"][0]
        self.assertFalse(p01["household_independent"])


class ObservationIsolationTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_missing_observations_do_not_erase_knowledge_conclusions(self):
        tables = base_tables()  # 不含 observations.csv / feedback.csv
        write_dataset(self.tmp, tables)
        result = analyze_dir(self.tmp)
        self.assertFalse(result["observations"]["available"])
        # 知识主题结论依然存在且保留配对
        c = next(c for c in result["conclusions"] if c["topic"] == "先秦货币")
        self.assertEqual(c["pair_summary"]["n_gradeable_pairs"], 1)
        self.assertFalse(result["feedback"]["available"])

    def test_practice_observation_recorded_separately(self):
        tables = base_tables()
        tables["sessions.csv"].append(
            {"session_id": "S2", "course_version": "C1", "topic": "拓印实践",
             "start_date": "2026-09-09", "end_date": "2026-09-09"})
        tables["attendance.csv"].append(
            {"person_token": "P1", "household_token": "H1", "session_id": "S2",
             "stage": "rubbing", "status": "attended"})
        tables["observations.csv"] = [
            {"observation_id": "O1", "person_token": "P1", "household_token": "H1",
             "session_id": "S2", "stage": "rubbing", "result": "independent",
             "note": "独立完成"}]
        write_dataset(self.tmp, tables)
        result = analyze_dir(self.tmp)
        s2 = result["observations"]["sessions"]["S2"]
        self.assertEqual(s2["status"], "observed")
        self.assertEqual(s2["result_counts"], {"independent": 1})


class MappingCorrectionTests(unittest.TestCase):
    def test_correction_flags_referencing_conclusion_only(self):
        data = load_inputs(FIXTURE)
        classified = classify_all(data)
        classified["data"] = data
        result = analyze(classified)

        baseline = build_mappings(list(csv.DictReader(
            (ROOT / "examples/mapping_baseline_v1.csv").open(encoding="utf-8"))))
        corrected = build_mappings(list(csv.DictReader(
            (ROOT / "examples/mapping_corrected_q2_to_xianqin.csv").open(encoding="utf-8"))))
        impact = build_mapping_impact(result, baseline, corrected)

        changed_ids = {tuple(c["mapping_id"]) for c in impact["changed"]}
        self.assertIn(("F2", "Q2", "2026-10-01"), changed_ids)
        affected_topics = {a["topic"] for a in impact["affected_conclusions"]}
        self.assertIn("晋商信用", affected_topics)
        # S01 的 F1/Q1 结论不引用被改映射，不应被标记
        c1 = next(c for c in result["conclusions"]
                  if c["course_version"] == "C1" and c["topic"] == "先秦货币")
        self.assertFalse(c1["recommendation"]["affected_by_mapping_change"])

    def test_identical_mapping_has_no_impact(self):
        data = load_inputs(FIXTURE)
        mappings = classified_mappings(data)
        impact = build_mapping_impact(analyze_simple(data), mappings, mappings)
        self.assertEqual(impact["changed"], [])
        self.assertEqual(impact["affected_conclusions"], [])


def classified_mappings(data):
    return classify_all(data)["mappings"]


def analyze_simple(data):
    classified = classify_all(data)
    classified["data"] = data
    return analyze(classified)


class DeterminismTests(unittest.TestCase):
    def test_same_inputs_same_markdown(self):
        import re
        r1 = run(FIXTURE, out_dir=None)
        r2 = run(FIXTURE, out_dir=None)
        normalize = lambda md: re.sub(r"生成时间：.*", "生成时间：TS", md)
        self.assertEqual(normalize(r1["markdown"]), normalize(r2["markdown"]))

    def test_wilson_interval_bounds(self):
        ci = wilson_interval(1, 1)
        self.assertLess(ci["low"], 1.0)
        self.assertGreater(ci["high"], 0.9)
        self.assertIsNone(wilson_interval(0, 0))


class ReportContentTests(unittest.TestCase):
    def test_markdown_contains_provenance_and_uncertainty(self):
        r = run(FIXTURE, out_dir=None)
        md = r["markdown"]
        for token in ("课程版次", "可比较范围", "不确定性", "response_id",
                      "R01", "家庭", "映射更正", "未参加", "未作答", "无法判断"):
            self.assertIn(token, md)


if __name__ == "__main__":
    unittest.main()
