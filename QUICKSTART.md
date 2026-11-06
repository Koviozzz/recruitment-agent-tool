# QUICKSTART —— 你（子佳）具体要怎么做这个 Tool

> 目标：在 ChatDD 平台上跑通「招聘 agent」，能做 **简历解析与筛选** + **JD 解析与人才匹配**。
> 下面按 5 个阶段给动作、验收标准、卡住怎么办。按顺序做，每阶段过了再进下一阶段。

---

## 阶段 0：先搞清一个前置问题（半天内搞定，别跳过）

ChatDD 是水木分子的**闭源产品**，tool 怎么接进去，**取决于平台侧有没有开放注册入口**。
你需要先向平台/后端负责人确认一句话：

> 「ChatDD 的 agent 编排器现在支持注册外部 tool 吗？是走 OpenAI 兼容的 function-calling，
> 还是有内部的 tool 注册中心 / plugin 协议 / OpenBioMed Skill 打包规范？」

结果只有三种，对应三条路：

| 平台回答 | 你的路径 | 我们要改什么 |
|---|---|---|
| **A. 支持 OpenAI 兼容 function-calling** | 最理想，直接用本服务 | 基本不用改，按阶段 4-A 注册 |
| **B. 有内部 tool 注册中心 / Skill SDK** | 适配他们的注册格式 | 把 `schemas.py` 的 schema 转成他们的格式，包一层 adapter |
| **C. 目前不开放外部 tool** | 先做**独立 Demo 版**给老板看效果 | 用本服务 + 一个简易前端/Coze 跑演示，推动平台排期 |

> ⚠️ 这步不做，后面可能白干。先拿到「走哪条路」的确认。

---

## 阶段 1：本地跑通核心逻辑（1 天，不需要 LLM）

**做什么**
```bash
cd recruitment-agent-tool
python -m venv .venv && .venv/Scripts/activate
pip install -r requirements.txt
python test_tools.py          # 离线测试打分层
uvicorn app:app --port 8000   # 起服务
```
然后访问 `http://127.0.0.1:8000/docs`（FastAPI 自带 Swagger），在页面上直接点
`/tools/match` → Try it out，用示例 body 试一次。

**验收标准**
- `python test_tools.py` 输出 `✅ 离线测试通过`
- 强匹配候选人得分 > 90 且结论「强烈推荐」
- 不达门槛候选人触发硬门槛，结论「待定/不推荐」，gaps 里列出缺失技能

**卡住怎么办**
- 装依赖慢/失败 → 换国内镜像 `pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`
- 端口占用 → 换 `--port 8123`
- 中文 JSON 用 curl 报错 → 用 `/docs` 页面或 Postman，不要用 Git Bash curl

---

## 阶段 2：接内部 LLM 网关，让「解析」真正工作（1~2 天）

阶段 1 只验证了「匹配」（不需要 LLM）。「简历/JD 解析」必须接 LLM。

**做什么**
1. 找平台/算法同事要：内部 LLM 网关的 `base_url`、`api_key`、可用 `model` 名。
2. 设环境变量（Windows PowerShell 用 `$env:XXX="..."`）：
   ```bash
   export CHATDD_LLM_BASE_URL="https://<内部网关>/v1"
   export CHATDD_LLM_API_KEY="<key>"
   export CHATDD_LLM_MODEL="<model>"
   ```
3. 在 `/docs` 里测 `/tools/parse_resume`，粘一段真实简历文本，看是否返回结构化 JSON。

**验收标准**
- `parse_resume` 能把一段真实简历抽成 `skills / highest_degree / total_years_experience / domains` 等字段，字段基本正确
- `parse_jd` 能把真实 JD 抽出 `must_have_skills / min_degree / min_years_experience`
- 抽取结果丢给 `/tools/match`，能得到合理的分数与结论

