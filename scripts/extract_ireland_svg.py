"""Extract 26 ROI counties from the Wikipedia location-map SVG into a
compact inline SVG for the landing-page heatmap.

Source: https://commons.wikimedia.org/wiki/File:Ireland_location_map.svg
(public domain / CC0). Attribution kept in the output header.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "ireland-raw.svg"                  # cache of upstream (git-ignored)
OUT = ROOT / "frontend" / "ireland-counties.svg"         # served asset (committed)
UPSTREAM_URL = "https://commons.wikimedia.org/wiki/Special:FilePath/Ireland_location_map.svg"

# my-county-name  ->  list of source element ids to combine into a single <g>
COUNTY_SOURCES: dict[str, list[str]] = {
    "Carlow":     ["Carlow"],
    "Cavan":      ["Cavan"],
    "Clare":      ["Clare_mainland"],
    "Cork":       ["Cork_mainland"],
    "Donegal":    ["Donegal_mainland"],
    "Dublin":     ["Dublin_City_mainland", "Fingal_mainland", "South_Dublin", "Dun_Laoghaire_Rathdown"],
    "Galway":     ["Galway_united_mainland"],
    "Kerry":      ["Kerry_mainland"],
    "Kildare":    ["Kildare"],
    "Kilkenny":   ["Kilkenny"],
    "Laois":      ["Laois"],
    "Leitrim":    ["Leitrim"],
    "Limerick":   ["Limerick_mainland"],
    "Longford":   ["Longford"],
    "Louth":      ["Louth"],
    "Mayo":       ["Mayo_mainland"],
    "Meath":      ["Meath"],
    "Monaghan":   ["Monaghan"],
    "Offaly":     ["Offaly"],
    "Roscommon":  ["Roscommon"],
    "Sligo":      ["Sligo_mainland"],
    "Tipperary":  ["Tipperary_all"],
    "Waterford":  ["Waterford_all"],
    "Westmeath":  ["Westmeath"],
    "Wexford":    ["Wexford_mainland"],
    "Wicklow":    ["Wicklow"],
}

# Optional grey context outline for Northern Ireland
CONTEXT_SOURCES: dict[str, list[str]] = {
    "NorthernIreland": ["Northern_Ireland_mainland"],
}

ELEMENT_RE = re.compile(
    r'<(polygon|path)\s+id="([^"]+)"([^/]*?)/>',
    re.S,
)

NUM_RE = re.compile(r"-?\d+\.\d+")


def _shrink_numbers(attr: str, decimals: int = 0) -> str:
    """Round coord numbers. The map renders at ~500 CSS px across a 1450-unit
    viewBox (≈0.34 px per unit), so integer precision is invisible."""
    def repl(m: re.Match[str]) -> str:
        return f"{float(m.group(0)):.{decimals}f}"
    return NUM_RE.sub(repl, attr)


def _extract(raw: str) -> dict[str, tuple[str, str]]:
    """Return {source_id: (tag, geometry_attr)} where geometry_attr is
    e.g. `points="…"` or `d="…"` with coordinates rounded to 1 decimal."""
    out: dict[str, tuple[str, str]] = {}
    for tag, ident, rest in ELEMENT_RE.findall(raw):
        m = re.search(r'(points|d)="([^"]+)"', rest)
        if not m:
            continue
        geom_key, geom_val = m.group(1), _shrink_numbers(m.group(2))
        out[ident] = (tag, f'{geom_key}="{geom_val}"')
    return out


def build() -> str:
    raw = SRC.read_text(encoding="utf-8")
    els = _extract(raw)

    # Pull viewBox from source so coords stay valid.
    vb_match = re.search(r'viewBox="([^"]+)"', raw)
    view_box = vb_match.group(1) if vb_match else "0 0 1450 1807"

    lines: list[str] = []
    lines.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}" '
        f'class="ireland-map-svg" role="img" aria-label="Map of Ireland counties">'
    )
    # Source credit lives on the SVG so it survives copy/paste.
    lines.append(
        "<title>Ireland county map — derived from Wikipedia (public domain)</title>"
    )

    # Grey context (NI) first so counties overlay if any overlap.
    lines.append('<g class="ireland-ctx" fill="#1c1e22" stroke="#2a2d32" stroke-width="1">')
    for out_id, srcs in CONTEXT_SOURCES.items():
        for src in srcs:
            if src not in els:
                continue
            tag, geom = els[src]
            lines.append(f'<{tag} {geom}/>')
    lines.append('</g>')

    # Counties. Each is a group with data-county so JS can target it.
    lines.append('<g class="ireland-counties" stroke="#0a0b0d" stroke-width="1.2" stroke-linejoin="round">')
    missing: list[str] = []
    for out_id, srcs in COUNTY_SOURCES.items():
        shapes: list[str] = []
        for src in srcs:
            if src not in els:
                missing.append(f"{out_id}:{src}")
                continue
            tag, geom = els[src]
            shapes.append(f'<{tag} {geom}/>')
        if not shapes:
            continue
        lines.append(
            f'<g class="county" data-county="{out_id}" tabindex="0" '
            f'role="button" aria-label="{out_id}">' + "".join(shapes) + '</g>'
        )
    lines.append('</g>')
    lines.append('</svg>')

    if missing:
        print("WARN missing source ids:", ", ".join(missing))
    return "".join(lines)


def _fetch_upstream() -> None:
    """Download the raw Wikipedia SVG once. Cached under data/ (git-ignored)."""
    import urllib.request
    SRC.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {UPSTREAM_URL} -> {SRC}")
    with urllib.request.urlopen(UPSTREAM_URL) as resp, SRC.open("wb") as fh:
        fh.write(resp.read())


def main() -> None:
    if not SRC.exists():
        _fetch_upstream()
    svg = build()
    OUT.write_text(svg, encoding="utf-8")
    print(f"Wrote {OUT} ({len(svg):,} bytes)")


if __name__ == "__main__":
    main()
