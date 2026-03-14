import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{js,ts,jsx,tsx}", "./components/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        primary: {
          50: "#eef2ff",
          100: "#e0e7ff",
          300: "#a5b4fc",
          400: "#818cf8",
          500: "#6366f1",
          600: "#4f46e5",
          700: "#4338ca",
        },
        success: {
          50: "#ecfdf5",
          100: "#d1fae5",
          200: "#a7f3d0",
          500: "#10b981",
          600: "#059669",
          700: "#047857",
        },
        danger: {
          50: "#fff1f2",
          100: "#ffe4e6",
          200: "#fecdd3",
          500: "#f43f5e",
          600: "#e11d48",
          700: "#be123c",
        },
        warning: {
          50: "#fffbeb",
          100: "#fef3c7",
          200: "#fde68a",
          300: "#fcd34d",
          500: "#f59e0b",
          600: "#d97706",
          700: "#b45309",
        },
        surface: {
          50: "#f8fafc",
          100: "#f1f5f9",
        },
        border: "#e2e8f0",
      },
      boxShadow: {
        'soft': '0 4px 20px -2px rgba(0, 0, 0, 0.05)',
        'float': '0 10px 40px -10px rgba(0, 0, 0, 0.08)',
      }
    },
  },
  plugins: [],
};

export default config;
