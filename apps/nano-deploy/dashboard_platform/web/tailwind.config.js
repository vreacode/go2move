export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        page: "rgb(var(--color-page) / <alpha-value>)",
        panel: "rgb(var(--color-panel) / <alpha-value>)",
        card: "rgb(var(--color-card) / <alpha-value>)",
        divider: "rgb(var(--color-divider) / <alpha-value>)",
        base: "rgb(var(--color-base) / <alpha-value>)",
        primary: "rgb(var(--color-primary) / <alpha-value>)",
        secondary: "rgb(var(--color-secondary) / <alpha-value>)",
        tertiary: "rgb(var(--color-tertiary) / <alpha-value>)",
        muted: "rgb(var(--color-muted) / <alpha-value>)",
        disabled: "rgb(var(--color-disabled) / <alpha-value>)",
        accent: {
          50: "rgb(var(--color-accent-50) / <alpha-value>)",
          100: "rgb(var(--color-accent-100) / <alpha-value>)",
          200: "rgb(var(--color-accent-200) / <alpha-value>)",
          300: "rgb(var(--color-accent-300) / <alpha-value>)",
          400: "rgb(var(--color-accent-400) / <alpha-value>)",
          500: "rgb(var(--color-accent-500) / <alpha-value>)",
          600: "rgb(var(--color-accent-600) / <alpha-value>)",
        },
        danger: "#b91c1c",
      },
      boxShadow: {
        panel: "0 18px 42px rgba(120, 155, 188, 0.16)",
      },
      fontFamily: {
        sans: ["Inter", "\"PingFang SC\"", "\"Microsoft YaHei\"", "system-ui", "sans-serif"],
        mono: ["\"Roboto Mono\"", "\"SFMono-Regular\"", "monospace"],
      },
    },
  },
  plugins: [],
};
