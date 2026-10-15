const state = {
  sessionId: null,
  questions: [],
  candidates: [],
  inferredAnswers: {},
  selectedAnswers: {},
  route: null,
  marketData: null,
  externalDiscovery: null,
  workflowNodes: [],
};

const els = {
  refreshMarket: document.querySelector("#refreshMarket"),
  marketTitle: document.querySelector("#marketTitle"),
  marketStatus: document.querySelector("#marketStatus"),
  marketDates: document.querySelector("#marketDates"),
  marketEntries: document.querySelector("#marketEntries"),
  marketMatchInfo: document.querySelector("#marketMatchInfo"),
  marketDetails: document.querySelector("#marketDetails"),
  marketSourceLink: document.querySelector("#marketSourceLink"),
  status: document.querySelector("#serviceStatus"),
  llmStatus: document.querySelector("#llmStatus"),
  query: document.querySelector("#queryInput"),
  start: document.querySelector("#startButton"),
  answer: document.querySelector("#answerButton"),
  questionArea: document.querySelector("#questionArea"),
  resultRows: document.querySelector("#resultRows"),
  summary: document.querySelector("#summaryBox"),
  runSnapshot: document.querySelector("#runSnapshotBox"),
  aiMode: document.querySelector("#aiModeBox"),
  runtimeMetrics: document.querySelector("#runtimeMetricsBox"),
  playbook: document.querySelector("#playbookBox"),
  featureInsight: document.querySelector("#featureInsightBox"),
  winnerCard: document.querySelector("#winnerCard"),
  evidenceBox: document.querySelector("#evidenceBox"),
  domainBadge: document.querySelector("#domainBadge"),
  candidateStrip: document.querySelector("#candidateStrip"),
  profileList: document.querySelector("#profileList"),
  memoryList: document.querySelector("#memoryList"),
  savedMemoryList: document.querySelector("#savedMemoryList"),
  agentTimeline: document.querySelector("#agentTimeline"),
  supervisorDecisionList: document.querySelector("#supervisorDecisionList"),
  traceSummary: document.querySelector("#traceSummary"),
  traceList: document.querySelector("#traceList"),
  runEval: document.querySelector("#runEvalButton"),
  currentComparison: document.querySelector("#currentComparison"),
  evalSummary: document.querySelector("#evalSummary"),
  evalRows: document.querySelector("#evalRows"),
};

