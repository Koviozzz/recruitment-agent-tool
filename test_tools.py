"""
离线测试：不依赖 LLM，直接验证「匹配打分层」与「Tool 契约」。
运行：
    cd recruitment-agent-tool && python test_tools.py
    cd recruitment-agent-tool && pytest test_tools.py   # 同等用例，pytest 化（N1）
"""

import pytest

from schemas import (
    CandidateProfile,
    EducationLevel,
    JdRequirements,
    WorkExperience,
    RECRUITMENT_TOOLS,
)
from matcher import score_match


# --------------------------------------------------------------------------- #
# 构造器
# --------------------------------------------------------------------------- #
def _sample_candidate() -> CandidateProfile:
    return CandidateProfile(
        name="张三",
        summary="3 年 AI 制药算法经验，专注大模型与分子生成。",
        skills=["LLM", "机器学习", "Python", "分子生成", "蛋白质设计", "生信分析"],
        highest_degree=EducationLevel.MASTER,
        total_years_experience=3.0,
        experiences=[
            WorkExperience(
                company="某 AI 制药公司",
                title="算法工程师",
                domain="AI制药",
                start_year=2023,
                end_year=2026,
                highlights=["负责抗体人源化大模型", "落地分子生成 pipeline"],
            )
        ],
        domains=["AI制药", "大分子药物", "计算生物学"],
        publications=["NeurIPS 2025 一篇（分子生成）"],
        current_title="算法工程师",
    )


def _sample_jd() -> JdRequirements:
    return JdRequirements(
        job_title="AI 制药算法工程师",
        min_degree=EducationLevel.MASTER,
        min_years_experience=2.0,
        must_have_skills=["LLM", "机器学习", "Python"],
        preferred_skills=["蛋白质设计", "分子生成", "抗体工程"],
        target_domains=["AI制药", "大分子药物"],
    )


def _weak_candidate() -> CandidateProfile:
    """故意构造一个不达门槛的候选人，验证硬门槛逻辑。"""
    return CandidateProfile(
        name="李四",
        skills=["Python"],  # 缺 LLM / 机器学习
        highest_degree=EducationLevel.BACHELOR,  # 低于硕士门槛
        total_years_experience=1.0,  # 低于 2 年
        domains=["金融"],
        current_title="应届生",
    )


# --------------------------------------------------------------------------- #
# pytest 用例（N1：边界补充）
# --------------------------------------------------------------------------- #
def test_tool_contract():
    """Tool 名必须与 HTTP 端点后缀一致。"""
    names = [t["function"]["name"] for t in RECRUITMENT_TOOLS]
    assert names == ["parse_resume", "parse_jd", "match_candidate_to_jd", "screen"]


def test_strong_match():
    """强匹配：所有硬门槛达标 → 强烈推荐，hard_breach=False。"""
    r = score_match(_sample_candidate(), _sample_jd())
    assert r.recommendation == "强烈推荐"
    assert r.hard_breach is False
    assert r.missing_must_have_skills == []
    assert r.total_score == pytest.approx(96.5, abs=0.5)


def test_weak_match():
    """弱匹配：缺硬技能 + 学历/经验不达标 → 触发硬门槛。"""
    r = score_match(_weak_candidate(), _sample_jd())
    assert r.recommendation in ("待定", "不推荐")
    assert r.hard_breach is True
    assert r.missing_must_have_skills  # 应缺 LLM/机器学习


def test_no_experience_threshold():
    """JD 不设经验年限门槛时，经验维度计满分且不产生『经验低于门槛』差距。"""
    jd = _sample_jd()
    jd.min_years_experience = None
    cand = _sample_candidate()
    cand.total_years_experience = 0.0  # 即使 0 年，因 JD 无门槛也应满分
    r = score_match(cand, jd)
    exp = next(d for d in r.dimensions if d.name == "经验年限")
    assert exp.score == 100.0
    assert all("经验低于门槛" not in g for g in r.gaps)


def test_empty_domains():
    """JD 未指定目标领域时，领域维度计满分且不产生领域差距。"""
    jd = _sample_jd()
    jd.target_domains = []
    r = score_match(_sample_candidate(), jd)
    dom = next(d for d in r.dimensions if d.name == "领域契合")
    assert dom.score == 100.0
    assert all("领域" not in g for g in r.gaps)


