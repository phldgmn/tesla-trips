// eslint.config.js - Vite-Projekt mit React und TypeScript (ESLint 9 Flat Config)
//
// Migriert aus dem früheren `.eslintrc.cjs` (eslintrc-Format) mittels
// `FlatCompat`, damit die bisherigen `extends`-Einträge unverändert
// weiterverwendet werden können, statt jedes Plugin-Regelset von Hand neu
// zusammenzusetzen (Fehlerquelle bei einer 1:1-Migration).
import path from "path";
import { fileURLToPath } from "url";
import js from "@eslint/js";
import { FlatCompat } from "@eslint/eslintrc";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const compat = new FlatCompat({
  baseDirectory: __dirname,
  recommendedConfig: js.configs.recommended,
});

export default [
  { ignores: ["dist/**", "node_modules/**", "coverage/**"] },
  ...compat.config({
    env: {
      browser: true,
      es2021: true,
      node: true,
    },
    extends: [
      "eslint:recommended",
      "plugin:@typescript-eslint/recommended",
      "plugin:react/recommended",
      "plugin:react-hooks/recommended",
      "plugin:prettier/recommended",
    ],
    parser: "@typescript-eslint/parser",
    parserOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      ecmaFeatures: {
        jsx: true,
      },
    },
    settings: {
      react: {
        version: "detect",
      },
    },
    plugins: ["@typescript-eslint", "react", "react-hooks", "prettier"],
    rules: {
      // TypeScript-spezifisch
      "@typescript-eslint/no-explicit-any": "warn",
      "@typescript-eslint/no-unused-vars": "error",
      "@typescript-eslint/no-use-before-define": "off",

      // React-spezifisch
      "react/prop-types": "off",
      "react/react-in-jsx-scope": "off",
      "react/jsx-uses-react": "off",

      // Allgemein
      "no-console": ["warn", { allow: ["error"] }],
    },
  }),
];
