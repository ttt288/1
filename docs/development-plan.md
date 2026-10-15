# 自适应个性化消费决策 Agent 开发执行计划

## 0. 当前真实进度

当前项目已经不是纯 MVP 草图，而是一个可运行的决策 Agent 工作台。已完成的核心能力：

- 决策型 RAG：推荐前后会检索商品库、领域规则和知识文档，推荐依据可在页面中看到。
- 短期记忆：一次会话里的需求、候选商品、已识别答案和用户回答会进入本次排序。
- 长期记忆：只保存用户确认过的稳定表达偏好，不再自动保存预算这类容易随场景变化的信息。
- 多 Agent 工作流：领域路由、商品召回、差异分析、动态提问、画像、风险、排序、RAG、表达适配、记忆提议都有独立节点和 Trace。
- Tool Calling：商品搜索、问题生成、风险评分、排序、知识检索、记忆更新、京东榜单采集都通过工具化服务完成。
- Structured Output：后端核心输入输出使用 Pydantic Schema 校验。
- Guardrails：保健品和宠物食品风险由规则服务前置处理，LLM 不参与安全判断和排序裁决。
- Human-in-the-loop：记忆写入需要用户点“记住 / 本次有效 / 不记住”。
- Observability：页面展示执行节点、耗时、token、缓存和 fallback。
- Evaluation：提供固定回归用例、holdout 用例和当前推荐对比，用于验证推荐准确率、硬约束、动态提问、质量评分和成本效率。

当前完成度按项目能力约 70%，按面试演示可讲述度约 75%。主要短板不是 Agent 架构，而是真实商品知识库还需要继续补齐：京东公开猫粮总榜已经能采集，但它只能证明“外部热度”，不能证明每个商品适合幼猫、美毛或肠胃敏感，也不能替代配方核验。

2026-09-09 更新后，项目补强了四个面试容易追问的位置：

- Evaluation 增加 regression 与 holdout 两组用例。当前共有 13 条评测，其中 4 条 holdout；评测结果会区分推荐命中、安全拒推、Top1 硬约束满足率、动态问题效率、人工质量评分和 Latency / Token Cost。
- Agent Capability Benchmark 增加 HITL 记忆门控、工具调用覆盖率、workflow 节点数、handoff 链路和 fallback 恢复率。HITL 不只展示按钮，而是验证系统不会自动写入预算、风险敏感度等容易随场景变化的信息。
- Workflow 增加同一会话状态修订能力。用户中途修改预算、目标或前提时，系统会记录 StateUpdateAgent 事件，并重新构建画像、风险和排序。
- Reliability 增加真实测试覆盖：LLM 返回坏结果时规则兜底、服务重启后从本地 session 恢复、同一会话二次修改条件、硬排除不适合商品。
- Guardrails 区分软风险和硬排除。年龄阶段不匹配、保健品高风险上下文等必须排除或显著降级；如果所有候选都不合适，系统保留低分结果并带明确风险说明，而不是伪装成安全推荐。
- 宠物食品新增过敏硬约束。系统可从“鱼肉过敏 / 怀疑鱼类过敏 / 低敏”等表达中抽取过敏状态和需避开的成分，命中鱼肉、鱼油、三文鱼等成分时直接进入风险 block。

当前评测结果示例：

```text
total_cases = 13
holdout_cases = 4
agent_top1_accuracy_on_recommendable_cases = 1.00
agent_top3_hit_rate_on_recommendable_cases = 1.00
constraint_satisfaction = 1.00
safety_deferral_accuracy = 1.00
false_deferral_rate = 0.00
adaptive_avg_questions = 4.08
fixed_avg_questions = 6.31
question_reduction_rate = 0.35
agent_quality = relevance 4.45 / personalization 4.30 / explanation 4.15
avg_token_total = 120
token_reduction_estimate = 0.90
tool_call_coverage_rate = 1.00
hitl_memory_gate_rate = 1.00
avg_workflow_nodes = 10.00
```

