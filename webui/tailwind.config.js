/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        primary: {
          50: "#111f35",
          100: "#172c49",
          200: "#22436d",
          300: "#426ca5",
          400: "#6d9ae0",
          500: "#4484ee",
          600: "#2563eb",
          700: "#1e54cc",
          800: "#1945a8",
          900: "#163986",
        },
      },
    },
  },
  plugins: [],
};
