// Helpers shared by the national dashboard (app.js) and the county page
// (county.js). Loaded as a plain script before both, so everything here is a
// global — matching the no-build, no-framework approach of the rest of the site.

const fmtEur = (v) => (v == null ? "—" : `€${v.toFixed(3)}`);
const fmtPct = (v) => (v == null ? "—" : `${(v * 100).toFixed(2)}%`);
const fmtCents = (v) =>
    v == null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(1)}c`;

const fmtDate = (iso) => {
    const d = new Date(iso);
    return d.toLocaleDateString("en-IE", { year: "numeric", month: "short", day: "numeric" });
};

const fmtDMY = (iso) => {
    const [y, m, d] = iso.split("-");
    return `${d}/${m}/${y.slice(2)}`;
};

const fmtDateTime = (iso) => {
    const d = new Date(iso);
    return d.toLocaleString("en-IE", {
        year: "numeric", month: "short", day: "numeric",
        hour: "2-digit", minute: "2-digit",
    });
};

async function loadManifest() {
    try { return await jget("data/manifest.json"); }
    catch { return null; }
}

async function jget(path) {
    const r = await fetch(path, { cache: "no-cache" });
    if (!r.ok) throw new Error(`${path} → ${r.status}`);
    return r.json();
}

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
}

// RSS summaries arrive as HTML fragments (<p>, <a>, <ul>, entities). Strip
// markup to plain text so it renders cleanly inside the collapsible card.
function stripHtml(s) {
    if (!s) return "";
    const doc = new DOMParser().parseFromString(String(s), "text/html");
    return (doc.body.textContent || "").replace(/\s+/g, " ").trim();
}

// ------------------ chart theme ------------------
// Palette mirrors style.css industrial dark: sodium amber (petrol) + instrument
// teal (diesel) on a petroleum-black backdrop with warm off-white text.
const FUEL_COLORS = {
    petrol: { line: "#ffb648", fill: "rgba(255, 182, 72, 0.10)" },
    diesel: { line: "#7bd3cf", fill: "rgba(123, 211, 207, 0.09)" },
};

const CHART_INK  = "#ece7d8";
const CHART_DIM  = "#7c828c";
const CHART_GRID = "rgba(236, 231, 216, 0.05)";
const CHART_MONO = "'IBM Plex Mono', ui-monospace, SFMono-Regular, Consolas, monospace";
const CHART_SANS = "'IBM Plex Sans', ui-sans-serif, system-ui, sans-serif";

function baseChartOptions() {
    return {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
            legend: {
                labels: {
                    color: CHART_INK,
                    font: { family: CHART_MONO, size: 11, weight: "500" },
                    boxWidth: 10,
                    boxHeight: 10,
                    padding: 16,
                },
            },
            tooltip: {
                backgroundColor: "rgba(16, 18, 21, 0.94)",
                borderColor: "rgba(255, 182, 72, 0.35)",
                borderWidth: 1,
                titleColor: CHART_INK,
                titleFont: { family: CHART_MONO, size: 11, weight: "600" },
                bodyColor: CHART_INK,
                bodyFont: { family: CHART_MONO, size: 12 },
                padding: 10,
                cornerRadius: 4,
                displayColors: true,
                boxPadding: 4,
                callbacks: {
                    label: (c) => c.parsed.y == null
                        ? null
                        : `${c.dataset.label}: €${c.parsed.y.toFixed(3)}/L`,
                },
            },
        },
        scales: {
            x: {
                ticks: {
                    color: CHART_DIM,
                    font: { family: CHART_MONO, size: 10 },
                    maxTicksLimit: 10,
                    autoSkip: true,
                },
                grid:   { color: CHART_GRID, drawTicks: false },
                border: { color: "rgba(236, 231, 216, 0.10)" },
            },
            y: {
                ticks: {
                    color: CHART_DIM,
                    font: { family: CHART_MONO, size: 10 },
                    callback: (v) => `€${v.toFixed(2)}`,
                },
                grid:   { color: CHART_GRID, drawTicks: false },
                border: { color: "rgba(236, 231, 216, 0.10)" },
            },
        },
    };
}

// ------------------ sparkline ------------------
// `values` is a plain array of numbers, oldest first.
function renderSparklineValues(elId, values) {
    const svg = document.getElementById(elId);
    if (!svg || values.length < 2) return;
    const lo = Math.min(...values);
    const hi = Math.max(...values);
    const range = (hi - lo) || 1;
    const W = 200, H = 40, PAD = 2;
    const x = (i) => (i / (values.length - 1)) * (W - 2 * PAD) + PAD;
    const y = (v) => H - PAD - ((v - lo) / range) * (H - 2 * PAD);
    const line = values.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
    const fill = `M${x(0).toFixed(1)},${(H - PAD).toFixed(1)} ` +
                 values.map((v, i) => `L${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ") +
                 ` L${x(values.length - 1).toFixed(1)},${(H - PAD).toFixed(1)} Z`;
    svg.innerHTML = `<path class="fill" d="${fill}"/><path d="${line}"/>`;
}

