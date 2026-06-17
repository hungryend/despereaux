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
  private currentSectionIndex = 0

  // Read-aloud state: the current section tokenized into sentences, with a map
  // from character offset (in the concatenated section text) back to the DOM text
  // node, so we can highlight + page-follow as TTS reads.
  private ttsSegs: { node: Text; start: number }[] = []
  private ttsUnits: { text: string; startOff: number; endOff: number }[] = []
  private ttsDoc: Document | null = null
  private ttsWin: (Window & typeof globalThis) | null = null
  private ttsStyledDocs = new WeakSet<Document>()

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

    // Restore the saved position. Progress is keyed by (user, book) and shared
    // across formats, so `saved.position` may be a PDF position (`{"page":N}`)
    // or a CFI from an earlier EPUB build that conversion has since replaced —
    // either resolves to nothing here and makes epub.js' display() reject with
    // "No Section Found". Only restore a real epubcfi(), and fall back to the
    // first page if it's foreign or no longer resolves, rather than failing the
    // whole reader. (Mirrors the PDF reader's guard on its saved position.)
    const cfi = saved?.position
    if (cfi && cfi.startsWith('epubcfi(')) {
      try {
        await this.rendition.display(cfi)
      } catch {
        await this.rendition.display()
      }
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
      this.currentSectionIndex = loc?.start?.index ?? this.currentSectionIndex
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
    this.attachLifecycleSavers()
  }

  private attachLifecycleSavers(): void {
    // beforeunload alone isn't reliable (esp. on mobile). visibilitychange +
    // pagehide cover background-tab / app-switch / close cases too.
    const beaconNow = () => this.tracker.beacon()
    window.addEventListener('beforeunload', beaconNow)
    window.addEventListener('pagehide', beaconNow)
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') beaconNow()
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

  // === Read-aloud (Furlough TTS bridge) ===

  hasReadableText(): boolean {
    return true
  }

  canHighlight(): boolean {
    return true
  }

  // Tokenize the current section into sentences (each with its char-offset span in
  // the concatenated section text) so the app can speak them in order and we can
  // highlight + turn pages to follow. Returns the texts plus the index of the first
  // sentence already on screen, so "Listen" starts roughly where the reader is.
  async ttsBeginSection(): Promise<{ texts: string[]; start: number }> {
    this.clearTtsHighlight()
    this.ttsSegs = []
    this.ttsUnits = []
    this.ttsDoc = null
    this.ttsWin = null
    if (!this.rendition) return { texts: [], start: 0 }
    try {
      const raw = this.rendition.getContents() as unknown
      const contents = (Array.isArray(raw) ? raw[0] : raw) as any
      const doc: Document | undefined = contents?.document
      const root: HTMLElement | undefined = contents?.content ?? doc?.body
      if (!doc || !root) return { texts: [], start: 0 }
      this.ttsDoc = doc
      this.ttsWin = doc.defaultView as Window & typeof globalThis
      this.injectHighlightStyle(doc)

      const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT)
      let full = ''
      let node: Node | null
      while ((node = walker.nextNode())) {
        const data = (node as Text).data
        if (!data) continue
        this.ttsSegs.push({ node: node as Text, start: full.length })
        full += data
      }

      for (const span of this.splitSentences(full)) {
        const slice = full.slice(span.start, span.end)
        const trimmed = slice.trim()
        if (!trimmed) continue
        const lead = slice.length - slice.trimStart().length
        const startOff = span.start + lead
        this.ttsUnits.push({ text: trimmed, startOff, endOff: startOff + trimmed.length })
      }

      let start = 0
      for (let i = 0; i < this.ttsUnits.length; i++) {
        if (this.isOffsetVisible(this.ttsUnits[i].startOff)) {
          start = i
          break
        }
      }
      return { texts: this.ttsUnits.map((u) => u.text), start }
    } catch {
      return { texts: [], start: 0 }
    }
  }

  async ttsAdvanceSection(): Promise<boolean> {
    if (!this.rendition) return false
    try {
      this.clearTtsHighlight()
      const spine = this.book.spine as any
      const len: number = spine.length ?? 0
      const nextIndex = this.currentSectionIndex + 1
      if (nextIndex >= len) return false
      const href: string | undefined = spine.get(nextIndex)?.href
      if (!href) return false
      await this.rendition.display(href)
      return true
    } catch {
      return false
    }
  }

  async ttsHighlightUnit(index: number): Promise<void> {
    const unit = this.ttsUnits[index]
    if (!unit || !this.rendition) return
    // Page-follow: turn pages forward until the sentence's start is on screen,
    // without crossing into the next section.
    try {
      const sectionAtStart = this.currentSectionIndex
      let guard = 0
      while (guard++ < 40 && !this.isOffsetVisible(unit.startOff)) {
        const before = this.offsetLeft(unit.startOff)
        await this.rendition.next()
        if (this.currentSectionIndex !== sectionAtStart) break
        if (this.offsetLeft(unit.startOff) === before) break
      }
    } catch {
      /* paging is best-effort */
    }
    this.setTtsHighlight('tts-sentence', this.rangeFor(unit.startOff, unit.endOff))
    this.setTtsHighlight('tts-word', null)
  }

  async ttsHighlightWord(index: number, start: number, end: number): Promise<void> {
    const unit = this.ttsUnits[index]
    if (!unit) return
    const from = unit.startOff + Math.max(0, start)
    const to = unit.startOff + Math.max(start + 1, end)
    this.setTtsHighlight('tts-word', this.rangeFor(from, Math.min(to, unit.endOff)))
  }

  async ttsClearHighlight(): Promise<void> {
    this.clearTtsHighlight()
  }

  private splitSentences(full: string): { start: number; end: number }[] {
    const out: { start: number; end: number }[] = []
    const enders = '.!?。！？'
    const closers = ')"’”]»‘“'
    let start = 0
    let i = 0
    while (i < full.length) {
      if (enders.indexOf(full[i]) >= 0) {
        let j = i + 1
        while (j < full.length && closers.indexOf(full[j]) >= 0) j++
        if (j >= full.length || /\s/.test(full[j])) {
          if (full.slice(start, j).trim()) out.push({ start, end: j })
          while (j < full.length && /\s/.test(full[j])) j++
          start = j
          i = j
          continue
        }
      }
      i++
    }
    if (start < full.length && full.slice(start).trim()) out.push({ start, end: full.length })
    return out
  }

  private locate(off: number): { node: Text; offset: number } | null {
    const segs = this.ttsSegs
    if (!segs.length) return null
    let lo = 0
    let hi = segs.length - 1
    let idx = 0
    while (lo <= hi) {
      const mid = (lo + hi) >> 1
      if (segs[mid].start <= off) {
        idx = mid
        lo = mid + 1
      } else {
        hi = mid - 1
      }
    }
    const seg = segs[idx]
    return { node: seg.node, offset: Math.max(0, Math.min(off - seg.start, seg.node.data.length)) }
  }

  private rangeFor(start: number, end: number): Range | null {
    const doc = this.ttsDoc
    if (!doc) return null
    const a = this.locate(start)
    const b = this.locate(end)
    if (!a || !b) return null
    try {
      const range = doc.createRange()
      range.setStart(a.node, a.offset)
      range.setEnd(b.node, b.offset)
      return range
    } catch {
      return null
    }
  }

  private offsetLeft(off: number): number {
    const range = this.rangeFor(off, off + 1)
    if (!range) return NaN
    try {
      return range.getBoundingClientRect().left
    } catch {
      return NaN
    }
  }

  private isOffsetVisible(off: number): boolean {
    const range = this.rangeFor(off, off + 1)
    const win = this.ttsWin
    if (!range || !win) return true
    try {
      const rect = range.getBoundingClientRect()
      const w = win.innerWidth || 0
      return rect.left >= -1 && rect.left < w && rect.bottom >= -1
    } catch {
      return true
    }
  }

  private injectHighlightStyle(doc: Document): void {
    if (this.ttsStyledDocs.has(doc)) return
    try {
      const style = doc.createElement('style')
      style.textContent =
        '::highlight(tts-sentence){background-color:rgba(212,166,75,.22);}' +
        '::highlight(tts-word){background-color:rgba(212,166,75,.6);color:#0e0f12;}'
      ;(doc.head || doc.documentElement).appendChild(style)
      this.ttsStyledDocs.add(doc)
    } catch {
      /* highlight is best-effort */
    }
  }

  // Uses the CSS Custom Highlight API (no DOM mutation; handles ranges that cross
  // element boundaries). Silently no-ops on engines that lack it.
  private setTtsHighlight(name: string, range: Range | null): void {
    const win = this.ttsWin as any
    if (!win || !win.CSS || !win.CSS.highlights || typeof win.Highlight === 'undefined') return
    try {
      if (!range) {
        win.CSS.highlights.delete(name)
        return
      }
      win.CSS.highlights.set(name, new win.Highlight(range))
    } catch {
      /* ignore */
    }
  }

  private clearTtsHighlight(): void {
    this.setTtsHighlight('tts-sentence', null)
    this.setTtsHighlight('tts-word', null)
  }

  destroy(): void {
    this.clearTtsHighlight()
    this.rendition?.destroy()
    this.book.destroy()
  }
}
