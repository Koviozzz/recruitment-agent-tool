"""
招聘 Agent —— 核心数据结构与 ChatDD Tool 契约
================================================
本文件定义两件事：
1) 业务层 Pydantic 模型（简历/候选人画像、JD 需求、匹配结果）
2) 暴露给 ChatDD 编排器的 Tool Schema（OpenAI function-calling 格式）

ChatDD 的 AgentOS 本质是 planning + reasoning + tool calling + skills。
只要你的后端服务能按 OpenAI 兼容的 function-calling 协议被调用，
就能作为 tool 注册进 ChatDD 的 agent 编排器（或直接打包成一个 Recruitment Skill）。
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 1) 业务模型：候选人画像（简历解析结果）
# ---------------------------------------------------------------------------

class EducationLevel(str, Enum):
    """学历层级，用于后续匹配时的学历门槛比对。"""
    OTHER = "other"
    COLLEGE = "college"          # 大专
    BACHELOR = "bachelor"        # 本科
    MASTER = "master"            # 硕士
    PHD = "phd"                  # 博士
    POSTDOC = "postdoc"          # 博士后


class Education(BaseModel):
    school: Optional[str] = None
    major: Optional[str] = None
    degree: EducationLevel = EducationLevel.OTHER
    start_year: Optional[int] = None
    end_year: Optional[int] = None


class WorkExperience(BaseModel):
    company: Optional[str] = None
    title: Optional[str] = None
    # 这段经历所属领域标签，例如：biopharma / ai_ml / academia / cRO / consulting
    domain: Optional[str] = None
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    # 这段经历的关键职责/成就（一句话一条）
    highlights: List[str] = Field(default_factory=list)


class CandidateProfile(BaseModel):
    """简历结构化后的候选人画像。"""
    name: Optional[str] = None
    # 候选人对自己的一句话总结（若有）
    summary: Optional[str] = None
    # 技能/能力标签（归一化后的关键词，如 LLM、抗体工程、蛋白质设计、Python、GMP…）
    skills: List[str] = Field(default_factory=list)
    # 最高学历（用于快速比对门槛）
    highest_degree: EducationLevel = EducationLevel.OTHER
    educations: List[Education] = Field(default_factory=list)
    # 总工作年限（年），由经历推算或由 LLM 给出
    total_years_experience: Optional[float] = None
    experiences: List[WorkExperience] = Field(default_factory=list)
    # 领域契合标签（如：大分子药物、AI 制药、临床研究、生信）
    domains: List[str] = Field(default_factory=list)
    # 论文/专利/项目亮点
    publications: List[str] = Field(default_factory=list)
    # 当前/最近职级或头衔，如：算法工程师、研究员、PI
    current_title: Optional[str] = None
    # 可到岗时间、地点偏好等补充信息
    extra: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 2) 业务模型：JD 需求（岗位解析结果）
# ---------------------------------------------------------------------------

class JdRequirements(BaseModel):
    """JD 结构化后的岗位需求。

    字段与 matcher.score_match 严格对齐：匹配引擎只消费以下字段，
    不要在此新增 matcher 不读取的字段，否则会造成抽取↔匹配契约不一致。
    """
    job_title: Optional[str] = None
    # 硬性学历门槛
    min_degree: EducationLevel = EducationLevel.OTHER
    # 硬性经验年限门槛
    min_years_experience: Optional[float] = None
    # 必须掌握的技能标签
    must_have_skills: List[str] = Field(default_factory=list)
    # 优先/加分技能标签
    preferred_skills: List[str] = Field(default_factory=list)
    # 目标领域（如：大分子药物、AI 制药）
    target_domains: List[str] = Field(default_factory=list)
    # 其他补充说明（可选，不参与匹配打分）
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# 3) 业务模型：匹配结果
# ---------------------------------------------------------------------------

class DimensionScore(BaseModel):
    name: str                     # 维度名，如 技能覆盖 / 学历 / 经验 / 领域契合
    score: float                  # 0~100
    weight: float                 # 该维度在总分中的权重
    detail: str                   # 给 HR/候选人的可读解释


class MatchResult(BaseModel):
    candidate_name: Optional[str] = None
    job_title: Optional[str] = None
    total_score: float = 0.0      # 0~100 加权总分
    dimensions: List[DimensionScore] = Field(default_factory=list)
    # 命中 JD 的技能
    matched_skills: List[str] = Field(default_factory=list)
    # 缺失的硬门槛技能（直接构成风险项）
    missing_must_have_skills: List[str] = Field(default_factory=list)
    # 差距与建议
    gaps: List[str] = Field(default_factory=list)
    recommendation: str = ""      # 综合结论：强烈推荐 / 推荐 / 待定 / 不推荐
    # 是否触发硬门槛（学历/经验/必含技能不达标）。触发时 conclusion 不会高于「待定」，
    # 但 total_score 可能仍偏高 —— 该标记用于前端/展示层解释「高分≠通过」的割裂（见 N7）。
    hard_breach: bool = False
    # 给 HR/候选人看的一段自然语言结论（规则版离线可用，无需 LLM）。
    explanation: str = ""


# ---------------------------------------------------------------------------
# 4) ChatDD Tool 契约（OpenAI function-calling 格式）
#    把下面三个 tool 注册到 ChatDD 编排器的 tool 列表即可；
#    ChatDD 的 planner 会在「需要解析简历 / 解析 JD / 匹配人才」时自动调用。
# ---------------------------------------------------------------------------

PARSE_RESUME_TOOL = {
    "type": "function",
    "function": {
        "name": "parse_resume",
        "description": (
            "将一段非结构化简历文本（或简历关键信息）解析为结构化候选人画像，"
            "抽取姓名、技能标签、最高学历、工作年限、领域标签、论文/项目亮点等。"
            "当用户上传或粘贴简历、要求『解析候选人』『看这个人合不合适』时使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "resume_text": {
                    "type": "string",
                    "description": "简历全文或关键段落的纯文本。",
                },
                "candidate_name": {
                    "type": "string",
                    "description": "可选的候选人姓名，便于结果回显；解析不到时可省略。",
                },
            },
            "required": ["resume_text"],
        },
    },
}

PARSE_JD_TOOL = {
    "type": "function",
    "function": {
        "name": "parse_jd",
        "description": (
            "将一段非结构化岗位描述（JD）解析为结构化岗位需求，"
            "抽取岗位名称、学历门槛、经验年限门槛、必须技能、优先技能、目标领域等。"
            "当用户粘贴 JD、要求『解析岗位要求』『这个岗位要什么人』时使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "jd_text": {
                    "type": "string",
                    "description": "岗位描述全文或关键段落的纯文本。",
                },
            },
            "required": ["jd_text"],
        },
    },
}

MATCH_TOOL = {
    "type": "function",
    "function": {
        "name": "match_candidate_to_jd",
        "description": (
            "给定结构化的候选人画像与岗位需求，计算匹配度总分、各维度得分、"
            "命中/缺失技能、差距分析与推荐结论。用于简历筛选与人才排序。"
            "当用户问『这个人匹配这个岗位吗』『给候选人排序』『谁最合适』时使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "candidate": {
                    "type": "object",
                    "description": "parse_resume 返回的候选人画像 JSON。",
                },
                "jd": {
                    "type": "object",
                    "description": "parse_jd 返回的岗位需求 JSON。",
                },
            },
            "required": ["candidate", "jd"],
        },
    },
}

SCREEN_TOOL = {
    "type": "function",
    "function": {
        "name": "screen",
        "description": (
            "端到端简历筛选：输入简历文本与 JD 文本，自动解析候选人画像与岗位需求，"
            "直接计算匹配度总分、各维度得分、命中/缺失技能、差距分析与推荐结论。"
            "适合用户一句『这个人合适吗』『筛一下这份简历』时使用，无需先单独调用 "
            "parse_resume / parse_jd。注意：HTTP 端点为 /tools/screen，与 tool 名一致。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "resume_text": {
                    "type": "string",
                    "description": "简历全文或关键段落的纯文本。",
                },
                "jd_text": {
                    "type": "string",
                    "description": "岗位描述全文或关键段落的纯文本。",
                },
                "candidate_name": {
                    "type": "string",
                    "description": "可选的候选人姓名，便于结果回显；解析不到时可省略。",
                },
            },
            "required": ["resume_text", "jd_text"],
        },
    },
}

# 供 ChatDD 一次性批量注册的 tool 列表
# 注意：每个 tool 的 function.name 必须与 app.py 暴露的 HTTP 端点路径后缀一致
# （parse_resume→/tools/parse_resume、parse_jd→/tools/parse_jd、
#  match_candidate_to_jd→/tools/match_candidate_to_jd、screen→/tools/screen）。
RECRUITMENT_TOOLS = [PARSE_RESUME_TOOL, PARSE_JD_TOOL, MATCH_TOOL, SCREEN_TOOL]