function renderSparkline(elId, pts) {
    renderSparklineValues(elId, pts.map(p => p.price_eur_per_litre));
}

// ------------------ drag-to-compare overlay ------------------
// Chart.js plugin that draws two vertical guide lines + a connecting segment
// between the anchor point (mousedown) and the current point during a drag.
// State lives on `chart._dragCompare` so plugin stays stateless per instance.
const DragComparePlugin = {
    id: "dragCompare",
    afterDatasetsDraw(chart) {
        const dc = chart._dragCompare;
        if (!dc) return;
        const meta = chart.getDatasetMeta(dc.datasetIndex);
        const a = meta.data[dc.anchorIndex];
        const b = meta.data[dc.currentIndex];
        if (!a || !b) return;
        const ds = chart.data.datasets[dc.datasetIndex];
        const color = ds.borderColor;
        const { ctx, scales } = chart;
        ctx.save();
        ctx.strokeStyle = color;
        ctx.globalAlpha = 0.55;
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        for (const p of [a, b]) {
            ctx.beginPath();
            ctx.moveTo(p.x, scales.y.top);
            ctx.lineTo(p.x, scales.y.bottom);
            ctx.stroke();
        }
        ctx.setLineDash([]);
        ctx.globalAlpha = 0.95;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
        for (const p of [a, b]) {
            ctx.beginPath();
            ctx.fillStyle = "#0a0b0d";
            ctx.arc(p.x, p.y, 5.5, 0, Math.PI * 2);
            ctx.fill();
            ctx.beginPath();
            ctx.fillStyle = color;
            ctx.arc(p.x, p.y, 3.5, 0, Math.PI * 2);
            ctx.fill();
        }
        ctx.restore();
    },
};
if (typeof Chart !== "undefined") Chart.register(DragComparePlugin);

