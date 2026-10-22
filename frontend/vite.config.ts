import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// dev 主路径：/chat /documents /health 代理到本机 FastAPI（:8000），前后端同源免 CORS。
// 生产由 docker/nginx.conf 反代（compose 内 api:8000 同源）。
// vite preview 直连 API 时用 VITE_API_URL + 后端 CORS_ORIGINS 兜底。
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/chat": { target: "http://localhost:8000", changeOrigin: true },
      "/documents": { target: "http://localhost:8000", changeOrigin: true },
      "/health": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
