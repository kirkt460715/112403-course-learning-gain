"""核心状态、原因码与数据结构。

四种作答/证据状态严格区分：
- NOT_ATTENDED 未参加：该环节没有有效到场记录（含登记缺席）
- NOT_ANSWERED 未作答：参加了、题目在有效期内，但没有可判定的答案（空答案/缺提交）
- WRONG         答错：有有效答案且答案键可判定，答案错误
- INDETERMINATE 无法判断：有材料，但因映射/有效期/答案键/重复提交/无签到等原因不能判定

另有 CORRECT 表示答对（它是“可判断”的正向状态，不是第五种歧义状态）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# 证据状态
CORRECT = "correct"
WRONG = "wrong"
NOT_ANSWERED = "not_answered"
NOT_ATTENDED = "not_attended"
INDETERMINATE = "indeterminate"

STATUS_LABELS_ZH = {
    CORRECT: "答对",
    WRONG: "答错",
    NOT_ANSWERED: "未作答",
    NOT_ATTENDED: "未参加",
    INDETERMINATE: "无法判断",
}

JUDGEABLE_STATUSES = (CORRECT, WRONG)

# 到场状态：attended 为独立参加；assisted 为需协助（体验类可观察，
# 但在知识证据中不作为独立作答者）；absent/其他一律视为未有效到场。
ATTENDED = "attended"
ASSISTED = "assisted"
ABSENT = "absent"

# 环节与知识主题的默认对应。拓印实践以操作观察为主要证据。
STAGE_SHARE = "share"
STAGE_RUBBING = "rubbing"
PRACTICE_TOPIC = "拓印实践"

# ---- 原因码（机器可读，报告中翻译成中文说明）----
R_ABSENT_RECORD = "absent_record"                 # 签到表明确登记缺席
R_NO_ATTENDANCE = "no_attendance_record"           # 查无该环节签到记录
R_ASSISTED = "assisted_participation"               # 协助/非独立参与
R_BLANK_ANSWER = "blank_answer"                    # 提交了空答案
R_NO_RESPONSE = "no_response_row"                  # 没有该阶段作答记录
R_NO_MEASUREMENT = "no_measurement_mapping"       # 题目没有主题映射
R_MAPPING_OUT_OF_WINDOW = "mapping_out_of_window"  # 作答日期不在映射有效期
R_NO_ANSWER_KEY = "no_answer_key"                  # 缺少答案键/键不在有效期
R_DUPLICATE_RESPONSE = "duplicate_response"        # 同题同阶段多条作答
R_RESPONSE_WITHOUT_ATTENDANCE = "response_without_attendance"  # 有作答无签到
R_TOPIC_MISMATCH = "item_topic_differs_from_session"  # 题目主题与当场主题不一致
R_CROSS_FORM_PAIR = "pre_post_different_form_version"  # 前后测量表版本不同
R_HOUSEHOLD_CONFLICT = "household_answers_conflict"   # 同户代填/答案冲突
R_HOUSEHOLD_PROXY = "household_proxy_questionnaire"    # 家庭代填问卷，非个人证据
R_OBSERVATION_MISSING = "observation_missing"          # 观察缺失
R_PHASE_UNKNOWN = "unknown_response_phase"            # 未知前后测阶段

REASON_LABELS_ZH = {
    R_ABSENT_RECORD: "签到表登记为缺席",
    R_NO_ATTENDANCE: "查无该环节有效签到记录",
    R_ASSISTED: "属于协助参与，不能作为独立掌握证据",
    R_BLANK_ANSWER: "答案为空，按未作答处理",
    R_NO_RESPONSE: "没有该阶段的作答记录",
    R_NO_MEASUREMENT: "题目在测量版本表中没有主题映射",
    R_MAPPING_OUT_OF_WINDOW: "作答日期不在题目映射有效期内，题目含义可能已变化",
    R_NO_ANSWER_KEY: "缺少对应版本/有效期内的答案键，对错无法判定",
    R_DUPLICATE_RESPONSE: "同一人同一题同一阶段存在多条作答，取值不确定",
    R_RESPONSE_WITHOUT_ATTENDANCE: "有作答但没有对应环节签到，参与范围无法确认",
    R_TOPIC_MISMATCH: "该题映射主题与当场课程主题不同，不能作为当场内容的掌握证据",
    R_CROSS_FORM_PAIR: "前测与后测来自不同量表版本，不能直接比较变化",
    R_HOUSEHOLD_CONFLICT: "同一家庭出现互相冲突的共同作答，不能并作多份一致证据",
    R_HOUSEHOLD_PROXY: "问卷为家庭成员代填/合填，仅作家庭层面反馈",
    R_OBSERVATION_MISSING: "该活动没有可用的拓印观察记录",
    R_PHASE_UNKNOWN: "作答阶段不是 pre/post，无法放入前后测设计",
}


@dataclass
class Issue:
    """数据质量/可解释性问题，全部保留到报告与 JSON。"""

    code: str
    severity: str  # warning / error / info
    message: str
    refs: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "severity": self.severity,
                "message": self.message, "refs": self.refs}


@dataclass
class Evidence:
    """一个 人×场次×题目×阶段 的证据格。"""

    person_token: str
    household_token: Optional[str]
    session_id: str
    course_version: str
    session_topic: str
    stage: str
    item_id: str
    form_version: str
    phase: str  # pre / post
    status: str
    reasons: List[str] = field(default_factory=list)
    response_id: Optional[str] = None
    raw_answer: Optional[str] = None
    all_raw_answers: List[str] = field(default_factory=list)
    graded_answer: Optional[str] = None
    mapped_topic: Optional[str] = None
    mapping_valid_from: Optional[str] = None
    mapping_valid_to: Optional[str] = None
    submitted_at: Optional[str] = None
    attendance_status: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        d["status_zh"] = STATUS_LABELS_ZH[self.status]
        d["reason_labels_zh"] = [REASON_LABELS_ZH.get(r, r) for r in self.reasons]
        return d
