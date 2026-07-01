import * as pdfjsLib from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

import { ProgressTracker } from './progress-tracker'
import { TocPanel } from './toc-panel'
import type { BookBootstrap, Reader, TocItem } from './types'

// Wire the worker via Vite's ?url import.
pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl

// Save progress when the page index moves at least this much (avoids
// upserting on every scroll/render tick).
const SAVE_PAGE_DELTA = 1

// Zoom limits. 1 = fit-to-window (the default). Above 1, the page is rendered
// larger than the viewport and can be panned. Re-rendering at the target scale
// (rather than CSS-scaling a bitmap) keeps text crisp at every zoom level.
const MIN_ZOOM = 1
const MAX_ZOOM = 4
const ZOOM_STEP = 1.25
const ZOOM_KEY = 'despereaux:pdfZoom'

interface PdfPosition {
  page: number
}

export class PdfReader implements Reader {
  private pdf: pdfjsLib.PDFDocumentProxy | null = null
  // Kept for teardown: since pdfjs-dist 5/6, destroy() lives on the loading
  // task (PDFDocumentProxy.destroy was removed).
  private loadingTask: pdfjsLib.PDFDocumentLoadingTask | null = null
  private tracker: ProgressTracker
  private tocPanel: TocPanel | null = null
  private container: HTMLElement | null = null
  private canvas: HTMLCanvasElement
  private renderTask: pdfjsLib.RenderTask | null = null
  private currentPage = 1
  private numPages = 0
  private lastSavedPage = -1
  // Look-ahead so a next-page turn doesn't stall on a range fetch + page parse.
  private readonly prefetchAheadCount = 2
  private pageCache = new Map<number, pdfjsLib.PDFPageProxy>()

  private zoom = 1
  private fitScale = 1
  private zoomControls: HTMLElement | null = null
  private zoomLabel: HTMLElement | null = null

  constructor(private bootstrap: BookBootstrap) {
    this.tracker = new ProgressTracker(bootstrap.progressUrl)
    this.canvas = document.createElement('canvas')
    this.canvas.className = 'pdf-canvas'
    this.zoom = this.loadZoom()
  }

  async start(): Promise<void> {
    const root = document.querySelector<HTMLElement>('#reader-root')
    if (!root) throw new Error('reader-root element missing')
    this.container = root
    root.classList.add('pdf-mode')
    root.appendChild(this.canvas)

    this.loadingTask = pdfjsLib.getDocument({
      url: this.bootstrap.fileUrl,
      withCredentials: true,
      // Range requests + streaming so opening a 200MB book doesn't download
      // the whole thing before showing page 1.
      disableAutoFetch: false,
      disableStream: false,
      rangeChunkSize: 65536,
    })
    this.pdf = await this.loadingTask.promise
    this.numPages = this.pdf.numPages

    // Restore saved position.
    const saved = await this.tracker.load()
    let startPage = 1
    if (saved?.position) {
      try {
        const pos = JSON.parse(saved.position) as PdfPosition
        if (typeof pos.page === 'number' && pos.page >= 1 && pos.page <= this.numPages) {
          startPage = pos.page
        }
      } catch {
        /* malformed — start at 1 */
      }
    }

    // Load outline as TOC.
    try {
      const outline = await this.pdf.getOutline()
      const toc = this.outlineToToc(outline ?? [])
      this.tocPanel = new TocPanel('#toc-panel', '#toc-toggle', (href) =>
        this.handleTocClick(href)
      )
      this.tocPanel.setItems(toc)
    } catch {
      /* no outline */
    }

    // Mark the loaded page as "already saved" so the render-completion
    // savePositionIfChanged() doesn't fire a redundant write of the same value.
    this.lastSavedPage = startPage
    await this.goToPage(startPage, false, true)
    this.createZoomControls()
    this.attachNavigation()
    this.attachLifecycleSavers()

    window.addEventListener('resize', () => {
      void this.goToPage(this.currentPage, /*redraw*/ true)
    })
  }

