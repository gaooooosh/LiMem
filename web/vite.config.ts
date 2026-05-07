import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// 同进程托管：base="/ui/"，构建产物在 dist/，Dockerfile stage 2 拷到 src/service/static/ui/
// 开发期 5173 走 proxy 调后端 :8000
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  base: "/ui/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      "/me": "http://127.0.0.1:8000",
      "/admin": "http://127.0.0.1:8000",
      "/databases": "http://127.0.0.1:8000",
      "/db": "http://127.0.0.1:8000",
      "/graph": "http://127.0.0.1:8000",
      "/logs": "http://127.0.0.1:8000",
    },
  },
});
