# 商品数据源策略

## 1. 当前结论

本项目主线是个性化消费决策，不是爬虫系统。真实商品数据需要支持大陆消费场景，但不能让项目依赖不稳定、违规或高成本的数据链路。

因此数据源采用分层策略：

```text
第一层：本地真实品牌调研种子库
第二层：公开榜单/行业页 Provider
第三层：第三方商品 API Provider
第四层：平台授权 API Provider
```

Agent 只依赖标准化后的 `ProductOffer`，不直接感知淘宝、京东、小红书或某个网页结构。

## 2. 第一版数据

当前猫粮数据已经从虚构品牌替换为大陆市场常见品牌参考样例，包括：

- 麦富迪
- 蓝氏
- 网易严选
- 皇家
- 鲜朗
- 京东京造
- 凯锐思

这些数据标记为：

```text
source_type = seed_market_research
```

含义：

> 字段结构和商品形态参考公开榜单与行业信息整理，用于稳定演示和算法验证，不代表实时价格、实时销量或实时库存。

## 3. 可利用的数据来源

### 3.1 京东榜单/商品页

可获得的信息：

- 商品标题
- 品牌
- 规格
- 当前展示价格，部分页面可见
- 评论量
- 好评率，部分页面可见
- 榜单分/金榜分，部分页面可见
- 用户评价摘要

适合做：

- 热门候选商品召回
- 市场热度特征
- 商品名称和卖点提取

限制：

- 页面结构可能变化。
- 价格和优惠会变化。
- 不应把网页全文直接塞给 LLM。

### 3.2 宠物行业数据页

例如宠物产品洞察类页面。

可获得的信息：

- 品牌
- 类目
- 卖点
- 月成交件数区间
- 商品成交价格区间
- 500g 标价
- 更新时间
- 生产商

适合做：

- 种子数据构建
- 行业热度参考
- 商品价格区间参考

限制：

- 是否允许程序化调用需要确认。
- 数据未必实时。

### 3.3 第三方商品 API

可获得的信息取决于 API：

- 商品详情
- SKU
- 店铺
- 价格
- 月销量
- 评论关键词
- 优惠券

适合做：

- 后续增强版真实商品数据

限制：

- 通常需要 key。
- 可能按次收费。
- 需要控制调用频率和缓存。

### 3.4 平台授权 API

淘宝、京东等平台官方 API 可以作为长期扩展方向。

限制：

- 申请和权限复杂。
- 很多接口偏商家或联盟生态。
- 不适合作为第一版主链路。

## 4. Provider 设计

当前代码已有：

```text
ProductSearchProvider
  ├── LocalProductProvider
  ├── MarketResearchProvider
  └── JDTopListProvider
```

当前启用链路：

```text
ProductSearchService
  → JDTopListProvider(disabled)
  → MarketResearchProvider
  → LocalProductProvider
```

后续启用真实网络查询时：

```text
JDTopListProvider
  → fetch public page
  → parse product cards
  → normalize to ProductOffer
  → cache result
  → return compact structured products
```

## 5. Token 控制原则

不允许：

- 把整页 HTML 直接发给 LLM。
- 每次用户请求都让 LLM 做全网搜索。
- 让 LLM 自己判断实时价格是否可信。

应该：

- 规则先判断领域。
- Provider 先获取数据。
- 代码解析出结构化字段。
- Redis/本地缓存保存结果。
- 只把 Top K 商品摘要交给 LLM。
- 排序分数用代码计算。
- LLM 只负责动态问题、解释和表达适配。

推荐流程：

```text
用户请求
  → 规则领域判断
  → 查缓存
  → 缓存未命中再调用 Provider
  → Provider 结构化商品数据
  → 代码排序预处理
  → LLM 生成问题/解释
```

## 6. 长期记忆和跨品类使用

长期记忆分三类：

```text
Global Memory
Domain Memory
Sensitive / Temporary Memory
```

### 6.1 Global Memory

跨品类可用：

- 喜欢表格
- 喜欢先看结论
- 预算敏感
- 风险敏感
- 喜欢短句或详细解释
- 偏好温和/直接/鼓励型语气

例如用户以后问手机，系统可以参考“喜欢表格”和“预算敏感”。

### 6.2 Domain Memory

只在对应领域使用：

- 猫粮：猫容易软便、挑食、年龄阶段。
- 保健品：过敏、服药、健康目标。
- 手机：看重续航、拍照、游戏性能。

猫粮里的“软便”不能影响手机推荐。

### 6.3 Sensitive / Temporary Memory

健康、用药、疾病等信息默认不长期保存，除非用户明确确认。保健品场景尤其需要保守处理。

## 7. 后续实现顺序

1. 扩充本地真实品牌种子库到每个领域 30-50 条。
2. 实现缓存层，避免重复解析和重复 LLM 调用。
3. 启用 `JDTopListProvider` 的页面获取和结构化解析。
4. 增加 Provider 数据质量评分。
5. 增加商品去重和品牌/型号归一化。
6. 接入 LLM 做动态问题生成和表达适配。