解释这些数字时要注意：Top-1/Top-3 只统计“应该推荐”的样本；儿童、服药、过敏不确定等高风险保健品样本的正确行为是安全拒推。硬约束满足率统计主推荐 Top1 是否满足年龄阶段、预算、必须功能和排除项；Latency / Token Cost 是离线规则路径和 LLM-heavy 方案的架构估算，不等同于真实线上压测。当前评估集仍是小样本，适合证明评估方法和回归稳定性，不应包装成真实线上用户满意率。

项目边界建议保持为：

```text
决策 Agent 框架
  + 猫粮垂直样例
  + 保健品安全样例
  + 京东公开总榜作为外部热度信号
```

不要继续扩成“全品类实时价格平台”。那会变成数据工程和电商爬虫项目，风险和工作量都不适合个人求职项目。

## 0.1 当前对比和固定评估的作用

页面底部的“当前对比与固定评估”不是给购物用户看的功能，而是给开发调试和面试展示的验证能力。

当前推荐对比：

- 对比本次 Agent Top1、热门基线、低参考单价基线。
- 用来说明系统不是只按热度或只按便宜推荐。
- 低参考单价基线使用 `unit_price`，猫粮里表示参考元/kg，不是页面总价。

固定评估：

- 使用固定测试用例，不随当前页面输入变化。
- 用来防止后续改代码时把场景识别、风险边界、动态问题数量、Ground Truth 命中和约束检查改坏。
- 其中 regression 用例验证核心能力是否回归；holdout 用例用于避免只对训练过的表达过拟合。
- 当前质量评分是人工标注样例集的离线评分，适合面试展示方法论；后续如果扩展到真实用户反馈，可以替换为线上打分。

## 0.2 京东榜单和当前推荐的边界

京东公开“猫粮总榜”目前只作为外部市场热度参考：

- 它不是幼猫榜、成猫榜、美毛榜或肠胃敏感榜。
- 页面展示的“最近检查”和“内容变化”分别表示系统检查时间和榜单结构化内容变化时间。
- 如果榜单内容没变，内容变化时间不会更新，这是正常的。
- 榜单未提供可稳定采集的价格时，系统不伪造实时价格。
- 只有 SKU 和规格精确匹配到本地商品库时，榜单信号才会进入推荐候选。

因此，当用户问“幼猫高品质”时，系统应该优先使用幼猫阶段、配方、风险和本地已核验字段排序；京东总榜只能在旁边说明市场热度，不能直接决定 Top1。

## 1. 当前阶段目标

接下来先做一个可运行的 MVP，但这个 MVP 必须是纵向闭环，不是只做页面或只做 Prompt。

第一阶段目标：

> 用户输入消费需求后，系统能判断领域、召回候选商品、生成动态问题、接收回答、完成个性化排序、输出推荐结果，并展示用户画像、记忆和 Agent Trace。

第一版不做登录。系统固定使用：

```text
demo_user_id = "demo_user"
```

这样可以把时间集中在个性化推荐、Agent Workflow、记忆、风险控制和评估上。

## 2. 开发原则

### 2.1 先打通主链路

不要一开始做完整后台、复杂登录、真实电商 API 或漂亮但空的页面。

优先打通这条链路：

```text
自然语言需求
  → 领域判断
  → 商品候选召回
  → 商品差异分析
  → 动态问题生成
  → 用户回答
  → 用户画像构建
  → 风险分析
  → 个性化排序
  → 推荐解释
  → 记忆更新
  → Trace 展示
```

### 2.2 不写死单一领域

第一版支持宠物食品和保健品，但系统不能写死成猫粮或保健品推荐。

所有领域差异放到：

```text
Domain Config
Product Schema
Risk Rules
Ranking Weights
Question Dimensions
Knowledge Documents
```

