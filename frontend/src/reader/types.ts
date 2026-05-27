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
}
