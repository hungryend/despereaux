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

interface PdfPosition {
  page: number
}

export class PdfReader implements Reader {
  private pdf: pdfjsLib.PDFDocumentProxy | null = null
  private tracker: ProgressTracker
  private tocPanel: TocPanel | null = null
  private container: HTMLElement | null = null
  private canvas: HTMLCanvasElement
  private renderTask: pdfjsLib.RenderTask | null = null
  private currentPage = 1
  private numPages = 0
  private lastSavedPage = -1

  constructor(private bootstrap: BookBootstrap) {
    this.tracker = new ProgressTracker(bootstrap.progressUrl)
    this.canvas = document.createElement('canvas')
    this.canvas.className = 'pdf-canvas'
  }

  async start(): Promise<void> {
    const root = document.querySelector<HTMLElement>('#reader-root')
    if (!root) throw new Error('reader-root element missing')
    this.container = root
    root.classList.add('pdf-mode')
    root.appendChild(this.canvas)

    const loadingTask = pdfjsLib.getDocument({
      url: this.bootstrap.fileUrl,
      withCredentials: true,
      // Range requests + streaming so opening a 200MB book doesn't download
      // the whole thing before showing page 1.
      disableAutoFetch: false,
      disableStream: false,
      rangeChunkSize: 65536,
    })
    this.pdf = await loadingTask.promise
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
    await this.goToPage(startPage)
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
      await this.goToPage(pageIndex + 1)
    } catch (e) {
      console.warn('PDF TOC navigation failed', e)
    }
  }

  private async goToPage(pageNum: number, force = false): Promise<void> {
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

    const page = await this.pdf.getPage(pageNum)

    // Fit-to-width based on the container's current size.
    const containerWidth = this.container.clientWidth
    const containerHeight = this.container.clientHeight
    const baseViewport = page.getViewport({ scale: 1 })
    const scaleX = containerWidth / baseViewport.width
    const scaleY = containerHeight / baseViewport.height
    const scale = Math.min(scaleX, scaleY) * (window.devicePixelRatio || 1)
    const viewport = page.getViewport({ scale })

    this.canvas.width = viewport.width
    this.canvas.height = viewport.height
    // CSS size so DPR scaling looks right.
    this.canvas.style.width = `${viewport.width / (window.devicePixelRatio || 1)}px`
    this.canvas.style.height = `${viewport.height / (window.devicePixelRatio || 1)}px`

    const ctx = this.canvas.getContext('2d')
    if (!ctx) return

    this.renderTask = page.render({ canvasContext: ctx, viewport })
    try {
      await this.renderTask.promise
    } catch (e: any) {
      if (e?.name !== 'RenderingCancelledException') throw e
    } finally {
      this.renderTask = null
    }

    this.savePositionIfChanged()
  }

  private savePositionIfChanged(): void {
    if (Math.abs(this.currentPage - this.lastSavedPage) < SAVE_PAGE_DELTA) return
    this.lastSavedPage = this.currentPage
    const percent = this.numPages > 0 ? this.currentPage / this.numPages : 0
    this.tracker.schedule(JSON.stringify({ page: this.currentPage }), percent)
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
        void this.goToPage(1)
      } else if (e.key === 'End') {
        void this.goToPage(this.numPages)
      }
    }
    document.addEventListener('keyup', onKey)

    // Touch swipes on the canvas.
    let sx: number | null = null
    let sy: number | null = null
    const TH = 50
    this.canvas.addEventListener(
      'touchstart',
      (e) => {
        if (e.touches.length !== 1) return
        sx = e.touches[0].clientX
        sy = e.touches[0].clientY
      },
      { passive: true }
    )
    this.canvas.addEventListener('touchend', (e) => {
      if (sx === null || sy === null) return
      const dx = (e.changedTouches[0]?.clientX ?? sx) - sx
      const dy = (e.changedTouches[0]?.clientY ?? sy) - sy
      sx = sy = null
      if (Math.abs(dx) < TH || Math.abs(dx) < Math.abs(dy)) return
      if (dx < 0) this.next()
      else this.prev()
    })
  }

  next(): void {
    void this.goToPage(this.currentPage + 1)
  }

  prev(): void {
    void this.goToPage(this.currentPage - 1)
  }

  goTo(href: string): void {
    // Used by external TOC clicks; format mirrors handleTocClick().
    void this.handleTocClick(href)
  }

  toc(): TocItem[] {
    return []
  }

  destroy(): void {
    this.renderTask?.cancel()
    void this.pdf?.destroy()
    this.canvas.remove()
    this.container?.classList.remove('pdf-mode')
  }
}
