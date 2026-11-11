import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { rollupOptions: { output: { manualChunks: { graph: ['@xyflow/react'], motion: ['framer-motion'] } } } },
  server: { port: 3000, strictPort: true, proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true, timeout: 0 } } },
})
