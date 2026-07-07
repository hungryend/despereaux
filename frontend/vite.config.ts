import { defineConfig } from 'vite'
import { resolve } from 'node:path'

const defaultOutDir = resolve(__dirname, '../src/despereaux/static/reader')
const outDir = process.env.VITE_OUT_DIR ?? defaultOutDir

export default defineConfig({
  // Important: the bundle is served from /static/reader/ in the FastAPI app
  // (StaticFiles mount). `?url` imports + chunk URLs both resolve against
  // this base — without it, PDF.js's worker ends up at /assets/... and 404s.
  base: '/static/reader/',
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
          // `names` replaced the deprecated singular `name` (Rollup 4 / Vite 6+).
          const name = assetInfo.names?.[0]
          // reader.css keeps a stable name — the HTML cache-busts it with ?v=.
          if (name?.endsWith('.css')) return 'assets/reader.css'
          // Everything else — notably PDF.js's pdf.worker.min.mjs — is
          // content-hashed. The worker URL is emitted INSIDE reader.js (via the
          // `?url` import), so the HTML's ?v token can't reach it; at a fixed
          // unversioned name served `immutable` for a year, a client that cached
          // one worker build keeps running it against a newer reader.js. That is
          // exactly what stranded the household tablet after the pdf.js v6->v5
          // revert: a cached v6 worker vs a v5 reader.js → version mismatch, and
          // the reader dies before fetching the PDF. A content hash gives each
          // worker build its own URL, so a new deploy is always fetched fresh.
          return 'assets/[name]-[hash][extname]'
        },
      },
    },
  },
})
