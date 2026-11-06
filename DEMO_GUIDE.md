# 简单版 Demo —— 你需要做什么

> 一句话：**代码我已经写完并跑通了，你要做的是「换成真实数据 → 配 LLM → 部署 → 准备演示话术」这 4 件事。**

---

## ✅ 已经做好、你不用管的

| 文件 | 作用 | 状态 |
|---|---|---|
| `demo_streamlit.py` | Streamlit 单页 Demo（粘贴 → 解析 → 打分 → 结论） | 已跑通，实测 86.4 分 |
| `simple_extract.py` | 无 LLM 时的规则抽取兜底 | 已跑通 |
| `matcher.py` | 加权打分 + 硬门槛 + 差距分析 | 已跑通（单测通过） |
| `schemas.py` | 数据结构 + Tool 契约 | 已跑通 |
| `extractors.py` / `llm.py` | LLM 结构化抽取（可选启用） | 已就绪，待配 key |
| `.venv` | 依赖已装好（含 streamlit 1.66） | 已就绪 |

**Demo 现在的样子**：左右两栏贴简历/JD → 点「开始匹配」→ 出总分、4 维得分条、技能命中/缺失、差距分析、结构化解析（可展开）。

---

## 你要做的 4 件事（总耗时约 1.5 小时）

### 第 1 步：跑起来看看（5 分钟）

```bash
cd recruitment-agent-tool
.venv\Scripts\activate
streamlit run demo_streamlit.py
```
浏览器自动打开 `http://localhost:8501`，点「🚀 开始匹配」。

**验收**：看到「匹配总分 86.4 / 100」+ 4 个维度进度条 + 技能命中/缺失。

---

### 第 2 步：换成你的真实数据（10 分钟）

两种改法，任选：

**改法 A（推荐，最适合演示）**：页面里直接粘贴
- 打开页面，把左边文本框内容全选删掉，粘贴一份**真实简历**（脱敏：姓名改「张三」、公司改「某公司」）
- 右边粘贴**真实的 AI 制药/算法岗 JD**
- 点开始匹配，看结果合不合理

**改法 B**：改默认值（这样一打开就是你的数据）
- 编辑 `demo_streamlit.py` 顶部的 `SAMPLE_RESUME` 和 `SAMPLE_JD`，换成你的内容

**验收**：换 3 份不同简历，分数有明显区分（强匹配 > 80、不达门槛 < 50），排序符合你的直觉。

> ⚠️ **脱敏很重要**：简历是个人敏感信息，演示用的材料千万别带真实姓名/手机号/身份证。

---

### 第 3 步（推荐）：配 LLM，让抽取变准（15 分钟）

规则模式能演示，但抽取质量一般（比如「3年经验」能抽到，复杂表述就漏）。配上 LLM 效果会明显好。

1. 找平台/算法同事要内部 LLM 网关的 `base_url` / `key` / `model`
2. 设环境变量（Windows PowerShell）：
   ```powershell
   $env:CHATDD_LLM_BASE_URL="https://<网关>/v1"
   $env:CHATDD_LLM_API_KEY="<key>"
   $env:CHATDD_LLM_MODEL="<model>"
   ```
3. 重新 `streamlit run`，侧边栏「启用 LLM 结构化抽取」开关会自动打开

**验收**：展开底部「查看结构化解析结果」，`skills`、`highest_degree`、`total_years_experience`、`domains` 字段明显更完整准确。

> 💡 没拿到 key 也不影响：开关关掉就走规则模式，Demo 照样能演示，只是抽得粗一点。

---

### 第 3.5 步：key 粘好后，怎么确认「真的通了」（4 关，5 分钟）

**第 0 关：确认贴进去的是整串 key，不是半截**
- 打开 `.env`，看 `OPENAI_API_KEY=` 后面：整串连续、无空格、无换行、无引号（不要写成 `"sk-..."`）
- 别把 key 贴进微信/聊天窗口/截图，只放 `.env` 里；`.env` 不要提交 git

**第 1 关：脚本自检（一条命令，最可靠）**
```bash
cd recruitment-agent-tool
.venv\Scripts\python.exe check_llm.py --quick
```
| 看到什么 | 说明 |
|---|---|
| `api_key : （未配置）` | 没读到 key，`.env` 不在本目录或变量名写错 |
| `api_key : （占位符未替换）` | `.env` 里还是示例文字，没换成真 key |
| `api_key : sk-ant...abcd` + `✅ 响应正常 320 ms` | **通了**，进第 2 关 |
| `❌ 401 鉴权失败` | key 不对/过期，或不属于这个网关（检查是否复制漏了字符） |
| `❌ 404 模型名不存在` | `OPENAI_MODEL` 换成站点「模型广场」里的真实模型名 |
| `❌ 请求超时 / 网络连接失败` | 线路问题：换 `https://code2.newcli.com/v1` 或 `https://dm-fox.rjj.cc/v1` |
| `protocol=anthropic` + 通过 | 正常：这类 Claude 中转站只认 Anthropic 原生协议，`LLM_PROTOCOL=auto` 会自动切，不用管 |

