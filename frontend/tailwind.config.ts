import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        primary: {
          DEFAULT: "#1677ff",
          50: "#f0f5ff",
          100: "#d6e4ff",
          200: "#adc8ff",
          300: "#85a5ff",
          400: "#597ef7",
          500: "#1677ff",
          600: "#0958d9",
          700: "#003eb3",
          800: "#002c8c",
          900: "#001d66",
        },
        success: {
          DEFAULT: "#52c41a",
          50: "#f6ffed",
          100: "#d9f7be",
          200: "#b7eb8f",
          300: "#95de64",
          400: "#73d13d",
          500: "#52c41a",
          600: "#389e0d",
          700: "#237804",
          800: "#135200",
          900: "#092b00",
        },
        warning: {
          DEFAULT: "#faad14",
          50: "#fffbe6",
          100: "#fff1b8",
          200: "#ffe58f",
          300: "#ffd666",
          400: "#ffc53d",
          500: "#faad14",
          600: "#d48806",
          700: "#ad6800",
          800: "#874d00",
          900: "#613400",
        },
        error: {
          DEFAULT: "#ff4d4f",
          50: "#fff2f0",
          100: "#fff1f0",
          200: "#ffccc7",
          300: "#ffa39e",
          400: "#ff7875",
          500: "#ff4d4f",
          600: "#f5222d",
          700: "#cf1322",
          800: "#a8071a",
          900: "#820014",
        },
        surface: {
          DEFAULT: "#ffffff",
          dark: "#1f1f1f",
        },
      },
      backgroundColor: {
        DEFAULT: "#f5f5f5",
        dark: "#141414",
      },
      borderRadius: {
        sm: "4px",
        md: "8px",
        lg: "12px",
      },
      boxShadow: {
        sm: "0 1px 2px rgba(0,0,0,0.06)",
        lg: "0 8px 24px rgba(0,0,0,0.12)",
      },
      fontFamily: {
        sans: ["Inter", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
      },
      spacing: {
        unit: "4px",
      },
    },
  },
  plugins: [],
};

export default config;
