/**
 * Rewrite absolute asset URLs in the built HTML to relative ones.
 *
 * The built site is meant to open by double-clicking `index.html`, with no
 * server anywhere. Astro emits `/assets/…` regardless of `base` (see
 * astro.config.mjs for the values tried), and an absolute path resolves
 * against the filesystem root when the page is opened as a file — so the page
 * renders unstyled and inert, but only for the person you sent it to, because
 * it works perfectly over `http://localhost`.
 *
 * Single page, single directory level, so a relative path is always `./`.
 */
import { readdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const DIST = new URL('../dist/', import.meta.url).pathname;

// Only in an attribute, so a `/assets/` appearing inside page text or inside a
// JSON blob of company data is left alone.
const ABSOLUTE_ASSET = /(\s(?:src|href)=")\/+(\.\/)*assets\//g;

// Stylesheets have the same problem in a different syntax. Vite rewrites
// Leaflet's `url(images/layers.png)` to `url(/assets/layers.<hash>.png)` —
// absolute, so the control icons vanish when the folder is opened as a file.
// A stylesheet in `assets/` sits beside them, so the relative form is the bare
// filename.
const ABSOLUTE_CSS_URL = /url\(\/+(?:\.\/)*assets\//g;

const entries = await readdir(DIST, { recursive: true });
let rewritten = 0;
let scanned = 0;

for (const file of entries) {
  const isHtml = file.endsWith('.html');
  const isCss = file.endsWith('.css');
  if (!isHtml && !isCss) continue;

  scanned += 1;
  const path = join(DIST, file);
  const before = await readFile(path, 'utf8');
  const after = isHtml
    ? before.replace(ABSOLUTE_ASSET, '$1./assets/')
    : before.replace(ABSOLUTE_CSS_URL, 'url(');
  if (after !== before) {
    await writeFile(path, after);
    rewritten += 1;
  }
}

console.log(`relativised asset URLs in ${rewritten}/${scanned} file(s)`);
