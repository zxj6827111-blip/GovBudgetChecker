import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{js,ts,jsx,tsx}", "./components/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        // primary（主色·墨绿/深青）：取自原型图主按钮、链接、进度条填充、
        // 环形图最大分段（600 档 #087f75 为像素采样实测值，见
        // docs/UI_COLOR_TOKEN_MAPPING.md 的取色记录）。
        // 50-300 只做浅底装饰，不承载文字；600 及以上档位在白底/浅底上文字对比度均 ≥4.5:1。
        primary: {
          50: "#f2fbf9",
          100: "#dff2ee",
          200: "#b3ded4",
          300: "#7fc4b6",
          400: "#3fa294",
          500: "#0c8c7f",
          600: "#087f75",
          700: "#0a6b62",
          800: "#0d5650",
          900: "#0f423e",
        },
        // success（成功/门禁通过）：取自原型图服务状态点与质量门禁「通过」图标底色
        // （700 档 #19764b 为实测值）。
        success: {
          50: "#eaf7ef",
          100: "#e7f5ed",
          200: "#bfe6cf",
          300: "#8fd1ab",
          400: "#4fae7c",
          500: "#268f5c",
          600: "#1f7f52",
          700: "#19764b",
          800: "#155f3d",
          900: "#124a30",
        },
        // danger（危险/处理失败）：取自原型图环形图「AI 分析」分段与告警卡左侧红色竖条
        // （600 档 #b5332d 为实测值）。
        danger: {
          50: "#fdf2f1",
          100: "#fdecea",
          200: "#f8cdc9",
          300: "#ec9d96",
          400: "#cf5f57",
          500: "#bd4038",
          600: "#b5332d",
          700: "#962a25",
          800: "#78221e",
          900: "#5c1a17",
        },
        // warning（警告/需要确认/发布门禁未通过）：取自原型图环形图「元数据识别」分段、
        // 质量管理页门禁未通过行、审核工作台高风险徽章浅底（700 档 #a65f00 为实测值）。
        // 注意：warning-700 文字置于 warning-100（#fff3d9，原型图实测底色）上的对比度为
        // 4.48:1，略低于 4.5:1 常规文字门槛；原型图中该组合仅用于加粗徽章标签
        // （≥14px 加粗，接近但不严格满足 WCAG"大字号"豁免档）。为保持与原型图 1:1
        // 视觉还原不擅自加深实测色值，已在无障碍测试中记录该已知限制，建议后续如需
        // 更严格合规可切换到 warning-800 文字（同一 warning-100 浅底下对比度 6.42:1，
        // 见 docs/UI_COLOR_TOKEN_MAPPING.md 第三节独立核算）。
        warning: {
          50: "#fffaf0",
          100: "#fff3d9",
          200: "#ffe1a3",
          300: "#ffc44a",
          400: "#e5ad21",
          500: "#c98800",
          600: "#b57200",
          700: "#a65f00",
          800: "#834b00",
          900: "#603700",
        },
        // info（信息/PDF 解析阶段/次要指标）：原型图新增色系，取自环形图「PDF 解析」分段与
        // 质量管理页第二条指标条（600 档 #1769aa 为实测值）。现有代码库未使用过 info-*，
        // 本次为 Task 4/6/7 消费阶段状态预留。
        info: {
          50: "#eef6fb",
          100: "#dcecf6",
          200: "#b0d5ea",
          300: "#7ab8db",
          400: "#3f93c2",
          500: "#1c7cae",
          600: "#1769aa",
          700: "#125485",
          800: "#0d4066",
          900: "#092c47",
        },
        // brand（品牌深色·logo 色块）：原型图 Logo 色块用的是独立的深藏青，
        // 不是 primary 的加深版（像素采样确认二者色相不同：primary 是墨绿，
        // brand 是深藏青），单独建一档避免被误认成 primary-900。
        brand: {
          900: "#19394d",
        },
        // neutral-chart：环形图「质量门禁」分段与部分中性装饰用色。
        // 对比度极低（约 1.78:1，见 docs/UI_COLOR_TOKEN_MAPPING.md），
        // 只允许用于图表分段填充等纯装饰场景，禁止承载文字或需要辨识的图标。
        "neutral-chart": {
          400: "#b9c4c9",
        },
        surface: {
          50: "#fafcfc",
          100: "#f7f9fa",
        },
        border: "#e2e8f0",
      },
      borderRadius: {
        card: "0.75rem",
      },
      boxShadow: {
        soft: "0 4px 20px -2px rgba(0, 0, 0, 0.05)",
        float: "0 10px 40px -10px rgba(0, 0, 0, 0.08)",
        // 全屏 overlay 对话框（如结构化清理）的重投影，收敛自组件内硬编码阴影。
        dialog: "0 30px 80px rgba(15, 23, 42, 0.28)",
      },
      backgroundImage: {
        // 结构化清理对话框头部柔光：左上 info 色系径向光晕 + 白→info-50 斜向渐变。
        // 收敛自原 sky 系硬编码 rgba(14,165,233,0.18) 与 #eef6ff（#eef6ff 与
        // info-50 #eef6fb 肉眼不可辨，径向光晕按 info-500 加同透明度折算）。
        "dialog-header-wash":
          "radial-gradient(circle at top left, rgba(28, 124, 174, 0.18), transparent 38%), linear-gradient(135deg, #ffffff, #eef6fb)",
      },
      fontSize: {
        // 字号阶梯：对照原型图 KPI 数值(28px)/卡片标题(15-16px)/正文(13-14px)/
        // 辅助说明(12px) 四级。现有 Tailwind 默认字号已覆盖大部分场景，
        // 这里只补齐原型图实测但默认刻度未精确覆盖的两档。
        "kpi-value": ["1.75rem", { lineHeight: "2.25rem", fontWeight: "700" }],
        caption: ["0.75rem", { lineHeight: "1rem" }],
      },
    },
  },
  plugins: [],
};

export default config;
