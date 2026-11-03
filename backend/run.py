# -*- coding: utf-8 -*-
"""智能体服务启动入口

    python backend/run.py          # 默认 8790
    AGENT_PORT=9000 python backend/run.py

等价于在仓库根执行 `python -m backend.app.main`。
本文件负责把仓库根放进 sys.path，因此在任何目录下执行都能跑。
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.main import main  # noqa: E402

if __name__ == "__main__":
    main()
