import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ command }) => ({
  plugins: [react()],
  base: command === 'build' ? '/static/build/' : '/',
  server: {
    proxy: {
      '/ask': 'http://127.0.0.1:8080',
      '/health': 'http://127.0.0.1:8080',
    },
  },
  build: {
    outDir: '../static/build',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return;
          if (/node_modules\/(react|react-dom|scheduler)\//.test(id)) return 'framework';
          if (/node_modules\/(recharts|d3-[^/]+)\//.test(id)) return 'charts';
          if (/node_modules\/(react-markdown|remark-[^/]+|unified|micromark|mdast-[^/]+)\//.test(id)) return 'markdown';
        },
      },
    },
  },
}));