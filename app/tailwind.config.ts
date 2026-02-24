import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{js,ts,jsx,tsx}", "./components/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        theme: {
          bg: "#f3f5f8", // Light grayish-blue background
          surface: "#ffffff",
          board: "#f8f9fc", // Slightly darker than white for panels
          text: "#1e293b",
          subtext: "#64748b",
          border: "#e2e8f0",
          primary: "#4f46e5", // Indigo base
          primary_hover: "#4338ca",
          success: "#10b981", // Green
          warning: "#f59e0b", // Yellow/Orange
          danger: "#ef4444", // Red
          info: "#3b82f6", // Blue
        }
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
