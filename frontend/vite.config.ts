import { defineConfig } from 'vite'
import { resolve } from 'node:path'

const defaultOutDir = resolve(__dirname, '../src/despereaux/static/reader')
const outDir = process.env.VITE_OUT_DIR ?? defaultOutDir

export default defineConfig({
  build: {
    outDir,
    emptyOutDir: true,
    sourcemap: true,
    rollupOptions: {
      input: resolve(__dirname, 'src/reader/index.ts'),
      output: {
        entryFileNames: 'assets/reader.js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: (assetInfo) => {
          if (assetInfo.name?.endsWith('.css')) return 'assets/reader.css'
          return 'assets/[name][extname]'
        },
      },
    },
  },
})
