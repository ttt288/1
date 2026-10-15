# Adaptive Personalized Consumer Decision Agent

自适应个性化消费决策 Agent。项目面向高复杂度消费选择场景，通过领域配置、动态问题生成、用户画像、风险规则和个性化排序，为用户生成可解释的推荐结果。

第一版支持两个 Demo Domain：

- 宠物食品
- 保健品

项目不做登录注册，默认使用 `demo_user`。第一版也不依赖淘宝、京东、小红书等实时数据源，先使用本地种子数据跑通完整决策链路，后续通过 Data Provider 扩展真实 API。

## 当前进度

已完成：

- 项目设计文档
- 开发执行计划
- 后端 FastAPI 骨架
- Pydantic Schema
- 宠物食品和保健品 Domain Config
- ProductSearchProvider 抽象
- 本地/市场调研 Product Provider
- 规则版领域识别、动态问题生成、画像构建、风险分析、个性化排序
- TraceEvent 记录
- 核心流程测试用例

## 本地运行

安装依赖：

```bash
cd backend
python3 -m pip install -r requirements.txt
```

启动后端：

```bash
cd backend
PYTHONPATH=. uvicorn app.main:app --reload --port 8000
```

查看工作台：

```text
http://127.0.0.1:8000/
```

查看 API 文档：

```text
http://127.0.0.1:8000/docs
```

健康检查：

```bash
curl http://localhost:8000/api/health
```

## 启用 AI 模式

默认是规则模式，不会调用 OpenAI API，也不会产生 API 成本。

需要启用 AI 时设置环境变量：

```bash
export OPENAI_API_KEY="your_api_key_here"
export LLM_ENABLED=true
export OPENAI_MODEL=gpt-5-nano
export LLM_TIMEOUT_SECONDS=6
export LLM_QUERY_UNDERSTANDING_ENABLED=false
```

然后启动服务：

```bash
cd backend
PYTHONPATH=. uvicorn app.main:app --port 8000
```

当前 AI 模式只用于：

- 更自然地结构化理解用户输入。
- 生成更贴合语气偏好的推荐摘要。

排序、风险边界、价格计算和硬约束仍由代码控制。

开始一次决策：

```bash
curl -X POST http://localhost:8000/api/decision/start \
  -H "Content-Type: application/json" \
  -d '{"query":"我想买猫粮，不知道哪个适合我","user_id":"demo_user"}'
```

## 文档

- [项目设计文档](docs/project-design.md)
- [开发执行计划](docs/development-plan.md)
- [商品数据源策略](docs/data-source-strategy.md)

## Render 部署

仓库包含 `render.yaml`，可以通过 Render Blueprint 直接部署。

1. 在 Render 新建 Blueprint 并连接此 GitHub 仓库。
2. 部署时为 `OPENAI_API_KEY` 填写 Secret，不要把真实 Key 提交到仓库。
3. Render 会执行以下启动命令：

```bash
PYTHONPATH=backend python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

免费实例闲置后会休眠，并且运行时写入的会话和记忆文件不会永久保存；源码内置的商品、知识与评测数据不受影响。
