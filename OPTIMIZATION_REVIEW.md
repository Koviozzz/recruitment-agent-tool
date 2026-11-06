# recruitment-agent-tool 优化评审

> 评审对象：`D:\产品学习\WorkBuddy-Projects\2026-11-03-task-94\recruitment-agent-tool`
> 评审时间：2026-11-05
> 基线状态：`python test_tools.py` 通过（强匹配 96.5 分→强烈推荐；弱匹配 28.2 分→不推荐，硬门槛触发正常）
> 结论：核心逻辑可用，但**存在 3 个会阻断 ChatDD 接入 / 误导运行的真实问题**，以及若干上线前必补的合规与健壮性项。
> 🔧 进度：B1 / B2 / B3 已于 2026-11-05 修复（见各条「状态：✅ 已修复」标记）。  
> O 系列：O1/O2/O3/O4/O5/O6 已全部修复（O1-O4/O6 于 2026-11-05，O5 异步化于 2026-11-06）。  
> N 系列：N1/N3/N4/N5/N7/N8 已于 2026-11-05 修复；**N2（规则抽取偏差，仅文档提示）、N6（_http_post 技术债收敛）延后**（低优先级，不影响功能）。

---

## 一、🔴 必改（不修会阻断接入或造成"以为通了其实没通"）

### B1. Tool 函数名与 HTTP 端点对不上 —— ChatDD 派发会失败
- **现象**：`schemas.py` 里 `MATCH_TOOL.function.name = "match_candidate_to_jd"`，但实际路由是 `app.py` 的 `POST /tools/match`。
- **后果**：ChatDD 按 function name 注册后，调用 `match_candidate_to_jd` 找不到对应端点。
- **连带问题**：`app.py` 还有 `/tools/screen` 端点，但 `RECRUITMENT_TOOLS` 里**没注册它** → ChatDD 根本看不见这个"端到端一键匹配"工具。
- **修法（推荐）**：把端点改名为 `POST /tools/match_candidate_to_jd`，与 tool 名保持一致；同时在 `RECRUITMENT_TOOLS` 中补上 `screen` 的 tool 定义（或删掉该端点）。
- 涉及：`schemas.py`（新增 SCREEN_TOOL 并加入 RECRUITMENT_TOOLS）、`app.py`（路由改为 `/tools/match_candidate_to_jd`，保留 `/tools/match` 为 deprecated 别名）。
- **状态：✅ 已修复（2026-11-05）**：路由已与 tool 名对齐（`/tools/match_candidate_to_jd`），`screen` 已在 `RECRUITMENT_TOOLS` 注册；`test_tools.py` 断言已同步更新，`python test_tools.py` 通过。

### B2. `find_base_url.py` 会写出 `LLM_PROTOCOL=anthropic`，但 `llm.py` 从不读取
- **现象**：`find_base_url.py` 命中 Anthropic 网关后 `apply_env(... protocol="anthropic")`，在 `.env` 写入 `LLM_PROTOCOL=anthropic`；但 `llm.py` 的 `effective_protocol` 恒返回 `"openai"`，`_create` 永远只调 `chat.completions.create`（OpenAI 协议）。
- **后果**：用户按工具提示 `--apply` 后，以为"协议已自动切到 anthropic"，实际 `llm.py` 仍按 OpenAI 协议请求 → 必失败，且排查时极容易误判。
- **修法（推荐，符合 llm.py 现有约束）**：在 `find_base_url.py` 命中 Anthropic 时**不要写 `LLM_PROTOCOL`**，改为明确提示："当前 llm.py 仅支持 OpenAI 兼容协议，请走中转/OpenAI 兼容层，或改用支持原生 Anthropic 的版本"。若真要支持原生 Anthropic，则应在 `llm.py` 按 `LLM_PROTOCOL` 分派到 `/v1/messages`。
- 涉及：`find_base_url.py`（apply_env 不再写 LLM_PROTOCOL；Anthropic 分支改为打印警告不写 .env）、`llm.py`（新增防御性警告：残留非 openai 的 LLM_PROTOCOL 时提示被忽略）。
- **状态：✅ 已修复（2026-11-05）**：`apply_env` 已移除 `LLM_PROTOCOL` 写入（并会在 .env 残留时注释掉）；Anthropic 命中改打印明确警告、不写 .env；`llm.py` 在 `import` 时若检测到 `LLM_PROTOCOL=anthropic` 会发出 `UserWarning`。

