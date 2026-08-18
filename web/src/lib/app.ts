/**
 * Filtering, the map, the list, and the detail view.
 *
 * One filter state drives everything. The map and the list are two renderings
 * of the same answer, so they cannot disagree about what is selected — which
 * is the whole reason they are on one page.
 */
import L from 'leaflet';
import 'leaflet.markercluster';

import type { Company } from './types';

const companies: Company[] = (window as any).__COMPANIES__ ?? [];

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T | null;

const controls = {
  tier: $<HTMLSelectElement>('f-tier'),
  canton: $<HTMLSelectElement>('f-canton'),
  service: $<HTMLSelectElement>('f-service'),
  industry: $<HTMLSelectElement>('f-industry'),
  size: $<HTMLSelectElement>('f-size'),
  has: $<HTMLSelectElement>('f-has'),
  q: $<HTMLInputElement>('f-q'),
  own: $<HTMLInputElement>('f-own'),
  sort: $<HTMLSelectElement>('f-sort'),
};

/**
 * Companies clicked on the map. The map answers "who is here"; the list is
 * where that answer is readable, so a click narrows the list rather than
 * relying on a popup to hold the detail.
 */
let spotlight: Set<number> | null = null;
let spotlightLabel = '';

/**
 * Your own companies first, then whatever sort is selected.
 *
 * The pin is unconditional. They were previously sorted like everything else,
 * which buried them among eighty tier-1 entries — the one comparison the
 * dataset exists to support was the hardest one to actually perform.
 */
function ordered(rows: Company[]): Company[] {
  const mode = controls.sort?.value ?? 'tier';
  return [...rows].sort((a, b) => {
    if (a.is_own !== b.is_own) return a.is_own ? -1 : 1;
    if (mode === 'traction') {
      const diff = b.traction.points - a.traction.points;
      if (diff !== 0) return diff;
    } else if (mode === 'evidence') {
      const weight = (c: Company) =>
        c.case_studies.length + c.clients.length + c.products.length + c.mentions.length;
      const diff = weight(b) - weight(a);
      if (diff !== 0) return diff;
    } else if (mode === 'size') {
      const diff = (b.headcount_est ?? -1) - (a.headcount_est ?? -1);
      if (diff !== 0) return diff;
    } else {
      const at = a.tier ?? 9;
      const bt = b.tier ?? 9;
      if (at !== bt) return at - bt;
    }
    return a.canonical_name.localeCompare(b.canonical_name);
  });
}

/** Text a search should look through — everything the company says about itself. */
function haystack(c: Company): string {
  return [
    c.canonical_name,
    c.domain,
    c.one_liner ?? '',
    c.city ?? '',
    ...c.offerings.map((o) => o.label),
    ...c.clients.map((x) => x.client_name),
    ...c.products.map((p) => p.name),
    ...c.case_studies.map((s) => s.title),
    ...c.mentions.map((m) => `${m.headline} ${m.outlet ?? ''}`),
    ...c.tags.map((t) => t.value),
    ...Object.values(c.attributes ?? {}).flat(),
  ]
    .join(' ')
    .toLowerCase();
}

function matches(c: Company): boolean {
  // Default is 'in': companies the classifier placed in the segment. The page
  // used to open on everything, so 109 candidates it had explicitly rejected —
  // meetup.com, ey.com, a Polish software house — were counted as companies
  // with an unknown location, and the map reported 99 missing addresses that
  // were mostly firms with no business being on the map at all.
  const tier = controls.tier?.value ?? 'in';
  if (tier === 'in' && c.tier === null) return false;
  if (tier === 'none' && c.tier !== null) return false;
  if (tier && tier !== 'none' && tier !== 'in' && String(c.tier) !== tier) return false;

  const canton = controls.canton?.value ?? '';
  if (canton && c.canton !== canton) return false;

  const service = controls.service?.value ?? '';
  if (service && !c.tags.some((t) => t.facet === 'service_type' && t.value === service))
    return false;

  const industry = controls.industry?.value ?? '';
  if (
    industry &&
    !c.tags.some((t) => t.facet === 'vertical' && t.value === industry) &&
    !c.case_studies.some((s) => s.industry === industry) &&
    !c.clients.some((x) => x.industry === industry)
  )
    return false;

  const size = controls.size?.value ?? '';
  if (size && c.size_band !== size) return false;

  const has = controls.has?.value ?? '';
  if (has === 'case_studies' && c.case_studies.length === 0) return false;
  if (has === 'clients' && c.clients.length === 0) return false;
  if (has === 'products' && c.products.length === 0) return false;

  if (controls.own?.checked && !c.is_own) return false;

  const q = (controls.q?.value ?? '').trim().toLowerCase();
  if (q && !haystack(c).includes(q)) return false;

  return true;
}

