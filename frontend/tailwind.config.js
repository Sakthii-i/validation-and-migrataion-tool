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
          50:  '#eef4ff',
          100: '#dbe7ff',
          200: '#b7ceff',
          300: '#93b5ff',
          400: '#6f9cff',
          500: '#3b82f6', // MAIN BLUE (modern)
          600: '#2563eb',
          700: '#1d4ed8',
          800: '#1e40af',
          900: '#1e3a8a',
        },
        surface: {
          50: '#fafafa',
          100: '#f5f5f5',
          200: '#eeeeee',
          300: '#e0e0e0',
          400: '#bdbdbd',
        },
        success: { DEFAULT: '#16a34a', light: '#22c55e', bg: '#ecfdf5' },
        danger:  { DEFAULT: '#dc2626', light: '#ef4444', bg: '#fef2f2' },
        warning: { DEFAULT: '#d97706', light: '#f59e0b', bg: '#fffbeb' },
        info:    { DEFAULT: '#2563eb', light: '#3b82f6', bg: '#eff6ff' },
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
