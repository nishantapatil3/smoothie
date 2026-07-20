import js from "@eslint/js";
import globals from "globals";
import tseslint from "@typescript-eslint/eslint-plugin";
import tsparser from "@typescript-eslint/parser";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";

// eslint-plugin-react may not be present in every install; load it defensively
// so the flat config still lints TypeScript when the dependency is absent.
let react = null;
try {
  react = await import("eslint-plugin-react");
  react = react.default;
} catch {
  react = null;
}

const reactRules = react ? { ...react.configs.recommended.rules } : {};
const reactSettings = react ? { react: { version: "18.3" } } : {};
const reactPlugin = react ? { react } : {};

export default [
  js.configs.recommended,
  {
    ignores: ["dist", "node_modules"],
  },
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2020,
      globals: {
        ...globals.browser,
        ...globals.node,
      },
      parser: tsparser,
      parserOptions: { ecmaVersion: "latest", sourceType: "module", ecmaFeatures: { jsx: true } },
    },
    settings: reactSettings,
    plugins: {
      ...reactPlugin,
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
      "@typescript-eslint": tseslint,
    },
    rules: {
      ...tseslint.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      ...reactRules,
      // TypeScript already catches undefined identifiers; the parser doesn't
      // see type-only references (e.g. `React.FormEvent`, `RequestInit`), so
      // this rule produces false positives on valid TS.
      "no-undef": "off",
      "react/jsx-no-target-blank": "off",
      "react-refresh/only-export-components": [
        "warn",
        { allowConstantExport: true },
      ],
    },
  },
];
