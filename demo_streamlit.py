"""
招聘 Agent Demo（Streamlit 单页）
=================================
演示链路：粘贴简历 + 粘贴 JD → 结构化解析 → 加权打分 → 匹配结论 + 差距分析

两种模式（自动判断）：
  • LLM 模式：运行环境已注入 CHATDD_LLM_BASE_URL / CHATDD_LLM_API_KEY 时，
              走真实结构化抽取（任一 OpenAI 兼容端点）
  • 规则模式：未注入端点时自动启用（关键词 + 正则兜底），不联网也能完整演示

启动：
  streamlit run demo_streamlit.py
"""

from __future__ import annotations

import os

import streamlit as st

import simple_extract
from matcher import score_match
from schemas import CandidateProfile, JdRequirements

st.set_page_config(page_title="招聘 Agent Demo", page_icon="🎯", layout="wide")

# ---------------------------------------------------------------------------
# 示例数据（点一下就能演示，不用现场找简历）
# ---------------------------------------------------------------------------
SAMPLE_RESUME = """姓名：张三
学历：硕士（某大学生物信息学）
工作经验：3年
技能：LLM、机器学习、Python、分子生成、蛋白质设计、生物信息
经历：某AI制药公司 算法工程师，负责抗体人源化大模型与分子生成 pipeline
论文：NeurIPS 2025 一篇（分子生成）
"""

SAMPLE_JD = """AI制药算法工程师（水木分子）
学历要求：硕士及以上
经验要求：2年以上
必须技能：LLM、机器学习、Python
优先技能：分子生成、蛋白质设计、抗体工程
目标领域：AI制药、大分子药物
岗位职责：负责药物设计大模型研发与落地
"""


def _has_llm() -> bool:
    """判断是否真的配了可用的 key（顺带加载同目录 .env，并过滤占位符）。"""
    try:
        from llm import load_dotenv

        load_dotenv()
    except Exception:  # noqa: BLE001
        pass
    base = (os.getenv("CHATDD_LLM_BASE_URL") or "").strip()
    key = (os.getenv("CHATDD_LLM_API_KEY") or "").strip()
    # 端点与 key 必须同时存在；再过滤没改过的占位符
    if not base or not key:
        return False
    if len(key) < 16 or "你的" in key or "xxx" in key.lower():
        return False
    return True


def _extract(text: str, is_resume: bool, use_llm: bool):
    """返回 (结构化对象, 错误信息)。LLM 失败自动降级到规则抽取。"""
    if use_llm:
        try:
            from extractors import extract_candidate, extract_jd
            from llm import LLMClient

            llm = LLMClient()
            obj = extract_candidate(text, llm=llm) if is_resume else extract_jd(text, llm=llm)
            return obj, None
        except Exception as e:  # noqa: BLE001
            fallback = (
                simple_extract.simple_extract_resume(text)
                if is_resume
                else simple_extract.simple_extract_jd(text)
            )
            return fallback, f"LLM 调用失败，已回退规则抽取：{e}"
    return (
        simple_extract.simple_extract_resume(text)
        if is_resume
        else simple_extract.simple_extract_jd(text)
    ), None


# ---------------------------------------------------------------------------
# 侧边栏
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ 设置")
    has_key = _has_llm()
    use_llm = st.toggle(
        "启用 LLM 结构化抽取",
        value=has_key,
        help="需运行环境已注入 CHATDD_LLM_BASE_URL 与 CHATDD_LLM_API_KEY。未注入则用规则抽取兜底。",
    )

    # ---- 当前 LLM 连接信息（key 打码，不会泄露）----
    if has_key:
        try:
            from llm import LLMClient

            _cli = LLMClient()
            st.caption(f"🟢 **{_cli.provider}**")
            st.caption(f"model `{_cli.model}` · key `{_cli.masked_key}`")
            st.caption(f"协议 `{_cli.effective_protocol}`")
        except Exception:  # noqa: BLE001
            st.caption("🟢 已检测到 API key")
    else:
        st.caption("🟡 规则模式：无需任何 key，可直接演示")

    if st.button("🔌 测试 LLM 连通", use_container_width=True):
        try:
            from llm import LLMClient

            with st.spinner("正在真实请求模型…"):
                info = LLMClient().ping()
            msg = f"连通正常 · {info['latency_ms']} ms · {info['model']} · {info.get('protocol', 'openai')} 协议"
            st.success(msg)
        except Exception as e:  # noqa: BLE001
            st.error(str(e))

    st.divider()
    st.markdown("#### 端点如何注入")
    st.code(
        "# 由运行环境（或编排器）注入，代码不内置任何默认端点\n"
        "CHATDD_LLM_BASE_URL=http://<内网推理网关>:<port>/v1\n"
        "CHATDD_LLM_API_KEY=<由平台下发>\n"
        "CHATDD_LLM_MODEL=gpt-4o-mini\n\n"
        "# 未注入时：自动进入规则模式，全程离线可演示",
        language="bash",
    )
    st.caption("说明：本工具不内置公网模型地址，简历文本不会离开当前网络环境。")

    st.divider()
    st.markdown("#### 打分维度权重")
    st.markdown(
        "- 技能覆盖 **0.35**\n- 学历 **0.20**\n- 经验年限 **0.20**\n- 领域契合 **0.25**"
    )
    st.caption("硬门槛：缺 must-have 技能 / 学历或经验不达标 → 结论封顶「待定」")