**第 2 关：跑端到端抽取（去掉 `--quick`）**
```bash
.venv\Scripts\python.exe check_llm.py
```
要看到 4 段全绿：简历抽取 → JD 抽取 → 加权匹配，且 `技能=7 个` 这类数字比规则模式更全。
最后一行会打印「🎉 全链路通过」。

**第 3 关：UI 里点一次**
```bash
.venv\Scripts\python.exe -m streamlit run demo_streamlit.py
```
- 侧边栏出现 🟢 + `model claude-sonnet-4-5 · key sk-ant...xxxx` → 配置读到了
- 点「🔌 测试 LLM 连通」→ 绿色「连通正常 · XXX ms」→ **这才是可以上台演示的状态**
- 再点「🚀 开始匹配」，展开底部「🔍 查看结构化解析结果」，`skills` / `domains` 字段粗细明显比规则模式丰富

**第 4 关：证明「真的在调模型」，而不是悄悄回退了规则模式（最硬的一招）**
1. 关掉侧边栏「启用 LLM 结构化抽取」，跑一次 → 记下总分和技能数（规则模式基线）
2. 打开开关，跑一次 → 分数/技能数应当不同
3. 想更彻底：故意把 key 最后一位改错 → 应看到 `401 鉴权失败` 红色提示（说明请求真发出去了）；改回来再跑

> 第 3 关里如果出现黄色警告「LLM 调用失败，已回退规则抽取：…」，说明 key/线路有问题但页面不会崩——这句就是现场演示的救生衣，但也意味着得回去看第 1 关。

---

### 第 4 步：部署成链接，发给别人看（30 分钟）

你之前把 RAG 部署到 ModelScope Studio 走过一遍，流程一样：

1. ModelScope 建一个 Studio（选 Streamlit 模板）
2. 上传这 6 个文件：`demo_streamlit.py`、`schemas.py`、`matcher.py`、`simple_extract.py`、`extractors.py`、`llm.py`
3. 配 `requirements.txt`（内容见下）
4. 启动命令填：`streamlit run demo_streamlit.py --server.port 7860`
5. 把 LLM 的 3 个环境变量配到 Studio 的「环境变量/Secrets」里

`requirements.txt` 用这个（Demo 专用，比原版多了 streamlit）：
```
fastapi>=0.110
uvicorn>=0.29
pydantic>=2.6
openai>=1.30
streamlit>=1.30
```

**验收**：拿到一个公网链接，手机打开也能看。

---

## 演示怎么讲（这才是 PM 的核心工作，花 30 分钟准备）

3 分钟演示脚本：

1. **痛点（30 秒）**：HR 一天看 200 份简历，靠人眼筛，漏人 + 慢 + 标准不统一。
2. **演示（90 秒）**：当场贴一份简历 + 一个 JD → 10 秒出结论：「86 分，强烈推荐，硬门槛全过，但缺抗体工程经验」。
3. **价值（30 秒）**：粗筛从「每份 3 分钟」降到「每份 10 秒」，且标准可复现、可审计。
4. **下一步（30 秒）**：接真实人才库做批量排序 → 接 ChatDD 编排器变成对话式 → 加面试安排。

**准备 3 组对照案例**（最能打动人）：
- 一份**明显匹配**的 → 高分
- 一份**差一点**的（缺一个硬门槛技能）→ 待定 + 明确说缺什么
- 一份**不匹配**的 → 低分 + 原因

---

## 可能的坑（提前知道）

| 现象 | 原因 | 解决 |
|---|---|---|
| 页面打开是空白/报错 | 端口被占用 | 换 `--server.port 8502` |
| 分数全都是 100 | JD 里没写 must_have/学历门槛，规则没抽到 | 配 LLM，或在 JD 里写清「必须技能：…」 |
| 分数都是 0 | 简历里技能词和 JD 对不上（同义词问题） | 在 `simple_extract.py` 的 `SKILL_LEXICON` 加词；或配 LLM |
| 配了 LLM 还是走规则 | 环境变量名写错 / 没重启 | 检查 `CHATDD_LLM_API_KEY`，重启 streamlit |
| 部署后打不开 | ModelScope 端口不对 | 启动命令必须带 `--server.port 7860` |

---

## 时间不够的话，最小路径

**第 1 步 + 第 2 步** 就能拿去汇报了（本地跑、贴真实数据）。
第 3、4 步等要给更多人看时再补。