### B3. JD 抽取契约与匹配逻辑不一致：`min_related_years` / `requirements` 被要求、却从未落地
- **现象**：`extractors.py` 的 `_JD_SYSTEM` 要求 LLM 抽 `min_related_years`（相关经验年限）和带 `weight/must_have` 的 `requirements` 列表；但 `schemas.py` 的 `JdRequirements` **没有 `min_related_years` 字段**，`requirements` 字段虽有定义但 `matcher.py` **完全没用它**。
- **后果**：① LLM 抽出的 `requirements` 权重被丢弃，白算；② 更有业务价值的"相关经验年限"门槛没生效，匹配只用"总年限" `min_years_experience`。
- **修法（三选一）**：
  1. 在 `JdRequirements` 增加 `min_related_years` 字段，并在 `matcher.py` 增加"相关经验"门槛/维度；
  2. 把 `requirements` 真正用于加权打分（取代写死的 `WEIGHTS`）；
  3. 最低限度：改 `_JD_SYSTEM` 提示词，不再要求抽 `requirements` 权重，避免模型做无用功。
- 涉及：`extractors.py`（_JD_SYSTEM）、`schemas.py`（JdRequirements）、`matcher.py`。
- **状态：✅ 已修复（2026-11-05，采用方案 3 + 删除死字段）**：选择方案 3 并进一步删除从未被消费的死字段——`_JD_SYSTEM` 现在只要求抽取 matcher 实际消费的 5 个字段（min_degree / min_years_experience / must_have_skills / preferred_skills / target_domains）；`schemas.py` 删除了 `RequirementItem` 类与 `JdRequirements.requirements` 字段；`_mock_llm.py` 同步移除了 `requirements` 键。契约现已自洽（抽取↔匹配对齐）。

---

## 二、🟠 重要（上线前必补，尤其招聘=敏感个人信息场景）

### O1. 缺日志 + 缺 PII 脱敏（合规硬要求）
- README 第 5 节自己写了"补充调用日志与匹配结果审计""简历属敏感信息需脱敏"，但 `app.py`/`llm.py` 目前**零 logging**。
- **建议**：加 `logging` 中间件，记录：入参摘要（截断 `resume_text`、绝不记明文 key）、出参分数、耗时、异常；匹配结果留痕（谁/何时/匹配了谁）。这是上线前置。
- **状态：✅ 已修复（2026-11-05）**：`app.py` 接入 `logging`——`log_access` 中间件记录 路径/状态码/耗时；各端点记录入参**长度**（绝不记明文简历）与脱敏后的姓名（`_mask_name` 仅留前 2 字）、出参 分数/结论/hard_breach；`llm.py` 增加 `recruitment_tool.llm` logger 供重试告警。所有日志均不含明文 key 或简历内容。

### O2. `/tools/*` 无鉴权 + CORS 全开
- `app.py` CORS `allow_origins=["*"]` 且无任何 token。README 说"网关层加 token"，但 Demo 外发（如 ModelScope）时风险高。
- **建议**：加一个 `Depends` 校验内部 token（从 header 或环境变量读）。
- **状态：✅ 已修复（2026-11-05）**：新增 `require_token` 依赖，从环境变量 `RECRUITMENT_TOOL_TOKEN` 读取预期 token，校验请求头 `X-Tool-Token`；仅作用于 `POST /tools/*`（GET `/health`、`/tools` 保持开放供 ChatDD 注册）。未设置该变量时本地放开但明确 `WARNING` 提示生产必须设置。已用 TestClient 验证：缺 token→401、带正确 token→通过。

