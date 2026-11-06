"""
匹配打分层（确定性，无需 LLM，便于离线测试与可复现）
=====================================================
维度与权重（可在 ChatDD 里按岗位类型调参）：
  技能覆盖 0.35  |  学历 0.20  |  经验年限 0.20  |  领域契合 0.25

硬门槛规则：
  - 缺失 JD 的 must_have 技能  → 总分封顶，且结论不会高于「待定」
  - 学历/经验低于门槛          → 对应维度低分，并计入差距
"""

from __future__ import annotations

from typing import Dict, List, Optional

from schemas import (
    CandidateProfile,
    DimensionScore,
    EducationLevel,
    JdRequirements,
    MatchResult,
)

# 学历层级排序，用于门槛比对
_DEGREE_ORDER = {
    EducationLevel.OTHER: 0,
    EducationLevel.COLLEGE: 1,
    EducationLevel.BACHELOR: 2,
    EducationLevel.MASTER: 3,
    EducationLevel.PHD: 4,
    EducationLevel.POSTDOC: 5,
}

# 各维度默认权重
WEIGHTS = {
    "skill": 0.35,
    "education": 0.20,
    "experience": 0.20,
    "domain": 0.25,
}


def _norm(items):
    return {str(i).strip().lower() for i in (items or [])}


def _skill_dimension(candidate: CandidateProfile, jd: JdRequirements):
    cand_skills = _norm(candidate.skills)
    must = _norm(jd.must_have_skills)
    pref = _norm(jd.preferred_skills)

    matched_must = [s for s in jd.must_have_skills if s.strip().lower() in cand_skills]
    missing_must = [s for s in jd.must_have_skills if s.strip().lower() not in cand_skills]
    matched_pref = [s for s in jd.preferred_skills if s.strip().lower() in cand_skills]

    cov_must = (len(matched_must) / len(must)) * 100 if must else 100.0
    cov_pref = (len(matched_pref) / len(pref)) * 100 if pref else 100.0
    # 硬门槛占 70%，加分项占 30%
    score = 0.7 * cov_must + 0.3 * cov_pref

    detail = (
        f"硬技能命中 {len(matched_must)}/{len(must)}，"
        f"加分技能命中 {len(matched_pref)}/{len(pref)}。"
    )
    if missing_must:
        detail += f"缺失硬门槛技能：{', '.join(missing_must)}。"

    return score, matched_must + matched_pref, missing_must, detail


def _education_dimension(candidate: CandidateProfile, jd: JdRequirements):
    c_lvl = _DEGREE_ORDER.get(candidate.highest_degree, 0)
    j_lvl = _DEGREE_ORDER.get(jd.min_degree, 0)
    if c_lvl >= j_lvl:
        score = 100.0
        detail = f"最高学历 {candidate.highest_degree.value} 满足门槛 {jd.min_degree.value}。"
    else:
        # 差一级按比例给分，差两级以上直接很低
        gap = j_lvl - c_lvl
        score = max(0.0, 100.0 - gap * 50.0)
        detail = f"最高学历 {candidate.highest_degree.value} 低于门槛 {jd.min_degree.value}，差距 {gap} 级。"
    return score, detail


def _experience_dimension(candidate: CandidateProfile, jd: JdRequirements):
    if jd.min_years_experience is None:
        return 100.0, "JD 未设经验年限门槛。"
    cand_years = candidate.total_years_experience or 0.0
    if cand_years >= jd.min_years_experience:
        score = 100.0
        detail = f"经验 {cand_years:.1f} 年，满足门槛 {jd.min_years_experience} 年。"
    else:
        score = max(0.0, (cand_years / jd.min_years_experience) * 100.0)
        detail = f"经验 {cand_years:.1f} 年，低于门槛 {jd.min_years_experience} 年。"
    return score, detail


def _domain_dimension(candidate: CandidateProfile, jd: JdRequirements):
    cand_domains = _norm(candidate.domains)
    target = _norm(jd.target_domains)
    if not target:
        return 100.0, "JD 未指定目标领域。"
    overlap = cand_domains & target
    score = (len(overlap) / len(target)) * 100.0
    detail = (
        f"领域命中 {len(overlap)}/{len(target)}：{', '.join(sorted(overlap)) or '无'}。"
        if overlap
        else "领域无重合，契合度低。"
    )
    return score, detail


