import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 开发期：/api 代理到 FastAPI(:8000)；交付期由 FastAPI 托管 dist。
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
