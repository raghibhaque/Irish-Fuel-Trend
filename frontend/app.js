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

    renderChart(view);
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
    });
    document.getElementById("prediction-notes").textContent = (data.notes || []).join("  ");
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

Promise.all([loadPrices(26), loadPrediction(), loadNews()])
    .catch(err => console.error("Dashboard load failed:", err));