def test_score_boundary_recommend():
    """分数边界：无硬门槛且 total≈65 → 推荐（N1 边界 65）。"""
    jd = JdRequirements(
        job_title="边界岗",
        min_degree=EducationLevel.BACHELOR,
        min_years_experience=None,
        must_have_skills=["sk_a", "sk_b", "sk_c"],
        preferred_skills=[f"p{i}" for i in range(10)],  # 候选全不命中 → 压低技能分
        target_domains=[f"d{i}" for i in range(50)],     # 仅命中 1/50 → 领域分≈2
    )
    cand = CandidateProfile(
        highest_degree=EducationLevel.BACHELOR,
        skills=["sk_a", "sk_b", "sk_c"],  # 全部命中硬门槛（无 missing_must）
        domains=["d0"],                    # 1/50 → dom_score=2
    )
    r = score_match(cand, jd)
    assert r.hard_breach is False
    assert r.total_score == pytest.approx(65.0, abs=0.5)
    assert r.recommendation == "推荐"


def test_score_boundary_pending():
    """分数边界：无硬门槛且 total<65 → 待定（N1 边界 50/65）。"""
    jd = JdRequirements(
        job_title="边界岗",
        min_degree=EducationLevel.BACHELOR,
        min_years_experience=None,
        must_have_skills=["sk_a", "sk_b", "sk_c"],
        preferred_skills=[f"p{i}" for i in range(10)],
        target_domains=[f"d{i}" for i in range(50)],  # 0/50 → 领域分=0
    )
    cand = CandidateProfile(
        highest_degree=EducationLevel.BACHELOR,
        skills=["sk_a", "sk_b", "sk_c"],
        domains=[],  # 0/50 → dom_score=0
    )
    r = score_match(cand, jd)
    assert r.hard_breach is False
    assert r.total_score == pytest.approx(64.5, abs=0.5)
    assert r.recommendation == "待定"


def test_education_gap_2():
    """学历差两级（本科 vs 博士）：硬门槛触发，结论待定，差距含学历项。"""
    jd = _sample_jd()
    jd.min_degree = EducationLevel.PHD  # 要求博士
    cand = _sample_candidate()
    cand.highest_degree = EducationLevel.BACHELOR  # 实际本科
    r = score_match(cand, jd)
    assert r.hard_breach is True
    assert r.recommendation == "待定"
    assert any("学历低于门槛" in g for g in r.gaps)


def test_weights_override():
    """N3：传入 weights 应覆盖默认权重，使总分按新权重计算。"""
    cand, jd = _sample_candidate(), _sample_jd()
    # 仅按技能维度计分
    r = score_match(cand, jd, weights={"skill": 1.0, "education": 0.0, "experience": 0.0, "domain": 0.0})
    # 样本里技能维度约 90 分（must 全中、pref 命中 2/3）
    assert r.total_score == pytest.approx(90.0, abs=1.0)
    # 默认权重下总分应明显更高（约 96.5），证明 override 生效
    r_default = score_match(cand, jd)
    assert r_default.total_score > r.total_score


def test_explanation_filled():
    """N5/N7：结果自带自然语言解释；高分但触硬门槛时解释点明『硬门槛』。"""
    # 普通强匹配：解释非空
    r_ok = score_match(_sample_candidate(), _sample_jd())
    assert r_ok.explanation
    assert "硬门槛" not in r_ok.explanation  # 未触发硬门槛不应出现该提示

    # 触发硬门槛但其余维度满分 → 总分偏高却被压低，解释应点明硬门槛（N7）
    jd = _sample_jd()
    cand = _sample_candidate()
    cand.skills = ["机器学习", "Python"]  # 缺 LLM（must 第一项）→ 触发硬门槛
    r_breach = score_match(cand, jd)
    assert r_breach.hard_breach is True
    assert "硬门槛" in r_breach.explanation


# --------------------------------------------------------------------------- #
# 兼容 `python test_tools.py` 直接运行
# --------------------------------------------------------------------------- #
def main():
    tests = [
        test_tool_contract,
        test_strong_match,
        test_weak_match,
        test_no_experience_threshold,
        test_empty_domains,
        test_score_boundary_recommend,
        test_score_boundary_pending,
        test_education_gap_2,
        test_weights_override,
        test_explanation_filled,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"✓ {t.__name__}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"✗ {t.__name__}: {e!r}")
    print(f"\n=== 离线测试：{passed}/{len(tests)} 通过 ===")
    if passed != len(tests):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
