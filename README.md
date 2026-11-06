# 招聘 Agent Tool（ChatDD 接入版）

> **🌐 在线体验**：https://kovoiiizzz-recruitment-agent-tool.ms.show （ModelScope 创空间，免费公网直连）
>
> 打开即用：左右两栏粘贴简历/JD → 点「开始匹配」→ 加权打分 + 硬门槛判定 + 差距分析。
> 演示案例与预期结果见仓库内 `DEMO_CASES.pdf` / `demo_cases.txt`（均为虚构人物）。

在 ChatDD 的 AgentOS（planning + reasoning + tool calling + skills）里新增一个
**招聘 agent**，提供两个核心能力：**简历解析与筛选** 与 **JD 解析与人才匹配**。

落地形态：一组可被 ChatDD 编排器注册调用的 **Tool**（也可进一步打包成
`Recruitment Skill`），后端是一个独立的 FastAPI 微服务。ChatDD 通过 OpenAI 兼容的
function-calling 协议来调用本服务。

---

## ⚡ Demo 快速入口

**可视化 Demo（推荐演示用）**：

```bash
pip install -r requirements.txt
streamlit run demo_streamlit.py
# 浏览器打开 http://localhost:8501，左右两栏粘贴简历/JD → 点「开始匹配」
```

**API 服务**：

```bash
uvicorn app:app --port 8100
# GET /tools 查看工具清单；POST /tools/screen 一站式：简历+JD → 匹配结果
```

**演示材料**：演示话术与对照案例见 `DEMO_GUIDE.md`；LLM 连通性自检见 `check_llm.py`。

---

## 1. 架构与数据流

```
用户对话（ChatDD 对话界面）
      │  “解析这份简历”“这个人匹配这个岗位吗”
      ▼
ChatDD 编排器（planner / reasoning）
      │  根据 tool 描述决定调用哪个工具
      ▼
本服务 FastAPI（/tools/parse_resume, /tools/parse_jd, /tools/match, /tools/screen）
      │  ├─ 解析类工具：调用内部 LLM 网关做结构化抽取（structured outputs）
      │  └─ 匹配工具：确定性加权打分（无需 LLM，可复现）
      ▼
结果 JSON 回注 ChatDD → LLM 生成给 HR / 候选人的自然语言答复
```

核心设计点：
- **抽取**（parse_resume / parse_jd）依赖 LLM，走内部 LLM 网关（`CHATDD_LLM_*` 环境变量）。
- **匹配**（match）是**确定性**算法，不依赖 LLM，保证可复现、可审计、可离线测试。
- 三个 tool 的 schema 见 `schemas.py` 的 `RECRUITMENT_TOOLS`，可直接注册进 ChatDD。

---

## 2. Tool 契约（注册到 ChatDD）

启动后访问 `GET /tools` 即可拿到完整 tool 清单。三个工具：

| Tool 名 | 作用 | 关键入参 |
|---|---|---|
| `parse_resume` | 简历 → 结构化候选人画像 | `resume_text` |
| `parse_jd` | JD → 结构化岗位需求 | `jd_text` |
| `match_candidate_to_jd` | 候选人 + JD → 匹配结果 | `candidate`, `jd`（上两步的 JSON） |

ChatDD 的 planner 在对话中自动选择调用；你也可以显式编排成一条招聘 Skill：
`parse_resume → parse_jd → match_candidate_to_jd → 生成推荐语`。

---

## 3. 本地运行与测试

```bash
cd recruitment-agent-tool
python -m venv .venv && .venv/Scripts/activate
pip install -r requirements.txt

# 离线测试（只验证打分层，不需 LLM）
python test_tools.py

# 启动服务
uvicorn app:app --host 0.0.0.0 --port 8000
```

健康检查与 tool 清单：
```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/tools
```

匹配端点（离线可用，不调 LLM）：
```bash
curl -X POST http://127.0.0.1:8000/tools/match \
  -H "Content-Type: application/json" \
  -d '{"candidate":{"name":"张三","skills":["LLM","机器学习","Python"],"highest_degree":"master","total_years_experience":3,"domains":["AI制药"]},
       "jd":{"job_title":"AI制药算法工程师","min_degree":"master","min_years_experience":2,"must_have_skills":["LLM","机器学习","Python"],"target_domains":["AI制药"]}}'
```
> 注意：在 Windows Git Bash 里直接 curl 含中文的 JSON 容易因引号转义失败，
> 建议用 Python（`requests`/`urllib`）或 Postman 发请求。

---

## 4. 接入 ChatDD 内部 LLM 网关

抽取工具依赖 LLM，通过环境变量指向 ChatDD 内部推理服务（OpenAI 兼容）：

```bash
export CHATDD_LLM_BASE_URL="https://<chatdd-internal-llm-gateway>/v1"
export CHATDD_LLM_API_KEY="<internal-key>"
export CHATDD_LLM_MODEL="<model-name>"
```

`llm.py` 默认优先使用 `response_format=json_schema`（structured outputs），
若网关仅支持 `json_object` 会自动降级。

---

## 5. 部署建议（给 ChatDD 平台侧）

- **容器化**：用官方 `python:3.12-slim` 基础镜像，`pip install -r requirements.txt`，
  启动命令 `uvicorn app:app --host 0.0.0.0 --port 8000`。
- **服务发现**：把本服务注册到 ChatDD 的内部服务网格，编排器通过 `/tools` 动态拉取 tool 清单。
- **鉴权**：建议在网关层给 `/tools/*` 加内部 token，防止未授权调用。
- **可观测**：`/health` 用于探针；建议补充调用日志与匹配结果审计（招聘场景涉及个人信息，需合规留痕）。
- **合规提醒**：简历/候选人数据属个人敏感信息，落库与日志需做脱敏，符合公司数据安全规范。

---

## 6. 目录结构

```
recruitment-agent-tool/
├── schemas.py        # 业务模型 + ChatDD Tool 契约（RECRUITMENT_TOOLS）
├── llm.py            # LLM 客户端封装（OpenAI 兼容，可指内部网关）
├── extractors.py     # 简历 / JD 结构化抽取（LLM）
├── matcher.py        # JD-候选人匹配打分（确定性加权）
├── app.py            # FastAPI 服务与端点
├── test_tools.py     # 离线测试（打分层 + 契约自检）
├── QUICKSTART.md     # 从 0 到 1 的实操步骤清单
├── requirements.txt
└── README.md
```

## 7. 可扩展方向

- **批量筛选**：`match` 接人才库列表 → 输出 ranking + shortlist。
- **语义匹配**：引入 embedding 计算 JD 与简历的语义相似度，补强「技能覆盖」维度。
- **面试安排 / 招聘 FAQ RAG**：按后续需求再加 `schedule_interview`、`recruit_qa` 两个 tool，复用同一注册机制。
- **多岗位权重模板**：把 `matcher.WEIGHTS` 与学历/经验门槛按岗位族做成配置，避免写死。
