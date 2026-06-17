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
    installTtsBridge(reader)
  } catch (e) {
    console.error('reader failed to start', e)
    renderUnsupported('Reader failed to start — check the console.')
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
  root.innerHTML = `<div class="reader-error"><p>${msg}</p></div>`
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bootstrap)
} else {
  void bootstrap()
}