// --- map --------------------------------------------------------------------

const map = L.map('map', { scrollWheelZoom: true }).setView([46.82, 8.23], 8);

// Leaflet measures its container once, at construction. This one lives in a
// CSS grid inside a panel, and the grid settles after that — web font swaps
// in, the sticky toolbar takes its height, the column resolves its width — so
// the map is routinely built against a box that no longer exists and paints
// either nothing or a grey rectangle with tiles in the wrong places.
//
// `invalidateSize` re-measures. Once after layout for the initial settle, and
// then whenever the container actually changes size, which also covers a
// window resize and the panel reflowing at a breakpoint.
const remeasure = () => map.invalidateSize({ animate: false });
requestAnimationFrame(remeasure);
window.addEventListener('load', remeasure);
const mapEl = document.getElementById('map');
if (mapEl && 'ResizeObserver' in window) {
  new ResizeObserver(remeasure).observe(mapEl);
}
L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
  attribution: '&copy; OpenStreetMap, &copy; CARTO',
  maxZoom: 19,
}).addTo(map);

let cluster: any = null;

function drawMap(rows: Company[]): void {
  if (cluster) map.removeLayer(cluster);
  cluster = (L as any).markerClusterGroup({
    maxClusterRadius: 40,
    disableClusteringAtZoom: 14,
    spiderfyOnMaxZoom: true,
    showCoverageOnHover: false,
  });

  // Companies that gave only a city share that city's coordinate exactly, so
  // group by point and draw one marker carrying the count. A separate marker
  // per company at the same pixel is unclickable however it is styled.
  const byPoint = new Map<string, Company[]>();
  for (const c of rows) {
    if (c.lat === null || c.lon === null) continue;
    const key = `${c.lat.toFixed(4)},${c.lon.toFixed(4)}`;
    const list = byPoint.get(key);
    if (list) list.push(c);
    else byPoint.set(key, [c]);
  }

  for (const [key, here] of byPoint) {
    const [lat, lon] = key.split(',').map(Number);
    const n = here.length;
    const size = Math.round(2 * (7 + 3.2 * Math.sqrt(n)));
    const own = here.some((c) => c.is_own);

    const ids = here.map((c) => c.id);
    const marker = L.marker([lat, lon], {
      icon: L.divIcon({
        className: '',
        iconSize: [size, size],
        iconAnchor: [size / 2, size / 2],
        html:
          `<div style="width:${size}px;height:${size}px;border-radius:50%;` +
          `background:${own ? '#0E0E10' : '#FF000D'};border:1.5px solid #fff;` +
          `box-shadow:0 1px 4px rgba(0,0,0,.35);color:#fff;font-weight:700;` +
          `font-family:Archivo,sans-serif;font-size:${Math.max(10, Math.min(15, size / 3))}px;` +
          `display:flex;align-items:center;justify-content:center">${n}</div>`,
      }),
    });

    const where = here.find((c) => c.city)?.city ?? 'Location';
    const items = here
      .map(
        (c) =>
          `<li style="margin-bottom:5px"><a href="#" data-id="${c.id}" class="pin-link">` +
          `<b>${c.canonical_name}</b></a>${c.tier ? ` · tier ${c.tier}` : ''}<br>` +
          `<span style="color:#71716E">${c.domain}</span></li>`,
      )
      .join('');
    marker.bindPopup(
      `<div style="font-family:Archivo,sans-serif;font-size:13px;max-height:300px;overflow:auto">` +
        `<b>${where}</b> — ${n} ${n === 1 ? 'company' : 'companies'}` +
        `<ul style="padding-left:16px;margin:8px 0">${items}</ul></div>`,
    );
    // Narrow the list to what is under the pin. A popup can hold three names
    // legibly; the panel beside it can hold thirty with their evidence counts.
    marker.on('click', () => {
      spotlight = new Set(ids);
      spotlightLabel = `${where} — ${n} ${n === 1 ? 'company' : 'companies'}`;
      drawList(companies.filter(matches));
    });
    cluster.addLayer(marker);
  }

  map.addLayer(cluster);

  // Three different reasons a company is not on the map, previously reported
  // as one number. "No address published" is a fact about a company; "not in
  // Switzerland" is a fact about the segment; and they are not the same news.
  const placed = rows.filter((c) => c.lat !== null).length;
  const foreign = rows.filter((c) => c.lat === null && c.geocode_status === 'not_found').length;
  const noAddress = rows.length - placed - foreign;
  const parts = [`${placed} placed`];
  if (noAddress > 0) parts.push(`${noAddress} publish no address`);
  if (foreign > 0) parts.push(`${foreign} outside Switzerland`);
  const el = $('map-count');
  if (el) el.textContent = parts.join(' · ');
}

