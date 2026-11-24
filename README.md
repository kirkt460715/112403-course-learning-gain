# 课程学习评估（钱币专题分享 × 拓印体验）

一个可随每轮资料重复使用的**本地学习评估工具**：只依赖 Python 3.9+ 标准库，
把签到、版本化讲解主题、前后测作答、拓印观察和家庭问卷，整理成教研人员
能直接用于“保留 / 重排 / 重讲 / 补证据”决策的结论。

它不输出一个掩盖差异的总分，而是回答：

- 每个人在每个主题上究竟是 **未参加 / 未作答 / 答错 / 无法判断 / 答对**；
- 哪些是同一人、同一场、同一量表版本的真实“错→对”学习变化；
- 哪些只是参与差异、家庭代填，或测量题目/映射版本变化造成的；
- 每条结论来自哪个课程版次、哪些场次、哪些 response_id、哪版映射；
- 课程主题映射更正后，哪些既有结论与改版建议必须复查；
- 拓印观察缺失时，只让操作结论留白，不抹掉其他主题仍然有效的结论。

## 快速开始

```bash
# 1) 校验输入清单与列
python3 input_audit.py

# 2) 跑评估，产出 Markdown + JSON（默认读 data/，写 reports/）
python3 -m assessment.cli --data data --out reports

# 3) 跑全部测试
python3 -m unittest discover -s tests -v
```

打开 `reports/learning_report.md` 阅读教研报告；
`reports/learning_report.json` 供下一轮工具或留档复用（含输入文件 SHA-256 指纹）。

也可以用便捷脚本：`python3 run_report.py`。

## 每轮新资料怎么复用

1. 复制 `data/` 为新的一轮目录，例如 `rounds/2026-round2/`。
2. 按 `manifest.json` 声明的列更新各 CSV；`sessions / attendance /
   responses / measurements` 为必填，`answer_keys / observations / feedback`
   为可选（缺失只会让对应结论留白，不会让其他主题失效）。
3. 运行：

   ```bash
   python3 -m assessment.cli --data rounds/2026-round2 \
       --out rounds/2026-round2/report
   ```

4. 若改了阈值口径，可用 `--min-pairs 5` 调整“强结论”所需的最小可判配对数
   （默认 3；低于该数只给谨慎线索，不替教研拍板）。

## 输入表与关键口径

| 文件 | 作用 |
|---|---|
| `sessions.csv` | 场次与**课程版次**（course_version）、当场主题、日期 |
| `attendance.csv` | 人 × 户 × 场次 × 环节（share/rubbing）× 状态（attended/assisted/absent） |
| `responses.csv` | 匿名作答，phase=pre/post，含量表 form_version、题目、答案、提交时间 |
| `measurements.csv` | **版本化题目→主题映射 + 有效期**；旧题重用、含义变化都靠有效期识别 |
| `answer_keys.csv` | 可选，版本化答案键；缺失时对错一律标“无法判断”而非猜测 |
| `observations.csv` | 可选，拓印操作观察（independent/assisted 等） |
| `feedback.csv` | 可选，**家庭层面**问卷，按户去重，不并入个人学习证据 |

### 五种状态如何判定

- **未参加 `not_attended`**：该主题对应环节查无 `attended` 签到，或登记
  `absent`；`assisted`（协助参与）也归为未独立参加，并注明原因。
- **未作答 `not_answered`**：本人独立到场，但没有该阶段记录，或答案为空白。
- **答错 `wrong` / 答对 `correct`**：有非空答案，且提交日期落在映射与答案键
  的有效期内，可判定。
- **无法判断 `indeterminate`**：有材料但参与范围或测量含义不能确认，包括
  有作答无签到、映射缺失/过期、无有效答案键、同格重复作答、未知 phase 等。

### 什么才算“真实学习变化”

只有 **同一 person_token + 同一场次 + 同一 form_version + 前后两侧都可判对错**
才形成配对，分为：错→对（改善）、对→错（回退）、对→对（稳定掌握）、
错→错（持续错误）。跨量表版本（如 F1 前测、F2 后测）不直接比较，只列为
可比性提示。报告同时给原始计数与 Wilson 95% 区间；样本小于阈值时明确
“证据有限”，建议保留并继续收集配对，而不是给确定性改课结论。

### 家庭证据规则

- 问卷/共同填写按 `household_token + session` **一户一条**，评分均值只作
  家庭主观反馈，不与个人成绩合并。
- 能归属到不同个人、且本人独立到场的作答，仍是不同个人的证据流
  （例如 H01 的 P01、P02）。
- 同一人同一格出现多个不同答案（含重复提交）时，该格判“无法判断”，
  并在家庭口径表与数据质量节中标出，不会被当成多份一致证据。

## 映射更正如何追踪影响

`measurements.csv` 的映射身份是 `(form_version, item_id, valid_from)`，
每条映射与整份映射都有指纹。主题映射被更正时：

```bash
# examples/mapping_baseline_v1.csv 是“更正前”留档样式
python3 -m assessment.cli --data data --out reports \
  --mapping-baseline examples/mapping_baseline_v1.csv
```

报告第 8 节与 JSON 的 `mapping_correction_impact` 会列出：

- 哪条映射被更正/新增/删除（主题或有效期的前后值、指纹）；
- 哪些主题结论与“保留/重排/重讲”建议引用了它，需要复查；
- 哪些结论不受影响（例如 F2/Q2 的改挂不会动摇基于 F1/Q1 的 S01 结论）。

可直接用示例体验：把
`examples/mapping_corrected_q2_to_xianqin.csv` 覆盖为某轮的
`measurements.csv`（它演示把 Q2 从“晋商信用”改挂“先秦货币”），
再带 `--mapping-baseline examples/mapping_baseline_v1.csv` 运行。

## 目录结构

```
assessment/            评估包（加载/分类/统计/分析/溯源/报告/CLI）
data/                  当前一轮合成演练资料（含全部七张表）
examples/              映射更正前后的示例
tests/                 输入契约测试 + 评估口径行为测试
input_audit.py         清单/列/主键校验
run_report.py          便捷入口
reports/               生成产物（已 gitignore，不入库）
```

所有人物编号均为合成身份；量表、答案键与课程映射仅适用于各自标明的版本与有效期。