  private attachLifecycleSavers(): void {
    // beforeunload alone misses mobile tab-switch / app-background. Hook the
    // visibility + pagehide events too; tracker.beacon() uses sendBeacon-style
    // keepalive so the save survives page teardown.
    const beaconNow = () => this.tracker.beacon()
    window.addEventListener('beforeunload', beaconNow)
    window.addEventListener('pagehide', beaconNow)
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') beaconNow()
    })
  }

  private outlineToToc(outline: any[]): TocItem[] {
    return outline.map((item: any) => {
      // PDF.js outline items: { title, dest, items, ... }
      // dest is either a string (named) or an array (explicit). Encode as JSON.
      const href = JSON.stringify({ kind: 'pdf-dest', dest: item.dest })
      return {
        label: item.title ?? '—',
        href,
        subitems: item.items ? this.outlineToToc(item.items) : undefined,
      }
    })
  }

  private async handleTocClick(href: string): Promise<void> {
    if (!this.pdf) return
    try {
      const parsed = JSON.parse(href) as { kind: string; dest: any }
      if (parsed.kind !== 'pdf-dest') return
      let dest = parsed.dest
      if (typeof dest === 'string') {
        dest = await this.pdf.getDestination(dest)
      }
      if (!Array.isArray(dest) || dest.length === 0) return
      const ref = dest[0]
      const pageIndex = await this.pdf.getPageIndex(ref)
      await this.goToPage(pageIndex + 1, false, true)
    } catch (e) {
      console.warn('PDF TOC navigation failed', e)
    }
  }

  private async goToPage(pageNum: number, force = false, recenter = false): Promise<void> {
    if (!this.pdf || !this.container) return
    if (pageNum < 1 || pageNum > this.numPages) return
    if (!force && pageNum === this.currentPage && this.renderTask === null) {
      // Already showing it.
    }
    this.currentPage = pageNum

    // Cancel any in-flight render before starting a new one.
    if (this.renderTask) {
      this.renderTask.cancel()
      this.renderTask = null
    }

    const page = this.pageCache.get(pageNum) ?? (await this.pdf.getPage(pageNum))

    // Fit-to-window scale, then multiply by the user's zoom. The backing store
    // is sized at fit * zoom * devicePixelRatio so text stays sharp; the CSS
    // size is fit * zoom CSS px (so zoom > 1 overflows the viewport and pans).
    const containerWidth = this.container.clientWidth
    const containerHeight = this.container.clientHeight
    const baseViewport = page.getViewport({ scale: 1 })
    const scaleX = containerWidth / baseViewport.width
    const scaleY = containerHeight / baseViewport.height
    this.fitScale = Math.min(scaleX, scaleY)
    const cssScale = this.fitScale * this.zoom
    const dpr = window.devicePixelRatio || 1
    const viewport = page.getViewport({ scale: cssScale * dpr })

    this.canvas.width = viewport.width
    this.canvas.height = viewport.height
    this.canvas.style.width = `${viewport.width / dpr}px`
    this.canvas.style.height = `${viewport.height / dpr}px`

    // pdfjs-dist >=6 takes the canvas itself (canvasContext was removed).
    this.renderTask = page.render({ canvas: this.canvas, viewport })
    try {
      await this.renderTask.promise
    } catch (e: any) {
      if (e?.name !== 'RenderingCancelledException') throw e
    } finally {
      this.renderTask = null
    }

    if (recenter) {
      // Center horizontally, top-align vertically (typical for a fresh page).
      const cssW = this.canvas.clientWidth
      this.container.scrollLeft = Math.max(0, (cssW - this.container.clientWidth) / 2)
      this.container.scrollTop = 0
    }

    this.savePositionIfChanged()
    void this.prefetchAhead()
  }

  // Fire-and-forget warm-up of the next few pages: getPage() pulls the page's
  // bytes (range fetch) and getOperatorList() parses it, so the eventual render()
  // on a page turn is just rasterization. Bounded + evicted so a 200MB book
  // doesn't accrete page proxies.
  private async prefetchAhead(): Promise<void> {
    if (!this.pdf) return
    for (let d = 1; d <= this.prefetchAheadCount; d++) {
      const n = this.currentPage + d
      if (n > this.numPages || this.pageCache.has(n)) continue
      try {
        const page = await this.pdf.getPage(n)
        await page.getOperatorList()
        this.pageCache.set(n, page)
      } catch {
        /* prefetch is best-effort */
      }
    }
    for (const n of this.pageCache.keys()) {
      if (Math.abs(n - this.currentPage) > this.prefetchAheadCount + 1) this.pageCache.delete(n)
    }
  }

  private savePositionIfChanged(): void {
    if (Math.abs(this.currentPage - this.lastSavedPage) < SAVE_PAGE_DELTA) return
    this.lastSavedPage = this.currentPage
    const percent = this.numPages > 0 ? this.currentPage / this.numPages : 0
    this.tracker.schedule(JSON.stringify({ page: this.currentPage }), percent)
  }

  // === Zoom ===

  private loadZoom(): number {
    try {
      const raw = window.localStorage.getItem(ZOOM_KEY)
      if (raw) return this.clampZoom(parseFloat(raw))
    } catch {
      /* storage unavailable */
    }
    return 1
  }

  private clampZoom(z: number): number {
    if (!Number.isFinite(z)) return 1
    return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z))
  }

  /**
   * Re-render the current page at `target` zoom, keeping the point at
   * (screenX, screenY) anchored under the same spot on screen.
   */
  private async applyZoom(target: number, screenX: number, screenY: number): Promise<void> {
    if (!this.container) return
    const newZoom = this.clampZoom(target)
    if (Math.abs(newZoom - this.zoom) < 0.001) return

    const root = this.container
    const ratio = newZoom / this.zoom
    const rect = root.getBoundingClientRect()
    const fx = screenX - rect.left
    const fy = screenY - rect.top
    const newScrollLeft = (root.scrollLeft + fx) * ratio - fx
    const newScrollTop = (root.scrollTop + fy) * ratio - fy

    this.zoom = newZoom
    this.persistZoom()
    this.updateZoomUi()
    await this.goToPage(this.currentPage, true)

    root.scrollLeft = Math.max(0, newScrollLeft)
    root.scrollTop = Math.max(0, newScrollTop)
  }

  private persistZoom(): void {
    try {
      window.localStorage.setItem(ZOOM_KEY, String(this.zoom))
    } catch {
      /* storage unavailable */
    }
  }

  private viewportCenter(): { x: number; y: number } {
    const rect = this.container?.getBoundingClientRect()
    if (!rect) return { x: 0, y: 0 }
    return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }
  }

  zoomIn(): void {
    const c = this.viewportCenter()
    void this.applyZoom(this.zoom * ZOOM_STEP, c.x, c.y)
  }

  zoomOut(): void {
    const c = this.viewportCenter()
    void this.applyZoom(this.zoom / ZOOM_STEP, c.x, c.y)
  }

  resetZoom(): void {
    const c = this.viewportCenter()
    void this.applyZoom(1, c.x, c.y)
  }

  private createZoomControls(): void {
    const bar = document.createElement('div')
    bar.className = 'pdf-zoom'

    const out = document.createElement('button')
    out.type = 'button'
    out.textContent = '−'
    out.setAttribute('aria-label', 'Zoom out')
    out.addEventListener('click', (e) => {
      e.stopPropagation()
      this.zoomOut()
    })

    const level = document.createElement('button')
    level.type = 'button'
    level.className = 'pdf-zoom-level'
    level.setAttribute('aria-label', 'Reset zoom')
    level.addEventListener('click', (e) => {
      e.stopPropagation()
      this.resetZoom()
    })

    const inc = document.createElement('button')
    inc.type = 'button'
    inc.textContent = '+'
    inc.setAttribute('aria-label', 'Zoom in')
    inc.addEventListener('click', (e) => {
      e.stopPropagation()
      this.zoomIn()
    })

    bar.append(out, level, inc)
    document.body.appendChild(bar)
    this.zoomControls = bar
    this.zoomLabel = level
    this.updateZoomUi()
  }

  private updateZoomUi(): void {
    if (this.zoomLabel) this.zoomLabel.textContent = `${Math.round(this.zoom * 100)}%`
  }

  private attachNavigation(): void {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight' || e.key === 'PageDown' || e.key === ' ') {
        this.next()
        e.preventDefault()
      } else if (e.key === 'ArrowLeft' || e.key === 'PageUp') {
        this.prev()
        e.preventDefault()
      } else if (e.key === 'Home') {
        void this.goToPage(1, false, true)
      } else if (e.key === 'End') {
        void this.goToPage(this.numPages, false, true)
      } else if (e.key === '+' || e.key === '=') {
        this.zoomIn()
        e.preventDefault()
      } else if (e.key === '-' || e.key === '_') {
        this.zoomOut()
        e.preventDefault()
      } else if (e.key === '0') {
        this.resetZoom()
        e.preventDefault()
      }
    }
    document.addEventListener('keyup', onKey)

    // Touch gestures: single-finger swipe turns pages (only at fit zoom, so
    // panning a zoomed page doesn't flip it); two-finger pinch zooms; a
    // double-tap toggles between fit and 2x at the tapped point.
    const TH = 50
    const DOUBLE_TAP_MS = 300
    let sx: number | null = null
    let sy: number | null = null
    let pinchStartDist: number | null = null
    let pinchStartZoom = 1
    let pinchMidX = 0
    let pinchMidY = 0
    let liveZoom = this.zoom
    let lastTapTime = 0
    let lastTapX = 0
    let lastTapY = 0
    let consumedByDoubleTap = false

    const distance = (a: Touch, b: Touch) =>
      Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY)

    this.canvas.addEventListener(
      'touchstart',
      (e) => {
        if (e.touches.length === 2) {
          // Begin pinch.
          sx = null
          pinchStartDist = distance(e.touches[0], e.touches[1])
          pinchStartZoom = this.zoom
          liveZoom = this.zoom
          pinchMidX = (e.touches[0].clientX + e.touches[1].clientX) / 2
          pinchMidY = (e.touches[0].clientY + e.touches[1].clientY) / 2
          const r = this.canvas.getBoundingClientRect()
          this.canvas.style.transformOrigin = `${pinchMidX - r.left}px ${pinchMidY - r.top}px`
          e.preventDefault()
          return
        }
        if (e.touches.length !== 1) return
        const t = e.touches[0]
        const now = Date.now()
        if (
          now - lastTapTime < DOUBLE_TAP_MS &&
          Math.abs(t.clientX - lastTapX) < 30 &&
          Math.abs(t.clientY - lastTapY) < 30
        ) {
          // Double tap: toggle fit <-> 2x at the tap point.
          consumedByDoubleTap = true
          lastTapTime = 0
          sx = null
          e.preventDefault()
          void this.applyZoom(this.zoom > 1.01 ? 1 : 2, t.clientX, t.clientY)
          return
        }
        consumedByDoubleTap = false
        lastTapTime = now
        lastTapX = t.clientX
        lastTapY = t.clientY
        sx = t.clientX
        sy = t.clientY
      },
      { passive: false }
    )

    this.canvas.addEventListener(
      'touchmove',
      (e) => {
        if (pinchStartDist != null && e.touches.length === 2) {
          e.preventDefault()
          const d = distance(e.touches[0], e.touches[1])
          liveZoom = this.clampZoom(pinchStartZoom * (d / pinchStartDist))
          // Smooth CSS-transform preview; committed to a crisp re-render on end.
          this.canvas.style.transform = `scale(${liveZoom / this.zoom})`
        }
      },
      { passive: false }
    )

    this.canvas.addEventListener('touchend', (e) => {
      if (pinchStartDist != null && e.touches.length < 2) {
        pinchStartDist = null
        this.canvas.style.transform = ''
        this.canvas.style.transformOrigin = ''
        void this.applyZoom(liveZoom, pinchMidX, pinchMidY)
        return
      }
      if (consumedByDoubleTap) {
        consumedByDoubleTap = false
        return
      }
      if (sx === null || sy === null) return
      const dx = (e.changedTouches[0]?.clientX ?? sx) - sx
      const dy = (e.changedTouches[0]?.clientY ?? sy) - sy
      sx = sy = null
      // When zoomed in, a single-finger drag pans (native scroll) — don't page.
      if (this.zoom > 1.01) return
      if (Math.abs(dx) < TH || Math.abs(dx) < Math.abs(dy)) return
      if (dx < 0) this.next()
      else this.prev()
    })
  }

  next(): void {
    void this.goToPage(this.currentPage + 1, false, true)
  }

  prev(): void {
    void this.goToPage(this.currentPage - 1, false, true)
  }

  goTo(href: string): void {
    // Used by external TOC clicks; format mirrors handleTocClick().
    void this.handleTocClick(href)
  }

  toc(): TocItem[] {
    return []
  }

  // === Read-aloud (Furlough TTS bridge) ===

  hasReadableText(): boolean {
    return true
  }

  // PDF pages render to a canvas (no selectable DOM text), so we can read aloud
  // but can't highlight without building a text-layer overlay first.
  canHighlight(): boolean {
    return false
  }

  // One "unit" per page: the page's text layer. Empty for scanned/image PDFs (no
  // text layer) — the app surfaces that as "no readable text" rather than silence.
  async ttsBeginSection(): Promise<{ texts: string[]; start: number }> {
    if (!this.pdf) return { texts: [], start: 0 }
    try {
      const page = await this.pdf.getPage(this.currentPage)
      const tc = await page.getTextContent()
      const text = tc.items
        .map((it: any) => (typeof it?.str === 'string' ? it.str : ''))
        .join(' ')
        .replace(/\s+/g, ' ')
        .trim()
      return { texts: text ? [text] : [], start: 0 }
    } catch {
      return { texts: [], start: 0 }
    }
  }

  async ttsAdvanceSection(): Promise<boolean> {
    if (!this.pdf) return false
    if (this.currentPage >= this.numPages) return false
    await this.goToPage(this.currentPage + 1, false, true)
    return true
  }

  async ttsHighlightUnit(_index: number): Promise<void> {
    /* no text layer to highlight */
  }

  async ttsHighlightWord(_index: number, _start: number, _end: number): Promise<void> {
    /* no text layer to highlight */
  }

  async ttsClearHighlight(): Promise<void> {
    /* nothing to clear */
  }

  destroy(): void {
    this.renderTask?.cancel()
    void this.loadingTask?.destroy()
    this.pdf = null
    this.canvas.remove()
    this.zoomControls?.remove()
    this.container?.classList.remove('pdf-mode')
  }
}
