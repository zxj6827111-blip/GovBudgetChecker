# GovBudgetChecker 五页截图式 UI DEMO

这组文件是评审用静态原型，不接入真实接口，不影响现有业务页面。

## 页面

- `login.html`：登录页
- `dashboard.html`：工作台
- `upload.html`：批量上传
- `department.html`：部门页
- `review.html`：审校详情页

## 设计重点

- 不做统一门户首页，只展示 GovBudgetChecker 自身的预决算审校入口。
- 预算和决算作为一等业务类型贯穿页面：预算使用蓝色标签，决算使用青绿色标签，类型待确认使用橙色标签。
- 视觉参考 KIROGOVCOMPARE 的浅色政务后台：白色顶栏、浅灰背景、紧凑表格、细边框、低阴影、6-8px 圆角。
- DEMO 用静态示例数据表达信息架构，后续确认后再迁移到 Next.js 正式页面。

## 查看方式

直接在浏览器打开任一 HTML 文件即可，例如：

```text
docs/ui-demo/dashboard.html
```

截图输出建议放在：

```text
docs/ui-demo/screenshots/
```
