/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          50: '#f3e5f5',
          100: '#e1bee7',
          200: '#ce93d8',
          300: '#ba68c8',
          400: '#ab47bc',
          500: '#7b1fa2',
          600: '#6a1b9a',
          700: '#4a148c',
          800: '#38006b',
          900: '#1a0033',
        },
        surface: {
          50: '#fafafa',
          100: '#f5f5f5',
          200: '#eeeeee',
          300: '#e0e0e0',
          400: '#bdbdbd',
        },
        success: { DEFAULT: '#2e7d32', light: '#4caf50', bg: '#e8f5e9' },
        danger: { DEFAULT: '#c62828', light: '#ef5350', bg: '#ffebee' },
        warning: { DEFAULT: '#e65100', light: '#ff9800', bg: '#fff3e0' },
        info: { DEFAULT: '#0277bd', light: '#29b6f6', bg: '#e1f5fe' },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      boxShadow: {
        card: '0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.06)',
        elevated: '0 4px 6px rgba(0,0,0,0.07), 0 2px 4px rgba(0,0,0,0.06)',
        modal: '0 20px 25px -5px rgba(0,0,0,0.1), 0 10px 10px -5px rgba(0,0,0,0.04)',
      },
    },
  },
  plugins: [],
}
