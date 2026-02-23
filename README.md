# 政府预决算检查系统（AI增强版）

## 项目简介

政府预决算检查系统是一个智能化的预决算文档分析工具，支持传统规则检查和AI智能分析的双模式检测，帮助提高预决算审查的效率和准确性。

## 🚀 核心特性

### 双模式分析引擎
- **AI智能分析**：基于大语言模型的智能问题检测，支持多种AI提供商
- **规则引擎检查**：基于预定义规则的传统检测（V3.3规则集）
- **双模式融合**：同时运行两种模式并智能合并结果
- **冲突检测**：自动识别和处理两种模式间的分歧
- **一致性验证**：验证不同检测方式的结果一致性

### 多AI提供商支持
- **OpenAI兼容接口**：支持OpenAI API格式的各种服务
- **智谱AI (GLM)**：支持智谱清言系列模型
- **豆包AI (字节跳动)**：支持豆包系列模型
- **自动故障转移**：AI服务异常时自动切换到规则引擎
- **负载均衡**：支持多提供商间的智能调度

### 智能文档处理
- **PDF文档解析**：支持复杂格式的预决算PDF文档
- **OCR文本提取**：智能识别表格、图表中的关键信息
- **结构化数据提取**：自动识别预算科目、金额、比例等关键数据
- **九表检查**：专门针对政府预决算九表的格式验证

### 现代化用户界面
- **响应式设计**：支持桌面和移动设备
- **实时分析进度**：可视化显示分析进度和状态
- **多视图展示**：标签页、列表、卡片多种结果展示方式
- **交互式结果**：支持问题筛选、搜索、排序等操作
- **冲突可视化**：直观展示AI和规则检测的差异

## 🏗️ 系统架构

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   前端界面      │    │   后端API       │    │   AI服务        │
│   (Next.js)     │◄──►│   (FastAPI)     │◄──►│   (LLM)         │
└─────────────────┘    └─────────────────┘    └─────────────────┘
                              │
                              ▼
                       ┌─────────────────┐
                       │   双模式引擎    │
                       │                 │
                       │  ┌───────────┐  │
                       │  │ AI分析器  │  │
                       │  └───────────┘  │
                       │  ┌───────────┐  │
                       │  │ 规则引擎  │  │
                       │  └───────────┘  │
                       │  ┌───────────┐  │
                       │  │ 结果合并  │  │
                       │  └───────────┘  │
                       └─────────────────┘
```

## 📦 安装部署

### 环境要求

- Python 3.8+
- Node.js 16+
- npm 或 yarn
- Docker (可选，用于 PostgreSQL)

### 🐘 PostgreSQL 数据库设置（可选）

系统支持使用 PostgreSQL 存储分析任务和结果。如果不配置数据库，系统将使用文件系统存储。

#### 快速启动

```bash
# 1. 启动 PostgreSQL 容器（首次自动创建 fiscal_db 数据库）
docker compose up -d

# 2. 等待容器健康状态
docker compose ps

# 3. 复制环境变量模板
cp .env.example .env

# 4. 安装 Python 依赖（包含 asyncpg）
pip install -r requirements.txt

# 5. 启动后端（自动执行迁移）
python -m uvicorn api.main:app --reload --port 8000
```

#### 验证数据库

**首次启动时**，日志应显示：
```
INFO: Connecting to PostgreSQL with schema: public
INFO: Schema 'public' ready, search_path configured
INFO: Applying migration: 2026-01-14_0001_init
INFO: ✓ Migration 2026-01-14_0001_init applied successfully
INFO: ✓ Database initialization completed
```

**二次启动时**，日志应显示（不重复执行迁移）：
```
INFO: Found 1 previously applied migrations
INFO: ✓ Database is up to date, no migrations needed
```

#### 检查迁移状态

```bash
# API 端点
curl http://localhost:8000/api/migrations/status

# 直接连接数据库
docker compose exec postgres psql -U fiscal_user -d fiscal_db -c "SELECT * FROM schema_migrations;"
```

#### 未来扩展

- 修改 `.env` 中的 `PG_SCHEMA=fiscal` 可使用独立 schema
- 年报系统可共用同一 PostgreSQL 实例，使用不同数据库或 schema

### 后端部署

1. **克隆项目**
```bash
git clone <repository-url>
cd GovBudgetChecker
```

2. **安装Python依赖**
```bash
pip install -r requirements.txt
```

3. **配置环境变量**
```bash
# 复制环境变量模板
cp .env.example .env