### O3. 错误处理粒度太粗
- `app.py` 所有异常都 `→ 500`，把 `llm.py` 已经区分清楚的 `LLMError`（401/404/429/超时）也压成 500，ChatDD 无法区分"配置错了"还是"真挂了"。
- **建议**：捕获 `LLMError`，按语义返回 400（配置/鉴权错）/ 502（网关错）/ 429（限流）。
- **状态：✅ 已修复（2026-11-05）**：`llm.py` 的 `LLMError` 现携带 `category`（config/rate/gateway/unknown）与对应 `status_code`；`_humanize` 按异常类型分类（401/404→400、429→429、超时/连接/其他 5xx→502、其余→500）。`app.py` 新增 `_http_err` 将 `LLMError` 映射为对应 HTTP 状态码，ChatDD 可据此区分"配置错了"vs"网关挂了"。已用单测验证 400/429/502 映射正确。

### O4. 无重试 / 无熔断
- 单次 LLM 失败（429/超时是常态瞬时错误）直接 500，Demo 现场易翻车。
- **建议**：对 `extract` 加指数退避重试，仅对 429/超时重试。
- **状态：✅ 已修复（2026-11-05）**：`llm.py` 新增 `_create_retry`，对 `category` 为 `rate`（429）或 `gateway`（超时/连接失败）的 `LLMError` 做指数退避重试（0.5s→1.0s→上限 8s，默认 3 次尝试）；配置类错误（401/404）与业务错误不重试，直接抛出，避免无意义轰炸。仅作用于 `extract` 抽取路径。已用 mock 网关验证抽取主链路（json_schema / 降级 json_object / 推理模型）正常，重试能自愈瞬时错误。

### O5. 同步阻塞端点
- `app.py` 端点都是同步 `def`，LLM 是阻塞 I/O；高并发会占满线程池。
- **建议**：改 `async def` + openai 异步客户端 `await`。
- **状态：✅ 已修复（2026-11-06）**：`llm.py` 新增 `AsyncOpenAI` 懒加载客户端（`_lazy_aclient`）与全套异步方法（`aextract` / `_aextract_openai` / `_acreate` / `_acreate_retry`），与同步版共享同一套降级（json_schema→json_object）、退避重试（O4）、token 预算（N8）与响应解析（抽成共用 `_parse_extract_response`）；`extractors.py` 新增 `aextract_candidate` / `aextract_jd`；`app.py` 全部端点改 `async def`，`/tools/screen` 内部用 `asyncio.gather` 并发两次抽取（比串行快近一半），deprecated 别名 `/tools/match` 正确 `await` 转发。同步路径完整保留（CLI / Streamlit / find_base_url 不受影响）。已用 mock 网关验证：parse_resume / parse_jd / screen / 别名 全 200，配置错 → 400。顺带修复：`_check_config` 的配置类 LLMError 此前缺 category/status_code（默认落 500），现已统一为 config/400。

### O6. 输入无长度/格式校验
- `resume_text`/`jd_text` 可无限长，token 成本不可控，也易被注入超长文本。
- **建议**：加 `max_length` 校验（如 20k 字符）。
- **状态：✅ 已修复（2026-11-05）**：`app.py` 所有请求体的 `resume_text`/`jd_text` 加 `Field(max_length=20000)`，超出直接 422（已用 TestClient 验证超长→422、合法短文本通过校验）。

---

## 三、🟢 可选增强（打磨项）

