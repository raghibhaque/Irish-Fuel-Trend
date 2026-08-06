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
        // Only the last 8 calls in the LED strip — the wider backtest feeds
        // the hit-rate scorecard.
        const bt = p.backtest || [];
        renderBacktest(card.querySelector(".bt-list"), bt.slice(-8));
        renderHitRate(card.querySelector(".hr-list"), bt);
    });
    renderDecision(data);
    document.getElementById("prediction-notes").textContent = (data.notes || []).join("  ");
    updateCalculator();
}

// Hit-rate = fraction of last-N one-step-ahead calls whose direction matched.
// Uses whatever's in `backtest` (up to 52 rows), rounds to the nearest %.
function renderHitRate(list, points) {
    if (!list) return;
    const rate = (n) => {
        const slice = points.slice(-n);
        if (!slice.length) return null;
        const hits = slice.filter(pt => pt.direction_correct).length;
        return hits / slice.length;
    };
    const grade = (r) => r == null ? "muted" : r >= 0.6 ? "good" : r >= 0.5 ? "ok" : "bad";
    const windows = [
        { key: "8w",  n: 8  },
        { key: "26w", n: 26 },
        { key: "52w", n: 52 },
    ];
    list.querySelectorAll("li").forEach((li, i) => {
        const r = rate(windows[i].n);
        const val = li.querySelector(".hr-value");
        val.textContent = r == null ? "—" : `${Math.round(r * 100)}%`;
        li.dataset.grade = grade(r);
    });
}

// "Fill now vs wait" call. Uses the same 3-week horizon as the calculator and
// the model's own confidence to grade the strength of the recommendation.
const DECISION_REF_LITRES = 60;

function renderDecision(data) {
    ["petrol", "diesel"].forEach(fuel => {
        const p = data[fuel];
        const card = document.getElementById(`decision-${fuel}`);
        if (!card || !p) return;
        const now  = p.current_pump_eur_per_l;
        const then = p.predicted_pump_3w_eur_per_l;
        const perL = then - now;
        const perFill = perL * DECISION_REF_LITRES;
        // Anything under 0.5 c/L is inside the model's slop — call it a wash.
        const signal = perL > 0.005 ? "fill" : perL < -0.005 ? "wait" : "neutral";
        const verdict = { fill: "Fill now", wait: "Wait", neutral: "Either" }[signal];
        const abs = Math.abs(perFill).toFixed(2);
        const detail = signal === "fill"
            ? `Predicted +€${(perL * 100).toFixed(1)}c/L in ~3 weeks. Fill a ${DECISION_REF_LITRES} L tank now, save about €${abs}.`
            : signal === "wait"
                ? `Predicted −€${(Math.abs(perL) * 100).toFixed(1)}c/L in ~3 weeks. Delay a ${DECISION_REF_LITRES} L fill, save about €${abs}.`
                : "Predicted move is inside the model's noise floor. Fill whenever — the timing barely matters.";
        card.querySelector(".decision-light").dataset.signal = signal;
        card.querySelector(".decision-verdict").textContent = verdict;
        card.querySelector(".decision-detail").textContent = detail;
        card.querySelector(".decision-conf b").textContent = `${Math.round(p.confidence * 100)}%`;
        card.dataset.signal = signal;
    });
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