// --- list -------------------------------------------------------------------

function drawList(all: Company[]): void {
  const list = $('list');
  if (!list) return;

  const rows = ordered(spotlight ? all.filter((c) => spotlight!.has(c.id)) : all);

  const note = $('spotlight');
  if (note) {
    note.innerHTML = spotlight
      ? `<span>${spotlightLabel}</span><button id="spot-clear">show all</button>`
      : '';
    note.hidden = !spotlight;
  }

  if (rows.length === 0) {
    list.innerHTML = '<div class="empty">Nothing matches these filters.</div>';
  } else {
    list.innerHTML = rows
      .map((c) => {
        const services = c.tags
          .filter((t) => t.facet === 'service_type')
          .slice(0, 3)
          .map((t) => `<span class="chip">${t.value.replace(/_/g, ' ')}</span>`)
          .join('');
        const evidence = [
          c.case_studies.length ? `${c.case_studies.length} projects` : '',
          c.clients.length ? `${c.clients.length} clients` : '',
          c.products.length ? `${c.products.length} products` : '',
          c.mentions.filter((m) => !m.is_self_published).length
            ? `${c.mentions.filter((m) => !m.is_self_published).length} in the press`
            : '',
        ]
          .filter(Boolean)
          .map((label) => `<span class="chip ev">${label}</span>`)
          .join('');
        const badges =
          (c.is_own ? '<span class="chip own">yours</span>' : '') +
          (c.tier ? `<span class="chip t${c.tier}">tier ${c.tier}</span>` : '');
        const where = [c.city, c.canton].filter(Boolean).join(', ') || 'location unknown';
        const size = c.headcount_est ? `${c.headcount_est} staff` : '';
        // An unknown score and a low score mean different things, so they are
        // drawn differently rather than both rendering as a small number.
        const t = c.traction;
        const score = t.is_unknown
          ? `<span class="score none" title="Nothing published to score">–</span>`
          : `<span class="score" title="${t.points}/100 from published evidence" ` +
            `style="--fill:${t.points}%">${t.points}</span>`;
        return (
          `<div class="company${c.is_own ? ' own' : ''}" data-id="${c.id}">` +
          `<div class="name">${score}${c.canonical_name}</div>` +
          `<div class="size">${size}</div>` +
          `<div class="meta">${c.domain} · ${where}</div>` +
          `<div class="chips">${badges}${evidence}${services}</div></div>`
        );
      })
      .join('');
  }

  list.scrollTop = 0;
  const el = $('list-count');
  if (el) el.textContent = `${rows.length} shown`;
}

// --- detail -----------------------------------------------------------------

function evidence(url: string, quote: string): string {
  return (
    `<blockquote class="evidence">${quote}</blockquote>` +
    `<div class="muted"><a href="${url}" target="_blank" rel="noopener">source</a></div>`
  );
}

