/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        oracle: {
          value:   '#f59e0b',
          growth:  '#10b981',
          yield:   '#0ea5e9',
          macro:   '#8b5cf6',
          quant:   '#f43f5e',
          pragma:  '#94a3b8',
        },
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'ui-monospace', 'monospace'],
      },
    },
  },
  plugins: [],
}
