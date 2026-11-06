r"""
LLM 连通性自检
==============
一条命令验证「key 对不对 / 网络通不通 / 模型名存不存在 / 结构化抽取能不能用」。

用法：
    python check_llm.py --quick                # 只验证端点连通
    python check_llm.py --base-url http://host:8000/v1 --model gpt-4o-mini
    python check_llm.py                        # 端到端：抽取 + 匹配

前置：运行环境需注入 CHATDD_LLM_BASE_URL / CHATDD_LLM_API_KEY
     （或用 --base-url 显式传入端点）。

退出码：0 全部通过；1 有失败项（可用于 CI / 演示前检查）。
       2 未配置端点（纯离线规则模式无需执行本脚本）。
"""

from __future__ import annotations

import argparse
import os
import sys

from llm import LLMClient, LLMError
from matcher import score_match
from extractors import extract_candidate, extract_jd

DEMO_RESUME = """姓名：张三
学历：硕士（某大学生物信息学）
工作经验：3年
技能：LLM、机器学习、Python、分子生成、蛋白质设计、生物信息
经历：某AI制药公司 算法工程师，负责抗体人源化大模型与分子生成 pipeline
"""

DEMO_JD = """AI制药算法工程师
学历要求：硕士及以上
经验要求：2年以上
必须技能：LLM、机器学习、Python
优先技能：分子生成、蛋白质设计
目标领域：AI制药、大分子药物
"""


def line(ok: bool, msg: str) -> None:
    print(("  ✅ " if ok else "  ❌ ") + msg)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None, help="临时覆盖模型名")
    ap.add_argument("--base-url", default=None, help="临时覆盖 base_url")
    ap.add_argument("--quick", action="store_true", help="只做 ping，不跑端到端抽取")
    args = ap.parse_args()

    print("\n=== 1. 当前配置 ===")
    if not args.base_url and not (os.getenv("CHATDD_LLM_BASE_URL") or "").strip():
        print("  ⚠️  未配置 LLM 端点（CHATDD_LLM_BASE_URL 为空）。")
        print("      纯规则模式（离线演示）无需执行本脚本；如需验证 LLM 链路，")
        print("      请注入端点或用 --base-url 指定，例如：")
        print("      python check_llm.py --base-url http://127.0.0.1:8000/v1")
        return 2
    cli = LLMClient(base_url=args.base_url, model=args.model)
    print(f"  provider : {cli.provider}")
    print(f"  model    : {cli.model}")
    print(f"  api_key  : {cli.masked_key}")
    if "未配置" in cli.masked_key:
        print("\n  ⚠️  没有读到任何 API key。请设置环境变量或创建 .env（参考 .env.example）")
        return 1
    if "占位符" in cli.masked_key:
        print("\n  ⚠️  读到的是 .env 里的示例占位符，不是真 key。")
        print("      请由运行环境注入 CHATDD_LLM_API_KEY（占位符不算真实凭证）。")
        return 1

    print("\n=== 2. 连通性（真实发一个 ping）===")
    try:
        r = cli.ping()
        line(True, f"{r['model']} 响应正常，耗时 {r['latency_ms']} ms")
    except LLMError as e:
        line(False, str(e))
        return 1

    if args.quick:
        print("\n全部通过（quick 模式已跳过端到端抽取）。\n")
        return 0

    print("\n=== 3. 简历结构化抽取 ===")
    try:
        cand = extract_candidate(DEMO_RESUME, llm=cli)
        line(True, f"姓名={cand.name} 学历={cand.highest_degree.value} "
                   f"年限={cand.total_years_experience} 技能={len(cand.skills)} 个")
        print(f"     技能: {'、'.join(cand.skills)}")
    except LLMError as e:
        line(False, str(e))
        return 1

    print("\n=== 4. JD 结构化抽取 ===")
    try:
        jd = extract_jd(DEMO_JD, llm=cli)
        line(True, f"岗位={jd.job_title} 最低学历={jd.min_degree.value} "
                   f"必须技能={jd.must_have_skills}")
    except LLMError as e:
        line(False, str(e))
        return 1

    print("\n=== 5. 加权匹配 ===")
    res = score_match(cand, jd)
    line(True, f"总分 {res.total_score}/100 → 结论：{res.recommendation}")
    for d in res.dimensions:
        print(f"     {d.name:<6} {d.score:>5.1f}  权重 {d.weight}")

    print("\n🎉 全链路通过，可以跑 Demo：")
    print("   python -m streamlit run demo_streamlit.py\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
