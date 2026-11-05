# -*- coding: utf-8 -*-
"""全局配置：路径、端口与开关

所有配置项都来自环境变量（带默认值），改行为不用改代码：
  AGENT_PORT        智能体服务端口，默认 8790
  USE_PLANNER       首次出题前是否跑策划官，0/false 关闭
  DB_PATH           SQLite 文件位置，默认 <项目根>/data/interview.db
  DATA_DIR          数据目录，默认 <项目根>/data
  HARNESS_ADAPTER_DIR  偏好根目录；默认不设，偏好从 plugins/<name>/preferences/ 读
                       （显式设置时独占，用于挂一份实验版本，见 services/harness.py）
  SKILLS_ENABLED    是否装载技能，0/false 关闭
  SKILLS_DIR        技能根目录，默认 backend/skills
"""

import os
from pathlib import Path

# backend/app/config.py → backend/ → 仓库根
BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent

DATA_DIR = Path(os.environ.get("DATA_DIR") or (PROJECT_ROOT / "data"))

# 专家插件根目录：每个子目录一个能力包，内含 plugin.yaml、流水线实现，
# 以及可训练层 preferences/（ADAPTER.md + hermit 清单）。
# 以前可训练层单独放 backend/adapters/，与插件目录同名却不同义，已合并进来。
PLUGINS_DIR = Path(os.environ.get("PLUGINS_DIR") or (BACKEND_DIR / "plugins"))
PLUGINS_ENABLED = os.environ.get("PLUGINS_ENABLED", "1") not in ("0", "false", "False")

# 不传 agent_mode 时走哪条业务线（默认面试出题官）。之前这个回落写死在
# runtime 里 import 面试流水线，现在只是配置。
DEFAULT_MODE = os.environ.get("DEFAULT_MODE", "interview")

# 技能（Skill）根目录：每个子目录一个技能，内含 SKILL.md
SKILLS_DIR = Path(os.environ.get("SKILLS_DIR") or (BACKEND_DIR / "skills"))
SKILLS_ENABLED = os.environ.get("SKILLS_ENABLED", "1") not in ("0", "false", "False")

PORT = int(os.environ.get("AGENT_PORT", "8790"))

# 首次出题前是否先跑「策划官」生成考察大纲（关掉可省一次模型调用：USE_PLANNER=0）
USE_PLANNER = os.environ.get("USE_PLANNER", "1") not in ("0", "false", "False")

AGENT_NAME = "agentscope-interview"
