import type { SavedProgress } from './types'

const DEBOUNCE_MS = 5000

export class ProgressTracker {
  private timer: number | null = null
  private pending: { position: string; percent: number } | null = null
  private inFlight = false

  constructor(private progressUrl: string) {}

  async load(): Promise<SavedProgress | null> {
    try {
      const res = await fetch(this.progressUrl, { credentials: 'same-origin' })
      if (!res.ok) return null
      const data = (await res.json()) as SavedProgress | null
      return data
    } catch {
      return null
    }
  }

  schedule(position: string, percent: number): void {
    this.pending = { position, percent }
    if (this.timer !== null) return
    this.timer = window.setTimeout(() => this.flush(), DEBOUNCE_MS)
  }

  private async flush(): Promise<void> {
    this.timer = null
    if (!this.pending || this.inFlight) return
    const body = this.pending
    this.pending = null
    this.inFlight = true
    try {
      await fetch(this.progressUrl, {
        method: 'PUT',
        credentials: 'same-origin',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
        keepalive: true,
      })
    } catch (e) {
      console.warn('progress save failed', e)
    } finally {
      this.inFlight = false
      if (this.pending && this.timer === null) {
        this.timer = window.setTimeout(() => this.flush(), DEBOUNCE_MS)
      }
    }
  }

  async flushNow(): Promise<void> {
    if (this.timer !== null) {
      window.clearTimeout(this.timer)
      this.timer = null
    }
    await this.flush()
  }

  /**
   * Fire-and-forget save designed for page-hide / tab-close paths. Uses
   * sendBeacon when available (browser guarantees delivery even if the page
   * is being torn down), falls back to keepalive fetch. Doesn't await.
   */
  beacon(): void {
    if (!this.pending) return
    const body = JSON.stringify(this.pending)
    this.pending = null
    if (this.timer !== null) {
      window.clearTimeout(this.timer)
      this.timer = null
    }
    // sendBeacon only does POST, and our /progress endpoint is PUT. Using
    // fetch with `keepalive: true` is the spec-blessed equivalent that
    // preserves the HTTP verb and reaches the server even after page unload.
    try {
      void fetch(this.progressUrl, {
        method: 'PUT',
        credentials: 'same-origin',
        headers: { 'content-type': 'application/json' },
        body,
        keepalive: true,
      })
    } catch {
      /* page is gone — nothing else we can do */
    }
  }
}