**卡住怎么办**
- 网关不支持 `json_schema` → `llm.py` 已自动降级到 `json_object`，无需改代码
- 字段抽取不准 → 改 `extractors.py` 里的 `_RESUME_SYSTEM` / `_JD_SYSTEM` 提示词，加上你们真实的技能词表
- 报 401/403 → 确认 key 和 base_url 是否配对，是否需要额外 header

---

## 阶段 3：打包成可部署服务（1 天）

**做什么**
1. 写 `Dockerfile`（示例见下），构建镜像。
2. 本地 `docker run` 起来，确认 `/health`、`/tools` 正常。

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
```

**验收标准**
- 容器内 `curl /health` 返回 `{"status":"ok",...}`
- 容器内 `curl /tools` 返回三个 tool 的 schema

---

## 阶段 4：注册到 ChatDD（按阶段 0 的结论走）

### 4-A：平台支持 OpenAI 兼容 function-calling（最理想）
1. 让平台侧把 `GET /tools` 返回的 `tools` 数组登记为可用 tool 集（或把 URL 配给他们）。
2. 在 ChatDD 后台新建一个 **招聘 agent**，系统提示词可参考：
   ```
   你是水木分子的招聘助手。用户给出简历或 JD 时：
   1) 先用 parse_resume / parse_jd 做结构化；
   2) 用户问「合不合适/排序」时调 match_candidate_to_jd；
   3) 用中文给出结论、亮点、风险与下一步建议，不编造简历里没有的信息。
   ```
3. 在 ChatDD 对话里实测：「这是我收到的简历……帮我看看匹配 AI 制药算法岗吗」

### 4-B：平台有内部注册中心 / Skill SDK
- 把 `schemas.py` 的三个 schema 按他们格式改写成注册描述（多数也是 JSON Schema，改动很小）。
- 若要打包成 OpenBioMed Skill：把「parse → match」编排成固定工作流，作为 Skill 的一个能力暴露。

### 4-C：平台不开放外部 tool
- 用本服务 + 简单前端（或 Coze/Dify）做一个**独立招聘助手 Demo**，录屏给业务方看效果，
- 用 Demo 的数据（准确率、耗时、HR 节省时间）去推动平台排期接入。

**验收标准**
- 在 ChatDD（或 Demo）里，从「贴简历 + 贴 JD」到「输出匹配结论」全链路跑通，无需人工改 JSON

---

## 阶段 5：验收、合规与上线

**做什么**
1. **效果验收**：准备 10 份真实简历 + 1 个真实 JD，人工打分 vs 系统打分对比，看排序是否合理（这是给老板讲效果的关键材料）。
2. **合规**：简历是敏感个人信息 → 日志脱敏、不落明文、访问加内部 token。
3. **可观测**：加调用日志 + 匹配结果留痕（谁、什么时候、匹配了谁，便于审计）。
4. **上线**：注册到内部服务网格，配探针 `/health`。

**验收标准**
- 10 份样本里，系统排序与人工排序的 Top-3 重合度 ≥ 70%（可作为初版效果指标）
- 无明文简历落盘；有调用日志

---

## 一页速查：你要动的文件

| 想改什么 | 改哪个文件 |
|---|---|
| 抽取的字段 / 提示词 | `extractors.py`（`_RESUME_SYSTEM` / `_JD_SYSTEM`） |
| 匹配权重 / 门槛规则 | `matcher.py`（`WEIGHTS` 和各 `_xxx_dimension`） |
| 数据结构 / tool 契约 | `schemas.py`（`CandidateProfile` / `JdRequirements` / `RECRUITMENT_TOOLS`） |
| 新增端点 | `app.py` |
| LLM 网关地址 | 环境变量 `CHATDD_LLM_*`（或 `llm.py` 默认值） |

---

## 最小可行路径（时间紧就只做这条）

`阶段 0 确认路径 → 阶段 1 本地跑通 → 阶段 2 接 LLM → 阶段 4-A 注册`
这四步做完就能在 ChatDD 里演示「贴简历+贴JD→出匹配结论」，够拿去汇报。
阶段 3（容器）和阶段 5（合规/效果验收）等要真正上线再补。
