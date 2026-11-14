import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
export default defineConfig({plugins:[vue()],server:{proxy:{'/api':'http://127.0.0.1:8000'}},build:{outDir:'../backend/app/static',emptyOutDir:true,chunkSizeWarningLimit:1800,rollupOptions:{output:{manualChunks:{charts:['echarts'],ui:['element-plus'],math:['katex','highlight.js']}}}}});