function openDetail(id: number): void {
  const c = companies.find((x) => x.id === id);
  const dialog = $<HTMLDialogElement>('detail');
  const body = $('detail-body');
  if (!c || !dialog || !body) return;

  const section = (title: string, inner: string) => (inner ? `<h3>${title}</h3>${inner}` : '');
  const entry = (heading: string, extra: string, url: string, quote: string) =>
    `<div class="entry"><div class="t">${heading}</div>${extra}${evidence(url, quote)}</div>`;

  const bars = c.traction.components
    .map(
      (comp) =>
        `<div class="bar"><span>${comp.name}</span>` +
        `<span class="track"><span class="fill${comp.observed ? '' : ' soft'}" ` +
        `style="width:${(comp.points / comp.max_points) * 100}%"></span></span>` +
        `<span class="n">${comp.points}</span></div>` +
        `<div class="muted" style="grid-column:1/-1;margin:-2px 0 4px">${comp.detail}</div>`,
    )
    .join('');

  body.innerHTML =
    `<h2 style="font-size:1.35rem;text-transform:none;letter-spacing:-.02em;color:var(--ink)">` +
    `${c.canonical_name}${c.is_own ? ' <span class="chip own">yours</span>' : ''}</h2>` +
    `<p class="muted">${[c.street, c.postal_code, c.city, c.canton].filter(Boolean).join(', ') || 'no address recorded'} · ` +
    `<a href="https://${c.domain}" target="_blank" rel="noopener">${c.domain}</a></p>` +
    (c.one_liner ? `<p>${c.one_liner}</p>` : '') +
    (c.tier_rationale
      ? `<p class="muted"><strong>${
          c.tier === null ? 'Not in this segment' : `Tier ${c.tier}`
        }:</strong> ${c.tier_rationale}</p>`
      : '') +
    section(
      'Sells',
      c.offerings.map((o) => entry(o.label, '', o.evidence_url, o.evidence_quote)).join(''),
    ) +
    section(
      'Has built',
      c.case_studies
        .map((s) =>
          entry(
            s.title + (s.industry ? ` <span class="muted">· ${s.industry}</span>` : ''),
            s.summary ? `<div class="muted">${s.summary}</div>` : '',
            s.evidence_url,
            s.evidence_quote,
          ),
        )
        .join(''),
    ) +
    section(
      'Named clients',
      c.clients
        .map((x) =>
          entry(
            x.client_name +
              (x.industry ? ` <span class="muted">· ${x.industry}</span>` : '') +
              ` <span class="muted">(${x.relationship.replace(/_/g, ' ')})</span>`,
            '',
            x.evidence_url,
            x.evidence_quote,
          ),
        )
        .join(''),
    ) +
    section(
      'Products',
      c.products
        .map((p) =>
          entry(
            `${p.name} <span class="muted">(${p.kind})</span>`,
            p.summary ? `<div class="muted">${p.summary}</div>` : '',
            p.evidence_url,
            p.evidence_quote,
          ),
        )
        .join(''),
    ) +
    section(
      c.traction.is_unknown ? 'Nothing published to score' : `Visible traction — ${c.traction.points}/100`,
      `<div class="bars" style="grid-template-columns:1fr">${bars}</div>` +
        `<p class="muted">A floor on what this company can demonstrate publicly, not a ` +
        `measure of how well it is doing. ${Math.round(c.traction.confidence * 100)}% of the ` +
        `components could be observed at all; the rest are blank rather than bad. ` +
        `<strong>Coverage scores zero for every company in this dataset</strong> — it is ` +
        `read from company websites, and almost none list press they did not write ` +
        `themselves. It needs an external news source to mean anything, so read it as ` +
        `missing rather than as absent coverage.</p>`,
    ) +
    section(
      'How they work',
      (
        [
          ['hosting', 'Runs on'],
          ['cloud_providers', 'Clouds'],
          ['technologies', 'Tools'],
          ['certifications', 'Certifications'],
          ['workshop_formats', 'Training formats'],
          ['industries_served', 'Sectors served'],
        ] as const
      )
        .map(([key, label]) => {
          const values = c.attributes?.[key] ?? [];
          if (values.length === 0) return '';
          return (
            `<div class="attr"><span class="k">${label}</span>` +
            `<span class="v">${values
              .map((x) => `<span class="chip">${x.replace(/_/g, ' ')}</span>`)
              .join('')}</span></div>`
          );
        })
        .join(''),
    ) +
    section(
      c.seo.is_unknown ? 'Search visibility not measured' : `Search visibility — ${c.seo.score}/100`,
      c.seo.is_unknown
        ? '<p class="muted">No pages were crawled for this company.</p>'
        : (c.seo.findings.length
            ? `<ul class="findings">${c.seo.findings.map((f) => `<li>${f}</li>`).join('')}</ul>`
            : '<p class="muted">Nothing obviously missing.</p>') +
          `<p class="muted">${c.seo.schema_types.length
            ? `Structured data: ${c.seo.schema_types.join(', ')}.`
            : 'No structured data at all.'} ` +
          `${c.seo.languages_declared || 0} language${c.seo.languages_declared === 1 ? '' : 's'} ` +
          `declared, median ${c.seo.median_word_count ?? 0} words per page. ` +
          `Measured from markup — it says nothing about backlinks or actual rankings.</p>`,
    ) +
    section(
      'In the press',
      c.mentions
        .map((m) =>
          entry(
            `<a href="${m.url}" target="_blank" rel="noopener">${m.headline}</a>` +
              (m.outlet ? ` <span class="muted">· ${m.outlet}</span>` : '') +
              (m.published_year ? ` <span class="muted">· ${m.published_year}</span>` : '') +
              (m.is_self_published ? ' <span class="chip">self-published</span>' : ''),
            '',
            m.evidence_url,
            m.evidence_quote,
          ),
        )
        .join(''),
    ) +
    section(
      'Its website has',
      c.signals.filter((s) => s.present).length
        ? `<p>${c.signals
            .filter((s) => s.present)
            .map((s) => `<span class="chip">${s.signal.replace(/_/g, ' ')}</span>`)
            .join(' ')}</p>`
        : '',
    );

  dialog.showModal();
}

