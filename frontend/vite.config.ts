import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': {
        target: 'https://reduce-wing-oxide-crops.trycloudflare.com',
        changeOrigin: true,
      },
    },
  },
})
