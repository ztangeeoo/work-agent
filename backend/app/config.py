# -*- coding: utf-8 -*-
"""全局配置：路径、端口与开关

所有配置项都来自环境变量（带默认值），改行为不用改代码：
  AGENT_PORT        智能体服务端口，默认 8790
  USE_PLANNER       首次出题前是否跑策划官，0/false 关闭
  DB_PATH           SQLite 文件位置，默认 <项目根>/data/interview.db
  DATA_DIR          数据目录，默认 <项目根>/data
  HARNESS_ADAPTER_DIR  adapter 根目录，默认 backend/adapters
  SKILLS_ENABLED    是否装载技能，0/false 关闭
  SKILLS_DIR        技能根目录，默认 backend/skills
"""

import os
from pathlib import Path

# backend/app/config.py → backend/ → 仓库根
BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent

DATA_DIR = Path(os.environ.get("DATA_DIR") or (PROJECT_ROOT / "data"))
ADAPTERS_DIR = Path(os.environ.get("HARNESS_ADAPTER_DIR") or (BACKEND_DIR / "adapters"))

# 技能（Skill）根目录：每个子目录一个技能，内含 SKILL.md
SKILLS_DIR = Path(os.environ.get("SKILLS_DIR") or (BACKEND_DIR / "skills"))
SKILLS_ENABLED = os.environ.get("SKILLS_ENABLED", "1") not in ("0", "false", "False")

PORT = int(os.environ.get("AGENT_PORT", "8790"))

# 首次出题前是否先跑「策划官」生成考察大纲（关掉可省一次模型调用：USE_PLANNER=0）
USE_PLANNER = os.environ.get("USE_PLANNER", "1") not in ("0", "false", "False")

AGENT_NAME = "agentscope-interview"
