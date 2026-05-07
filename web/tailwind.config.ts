import type { Config } from "tailwindcss";

// 主题令牌通过 CSS 变量喂给 Tailwind，与 graph.html 的 [data-theme] 命名对齐
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        bg: "hsl(var(--bg))",
        "bg-soft": "hsl(var(--bg-soft))",
        panel: "hsl(var(--panel))",
        "panel-soft": "hsl(var(--panel-soft))",
        muted: "hsl(var(--muted))",
        border: "hsl(var(--border))",
        "border-strong": "hsl(var(--border-strong))",
        text: "hsl(var(--text))",
        "text-soft": "hsl(var(--text-soft))",
        subtle: "hsl(var(--subtle))",
        accent: "hsl(var(--accent))",
        "accent-hover": "hsl(var(--accent-hover))",
        "accent-soft": "hsl(var(--accent-soft))",
        "accent-2": "hsl(var(--accent-2))",
        "accent-3": "hsl(var(--accent-3))",
        danger: "hsl(var(--danger))",
        "danger-soft": "hsl(var(--danger-soft))",
        success: "hsl(var(--success))",
        "success-soft": "hsl(var(--success-soft))",
        warning: "hsl(var(--warning))",
        "warning-soft": "hsl(var(--warning-soft))",
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 4px)",
        sm: "calc(var(--radius) - 6px)",
        xl: "var(--radius-lg)",
        "2xl": "calc(var(--radius-lg) + 4px)",
      },
      boxShadow: {
        soft: "var(--shadow-soft)",
        DEFAULT: "var(--shadow-soft)",
        md: "var(--shadow-md)",
        lg: "var(--shadow-lg)",
        glow: "0 0 0 1px hsl(var(--accent) / 0.15), 0 8px 32px -8px hsl(var(--accent) / 0.40)",
        ring: "0 0 0 4px hsl(var(--ring))",
      },
      backgroundImage: {
        "gradient-brand": "var(--gradient-brand)",
        "gradient-brand-soft": "var(--gradient-brand-soft)",
      },
      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "fade-up": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "scale-in": {
          from: { opacity: "0", transform: "scale(0.97)" },
          to: { opacity: "1", transform: "scale(1)" },
        },
        "slide-in-right": {
          from: { opacity: "0", transform: "translateX(12px)" },
          to: { opacity: "1", transform: "translateX(0)" },
        },
      },
      animation: {
        "fade-in": "fade-in 200ms ease-out both",
        "fade-up": "fade-up 220ms ease-out both",
        "scale-in": "scale-in 180ms cubic-bezier(.2,.8,.2,1) both",
        "slide-in-right": "slide-in-right 220ms ease-out both",
      },
      fontFamily: {
        sans: [
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Inter",
          "Roboto",
          "PingFang SC",
          "Microsoft YaHei",
          "sans-serif",
        ],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "JetBrains Mono",
          "Menlo",
          "Monaco",
          "Consolas",
          "monospace",
        ],
      },
      transitionTimingFunction: {
        "out-soft": "cubic-bezier(.2,.8,.2,1)",
      },
    },
  },
  plugins: [],
} satisfies Config;
