# 更新日志

本文档记录了政府预决算检查系统的所有重要变更和新功能。

## [2.0.0] - 2024-01-15

### 🎉 重大更新 - 双模式分析功能

#### ✨ 新增功能

##### 双模式分析引擎
- **AI智能分析**: 集成大语言模型进行智能文档分析
- **规则引擎检查**: 保留并优化传统规则检查功能
- **智能结果合并**: 自动合并AI和规则检测结果
- **冲突检测**: 识别和处理两种检测方式的分歧
- **一致性验证**: 验证不同检测方式的结果一致性

##### 后端API增强
- 新增 `/analyze` 端点支持多种分析模式 (`dual`, `ai`, `local`)
- 新增 `/jobs/{job_id}/status` 异步作业状态查询
- 新增 `/jobs/{job_id}/result` 作业结果获取
- 新增 `/config` 系统配置查询
- 优化错误处理和响应格式

##### 前端界面重构
- **多视图支持**: 标签页、列表、卡片三种结果展示方式
- **IssueTabs组件**: 分别展示AI和规则检测结果
- **IssueList组件**: 统一列表展示合并结果
- **IssueCard组件**: 详细展示单个问题信息
- **冲突可视化**: 直观展示检测结果冲突和一致性
- **实时进度**: 可视化显示分析进度和状态

##### 数据结构优化
- 扩展 `Issue` 数据结构支持多源信息
- 新增 `DualModeResult` 双模式结果格式
- 新增 `Conflict` 和 `Agreement` 冲突一致性数据结构
- 优化证据 (`Evidence`) 和位置 (`Location`) 信息

#### 🔧 技术改进

##### 服务架构
- **DualModeAnalyzer**: 双模式分析协调器
- **AIFindingsService**: AI检测结果处理服务
- **RuleFindingsService**: 规则检测结果处理服务
- **MergeFindingsService**: 结果合并处理服务
- **OCRProcessor**: 优化文档OCR处理

##### 异步处理
- 支持大文件异步分析处理
- 实现作业队列和状态管理
- 添加处理超时和错误恢复机制

##### 缓存优化
- 文档分析结果缓存
- AI模型响应缓存
- 规则检查结果缓存

#### 🧪 测试覆盖

##### 新增测试文件
- `tests/test_dual_mode.py`: 双模式分析功能测试
- `tests/test_api_integration.py`: API集成测试
- `tests/test_frontend_components.py`: 前端组件测试
- `tests/conftest.py`: 测试配置和fixtures
- `smoke_test.py`: 冒烟测试脚本

##### 测试配置
- `pytest.ini`: pytest配置文件
- `requirements-test.txt`: 测试依赖管理
- 覆盖率报告和性能测试

#### 📚 文档完善

##### 新增文档
- `README.md`: 项目概述和使用指南
- `docs/API_DOCUMENTATION.md`: 详细API文档
- `docs/DUAL_MODE_GUIDE.md`: 双模式功能使用指南
- `CHANGELOG.md`: 版本更新日志

##### 文档内容
- 完整的安装部署指南
- 详细的API接口说明
- 双模式功能使用最佳实践
- 故障排除和性能优化建议

#### 🔒 安全增强
- 文件上传安全验证
- API请求频率限制
- 输入数据清理和验证
- 错误信息安全处理

#### ⚡ 性能优化
- 并行执行AI和规则分析
- 优化前端组件渲染性能
- 减少不必要的API调用
- 智能缓存策略

### 🐛 问题修复
- 修复PDF文档解析中的编码问题
- 修复大文件上传超时问题
- 修复前端状态管理的内存泄漏
- 修复规则引擎的边界条件处理

### 🔄 重大变更
- API响应格式调整，支持双模式结果结构
- 前端组件架构重构，采用模块化设计
- 数据库结构更新，支持多源检测结果
- 配置文件格式变更，增加AI服务配置

### 📦 依赖更新
- 升级FastAPI到最新版本
- 添加AI服务相关依赖
- 更新前端React和TypeScript版本
- 优化Python和Node.js依赖管理

### 🚀 部署变更
- 新增AI服务部署要求
- 更新环境变量配置
- 添加服务健康检查
- 优化Docker容器配置

---

## [1.0.0] - 2023-12-01

### 🎉 初始版本发布

#### ✨ 核心功能
- **PDF文档解析**: 支持复杂格式的预决算PDF文档处理
- **规则检查引擎**: 基于预定义规则的文档检查功能
- **Web界面**: 基于React的现代化用户界面
- **文件上传**: 支持拖拽上传和文件选择
- **结果展示**: 清晰的问题列表和详情展示

#### 🔧 技术栈
- **后端**: Python + FastAPI
- **前端**: React + TypeScript + Next.js
- **文档处理**: PyPDF2 + OCR
- **数据库**: SQLite（开发环境）

#### 📋 检查规则
- 预算科目格式检查
- 金额数值范围验证
- 表格结构完整性检查
- 必填字段缺失检测
- 计算公式准确性验证

#### 🎨 用户界面
- 响应式设计，支持桌面和移动设备
- 直观的文件上传界面
- 清晰的检查结果展示
- 问题分类和严重程度标识

#### 🧪 基础测试
- 单元测试覆盖核心功能
- 集成测试验证API端点
- 手动测试确保用户体验

---

## 版本规划

### [2.1.0] - 计划中
- **批量处理**: 支持多文件批量分析
- **报告导出**: PDF和Excel格式的检查报告
- **规则编辑器**: 可视化规则配置界面
- **用户管理**: 多用户和权限管理系统

### [2.2.0] - 计划中
- **API认证**: JWT令牌认证机制
- **数据统计**: 检查结果统计和趋势分析
- **通知系统**: 邮件和消息通知功能
- **审计日志**: 完整的操作审计记录

### [3.0.0] - 远期规划
- **机器学习**: 基于历史数据的智能优化
- **微服务架构**: 服务拆分和容器化部署
- **实时协作**: 多用户实时协作功能
- **移动应用**: 原生移动应用支持

---

## 贡献指南

### 版本号规范
本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)规范：
- **主版本号**: 不兼容的API修改
- **次版本号**: 向下兼容的功能性新增
- **修订号**: 向下兼容的问题修正

### 更新日志格式
- 🎉 重大更新
- ✨ 新增功能
- 🔧 技术改进
- 🐛 问题修复
- 🔄 重大变更
- 📚 文档更新
- 🧪 测试相关
- 🔒 安全相关
- ⚡ 性能优化
- 📦 依赖更新
- 🚀 部署相关

### 提交信息规范
```
<类型>(<范围>): <描述>

[可选的正文]

[可选的脚注]
```

类型包括：
- `feat`: 新功能
- `fix`: 问题修复
- `docs`: 文档更新
- `style`: 代码格式调整
- `refactor`: 代码重构
- `test`: 测试相关
- `chore`: 构建过程或辅助工具的变动

---

**注意**: 本更新日志将持续更新，记录系统的所有重要变更。如有疑问，请查看对应版本的详细文档或联系开发团队。

## [Unreleased] - 2026-02-23

### Refactor
- Removed legacy compatibility shim packages: `services/`, `engine/`, `providers/`, `schemas/`.
- Standardized backend imports on `src.*` only.
- Updated type-check command in `Makefile` to `mypy api src tests`.

### Tests
- Replaced shim-compat test with import policy guard:
  - `tests/test_import_path_policy.py`
  - Ensures no legacy import roots are reintroduced.

### Docs
- Updated migration notes in `docs/SRC_LAYOUT_MIGRATION.md` for Phase 6 cleanup status.
- Updated README import-path convention section to reflect shim removal.