未来加护肤品、咖啡豆、耳机，只需要增加配置和数据。

### 2.3 推荐逻辑不能完全交给大模型

大模型负责：

- 意图理解。
- 特征解释。
- 动态问题生成。
- 个性化表达。
- 复杂约束总结。

规则和代码负责：

- 单价计算。
- 风险标签匹配。
- 排序分数。
- Schema 校验。
- 缓存。
- Trace 记录。

这样面试时可以清楚说明：系统不是让 LLM 凭感觉推荐，而是把 LLM 放在可控工作流里。

## 3. 第一阶段功能拆分

### 3.1 Backend Skeleton

先建立后端基础结构：

```text
backend/
  app/
    main.py
    api/
    agents/
    schemas/
    services/
    data_providers/
    memory/
    tracing/
    guardrails/
    evaluation/
  tests/
```

第一版 API：

```text
POST /api/decision/start
POST /api/decision/answer
GET  /api/memory
GET  /api/traces/{session_id}
GET  /api/products
POST /api/evaluation/run
```

### 3.2 Data Foundation

先准备本地数据，不依赖爬虫。

```text
data/
  products/
    pet_food.json
    supplements.json
  domains/
    pet_food.json
    supplements.json
  knowledge/
    pet_food_rules.md
    supplement_rules.md
    ingredient_glossary.md
  evaluation/
    eval_cases.json
```

每个商品必须有：

```text
id
domain
name
brand
price
spec
unit_price
popularity_score
features
strengths
weaknesses
risk_tags
target_users
avoid_for
source_type
last_updated
```

商品数据第一版目标：

- 宠物食品 30-50 个。
- 保健品 30-50 个。
- 每个领域至少覆盖 5 种明显差异类型。

### 3.3 Schema First

先写 Pydantic Schema，再写 Agent。

第一批 Schema：

```text
DomainRoute
ProductOffer
ProductFeatureMatrix
Question
QuestionPlan
UserAnswer
UserProfile
ScenarioProfile
RiskResult
RankingResult
RecommendationResult
MemoryItem
MemoryUpdateProposal
TraceEvent
```

这样后续不会反复改接口。

### 3.4 Core Services

先写纯代码服务，确保不用 LLM 也能跑基础流程：

```text
ProductSearchService
FeatureMatrixService
QuestionPlanningService
RiskRuleService
RankingService
MemoryService
TraceService
```

这样后面接 Agent 时，每个 Agent 都是在调用稳定服务。

### 3.5 Agent Workflow

第一版 LangGraph 节点：

```text
domain_router
product_discovery
feature_extraction
question_generation
user_modeling
risk_analysis
personalized_ranking
response_adaptation
memory_update
```

第一版条件路由：

```text
领域不明确 → 问澄清问题
用户信息不足 → 生成动态问题
用户回答完成 → 进入风险分析
高风险 → 输出安全提示
可推荐 → 个性化排序
需要写记忆 → 触发确认
```

## 4. 前端第一版

第一版做一个工作台页面，不做营销式首页。

页面布局：

```text
左侧：对话输入和动态问题
中间：推荐结果和商品对比表
右侧：用户画像、长期记忆、Trace
```

核心展示：

- 推荐 Top 3。
- 商品对比表。
- 每个商品的价格、长处、缺点、风险、适合原因。
- 当前用户画像。
- 系统准备写入的记忆。
- Agent 执行 Trace。

推荐表格字段：

```text
排名
产品
价格
单价
适合原因
长处
缺点
风险提示
置信度
```

## 5. MVP 验收流程

### 5.1 宠物食品 Demo

输入：

```text
我想买猫粮，不知道哪个适合我。
```

系统应该：

1. 判断领域为宠物食品。
2. 召回猫粮候选商品。
3. 分析候选商品差异。
4. 生成 5-6 个问题。
5. 根据回答完成画像。
6. 输出推荐 Top 3。
7. 展示换粮、软便、年龄阶段等风险提示。
8. 提出可写入记忆。
9. 展示 Trace。

