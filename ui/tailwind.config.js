/** @type {import('tailwindcss').Config} */
// Colors map to the CSS tokens in src/styles.css, so light/dark mode is one switch
// (the data-theme attribute on <html>) for both Tailwind and the older review styles.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["selector", '[data-theme="dark"]'],
  corePlugins: {
    // src/styles.css already resets the page; Tailwind's reset would restyle the review screen.
    preflight: false,
  },
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        surface: "var(--surface)",
        "surface-2": "var(--surface-2)",
        ink: "var(--text)",
        muted: "var(--muted)",
        line: "var(--border)",
        primary: "var(--primary)",
        "primary-hover": "var(--primary-hover)",
        "primary-ink": "var(--primary-text)",
        selected: "var(--selected)",
        urgent: "var(--urgent)",
        "urgent-soft": "var(--urgent-soft)",
        "urgent-ink": "var(--urgent-text)",
        high: "var(--high)",
        "high-soft": "var(--high-soft)",
        "high-ink": "var(--high-text)",
        medium: "var(--medium)",
        "medium-soft": "var(--medium-soft)",
        "medium-ink": "var(--medium-text)",
        low: "var(--low)",
        "low-soft": "var(--low-soft)",
        "low-ink": "var(--low-text)",
        good: "var(--good)",
        "good-soft": "var(--good-soft)",
        series: "var(--series-1)",
      },
    },
  },
  plugins: [],
};
