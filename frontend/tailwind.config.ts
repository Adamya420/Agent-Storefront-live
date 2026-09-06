import type { Config } from "tailwindcss";

// Colors are driven by CSS variables (see globals.css) so the light/dark swap
// is a genuine theme change. Light is the default surface — "mostly neutral,
// color used only as semantic signal" (Observatory design brief) — dark is a
// fully-specified equal citizen, not an inverted afterthought.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "rgb(var(--bg) / <alpha-value>)",
        surface: "rgb(var(--surface) / <alpha-value>)",
        ink: "rgb(var(--ink) / <alpha-value>)",
        ink2: "rgb(var(--ink2) / <alpha-value>)",
        ink3: "rgb(var(--ink3) / <alpha-value>)",
        line: "rgb(var(--line) / <alpha-value>)",
        line2: "rgb(var(--line2) / <alpha-value>)",
        accent: "rgb(var(--accent) / <alpha-value>)",
        "accent-bg": "rgb(var(--accent-bg) / <alpha-value>)",
        jade: "rgb(var(--jade) / <alpha-value>)",
        "jade-bg": "rgb(var(--jade-bg) / <alpha-value>)",
        ochre: "rgb(var(--ochre) / <alpha-value>)",
        "ochre-bg": "rgb(var(--ochre-bg) / <alpha-value>)",
        crimson: "rgb(var(--crimson) / <alpha-value>)",
        "crimson-bg": "rgb(var(--crimson-bg) / <alpha-value>)",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      borderRadius: { card: "6px", pill: "6px", chip: "4px" },
      fontSize: {
        "2xs": ["10.5px", { lineHeight: "1.4", letterSpacing: "0.02em" }],
      },
      boxShadow: {
        // deliberately minimal — the brief avoids "shadows on everything";
        // elevation is reserved for things that truly float above the page
        flyout: "0 8px 28px -8px rgb(var(--shadow) / 0.28)",
      },
      transitionTimingFunction: { swift: "cubic-bezier(0.4, 0, 0.1, 1)" },
      keyframes: {
        "fade-up": { "0%": { opacity: "0", transform: "translateY(4px)" }, "100%": { opacity: "1", transform: "translateY(0)" } },
        "fade-in": { "0%": { opacity: "0" }, "100%": { opacity: "1" } },
        "slide-in": { "0%": { transform: "translateX(12px)", opacity: "0" }, "100%": { transform: "translateX(0)", opacity: "1" } },
      },
      animation: {
        "fade-up": "fade-up 0.32s var(--ease, cubic-bezier(0.4,0,0.1,1)) both",
        "fade-in": "fade-in 0.22s var(--ease, cubic-bezier(0.4,0,0.1,1)) both",
        "slide-in": "slide-in 0.26s var(--ease, cubic-bezier(0.4,0,0.1,1)) both",
      },
    },
  },
  plugins: [],
};
export default config;
