import { defineConfig, type Plugin } from 'vite'
import { createRequire } from 'node:module'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'

const defaultOutDir = resolve(__dirname, '../src/despereaux/static/reader')
const outDir = process.env.VITE_OUT_DIR ?? defaultOutDir

const require = createRequire(import.meta.url)
const pdfjsPkgPath = require.resolve('pdfjs-dist/package.json')
const pdfjsRoot = resolve(pdfjsPkgPath, '..')
const pdfjsVersion = JSON.parse(readFileSync(pdfjsPkgPath, 'utf8')).version as string

// Package data PDF.js loads at RUNTIME by URL rather than importing: the
// OpenJPEG/JBIG2 decoders (wasm/), CJK character maps (cmaps/), the standard-14
// font data (standard_fonts/) and ICC profiles (iccs/). None of it is reachable
// from the module graph, so Vite never sees it — and when the URL is missing
// PDF.js only WARNS. A JPEG 2000 scan (any scanned codex) then renders as a
// blank white page with no error: exactly the failure this ships to fix.
const PDFJS_DATA_DIRS = ['wasm', 'cmaps', 'standard_fonts', 'iccs'] as const

// Version-scoped so each pdfjs-dist upgrade gets its own URL. These files must
// keep their exact names (PDF.js concatenates `${wasmUrl}openjpeg.wasm`), so a
// content hash is not an option; putting the version in the directory is what
// keeps an immutable-cached copy from ever facing a mismatched reader build.
const pdfjsDataDir = `assets/pdfjs-${pdfjsVersion}/`

function copyPdfjsData(): Plugin {
  return {
    name: 'despereaux:pdfjs-data',
    buildStart() {
      for (const dir of PDFJS_DATA_DIRS) {
        const src = join(pdfjsRoot, dir)
        for (const entry of readdirSync(src)) {
          const file = join(src, entry)
          if (!statSync(file).isFile()) continue
          this.emitFile({
            type: 'asset',
            fileName: `${pdfjsDataDir}${dir}/${entry}`,
            source: readFileSync(file),
          })
        }
      }
    },
  }
}

export default defineConfig({
  plugins: [copyPdfjsData()],
  // Exposes the version-scoped data directory to the reader, which builds the
  // cMapUrl/standardFontDataUrl/wasmUrl/iccUrl it hands to getDocument().
  define: {
    __PDFJS_DATA_DIR__: JSON.stringify(pdfjsDataDir),
  },
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
