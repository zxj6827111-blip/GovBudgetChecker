# GovBudgetChecker v2 截图式 DEMO

本轮重新设计保留“只做 DEMO、不改正式业务页面”的边界，并补上上一版缺失的业务逻辑：

- 新增 `region-management.html`，把行政区划、部门、单位、公开站点、材料归属规则作为独立管理入口。
- 预算公开使用蓝色工作线，决算公开使用青绿色工作线，`类型待确认` 使用橙色提示。
- 工作台、上传、部门台账、审校详情都先显示当前地区和组织范围，再进入预算/决算任务。
- 审校详情页用三栏结构固定为“问题队列 / 证据与建议 / 原文定位”，避免每栏用途不清。
- `index.html` 只是 DEMO 导航，不是统一门户首页。

页面清单：

1. `login.html`：GovBudgetChecker 自身登录页。
2. `dashboard.html`：今天优先处理什么。
3. `upload.html`：批量上传与预算/决算类型确认。
4. `region-management.html`：地区与组织管理。
5. `department.html`：部门任务台账。
6. `review.html`：审校详情页。
