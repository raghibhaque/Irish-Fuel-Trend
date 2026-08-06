// National dashboard controller. Reads static JSON snapshots from ./data/*.json
// so the same bundle works both under FastAPI (local dev) and GitHub Pages
// (static). Formatting, chart theme, and sparkline helpers live in shared.js,
// which county.js also uses.

let chart = null;
let priceData = null;   // full history, cached in memory; range selector filters this
let predictionData = null;  // cached so calculator can react to input changes without refetch

// ------------------ prices + chart ------------------
async function loadPrices(weeks = 26) {
    if (!priceData) priceData = await jget("data/prices.json");
    const view = {
        petrol: sliceByWeeks(priceData.petrol, weeks),
        diesel: sliceByWeeks(priceData.diesel, weeks),
    };

    document.getElementById("current-petrol").textContent = fmtEur(view.petrol.latest?.price_eur_per_litre);
    document.getElementById("current-diesel").textContent = fmtEur(view.diesel.latest?.price_eur_per_litre);
    const upd = window.__updatedAtLabel || "";
    document.getElementById("asof-petrol").textContent = view.petrol.latest ? `as of ${fmtDate(view.petrol.latest.date)}${upd}` : "";
    document.getElementById("asof-diesel").textContent = view.diesel.latest ? `as of ${fmtDate(view.diesel.latest.date)}${upd}` : "";

    // Sparklines always use the last 12 weeks from the full series, not the
    // range-filtered view, so they stay stable when the user swaps ranges.
    renderSparkline("spark-petrol", priceData.petrol.points.slice(-12));
    renderSparkline("spark-diesel", priceData.diesel.points.slice(-12));

    renderChart(view);
}

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
                    borderColor: FUEL_COLORS.petrol.line,
                    backgroundColor: FUEL_COLORS.petrol.fill,
                    tension: 0.25,
                    pointRadius: 0,
                    borderWidth: 2,
                    fill: true,
                },
                {
                    label: "Diesel",
                    data: diesel,
                    borderColor: FUEL_COLORS.diesel.line,
                    backgroundColor: FUEL_COLORS.diesel.fill,
                    tension: 0.25,
                    pointRadius: 0,
                    borderWidth: 2,
                    fill: true,
                },
            ],
        },
        options: baseChartOptions(),
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

// ------------------ boot ------------------
document.getElementById("chart-range").addEventListener("change", (e) => {
    loadPrices(parseInt(e.target.value, 10)).catch(console.error);
});
document.getElementById("calc-litres").addEventListener("input", updateCalculator);
document.getElementById("calc-fuel").addEventListener("change", updateCalculator);

loadManifest().then(m => {
    if (m && m.generated_at) {
        window.__updatedAtLabel = ` (updated at: ${fmtDateTime(m.generated_at)})`;
        const chip = document.getElementById("hdr-updated");
        if (chip) chip.textContent = `Updated ${fmtDateTime(m.generated_at)}`;
    } else {
        const chip = document.getElementById("hdr-updated");
        if (chip) { chip.textContent = "Awaiting refresh"; chip.dataset.tone = "warn"; }
    }
    return Promise.all([loadPrices(26), loadPrediction(), loadNews()]);
}).then(() => {
    updateCalculator();
    attachDragCompare("price-chart", "dc-popup");
}).catch(err => console.error("Dashboard load failed:", err));
