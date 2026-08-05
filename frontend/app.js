// Dashboard controller. Reads static JSON snapshots from ./data/*.json so the
// same bundle works both under FastAPI (local dev) and GitHub Pages (static).

const fmtEur = (v) => (v == null ? "—" : `€${v.toFixed(3)}`);
const fmtPct = (v) => (v == null ? "—" : `${(v * 100).toFixed(2)}%`);
const fmtDate = (iso) => {
    const d = new Date(iso);
    return d.toLocaleDateString("en-IE", { year: "numeric", month: "short", day: "numeric" });
};

let chart = null;
let priceData = null;   // full history, cached in memory; range selector filters this
let predictionData = null;  // cached so calculator can react to input changes without refetch

async function jget(path) {
    const r = await fetch(path, { cache: "no-cache" });
    if (!r.ok) throw new Error(`${path} → ${r.status}`);
    return r.json();
}

// ------------------ prices + chart ------------------
function sliceByWeeks(series, weeks) {
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - weeks * 7);
    const cutoffIso = cutoff.toISOString().slice(0, 10);
    const points = series.points.filter(p => p.date >= cutoffIso);
    return {
        ...series,
        points,
        latest: points.length ? points[points.length - 1] : null,
    };
}

async function loadPrices(weeks = 26) {
    if (!priceData) priceData = await jget("data/prices.json");
    const view = {
        petrol: sliceByWeeks(priceData.petrol, weeks),
        diesel: sliceByWeeks(priceData.diesel, weeks),
    };

    document.getElementById("current-petrol").textContent = fmtEur(view.petrol.latest?.price_eur_per_litre);
    document.getElementById("current-diesel").textContent = fmtEur(view.diesel.latest?.price_eur_per_litre);
    document.getElementById("asof-petrol").textContent = view.petrol.latest ? `as of ${fmtDate(view.petrol.latest.date)}` : "";
    document.getElementById("asof-diesel").textContent = view.diesel.latest ? `as of ${fmtDate(view.diesel.latest.date)}` : "";

    // Sparklines always use the last 12 weeks from the full series, not the
    // range-filtered view, so they stay stable when the user swaps ranges.
    renderSparkline("spark-petrol", priceData.petrol.points.slice(-12));
    renderSparkline("spark-diesel", priceData.diesel.points.slice(-12));

    renderChart(view);
}

