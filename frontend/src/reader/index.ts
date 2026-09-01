import './reader.css'
import { ComicReader } from './comic-reader'
import { EpubReader } from './epub-reader'
import { PdfReader } from './pdf-reader'
import type { Reader } from './types'

async function bootstrap(): Promise<void> {
  const cfg = window.DESPEREAUX_BOOK
  if (!cfg) {
    console.error('DESPEREAUX_BOOK bootstrap missing')
    hideLoading()
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
    wireNavZones(reader)
    installTtsBridge(reader)
  } catch (e) {
    console.error('reader failed to start', e)
    // Surface the real reason (e.g. a PDF.js worker/version mismatch, or an
    // unsupported-browser error) so a failure on a device we can't attach
    // DevTools to is self-diagnosing rather than a blank page.
    const detail = e instanceof Error ? e.message : String(e)
    renderUnsupported(`Reader failed to start: ${detail}`)
  } finally {
    // First page is up (or we've shown an error) — drop the loading overlay.
    hideLoading()
  }

  // Expose for ad-hoc debugging from DevTools.
  ;(window as unknown as { __despereaux_reader: Reader }).__despereaux_reader = reader
}

// Page-turn zones: full-height strips down the left and right edges of the
// screen (see .reader-nav in reader.css), so a page turn is always under the
// thumb wherever the device is held — no reaching for a corner button.
//
// They are real <button>s, so keyboard and screen-reader users get them for
// free. On touch they have to behave like the content underneath, because on
// PDFs and comics the zones float over the page:
//   - a tap turns the page (the zone's own direction);
//   - a horizontal swipe turns the page in the swipe's direction, matching the
//     swipe handling the readers install on the content itself;
//   - a drag while the page is zoomed in PANS it, so the outer edges of a
//     zoomed comic stay reachable instead of being dead strips.
// A pinch that STARTS inside a zone is the one gesture that's lost: the zone
// gets the touch, not the canvas. Pinching from the middle of the page (or the
// zoom pill) still works.
const TAP_SLOP = 12 // px of travel still counted as a tap, not a drag
const SWIPE_TH = 50 // px of horizontal travel that counts as a swipe
// A tap fires touchend and then a synthesised click ~300ms later; ignore the
// click so one tap doesn't turn two pages.
const GHOST_CLICK_MS = 700

type NavDir = 'prev' | 'next'

function wireNavZones(reader: Reader): void {
  const prev = document.getElementById('reader-prev')
  const next = document.getElementById('reader-next')
  if (prev) wireNavZone(prev, reader, 'prev')
  if (next) wireNavZone(next, reader, 'next')
}

function wireNavZone(zone: HTMLElement, reader: Reader, dir: NavDir): void {
  const turn = (d: NavDir): void => (d === 'next' ? reader.next() : reader.prev())

  let lastTouchEnd = 0
  zone.addEventListener('click', (e) => {
    e.stopPropagation()
    if (Date.now() - lastTouchEnd < GHOST_CLICK_MS) return
    turn(dir)
  })

  let startX = 0
  let startY = 0
  let lastX = 0
  let lastY = 0
  let tracking = false
  let panned = false

  zone.addEventListener(
    'touchstart',
    (e) => {
      if (e.touches.length > 1) {
        tracking = false // multi-touch (pinch): not ours
        return
      }
      tracking = true
      panned = false
      startX = lastX = e.touches[0].clientX
      startY = lastY = e.touches[0].clientY
    },
    { passive: true }
  )

  zone.addEventListener(
    'touchmove',
    (e) => {
      if (!tracking) return
      if (e.touches.length > 1) {
        tracking = false
        return
      }
      const t = e.touches[0]
      // Zoomed in? The reader root is the scroll container — pan it by hand,
      // since the browser won't scroll it for a touch that began on the zone.
      const root = document.getElementById('reader-root')
      if (root && isPannable(root)) {
        root.scrollLeft -= t.clientX - lastX
        root.scrollTop -= t.clientY - lastY
        panned = true
      }
      lastX = t.clientX
      lastY = t.clientY
    },
    { passive: true }
  )

  zone.addEventListener(
    'touchend',
    (e) => {
      lastTouchEnd = Date.now()
      if (!tracking) return
      tracking = false
      if (panned) return // that was a pan, not a page turn
      const dx = (e.changedTouches[0]?.clientX ?? startX) - startX
      const dy = (e.changedTouches[0]?.clientY ?? startY) - startY
      if (Math.abs(dx) <= TAP_SLOP && Math.abs(dy) <= TAP_SLOP) {
        turn(dir)
      } else if (Math.abs(dx) >= SWIPE_TH && Math.abs(dx) > Math.abs(dy)) {
        turn(dx < 0 ? 'next' : 'prev')
      }
      // Anything else (a vertical or ambiguous drag) turns nothing.
    },
    { passive: true }
  )
}

// True when the content is larger than its container, i.e. a zoomed-in PDF or
// comic page that can be panned. EPUB text never scrolls here (epub.js
// paginates into the box), so it always reads false.
function isPannable(root: HTMLElement): boolean {
  return root.scrollWidth > root.clientWidth + 1 || root.scrollHeight > root.clientHeight + 1
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
  hideLoading()
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

// Remove the initial-open loading overlay (rendered in reader.html) once the
// reader has shown its first page or hit an error. Fades out, then removes the
// node; idempotent — safe to call more than once and a no-op if already gone.
function hideLoading(): void {
  const el = document.getElementById('reader-loading')
  if (!el) return
  el.classList.add('is-done')
  const remove = (): void => el.remove()
  el.addEventListener('transitionend', remove, { once: true })
  window.setTimeout(remove, 400)
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bootstrap)
} else {
  void bootstrap()
}
