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
  NOTIFIERS_DIR     通知渠道根目录，默认 backend/notifiers
  NOTIFIERS_ENABLED 是否装载通知渠道，0/false 关闭（关掉就不再推送群消息）
  WECOM_WEBHOOK     企业微信群机器人 webhook（密钥，不放仓库，见下）
"""

import os
from pathlib import Path

# backend/app/config.py → backend/ → 仓库根
BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent


def _load_dotenv(path: Path) -> int:
    """把仓库根的 .env 读进环境变量（已存在的变量不覆盖）

    为什么需要它：本仓库是 public，企业微信 webhook、模型 API Key 这类
    值不能写进 `notifier.yaml` 或任何被跟踪的文件，只能走环境变量。但让
    每个人自己去「系统属性 → 环境变量」里加，Windows 下还要重启终端，
    实际体验就是「配了没生效」。放一个 .env（已在 .gitignore 里）更省事。

    三个刻意的限制：
      - **不覆盖已有值**：`WECOM_WEBHOOK=x python run.py` 这种一次性覆盖要生效
      - **不引入 python-dotenv**：只支持 KEY=VALUE / # 注释，够用且零依赖
      - **解析失败静默跳过**：配置文件写歪了不该让服务起不来
    """
    try:
        if not path.is_file():
            return 0
        n = 0
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            key, _, val = line.partition("=")
            key = key.strip()
            if not key or key in os.environ:
                continue
            val = val.strip()
            # 去掉成对引号（.env 里常见 KEY="a b" 的写法）
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            os.environ[key] = val
            n += 1
        return n
    except Exception:
        return 0


load_dotenv = _load_dotenv          # 对外名字：tests / 脚本可复用
_load_dotenv(PROJECT_ROOT / ".env")

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

# 通知渠道根目录：每个子目录一个渠道（notifier.yaml + notifier.py）。
# 与 plugins/ 是同构但不是一回事：插件是「一条业务线」，会被投影进
# expert / agent 两张表、出现在专家中心；通知渠道只做「把事件发出去」，
# 不进库、不占专家位。放同一个目录里会让专家中心多出一张「企业微信推送」卡片。
NOTIFIERS_DIR = Path(os.environ.get("NOTIFIERS_DIR") or (BACKEND_DIR / "notifiers"))
NOTIFIERS_ENABLED = os.environ.get("NOTIFIERS_ENABLED", "1") not in ("0", "false", "False")

PORT = int(os.environ.get("AGENT_PORT", "8790"))

# 首次出题前是否先跑「策划官」生成考察大纲（关掉可省一次模型调用：USE_PLANNER=0）
USE_PLANNER = os.environ.get("USE_PLANNER", "1") not in ("0", "false", "False")

AGENT_NAME = "agentscope-interview"