function renderSparkline(elId, pts) {
    const svg = document.getElementById(elId);
    if (!svg || pts.length < 2) return;
    const vals = pts.map(p => p.price_eur_per_litre);
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    const range = (hi - lo) || 1;
    const W = 200, H = 40, PAD = 2;
    const x = (i) => (i / (vals.length - 1)) * (W - 2 * PAD) + PAD;
    const y = (v) => H - PAD - ((v - lo) / range) * (H - 2 * PAD);
    const line = vals.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
    const fill = `M${x(0).toFixed(1)},${(H - PAD).toFixed(1)} ` +
                 vals.map((v, i) => `L${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ") +
                 ` L${x(vals.length - 1).toFixed(1)},${(H - PAD).toFixed(1)} Z`;
    svg.innerHTML = `<path class="fill" d="${fill}"/><path d="${line}"/>`;
}

const fmtDMY = (iso) => {
    const [y, m, d] = iso.split("-");
    return `${d}/${m}/${y.slice(2)}`;
};

function renderChart(data) {
    const labels = data.petrol.points.map(p => fmtDMY(p.date));
    const petrol = data.petrol.points.map(p => p.price_eur_per_litre);
    const diesel = data.diesel.points.map(p => p.price_eur_per_litre);

    const ctx = document.getElementById("price-chart").getContext("2d");
    if (chart) chart.destroy();

    chart = new Chart(ctx, {
        type: "line",
        data: {
            labels,
            datasets: [
                {
                    label: "Petrol (95)",
                    data: petrol,
                    borderColor: "#4ea1ff",
                    backgroundColor: "rgba(78, 161, 255, 0.08)",
                    tension: 0.25,
                    pointRadius: 0,
                    borderWidth: 2,
                    fill: true,
                },
                {
                    label: "Diesel",
                    data: diesel,
                    borderColor: "#ffb454",
                    backgroundColor: "rgba(255, 180, 84, 0.06)",
                    tension: 0.25,
                    pointRadius: 0,
                    borderWidth: 2,
                    fill: true,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            plugins: {
                legend: { labels: { color: "#e6edf3" } },
                tooltip: {
                    callbacks: {
                        label: (c) => `${c.dataset.label}: €${c.parsed.y.toFixed(3)}/L`,
                    },
                },
            },
            scales: {
                x: {
                    ticks: { color: "#9aa7b4", maxTicksLimit: 10, autoSkip: true },
                    grid:  { color: "rgba(255,255,255,0.04)" },
                },
                y: {
                    ticks: {
                        color: "#9aa7b4",
                        callback: (v) => `€${v.toFixed(2)}`,
                    },
                    grid: { color: "rgba(255,255,255,0.04)" },
                },
            },
        },
    });
}

// ------------------ prediction ------------------
async function loadPrediction() {
    const data = await jget("data/prediction.json");
    predictionData = data;
    ["petrol", "diesel"].forEach(fuel => {
        const p = data[fuel];
        const card = document.getElementById(`pred-${fuel}`);
        card.querySelector(".trend-pill").textContent = p.trend;
        card.querySelector(".trend-pill").setAttribute("data-trend", p.trend);
        card.querySelector(".pred-explain").textContent = p.explanation;
        card.querySelector(".pred-delta").textContent = fmtPct(p.predicted_weekly_return);
        card.querySelector(".pred-conf").textContent  = `${Math.round(p.confidence * 100)}%`;
        card.querySelector(".pred-r2").textContent    = p.r2.toFixed(2);
        const arrow = p.predicted_pump_eur_per_l >= p.current_pump_eur_per_l ? "▲" : "▼";
        card.querySelector(".pred-price").textContent =
            `€${p.predicted_pump_eur_per_l.toFixed(3)} ${arrow} (from €${p.current_pump_eur_per_l.toFixed(3)})`;
        card.querySelector(".pred-band").textContent =
            `€${p.predicted_pump_low_eur_per_l.toFixed(3)} – €${p.predicted_pump_high_eur_per_l.toFixed(3)}`;
        card.querySelector(".pred-price-3w").textContent =
            `€${p.predicted_pump_3w_eur_per_l.toFixed(3)}`;
        renderBacktest(card.querySelector(".bt-list"), p.backtest || []);
    });
    document.getElementById("prediction-notes").textContent = (data.notes || []).join("  ");
    updateCalculator();
}

function renderBacktest(list, points) {
    if (!list) return;
    list.innerHTML = points.map(pt => {
        const dir  = pt.predicted_return >= 0 ? "up" : "down";
        const act  = pt.actual_return >= 0 ? "up" : "down";
        const err  = (pt.actual_pump_eur_per_l - pt.predicted_pump_eur_per_l);
        const errS = `${err >= 0 ? "+" : ""}${err.toFixed(3)}`;
        const tip  = `${fmtDMY(pt.date)}\nPredicted ${dir} (${fmtPct(pt.predicted_return)}) → €${pt.predicted_pump_eur_per_l.toFixed(3)}\nActual ${act} (${fmtPct(pt.actual_return)}) → €${pt.actual_pump_eur_per_l.toFixed(3)}\nError €${errS}`;
        return `<li data-hit="${pt.direction_correct}" title="${escapeHtml(tip)}"></li>`;
    }).join("");
}

// ------------------ fill-up calculator ------------------
function updateCalculator() {
    if (!predictionData) return;
    const fuel   = document.getElementById("calc-fuel").value;
    const litres = parseFloat(document.getElementById("calc-litres").value) || 0;
    const p = predictionData[fuel];
    if (!p) return;
    const nowCost   = litres * p.current_pump_eur_per_l;
    const thenCost  = litres * p.predicted_pump_3w_eur_per_l;
    const diff      = thenCost - nowCost;
    const sign      = diff > 0.005 ? "up" : diff < -0.005 ? "down" : "flat";
    const verb      = diff > 0 ? "more" : diff < 0 ? "less" : "same";
    document.getElementById("calc-now").textContent  = `Today: €${nowCost.toFixed(2)}`;
    document.getElementById("calc-then").textContent = `In ~3 weeks: €${thenCost.toFixed(2)}`;
    const diffEl = document.getElementById("calc-diff");
    diffEl.textContent = diff === 0
        ? "Difference: €0.00"
        : `Difference: €${diff >= 0 ? "+" : ""}${diff.toFixed(2)} (${verb})`;
    diffEl.setAttribute("data-sign", sign);
}

// ------------------ news ------------------
async function loadNews() {
    const data = await jget("data/news.json");
    const list = document.getElementById("news-list");
    if (!data.items || data.items.length === 0) {
        list.innerHTML = `<li class="empty">No news items yet.</li>`;
        return;
    }
    list.innerHTML = data.items.map(item => {
        const when = new Date(item.published_at).toLocaleString("en-IE");
        const sumTxt = stripHtml(item.summary);
        const summary = sumTxt ? `<p class="news-summary">${escapeHtml(sumTxt)}</p>` : "";
        const matched = item.matched_keywords
            ? `<p class="news-matched">Matched: ${escapeHtml(item.matched_keywords)}</p>` : "";
        return `
        <li>
            <details>
                <summary>
                    <div class="news-summary-row">
                        <span class="news-title">${escapeHtml(item.title)}</span>
                        <span class="meta">${escapeHtml(item.source)} · ${when}</span>
                    </div>
                </summary>
                <div class="news-body">
                    ${summary}
                    ${matched}
                    <p><a href="${item.url}" target="_blank" rel="noopener">Read on ${escapeHtml(item.source)} ↗</a></p>
                </div>
            </details>
        </li>`;
    }).join("");
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

// ------------------ boot ------------------
document.getElementById("chart-range").addEventListener("change", (e) => {
    loadPrices(parseInt(e.target.value, 10)).catch(console.error);
});
document.getElementById("calc-litres").addEventListener("input", updateCalculator);
document.getElementById("calc-fuel").addEventListener("change", updateCalculator);

Promise.all([loadPrices(26), loadPrediction(), loadNews()])
    .then(() => updateCalculator())
    .catch(err => console.error("Dashboard load failed:", err));
