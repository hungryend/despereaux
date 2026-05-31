import './reader.css'
import { EpubReader } from './epub-reader'
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
    case 'cbz':
    case 'cbr':
      renderUnsupported(`${cfg.format.toUpperCase()} reader arrives in Phase 2.`)
      return
    default:
      renderUnsupported(`Unsupported format: ${cfg.format}`)
      return
  }

  try {
    await reader.start()
    wireNavButtons(reader)
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
