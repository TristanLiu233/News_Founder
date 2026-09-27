import { FlatCompat } from "@eslint/eslintrc";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

const baseDirectory = dirname(fileURLToPath(import.meta.url));
const compat = new FlatCompat({ baseDirectory });

const config = [
  ...compat.config({ extends: ["next/core-web-vitals", "next/typescript"] }),
  { ignores: [".next/**", "node_modules/**", "out/**", "coverage/**", "next-env.d.ts"] },
];

export default config;
