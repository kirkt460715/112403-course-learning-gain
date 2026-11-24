"""便捷入口：python3 run_report.py [--data data] [--out reports] ...

等价于 python3 -m assessment.cli。不带参数时使用 README 中的默认路径。
"""
from assessment.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
