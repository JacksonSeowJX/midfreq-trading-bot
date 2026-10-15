/**
 * Pure functions behind the Guide page: the headings of the guide for its table of contents,
 * with the ids rehype-slug gives the rendered headings (GitHub's anchors, so a link such as
 * #glossary works both on GitHub and in the app), and what kind of link a guide link is.
 */
import GithubSlugger from 'github-slugger'

export interface GuideHeading {
  depth: number
  text: string
  id: string
}

/** A heading's text as it reads once rendered: links, code and emphasis markers dropped. */
export function headingText(markdown: string): string {
  return markdown
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .trim()
}

const FENCE = /^ {0,3}(`{3,}|~{3,})/
const ATX = /^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$/

/**
 * Every ATX heading (# to ######) outside fenced code, in order. Ids are slugged in document
 * order over all levels, as rehype-slug does, so repeated headings get the same -1, -2 suffixes.
 */
export function guideHeadings(markdown: string): GuideHeading[] {
  const slugger = new GithubSlugger()
  const out: GuideHeading[] = []
  let fence: string | null = null
  for (const line of markdown.split(/\r?\n/)) {
    const f = FENCE.exec(line)
    if (f) {
      const marker = f[1]!
      if (fence === null) fence = marker[0]!.repeat(marker.length)
      else if (marker[0] === fence[0] && marker.length >= fence.length) fence = null
      continue
    }
    if (fence !== null) continue
    const m = ATX.exec(line)
    if (!m) continue
    const text = headingText(m[2]!)
    out.push({ depth: m[1]!.length, text, id: slugger.slug(text) })
  }
  return out
}

/** The table of contents: the sections (##) and their subsections (###). */
export function tocEntries(headings: GuideHeading[]): GuideHeading[] {
  return headings.filter((h) => h.depth === 2 || h.depth === 3)
}

export type GuideLink = { kind: 'hash'; id: string } | { kind: 'app'; to: string } | { kind: 'external'; href: string }

/**
 * Where a link in the guide goes: a section of the guide (#id), a page of this app (/path,
 * a router link; on GitHub such a link is dead, so its text names the page), or elsewhere.
 */
export function classifyLink(href: string): GuideLink {
  if (href.startsWith('#')) return { kind: 'hash', id: decodeURIComponent(href.slice(1)) }
  if (href.startsWith('/') && !href.startsWith('//')) return { kind: 'app', to: href }
  return { kind: 'external', href }
}

/** The app pages the guide links to that open a run form prefilled: kind and preset id. */
export function presetLinks(markdown: string): { path: string; preset: string }[] {
  const out: { path: string; preset: string }[] = []
  for (const m of markdown.matchAll(/\]\((\/[a-z-]+)\?preset=([a-z0-9-]+)\)/g)) out.push({ path: m[1]!, preset: m[2]! })
  return out
}
