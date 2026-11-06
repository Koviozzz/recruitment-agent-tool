"""
LLM 客户端封装（OpenAI 兼容协议）
======================================
设计约束（v1，与「离线可跑、简历不出内网」前提一致）：

  - 不内置任何默认端点。地址必须由外部注入：
        CHATDD_LLM_BASE_URL / CHATDD_LLM_API_KEY / CHATDD_LLM_MODEL
    未注入时构造对象不会报错，但一调用 ping()/extract()/complete()
    就会在 _check_config() 处被拦下，并给出明确提示。
    这样保证「没配置 = 启动即失败」，而不是悄悄连到某个公网地址。

  - 只支持 OpenAI 兼容协议。不内置 Anthropic 原生协议、
    不做协议自动切换，也不针对任何第三方中转做适配。

  - key 一律从环境变量读取，代码里不存在任何字面量密钥。

已知的模型差异（已适配）：
  1. gpt-5* / o1* / o3* / o4* 不接受自定义 temperature，
     max_tokens 参数名为 max_completion_tokens —— 自动识别。
  2. 部分网关不支持 json_schema 结构化输出；失败自动降级 json_object，
     并剥掉模型偶尔加的 ```json 代码围栏。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import warnings
from pathlib import Path
from typing import Any, Type, TypeVar

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger("recruitment_tool.llm")


# ---------------------------------------------------------------------------
# .env 加载（手写实现，不额外引入 python-dotenv）
# ---------------------------------------------------------------------------
def load_dotenv(path: str | Path | None = None) -> None:
    """把项目根目录 .env 里的键值对注入 os.environ（已存在的变量不覆盖）。"""
    p = Path(path) if path else Path(__file__).parent / ".env"
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


load_dotenv()

# 防御：本客户端仅支持 OpenAI 兼容协议。若环境里残留 LLM_PROTOCOL=anthropic 等
# 非 openai 取值（历史脚本曾写入），明确提示它被忽略，避免误以为已支持该协议。
_protocol_env = os.getenv("LLM_PROTOCOL")
if _protocol_env and _protocol_env.strip().lower() not in ("openai", ""):
    warnings.warn(
        f"LLM_PROTOCOL={_protocol_env!r} 被忽略：本客户端仅支持 OpenAI 兼容协议"
        "（/v1/chat/completions）。要接入 Anthropic 等非兼容端点，请在其前加一层"
        " OpenAI 兼容转换网关（如 litellm），或为 llm.py 扩展对应协议。",
        stacklevel=2,
    )


# 推理模型前缀：不接受自定义 temperature，用 max_completion_tokens
_REASONING_PREFIXES = ("o1", "o3", "o4", "gpt-5")


def _is_reasoning_model(model: str) -> bool:
    m = model.lower()
    return any(m.startswith(p) for p in _REASONING_PREFIXES)


def _strip_fence(text: str) -> str:
    """去掉模型偶尔包的 ```json ... ``` 围栏。"""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _http_post(url: str, headers: dict, payload: dict, timeout: float) -> tuple[int, str]:
    """
    发 POST 并返回 (status_code, text)。
    兼容三种客户端：httpx / httpx2（部分环境里装的是这个名字）/ requests。
    有哪个用哪个，都没有才报错。
    """
    last_err: Exception | None = None
    for mod_name in ("httpx", "httpx2"):
        try:
            mod = __import__(mod_name)
        except ImportError as e:
            last_err = e
            continue
        try:
            r = mod.post(url, headers=headers, json=payload, timeout=timeout)
            return r.status_code, r.text
        except Exception as e:  # noqa: BLE001
            last_err = e

    try:
        import requests

        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        return r.status_code, r.text
    except Exception as e:  # noqa: BLE001
        raise LLMError(
            f"网络请求失败：{type(e).__name__}: {str(e)[:200]}。检查 base_url 是否可达。"
        ) from (last_err or e)


class LLMError(RuntimeError):
    """带人话说明的 LLM 调用异常，方便在 UI 里直接展示。

    额外携带 category / status_code，供上层（app.py）把异常映射成合适的
    HTTP 状态码（O3）：
      - category="config"   → 400（配置/鉴权错误，客户端应改配置）
      - category="rate"     → 429（限流/额度，可重试）
      - category="gateway"  → 502（网关/网络/超时，瞬时可重试）
      - category="unknown"  → 500（未预期错误）
    status_code 为该类别对应的建议 HTTP 状态码。
    """

    def __init__(self, message: str, *, category: str = "unknown", status_code: int = 500):
        super().__init__(message)
        self.category = category
        self.status_code = status_code


class LLMClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        timeout: float = 60.0,
    ):
        # 优先级：显式入参 > OPENAI_* > CHATDD_* > 兜底
        self.base_url = (
            base_url
            or os.getenv("OPENAI_BASE_URL")
            or os.getenv("CHATDD_LLM_BASE_URL")
            or ""
        )
        self.api_key = (
            api_key
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("CHATDD_LLM_API_KEY")
            or ""
        )
        self.model = (
            model
            or os.getenv("OPENAI_MODEL")
            or os.getenv("CHATDD_LLM_MODEL")
            or "gpt-4o-mini"
        )
        self.temperature = temperature
        self.timeout = timeout
        # 协议固定为 OpenAI 兼容协议
        self.protocol = "openai"
        # 注意：这里不立即建 client。空配置下建 client 会抛 SDK 自己的晦涩错误，
        # 统一改到 _lazy_client() / _lazy_aclient()，由 _check_config() 先给出人话提示。
        self._client = None   # 同步客户端（CLI / Streamlit 用）
        self._aclient = None  # 异步客户端（FastAPI async 端点用，O5）

    # -- 展示用（绝不回显完整 key）-----------------------------------------
    @property
    def masked_key(self) -> str:
        k = (self.api_key or "").strip()
        if len(k) <= 8:
            return "（未配置）"
        # 占位符没替换时不要显示成「半截看着像真 key」的假象，直接说明状态
        if any(ord(c) > 127 for c in k) or any(t in k for t in ("你的", "xxx", "XXX", "粘贴", "请填")):
            return "（占位符未替换）"
        return f"{k[:6]}...{k[-4:]}"

    @property
    def provider(self) -> str:
        """仅用于 UI 展示。不臆测供应商，不回显完整地址。"""
        u = self.base_url.lower()
        if not u:
            return "未配置"
        if "127.0.0.1" in u or "localhost" in u:
            return "本地推理服务"
        return "已配置端点"

    @property
    def effective_protocol(self) -> str:
        """当前实际会用的协议（v1 起恒为 openai 兼容）。"""
        return "openai"


    # -- 配置体检：提前拦住「占位符没改」这类低级错 -------------------------
    def _check_config(self) -> None:
        """
        启动前的配置体检。原则：宁可在这里失败，也不允许带着空配置去连任何地址。
        """
        if not self.base_url:
            raise LLMError(
                "未配置 LLM 端点（base_url 为空）。请由运行环境注入 CHATDD_LLM_BASE_URL，"
                "或构造 LLMClient 时显式传入 base_url。",
                category="config",
                status_code=400,
            )
        if any(ord(c) > 127 for c in self.base_url):
            raise LLMError(
                f"base_url 里含中文/全角字符（{self.base_url}），多为占位符未替换。"
                "请改为真实地址，形如 http://<host>:<port>/v1。",
                category="config",
                status_code=400,
            )
        if not self.base_url.startswith(("http://", "https://")):
            raise LLMError(
                f"base_url 必须以 http:// 或 https:// 开头，当前是：{self.base_url}",
                category="config",
                status_code=400,
            )
        if any(ord(c) > 127 for c in self.model):
            raise LLMError(
                f"模型名里含中文（{self.model}），请改为服务端真实模型名，如 gpt-4o-mini。",
                category="config",
                status_code=400,
            )

        k = (self.api_key or "").strip()
        if len(k) < 12 or any(t in k for t in ("你的", "xxx", "XXX", "请把", "粘贴")):
            raise LLMError(
                "LLM 凭证未配置（api_key 为空或为占位符）。请由运行环境注入 "
                "CHATDD_LLM_API_KEY，或构造 LLMClient 时显式传入 api_key。",
                category="config",
                status_code=400,
            )

    def _lazy_client(self) -> OpenAI:
        """按需构建 SDK client，构建前先做配置体检。"""
        self._check_config()
        if self._client is None:
            self._client = OpenAI(
                base_url=self.base_url, api_key=self.api_key, timeout=self.timeout
            )
        return self._client

    def _lazy_aclient(self) -> AsyncOpenAI:
        """按需构建异步 SDK client（O5），构建前同样先做配置体检。"""
        self._check_config()
        if self._aclient is None:
            self._aclient = AsyncOpenAI(
                base_url=self.base_url, api_key=self.api_key, timeout=self.timeout
            )
        return self._aclient

    # -- 连通性自检 ---------------------------------------------------------
    def ping(self) -> dict[str, Any]:
        """发一个极小的真实请求，验证 key / 网络 / 模型名三件事都对。"""
        self._check_config()

        t0 = time.time()
        cap = (
            {"max_completion_tokens": 32}
            if _is_reasoning_model(self.model)
            else {"max_tokens": 8}
        )
        try:
            resp = self._lazy_client().with_options(timeout=15.0).chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "ping"}],
                **cap,
            )
        except Exception as e:  # noqa: BLE001
            raise _humanize(e, self) from e
        return {
            "ok": True,
            "model": self.model,
            "provider": self.provider,
            "protocol": "openai",
            "latency_ms": int((time.time() - t0) * 1000),
            "reply": (resp.choices[0].message.content or "").strip()[:40],
        }

    # -- 结构化抽取（入口）---------------------------------------------------
    def extract(
        self,
        system_prompt: str,
        user_text: str,
        schema: Type[T],
        temperature: float | None = None,
    ) -> T:
        return self._extract_openai(system_prompt, user_text, schema, temperature)


    # -- 带退避重试的创建（O4）---------------------------------------------
    def _create_retry(self, messages, temperature=None, max_retries=3, **kwargs):
        """与 _create 相同，但对『限流 / 网关瞬时错误』做指数退避重试。

        仅重试 category 为 rate / gateway 的 LLMError（429、超时、连接失败）；
        配置类错误（401/404）与业务错误不重试，直接抛出，避免无意义轰炸。
        """
        delay = 0.5
        last_err: "LLMError | None" = None
        for attempt in range(max_retries):
            try:
                return self._create(messages=messages, temperature=temperature, **kwargs)
            except LLMError as e:
                last_err = e
                cat = getattr(e, "category", "unknown")
                if cat in ("rate", "gateway") and attempt < max_retries - 1:
                    logger.warning(
                        "LLM 瞬时错误（%s），%.1fs 后第 %d/%d 次重试",
                        cat, delay, attempt + 1, max_retries - 1,
                    )
                    time.sleep(delay)
                    delay = min(delay * 2, 8.0)
                    continue
                raise
        if last_err:
            raise last_err
        raise LLMError("重试逻辑异常：未捕获任何结果。", category="unknown", status_code=500)

    # -- 结构化抽取（OpenAI 兼容协议）---------------------------------------
    def _extract_openai(
        self,
        system_prompt: str,
        user_text: str,
        schema: Type[T],
        temperature: float | None = None,
    ) -> T:
        """
        让 LLM 把 user_text 按 schema 抽成结构化对象。
        优先 response_format=json_schema（structured outputs）；
        若网关不支持（返回 400/404/422），自动退化为 json_object 模式 + 手工解析。
        抽取调用走 _create_retry，对 429/超时自动退避重试（O4）；
        推理模型显式给定抽取 token 预算（N8）。
        """
        msgs = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ]
        json_schema = schema.model_json_schema()

        # N8：推理模型（o1/o3/o4/gpt-5*）默认不收 max_tokens，需显式给抽取预算，
        # 否则个别网关会按极小默认返回，导致 JSON 被截断。
        extract_kwargs: dict = {}
        if _is_reasoning_model(self.model):
            extract_kwargs["max_completion_tokens"] = 2048

        resp = None
        try:
            resp = self._create_retry(
                messages=msgs,
                temperature=temperature,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema.__name__,
                        "schema": json_schema,
                        "strict": False,  # 宽松些，兼容带默认值字段，降低 400 概率
                    },
                },
                **extract_kwargs,
            )
        except LLMError as e:
            # 只有「网关/模型不支持该参数」这类错误才降级；
            # 认证失败、限流、超时降级也没用，直接抛出去让上层看到真因。
            cause = e.__cause__
            status = getattr(cause, "status_code", None)
            if status not in (400, 404, 422):
                raise

        if resp is None:
            msgs2 = [
                {
                    "role": "system",
                    "content": system_prompt + "\n严格以 JSON 对象输出，不要任何额外文字。",
                },
                {"role": "user", "content": user_text},
            ]
            resp = self._create_retry(
                messages=msgs2,
                temperature=temperature,
                response_format={"type": "json_object"},
                **extract_kwargs,
            )

        return _parse_extract_response(resp, schema)

    # -- 结构化抽取（异步入口，O5）------------------------------------------
    async def aextract(
        self,
        system_prompt: str,
        user_text: str,
        schema: Type[T],
        temperature: float | None = None,
    ) -> T:
        """extract 的异步版本：供 FastAPI async 端点 await，不阻塞事件循环。

        与同步版共享同一套配置体检、json_schema→json_object 降级、
        指数退避重试（O4）与响应解析逻辑，行为完全一致。
        """
        return await self._aextract_openai(system_prompt, user_text, schema, temperature)

    # -- 带退避重试的异步创建（O4 + O5）------------------------------------
    async def _acreate_retry(self, messages, temperature=None, max_retries=3, **kwargs):
        """_create_retry 的异步版：重试等待用 asyncio.sleep，不阻塞事件循环。"""
        delay = 0.5
        last_err: "LLMError | None" = None
        for attempt in range(max_retries):
            try:
                return await self._acreate(messages=messages, temperature=temperature, **kwargs)
            except LLMError as e:
                last_err = e
                cat = getattr(e, "category", "unknown")
                if cat in ("rate", "gateway") and attempt < max_retries - 1:
                    logger.warning(
                        "LLM 瞬时错误（%s），%.1fs 后第 %d/%d 次重试",
                        cat, delay, attempt + 1, max_retries - 1,
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 8.0)
                    continue
                raise
        if last_err:
            raise last_err
        raise LLMError("重试逻辑异常：未捕获任何结果。", category="unknown", status_code=500)

    # -- 结构化抽取（异步，OpenAI 兼容协议）---------------------------------
    async def _aextract_openai(
        self,
        system_prompt: str,
        user_text: str,
        schema: Type[T],
        temperature: float | None = None,
    ) -> T:
        """_extract_openai 的异步版：降级/重试/token 预算逻辑与同步版一致。"""
        msgs = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ]
        json_schema = schema.model_json_schema()

        # N8：推理模型显式给定抽取 token 预算（与同步版一致）
        extract_kwargs: dict = {}
        if _is_reasoning_model(self.model):
            extract_kwargs["max_completion_tokens"] = 2048

        resp = None
        try:
            resp = await self._acreate_retry(
                messages=msgs,
                temperature=temperature,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema.__name__,
                        "schema": json_schema,
                        "strict": False,
                    },
                },
                **extract_kwargs,
            )
        except LLMError as e:
            # 只有「网关/模型不支持该参数」这类错误才降级（与同步版一致）
            cause = e.__cause__
            status = getattr(cause, "status_code", None)
            if status not in (400, 404, 422):
                raise

        if resp is None:
            msgs2 = [
                {
                    "role": "system",
                    "content": system_prompt + "\n严格以 JSON 对象输出，不要任何额外文字。",
                },
                {"role": "user", "content": user_text},
            ]
            resp = await self._acreate_retry(
                messages=msgs2,
                temperature=temperature,
                response_format={"type": "json_object"},
                **extract_kwargs,
            )

        return _parse_extract_response(resp, schema)

    # -- 通用文本补全 -------------------------------------------------------
    def complete(self, system_prompt: str, user_text: str, temperature: float = 0.3) -> str:
        resp = self._create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            temperature=temperature,
        )
        return resp.choices[0].message.content or ""

    # -- 内部：统一参数适配 -------------------------------------------------
    def _create(self, messages: list[dict], temperature: float | None = None, **kwargs):
        self._check_config()
        t = self.temperature if temperature is None else temperature
        if not _is_reasoning_model(self.model):
            kwargs["temperature"] = t
        try:
            return self._lazy_client().chat.completions.create(
                model=self.model, messages=messages, **kwargs
            )
        except Exception as e:  # noqa: BLE001
            raise _humanize(e, self) from e

    # -- 内部：统一参数适配（异步版，O5）-----------------------------------
    async def _acreate(self, messages: list[dict], temperature: float | None = None, **kwargs):
        """_create 的异步版：走 AsyncOpenAI，await 不阻塞事件循环。"""
        self._check_config()
        t = self.temperature if temperature is None else temperature
        if not _is_reasoning_model(self.model):
            kwargs["temperature"] = t
        try:
            return await self._lazy_aclient().chat.completions.create(
                model=self.model, messages=messages, **kwargs
            )
        except Exception as e:  # noqa: BLE001
            raise _humanize(e, self) from e


def _parse_extract_response(resp, schema: Type[T]) -> T:
    """从 chat.completion 响应解析出 schema 实例（同步/异步抽取共用）。

    剥围栏 → json.loads → pydantic 校验，失败均抛带人话说明的 LLMError。
    """
    content = (resp.choices[0].message.content or "").strip()
    if not content:
        raise LLMError(
            "模型返回了空内容。若是推理类模型，可改用 gpt-4o-mini 这类非推理模型做抽取。",
            category="gateway",
            status_code=502,
        )
    content = _strip_fence(content)
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise LLMError(f"模型返回的不是合法 JSON：{content[:200]}", category="gateway", status_code=502) from e
    try:
        return schema.model_validate(data)
    except ValidationError as e:
        raise LLMError(
            f"模型返回的字段不符合约定 schema：{e.errors()[:2]}",
            category="gateway",
            status_code=502,
        ) from e


def _humanize(e: Exception, cli: "LLMClient") -> LLMError:
    """把 SDK 异常翻译成能直接贴到 UI 上的一句话建议，并附分类（O3）。"""
    where = f"[provider={cli.provider} model={cli.model} key={cli.masked_key}]"

    if isinstance(e, UnicodeEncodeError):
        return LLMError(
            "配置里含中文/全角字符，导致请求地址无法编码。"
            "常见原因是 .env 的 OPENAI_BASE_URL / OPENAI_MODEL 还是占位符，请换成真实值。"
            f"{where}",
            category="config",
            status_code=400,
        )
    if isinstance(e, AuthenticationError):
        return LLMError(
            f"401 认证失败：API key 无效、写错或已过期。{where}",
            category="config",
            status_code=400,
        )
    if isinstance(e, RateLimitError):
        return LLMError(
            f"429 触发限流或余额不足，稍后重试或检查账户额度。{where}",
            category="rate",
            status_code=429,
        )
    if isinstance(e, APITimeoutError):
        return LLMError(
            f"请求超时：请求在规定时间内未响应，请检查 {cli.base_url} 是否可达。{where}",
            category="gateway",
            status_code=502,
        )
    if isinstance(e, APIConnectionError):
        return LLMError(
            f"网络连接失败：无法连接到 {cli.base_url}，请确认端点在当前网络内可达。{where}",
            category="gateway",
            status_code=502,
        )
    if isinstance(e, APIStatusError):
        code = getattr(e, "status_code", "?")
        if code == 404:
            return LLMError(
                f"404：模型名 '{cli.model}' 在该网关上不存在，或 base_url 少了 /v1。{where}",
                category="config",
                status_code=400,
            )
        return LLMError(
            f"{code} 接口报错：{str(e)[:200]} {where}",
            category="gateway",
            status_code=502,
        )
    return LLMError(
        f"调用失败：{type(e).__name__}: {str(e)[:200]} {where}",
        category="unknown",
        status_code=500,
    )
