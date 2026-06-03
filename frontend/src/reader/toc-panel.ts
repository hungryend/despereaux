import type { TocItem } from './types'

export class TocPanel {
  private root: HTMLElement
  private toggleBtn: HTMLButtonElement | null
  private items: TocItem[] = []
  private onSelect: (href: string) => void

  constructor(rootSelector: string, toggleSelector: string, onSelect: (href: string) => void) {
    const r = document.querySelector<HTMLElement>(rootSelector)
    if (!r) throw new Error(`TocPanel: root ${rootSelector} not found`)
    this.root = r
    this.toggleBtn = document.querySelector<HTMLButtonElement>(toggleSelector)
    this.onSelect = onSelect
    this.toggleBtn?.addEventListener('click', () => this.toggle())
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') this.hide()
    })
  }

  setItems(items: TocItem[]): void {
    this.items = items
    this.render()
  }

  private render(): void {
    this.root.innerHTML = ''
    const ul = document.createElement('ul')
    ul.className = 'toc-list'
    this.renderInto(ul, this.items)
    this.root.appendChild(ul)
  }

  private renderInto(parent: HTMLUListElement, items: TocItem[]): void {
    for (const item of items) {
      const li = document.createElement('li')
      const a = document.createElement('a')
      a.href = '#'
      a.textContent = item.label.trim() || '—'
      a.addEventListener('click', (e) => {
        e.preventDefault()
        this.onSelect(item.href)
        this.hide()
      })
      li.appendChild(a)
      if (item.subitems && item.subitems.length > 0) {
        const sub = document.createElement('ul')
        this.renderInto(sub, item.subitems)
        li.appendChild(sub)
      }
      parent.appendChild(li)
    }
  }

  toggle(): void {
    this.root.hidden ? this.show() : this.hide()
  }

  show(): void {
    this.root.hidden = false
  }

  hide(): void {
    this.root.hidden = true
  }
}
