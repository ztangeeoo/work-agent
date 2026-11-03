# -*- coding: utf-8 -*-
"""评分卡数据契约（Pydantic）

交给 AgentScope 的 `structured_schema` 做结构化输出：模型直接吐这个结构的
JSON，失败时上层回退到「从文本里捞 JSON」（utils/parsing.try_parse_score）。
"""

from pydantic import BaseModel, Field


class ScoreDimension(BaseModel):
    """评分维度"""

    name: str = Field(description="维度名称，例如：准确性 / 深度 / 结构 / 表达")
    score: int = Field(description="该维度得分，0-100 的整数")
    comment: str = Field(description="一句话点评，指出具体好在哪/差在哪")


class ScoreCard(BaseModel):
    """面试回答评分卡"""

    overall: int = Field(description="总分，0-100 的整数")
    dimensions: list[ScoreDimension] = Field(description="各维度得分")
    strengths: list[str] = Field(description="回答中的亮点，1-4 条")
    weaknesses: list[str] = Field(description="回答中的不足，1-4 条")
    suggestions: list[str] = Field(description="可执行的改进建议，1-4 条")
    reference_answer: str = Field(description="该问题的参考回答要点，Markdown 列表")
