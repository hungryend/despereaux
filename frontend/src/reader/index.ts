import './reader.css'
import { ComicReader } from './comic-reader'
import { EpubReader } from './epub-reader'
import { PdfReader } from './pdf-reader'
import type { Reader } from './types'

async function bootstrap(): Promise<void> {
  const cfg = window.DESPEREAUX_BOOK
  if (!cfg) {
    console.error('DESPEREAUX_BOOK bootstrap missing')
    return
  }

  let reader: Reader
  switch (cfg.format) {
    case 'epub':
      reader = new EpubReader(cfg)
      break
    case 'pdf':
      reader = new PdfReader(cfg)
      break
    case 'cbz':
    case 'cbr':
      reader = new ComicReader(cfg)
      break
    default:
      renderUnsupported(`Unsupported format: ${cfg.format}`)
      return
  }

  try {
    await reader.start()
    wireNavButtons(reader)
    wireNavPositionToggle()
    installTtsBridge(reader)
  } catch (e) {
    console.error('reader failed to start', e)
    // Surface the real reason (e.g. a PDF.js worker/version mismatch, or an
    // unsupported-browser error) so a failure on a device we can't attach
    // DevTools to is self-diagnosing rather than a blank page.
    const detail = e instanceof Error ? e.message : String(e)
    renderUnsupported(`Reader failed to start: ${detail}`)
  }

  // Expose for ad-hoc debugging from DevTools.
  ;(window as unknown as { __despereaux_reader: Reader }).__despereaux_reader = reader
}

function wireNavButtons(reader: Reader): void {
  const prev = document.getElementById('reader-prev')
  const next = document.getElementById('reader-next')
  prev?.addEventListener('click', (e) => {
    e.stopPropagation()
    reader.prev()
  })
  next?.addEventListener('click', (e) => {
    e.stopPropagation()
    reader.next()
  })
}

// Persisted per device; reader.html applies the class pre-paint on load.
const NAV_POS_KEY = 'despereaux:navPos'

function wireNavPositionToggle(): void {
  const btn = document.getElementById('nav-pos-toggle')
  btn?.addEventListener('click', (e) => {
    e.stopPropagation()
    const root = document.documentElement
    const toTop = !root.classList.contains('nav-top')
    root.classList.toggle('nav-top', toTop)
    root.classList.toggle('nav-bottom', !toTop)
    try {
      localStorage.setItem(NAV_POS_KEY, toTop ? 'top' : 'bottom')
    } catch {
      /* storage unavailable — position still applies for this page */
    }
    // Flipping the strip changes #reader-root's height; epub.js and the PDF
    // reader both re-measure on window resize.
    window.dispatchEvent(new Event('resize'))
  })
}

// Exposed to the Furlough Android app over the WebView JS bridge. The app calls
// these (async ones via a @JavascriptInterface callback) to read the book aloud.
interface FurloughTtsBridge {
  capabilities(): { hasText: boolean; canHighlight: boolean; format: string }
  beginSection(): Promise<{ texts: string[]; start: number }>
  advanceSection(): Promise<boolean>
  highlightUnit(index: number): Promise<void>
  highlightWord(index: number, start: number, end: number): Promise<void>
  clearHighlight(): Promise<void>
}

function installTtsBridge(reader: Reader): void {
  const api: FurloughTtsBridge = {
    capabilities: () => ({
      hasText: reader.hasReadableText(),
      canHighlight: reader.canHighlight(),
      format: window.DESPEREAUX_BOOK?.format ?? '',
    }),
    beginSection: () => reader.ttsBeginSection(),
    advanceSection: () => reader.ttsAdvanceSection(),
    highlightUnit: (i) => reader.ttsHighlightUnit(i),
    highlightWord: (i, s, e) => reader.ttsHighlightWord(i, s, e),
    clearHighlight: () => reader.ttsClearHighlight(),
  }
  ;(window as unknown as { __furloughTts: FurloughTtsBridge }).__furloughTts = api
}

function renderUnsupported(msg: string): void {
  const root = document.querySelector<HTMLElement>('#reader-root')
  if (!root) return
  // Build via textContent (not interpolated innerHTML) — msg can now carry a
  // raw error string, which must never be parsed as HTML.
  root.innerHTML = ''
  const wrap = document.createElement('div')
  wrap.className = 'reader-error'
  const p = document.createElement('p')
  p.textContent = msg
  wrap.appendChild(p)
  root.appendChild(wrap)
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bootstrap)
} else {
  void bootstrap()
}