### 5.2 保健品 Demo

输入：

```text
我想买益生菌，不知道怎么选。
```

系统应该：

1. 判断领域为保健品。
2. 召回益生菌相关候选商品。
3. 根据剂量、成分、适用人群、风险条件生成问题。
4. 如果用户提到服药、孕期、儿童或慢病，触发 Guardrail。
5. 输出消费建议，不做医疗诊断。
6. 展示推荐依据和风险边界。
7. 展示 Trace。

## 6. 技术能力落地清单

### 6.1 RAG

落地方式：

- 商品库检索：先由 ProductDiscoveryAgent 召回候选商品，商品字段本身是第一层结构化知识。
- 规则知识检索：使用 `decision_chunks.json` 保存结构化 chunk，每个 chunk 带 `chunk_id`、`chunk_type`、`decision_dimensions`、`keywords` 和正文。
- 文档知识检索：继续兼容 `pet_food_rules.md`、`supplement_rules.md`、`ingredient_glossary.md`，作为 legacy markdown chunk。
- Hybrid Retrieval：粗召回同时看用户 query、已回答画像、候选商品特征和 chunk metadata，不只做单行关键词匹配。
- Rerank：召回后按风险规则、领域规则、价格规则、当前决策维度重排，并做轻量多样性控制，避免返回的依据全是同一种泛泛规则。
- Token 控制：最终只返回 Top-K 证据，且 LLM 只拿 Top3 商品摘要和必要证据，不把完整商品库塞进 prompt。

验收：

- 推荐依据能展示 chunk 来源、命中词、召回阶段、rerank 分数和 rerank 原因。
- 动态问题能说明来自哪些商品差异，推荐解释能关联到知识 chunk 或商品字段。
- 信息不足、RAG 为空或外部数据失败时，系统能降级为商品字段 + 规则摘要，不强行编造依据。

面试解释：

- 为什么这样切 chunk：本项目是决策型 RAG，chunk 按“决策规则单元”切，例如幼猫阶段、肠胃敏感、过敏、预算单价、保健品禁忌。这样每条依据都能直接服务一个问题或一个排序因子。
- 为什么先不用重型向量库：当前知识库规模小，本地 hybrid 检索更可控、成本低、方便展示。后续数据扩大时可以把 first-stage retrieval 换成 BM25 + Embedding 双路召回，Rerank 层和证据 schema 不需要重写。
- 为什么需要 Rerank：第一阶段召回只回答“相关不相关”，Rerank 才结合当前用户画像、硬约束、风险优先级和证据多样性，决定哪些证据进入最终解释。

### 6.2 长短记忆

落地方式：

- 短期记忆保存当前 session 状态。
- 长期记忆保存稳定偏好。
- 记忆包含 source、confidence、evidence、last_updated。

验收：

- 第二次推荐能读取上一次确认过的偏好。
- 未确认的敏感信息不会直接写入长期记忆。

### 6.3 多 Agent

落地方式：

- 每个 Agent 有独立职责。
- 每个 Agent 有明确输入输出 Schema。
- 每个 Agent 的执行都进入 Trace。
- 当前实现是显式编排的多 Agent Workflow，不是模型自主创建 subagent。
- 这样设计是因为消费推荐里的价格、风险和硬约束必须可控，不能交给模型自由规划。
- 如果后续投更 Agent 平台方向，可以增加 Planner Agent，让模型在受限工具集合里选择下一步；但风险判断、硬约束和记忆写入仍由代码 guardrail 控制。

验收：

- 前端能展示完整 Agent 路径。
- 单个 Agent 失败时有 fallback。
- Evaluation 能展示工具覆盖率、平均 workflow 节点数、HITL 记忆门控和 fallback 恢复率。

### 6.4 Tool Calling

落地方式：

