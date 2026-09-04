/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        display: ['Manrope', 'Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'monospace'],
      },
      colors: {
        ink: '#14211c',
        parchment: '#f4f1e9',
        moss: '#326653',
        fern: '#74a88c',
        copper: '#c47745',
        fog: '#dfe5dd',
      },
      boxShadow: {
        soft: '0 18px 50px rgba(20, 33, 28, 0.10)',
      },
    },
  },
  plugins: [],
}
