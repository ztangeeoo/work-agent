# -*- coding: utf-8 -*-
"""
AgentScope 2.0 运行时基线测试（不联网、不调模型）

作用：把「本项目依赖的 AgentScope API 面」钉死成断言。以后升级 AgentScope
（或误装回 1.x / 旧版 agentscope-runtime）时，先跑这个文件，能立刻看出哪一块
API 变了，而不是等到线上出一堆看不懂的报错。

覆盖：
  - 版本基线：AgentScope 主版本 >= 2
  - 模型 / 凭据 / 工具 / Toolkit 构造参数
  - Agent 构造 + reply_stream(structured_schema, yield_final_msg)
  - 事件字段：TextBlockDeltaEvent.delta / ToolCallStartEvent.tool_call_name /
    ReplyEndEvent.finished_reason|error
  - 上下文语义：make_agent 不再预置 state.context（防历史重复的回归锁）
  - OpenAI 格式消息 → Msg 的转换、意图路由、评分卡 JSON 容错解析

运行：.venv/Scripts/python.exe tests/test_agentscope_api.py
"""

import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agentscope  # noqa: E402
from agentscope.agent import Agent  # noqa: E402
from agentscope.event import (  # noqa: E402
    ReplyEndEvent,
    TextBlockDeltaEvent,
    ToolCallStartEvent,
)
from agentscope.state import AgentState  # noqa: E402
from agentscope.permission import PermissionBehavior  # noqa: E402
from agentscope.types import ReplyFinishedReason  # noqa: E402

import agent_server as A  # noqa: E402

pass_n = 0
fail_n = 0


def ok(cond, label):
    global pass_n, fail_n
    if cond:
        pass_n += 1
        print(f"  ✓ {label}")
    else:
        fail_n += 1
        print(f"  ✗ {label}")


def section(title):
    print(f"\n— {title} —")


def main():
    section("版本基线")
    ver = str(getattr(agentscope, "__version__", "0"))
    major = int(ver.split(".")[0]) if ver.split(".")[0].isdigit() else 0
    ok(major >= 2, f"AgentScope 主版本 >= 2（当前 {ver}）")
    ok(A.AS_VERSION == ver, "agent_server 读取到同一个版本号")
    try:
        import agentscope_runtime  # noqa: F401

        ok(False, "未安装已归档的 agentscope-runtime（2.0 已内建其能力）")
    except ImportError:
        ok(True, "未安装已归档的 agentscope-runtime（2.0 已内建其能力）")

    section("模型 / 工具构造")
    model = A.get_model("https://api.openai.com/v1", "sk-test", "gpt-4o-mini", 0.7)
    ok(type(model).__name__ == "OpenAIChatModel", "OpenAIChatModel 可构造")
    ok(model.model == "gpt-4o-mini", "模型名透传")
    ok(model.credential.base_url == "https://api.openai.com/v1", "base_url 透传（任意 OpenAI 兼容接口）")
    ok(model.stream is True, "默认流式")
    ok(A.get_model("https://api.openai.com/v1", "sk-test", "gpt-4o-mini", 0.7) is model, "同参数命中模型缓存")

    tool = A._bank_tool()
    ok(tool.name == "query_question_bank", "题库工具名来自函数名")
    perm = getattr(tool, "_permission", None)
    ok(perm is not None and getattr(perm, "behavior", None) == PermissionBehavior.ALLOW,
       "工具带显式 ALLOW 决策（服务端不会卡在人工确认）")

    section("Agent 构造与 reply_stream 契约")
    sig = inspect.signature(Agent.__init__)
    for p in ("name", "system_prompt", "model", "toolkit", "state"):
        ok(p in sig.parameters, f"Agent.__init__ 支持 {p}")

    rs = inspect.signature(Agent.reply_stream)
    ok("inputs" in rs.parameters, "reply_stream(inputs)")
    ok("structured_schema" in rs.parameters, "reply_stream(structured_schema=)（评分卡依赖）")
    ok("yield_final_msg" in rs.parameters, "reply_stream(yield_final_msg=)（取最终消息）")

    st = AgentState(context=[])
    ok(isinstance(st.context, list) and st.context == [], "AgentState(context=[]) 可用")

    msig = inspect.signature(A.make_agent)
    ok("context" not in msig.parameters,
       "make_agent 不再接收 context（历史只经 inputs 传一次，防重复）")

    agent = A.make_agent("测试面试官", "你是测试智能体", model, False)
    ok(agent.state.context == [], "新建 Agent 的 state.context 为空")
    ok(agent.name == "测试面试官", "Agent 名字透传")
    ok(hasattr(agent, "toolkit"), "Agent 持有 toolkit")
    ok(A.make_agent("测试面试官2", "sys", model, True).toolkit is not None, "带工具分支可构造")

    section("事件字段（drive_agent 依赖）")
    ok("delta" in TextBlockDeltaEvent.model_fields, "TextBlockDeltaEvent.delta")
    ok("tool_call_name" in ToolCallStartEvent.model_fields, "ToolCallStartEvent.tool_call_name")
    ok("finished_reason" in ReplyEndEvent.model_fields, "ReplyEndEvent.finished_reason")
    ok("error" in ReplyEndEvent.model_fields, "ReplyEndEvent.error")
    ok(hasattr(ReplyFinishedReason, "ERROR"), "ReplyFinishedReason.ERROR 存在")

    section("消息转换（OpenAI → Msg）")
    msgs = A.to_scope_msgs([
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "第一答"},
        {"role": "user", "content": [{"type": "text", "text": "多模态分段"}]},
        {"role": "user", "content": "   "},
    ])
    ok(len(msgs) == 3, f"过滤 system 与空消息后剩 3 条（实际 {len(msgs)}）")
    ok([m.role for m in msgs] == ["user", "assistant", "user"], "角色顺序保持不变")
    ok(msgs[2].get_text_content() == "多模态分段", "多模态分段拼回纯文本")
    ok(A.last_user_text([{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
                          {"role": "user", "content": "最后一个"}]) == "最后一个", "取最后一条用户消息")

    section("意图路由")
    ok(A.route_intent("帮我评分一下这个回答", True) == "score", "评分类问题走评分官")
    ok(A.route_intent("再出三道题", True) == "interview", "出题类问题走面试官")
    ok(A.route_intent("", False) == "interview", "空输入默认出题")

    section("评分卡容错解析")
    card = A.try_parse_score('```json\n{"overall": 82, "dimensions": []}\n```')
    ok(isinstance(card, dict) and card.get("overall") == 82, "带 markdown 围栏也能解析")
    ok(A.try_parse_score('前言 {"overall": 60} 后记')["overall"] == 60, "从长文本里捞出 JSON")
    ok(A.try_parse_score("这不是 JSON") is None, "非 JSON 返回 None")
    md = A.render_score_card({"overall": 88, "dimensions": [{"name": "准确性", "score": 90, "comment": "到位"}],
                              "strengths": ["思路清晰"], "weaknesses": [], "suggestions": ["补边界"],
                              "reference_answer": "- 要点一"})
    ok("## 📊 回答评分：88 / 100" in md and "准确性" in md, "评分卡渲染 Markdown")

    print(f"\n结果：{pass_n} 通过 / {fail_n} 失败\n")
    return 1 if fail_n else 0


if __name__ == "__main__":
    sys.exit(main())
