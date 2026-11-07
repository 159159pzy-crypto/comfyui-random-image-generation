"""统一把仓库根目录加入 sys.path,让每个测试文件都能独立运行。

此前只有部分测试文件自带 sys.path 引导,单独运行其余文件会
ModuleNotFoundError,全量运行能过只是收集顺序的副作用。
"""

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
