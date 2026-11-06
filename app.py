"""
招聘 Agent Tool 服务（FastAPI）
================================
ChatDD 编排器通过 OpenAI 兼容的 function-calling 调我们：
  1) 启动后访问 GET /tools 拿到 RECRUITMENT_TOOLS（tool 清单）注册进 ChatDD；
  2) ChatDD 的 planner 在对话中决定调用 parse_resume / parse_jd / match_candidate_to_jd；
  3) 本服务暴露对应 HTTP 端点，接收 tool 参数、返回 JSON 结果，ChatDD 再回注给 LLM 生成答复。

端点（O5：全部 async def，LLM 走 AsyncOpenAI，不阻塞事件循环）：
  GET  /health                       健康检查
  GET  /tools                        返回 tool schema 清单（供 ChatDD 注册）
  POST /tools/parse_resume           简历 → CandidateProfile
  POST /tools/parse_jd               JD → JdRequirements
  POST /tools/match_candidate_to_jd 候选人 + JD → MatchResult（tool 名 match_candidate_to_jd）
  POST /tools/screen                 一键：简历 + JD → 解析 + 匹配（端到端，tool 名 screen）

安全（见 OPTIMIZATION_REVIEW O 系列）：
  - O1 访问日志 + PII 脱敏（绝不记明文简历/key，仅记长度与脱敏后的姓名）
  - O2 POST /tools/* 需 X-Tool-Token（由环境变量 RECRUITMENT_TOOL_TOKEN 设定；未设定时本地放开并告警）
  - O3 LLM 异常按语义映射 400/429/502，而非一律 500
  - O5 全部端点 async def + AsyncOpenAI：LLM I/O await，不占满线程池
  - O6 入参简历/ JD 文本做 20000 字符长度上限校验
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from schemas import (
    CandidateProfile,
    JdRequirements,
    MatchResult,
    RECRUITMENT_TOOLS,
)
from llm import LLMClient, LLMError
from extractors import aextract_candidate, aextract_jd  # O5：异步抽取入口
from matcher import score_match  # 纯 CPU 计算（亚毫秒级），async 端点内直接调用

# --------------------------------------------------------------------------- #
# O1：日志（绝不记明文简历/key；仅记路径、耗时、脱敏后的姓名、分数、结论）
# --------------------------------------------------------------------------- #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("recruitment_tool.app")

# 单条简历/JD 文本最大字符数（O6）
MAX_TEXT_LEN = 20_000

app = FastAPI(title="Recruitment Agent Tool", version="0.1.0")

# ChatDD 前端/编排器跨域调用本服务时需要
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_access(request: Request, call_next):
    """O1：访问日志（路径/状态码/耗时）。不读取/记录请求体明文。"""
    t0 = time.time()
    response = await call_next(request)
    latency_ms = int((time.time() - t0) * 1000)
    logger.info(
        "ACCESS %s %s -> %d (%dms)",
        request.method, request.url.path, response.status_code, latency_ms,
    )
    return response


def _mask_name(name: Optional[str]) -> str:
    """O1：姓名脱敏——保留前 2 字，超长用长度替代，避免日志泄露完整 PII。"""
    if not name:
        return "(未知)"
    if len(name) <= 2:
        return name
    return f"{name[:2]}…(共{len(name)}字)"


# --------------------------------------------------------------------------- #
# O2：POST /tools/* 鉴权（X-Tool-Token）
# --------------------------------------------------------------------------- #
def require_token(
    api_key: Optional[str] = Header(default=None, alias="X-Tool-Token"),
) -> None:
    expected = os.getenv("RECRUITMENT_TOOL_TOKEN")
    if not expected:
        # 本地开发未设 token 时放开，但明确告警（生产务必设置）。
        logger.warning(
            "O2：未设置 RECRUITMENT_TOOL_TOKEN，POST /tools/* 当前无鉴权，"
            "外发/生产环境请务必设置该环境变量。"
        )
        return
    if api_key != expected:
        raise HTTPException(status_code=401, detail="未授权：缺少或错误的 X-Tool-Token")


def _http_err(e: Exception) -> HTTPException:
    """O3：把异常映射为合适的 HTTP 状态码。"""
    if isinstance(e, LLMError):
        code = getattr(e, "status_code", 500) or 500
        return HTTPException(status_code=code, detail=str(e))
    return HTTPException(status_code=500, detail=f"内部错误: {e}")


# --------------------------- 请求体模型 ---------------------------
class ParseResumeReq(BaseModel):
    resume_text: str = Field(..., max_length=MAX_TEXT_LEN, description="简历全文或关键段落的纯文本。")
    candidate_name: Optional[str] = None


class ParseJdReq(BaseModel):
    jd_text: str = Field(..., max_length=MAX_TEXT_LEN, description="岗位描述全文或关键段落的纯文本。")


class MatchReq(BaseModel):
    candidate: CandidateProfile
    jd: JdRequirements


class ScreenReq(BaseModel):
    resume_text: str = Field(..., max_length=MAX_TEXT_LEN, description="简历全文或关键段落的纯文本。")
    jd_text: str = Field(..., max_length=MAX_TEXT_LEN, description="岗位描述全文或关键段落的纯文本。")
    candidate_name: Optional[str] = None


# 懒加载 LLM 客户端（首次调用时创建，方便无密钥时只测 matcher）
_llm: Optional[LLMClient] = None


def _get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm


# --------------------------- 端点 ---------------------------
@app.get("/health")
def health():
    return {"status": "ok", "tools": [t["function"]["name"] for t in RECRUITMENT_TOOLS]}


@app.get("/tools")
def list_tools():
    """ChatDD 编排器拉取 tool 清单的入口。"""
    return {"tools": RECRUITMENT_TOOLS}


@app.post("/tools/parse_resume", response_model=CandidateProfile, dependencies=[Depends(require_token)])
async def parse_resume(req: ParseResumeReq):
    logger.info("PARSE_RESUME size=%d name=%s", len(req.resume_text), _mask_name(req.candidate_name))
    try:
        return await aextract_candidate(req.resume_text, req.candidate_name, _get_llm())
    except LLMError as e:  # O3：配置/限流/网关错误透传语义码
        raise _http_err(e)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)


@app.post("/tools/parse_jd", response_model=JdRequirements, dependencies=[Depends(require_token)])
async def parse_jd(req: ParseJdReq):
    logger.info("PARSE_JD size=%d", len(req.jd_text))
    try:
        return await aextract_jd(req.jd_text, _get_llm())
    except LLMError as e:
        raise _http_err(e)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)


@app.post("/tools/match_candidate_to_jd", response_model=MatchResult, dependencies=[Depends(require_token)])
async def match_candidate_to_jd(req: MatchReq):
    try:
        result = score_match(req.candidate, req.jd)
    except LLMError as e:
        raise _http_err(e)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
    logger.info(
        "MATCH candidate=%s jd=%s score=%.1f rec=%s hard_breach=%s",
        _mask_name(result.candidate_name), result.job_title,
        result.total_score, result.recommendation, result.hard_breach,
    )
    return result


@app.post("/tools/match", response_model=MatchResult, deprecated=True, dependencies=[Depends(require_token)])
async def match_legacy(req: MatchReq):
    """向后兼容别名：旧调用方可能仍用 /tools/match。新接入请用 /tools/match_candidate_to_jd。"""
    return await match_candidate_to_jd(req)


@app.post("/tools/screen", response_model=MatchResult, dependencies=[Depends(require_token)])
async def screen(req: ScreenReq):
    """端到端：简历+JD 一次性出匹配结论（含自然语言解释 explanation），适合 ChatDD 里一句『这个人合适吗』。

    O5：两次 LLM 抽取并发执行（asyncio.gather），比串行快近一半。
    """
    logger.info("SCREEN size=%d/%d name=%s", len(req.resume_text), len(req.jd_text), _mask_name(req.candidate_name))
    try:
        llm = _get_llm()
        cand, jd = await asyncio.gather(
            aextract_candidate(req.resume_text, req.candidate_name, llm),
            aextract_jd(req.jd_text, llm),
        )
        result = score_match(cand, jd)
    except LLMError as e:
        raise _http_err(e)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
    logger.info(
        "MATCH candidate=%s jd=%s score=%.1f rec=%s hard_breach=%s",
        _mask_name(result.candidate_name), result.job_title,
        result.total_score, result.recommendation, result.hard_breach,
    )
    return result


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