// --- wiring -----------------------------------------------------------------

function render(): void {
  const rows = companies.filter(matches);
  drawMap(rows);
  drawList(rows);
}

for (const control of Object.values(controls)) {
  control?.addEventListener('input', render);
}

$('f-reset')?.addEventListener('click', () => {
  for (const control of Object.values(controls)) {
    if (!control) continue;
    if (control instanceof HTMLInputElement && control.type === 'checkbox') control.checked = false;
    else (control as HTMLInputElement | HTMLSelectElement).value = '';
  }
  if (controls.tier) controls.tier.value = 'in';
  spotlight = null;
  // The header is transparent over the hero and gains a hairline once the page
// has moved. It is the only chrome that changes on scroll, which is what makes
// it read as a state rather than as decoration.
const header = document.querySelector('header.top');
if (header) {
  const onScroll = () => header.classList.toggle('scrolled', window.scrollY > 8);
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();
}

render();
});

function handleClick(event: Event): void {
  const target = event.target as HTMLElement;
  const link = target.closest('.pin-link') as HTMLElement | null;
  if (link?.dataset.id) {
    event.preventDefault();
    openDetail(Number(link.dataset.id));
    return;
  }
  if (target.closest('#spot-clear')) {
    spotlight = null;
    drawList(companies.filter(matches));
    return;
  }
  const card = target.closest('.company') as HTMLElement | null;
  if (card?.dataset.id) openDetail(Number(card.dataset.id));
}

document.addEventListener('click', handleClick);

// Leaflet calls `disableClickPropagation` on every popup container so that a
// click inside a popup does not reach the map underneath. That also stops it
// reaching `document`, which silently killed the delegated listener above and
// left every name in a popup unclickable. Bind to the popup itself instead.
map.on('popupopen', (event: any) => {
  event.popup.getElement()?.addEventListener('click', handleClick);
});

$('detail-close')?.addEventListener('click', () => $<HTMLDialogElement>('detail')?.close());

render();
