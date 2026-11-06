---
title: 招聘 Agent Tool — 简历/JD 智能匹配演示
sdk: streamlit
app_file: app.py
emoji: 🔍
colorFrom: blue
colorTo: green
---

# 招聘 Agent Tool · Demo

粘贴简历与岗位 JD，一键得到加权匹配打分（技能 35% / 学历 20% / 经验 20% / 领域 25%）、
硬门槛判定与差距分析。规则抽取离线可用；注入 LLM 环境变量后自动升级为
LLM 结构化抽取。

- 演示案例：左侧文本框默认内容，或参考 `demo_cases.txt`
- 技术栈：Streamlit + Pydantic + OpenAI 兼容接口（可选）
- 完整文档：https://github.com/Koviozzz/recruitment-agent-tool

> 演示数据均为虚构人物，不含任何真实个人信息。
