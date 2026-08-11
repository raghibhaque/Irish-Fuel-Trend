// National dashboard controller. Reads static JSON snapshots from ./data/*.json
// so the same bundle works both under FastAPI (local dev) and GitHub Pages
// (static). Formatting, chart theme, and sparkline helpers live in shared.js,
// which county.js also uses.

let chart = null;
let priceData = null;   // full history, cached in memory; range selector filters this
let predictionData = null;  // cached so calculator can react to input changes without refetch

// ------------------ prices + chart ------------------
let currentRangeWeeks = 26;

async function loadPrices(weeks = 26) {
    currentRangeWeeks = weeks;
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

// Push `days` calendar days onto an ISO yyyy-mm-dd date.
function shiftIso(iso, days) {
    const d = new Date(iso);
    d.setUTCDate(d.getUTCDate() + days);
    return d.toISOString().slice(0, 10);
}

// Build forecast tail datasets (line + band) per fuel. Returns null when
// prediction data isn't loaded yet — chart still renders history-only.
function forecastDatasetsFor(fuel, histPointsLen, lastIso, lastPrice) {
    if (!predictionData || !lastIso || lastPrice == null) return null;
    const p = predictionData[fuel];
    if (!p) return null;
    // The forecast segment anchors on the last historical point so the line
    // visually continues from history into projection without a gap.
    const nulls = Array(histPointsLen - 1).fill(null);
    const line = [...nulls, lastPrice, p.predicted_pump_eur_per_l, p.predicted_pump_3w_eur_per_l];
    // 50% band widths: model ships symmetric high/low for 1w. For 3w we don't
    // ship a native band, so we scale the 1w half-width by sqrt(3) (random-walk
    // approximation on weekly returns) — signposts uncertainty widens.
    const half1w = (p.predicted_pump_high_eur_per_l - p.predicted_pump_low_eur_per_l) / 2;
    const half3w = half1w * Math.sqrt(3);
    const low  = [...nulls, lastPrice, p.predicted_pump_low_eur_per_l,  p.predicted_pump_3w_eur_per_l - half3w];
    const high = [...nulls, lastPrice, p.predicted_pump_high_eur_per_l, p.predicted_pump_3w_eur_per_l + half3w];
    return { line, low, high };
}

function renderChart(data) {
    const histLabels = data.petrol.points.map(p => fmtDMY(p.date));
    const petrolHist = data.petrol.points.map(p => p.price_eur_per_litre);
    const dieselHist = data.diesel.points.map(p => p.price_eur_per_litre);

    const lastIso = data.petrol.latest?.date || data.diesel.latest?.date;
    const wantForecast = !!(predictionData && lastIso);
    const forecastLabels = wantForecast
        ? [fmtDMY(shiftIso(lastIso, 7)), fmtDMY(shiftIso(lastIso, 21))]
        : [];
    const labels = [...histLabels, ...forecastLabels];

    const histLen = histLabels.length;
    const tail = (arr) => wantForecast ? [...arr, null, null] : arr;

    const datasets = [
        {
            label: "Petrol (95)",
            data: tail(petrolHist),
            borderColor: FUEL_COLORS.petrol.line,
            backgroundColor: FUEL_COLORS.petrol.fill,
            tension: 0.25,
            pointRadius: 0,
            borderWidth: 2,
            fill: true,
        },
        {
            label: "Diesel",
            data: tail(dieselHist),
            borderColor: FUEL_COLORS.diesel.line,
            backgroundColor: FUEL_COLORS.diesel.fill,
            tension: 0.25,
            pointRadius: 0,
            borderWidth: 2,
            fill: true,
        },
    ];

    if (wantForecast) {
        const lastPetrol = data.petrol.latest?.price_eur_per_litre;
        const lastDiesel = data.diesel.latest?.price_eur_per_litre;
        const pFcast = forecastDatasetsFor("petrol", histLen, lastIso, lastPetrol);
        const dFcast = forecastDatasetsFor("diesel", histLen, lastIso, lastDiesel);

        // Band is a pair of hidden line datasets — the upper fills down to the
        // lower via `fill: '-1'`. Order matters: low BEFORE high in the array.
        if (pFcast) {
            datasets.push(
                {
                    label: "Petrol 50% band low",
                    data: pFcast.low,
                    borderColor: "rgba(0,0,0,0)",
                    backgroundColor: "rgba(0,0,0,0)",
                    pointRadius: 0,
                    fill: false,
                    tension: 0.25,
                    spanGaps: false,
                    // Hidden from legend + tooltip — it exists purely as the
                    // fill anchor for the "band high" dataset.
                    __hideLegend: true,
                    __hideTooltip: true,
                },
                {
                    label: "Petrol 50% band",
                    data: pFcast.high,
                    borderColor: "rgba(0,0,0,0)",
                    backgroundColor: "rgba(255, 182, 72, 0.18)",
                    pointRadius: 0,
                    fill: "-1",
                    tension: 0.25,
                    spanGaps: false,
                    __hideTooltip: true,
                },
                {
                    label: "Petrol forecast",
                    data: pFcast.line,
                    borderColor: FUEL_COLORS.petrol.line,
                    backgroundColor: "rgba(0,0,0,0)",
                    borderDash: [4, 4],
                    borderWidth: 2,
                    pointRadius: 3,
                    pointBackgroundColor: FUEL_COLORS.petrol.line,
                    fill: false,
                    tension: 0.25,
                    spanGaps: false,
                },
            );
        }
        if (dFcast) {
            datasets.push(
                {
                    label: "Diesel 50% band low",
                    data: dFcast.low,
                    borderColor: "rgba(0,0,0,0)",
                    backgroundColor: "rgba(0,0,0,0)",
                    pointRadius: 0,
                    fill: false,
                    tension: 0.25,
                    spanGaps: false,
                    __hideLegend: true,
                    __hideTooltip: true,
                },
                {
                    label: "Diesel 50% band",
                    data: dFcast.high,
                    borderColor: "rgba(0,0,0,0)",
                    backgroundColor: "rgba(123, 211, 207, 0.18)",
                    pointRadius: 0,
                    fill: "-1",
                    tension: 0.25,
                    spanGaps: false,
                    __hideTooltip: true,
                },
                {
                    label: "Diesel forecast",
                    data: dFcast.line,
                    borderColor: FUEL_COLORS.diesel.line,
                    backgroundColor: "rgba(0,0,0,0)",
                    borderDash: [4, 4],
                    borderWidth: 2,
                    pointRadius: 3,
                    pointBackgroundColor: FUEL_COLORS.diesel.line,
                    fill: false,
                    tension: 0.25,
                    spanGaps: false,
                },
            );
        }
    }

    const ctx = document.getElementById("price-chart").getContext("2d");
    if (chart) chart.destroy();

    const opts = baseChartOptions();
    // Hide legend entries flagged with __hideLegend, and suppress tooltip lines
    // for datasets flagged with __hideTooltip (band anchors + fill layers).
    opts.plugins.legend.labels.filter = (item, data) =>
        !data.datasets[item.datasetIndex].__hideLegend;
    const origLabel = opts.plugins.tooltip.callbacks.label;
    opts.plugins.tooltip.callbacks.label = (c) =>
        c.dataset.__hideTooltip ? null : origLabel(c);

    chart = new Chart(ctx, {
        type: "line",
        data: { labels, datasets },
        options: opts,
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
    // If the historical chart already rendered before predictionData arrived,
    // redraw it so the forecast tail + confidence band appear.
    if (priceData) {
        renderChart({
            petrol: sliceByWeeks(priceData.petrol, currentRangeWeeks),
            diesel: sliceByWeeks(priceData.diesel, currentRangeWeeks),
        });
    }
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
        // Trend=unknown means an upstream is running on synthetic fallback
        // data (see backend). Suppress the verdict rather than render a
        // green "Fill now" against a sine-wave forecast.
        if (p.trend === "unknown") {
            card.querySelector(".decision-light").dataset.signal = "neutral";
            card.querySelector(".decision-verdict").textContent = "—";
            card.querySelector(".decision-detail").textContent =
                "Awaiting real market data. Recommendation suppressed while an upstream feed is running on the synthetic fallback generator.";
            card.querySelector(".decision-conf b").textContent = "—";
            card.dataset.signal = "neutral";
            return;
        }
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
let calcHorizonWeeks = 3;

// Extrapolate the pump-price forecast to an arbitrary horizon by compounding
// the model's shipped weekly return. Reproduces the backend's two shipped
// points exactly at N=1 and N=3, and matches the same formula the backend
// uses for the 3-week point (pump = current + wholesale·((1+ret)^N − 1)·(1+VAT))
// without needing to ship the wholesale anchor to the client.
function predictedPumpAtWeeks(p, weeks) {
    if (weeks === 1) return p.predicted_pump_eur_per_l;
    if (weeks === 3) return p.predicted_pump_3w_eur_per_l;
    const ret = p.predicted_weekly_return;
    const now = p.current_pump_eur_per_l;
    // Near-zero weekly return means the model sees no signal — hold flat
    // instead of dividing by a value indistinguishable from noise.
    if (Math.abs(ret) < 1e-6) return now;
    const oneWeekDelta = p.predicted_pump_eur_per_l - now;
    const factor = oneWeekDelta / ret;
    return now + factor * (Math.pow(1 + ret, weeks) - 1);
}

function updateCalculator() {
    if (!predictionData) return;
    const fuel   = document.getElementById("calc-fuel").value;
    const litres = parseFloat(document.getElementById("calc-litres").value) || 0;
    const p = predictionData[fuel];
    if (!p) return;
    // Same reasoning as renderDecision: forecast on synthetic data is not a
    // forecast. Zero out the difference line instead of extrapolating.
    if (p.trend === "unknown") {
        const nowCost = litres * (p.current_pump_eur_per_l || 0);
        document.getElementById("calc-now").textContent  = `Today: €${nowCost.toFixed(2)}`;
        document.getElementById("calc-then").textContent = `In ~${calcHorizonWeeks} weeks: —`;
        const diffEl = document.getElementById("calc-diff");
        diffEl.textContent = "Difference: — (feed on synthetic fallback)";
        diffEl.setAttribute("data-sign", "flat");
        return;
    }
    const weeks     = calcHorizonWeeks;
    const thenPrice = predictedPumpAtWeeks(p, weeks);
    const nowCost   = litres * p.current_pump_eur_per_l;
    const thenCost  = litres * thenPrice;
    const diff      = thenCost - nowCost;
    const sign      = diff > 0.005 ? "up" : diff < -0.005 ? "down" : "flat";
    const verb      = diff > 0 ? "more" : diff < 0 ? "less" : "same";
    document.getElementById("calc-now").textContent  = `Today: €${nowCost.toFixed(2)}`;
    document.getElementById("calc-then").textContent = `In ~${weeks} week${weeks === 1 ? "" : "s"}: €${thenCost.toFixed(2)}`;
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
        // RSS URLs come from untrusted third-party feeds — validate scheme
        // AND attribute-escape. Drop the link entirely if the URL is not a
        // plain http(s) URL so a hijacked feed cannot ship a javascript: click.
        const safeHref = safeUrl(item.url);
        const readLink = safeHref
            ? `<p><a href="${safeHref}" target="_blank" rel="noopener">Read on ${escapeHtml(item.source)} ↗</a></p>`
            : "";
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
                    ${readLink}
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
document.getElementById("calc-horizon").addEventListener("click", (e) => {
    const btn = e.target.closest(".horizon-chip");
    if (!btn) return;
    const weeks = parseInt(btn.dataset.weeks, 10);
    if (!weeks || weeks === calcHorizonWeeks) return;
    calcHorizonWeeks = weeks;
    document.querySelectorAll("#calc-horizon .horizon-chip").forEach(el => {
        const active = parseInt(el.dataset.weeks, 10) === weeks;
        el.classList.toggle("is-active", active);
        el.setAttribute("aria-selected", active ? "true" : "false");
    });
    updateCalculator();
});

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
    mountDevIngestButton({
        onDone: async () => {
            priceData = null;
            predictionData = null;
            await Promise.all([loadPrices(26), loadPrediction(), loadNews()]);
            updateCalculator();
        },
    });
}).catch(err => console.error("Dashboard load failed:", err));
