# 课程学习评估

本地演练资料记录分轮活动、课程主题、测量题目、匿名参与者作答和现场观察。身份均为合成编号，量表及课程映射仅适用于各自标明的版本。

## Local checks

Python 3.9 or newer is sufficient. The repository has no third-party runtime dependencies.

Run `python3 input_audit.py` to verify the declared files and columns. Run `python3 -m unittest discover -s tests -v` to check the prepared input contract.

The records are synthetic fixtures. Empty values and status labels retain their literal input meaning. The manifest lists file columns and the stable row identifier used by the input audit.
