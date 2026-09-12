# Dashboard Redesign — Design Spec

Date: 2026-09-12
Status: Approved (2026-09-12), implementing in single PR.

## Goal

Move `frontend/` from single long-scroll landing page to a multi-view dashboard organised by user intent. Sidebar navigation (desktop) + bottom nav (mobile). Preserve all existing functionality; no data-fetch changes.

## Information architecture

Nav (6 items, in order):

1. **Overview** — landing snapshot
2. **Decide** — should I fill up now?
3. **Track** — my fills + spend
4. **Analyse** — chart + predictions + backtest
5. **Map** — national interactive Ireland map
6. **County** — per-county detail (last-viewed or Dublin default)

## Routing

- SPA in `index.html` with hash router. Routes: `#/overview`, `#/decide`, `#/track`, `#/analyse`, `#/map`.
- `county.html` remains a separate file (deep-linked via `?county=`), receives the same sidebar shell.
- Legacy anchor links (`#ireland-map`, etc.) redirect via hashchange guard to their new home route.
- Default route on empty hash: `#/overview`.

## Layout shell

- Desktop (≥768px): fixed left sidebar 240px, main content fills remainder.
- Mobile (<768px): sidebar hidden, fixed bottom nav bar with 6 icons (~62px each on 375px viewport).
- Sidebar contents (top → bottom):
  1. Logo + title
  2. 6 nav items (icon + label)
  3. Global fuel toggle (Petrol / Diesel)
  4. Live petrol + diesel mini-tile (price + sparkline)

## Per-page layout (adaptive)

- **Overview** — 2-column tile grid (1-col on mobile). 6 tiles in order: Live prices, Decision, Next-week forecast, County map thumbnail, Your 30d spend (hidden if no fills), News. Each tile links to its full page.
- **Decide** — focus (fill decision card, enlarged) + right rail (fillup calc, share card).
- **Track** — focus (fill log form + list) + right rail (30d stats vs national).
- **Analyse** — focus (historical chart) + right rail (predictions card, backtest strip, hit-rate).
- **Map** — focus (full interactive map) + right rail (metric chips, legend, top cheapest / priciest counties).
- **County** — unchanged behaviour, sidebar added.

## Global state

- Fuel selection: single source in sidebar, persisted to `localStorage`. All per-section fuel chips subscribe via custom event `fuel:change`.
- Data fetching: unchanged. Fetched once at boot, cached in module scope.
- Chart / interactive map: lazy-init on first activation of their route (avoid draw before canvas / SVG visible). Router fires `viewshow` event with route name; widgets listen.

## Visual tokens (refresh, still dark)

- Base bg: `#0a0b0d` (unchanged)
- Tile bg: `#111318` (new)
- Border: 1px hairline `rgba(255,255,255,0.06)`
- Spacing scale: multiples of 8px
- Fonts: unchanged (IBM Plex Sans / Mono / Sans Condensed)
- Signal colours: unchanged (green / red / amber)

## Files touched

| File | Change |
|---|---|
| `frontend/index.html` | Rewrite: sidebar shell + `<main>` with 5 route panels |
| `frontend/county.html` | Add sidebar shell (reuse `shared.js` render) |
| `frontend/style.css` | New sections: sidebar, bottom-nav, tile-grid, focus-rail, refreshed tokens |
| `frontend/app.js` | Add hash router + view registry; lazy widget init |
| `frontend/shared.js` | Sidebar / bottom-nav render function (reused by both HTML files) |

## Out of scope

- No data-fetch changes.
- No new chart, forecast, or map features.
- No build tool introduced.
- No auth, no server, no framework.

## Rollout

- Single PR, no phase gate.
- Deep links preserved: `county.html?county=Cork` still works.
- Legacy anchor links redirect to new SPA routes.

## Estimate

~3–5 hours of focused work.
