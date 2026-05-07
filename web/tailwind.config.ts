import type { Config } from "tailwindcss";

// 主题令牌通过 CSS 变量喂给 Tailwind，与 graph.html 的 [data-theme] 命名对齐
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        bg: "hsl(var(--bg))",
        panel: "hsl(var(--panel))",
        muted: "hsl(var(--muted))",
        border: "hsl(var(--border))",
        text: "hsl(var(--text))",
        subtle: "hsl(var(--subtle))",
        accent: "hsl(var(--accent))",
        "accent-hover": "hsl(var(--accent-hover))",
        danger: "hsl(var(--danger))",
        "danger-soft": "hsl(var(--danger-soft))",
        success: "hsl(var(--success))",
        warning: "hsl(var(--warning))",
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      fontFamily: {
        sans: [
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "PingFang SC",
          "Microsoft YaHei",
          "sans-serif",
        ],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Monaco",
          "Consolas",
          "monospace",
        ],
      },
    },
  },
  plugins: [],
} satisfies Config;
