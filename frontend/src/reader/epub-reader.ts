import ePub, { Book, Rendition } from 'epubjs'

import { ProgressTracker } from './progress-tracker'
import { TocPanel } from './toc-panel'
import type { BookBootstrap, Reader, TocItem } from './types'

const PROGRESS_SAVE_PERCENT_DELTA = 0.0005

export class EpubReader implements Reader {
  private book: Book
  private rendition: Rendition | null = null
  private tracker: ProgressTracker
  private tocPanel: TocPanel | null = null
  private locationsReady = false
  private lastSavedPercent = -1

  constructor(bootstrap: BookBootstrap) {
    this.book = ePub(bootstrap.fileUrl, { openAs: 'epub' })
    this.tracker = new ProgressTracker(bootstrap.progressUrl)
  }

  async start(): Promise<void> {
    const root = document.querySelector<HTMLElement>('#reader-root')
    if (!root) throw new Error('reader-root element missing')

    this.rendition = this.book.renderTo(root, {
      width: '100%',
      height: '100%',
      flow: 'paginated',
      spread: 'none', // single page; reader-root is centered + max-widthed via CSS
      allowScriptedContent: false,
    })

    this.applyReaderTheme()

    // Load saved progress + TOC in parallel; both block the first paint of the right page.
    const [saved] = await Promise.all([
      this.tracker.load(),
      this.book.ready,
    ])

    if (saved?.position) {
      await this.rendition.display(saved.position)
    } else {
      await this.rendition.display()
    }

    this.book.locations.generate(1024).then(() => {
      this.locationsReady = true
    })

    const nav = await this.book.loaded.navigation
    const toc = this.normaliseToc(nav.toc as TocItem[])
    this.tocPanel = new TocPanel('#toc-panel', '#toc-toggle', (href) => this.goTo(href))
    this.tocPanel.setItems(toc)

    this.rendition.on('relocated', (loc: any) => {
      const cfi: string | undefined = loc?.start?.cfi
      if (!cfi) return
      let percent = 0
      if (this.locationsReady) {
        try {
          percent = this.book.locations.percentageFromCfi(cfi) || 0
        } catch {
          percent = 0
        }
      } else {
        const total = (this.book.spine as any).length || 1
        percent = ((loc.start.index ?? 0) + 1) / total
      }
      if (Math.abs(percent - this.lastSavedPercent) < PROGRESS_SAVE_PERCENT_DELTA) return
      this.lastSavedPercent = percent
      this.tracker.schedule(cfi, percent)
    })

    this.attachNavigation()
    window.addEventListener('beforeunload', () => {
      void this.tracker.flushNow()
    })
  }

  private applyReaderTheme(): void {
    if (!this.rendition) return
    this.rendition.themes.register('dark', {
      body: {
        background: '#0e0f12',
        color: '#e9ecf1',
        'font-family': 'Georgia, "Iowan Old Style", serif',
        'line-height': '1.55',
      },
      a: { color: '#d4a64b' },
      'p, li, blockquote, dd, dt, h1, h2, h3, h4, h5, h6': {
        color: '#e9ecf1',
      },
    })
    this.rendition.themes.select('dark')
  }

  private normaliseToc(items: TocItem[]): TocItem[] {
    return items.map((it) => ({
      label: it.label,
      href: it.href,
      subitems: it.subitems ? this.normaliseToc(it.subitems) : undefined,
    }))
  }

  private attachNavigation(): void {
    if (!this.rendition) return

    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight' || e.key === 'PageDown' || e.key === ' ') {
        this.next()
        e.preventDefault()
      } else if (e.key === 'ArrowLeft' || e.key === 'PageUp') {
        this.prev()
        e.preventDefault()
      }
    }
    document.addEventListener('keyup', handleKey)
    this.rendition.on('keyup', handleKey)

    // Touch swipes (basic, on the rendered iframe + on the host root).
    let touchStartX: number | null = null
    let touchStartY: number | null = null
    const TH = 50

    const onTouchStart = (e: TouchEvent) => {
      if (e.touches.length !== 1) return
      touchStartX = e.touches[0].clientX
      touchStartY = e.touches[0].clientY
    }
    const onTouchEnd = (e: TouchEvent) => {
      if (touchStartX === null || touchStartY === null) return
      const dx = (e.changedTouches[0]?.clientX ?? touchStartX) - touchStartX
      const dy = (e.changedTouches[0]?.clientY ?? touchStartY) - touchStartY
      touchStartX = touchStartY = null
      if (Math.abs(dx) < TH || Math.abs(dx) < Math.abs(dy)) return
      if (dx < 0) this.next()
      else this.prev()
    }

    const root = document.querySelector<HTMLElement>('#reader-root')!
    root.addEventListener('touchstart', onTouchStart, { passive: true })
    root.addEventListener('touchend', onTouchEnd)
    this.rendition.hooks.content.register((contents: any) => {
      const doc = contents.document as Document
      doc.addEventListener('touchstart', onTouchStart, { passive: true })
      doc.addEventListener('touchend', onTouchEnd)
      doc.addEventListener('keyup', handleKey as any)
    })

    // Click on right/left edge of the viewport navigates pages.
    root.addEventListener('click', (e) => {
      const rect = root.getBoundingClientRect()
      const x = e.clientX - rect.left
      if (x < rect.width * 0.2) this.prev()
      else if (x > rect.width * 0.8) this.next()
    })
  }

  next(): void {
    this.rendition?.next()
  }

  prev(): void {
    this.rendition?.prev()
  }

  goTo(href: string): void {
    this.rendition?.display(href)
  }

  toc(): TocItem[] {
    return []
  }

  destroy(): void {
    this.rendition?.destroy()
    this.book.destroy()
  }
}
