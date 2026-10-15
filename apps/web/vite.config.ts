import { fileURLToPath, URL } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Every API call is relative to /api. In development Vite forwards it to the FastAPI service
// (apps/api); hosted, the same build is served from the API's origin, so nothing changes.
const API_TARGET = process.env.MIDFREQ_API_URL ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: { '/api': { target: API_TARGET, changeOrigin: false } },
  },
  preview: {
    port: 4173,
    proxy: { '/api': { target: API_TARGET, changeOrigin: false } },
  },
  build: {
    // Vendor code in its own chunks, so a release that changes only the app keeps them cached.
    // ECharts loads only with the Overview and Research pages.
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            // shared helpers (tslib) stay out of the lazy ECharts chunk
            { name: 'vendor', test: /node_modules[\\/]tslib[\\/]/, priority: 40 },
            { name: 'react', test: /node_modules[\\/](react|react-dom|scheduler|react-router)[\\/]/, priority: 30 },
            { name: 'tradingview', test: /node_modules[\\/](lightweight-charts|fancy-canvas)[\\/]/, priority: 30 },
            { name: 'echarts', test: /node_modules[\\/](echarts|zrender|echarts-for-react|size-sensor)[\\/]/, priority: 30 },
          ],
        },
      },
    },
    chunkSizeWarningLimit: 600,
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    restoreMocks: true,
    env: { TZ: 'UTC' },
  },
})
