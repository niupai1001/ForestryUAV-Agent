import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: '/ui/',
  build: { outDir: '../src/forestry_agent/static', emptyOutDir: true },
  server: { proxy: { '/tasks': 'http://127.0.0.1:8765', '/session': 'http://127.0.0.1:8765',
    '/resources': 'http://127.0.0.1:8765', '/models': 'http://127.0.0.1:8765',
    '/settings': 'http://127.0.0.1:8765', '/artifacts': 'http://127.0.0.1:8765' } }
});
