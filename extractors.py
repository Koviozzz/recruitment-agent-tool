"""
抽取层：把非结构化简历 / JD 文本 → 结构化对象
=============================================
领域适配：水木分子招聘偏「医疗 AI / 生物制药」，所以提示词里引导模型
关注 大模型(LLM)、药物设计、抗体/蛋白质工程、机器学习、生信、药企经历等标签。
如果你的招聘方向不同，改 system_prompt 即可，不影响下游匹配逻辑。
"""

from __future__ import annotations

from llm import LLMClient
from schemas import CandidateProfile, JdRequirements

_RESUME_SYSTEM = """你是一个招聘信息抽取专家。请将用户提供的简历文本解析为结构化候选人画像。
要求：
- skills：归一化后的能力/技能标签（如 LLM、抗体工程、蛋白质设计、机器学习、Python、GMP、临床研究、生信分析等）。
- highest_degree：取最高学历，取值仅限 other/college/bachelor/master/phd/postdoc。
- total_years_experience：根据工作经历推算的总年限（数字，可含小数）。
- domains：领域标签，按「六轴」取值（对不上时可保留原文短语）：
    靶点/生物学（如 EGFR、PD-1、GLP-1R、KRAS）
    药物模态（小分子 / 抗体 / 双抗 / ADC / 融合蛋白 / 核酸 / 细胞治疗）
    治疗领域（肿瘤 / 代谢 / 免疫 / 神经 / 感染 / 罕见病）
    研发阶段（发现 / 临床前 / IND / I期 / II期 / III期 / 上市后）
    方法学（CADD / AIDD / 结构生物学 / DEL / 蛋白表达纯化 / 生信 / 临床统计）
    监管经验（NMPA / FDA / EMA 申报、IND、NDA、BLA、GMP、GCP）
- experiences：逐段工作经历，包含 company/title/domain/起止年份/highlights。
- publications：论文/专利/核心项目亮点（一句话一条）。
只输出符合 schema 的 JSON，不要额外解释。"""

_JD_SYSTEM = """你是一个招聘信息抽取专家。请将用户提供的岗位描述(JD)解析为结构化需求。
要求：
- job_title：岗位名称。
- min_degree：最低学历门槛，取值仅限 other/college/bachelor/master/phd/postdoc。
- min_years_experience：最低经验年限（数字，可含小数）。
- must_have_skills：硬性必须掌握的技能标签（命中才算满足硬门槛）。
- preferred_skills：优先/加分技能标签（非硬性，缺了不封顶）。
- target_domains：目标领域标签，沿用「六轴」：靶点/生物学、药物模态、治疗领域、研发阶段、方法学、监管经验。

重要：下游匹配引擎只消费以上字段（min_degree / min_years_experience / must_have_skills /
preferred_skills / target_domains）。请把 JD 里的硬性要求准确映射到这些字段：
  · 硬性技能 → must_have_skills
  · 偏好/加分技能 → preferred_skills
  · 学历门槛 → min_degree
  · 经验年限门槛 → min_years_experience
  · 领域偏好 → target_domains
请勿输出额外的『相关经验年限』或逐条加权需求明细，那些字段不会被使用。
只输出符合 schema 的 JSON，不要额外解释。"""


def extract_candidate(text: str, name: str | None = None, llm: LLMClient | None = None) -> CandidateProfile:
    llm = llm or LLMClient()
    prof = llm.extract(_RESUME_SYSTEM, text, CandidateProfile)
    if name and not prof.name:
        prof.name = name
    return prof


def extract_jd(text: str, llm: LLMClient | None = None) -> JdRequirements:
    llm = llm or LLMClient()
    return llm.extract(_JD_SYSTEM, text, JdRequirements)


# ---------------------------------------------------------------------------
# 异步版（O5）：供 FastAPI async 端点 await，不阻塞事件循环。
# 行为与同步版完全一致（同一套提示词 / 降级 / 重试 / 解析）。
# ---------------------------------------------------------------------------
async def aextract_candidate(
    text: str, name: str | None = None, llm: LLMClient | None = None
) -> CandidateProfile:
    llm = llm or LLMClient()
    prof = await llm.aextract(_RESUME_SYSTEM, text, CandidateProfile)
    if name and not prof.name:
        prof.name = name
    return prof


async def aextract_jd(text: str, llm: LLMClient | None = None) -> JdRequirements:
    llm = llm or LLMClient()
    return await llm.aextract(_JD_SYSTEM, text, JdRequirements)
