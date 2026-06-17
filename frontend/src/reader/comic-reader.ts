import { ProgressTracker } from './progress-tracker'
import type { BookBootstrap, Reader, TocItem } from './types'

// Comic pages are raster images served one-per-request from the archive.
// Mirrors the PDF reader's pagination + zoom; progress uses the same
// {"page": N} shape so the continue-reading shelf + cross-device resume work.
const SAVE_PAGE_DELTA = 1
const MIN_ZOOM = 1
const MAX_ZOOM = 5
const ZOOM_STEP = 1.25
const ZOOM_KEY = 'despereaux:comicZoom'

interface ComicPosition {
  page: number
}

export class ComicReader implements Reader {
  private tracker: ProgressTracker
  private container: HTMLElement | null = null
  private img: HTMLImageElement
  private currentPage = 1
  private numPages = 0
  private lastSavedPage = -1
  private zoom = 1
  private fitScale = 1
  private zoomControls: HTMLElement | null = null
  private zoomLabel: HTMLElement | null = null
  private readonly pageBase: string

  constructor(private bootstrap: BookBootstrap) {
    this.tracker = new ProgressTracker(bootstrap.progressUrl)
    this.img = document.createElement('img')
    this.img.className = 'comic-page'
    this.img.alt = ''
    this.img.decoding = 'async'
    // fileUrl is ".../{id}/file" — page endpoint is ".../{id}/page/{n}" (0-based).
    this.pageBase = bootstrap.fileUrl.replace(/\/file$/, '/page/')
    this.zoom = this.loadZoom()
  }

  async start(): Promise<void> {
    const root = document.querySelector<HTMLElement>('#reader-root')
    if (!root) throw new Error('reader-root element missing')
    this.container = root
    root.classList.add('pdf-mode', 'comic-mode')
    root.appendChild(this.img)

    try {
      const res = await fetch(this.bootstrap.manifestUrl, { credentials: 'same-origin' })
      const data = await res.json()
      this.numPages = Number(data?.page_count) || 0
    } catch {
      this.numPages = 0
    }

    const saved = await this.tracker.load()
    let startPage = 1
    if (saved?.position) {
      try {
        const pos = JSON.parse(saved.position) as ComicPosition
        const ok = pos.page >= 1 && (this.numPages === 0 || pos.page <= this.numPages)
        if (typeof pos.page === 'number' && ok) startPage = pos.page
      } catch {
        /* malformed — start at 1 */
      }
    }

    this.img.addEventListener('load', () => this.sizeImage(/*recenter*/ true))

    this.lastSavedPage = startPage
    this.goToPage(startPage)
    this.createZoomControls()
    this.attachNavigation()
    this.attachLifecycleSavers()

    window.addEventListener('resize', () => this.sizeImage(false))
  }

