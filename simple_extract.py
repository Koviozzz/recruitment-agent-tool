"""
简易规则抽取器（无 LLM 兜底）
=============================
用途：Demo 现场没有 LLM key / 网关不通时，也能把简历和 JD 抽出个大概，
保证演示不断档。真实效果请配 LLM（extractors.py），这里只是保底。

抽取方式：关键词词表匹配 + 正则（学历、年限）。
"""

from __future__ import annotations

import re

from schemas import (
    CandidateProfile,
    EducationLevel,
    JdRequirements,
    WorkExperience,
)

# 医疗 AI / 生物制药领域技能词表（可继续加）
SKILL_LEXICON = [
    "LLM", "大模型", "机器学习", "深度学习", "Python", "R", "SQL",
    "分子生成", "蛋白质设计", "抗体工程", "抗体人源化", "生物信息", "生信分析",
    "药物设计", "CADD", "AIDD", "临床研究", "GMP", "药代动力学", "PKPD",
    "Transformer", "NLP", "计算机视觉", "PyTorch", "TensorFlow",
    "流式细胞", "细胞培养", "动物实验", "专利撰写", "SCI论文",
]

DOMAIN_LEXICON = [
    "AI制药", "大分子药物", "小分子药物", "抗体", "基因治疗", "细胞治疗",
    "临床研究", "CRO", "生物信息", "计算生物学", "医疗器械", "公共卫生",
]

_DEGREE_MAP = [
    ("博士后", EducationLevel.POSTDOC),
    ("博士", EducationLevel.PHD),
    ("硕士", EducationLevel.MASTER),
    ("研究生", EducationLevel.MASTER),
    ("本科", EducationLevel.BACHELOR),
    ("学士", EducationLevel.BACHELOR),
    ("大专", EducationLevel.COLLEGE),
    ("专科", EducationLevel.COLLEGE),
]


def _find_degree(text: str) -> EducationLevel:
    """取文本中出现的最高学历。"""
    best = EducationLevel.OTHER
    order = {
        EducationLevel.OTHER: 0, EducationLevel.COLLEGE: 1,
        EducationLevel.BACHELOR: 2, EducationLevel.MASTER: 3,
        EducationLevel.PHD: 4, EducationLevel.POSTDOC: 5,
    }
    for kw, lvl in _DEGREE_MAP:
        if kw in text and order[lvl] > order[best]:
            best = lvl
    return best


def _find_years(text: str) -> float | None:
    """找『3年经验』『5 年以上』这类表述。"""
    patterns = [
        r"(\d+(?:\.\d+)?)\s*年(?:以上)?(?:工作)?经验",
        r"工作\s*(\d+(?:\.\d+)?)\s*年",
        r"(\d+(?:\.\d+)?)\s*年.*?经验",
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
    return None


def _find_skills(text: str) -> list[str]:
    found = []
    low = text.lower()
    for s in SKILL_LEXICON:
        if s.lower() in low:
            found.append(s)
    return found


def _find_domains(text: str) -> list[str]:
    low = text.lower()
    return [d for d in DOMAIN_LEXICON if d.lower() in low]


def _find_name(text: str) -> str | None:
    m = re.search(r"(?:姓名|Name)\s*[:：]\s*([\u4e00-\u9fa5A-Za-z·\s]{2,20})", text)
    if m:
        return m.group(1).strip()
    # 退化：取第一行前 6 个中文字
    first = text.strip().splitlines()[0] if text.strip() else ""
    m2 = re.match(r"([\u4e00-\u9fa5]{2,6})", first)
    return m2.group(1) if m2 else None


def simple_extract_resume(text: str) -> CandidateProfile:
    skills = _find_skills(text)
    domains = _find_domains(text)
    years = _find_years(text)
    degree = _find_degree(text)

    return CandidateProfile(
        name=_find_name(text),
        summary="（规则抽取，未接 LLM）",
        skills=skills,
        highest_degree=degree,
        total_years_experience=years,
        domains=domains,
        current_title=None,
    )


def simple_extract_jd(text: str) -> JdRequirements:
    skills = _find_skills(text)
    domains = _find_domains(text)
    years = _find_years(text)
    degree = _find_degree(text)

    # 规则版：把抽到的技能全当 must_have（Demo 够用）
    return JdRequirements(
        job_title=text.strip().splitlines()[0][:40] if text.strip() else None,
        min_degree=degree,
        min_years_experience=years,
        must_have_skills=skills[:4],
        preferred_skills=skills[4:],
        target_domains=domains,
    )