| 编号 | 项 | 说明 | 状态 |
|---|---|---|---|
| N1 | 测试覆盖太薄 | `test_tools.py` 仅 2 场景，没覆盖：无 `min_years_experience` 的 JD、空 `target_domains`、分数边界(65/50 阈值)、教育 gap=2 等。建议补边界用例并接 pytest。 | ✅ 已修复：扩至 10 个用例（含上述边界），`test_tools.py` 同时兼容 `python test_tools.py` 与 `pytest`；`requirements.txt` 已加 `pytest`，`pytest>=8.0`。 |
| N2 | 规则抽取偏差 | `simple_extract_jd` 把 JD 文本出现的最高学历当"最低门槛"——"博士优先"会被误判为硬门槛；技能/领域靠小写 `in` 匹配，同义词(ML vs 机器学习)会漏。规则兜底可接受，但 Demo 话术要讲清局限。 | ⏸️ 延后：纯文档/话术层面提示，不影响功能；在 DEMO_GUIDE/README 注明规则兜底局限即可。 |
| N3 | 权重写死、无岗位模板 | `matcher.WEIGHTS` 与门槛写死在代码。README 第 7 节已列"多岗位权重模板"，建议做成可配置。 | ✅ 已修复：`score_match(candidate, jd, weights=None)` 支持按维度覆盖权重，默认行为不变；已加 `test_weights_override` 验证。 |
| N4 | 缺 `.gitignore` | `.env`(含 key)、`.venv`、`__pycache__` 未被忽略，极易误提交密钥（DEMO_GUIDE 自己也强调别提交 .env）。务必加。 | ✅ 已修复：新增 `.gitignore`，忽略 `.env`/`__pycache__`/`.venv` 等。 |
| N5 | `explain_match` 英雄无用武之地 | `matcher.explain_match` 已实现但无人调用，可在 `/tools/screen` 返回里附带一段推荐语，让 ChatDD 直接拿到自然语言结论。 | ✅ 已修复：`MatchResult` 新增 `explanation` 字段，`score_match` 用规则版 `explain_match` 离线填充；`/tools/screen` 与 `/tools/match_candidate_to_jd` 返回自带自然语言结论，ChatDD 直接可用。 |
| N6 | 技术债：`_http_post` 三客户端兼容 | `llm.py` 的 `_http_post`(httpx/httpx2/requests) 仅 `find_base_url.py` 的 Anthropic 探测用到，与 openai SDK 内建重复，建议收敛。 | ⏸️ 延后：仅 `find_base_url.py` 探测分支使用，无功能影响；可并入 O5 异步化时一并收敛，降低改动风险。 |
| N7 | 总分与结论可能"割裂" | 硬门槛触发时 `total_score` 仍可能很高（如缺 1 个硬技能但其余满分→~89 分却显示"待定"），展示层建议加说明或封顶显示，避免误导。 | ✅ 已修复：`MatchResult` 新增 `hard_breach: bool`；规则版 `explanation` 在「高分但触硬门槛」时显式提示"总分虽达 X，但因触发硬门槛结论被限制为 Y"，前端可据此展示警示。 |
| N8 | 推理模型 extract 未设 token 预算 | `ping()` 已处理 `max_completion_tokens`，但 `extract` 路径未显式设置，个别推理模型可能默认过小。 | ✅ 已修复：`_extract_openai` 对推理模型（`_is_reasoning_model`）显式加 `max_completion_tokens=2048`；已用 mock 网关（`gpt-5-mini`）验证抽取正常。 |

---

## 四、建议行动顺序（PM 视角）

1. **先修 B1 + B3**（半天）：这是接入 ChatDD 的硬卡点，名实一致 + 契约自洽，否则 Demo 链路上 ChatDD 调不通或抽了白抽。— ✅ 已完成（2026-11-05）
2. **再修 B2**（1 小时）：消除"以为协议切了其实没切"的坑，避免现场 debug 浪费时间。— ✅ 已完成（2026-11-05）
3. **补 O1 + O2 + O4 + O3 + O6**（1 天）：合规日志 + 鉴权 + 重试 + 错误粒度 + 输入校验，这是"能拿出手汇报/外发"的底线。— ✅ 已全部完成（2026-11-05）
4. **打磨 N1/N3/N4/N5/N7/N8**（半天）：测试补边界、权重可配置、加 `.gitignore` 防泄密、解释字段落地、推理模型预算。— ✅ 已全部完成（2026-11-05）
5. **延后项**：N2（规则抽取偏差文档提示）、N6（_http_post 技术债收敛），视节奏再排，均不影响当前功能与 Demo。（O5 已于 2026-11-06 完成异步化）

---

## 附：已确认正常的部分（不用动）
- 打分层 `matcher.py` 加权与硬门槛逻辑自洽，离线测试通过。
- `llm.py` 配置体检（`_check_config`）、`masked_key` 打码、错误人话化（`_humanize`）做得细致，是亮点。
- 规则兜底 `simple_extract.py` + Streamlit 自动降级设计合理，保证断网也能演示。
- `find_base_url.py` 探测逻辑（判读规则、key 格式猜测）很完整，只是 Anthropic 分支与 llm.py 脱节（见 B2）。
