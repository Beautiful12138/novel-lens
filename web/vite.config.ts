import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: '/ui/',
  build: { outDir: '../src/novel_lens/webui', emptyOutDir: true },
  server: {
    proxy: {
      '/auth': 'http://127.0.0.1:8000',
      '/assets': 'http://127.0.0.1:8000',
      '/library': 'http://127.0.0.1:8000',
      '/source': 'http://127.0.0.1:8000',
      '/works': 'http://127.0.0.1:8000',
    },
  },
});