# 编辑配置文件，支持多种AI提供商
# OpenAI兼容接口
export OPENAI_API_KEY=your_api_key
export OPENAI_BASE_URL=your_base_url
export OPENAI_MODEL=your_model

# 智谱AI
export OPENAI_API_KEY="your_zhipu_api_key"
export OPENAI_BASE_URL="https://open.bigmodel.cn/api/paas/v4"
export OPENAI_MODEL="glm-4-flash"

# 豆包AI (字节跳动)
export ARK_API_KEY=your_doubao_api_key
export ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
export ARK_MODEL="doubao-1-5-pro-32k-250115"

# 系统配置
export AI_ASSIST_ENABLED=true
export AI_EXTRACTOR_URL=http://127.0.0.1:9009/ai/extract/v1
```

4. **启动AI服务**
```bash
python3 ai_extractor_service.py
```

5. **启动后端API**
```bash
python3 -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

### 前端部署

1. **进入前端目录**
```bash
cd app
```

2. **安装依赖**
```bash
npm install
# 或
yarn install
```

3. **启动开发服务器**
```bash
npm run dev
# 或
yarn dev
```

4. **访问应用**
```
http://localhost:3000
```

## 🔧 使用指南

### 基本使用流程

1. **上传文档**：选择预决算PDF文件进行上传
2. **选择模式**：
   - **仅AI分析**：使用AI模型进行智能检测
   - **仅规则检查**：使用预定义规则进行检测
   - **双模式分析**：同时使用AI和规则进行检测
3. **查看结果**：系统将显示检测到的问题和建议
4. **结果分析**：可以切换不同视图查看详细结果

### 双模式分析详解

#### AI分析模式
- 使用大语言模型理解文档内容
- 识别复杂的语义问题和逻辑错误
- 提供智能化的问题描述和建议
- 适合处理非结构化和复杂格式的内容

#### 规则检查模式
- 基于预定义的检查规则
- 快速识别格式、数值范围等问题
- 提供标准化的问题分类和处理建议
- 适合处理结构化数据和标准格式

#### 双模式融合
- **冲突检测**：识别两种模式结果的差异
- **一致性验证**：验证相同问题的检测一致性
- **智能合并**：合并互补的检测结果
- **置信度评估**：基于多模式一致性评估结果可靠性

### API使用

#### 分析接口

```bash
# 双模式分析
curl -X POST "http://localhost:8000/analyze" \
  -F "file=@document.pdf" \
  -F "mode=dual"

# AI模式分析
curl -X POST "http://localhost:8000/analyze" \
  -F "file=@document.pdf" \
  -F "mode=ai"

# 规则模式分析
curl -X POST "http://localhost:8000/analyze" \
  -F "file=@document.pdf" \
  -F "mode=local"
```

#### 响应格式

```json
{
  "mode": "dual",
  "dual_mode": {
    "ai_findings": [
      {
        "id": "ai_001",
        "source": "ai",
        "severity": "high",
        "title": "预算执行率异常",
        "message": "预算执行率为45%，低于正常范围",
        "evidence": [...],
        "location": {...},
        "suggestions": [...]
      }
    ],
    "rule_findings": [
      {
        "id": "rule_001",
        "source": "rule",
        "rule_id": "BUDGET_EXEC_001",
        "severity": "medium",
        "title": "预算执行率偏低",
        "message": "预算执行率低于60%标准",
        "evidence": [...],
        "location": {...}
      }
    ],
    "merged": {
      "totals": {
        "ai": 1,
        "rule": 1,
        "merged": 2,
        "conflicts": 0,
        "agreements": 1
      },
      "conflicts": [],
      "agreements": [...]
    }
  }
}
```

## 🧪 测试

### 运行测试

```bash
# 安装测试依赖
pip install -r requirements-test.txt
python3 -m pip install pytest-asyncio

# 运行所有测试
python3 -m pytest

# 运行特定测试文件
python3 -m pytest tests/test_services.py -v

# 运行覆盖率测试
python3 -m pytest --cov=. --cov-report=html

# 运行冒烟测试
python3 smoke_test.py
```

### 测试分类

- **单元测试**：测试各个组件的独立功能
  - `tests/test_services.py` - 服务层基础功能测试
  - `tests/conftest.py` - 测试配置和fixtures