  private attachLifecycleSavers(): void {
    const beaconNow = () => this.tracker.beacon()
    window.addEventListener('beforeunload', beaconNow)
    window.addEventListener('pagehide', beaconNow)
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') beaconNow()
    })
  }

  private pageUrl(page1: number): string {
    return `${this.pageBase}${page1 - 1}`
  }

  private goToPage(pageNum: number): void {
    if (pageNum < 1) return
    if (this.numPages > 0 && pageNum > this.numPages) return
    this.currentPage = pageNum
    this.img.src = this.pageUrl(pageNum)
    this.savePositionIfChanged()
    this.preload(pageNum + 1)
    this.preload(pageNum - 1)
  }

  private preload(page1: number): void {
    if (page1 < 1 || (this.numPages > 0 && page1 > this.numPages)) return
    const im = new Image()
    im.src = this.pageUrl(page1)
  }

  private sizeImage(recenter: boolean): void {
    if (!this.container || !this.img.naturalWidth) return
    const cw = this.container.clientWidth
    const ch = this.container.clientHeight
    this.fitScale = Math.min(cw / this.img.naturalWidth, ch / this.img.naturalHeight)
    const w = this.img.naturalWidth * this.fitScale * this.zoom
    const h = this.img.naturalHeight * this.fitScale * this.zoom
    this.img.style.width = `${w}px`
    this.img.style.height = `${h}px`
    if (recenter) {
      this.container.scrollLeft = Math.max(0, (w - cw) / 2)
      this.container.scrollTop = 0
    }
  }

  private savePositionIfChanged(): void {
    if (Math.abs(this.currentPage - this.lastSavedPage) < SAVE_PAGE_DELTA) return
    this.lastSavedPage = this.currentPage
    const percent = this.numPages > 0 ? this.currentPage / this.numPages : 0
    this.tracker.schedule(JSON.stringify({ page: this.currentPage }), percent)
  }

  // === Zoom (mirrors pdf-reader; for raster pages this just rescales the img) ===

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

  private applyZoom(target: number, screenX: number, screenY: number): void {
    if (!this.container) return
    const newZoom = this.clampZoom(target)
    if (Math.abs(newZoom - this.zoom) < 0.001) return
    const root = this.container
    const ratio = newZoom / this.zoom
    const rect = root.getBoundingClientRect()
    const fx = screenX - rect.left
    const fy = screenY - rect.top
    const newLeft = (root.scrollLeft + fx) * ratio - fx
    const newTop = (root.scrollTop + fy) * ratio - fy
    this.zoom = newZoom
    this.persistZoom()
    this.updateZoomUi()
    this.sizeImage(false)
    root.scrollLeft = Math.max(0, newLeft)
    root.scrollTop = Math.max(0, newTop)
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
    this.applyZoom(this.zoom * ZOOM_STEP, c.x, c.y)
  }

  zoomOut(): void {
    const c = this.viewportCenter()
    this.applyZoom(this.zoom / ZOOM_STEP, c.x, c.y)
  }

  resetZoom(): void {
    const c = this.viewportCenter()
    this.applyZoom(1, c.x, c.y)
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
        this.goToPage(1)
      } else if (e.key === 'End') {
        if (this.numPages) this.goToPage(this.numPages)
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

    this.img.addEventListener(
      'touchstart',
      (e) => {
        if (e.touches.length === 2) {
          sx = null
          pinchStartDist = distance(e.touches[0], e.touches[1])
          pinchStartZoom = this.zoom
          liveZoom = this.zoom
          pinchMidX = (e.touches[0].clientX + e.touches[1].clientX) / 2
          pinchMidY = (e.touches[0].clientY + e.touches[1].clientY) / 2
          const r = this.img.getBoundingClientRect()
          this.img.style.transformOrigin = `${pinchMidX - r.left}px ${pinchMidY - r.top}px`
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
          consumedByDoubleTap = true
          lastTapTime = 0
          sx = null
          e.preventDefault()
          this.applyZoom(this.zoom > 1.01 ? 1 : 2, t.clientX, t.clientY)
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

    this.img.addEventListener(
      'touchmove',
      (e) => {
        if (pinchStartDist != null && e.touches.length === 2) {
          e.preventDefault()
          const d = distance(e.touches[0], e.touches[1])
          liveZoom = this.clampZoom(pinchStartZoom * (d / pinchStartDist))
          this.img.style.transform = `scale(${liveZoom / this.zoom})`
        }
      },
      { passive: false }
    )

    this.img.addEventListener('touchend', (e) => {
      if (pinchStartDist != null && e.touches.length < 2) {
        pinchStartDist = null
        this.img.style.transform = ''
        this.img.style.transformOrigin = ''
        this.applyZoom(liveZoom, pinchMidX, pinchMidY)
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
      if (this.zoom > 1.01) return // panning a zoomed page, don't flip
      if (Math.abs(dx) < TH || Math.abs(dx) < Math.abs(dy)) return
      if (dx < 0) this.next()
      else this.prev()
    })
  }

  next(): void {
    this.goToPage(this.currentPage + 1)
  }

  prev(): void {
    this.goToPage(this.currentPage - 1)
  }

  goTo(_href: string): void {
    /* comics have no TOC */
  }

  toc(): TocItem[] {
    return []
  }

  // === Read-aloud (Furlough TTS bridge) — comics are images, no text. ===

  hasReadableText(): boolean {
    return false
  }

  canHighlight(): boolean {
    return false
  }

  async ttsBeginSection(): Promise<{ texts: string[]; start: number }> {
    return { texts: [], start: 0 }
  }

  async ttsAdvanceSection(): Promise<boolean> {
    return false
  }

  async ttsHighlightUnit(_index: number): Promise<void> {}

  async ttsHighlightWord(_index: number, _start: number, _end: number): Promise<void> {}

  async ttsClearHighlight(): Promise<void> {}

  destroy(): void {
    this.img.remove()
    this.zoomControls?.remove()
    this.container?.classList.remove('pdf-mode', 'comic-mode')
  }
}
