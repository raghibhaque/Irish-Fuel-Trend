// Dashboard controller — hits /api/prices, /api/prediction, /api/news
// and renders into the elements in index.html.

const API = ""; // same-origin — FastAPI serves the frontend

const fmtEur = (v) => (v == null ? "—" : `€${v.toFixed(3)}`);
const fmtPct = (v) => (v == null ? "—" : `${(v * 100).toFixed(2)}%`);
const fmtDate = (iso) => {
    const d = new Date(iso);
    return d.toLocaleDateString("en-IE", { year: "numeric", month: "short", day: "numeric" });
};

let chart = null;

async function jget(path) {
    const r = await fetch(`${API}${path}`);
    if (!r.ok) throw new Error(`${path} → ${r.status}`);
    return r.json();
}

// ------------------ prices + chart ------------------
async function loadPrices(weeks = 26) {
    const data = await jget(`/api/prices?weeks=${weeks}`);

    // current price cards use the latest points
    document.getElementById("current-petrol").textContent = fmtEur(data.petrol.latest?.price_eur_per_litre);
    document.getElementById("current-diesel").textContent = fmtEur(data.diesel.latest?.price_eur_per_litre);
    document.getElementById("asof-petrol").textContent = data.petrol.latest ? `as of ${fmtDate(data.petrol.latest.date)}` : "";
    document.getElementById("asof-diesel").textContent = data.diesel.latest ? `as of ${fmtDate(data.diesel.latest.date)}` : "";

    renderChart(data);
}

function renderChart(data) {
    const labels = data.petrol.points.map(p => p.date);
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
    const data = await jget("/api/prediction");
    ["petrol", "diesel"].forEach(fuel => {
        const p = data[fuel];
        const card = document.getElementById(`pred-${fuel}`);
        card.querySelector(".trend-pill").textContent = p.trend;
        card.querySelector(".trend-pill").setAttribute("data-trend", p.trend);
        card.querySelector(".pred-explain").textContent = p.explanation;
        card.querySelector(".pred-delta").textContent = fmtPct(p.predicted_weekly_return);
        card.querySelector(".pred-conf").textContent  = `${Math.round(p.confidence * 100)}%`;
        card.querySelector(".pred-r2").textContent    = p.r2.toFixed(2);
    });
    document.getElementById("prediction-notes").textContent = (data.notes || []).join("  ");
}

// ------------------ news ------------------
async function loadNews() {
    const data = await jget("/api/news?limit=20");
    const list = document.getElementById("news-list");
    if (!data.items || data.items.length === 0) {
        list.innerHTML = `<li class="empty">No news items yet — RSS monitor not wired up (step 8).</li>`;
        return;
    }
    list.innerHTML = data.items.map(item => `
        <li>
            <a href="${item.url}" target="_blank" rel="noopener">${escapeHtml(item.title)}</a>
            <span class="meta">${escapeHtml(item.source)} · ${new Date(item.published_at).toLocaleString("en-IE")}${item.matched_keywords ? ` · matched: ${escapeHtml(item.matched_keywords)}` : ""}</span>
        </li>`).join("");
}

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
}

// ------------------ boot ------------------
document.getElementById("chart-range").addEventListener("change", (e) => {
    loadPrices(parseInt(e.target.value, 10)).catch(console.error);
});

Promise.all([loadPrices(26), loadPrediction(), loadNews()])
    .catch(err => console.error("Dashboard load failed:", err));