# ---------------------------------------------------------------------------
# 主界面
# ---------------------------------------------------------------------------
st.title("🎯 招聘 Agent Demo")
st.caption("简历 × JD 智能匹配 · 结构化解析 → 加权打分 → 匹配结论")

col1, col2 = st.columns(2)
with col1:
    st.subheader("📄 简历")
    resume_text = st.text_area("粘贴简历文本", value=SAMPLE_RESUME, height=260, key="resume")
with col2:
    st.subheader("📋 岗位 JD")
    jd_text = st.text_area("粘贴岗位描述", value=SAMPLE_JD, height=260, key="jd")

run = st.button("🚀 开始匹配", type="primary", use_container_width=True)

if run:
    if not resume_text.strip() or not jd_text.strip():
        st.error("简历和 JD 都要填内容")
        st.stop()

    with st.spinner("解析中…"):
        cand, err1 = _extract(resume_text, True, use_llm)
        jd, err2 = _extract(jd_text, False, use_llm)
        result = score_match(cand, jd)

    for e in (err1, err2):
        if e:
            st.warning(e)

    st.divider()

    # ---- 总分与结论 ----
    color_map = {
        "强烈推荐": "#16a34a",
        "推荐": "#2563eb",
        "待定": "#d97706",
        "不推荐": "#dc2626",
    }
    rc = color_map.get(result.recommendation, "#6b7280")

    m1, m2 = st.columns([1, 2])
    with m1:
        st.metric("匹配总分", f"{result.total_score} / 100")
    with m2:
        st.markdown(
            f"<div style='margin-top:8px;padding:14px 18px;border-radius:10px;"
            f"background:{rc}18;border-left:5px solid {rc};'>"
            f"<span style='font-size:15px;color:#666;'>匹配结论</span><br>"
            f"<span style='font-size:26px;font-weight:700;color:{rc};'>"
            f"{result.recommendation}</span></div>",
            unsafe_allow_html=True,
        )

    # ---- 维度得分 ----
    st.subheader("📊 各维度得分")
    for d in result.dimensions:
        c1, c2 = st.columns([1, 4])
        with c1:
            st.markdown(f"**{d.name}**<br><span style='color:#666;font-size:12px;'>权重 {d.weight}</span>",
                        unsafe_allow_html=True)
            st.markdown(f"<span style='font-size:20px;font-weight:700;'>{d.score:.1f}</span>",
                        unsafe_allow_html=True)
        with c2:
            st.progress(min(max(d.score / 100.0, 0.0), 1.0))
            st.caption(d.detail)

    # ---- 技能命中 ----
    st.subheader("🧩 技能匹配")
    s1, s2 = st.columns(2)
    with s1:
        st.markdown("**✅ 命中**")
        st.success("、".join(result.matched_skills) if result.matched_skills else "无")
    with s2:
        st.markdown("**❌ 缺失硬门槛**")
        if result.missing_must_have_skills:
            st.error("、".join(result.missing_must_have_skills))
        else:
            st.info("无，硬门槛技能全部命中")

    # ---- 差距分析 ----
    if result.gaps:
        st.subheader("⚠️ 差距分析")
        for g in result.gaps:
            st.markdown(f"- {g}")
    else:
        st.success("未发现明显差距")

    # ---- 结构化结果（给 PM 演示「解析能力」）----
    with st.expander("🔍 查看结构化解析结果（可展示给技术方）"):
        a, b = st.columns(2)
        with a:
            st.markdown("**候选人画像**")
            st.json(cand.model_dump())
        with b:
            st.markdown("**岗位需求**")
            st.json(jd.model_dump())

else:
    st.info("👆 点「开始匹配」看结果。示例数据已预填，可直接演示。")
