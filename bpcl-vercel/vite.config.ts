import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // `npm run dev` starts tools/devserver.py alongside Vite; it exposes the
    // same routes the Vercel functions do, so the client code is identical in
    // development and production.
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    rollupOptions: {
      output: {
        // Recharts and Leaflet are large and change far less often than this
        // app's own code. Splitting them keeps a redeploy from invalidating
        // ~400 KB of vendor bundle that did not change.
        manualChunks: {
          charts: ['recharts'],
          map: ['leaflet', 'react-leaflet'],
        },
      },
    },
  },
});
