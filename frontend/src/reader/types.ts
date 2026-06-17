export interface BookBootstrap {
  id: string
  title: string
  format: 'epub' | 'pdf' | 'cbz' | 'cbr' | 'mobi' | 'azw' | 'azw3'
  fileUrl: string
  manifestUrl: string
  progressUrl: string
}

declare global {
  interface Window {
    DESPEREAUX_BOOK: BookBootstrap
  }
}

export interface SavedProgress {
  book_id: string
  position: string
  percent: number
  updated_at: string
}

export interface TocItem {
  label: string
  href: string
  subitems?: TocItem[]
}

export interface Reader {
  start(): Promise<void>
  next(): void
  prev(): void
  goTo(href: string): void
  destroy(): void
  toc(): TocItem[]
  // Read-aloud (Furlough TTS bridge). A section is tokenized into ordered "units"
  // (sentences for EPUB, the whole page for PDF); the app speaks them in order and
  // the reader highlights + turns pages to follow. Comics expose nothing.
  hasReadableText(): boolean
  canHighlight(): boolean
  ttsBeginSection(): Promise<{ texts: string[]; start: number }>
  ttsAdvanceSection(): Promise<boolean>
  ttsHighlightUnit(index: number): Promise<void>
  ttsHighlightWord(index: number, start: number, end: number): Promise<void>
  ttsClearHighlight(): Promise<void>
}