async function api(path, options = {}) {
  const { timeoutMs = 30000, ...fetchOptions } = options;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      ...fetchOptions,
    });
  } catch (error) {
    if (error.name === "AbortError") {
      throw new Error("请求超时，请稍后重试");
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json();
}

async function checkHealth() {
  try {
    await api("/api/health");
    els.status.textContent = "API online";
    els.status.classList.add("ok");
    const llm = await api("/api/llm/status");
    els.llmStatus.textContent = llm.enabled ? `LLM ${llm.model}` : "Rules mode";
    els.llmStatus.classList.toggle("ok", Boolean(llm.enabled));
  } catch (error) {
    els.status.textContent = "API offline";
    els.llmStatus.textContent = "Mode unknown";
  }
}

let marketRequest = null;

function renderMarket(data) {
  if (data?.selected_source || data?.mode === "search_fallback") {
    renderExternalDiscovery(data);
    return;
  }
  const config = marketConfigForCurrentRoute();
  els.marketTitle.textContent = config.title;
  els.marketSourceLink.href = config.url;
  els.marketSourceLink.textContent = config.linkText;
  if (!config.supportsSnapshot) {
    state.marketData = null;
    els.refreshMarket.textContent = "打开来源";
    els.marketStatus.textContent = "当前品类未接入可校验榜单快照";
    els.marketDates.innerHTML = `<span>外部来源：${escapeHtml(config.sourceLabel)}</span><span>价格口径：仅作搜索入口，不参与排序</span>`;
    els.marketMatchInfo.textContent = config.note;
    els.marketEntries.innerHTML = "";
    els.marketDetails.open = false;
    return;
  }

  state.marketData = data;
  const labels = { never: "尚未采集", updated: "已取得榜单新快照", unchanged: "检查成功，榜单内容未变化", failed: "本次检查失败" };
  const snapshot = data.snapshot;
  const cacheText = data.refresh_result === "cached"
    ? (data.retry_after_seconds <= 30 ? ` · ${data.retry_after_seconds} 秒后可再次检查` : " · 使用已保存快照，可手动刷新")
    : "";
  els.marketStatus.textContent = `${labels[data.status] || "榜单状态未知"}${cacheText}${data.stale && snapshot ? " · 快照已过期" : ""}${data.error ? `：${data.error}${snapshot ? "，保留上次成功结果" : "，暂无可用快照"}` : ""}`;
  const dateText = value => value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "暂无";
  els.marketDates.innerHTML = [
    ["最近检查", data.last_checked_at], ["最近成功采集", data.last_success_at],
    ["内容变化", snapshot?.content_changed_at], ["平台发布时间", snapshot?.source_published_at],
  ].map(([label, value]) => `<span>${label}：${escapeHtml(dateText(value))}</span>`).join("");
  const fitNote = marketFitNote(snapshot);
  els.marketMatchInfo.textContent = snapshot
    ? `这是京东猫粮总榜，只作为外部热度参考，不等于当前细分需求榜。${fitNote} 采集 ${snapshot.entries.length} 条，SKU 与规格匹配 ${data.matched_count} 条，其余待核验。榜单未提供的价格保持为空；推荐仍使用本地参考配方和参考单价。已有推荐不会随刷新改变，需重新开始分析。`
    : "暂无联网榜单可用，推荐使用本地参考商品。";
  els.marketEntries.innerHTML = (snapshot?.entries || []).map(entry => `
    <li class="market-entry">
      <b>${entry.rank}</b>
      ${entry.image_url ? `<img src="${escapeHtml(entry.image_url)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : "<span></span>"}
      <div><a href="${escapeHtml(entry.product_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(entry.title)}</a>
        <small>SKU ${escapeHtml(entry.jd_sku)} · ${escapeHtml(entry.spec || "规格待核验")} · ${escapeHtml(entry.review_count_label || "评论数未提供")}</small>
        <small>${entry.price == null ? "榜单未提供价格" : `榜单采集价 ¥${escapeHtml(entry.price)}`} · ${escapeHtml(entry.match_reason)}</small>
      </div>
    </li>`).join("");
}

function renderExternalDiscovery(data) {
  state.externalDiscovery = data;
  const source = data.selected_source;
  const snapshot = data.snapshot;
  els.marketTitle.textContent = source?.name || "通用品类外部搜索参考";
  els.marketSourceLink.href = source?.url || data.search_url || jdSearchUrl(els.query.value.trim() || "商品 推荐");
  els.marketSourceLink.textContent = source ? "打开热榜来源" : "打开京东搜索";
  els.refreshMarket.textContent = source ? "刷新来源" : "打开来源";
  els.marketStatus.textContent = `${snapshot ? "已取得外部热榜快照" : "暂无可解析热榜快照"}${data.error ? `：${data.error}` : ""}`;
  els.marketDates.innerHTML = [
    ["来源", source?.name || "京东搜索入口"],
    ["模式", data.mode === "hotlist_snapshot" ? "热榜快照" : "搜索回退"],
    ["采集时间", snapshot?.observed_at],
    ["状态", data.status || "-"],
  ].map(([label, value]) => `<span>${label}：${escapeHtml(formatSourceValue(value))}</span>`).join("");
  els.marketMatchInfo.textContent = data.source_note || "外部来源仅作候选召回和热度参考，不直接写入记忆，也不覆盖风险规则。";
  els.marketEntries.innerHTML = (snapshot?.entries || []).map(entry => `
    <li class="market-entry">
      <b>${entry.rank}</b>
      ${entry.image_url ? `<img src="${escapeHtml(entry.image_url)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : "<span></span>"}
      <div><a href="${escapeHtml(entry.product_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(entry.title)}</a>
        <small>SKU ${escapeHtml(entry.jd_sku)} · ${escapeHtml(entry.spec || "规格待核验")} · ${escapeHtml(entry.review_count_label || "评论数未提供")}</small>
        <small>${entry.price == null ? "价格未结构化核验" : `榜单采集价 ¥${escapeHtml(entry.price)}`} · 外部热度参考</small>
      </div>
    </li>`).join("");
  els.marketDetails.open = Boolean(snapshot?.entries?.length);
}

function formatSourceValue(value) {
  if (!value) return "暂无";
  if (typeof value !== "string" || !value.includes("T")) return String(value);
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function marketConfigForCurrentRoute() {
  const route = state.route;
  const query = els.query.value.trim() || "商品 推荐";
  if (route?.domain === "pet_food") {
    return {
      title: "京东猫粮总榜参考",
      linkText: "查看京东猫粮原榜单",
      url: "https://www.jd.com/phb/key_699402c85b220b9a9f13.html",
      sourceLabel: "京东猫粮榜单",
      supportsSnapshot: true,
      note: "",
    };
  }

  if (route?.domain === "supplement") {
    const keywordByScenario = {
      fish_oil: "鱼油 保健品",
      probiotics: "益生菌 保健品",
      lutein: "叶黄素 护眼 保健品",
      multivitamin: "复合维生素 保健品",
      calcium_vitamin_d: "钙片 维生素D 保健品",
    };
    const keyword = keywordByScenario[route.scenario] || "保健品";
    return {
      title: `京东${keyword.split(" ")[0]}搜索参考`,
      linkText: `打开京东搜索：${keyword}`,
      url: jdSearchUrl(keyword),
      sourceLabel: "京东搜索页",
      supportsSnapshot: false,
      note: "当前没有接入可稳定解析的保健品榜单快照，因此页面只提供对应搜索入口；推荐排序仍使用本地商品库、风险规则和用户回答，不把搜索页价格当实时价格。",
    };
  }

  return {
    title: "通用品类外部搜索参考",
    linkText: `打开京东搜索：${query}`,
    url: jdSearchUrl(query),
    sourceLabel: "京东搜索页",
    supportsSnapshot: false,
    note: "当前品类还没有专属商品库和可校验榜单；系统会先做需求澄清，不会强行显示价格或商品排序。",
  };
}

function jdSearchUrl(keyword) {
  return `https://search.jd.com/Search?keyword=${encodeURIComponent(keyword)}`;
}

function marketFitNote(snapshot) {
  if (!snapshot?.entries?.length) return "";
  const answers = { ...(state.inferredAnswers || {}), ...(state.selectedAnswers || {}) };
  const titleText = (entry) => `${entry.title || ""} ${entry.spec || ""}`;
  if (answers.pet_age_stage === "幼猫") {
    const count = snapshot.entries.filter((entry) => /幼猫|奶糕|奶猫|离乳|成长/.test(titleText(entry))).length;
    return `当前识别为幼猫需求，总榜中标题直接命中幼猫/奶糕的有 ${count} 条；若为 0 或很少，榜单不能替代幼猫适配判断。`;
  }
  if (answers.priority === "美毛泪痕") {
    const count = snapshot.entries.filter((entry) => /美毛|泪痕|鱼油|三文鱼|海洋鱼|深海鱼/.test(titleText(entry))).length;
    return `当前主目标是美毛泪痕，总榜中标题直接命中相关词的有 ${count} 条；系统仍会以配方、阶段和风险规则排序。`;
  }
  if (answers.priority === "性价比" || answers.budget_range === "预算优先") {
    return "当前偏预算优先，榜单热度不会直接覆盖参考单价和包装规格判断。";
  }
  return "当前需求未限定到榜单可直接证明的细分场景，所以它不会直接决定 Top1。";
}

async function refreshMarket(force = false) {
  const config = marketConfigForCurrentRoute();
  if (state.externalDiscovery?.selected_source || state.route?.domain === "general" || state.route?.domain === "supplement") {
    if (marketRequest) return marketRequest;
    const query = encodeURIComponent(els.query.value.trim() || "商品 推荐");
    const domain = encodeURIComponent(state.route?.domain || "general");
    const scenario = encodeURIComponent(state.route?.scenario || "");
    marketRequest = (async () => {
      setLoading(els.refreshMarket, true, state.externalDiscovery?.selected_source ? "刷新来源" : "打开来源");
      try {
        const data = await api(`/api/market/jd/discover?query=${query}&domain=${domain}&scenario=${scenario}&refresh=${force}`, { timeoutMs: 18000 });
        renderMarket(data);
      } catch (error) {
        els.marketStatus.textContent = `外部来源检查失败：${error.message}`;
      } finally {
        setLoading(els.refreshMarket, false, state.externalDiscovery?.selected_source ? "刷新来源" : "打开来源");
      }
    })();
    try { await marketRequest; } finally { marketRequest = null; }
    return;
  }
  if (!config.supportsSnapshot) {
    renderMarket(null);
    return;
  }
  if (marketRequest) return marketRequest;
  marketRequest = (async () => {
    setLoading(els.refreshMarket, true, "刷新榜单");
    els.marketStatus.textContent = "正在检查京东榜单…";
    try {
      const data = await api(`/api/market/jd/refresh?force=${force}`, { method: "POST", timeoutMs: 18000 });
      renderMarket(data);
      if (data.refresh_result === "busy") els.marketStatus.textContent = "另一请求正在刷新，稍后再次查看";
    } catch (error) {
      try { renderMarket(await api("/api/market/jd", { timeoutMs: 4000 })); } catch (_) { /* Keep the visible snapshot. */ }
      els.marketStatus.textContent = `榜单检查未完成：${error.message}。推荐可继续使用本地资料。`;
    } finally {
      setLoading(els.refreshMarket, false, "刷新榜单");
    }
  })();
  try { await marketRequest; } finally { marketRequest = null; }
}

async function loadSavedMemories() {
  try {
    const memories = await api("/api/memory/demo_user", { timeoutMs: 8000 });
    renderSavedMemories(memories);
  } catch (error) {
    els.savedMemoryList.innerHTML = '<p class="empty-text">长期记忆读取失败</p>';
  }
}

function setLoading(button, loading, label) {
  button.disabled = loading;
  button.textContent = loading ? "处理中..." : label;
}

function renderQuestions(questions, inferredAnswers = {}) {
  els.questionArea.innerHTML = "";
  const hasValidInferredAnswer = (question) => {
    const value = inferredAnswers[question.id];
    if (value === null || value === undefined) return false;
    if (typeof value === "string" && ["", "null", "none", "undefined", "unknown", "未知", "未提及"].includes(value.trim().toLowerCase())) {
      return false;
    }
    if (Array.isArray(value)) {
      return value.length > 0 && (!question.options?.length || value.every((item) => question.options.includes(item)));
    }
    if (question.options?.length && !question.options.includes(value)) return false;
    return true;
  };
  const visibleQuestions = questions.filter((question) => !hasValidInferredAnswer(question));
  const inferredEntries = questions
    .filter((question) => hasValidInferredAnswer(question))
    .map((question) => [question.label, inferredAnswers[question.id]]);

  if (inferredEntries.length) {
    const inferredCard = document.createElement("div");
    inferredCard.className = "question-card inferred-card";
    inferredCard.innerHTML = `
      <label>已从输入识别</label>
      <p>这些条件会直接进入推荐排序，不需要重复回答。</p>
      <div class="inferred-list">
        ${inferredEntries
          .map(
            ([label, value]) => `
              <span>${escapeHtml(label)}：${escapeHtml(value)}</span>
            `,
          )
          .join("")}
      </div>
    `;
    els.questionArea.appendChild(inferredCard);
  }

  if (!visibleQuestions.length) {
    if (!inferredEntries.length) {
      els.questionArea.innerHTML = '<p class="empty-text">暂无问题</p>';
    }
    els.answer.classList.remove("hidden");
    return;
  }

  visibleQuestions.forEach((question) => {
    const card = document.createElement("div");
    card.className = "question-card";
    const options = question.options
      .map((option) => `<option value="${escapeHtml(option)}">${escapeHtml(option)}</option>`)
      .join("");
    card.innerHTML = `
      <label for="${question.id}">${escapeHtml(question.label)}</label>
      <p>${escapeHtml(question.reason)}</p>
      <select id="${question.id}" data-question-id="${question.id}">
        ${options}
      </select>
    `;
    els.questionArea.appendChild(card);
  });
  els.answer.classList.remove("hidden");
}

function renderCandidates(products) {
  els.candidateStrip.innerHTML = "";
  products.slice(0, 12).forEach((product) => {
    const card = document.createElement("article");
    card.className = "candidate-card";
    card.innerHTML = `
      <strong>${escapeHtml(product.name)}</strong>
      <span>${escapeHtml(product.brand)} · ${escapeHtml(product.spec)} · ${priceLabel(product)} · ${product.unit_price == null ? "参考单价待核验" : `参考单价 ¥${escapeHtml(product.unit_price)} ${escapeHtml(unitPriceLabel(product))}`} · 本地热度分 ${product.popularity_score}</span>
      ${renderProductSource(product)}
    `;
    els.candidateStrip.appendChild(card);
  });
}

function priceLabel(product) {
  if (product.price == null || Number(product.price) <= 0 || product.features?.price_unverified) {
    return "参考价待核验";
  }
  return `参考价 ¥${escapeHtml(product.price)}`;
}

function renderProductSource(product) {
  const sourceParts = [];
  if (product.market_rank) {
    sourceParts.push(`历史榜单第${product.market_rank}名`);
  }
  if (product.review_count_label) {
    sourceParts.push(product.review_count_label);
  }
  sourceParts.push("参考价格待核验");
  if (product.ranking_observed_at) sourceParts.push(`榜单采集 ${product.ranking_observed_at}`);
  if (product.last_updated) {
    sourceParts.push(`本地资料日期 ${product.last_updated}`);
  }
  if (!sourceParts.length) {
    return "";
  }
  return `<small class="source-line">${sourceParts.map(escapeHtml).join(" · ")}</small>`;
}

function renderPlaybook(route) {
  if (!route?.scenario) {
    els.playbook.classList.add("hidden");
    els.playbook.innerHTML = "";
    return;
  }

  const evidence = route.scenario_evidence?.length
    ? `命中词：${route.scenario_evidence.map(escapeHtml).join("、")}`
    : "根据候选商品差异进入通用策略";
  els.playbook.innerHTML = `
    <strong>当前策略：${escapeHtml(route.scenario)}</strong>
    <span>置信度 ${route.scenario_confidence}</span>
    <p>${evidence}</p>
  `;
  els.playbook.classList.remove("hidden");
}

function renderFeatureInsights(featureAnalysis) {
  const insights = featureAnalysis?.insights || [];
  if (!insights.length) {
    els.featureInsight.classList.add("hidden");
    els.featureInsight.innerHTML = "";
    return;
  }

  els.featureInsight.innerHTML = `
    <div class="insight-head">
      <strong>商品差异分析</strong>
      <span>${featureAnalysis.total_products} 个候选</span>
    </div>
    <div class="insight-grid">
      ${insights
        .map(
          (insight) => `
            <article class="insight-card ${insight.importance}">
              <b>${escapeHtml(insight.label)}</b>
              <p>${escapeHtml(insight.summary)}</p>
              <span>关联问题：${escapeHtml(insight.related_question_ids.join("、") || "-")}</span>
            </article>
          `,
        )
        .join("")}
    </div>
  `;
  els.featureInsight.classList.remove("hidden");
}

function renderTrace(events) {
  renderAgentTimeline(events, state.workflowNodes || []);
  renderSupervisorDecisions(events);
  els.traceList.innerHTML = "";
  if (!events.length) {
    els.traceSummary.textContent = "等待运行";
    return;
  }
  const totalLatency = events.reduce((sum, event) => sum + Number(event.latency_ms || 0), 0);
  const llmTokens = events.reduce(
    (sum, event) => sum + Number(event.token_input || 0) + Number(event.token_output || 0),
    0,
  );
  const cacheHits = events.filter((event) => event.output_summary.includes("cache=hit")).length;
  els.traceSummary.textContent = `${events.length} 步 · ${totalLatency}ms · ${llmTokens} token · ${cacheHits} 次缓存命中`;

  events.forEach((event) => {
    const item = document.createElement("li");
    const tokens = Number(event.token_input || 0) + Number(event.token_output || 0);
    item.innerHTML = `
      <strong>${escapeHtml(agentLabel(event.agent))}</strong>
      <span>${escapeHtml(stepLabel(event.step))} · ${event.latency_ms}ms${tokens ? ` · ${tokens} token` : ""}</span>
      <span>${escapeHtml(compactTraceOutput(event.output_summary))}</span>
    `;
    els.traceList.appendChild(item);
  });
}

function renderWorkflowNodes(nodes, events = []) {
  renderAgentTimeline(events, nodes || []);
  renderSupervisorDecisions(events);
  if (!nodes?.length) {
    renderTrace(events);
    return;
  }
  const eventByStep = new Map(events.map((event) => [event.step, event]));
  const totalLatency = events.reduce((sum, event) => sum + Number(event.latency_ms || 0), 0);
  const llmTokens = events.reduce(
    (sum, event) => sum + Number(event.token_input || 0) + Number(event.token_output || 0),
    0,
  );
  const fallbackCount = nodes.filter((node) => node.fallback_used).length;
  els.traceSummary.textContent = `${nodes.length} 个节点 · ${totalLatency}ms · ${llmTokens} token · ${fallbackCount} 次 fallback`;
  els.traceList.innerHTML = nodes
    .map((node) => {
      const event = eventByStep.get(node.node_id);
      const handoff = node.handoff_to ? `交给 ${escapeHtml(stepLabel(node.handoff_to))}` : "流程结束";
      return `
        <li>
          <strong>${escapeHtml(agentLabel(node.agent))}</strong>
          <span>${escapeHtml(node.tool_name)} · ${escapeHtml(node.status)} · ${handoff}</span>
          <span>${escapeHtml(node.reason)}</span>
          ${event ? `<span>${escapeHtml(compactTraceOutput(event.output_summary))}</span>` : ""}
        </li>
      `;
    })
    .join("");
}

function agentLabel(agent) {
  const labels = {
    SupervisorAgent: "Supervisor 调度",
    ResearchAgent: "Research 子 Agent",
    DecisionAgent: "Decision 子 Agent",
    ResponseAgent: "Response 子 Agent",
    DomainRouterAgent: "品类路由",
    ExternalDiscoveryAgent: "外部发现",
    ProductDiscoveryAgent: "商品召回",
    FeatureAnalysisAgent: "差异分析",
    DynamicQuestionAgent: "动态提问",
    UserModelingAgent: "用户画像",
    RiskGuardrailAgent: "风险边界",
    PersonalizedRankingAgent: "个性化排序",
    KnowledgeRAGAgent: "RAG 证据",
    ResponseAdaptationAgent: "表达适配",
    MemoryUpdateAgent: "记忆提议",
  };
  return labels[agent] || agent;
}

function stepLabel(step) {
  const labels = {
    supervisor: "调度决策",
    dynamic_subagent: "动态子 Agent",
    domain_router: "识别需求",
    external_discovery: "外部热榜",
    product_discovery: "召回候选",
    feature_analysis: "分析差异",
    question_generation: "生成问题",
    user_modeling: "构建画像",
    risk_analysis: "检查风险",
    personalized_ranking: "计算排序",
    knowledge_retrieval: "检索依据",
    response_adaptation: "生成摘要",
    memory_update: "生成记忆",
    "decision.user_modeling": "构建画像",
    "decision.risk_analysis": "检查风险",
    "decision.personalized_ranking": "计算排序",
    "decision.knowledge_retrieval": "检索依据",
    "response.final_summary": "生成摘要",
    "response.memory_update": "生成记忆",
  };
  return labels[step] || step;
}

function renderAgentTimeline(events = [], nodes = []) {
  if (!els.agentTimeline) return;
  const createdEvents = events.filter((event) => event.event_type === "subagent_created");
  if (!createdEvents.length) {
    els.agentTimeline.innerHTML = `<p class="empty-text">等待 Supervisor 创建子 Agent</p>`;
    return;
  }

  const completedEvents = [...events.filter((event) => event.event_type === "subagent_completed")];
  const cards = createdEvents.map((event, index) => {
    const created = parseTraceValue(event.output_summary, "created") || "WorkerAgent";
    const objective = parseTraceValue(event.output_summary, "objective") || event.output_summary;
    const allowedTools = parseToolList(event.input_summary);
    const usedTools = unique(
      nodes
        .filter((node) => node.agent === created)
        .map((node) => node.tool_name)
        .filter(Boolean),
    );
    const completionIndex = completedEvents.findIndex((item) => item.agent === created);
    const completion = completionIndex >= 0 ? completedEvents.splice(completionIndex, 1)[0] : null;
    const toolText = (usedTools.length ? usedTools : allowedTools).slice(0, 5).join("、") || "等待工具调用";
    return `
      <article class="agent-card">
        <div class="agent-card-head">
          <span>${index + 1}</span>
          <strong>${escapeHtml(agentLabel(created))}</strong>
        </div>
        <p>${escapeHtml(objective)}</p>
        <div class="agent-meta">
          <b>Tools</b>
          <span>${escapeHtml(toolText)}</span>
        </div>
        <div class="agent-meta">
          <b>Result</b>
          <span>${escapeHtml(completion?.output_summary || "正在等待 WorkResult")}</span>
        </div>
      </article>
    `;
  });

  els.agentTimeline.innerHTML = cards.join("");
}

function renderSupervisorDecisions(events = []) {
  if (!els.supervisorDecisionList) return;
  const decisions = events.filter(
    (event) => event.agent === "SupervisorAgent" && event.event_type === "agent_decision",
  );
  if (!decisions.length) {
    els.supervisorDecisionList.innerHTML = `<p class="empty-text">等待调度决策</p>`;
    return;
  }

  els.supervisorDecisionList.innerHTML = decisions
    .map((event, index) => {
      const action = parseTraceValue(event.output_summary, "action") || "-";
      const next = parseTraceValue(event.output_summary, "next") || action;
      const mode = parseTraceValue(event.output_summary, "mode") || "-";
      const reason = parseTraceValue(event.output_summary, "reason") || event.output_summary;
      return `
        <article class="supervisor-card">
          <div class="supervisor-card-head">
            <span>${index + 1}</span>
            <strong>${escapeHtml(action)} → ${escapeHtml(agentLabel(next))}</strong>
          </div>
          <p>${escapeHtml(reason)}</p>
          <small>${escapeHtml(formatStateSummary(event.input_summary))}</small>
          <small>决策模式：${escapeHtml(mode === "llm" ? "LLM 结构化输出" : "规则兜底")}</small>
        </article>
      `;
    })
    .join("");
}

function parseTraceValue(text = "", key) {
  const marker = `${key}=`;
  const start = text.indexOf(marker);
  if (start < 0) return "";
  const rest = text.slice(start + marker.length);
  const nextKey = rest.search(/,\s[a-zA-Z_]+=|$/);
  return rest.slice(0, nextKey < 0 ? rest.length : nextKey).trim();
}

function parseToolList(text = "") {
  const match = text.match(/tools=\[(.*)\]/);
  if (!match) return [];
  return match[1]
    .split(",")
    .map((tool) => tool.replaceAll("'", "").replaceAll('"', "").trim())
    .filter(Boolean);
}

function formatStateSummary(text = "") {
  return text
    .replace("phase=start", "阶段=需求分析")
    .replace("phase=answer", "阶段=推荐生成")
    .replace("domain=", "品类=")
    .replace("products=", "候选商品=")
    .replace("questions=", "问题=")
    .replace("ranked=", "已排序=")
    .replace("results=", "已有结果=");
}

function unique(items) {
  return [...new Set(items)];
}

function compactTraceOutput(output) {
  return output
    .replaceAll("cache=hit", "缓存命中")
    .replaceAll("cache=miss", "未命中缓存")
    .replaceAll("mode=llm", "AI 摘要")
    .replaceAll("mode=rules_fallback", "规则回退")
    .replaceAll("mode=rules", "规则模式")
    .replaceAll("APIConnectionError: Connection error.", "OpenAI 连接失败")
    .replaceAll("APITimeoutError", "OpenAI 超时")
    .replaceAll("ValidationError", "结构化输出校验失败")
    .replaceAll("retrieved=", "候选 ")
    .replaceAll("products", " 个商品")
    .replaceAll("evidence=", "依据 ");
}

function renderProfile(profile) {
  if (!profile) {
    els.profileList.innerHTML = "<dt>状态</dt><dd>等待推荐</dd>";
    return;
  }

  const rows = [
    ["场景", profile.scenario_profile?.domain || "-"],
    ["预算敏感度", profile.decision_profile?.budget_sensitivity ?? "-"],
    ["风险敏感度", profile.decision_profile?.risk_sensitivity ?? "-"],
    ["关注重点", profile.decision_profile?.priority || "-"],
    ["展示偏好", profile.communication_profile?.prefers_table ? "表格" : "文本"],
    ["解释深度", profile.communication_profile?.detail_level || "-"],
    ["语气", profile.communication_profile?.tone || "-"],
  ];

  els.profileList.innerHTML = rows
    .map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(String(value))}</dd>`)
    .join("");
}

function renderMemory(memories) {
  if (!memories.length) {
    els.memoryList.innerHTML = '<p class="empty-text">暂无记忆提议</p>';
    return;
  }
  els.memoryList.innerHTML = memories
    .map((memory, index) => {
      const encoded = encodeURIComponent(JSON.stringify(memory));
      return `
        <article class="memory-card" data-memory-index="${index}">
          <strong>${escapeHtml(memory.key)} = ${escapeHtml(String(memory.value))}</strong>
          <p>置信度 ${memory.confidence} · ${escapeHtml(memory.evidence)}</p>
          <div class="memory-actions">
            <button data-memory="${encoded}" data-action="remember">记住</button>
            <button data-memory="${encoded}" data-action="session_only">本次有效</button>
            <button data-memory="${encoded}" data-action="reject">不记住</button>
          </div>
        </article>
      `;
    })
    .join("");
  bindMemoryActions();
}

function renderSavedMemories(memories) {
  if (!memories.length) {
    els.savedMemoryList.innerHTML = '<p class="empty-text">暂无长期记忆</p>';
    return;
  }
  els.savedMemoryList.innerHTML = memories
    .map(
      (memory) => `
        <article class="memory-card saved">
          <strong>${escapeHtml(memory.key)} = ${escapeHtml(String(memory.value))}</strong>
          <p>${escapeHtml(memory.domain)} · 置信度 ${memory.confidence}</p>
        </article>
      `,
    )
    .join("");
}

function bindMemoryActions() {
  document.querySelectorAll("[data-memory][data-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const memory = JSON.parse(decodeURIComponent(button.dataset.memory));
      const action = button.dataset.action;
      button.disabled = true;
      try {
        await api("/api/memory/decision", {
          method: "POST",
          body: JSON.stringify({ memory, action }),
          timeoutMs: 8000,
        });
        button.closest(".memory-card").remove();
        await loadSavedMemories();
      } catch (error) {
        button.disabled = false;
      }
    });
  });
}

function renderRecommendation(recommendation, memories, traceEvents, runtimeMetrics) {
  els.summary.textContent = recommendation.summary;
  renderRunSnapshot(recommendation, traceEvents, runtimeMetrics, state.workflowNodes || []);
  renderResponseMode(recommendation.response_mode);
  renderRuntimeMetrics(runtimeMetrics);
  renderProfile(recommendation.user_profile);
  renderMemory(memories);
  renderTrace(traceEvents);
  renderWinner(recommendation);
  renderEvidence(recommendation.knowledge_evidence || []);

  const rows = recommendation.top_products
    .map((item) => {
      const product = item.product;
      return `
        <tr>
          <td>${item.rank}</td>
          <td><strong>${escapeHtml(product.name)}</strong><br><span class="empty-text">${escapeHtml(product.brand)}</span>${renderProductSource(product)}</td>
          <td>${priceLabel(product).replace("参考价 ", "")}</td>
          <td>${product.unit_price == null ? "待核验" : `¥${escapeHtml(product.unit_price)} ${escapeHtml(unitPriceLabel(product))}`}</td>
          <td><span class="score">${item.final_score}</span></td>
          <td>${escapeHtml(item.suitable_reasons.join("、"))}</td>
          <td>${escapeHtml(item.tradeoffs.join("、"))}</td>
          <td><div class="risk">${item.risk_notes.map((note) => `<span>${escapeHtml(note)}</span>`).join("") || "-"}</div></td>
        </tr>
      `;
    })
    .join("");

  els.resultRows.innerHTML = rows || `<tr><td colspan="8" class="empty">${escapeHtml(recommendation.status_reason || "暂无推荐结果")}</td></tr>`;
}

function unitPriceLabel(product) {
  if (product.domain === "pet_food") return "/kg";
  if (product.domain === "supplement") return "/计价单位";
  return "";
}

function renderRunSnapshot(recommendation, traceEvents, metrics, workflowNodes = []) {
  const evidenceCount = recommendation.knowledge_evidence?.length || 0;
  const workflowSteps = workflowNodes.length || traceEvents.length;
  const tokenTotal = Number(metrics?.estimated_input_tokens || 0) + Number(metrics?.estimated_output_tokens || 0);
  const fallbackCount = workflowNodes.filter((node) => node.fallback_used).length;
  els.runSnapshot.innerHTML = `
    <article>
      <span>Workflow</span>
      <strong>${workflowSteps} 步</strong>
      <small>${fallbackCount ? `${fallbackCount} 个节点触发 fallback` : "显式工具编排"}</small>
    </article>
    <article>
      <span>RAG</span>
      <strong>${evidenceCount} 条依据</strong>
      <small>本地知识检索</small>
    </article>
    <article>
      <span>Cost</span>
      <strong>${tokenTotal} token</strong>
      <small>${metrics?.cache_hits || 0} 次缓存命中</small>
    </article>
  `;
  els.runSnapshot.classList.remove("hidden");
}

function renderRuntimeMetrics(metrics) {
  if (!metrics) {
    els.runtimeMetrics.classList.add("hidden");
    els.runtimeMetrics.innerHTML = "";
    return;
  }

  const notes = (metrics.cost_control_notes || [])
    .map((note) => `<span>${escapeHtml(note)}</span>`)
    .join("");
  els.runtimeMetrics.innerHTML = `
    <div class="runtime-head">
      <strong>成本与缓存</strong>
      <span>本次运行指标</span>
    </div>
    <div class="runtime-grid">
      <div><b>${metrics.total_latency_ms}</b><span>步骤耗时 ms</span></div>
      <div><b>${metrics.llm_calls}</b><span>LLM 调用</span></div>
      <div><b>${metrics.estimated_input_tokens}</b><span>估算输入 token</span></div>
      <div><b>${metrics.estimated_output_tokens}</b><span>估算输出 token</span></div>
      <div><b>${metrics.cache_hits}</b><span>缓存命中</span></div>
      <div><b>${metrics.cache_misses}</b><span>缓存未命中</span></div>
    </div>
    <div class="runtime-notes">${notes}</div>
  `;
  els.runtimeMetrics.classList.remove("hidden");
}

function renderEvidence(evidence) {
  if (!evidence.length) {
    els.evidenceBox.classList.add("hidden");
    els.evidenceBox.innerHTML = "";
    return;
  }
  els.evidenceBox.innerHTML = `
    <div class="evidence-head">
      <strong>推荐依据</strong>
      <span>Hybrid RAG + Rerank 召回 ${evidence.length} 条</span>
    </div>
    <div class="evidence-list">
      ${evidence
        .map(
          (item) => `
            <article>
              <b>${escapeHtml(item.title)}</b>
              <p>${escapeHtml(item.content)}</p>
              <span>${escapeHtml(item.source)} · ${escapeHtml(item.chunk_type || "rule")} · ${escapeHtml(item.chunk_id || "-")}</span>
              <span>召回：${escapeHtml(item.retrieval_stage || "keyword")} · 命中 ${escapeHtml(item.matched_terms.join("、") || "-")}</span>
              <span>Rerank ${item.rerank_score || item.relevance_score} · ${escapeHtml(item.rerank_reason || "用于补充推荐解释")}</span>
            </article>
          `,
        )
        .join("")}
    </div>
  `;
  els.evidenceBox.classList.remove("hidden");
}

function renderCurrentComparison(comparison) {
  if (!comparison) {
    els.currentComparison.textContent = "生成推荐后，会展示当前 Top1 与热门基线、低参考单价基线的差异，用于证明排序不是只看一个单点指标。";
    return;
  }
  els.currentComparison.innerHTML = `
    <div class="comparison-head">
      <strong>当前推荐对比</strong>
      <span>${escapeHtml(comparison.conclusion)}</span>
    </div>
    <p class="comparison-note">这是给开发和面试看的 baseline：它证明 Agent 不是只按热度或低参考单价拍脑袋排序。</p>
    <div class="comparison-grid">
      <article>
        <b>个性化 Agent</b>
        <p>${escapeHtml(comparison.agent_top_product || "-")}</p>
        <span>推荐分 ${comparison.agent_score}</span>
      </article>
      <article>
        <b>热门 Baseline</b>
        <p>${escapeHtml(comparison.popularity_top_product || "-")}</p>
        <span>本地热度分 ${comparison.popularity_score}（非销量）</span>
      </article>
      <article>
        <b>低参考单价 Baseline</b>
        <p>${escapeHtml(comparison.value_top_product || "-")}</p>
        <span>参考单价 ${comparison.value_unit_price == null ? "待核验" : `${escapeHtml(comparison.value_unit_price)} ${escapeHtml(comparison.value_unit_label)}`}</span>
      </article>
    </div>
  `;
}

function renderResponseMode(mode) {
  const labels = {
    llm: "本次摘要由 AI 生成，排序和风险仍由规则控制",
    rules: "本次使用规则模式，没有调用 AI",
    rules_fallback: "AI 摘要连接失败或超时，本次已使用规则摘要兜底；排序、风险和 RAG 不受影响",
  };
  els.aiMode.textContent = labels[mode] || labels.rules;
  els.aiMode.className = `ai-mode-box ${mode || "rules"}`;
  els.aiMode.classList.remove("hidden");
}

function renderWinner(recommendation) {
  const items = recommendation.top_products || [];
  const winner = items[0];
  const runnerUp = items[1];
  if (!winner) {
    els.winnerCard.innerHTML = `
      <div class="winner-main">
        <div>
          <p class="section-kicker">Decision Status</p>
          <h3>${recommendation.recommendation_status === "no_suitable_candidate" ? "暂无安全主推荐" : "暂无推荐结果"}</h3>
          <p>${escapeHtml(recommendation.status_reason || recommendation.summary || "需要补充信息后再推荐。")}</p>
        </div>
      </div>
    `;
    els.winnerCard.classList.remove("hidden");
    return;
  }

  const gap = runnerUp ? (winner.final_score - runnerUp.final_score).toFixed(2) : "0.00";
  const breakdown = Object.entries(winner.score_breakdown)
    .map(
      ([key, value]) => `
        <div class="breakdown-row">
          <span>${scoreLabel(key)}</span>
          <div class="bar"><i style="width:${Math.max(0, Math.min(100, value))}%"></i></div>
          <b>${value}</b>
        </div>
      `,
    )
    .join("");

  els.winnerCard.innerHTML = `
    <div class="winner-main">
      <div>
        <p class="section-kicker">Top Choice</p>
        <h3>${escapeHtml(winner.product.name)}</h3>
        <p>${escapeHtml(winner.suitable_reasons.join("、"))}</p>
        ${renderProductSource(winner.product)}
      </div>
      <div class="winner-score">
        <strong>${winner.final_score}</strong>
        <span>领先第2名 ${gap}</span>
      </div>
    </div>
    <div class="breakdown">${breakdown}</div>
  `;
  els.winnerCard.classList.remove("hidden");
}

function scoreLabel(key) {
  const labels = {
    suitability: "需求适配",
    safety: "风险安全",
    value_match: "性价比",
    popularity: "热度",
    preference_match: "偏好匹配",
  };
  return labels[key] || key;
}

async function startDecision() {
  setLoading(els.start, true, "开始分析");
  try {
    const data = await api("/api/decision/start", {
      method: "POST",
      body: JSON.stringify({
        query: els.query.value.trim(),
        user_id: "demo_user",
        use_external_refresh: true,
      }),
      timeoutMs: 45000,
    });

    state.sessionId = data.session_id;
    state.questions = data.questions;
    state.candidates = data.candidate_products;
    state.inferredAnswers = data.inferred_answers || {};
    state.selectedAnswers = {};
    state.route = data.route;
    state.externalDiscovery = data.external_discovery || null;
    state.workflowNodes = data.workflow_nodes || [];
    els.domainBadge.textContent = data.route.domain;
    els.summary.textContent = `已召回 ${data.candidate_products.length} 个候选商品。系统会先分析这些商品在价格、适用场景、成分/功能和风险上的差异，再生成 ${data.questions.length} 个关键问题。`;
    renderPlaybook(data.route);
    renderFeatureInsights(data.feature_analysis);
    renderQuestions(data.questions, state.inferredAnswers);
    renderMarket(state.externalDiscovery || null);
    renderCandidates(data.candidate_products);
    renderWorkflowNodes(state.workflowNodes, data.trace_events);
    renderStartSnapshot(data.workflow_nodes || [], data.candidate_products || [], data.feature_analysis);
    renderProfile(null);
    renderMemory([]);
    els.winnerCard.classList.add("hidden");
    els.winnerCard.innerHTML = "";
    els.evidenceBox.classList.add("hidden");
    els.evidenceBox.innerHTML = "";
    els.runtimeMetrics.classList.add("hidden");
    els.runtimeMetrics.innerHTML = "";
    els.resultRows.innerHTML = '<tr><td colspan="8" class="empty">回答问题后生成推荐结果</td></tr>';
  } catch (error) {
    els.summary.textContent = `请求失败：${error.message}`;
    renderResponseMode("rules_fallback");
  } finally {
    setLoading(els.start, false, "开始分析");
  }
}

function renderStartSnapshot(workflowNodes, products, featureAnalysis) {
  const insightCount = featureAnalysis?.insights?.length || 0;
  els.runSnapshot.innerHTML = `
    <article>
      <span>Workflow</span>
      <strong>${workflowNodes.length} 步</strong>
      <small>等待用户回答</small>
    </article>
    <article>
      <span>Products</span>
      <strong>${products.length} 个候选</strong>
      <small>${insightCount} 类差异</small>
    </article>
    <article>
      <span>Next</span>
      <strong>动态提问</strong>
      <small>根据商品差异补充信息</small>
    </article>
  `;
  els.runSnapshot.classList.remove("hidden");
}

async function answerQuestions() {
  if (!state.sessionId) return;

  const answers = {};
  document.querySelectorAll("[data-question-id]").forEach((select) => {
    answers[select.dataset.questionId] = select.value;
  });
  state.selectedAnswers = answers;

  setLoading(els.answer, true, "生成推荐");
  try {
    const data = await api("/api/decision/answer", {
      method: "POST",
      body: JSON.stringify({
        session_id: state.sessionId,
        user_id: "demo_user",
        answers,
      }),
      timeoutMs: 45000,
    });
    state.workflowNodes = data.workflow_nodes || state.workflowNodes;
    renderRecommendation(
      data.recommendation,
      data.proposed_memories,
      data.trace_events,
      data.runtime_metrics,
    );
    renderWorkflowNodes(state.workflowNodes, data.trace_events);
    renderCurrentComparison(data.current_comparison);
    if (state.marketData) renderMarket(state.marketData);
  } catch (error) {
    els.summary.textContent = `请求失败：${error.message}`;
    renderResponseMode("rules_fallback");
  } finally {
    setLoading(els.answer, false, "生成推荐");
  }
}

async function runEvaluation() {
  setLoading(els.runEval, true, "运行评估");
  try {
    const data = await api("/api/evaluation/run", {
      method: "POST",
      body: "{}",
      timeoutMs: 30000,
    });
    renderEvaluation(data);
  } catch (error) {
    els.evalSummary.textContent = `评估失败：${error.message}`;
  } finally {
    setLoading(els.runEval, false, "运行评估");
  }
}

function renderEvaluation(data) {
  const accuracy = data.recommendation_accuracy || {};
  const constraints = data.constraint_satisfaction || {};
  const deferral = data.safety_deferral || {};
  const questions = data.question_efficiency || {};
  const quality = data.recommendation_quality || {};
  const cost = data.cost_efficiency || {};
  const capability = data.agent_capability || {};
  const methodology = data.methodology || {};
  const validation = data.dataset_validation || {};
  const splitRows = (data.split_summaries || [])
    .map((item) => `
      <tr>
        <td>${escapeHtml(item.split)}</td>
        <td>${item.total_cases}</td>
        <td>${item.recommendable_cases}</td>
        <td>${toPercent(item.top1_accuracy)}</td>
        <td>${toPercent(item.top3_hit_rate)}</td>
        <td>${toPercent(item.constraint_satisfaction_rate)}</td>
        <td>${toPercent(item.status_match_rate)}</td>
        <td>${item.avg_question_count}</td>
      </tr>
    `)
    .join("");
  const categoryRows = (data.category_summaries || [])
    .map((item) => `
      <tr>
        <td>${escapeHtml(item.category)}</td>
        <td>${item.total_cases}</td>
        <td>${toPercent(item.top1_accuracy)}</td>
        <td>${toPercent(item.top3_hit_rate)}</td>
        <td>${toPercent(item.constraint_satisfaction_rate)}</td>
        <td>${toPercent(item.status_match_rate)}</td>
        <td>${item.avg_question_count}</td>
      </tr>
    `)
    .join("");
  const qualityRow = (label, item = {}) => `
    <tr>
      <td>${label}</td>
      <td>${formatScore(item.relevance)}</td>
      <td>${formatScore(item.personalization)}</td>
      <td>${formatScore(item.explanation)}</td>
    </tr>
  `;
  els.evalSummary.innerHTML = `
    <p>固定评估用于证明推荐策略是否优于简单方案，不随当前表单变化。当前采用人工标注 Ground Truth、硬约束检查、动态问题效率、人工质量评分和成本估算五类指标。</p>
    <div class="metric-grid">
      <div><strong>${data.total_cases}</strong><span>用例数</span></div>
      <div><strong>${data.holdout_cases || 0}</strong><span>Holdout 用例</span></div>
      <div><strong>${data.independent_cases || 0}</strong><span>独立实验用例</span></div>
      <div><strong>${toPercent(accuracy.agent_top1_accuracy)}</strong><span>Top-1 Accuracy</span></div>
      <div><strong>${toPercent(accuracy.agent_top3_hit_rate)}</strong><span>Top-3 Hit Rate</span></div>
      <div><strong>${toPercent(constraints.agent_rate)}</strong><span>Top1 硬约束满足</span></div>
      <div><strong>${toPercent(deferral.deferral_accuracy)}</strong><span>安全拒推命中</span></div>
      <div><strong>${questions.adaptive_avg_questions ?? "-"}</strong><span>动态平均问题数</span></div>
      <div><strong>${toPercent(questions.reduction_rate)}</strong><span>减少提问轮次</span></div>
      <div><strong>${Math.round(cost.agent_avg_token_total || 0)}</strong><span>平均 token</span></div>
      <div><strong>${toPercent(cost.token_reduction_rate)}</strong><span>Token 节省估算</span></div>
      <div><strong>${toPercent(capability.hitl_memory_gate_rate)}</strong><span>HITL 记忆门控</span></div>
      <div><strong>${toPercent(capability.tool_call_coverage_rate)}</strong><span>工具覆盖率</span></div>
    </div>
    <div class="eval-cards">
      <article>
        <h3>① 推荐准确率</h3>
        <p>Agent Top-1：${toPercent(accuracy.agent_top1_accuracy)}；Top-3 命中：${toPercent(accuracy.agent_top3_hit_rate)}。热门基线：${toPercent(accuracy.popularity_top1_accuracy)}；低单价基线：${toPercent(accuracy.value_top1_accuracy)}。</p>
      </article>
      <article>
        <h3>② 硬约束满足率</h3>
        <p>Agent Top1：${toPercent(constraints.agent_rate)}；热门基线：${toPercent(constraints.popularity_rate)}；低单价基线：${toPercent(constraints.value_rate)}。硬约束包括年龄阶段、预算、必须功能和排除项。</p>
      </article>
      <article>
        <h3>③ 安全拒推</h3>
        <p>预期拒推 ${deferral.expected_deferrals || 0} 条；拒推命中 ${toPercent(deferral.deferral_accuracy)}；误拒推 ${toPercent(deferral.false_deferral_rate)}。这类 case 的正确行为是不给主推荐，而不是强行选一个商品。</p>
      </article>
      <article>
        <h3>④ 动态问题效率</h3>
        <p>固定问卷平均 ${questions.fixed_avg_questions ?? "-"} 个问题；Adaptive Agent 平均 ${questions.adaptive_avg_questions ?? "-"} 个问题；减少 ${toPercent(questions.reduction_rate)}。</p>
      </article>
      <article>
        <h3>⑤ 延迟与 Token 成本</h3>
        <p>当前架构平均 ${Math.round(cost.agent_avg_latency_ms || 0)}ms、${Math.round(cost.agent_avg_token_total || 0)} token；LLM-heavy 基线估算 ${Math.round(cost.llm_heavy_baseline_latency_ms || 0)}ms、${Math.round(cost.llm_heavy_baseline_token_total || 0)} token。</p>
      </article>
      <article>
        <h3>⑥ Agent 能力 Benchmark</h3>
        <p>${escapeHtml(capability.architecture_type || "-")}；${capability.true_subagents ? "Supervisor 动态 WorkerAgent" : "显式编排多 Agent Workflow"}。工具覆盖 ${toPercent(capability.tool_call_coverage_rate)}，平均 ${capability.avg_workflow_nodes ?? "-"} 个节点，HITL 记忆门控 ${toPercent(capability.hitl_memory_gate_rate)}，fallback 恢复 ${toPercent(capability.fallback_recovery_rate)}。</p>
      </article>
    </div>
    <p class="eval-note">${escapeHtml(capability.benchmark_note || "")}</p>
    <div class="eval-methodology">
      <h3>评估口径</h3>
      <p><b>实测：</b>${escapeHtml((methodology.measured_metrics || []).join("、") || "-")}</p>
      <p><b>人工标注：</b>${escapeHtml((methodology.annotated_metrics || []).join("、") || "-")}</p>
      <p><b>估算：</b>${escapeHtml((methodology.estimated_metrics || []).join("、") || "-")}</p>
      <p>${escapeHtml(methodology.caution || "")}</p>
    </div>
    <div class="eval-methodology">
      <h3>数据集校验</h3>
      <p><b>独立实验集：</b>${validation.independent_cases || 0} 条；唯一 Query：${validation.unique_queries || 0}/${validation.total_cases || 0}；重复：${validation.duplicate_queries || 0}。</p>
      <p><b>校验状态：</b>${validation.validation_passed ? "通过" : "需要检查"}</p>
      <p>${escapeHtml((validation.notes || []).join(" "))}</p>
    </div>
    <div class="quality-table">
      <h3>Split 结果</h3>
      <table>
        <thead><tr><th>数据集</th><th>总数</th><th>可推荐</th><th>Top-1</th><th>Top-3</th><th>硬约束</th><th>状态</th><th>问题数</th></tr></thead>
        <tbody>${splitRows}</tbody>
      </table>
    </div>
    <div class="quality-table">
      <h3>类别覆盖结果</h3>
      <table>
        <thead><tr><th>类别</th><th>总数</th><th>Top-1</th><th>Top-3</th><th>硬约束</th><th>状态</th><th>问题数</th></tr></thead>
        <tbody>${categoryRows}</tbody>
      </table>
    </div>
    <div class="quality-table">
      <h3>④ 推荐质量人工评分</h3>
      <table>
        <thead><tr><th>方案</th><th>相关性</th><th>个性化</th><th>解释质量</th></tr></thead>
        <tbody>
          ${qualityRow("Agent", quality.agent)}
          ${qualityRow("热门 Baseline", quality.popularity)}
          ${qualityRow("低单价 Baseline", quality.value)}
        </tbody>
      </table>
    </div>
  `;

  els.evalRows.innerHTML = data.cases
    .map((item) => {
      const popularity = item.baseline_results.find((baseline) => baseline.strategy === "popularity");
      const value = item.baseline_results.find((baseline) => baseline.strategy === "value");
      return `
        <tr>
          <td>${escapeHtml(item.case_id)}</td>
          <td>${escapeHtml(item.actual_scenario || "-")}<br><span class="${item.scenario_match ? "pass" : "fail"}">${item.scenario_match ? "match" : "mismatch"}</span><br><span>${escapeHtml(item.split || "regression")} · ${escapeHtml(item.category || "-")}</span></td>
          <td><strong>${escapeHtml(item.agent_top_product || item.actual_status || "-")}</strong><br><span class="${item.status_match ? "pass" : "fail"}">状态 ${item.status_match ? "match" : "mismatch"}</span><br><span class="${item.top1_correct ? "pass" : "fail"}">Top1 ${item.top1_correct ? "hit" : "miss"}</span><br><span>Top3 ${item.top3_hit ? "hit" : "miss"}</span></td>
          <td>${escapeHtml(popularity?.top_product_name || "-")}</td>
          <td>${escapeHtml(value?.top_product_name || "-")}</td>
          <td>${item.constraint_satisfied ? "满足" : "不满足"}</td>
          <td>${item.question_count}/${item.fixed_question_count}</td>
          <td>${item.workflow_node_count || "-"} 节点<br>工具 ${toPercent(item.tool_coverage_rate)}<br>HITL ${item.hitl_memory_gate_passed ? "pass" : "fail"}</td>
        </tr>
      `;
    })
    .join("");
}

function toPercent(value) {
  return `${Math.round((value || 0) * 100)}%`;
}

function formatScore(value) {
  return Number.isFinite(Number(value)) ? Number(value).toFixed(1) : "-";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

document.querySelectorAll("[data-query]").forEach((button) => {
  button.addEventListener("click", () => {
    els.query.value = button.dataset.query;
  });
});

els.start.addEventListener("click", startDecision);
els.answer.addEventListener("click", answerQuestions);
els.runEval.addEventListener("click", runEvaluation);
els.refreshMarket.addEventListener("click", () => {
  if (state.externalDiscovery?.selected_source) {
    refreshMarket(true);
    return;
  }
  if (state.externalDiscovery?.search_url) {
    window.open(state.externalDiscovery.search_url, "_blank", "noopener,noreferrer");
    return;
  }
  const config = marketConfigForCurrentRoute();
  if (!config.supportsSnapshot) {
    window.open(config.url, "_blank", "noopener,noreferrer");
    return;
  }
  refreshMarket(true);
});
checkHealth();
loadSavedMemories();
renderMarket(null);