- **集成测试**：测试组件间的交互和API端点
  - API端点测试
  - 双模式分析流程测试
- **前端测试**：测试前端组件的结构和交互
  - React组件单元测试
  - 用户交互测试
- **冒烟测试**：快速验证系统基本功能
  - `smoke_test.py` - 系统基本功能验证

## 📊 性能优化

### 后端优化
- **异步处理**：使用异步IO提高并发性能
- **缓存机制**：缓存分析结果和模型响应
- **批处理**：支持批量文档处理
- **资源管理**：智能管理内存和计算资源

### 前端优化
- **组件懒加载**：按需加载大型组件
- **虚拟滚动**：处理大量结果数据
- **状态管理**：优化状态更新和渲染
- **缓存策略**：缓存API响应和计算结果

## 🔒 安全考虑

- **文件验证**：严格验证上传文件类型和大小
- **输入清理**：防止注入攻击和恶意输入
- **API限流**：防止API滥用和DDoS攻击
- **数据隐私**：确保敏感数据的安全处理

## 🛠️ 开发指南

### 项目结构

```
GovBudgetChecker/
├── api/                    # 后端API
│   ├── main.py            # FastAPI应用入口
│   └── ...
├── services/              # 业务服务
│   ├── analyze_dual.py    # 双模式分析服务
│   ├── ai_findings.py     # AI分析服务
│   ├── rule_findings.py   # 规则检查服务
│   └── merge_findings.py  # 结果合并服务
├── schemas/               # 数据模型
│   └── issues.py          # 问题数据结构
├── config/                # 配置管理
│   └── settings.py        # 系统配置
├── app/                   # 前端应用
│   ├── components/        # React组件
│   │   ├── IssueTabs.tsx  # 标签页组件
│   │   ├── IssueList.tsx  # 列表组件
│   │   └── IssueCard.tsx  # 卡片组件
│   └── page.tsx           # 主页面
├── tests/                 # 测试文件
│   ├── test_dual_mode.py  # 双模式测试
│   ├── test_api_integration.py # API集成测试
│   └── conftest.py        # 测试配置
└── docs/                  # 文档
```

### 添加新功能

1. **后端功能**：
   - 在`services/`目录添加业务逻辑
   - 在`schemas/`目录定义数据结构
   - 在`api/`目录添加API端点
   - 编写相应的测试用例

2. **前端功能**：
   - 在`app/components/`添加新组件
   - 更新主页面集成新功能
   - 添加相应的样式和交互逻辑

### 代码规范

- **Python**：遵循PEP 8规范，使用black格式化
- **TypeScript**：遵循ESLint规则，使用Prettier格式化
- **提交信息**：使用约定式提交格式

## 📈 监控和日志

### 日志配置
- **结构化日志**：使用JSON格式记录关键信息
- **日志级别**：支持DEBUG、INFO、WARNING、ERROR级别
- **日志轮转**：自动管理日志文件大小和数量

### 性能监控
- **响应时间**：监控API响应时间
- **资源使用**：监控CPU、内存使用情况
- **错误率**：跟踪系统错误和异常

## 🤝 贡献指南

1. Fork项目仓库
2. 创建功能分支：`git checkout -b feature/new-feature`
3. 提交更改：`git commit -am 'Add new feature'`
4. 推送分支：`git push origin feature/new-feature`
5. 创建Pull Request

## 📄 许可证

本项目采用MIT许可证 - 查看[LICENSE](LICENSE)文件了解详情。

## 🆘 支持和帮助

- **问题报告**：在GitHub Issues中报告问题
- **功能请求**：在GitHub Issues中提出功能建议
- **文档**：查看项目Wiki获取详细文档
- **联系方式**：[联系信息]

## 🔄 更新日志

### v2.0.0 (当前版本)
- ✨ 新增双模式分析功能
- ✨ 集成AI智能检测
- ✨ 重构前端界面支持多视图
- ✨ 添加冲突检测和结果合并
- 🐛 修复多个已知问题
- 📚 完善文档和测试覆盖

### v1.0.0
- 🎉 初始版本发布
- ✨ 基础规则检查功能
- ✨ PDF文档解析
- ✨ 基础Web界面

---

**注意**：本系统仍在持续开发中，如遇到问题请及时反馈。