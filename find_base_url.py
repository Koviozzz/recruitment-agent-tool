r"""
只有 key、不知道 base_url 怎么办 —— 自动反查 + 地址验证
=======================================================
模式一：全量反查（不知道 key 属于谁）
    .\.venv\Scripts\python.exe find_base_url.py
    .\.venv\Scripts\python.exe find_base_url.py --apply     # 命中后自动写回 .env

模式二：验证某个地址（已经有候选域名，想知道对不对、有哪些模型可用）
    .\.venv\Scripts\python.exe find_base_url.py --try open.bigmodel.cn
    .\.venv\Scripts\python.exe find_base_url.py --try https://api.xxx.com/v1 --apply

判读规则（关键，别只看 200）：
    200          → 这家就是你的家，可以直接用
    400/404/422  → 地址对了、只是模型名不对（说明鉴权已通过，同样是强信号）
    401/403      → key 不是这家的
    429          → key 是对的，但额度/频率被限
    410          → 接口已下线，别再用了
    连接失败/超时 → 网络到不了（国内直连 OpenAI 官方通常就是这种）
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI

ENV_PATH = Path(__file__).parent / ".env"

# (显示名, base_url, 试探用模型名, 备注)
PROVIDERS: list[tuple[str, str, str, str]] = [
    ("OpenAI 官方", "https://api.openai.com/v1", "gpt-4o-mini", "国内需代理"),
    ("DeepSeek 深度求索", "https://api.deepseek.com/v1", "deepseek-chat", "国内直连"),
    ("智谱 AI (GLM)", "https://open.bigmodel.cn/api/paas/v4", "glm-4-flash", "国内直连"),
    ("阿里云百炼 (通义)", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-turbo", "国内直连"),
    ("硅基流动 SiliconFlow", "https://api.siliconflow.cn/v1", "Qwen/Qwen2.5-7B-Instruct", "国内直连"),
    ("月之暗面 Kimi", "https://api.moonshot.cn/v1", "moonshot-v1-8k", "国内直连"),
    ("阶跃星辰 StepFun", "https://api.stepfun.com/v1", "step-1-8k", "国内直连"),
    ("MiniMax", "https://api.minimax.chat/v1", "abab6.5s-chat", "国内直连"),
    ("腾讯混元", "https://api.hunyuan.cloud.tencent.com/v1", "hunyuan-turbos-latest", "国内直连"),
    ("火山方舟 (豆包)", "https://ark.cn-beijing.volces.com/api/v3", "doubao-1-5-lite-32k-250115", "需 endpoint id"),
    ("OpenRouter", "https://openrouter.ai/api/v1", "openai/gpt-4o-mini", "需外网"),
    ("百度千帆", "https://qianfan.baidubce.com/v2", "ernie-4.0-8k", "国内直连"),
]

# 不知道模型名时按这个顺序盲试（覆盖国内外最常见的几个）
CANDIDATE_MODELS = [
    "gpt-4o-mini",
    "gpt-4o",
    "glm-4-flash",
    "deepseek-chat",
    "qwen-turbo",
    "moonshot-v1-8k",
    "Qwen/Qwen2.5-7B-Instruct",
]

# 验证模式里 OpenAI 协议只试这几个，够用了，别浪费额度
OPENAI_TRY_MODELS = ["gpt-4o-mini", "gpt-4o", "deepseek-chat", "glm-4-flash"]

# Claude 系（含 Claude Code 中转站）的常见模型名
ANTHROPIC_MODELS = [
    "claude-sonnet-4-5",
    "claude-sonnet-4-5-20250929",
    "claude-sonnet-4-20250514",
    "claude-3-7-sonnet-latest",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
]

RANK = {"OK": 0, "MODEL": 1, "QUOTA": 2, "SERVER": 2, "AUTH": 3, "GONE": 3, "NET": 4}
LABEL = {
    "OK": "✅ 命中：地址和模型都对",
    "MODEL": "🟡 地址对了，模型名不对",
    "QUOTA": "🟡 key 有效，但被限流/欠费",
    "SERVER": "🟡 地址对了，服务端异常",
    "AUTH": "⬜ key 不是这家的",
    "GONE": "⬜ 该地址已停用",
    "NET": "⬜ 网络到不了",
}


def _pad(s: str, width: int) -> str:
    """按显示宽度对齐（中文算 2 个字符宽），否则中英混排会歪。"""
    w = sum(2 if ord(c) > 127 else 1 for c in s)
    return s + " " * max(0, width - w)


@dataclass
class Result:
    name: str
    base_url: str
    model: str
    note: str
    verdict: str
    detail: str
    latency_ms: int


def _key_hint(key: str) -> str:
    """根据 key 的书写格式先给个猜测，省得盲试。"""
    k = key.strip()
    if k.startswith("sk-or-v1-"):
        return "格式像 OpenRouter（sk-or-v1- 开头）"
    if k.startswith("sk-ant-"):
        return "格式像 Anthropic Claude（但它不是 OpenAI 兼容协议，得走转换层）"
    if k.startswith("bce-v3/") or "ALTAK" in k:
        return "格式像百度千帆（bce-v3/ALTAK- 开头）"
    m = re.fullmatch(r"([0-9a-fA-F]{16,32})\.([A-Za-z0-9]{8,})", k)
    if m:
        return "格式像智谱 AI（<id>.<secret> 中间一个英文句点）"
    if k.startswith("sk-proj-"):
        return "格式像 OpenAI 官方的新版项目 key（sk-proj- 开头）"
    if k.startswith("sk-") and len(k) >= 40:
        return "格式像通用 sk- key（OpenAI / DeepSeek / 硅基 / 中转商都长这样，需要实测）"
    if k.startswith("sk-"):
        return "格式像通用 sk- key（偏短，可能是中转商或内部网关发的）"
    return "格式不明显（内部网关、自建服务、或非标准前缀都可能长这样）"


def probe_chat(base_url: str, model: str, key: str, timeout: float) -> tuple[str, str]:
    """发一个极小的对话请求，返回 (verdict, detail)。"""
    try:
        cli = OpenAI(base_url=base_url, api_key=key, timeout=timeout, max_retries=0)
        cli.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
        )
        return "OK", "200"
    except Exception as e:  # noqa: BLE001
        status = getattr(e, "status_code", None)
        if status in (400, 404, 422):
            return "MODEL", f"{status} 模型名不匹配"
        if status == 429:
            return "QUOTA", "429 限流/欠费"
        if status in (401, 403):
            return "AUTH", f"{status} 鉴权失败"
        if status == 410:
            return "GONE", "410 接口已下线"
        if status:
            return "SERVER", f"{status} 服务端异常"
        return "NET", type(e).__name__


def probe(item: tuple[str, str, str, str], key: str, timeout: float) -> Result:
    name, base, model, note = item
    t0 = time.time()
    verdict, detail = probe_chat(base, model, key, timeout)
    return Result(name, base, model, note, verdict, detail, int((time.time() - t0) * 1000))


def probe_anthropic(base_url: str, model: str, key: str, timeout: float) -> tuple[int | None, str]:
    """走 Anthropic 原生 /v1/messages。同时带 x-api-key 和 Bearer 两种鉴权头。"""
    from llm import _http_post, LLMError

    s = base_url.rstrip("/")
    url = s + "/messages" if s.endswith("/v1") else s + "/v1/messages"
    headers = {
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
        "x-api-key": key,
        "authorization": f"Bearer {key}",
    }
    payload = {"model": model, "max_tokens": 4, "messages": [{"role": "user", "content": "hi"}]}
    try:
        status, text = _http_post(url, headers, payload, timeout)
        return status, (text or "").replace("\n", " ")[:160]
    except LLMError as e:
        return None, str(e)[:120]


def normalize_urls(raw: str) -> list[str]:
    """把用户随手写的域名补全，并顺带试 /v1 变体。"""
    s = raw.strip().rstrip("/")
    if not s.startswith(("http://", "https://")):
        s = "https://" + s
    out = [s]
    tail_is_version = re.search(r"/(v\d+|[a-z]+/v\d+)$", s) or "/api/paas" in s
    if not tail_is_version:
        out.append(s + "/v1")
    return out


def list_models(base_url: str, key: str, timeout: float) -> tuple[str, list[str], str]:
    """
    先试 /models 接口：成功就直接拿到「这个地址下你能用哪些模型」，
    这比盲猜模型名高效得多。返回 (verdict, model_ids, detail)。
    """
    try:
        cli = OpenAI(base_url=base_url, api_key=key, timeout=timeout, max_retries=0)
        data = cli.models.list()
        ids = sorted({m.id for m in data.data})
        return "OK", ids, f"共 {len(ids)} 个模型"
    except AttributeError:
        # 请求通了，但返回体不是标准 OpenAI 形状（有些网关 /models 返回自定义结构）
        return "NOLIST", [], "该地址能连上但 /models 响应格式不标准（改试对话接口）"
    except Exception as e:  # noqa: BLE001
        status = getattr(e, "status_code", None)
        if status in (401, 403):
            return "AUTH", [], f"{status} 鉴权失败"
        if status == 410:
            return "GONE", [], "410 接口已下线"
        if status in (404, 400):
            return "NOLIST", [], f"{status} 该地址没有 /models 接口（不代表地址错，改试对话接口）"
        if status in (405, 501):
            return "PATH", [], f"{status} 路径不对：这个地址下面没有该接口，去文档核对路径（如 /v1、/api/paas/v4）"
        if status:
            return "SERVER", [], f"{status} 服务端异常"
        return "NET", [], type(e).__name__


def read_env_key() -> str:
    try:
        from llm import load_dotenv

        load_dotenv()
    except Exception:  # noqa: BLE001
        pass
    import os

    for var in ("OPENAI_API_KEY", "CHATDD_LLM_API_KEY"):
        v = (os.getenv(var) or "").strip()
        if v and "你的" not in v and "xxx" not in v.lower():
            return v
    return ""


def apply_env(base_url: str, model: str, key: str | None) -> None:
    """把命中的地址/模型写回 .env（保留其余行，重复项注释掉）。

    注意：本运行时客户端（llm.py）仅支持 OpenAI 兼容协议，因此这里只写
    OPENAI_BASE_URL / OPENAI_MODEL / OPENAI_API_KEY，不再写 LLM_PROTOCOL。
    历史上曾写 LLM_PROTOCOL=anthropic，但运行时从未读取，属误导配置；
    若 .env 里残留该行，在此一并注释掉。
    """
    pairs = {"OPENAI_BASE_URL": base_url, "OPENAI_MODEL": model}
    if key:
        pairs["OPENAI_API_KEY"] = key

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        m = re.match(r"^\s*(OPENAI_BASE_URL|OPENAI_MODEL|OPENAI_API_KEY|LLM_PROTOCOL)\s*=", line)
        if m:
            k = m.group(1)
            if k == "LLM_PROTOCOL":
                # 历史残留，运行时不支持该协议，注释掉避免误导
                out.append("# " + line)
            elif k in pairs and k not in seen:
                out.append(f"{k}={pairs[k]}")
                seen.add(k)
            else:
                out.append("# " + line)
        else:
            out.append(line)
    for k, v in pairs.items():
        if k not in seen:
            out.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"\n已写回 {ENV_PATH}")


def cmd_try(urls: list[str], key: str, timeout: float, apply_it: bool) -> int:
    """验证候选地址：先列模型，列不出来就盲试几个常见模型名。"""
    for raw in urls:
        for base in normalize_urls(raw):
            print(f"\n--- 试探 {base}")

            # [0] 先看有没有 /models：有的话能直接列出可用模型，省得盲猜
            v0, ids, d0 = list_models(base, key, timeout)
            if v0 == "OK" and ids:
                print(f"  [0/2] /models 可用（{d0}），这个地址下的模型：")
                for i in ids[:25]:
                    print(f"        - {i}")
                if len(ids) > 25:
                    print(f"        … 另有 {len(ids) - 25} 个")
            else:
                print(f"  [0/2] /models 不可用（{d0}），继续试对话接口")

            # [1] OpenAI 兼容协议
            print("  [1/2] OpenAI 兼容协议 /v1/chat/completions")
            for m in OPENAI_TRY_MODELS:
                v, d = probe_chat(base, m, key, timeout)
                print(f"        {_pad(m, 32)} {v:<6} {d}")
                if v == "OK":
                    print(f"\n  ✅ 命中（OpenAI 协议）：base_url={base}  model={m}")
                    if apply_it:
                        apply_env(base, m, None)
                        print("  已写回 .env")
                    return 0

            # [2] Anthropic 原生协议（Claude / Claude Code 中转站走这条）
            print("  [2/2] Anthropic 原生协议 /v1/messages")
            for m in ANTHROPIC_MODELS:
                code, body = probe_anthropic(base, m, key, timeout)
                print(f"        {_pad(m, 32)} {str(code):<6} {body[:80]}")
                if code == 200:
                    print(f"\n  ✅ 命中（Anthropic 原生协议）：base_url={base}  model={m}")
                    print(
                        "  ⚠️ 注意：本运行时客户端 llm.py 仅支持 OpenAI 兼容协议"
                        "（/v1/chat/completions），不能直接调用 Anthropic 原生 /v1/messages。"
                        "若要接入，请在该端点前加一层 OpenAI 兼容转换网关（如 litellm / 中转商），"
                        "或用 OpenAI 兼容地址重新 --try。此处不写 LLM_PROTOCOL=anthropic，"
                        "以免产生『已支持』的误导。"
                    )
                    # 不写 .env：运行时暂不支持该协议，避免误导
                    return 0
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=None, help="直接传 key，不传则读 .env")
    ap.add_argument("--timeout", type=float, default=8.0, help="单次请求超时秒数，默认 8")
    ap.add_argument("--apply", action="store_true", help="命中后自动写回 .env")
    ap.add_argument("--try", dest="try_urls", nargs="+", default=None,
                    help="验证指定域名，如 --try open.bigmodel.cn 或 --try https://api.xxx.com/v1")
    args = ap.parse_args()

    key = (args.key or read_env_key()).strip()
    if not key:
        print("❌ 没读到 key。请在 .env 里填 OPENAI_API_KEY，或用 --key 传入。")
        return 1

    if args.try_urls:
        print(f"\n=== 验证模式 · key={key[:6]}...{key[-4:]} ===")
        return cmd_try(args.try_urls, key, args.timeout, args.apply)

    print("\n=== 0. key 格式推测 ===")
    print(f"  key      : {key[:6]}...{key[-4:]}（共 {len(key)} 字符）")
    print(f"  推测     : {_key_hint(key)}")
    print("  注意     : 格式只是线索，最终以实测为准（中转商的 key 可能伪装成任何格式）")

    print(f"\n=== 1. 正在并行探测 {len(PROVIDERS)} 家网关（每家超时 {args.timeout:g}s）===")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda it: probe(it, key, args.timeout), PROVIDERS))

    results.sort(key=lambda r: (RANK[r.verdict], r.latency_ms))

    print("\n=== 2. 探测结果（越像「你的家」的排越前）===")
    for r in results:
        print(f"  {_pad(LABEL[r.verdict], 30)} {_pad(r.name, 24)} {r.base_url}")
        if r.verdict != "AUTH":
            print(f"{'':6}试探模型={r.model}  详情={r.detail}  {r.latency_ms}ms  {r.note}")

    hits = [r for r in results if r.verdict == "OK"]
    near = [r for r in results if r.verdict in ("MODEL", "QUOTA", "SERVER")]

    print("\n=== 3. 结论 ===")
    if hits:
        best = hits[0]
        print(f"  ✅ 找到了：{best.name}")
        print(f"     OPENAI_BASE_URL={best.base_url}")
        print(f"     OPENAI_MODEL={best.model}   （这个模型名已验证可通）")
        if len(hits) > 1:
            print("     另外这些也通了（说明你的 key 是可跨家的通用/中转 key）：")
            for r in hits[1:]:
                print(f"       - {r.name}  {r.base_url}")
        if args.apply:
            apply_env(best.base_url, best.model, args.key)
        else:
            print("\n  想自动写回 .env 就加 --apply 再跑一次。")
        return 0

    if near:
        best = near[0]
        print(f"  🟡 地址大概率是这家的：{best.name}  ({best.base_url})")
        if best.verdict == "SERVER":
            print(f"     服务端返回 {best.detail}，鉴权未必有问题；先确认这个地址的路径对不对")
            print("     （有的网关是 /api/v1 而不是 /v1，去文档核对一下）。")
        elif best.verdict == "QUOTA":
            print("     key 认这家，只是被限流或欠费 —— 充值或稍等会儿再试。")
        else:
            print(f"     但试探模型 {best.model} 不通（{best.detail}）。")
            print("     用 --try 验证这个地址，它会直接列出可用模型名：")
            print(f"     .\\.venv\\Scripts\\python.exe find_base_url.py --try {best.base_url}")
        if args.apply:
            apply_env(best.base_url, best.model, args.key)
        return 0

    print("  ❌ 常见公开网关都不认这把 key。可能是这三种情况：")
    print("     1) 公司内部推理网关 → 域名是内网地址，只能问平台同事要 base_url，猜不出来")
    print("     2) 中转商发的 key → 去他后台的『令牌 / API 文档』页找『接口地址』")
    print("     3) 网络问题 → 若全部显示 NET，先确认本机能不能上网 / 是否走了代理")
    print("\n  拿到候选域名后，用 --try 验证：")
    print("     .\\.venv\\Scripts\\python.exe find_base_url.py --try <域名>")
    return 1


if __name__ == "__main__":
    sys.exit(main())
