import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:8000' } },
  build: { rollupOptions: { output: { manualChunks(id) {
    if (!id.includes('node_modules')) return
    if (id.includes('cytoscape')) return 'graph'
    if (id.includes('recharts') || /[\\/]d3-/.test(id)) return 'charts'
    return 'vendor'
  } } } },
})