def score_match(
    candidate: CandidateProfile,
    jd: JdRequirements,
    weights: Optional[Dict[str, float]] = None,
) -> MatchResult:
    # N3：权重可配置。传入 weights 时仅覆盖同名维度，其余沿用默认 WEIGHTS。
    w = dict(WEIGHTS)
    if weights:
        for k, v in weights.items():
            if k in w:
                w[k] = float(v)

    skill_score, matched_skills, missing_must, skill_detail = _skill_dimension(candidate, jd)
    edu_score, edu_detail = _education_dimension(candidate, jd)
    exp_score, exp_detail = _experience_dimension(candidate, jd)
    dom_score, dom_detail = _domain_dimension(candidate, jd)

    dims = [
        DimensionScore(name="技能覆盖", score=round(skill_score, 1), weight=w["skill"], detail=skill_detail),
        DimensionScore(name="学历", score=round(edu_score, 1), weight=w["education"], detail=edu_detail),
        DimensionScore(name="经验年限", score=round(exp_score, 1), weight=w["experience"], detail=exp_detail),
        DimensionScore(name="领域契合", score=round(dom_score, 1), weight=w["domain"], detail=dom_detail),
    ]

    total = sum(d.score * d.weight for d in dims)

    # 硬门槛判定
    hard_breach = bool(missing_must) or (edu_score < 100.0) or (exp_score < 100.0 and jd.min_years_experience is not None)

    gaps: List[str] = []
    if missing_must:
        gaps.append("缺失硬门槛技能：" + ", ".join(missing_must))
    if edu_score < 100.0:
        gaps.append(f"学历低于门槛（候选 {candidate.highest_degree.value} vs 要求 {jd.min_degree.value}）")
    if exp_score < 100.0 and jd.min_years_experience is not None:
        gaps.append(f"经验低于门槛（候选 {candidate.total_years_experience or 0:.1f} 年 vs 要求 {jd.min_years_experience} 年）")
    if dom_score < 50.0:
        gaps.append("领域契合度偏低，需评估转岗/培养成本")

    # 结论
    if hard_breach:
        recommendation = "不推荐" if (missing_must and (edu_score < 100.0 or exp_score < 100.0)) else "待定"
    else:
        if total >= 80:
            recommendation = "强烈推荐"
        elif total >= 65:
            recommendation = "推荐"
        elif total >= 50:
            recommendation = "待定"
        else:
            recommendation = "不推荐"

    result = MatchResult(
        candidate_name=candidate.name,
        job_title=jd.job_title,
        total_score=round(total, 1),
        dimensions=dims,
        matched_skills=matched_skills,
        missing_must_have_skills=missing_must,
        gaps=gaps,
        recommendation=recommendation,
        hard_breach=hard_breach,
    )
    # N5/N7：离线即用规则版解释，避免「高分但被硬门槛限制」的误导。
    result.explanation = explain_match(result)
    return result


# 下面这层可选：用 LLM 把结构化结果写成一段给 HR 看的推荐语（需要 LLM，非离线）
def explain_match(result: MatchResult, llm=None) -> str:
    if llm is None:
        # 无 LLM 时用规则拼一段（离线可用，无需联网）
        base = (
            f"匹配总分 {result.total_score}/100，结论：{result.recommendation}。"
        )
        # N7：总分不低却因硬门槛被压低结论时，显式提示，避免「高分=通过」误读。
        if result.hard_breach and result.total_score >= 50:
            base += (
                f"（注意：总分虽达 {result.total_score}，但因触发硬门槛，"
                f"结论被限制为『{result.recommendation}』，应以门槛项为准。）"
            )
        if result.gaps:
            base += "主要差距：" + "；".join(result.gaps) + "。"
        return base
    from .llm import LLMClient
    llm = llm or LLMClient()
    prompt = (
        "你是招聘顾问。根据下面的匹配结果，写一段 2-3 句给 HR 的推荐语，"
        "包含总评、亮点、风险与下一步建议。\n"
        f"{result.model_dump_json(indent=2)}"
    )
    return llm.complete("你是一名严谨的招聘顾问。", prompt)
