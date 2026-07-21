import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  root: "src/renderer",
  base: "./",
  plugins: [react()],
  resolve: { alias: { "@shared": path.resolve(__dirname, "src/shared") } },
  build: { outDir: "../../dist/renderer", emptyOutDir: false },
  server: { port: 5173, strictPort: true },
});
