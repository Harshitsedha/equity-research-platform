import type { Config } from "tailwindcss";

/**
 * The terminal design system, declared ONCE here over the CSS variables in
 * globals.css. Components consume these tokens (bg / fg / border / status-*) —
 * never ad-hoc hex. JetBrains Mono carries all data; Inter the labels/nav.
 */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        "bg-elevated": "var(--bg-elevated)",
        fg: "var(--fg)",
        "fg-dim": "var(--fg-dim)",
        border: "var(--border)",
        accent: "var(--accent)",
        "status-active": "var(--status-active)",
        "status-candidate": "var(--status-candidate)",
        "status-dropped": "var(--status-dropped)",
      },
      fontFamily: {
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
      },
      borderRadius: {
        none: "0",
        sm: "1px",
        DEFAULT: "2px",
      },
    },
  },
  plugins: [],
};

export default config;