- 商品搜索工具。
- 知识检索工具。
- 风险评估工具。
- 排序工具。
- 记忆写入工具。

验收：

- 工具参数有校验。
- 工具失败会记录并回退。

### 6.5 Structured Output

落地方式：

- Pydantic 校验 Agent 输出。
- 校验失败自动重试或 fallback。

验收：

- Evaluation 中统计 Schema Pass Rate。

### 6.6 Workflow / Handoff

落地方式：

- LangGraph 状态机。
- 条件路由控制下一步。

验收：

- 信息不足时不会强行推荐。
- 高风险时不会进入普通推荐话术。

### 6.7 Guardrails

落地方式：

- 输入 Guardrail。
- 工具 Guardrail。
- 输出 Guardrail。

验收：

- 保健品场景不出现治疗承诺。
- 宠物异常症状时提示就医。

### 6.8 Human-in-the-loop

落地方式：

- 记忆写入确认卡片。
- 高风险推荐确认卡片。

验收：

- 用户可以选择记住、本次有效或不要记住。

### 6.9 Tracing

落地方式：

- 每个 Agent 和工具调用写 TraceEvent。
- 前端右侧 Trace Panel 展示。

验收：

- 能看到耗时、步骤、工具、错误和 fallback。

### 6.10 Evaluation

落地方式：

- 构建 `eval_cases.json` 和 `holdout_cases.json`。
- 为每条需求标注 Ground Truth 商品、硬约束、固定问卷问题数和人工质量评分。
- 对比热门推荐、低参考单价推荐、固定问卷交互和完整 Agent。

验收：

- 前端展示 Recommendation Accuracy、Constraint Satisfaction、Dynamic Question Efficiency、Recommendation Quality、Latency / Token Cost 五类指标。

## 7. 开发顺序

### Step 1：项目骨架

创建：

```text
backend
frontend
data
docs
docker-compose.yml
README.md
```

完成后端 FastAPI 和前端 Next.js 基础启动。

### Step 2：Schema 和本地数据

先完成：

```text
schemas
domain configs
seed products
knowledge docs
```

### Step 3：不接 LLM 的规则版流程

先让系统不用 LLM 也能：

```text
查商品
生成基础问题
算风险
算排序
输出推荐
记录 trace
```

### Step 4：接入 LLM Agent

把 LLM 用在最有价值的地方：

```text
意图理解
动态问题生成
用户回答解析
推荐解释
表达适配
记忆提议
```

### Step 5：前端工作台

做一个能面试展示的页面：

```text
输入
问题
推荐表格
画像
记忆
Trace
```

### Step 6：Guardrails、HITL、Evaluation

补齐工程深度：

```text
安全边界
记忆确认
评估对比
成本延迟统计
```

## 8. 避免返工的关键决策

1. 一开始就用 Domain Config，不写死猫粮或保健品。
2. 一开始就用 Pydantic Schema，不让 Agent 输出自由文本。
3. 一开始就记录 Trace，不等后期再补日志。
4. 一开始就保留 Data Provider 接口，不绑定本地 JSON。
5. 一开始就区分推荐依据和表达偏好，避免 MBTI、星座影响科学判断。
6. 一开始就做风险边界，尤其保健品场景。
7. 先做规则版闭环，再逐步接入 LLM。

## 9. 下一步具体任务

下一步直接开始搭项目骨架：

1. 创建后端 FastAPI 结构。
2. 创建前端 Next.js 结构。
3. 创建 `data` 目录和 Domain Config。
4. 写第一版 Pydantic Schema。
5. 写本地 Product Provider。
6. 写第一条规则版推荐链路。

完成这一步后，系统应该能在不接 OpenAI API 的情况下跑通：

```text
输入需求 → 判断领域 → 取候选商品 → 生成问题 → 模拟回答 → 输出推荐表格
```

这会成为后续所有 Agent 能力的稳定地基。
