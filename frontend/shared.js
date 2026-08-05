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
const FUEL_COLORS = {
    petrol: { line: "#4ea1ff", fill: "rgba(78, 161, 255, 0.08)" },
    diesel: { line: "#ffb454", fill: "rgba(255, 180, 84, 0.06)" },
};

function baseChartOptions() {
    return {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
            legend: { labels: { color: "#e6edf3" } },
            tooltip: {
                callbacks: {
                    label: (c) => c.parsed.y == null
                        ? null
                        : `${c.dataset.label}: €${c.parsed.y.toFixed(3)}/L`,
                },
            },
        },
        scales: {
            x: {
                ticks: { color: "#9aa7b4", maxTicksLimit: 10, autoSkip: true },
                grid:  { color: "rgba(255,255,255,0.04)" },
            },
            y: {
                ticks: { color: "#9aa7b4", callback: (v) => `€${v.toFixed(2)}` },
                grid:  { color: "rgba(255,255,255,0.04)" },
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
