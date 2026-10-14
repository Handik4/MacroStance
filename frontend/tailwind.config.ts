import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper: "#faf8f3",
        ink: "#0b1220",
        rule: "#d9d4c7",
        // stance palette
        dovish: { DEFAULT: "#047857", soft: "#d1fae5", ink: "#064e3b" },
        neutral: { DEFAULT: "#1d4ed8", soft: "#dbeafe", ink: "#1e3a8a" },
        hawkish: { DEFAULT: "#b91c1c", soft: "#fee2e2", ink: "#7f1d1d" },
      },
      fontFamily: {
        serif: ['"Iowan Old Style"', '"Palatino Linotype"', "Palatino", "Georgia", "serif"],
        mono: ['"SF Mono"', "ui-monospace", "Menlo", "Consolas", '"Liberation Mono"', "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