function attachDragCompare(canvasId, popupId) {
    const canvas = document.getElementById(canvasId);
    const popup  = document.getElementById(popupId);
    if (!canvas || !popup) return;

    const wrap = canvas.closest(".chart-wrap") || canvas.parentElement;
    if (getComputedStyle(wrap).position === "static") wrap.style.position = "relative";

    let dragging = false;
    let anchorIndex = null;
    let datasetIndex = null;

    const chartOf = () => Chart.getChart(canvas);

    function nearestOnMousedown(evt) {
        const chart = chartOf();
        if (!chart) return null;
        const els = chart.getElementsAtEventForMode(
            evt, "nearest", { intersect: false, axis: "x" }, false,
        );
        return els.length ? els[0] : null;
    }

    // During drag the mouse can leave the canvas; convert clientX to a canvas
    // pixel manually and scan labels for the closest x-tick so the popup keeps
    // tracking even when the cursor is over the page background.
    function nearestByClientX(clientX) {
        const chart = chartOf();
        if (!chart || datasetIndex == null) return null;
        const rect = canvas.getBoundingClientRect();
        const xScale = chart.scales.x;
        const px = Math.max(xScale.left, Math.min(xScale.right, clientX - rect.left));
        const n = chart.data.labels.length;
        let best = 0, bestD = Infinity;
        for (let i = 0; i < n; i++) {
            const d = Math.abs(xScale.getPixelForValue(i) - px);
            if (d < bestD) { bestD = d; best = i; }
        }
        return { index: best, datasetIndex };
    }

    function place(evt) {
        const wrapRect = wrap.getBoundingClientRect();
        // Force layout so offsetWidth/Height reflect the just-rendered content
        // — reading them after `hidden = false` is not always enough in Chrome
        // when the innerHTML change happens in the same frame.
        void popup.offsetHeight;
        const w = popup.offsetWidth;
        const h = popup.offsetHeight;
        const mx = evt.clientX - wrapRect.left;
        const my = evt.clientY - wrapRect.top;
        const gap = 14;
        let x = mx + gap;
        let y = my + gap;
        if (x + w + 6 > wrapRect.width)  x = mx - w - gap;
        if (y + h + 6 > wrapRect.height) y = my - h - gap;
        // Final hard clamp inside the chart-wrap so the popup can never spill
        // into the section below the chart.
        x = Math.max(6, Math.min(x, wrapRect.width  - w - 6));
        y = Math.max(6, Math.min(y, wrapRect.height - h - 6));
        popup.style.left = x + "px";
        popup.style.top  = y + "px";
    }

    function render(evt, curOverride) {
        const chart = chartOf();
        if (!chart || anchorIndex == null) return;
        const cur = curOverride || nearestByClientX(evt.clientX);
        if (!cur) return;
        const ds  = chart.data.datasets[datasetIndex];
        const a   = ds.data[anchorIndex];
        const b   = ds.data[cur.index];
        if (a == null || b == null) return;
        const aLbl = chart.data.labels[anchorIndex];
        const bLbl = chart.data.labels[cur.index];
        const delta = b - a;
        const pct   = a !== 0 ? (delta / a) * 100 : 0;
        const sign  = delta > 0 ? "up" : delta < 0 ? "down" : "flat";
        const arrow = sign === "up" ? "▲" : sign === "down" ? "▼" : "→";
        const pctStr   = `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`;
        const deltaStr = `${delta >= 0 ? "+" : ""}€${delta.toFixed(3)}`;
        popup.innerHTML = `
            <div class="dc-header">
                <span class="dc-dot" style="background:${ds.borderColor}"></span>${escapeHtml(ds.label)}
            </div>
            <div class="dc-range">${escapeHtml(aLbl)} → ${escapeHtml(bLbl)}</div>
            <div class="dc-prices">€${a.toFixed(3)} → €${b.toFixed(3)}</div>
            <div class="dc-delta" data-sign="${sign}">${arrow} ${pctStr}
                <span class="dc-abs">${deltaStr}</span>
            </div>
        `;
        popup.hidden = false;
        place(evt);
        chart._dragCompare = { datasetIndex, anchorIndex, currentIndex: cur.index };
        chart.update("none");
    }

    function clear() {
        const chart = chartOf();
        popup.hidden = true;
        anchorIndex = null;
        datasetIndex = null;
        if (chart) {
            chart._dragCompare = null;
            chart.update("none");
        }
    }

    canvas.addEventListener("mousedown", (e) => {
        const cur = nearestOnMousedown(e);
        if (!cur) return;
        dragging = true;
        anchorIndex = cur.index;
        datasetIndex = cur.datasetIndex;
        e.preventDefault();
        render(e, cur);
    });
    window.addEventListener("mousemove", (e) => {
        if (!dragging) return;
        render(e);
    });
    window.addEventListener("mouseup", () => { dragging = false; });
    document.addEventListener("mousedown", (e) => {
        if (dragging) return;
        if (popup.hidden) return;
        if (!canvas.contains(e.target) && !popup.contains(e.target)) clear();
    });
    window.addEventListener("keydown", (e) => { if (e.key === "Escape") clear(); });
}

// ------------------ range filtering ------------------
function cutoffIso(weeks) {
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - weeks * 7);
    return cutoff.toISOString().slice(0, 10);
}

function sliceByWeeks(series, weeks) {
    const iso = cutoffIso(weeks);
    const points = series.points.filter(p => p.date >= iso);
    return { ...series, points, latest: points.length ? points[points.length - 1] : null };
}

// ------------------ dev ingest button ------------------
// Only rendered when served from a loopback host — matches the API guard in
// backend/app/routes/dev.py. Static Pages users never see it.
function isLocalhost() {
    const h = location.hostname;
    return h === "localhost" || h === "127.0.0.1" || h === "::1" || h === "";
}

function mountDevIngestButton({ onDone } = {}) {
    if (!isLocalhost()) return;
    const strip = document.querySelector(".status-strip");
    if (!strip || strip.querySelector(".dev-ingest")) return;

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "dev-ingest";
    btn.textContent = "⟳ Ingest";
    btn.title = "Dev: refresh news + Brent (loopback only)";

    btn.addEventListener("click", async () => {
        const original = btn.textContent;
        btn.disabled = true;
        btn.textContent = "Ingesting…";
        try {
            const r = await fetch("/api/ingest", { method: "POST" });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            const body = await r.json();
            const failed = Object.entries(body.results || {}).filter(([, v]) => !v.ok);
            btn.textContent = failed.length ? `✗ ${failed.map(([k]) => k).join(", ")}` : "✓ done";
            if (typeof onDone === "function") await onDone();
        } catch (err) {
            btn.textContent = `✗ ${err.message}`;
            console.error("ingest failed", err);
        } finally {
            setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 2500);
        }
    });

    strip.appendChild(btn);
}
