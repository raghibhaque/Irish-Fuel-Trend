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
    // The forecast tail only appears once predictionData is loaded; the
    // second render in loadPrediction() picks that up.
    const petrolPts = priceData.petrol.points.slice(-12);
    const dieselPts = priceData.diesel.points.slice(-12);
    const petrolAnchor = petrolPts[petrolPts.length - 1]?.price_eur_per_litre;
    const dieselAnchor = dieselPts[dieselPts.length - 1]?.price_eur_per_litre;
    renderSparkline("spark-petrol", petrolPts, {
        forecast: sparklineForecastFromPrediction(predictionData?.petrol, petrolAnchor),
    });
    renderSparkline("spark-diesel", dieselPts, {
        forecast: sparklineForecastFromPrediction(predictionData?.diesel, dieselAnchor),
    });

    renderChart(view);
}

// Push `days` calendar days onto an ISO yyyy-mm-dd date.
function shiftIso(iso, days) {
    const d = new Date(iso);
    d.setUTCDate(d.getUTCDate() + days);
    return d.toISOString().slice(0, 10);
}

// Forecast tail horizon in weeks. Draws a smooth uncertainty cone rather than
// the previous 1w + 3w anchor points — makes it visually obvious that the
// band widens further out. 6 is where the weekly model's signal starts to
// fade (see calc-hint in index.html); no point extending past that.
const FORECAST_WEEKS = 6;

// Build forecast tail datasets (line + band) per fuel. Returns null when
// prediction data isn't loaded yet — chart still renders history-only.
//
// Band widening follows a random-walk sqrt(t) scaling on the shipped 1-week
// half-width. The backend only ships a native band at t=1w, so anything past
// that is a linear-model approximation, not a fresh quantile prediction. It
// still communicates the right thing: uncertainty grows with horizon.
function forecastDatasetsFor(fuel, histPointsLen, lastIso, lastPrice) {
    if (!predictionData || !lastIso || lastPrice == null) return null;
    const p = predictionData[fuel];
    if (!p) return null;
    const nulls = Array(histPointsLen - 1).fill(null);
    const half1w = (p.predicted_pump_high_eur_per_l - p.predicted_pump_low_eur_per_l) / 2;
    // Anchor on the last historical point so the tail visually continues from
    // history into projection without a gap, then extend one row per week out
    // to FORECAST_WEEKS.
    const line = [lastPrice];
    const low  = [lastPrice];
    const high = [lastPrice];
    for (let w = 1; w <= FORECAST_WEEKS; w++) {
        const centre = predictedPumpAtWeeks(p, w);
        const half   = half1w * Math.sqrt(w);
        line.push(centre);
        low.push(centre - half);
        high.push(centre + half);
    }
    return {
        line: [...nulls, ...line],
        low:  [...nulls, ...low],
        high: [...nulls, ...high],
    };
}

function renderChart(data) {
    const histLabels = data.petrol.points.map(p => fmtDMY(p.date));
    const petrolHist = data.petrol.points.map(p => p.price_eur_per_litre);
    const dieselHist = data.diesel.points.map(p => p.price_eur_per_litre);

    const lastIso = data.petrol.latest?.date || data.diesel.latest?.date;
    const wantForecast = !!(predictionData && lastIso);
    const forecastLabels = wantForecast
        ? Array.from({ length: FORECAST_WEEKS },
                     (_, i) => fmtDMY(shiftIso(lastIso, (i + 1) * 7)))
        : [];
    const labels = [...histLabels, ...forecastLabels];

    const histLen = histLabels.length;
    const forecastNulls = Array(FORECAST_WEEKS).fill(null);
    const tail = (arr) => wantForecast ? [...arr, ...forecastNulls] : arr;

    const datasets = [
        {
            label: "Petrol (95)",
            data: tail(petrolHist),
            borderColor: FUEL_COLORS.petrol.line,
            backgroundColor: (ctx) => verticalGradient(
                ctx, FUEL_GRADIENT.petrol.from, FUEL_GRADIENT.petrol.to,
            ),
            tension: 0.25,
            pointRadius: 0,
            borderWidth: 2,
            fill: "origin",
        },
        {
            label: "Diesel",
            data: tail(dieselHist),
            borderColor: FUEL_COLORS.diesel.line,
            backgroundColor: (ctx) => verticalGradient(
                ctx, FUEL_GRADIENT.diesel.from, FUEL_GRADIENT.diesel.to,
            ),
            tension: 0.25,
            pointRadius: 0,
            borderWidth: 2,
            fill: "origin",
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
                    label: "Petrol 80% band low",
                    data: pFcast.low,
                    borderColor: "rgba(255, 182, 72, 0.55)",
                    borderDash: [2, 3],
                    borderWidth: 1,
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
                    label: "Petrol 80% band",
                    data: pFcast.high,
                    borderColor: "rgba(255, 182, 72, 0.55)",
                    borderDash: [2, 3],
                    borderWidth: 1,
                    backgroundColor: "rgba(255, 182, 72, 0.32)",
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
                    label: "Diesel 80% band low",
                    data: dFcast.low,
                    borderColor: "rgba(123, 211, 207, 0.55)",
                    borderDash: [2, 3],
                    borderWidth: 1,
                    backgroundColor: "rgba(0,0,0,0)",
                    pointRadius: 0,
                    fill: false,
                    tension: 0.25,
                    spanGaps: false,
                    __hideLegend: true,
                    __hideTooltip: true,
                },
                {
                    label: "Diesel 80% band",
                    data: dFcast.high,
                    borderColor: "rgba(123, 211, 207, 0.55)",
                    borderDash: [2, 3],
                    borderWidth: 1,
                    backgroundColor: "rgba(123, 211, 207, 0.30)",
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

    // -------- polish plugin options ----------
    // Pinned latest-value pill on each history line's endpoint.
    const lastP = petrolHist[petrolHist.length - 1];
    const lastD = dieselHist[dieselHist.length - 1];
    const tags = [];
    if (typeof lastP === "number") tags.push({
        datasetIndex: 0, index: histLen - 1,
        color: FUEL_COLORS.petrol.line, text: `€${lastP.toFixed(3)}`,
    });
    if (typeof lastD === "number") tags.push({
        datasetIndex: 1, index: histLen - 1,
        color: FUEL_COLORS.diesel.line, text: `€${lastD.toFixed(3)}`,
    });
    opts.plugins.latestTags = { tags };

    // "NOW" divider only when there is a forecast tail to divide off.
    opts.plugins.nowDivider = wantForecast
        ? { index: histLen - 1, datasetIndex: 0, label: "NOW" }
        : null;

    // 12-month rolling mean per fuel, computed from the visible slice so the
    // reference tracks the range the user picked rather than always being the
    // last-52-weeks. Falls back to the whole visible window when the slice is
    // shorter than a year.
    const meanOf = (arr) => {
        const cleaned = arr.filter(v => typeof v === "number");
        if (!cleaned.length) return null;
        return cleaned.reduce((a, b) => a + b, 0) / cleaned.length;
    };
    const refWindow = 52;
    const refs = [];
    const meanP = meanOf(petrolHist.slice(-Math.min(refWindow, petrolHist.length)));
    const meanD = meanOf(dieselHist.slice(-Math.min(refWindow, dieselHist.length)));
    if (petrolHist.length >= 8 && meanP != null) refs.push({
        color: FUEL_COLORS.petrol.line,
        value: meanP,
        label: `Petrol avg €${meanP.toFixed(3)}`,
    });
    if (dieselHist.length >= 8 && meanD != null) refs.push({
        color: FUEL_COLORS.diesel.line,
        value: meanD,
        label: `Diesel avg €${meanD.toFixed(3)}`,
    });
    opts.plugins.refLines = { refs };
    opts.plugins.crosshair = { enabled: true };

    chart = new Chart(ctx, {
        type: "line",
        data: { labels, datasets },
        options: opts,
    });

    // Screen-reader summary — the canvas itself is opaque to assistive tech.
    // This is not a full data table (that would be a much larger change), but
    // it covers the two headline questions a sighted user gets at a glance:
    // latest values and the range over the selected window.
    const summary = document.getElementById("chart-a11y-summary");
    if (summary) {
        const pRange = petrolHist.length ? [Math.min(...petrolHist), Math.max(...petrolHist)] : null;
        const dRange = dieselHist.length ? [Math.min(...dieselHist), Math.max(...dieselHist)] : null;
        const lastP = petrolHist[petrolHist.length - 1];
        const lastD = dieselHist[dieselHist.length - 1];
        const parts = [];
        if (lastP != null) parts.push(`Petrol latest €${lastP.toFixed(3)} per litre` +
            (pRange ? `, range €${pRange[0].toFixed(3)}–€${pRange[1].toFixed(3)}` : ""));
        if (lastD != null) parts.push(`Diesel latest €${lastD.toFixed(3)} per litre` +
            (dRange ? `, range €${dRange[0].toFixed(3)}–€${dRange[1].toFixed(3)}` : ""));
        summary.textContent = parts.join(". ") + (parts.length ? "." : "");
    }
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
        const badge = card.querySelector(".confidence-badge");
        if (badge) {
            const tier = p.confidence_tier || "medium";
            const labels = {
                high: "high skill",
                medium: "medium skill",
                low: "low skill",
                exploratory: "exploratory",
            };
            badge.dataset.tier = tier;
            badge.textContent = labels[tier] || tier;
            const skill = typeof p.r2 === "number" ? p.r2.toFixed(2) : "—";
            const spread = typeof p.ensemble_spread_pct === "number" ? p.ensemble_spread_pct.toFixed(2) : "—";
            badge.title =
                `Model confidence tier: ${tier}. ` +
                `Walk-forward R² = ${skill}. ` +
                `Ensemble disagreement = ${spread}×typical residual. ` +
                `High = strong skill and members agree; low = members disagree on today's inputs; ` +
                `exploratory = no demonstrable out-of-sample edge.`;
        }
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
    wireShareButtons();
    initSharePanel().catch(err => console.error(err));
    document.getElementById("prediction-notes").textContent = (data.notes || []).join("  ");
    updateCalculator();
    // If the historical chart already rendered before predictionData arrived,
    // redraw it so the forecast tail + confidence band appear.
    if (priceData) {
        renderChart({
            petrol: sliceByWeeks(priceData.petrol, currentRangeWeeks),
            diesel: sliceByWeeks(priceData.diesel, currentRangeWeeks),
        });
        // Same reason for the sparkline: it renders inside loadPrices, which
        // may have run before predictionData was ready.
        const petrolPts = priceData.petrol.points.slice(-12);
        const dieselPts = priceData.diesel.points.slice(-12);
        renderSparkline("spark-petrol", petrolPts, {
            forecast: sparklineForecastFromPrediction(
                predictionData.petrol,
                petrolPts[petrolPts.length - 1]?.price_eur_per_litre,
            ),
        });
        renderSparkline("spark-diesel", dieselPts, {
            forecast: sparklineForecastFromPrediction(
                predictionData.diesel,
                dieselPts[dieselPts.length - 1]?.price_eur_per_litre,
            ),
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

// ------------------ share card ------------------
// Renders the current "Fill now / Wait / Either" call as a 1200×630 PNG that
// slots into WhatsApp / iMessage / Twitter previews at the OG-standard size.
// All drawing happens client-side so this works on GitHub Pages with zero
// backend and zero third-party network calls.

const SHARE_W = 1200;
const SHARE_H = 630;

// Waits for the exact weight/family combos we're about to draw with. Canvas
// does not trigger the same lazy font-load path that live text does, so we
// have to ask for them explicitly or fall back to system-ui the first time.
async function ensureShareFonts() {
    if (!document.fonts || !document.fonts.load) return;
    try {
        await Promise.all([
            document.fonts.load('700 140px "IBM Plex Sans Condensed"'),
            document.fonts.load('600 32px "IBM Plex Sans"'),
            document.fonts.load('500 22px "IBM Plex Mono"'),
            document.fonts.load('600 48px "IBM Plex Sans"'),
        ]);
    } catch { /* fall back to system font — not fatal */ }
}

// Draws text and returns the block height it consumed. Handles word wrap by
// greedy splitting on spaces — good enough for our short verdict + detail
// strings; no need for a real hyphenation pass.
function drawWrappedText(ctx, text, x, y, maxWidth, lineHeight) {
    const words = String(text).split(/\s+/);
    let line = "";
    let lines = 0;
    for (const word of words) {
        const test = line ? line + " " + word : word;
        if (ctx.measureText(test).width > maxWidth && line) {
            ctx.fillText(line, x, y + lines * lineHeight);
            line = word;
            lines += 1;
        } else {
            line = test;
        }
    }
    if (line) {
        ctx.fillText(line, x, y + lines * lineHeight);
        lines += 1;
    }
    return lines * lineHeight;
}

function buildShareCardCanvas(opts) {
    const canvas = document.createElement("canvas");
    canvas.width  = SHARE_W;
    canvas.height = SHARE_H;
    const ctx = canvas.getContext("2d");

    // ---- palette (mirrors style.css :root vars, hardcoded because canvas
    // ---- has no CSS var access) ----
    const BG        = "#0a0b0d";
    const SURFACE   = "#101215";
    const RULE      = "#23272e";
    const INK       = "#ece7d8";
    const INK_MID   = "#b6b2a6";
    const INK_DIM   = "#7c828c";
    const AMBER     = "#ffb648";
    const UP        = "#ff5c4a";
    const DOWN      = "#7bd88f";
    const FLAT      = "#d6b45a";
    const signalColor = { fill: UP, wait: DOWN, neutral: FLAT }[opts.signal] || FLAT;

    // ---- background: petroleum black with a faint amber radial in the top-
    // ---- right corner, matching the decision-card gradient in style.css ----
    ctx.fillStyle = BG;
    ctx.fillRect(0, 0, SHARE_W, SHARE_H);
    const grad = ctx.createRadialGradient(SHARE_W, 0, 40, SHARE_W, 0, 800);
    grad.addColorStop(0, "rgba(255, 182, 72, 0.14)");
    grad.addColorStop(1, "rgba(255, 182, 72, 0)");
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, SHARE_W, SHARE_H);

    // Hairline border and inner rule so the exported PNG reads as a framed
    // panel on any messaging-app background, not just dark ones.
    ctx.strokeStyle = RULE;
    ctx.lineWidth = 2;
    ctx.strokeRect(1, 1, SHARE_W - 2, SHARE_H - 2);

    const PAD_X = 72;
    let cursorY = 88;

    // ---- eyebrow ----
    ctx.fillStyle = INK_DIM;
    ctx.font = '500 22px "IBM Plex Mono", ui-monospace, Consolas, monospace';
    ctx.textBaseline = "alphabetic";
    ctx.fillText("IRISH FUEL TREND  ·  IE  ·  3-WEEK OUTLOOK", PAD_X, cursorY);

    // ---- fuel label + LED ----
    cursorY += 60;
    const ledR = 14;
    ctx.beginPath();
    ctx.arc(PAD_X + ledR, cursorY - 12, ledR, 0, Math.PI * 2);
    ctx.fillStyle = signalColor;
    ctx.fill();
    // Soft glow around the LED — approximated with a second, larger, translucent
    // circle. Canvas has no box-shadow, so this is the manual equivalent.
    ctx.beginPath();
    ctx.arc(PAD_X + ledR, cursorY - 12, ledR * 2.4, 0, Math.PI * 2);
    const ledGlow = ctx.createRadialGradient(
        PAD_X + ledR, cursorY - 12, ledR,
        PAD_X + ledR, cursorY - 12, ledR * 2.4,
    );
    ledGlow.addColorStop(0, signalColor + "66");
    ledGlow.addColorStop(1, signalColor + "00");
    ctx.fillStyle = ledGlow;
    ctx.fill();

    ctx.fillStyle = INK_MID;
    ctx.font = '600 32px "IBM Plex Sans", ui-sans-serif, system-ui, sans-serif';
    ctx.fillText(opts.fuelLabel, PAD_X + ledR * 2 + 22, cursorY);

    // ---- verdict (huge display type) ----
    cursorY += 130;
    ctx.fillStyle = signalColor;
    ctx.font = '700 120px "IBM Plex Sans Condensed", "IBM Plex Sans", ui-sans-serif, sans-serif';
    ctx.fillText(opts.verdict.toUpperCase(), PAD_X, cursorY);

    // ---- price ladder: NOW, 1W, 2W, 3W. The featured horizon (opts.weeks)
    // ---- reads in accent, the others in dim text — a glance shows which
    // ---- horizon the verdict is tied to without hiding the full arc.
    cursorY += 50;
    const rungs = [
        { label: "NOW", value: opts.now,  weeks: 0 },
        { label: "1W",  value: opts.p1w,  weeks: 1 },
        { label: "2W",  value: opts.p2w,  weeks: 2 },
        { label: "3W",  value: opts.p3w,  weeks: 3 },
    ];
    const rungW = (SHARE_W - PAD_X * 2) / rungs.length;
    rungs.forEach((r, i) => {
        const cx = PAD_X + rungW * i + rungW / 2;
        const active = r.weeks === opts.weeks;
        ctx.textAlign = "center";
        ctx.fillStyle = INK_DIM;
        ctx.font = '500 20px "IBM Plex Mono", ui-monospace, Consolas, monospace';
        ctx.fillText(r.label, cx, cursorY);
        ctx.fillStyle = active ? signalColor : (r.weeks === 0 ? INK : INK_MID);
        ctx.font = `${active ? 700 : 600} 44px "IBM Plex Sans", ui-sans-serif, system-ui, sans-serif`;
        ctx.fillText(`€${r.value.toFixed(3)}`, cx, cursorY + 46);
    });
    ctx.textAlign = "left";

    // ---- headline savings line ----
    cursorY += 110;
    ctx.fillStyle = INK;
    ctx.font = '600 32px "IBM Plex Sans", ui-sans-serif, system-ui, sans-serif';
    drawWrappedText(ctx, opts.headline, PAD_X, cursorY, SHARE_W - PAD_X * 2, 42);

    // ---- footer strip: current price + updated ----
    const footerY = SHARE_H - 60;
    ctx.strokeStyle = RULE;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(PAD_X, footerY - 34);
    ctx.lineTo(SHARE_W - PAD_X, footerY - 34);
    ctx.stroke();

    ctx.fillStyle = INK_DIM;
    ctx.font = '500 20px "IBM Plex Mono", ui-monospace, Consolas, monospace';
    ctx.textBaseline = "alphabetic";
    ctx.fillText(opts.footerLeft, PAD_X, footerY);

    ctx.textAlign = "right";
    ctx.fillStyle = AMBER;
    ctx.fillText(opts.footerRight, SHARE_W - PAD_X, footerY);
    ctx.textAlign = "left";

    return canvas;
}

// Turns the raw predictionData row into the strings the card wants. Mirrors
// the wording in renderDecision so the shared image and the on-page card
// always agree — no risk of a screenshot saying "€2.19" while the page has
// silently refreshed to a different number after the user hit Share.
//
// `weeks` (1..3) selects which horizon drives the headline. All three
// forecast points are rendered on the card so the receiver still sees the
// full 1-3 week arc, but the featured verdict + savings track the sender's
// pick.
function shareCardOptsFor(fuel, data, weeks = 3) {
    const p = data[fuel];
    if (!p || p.trend === "unknown") return null;

    const now  = p.current_pump_eur_per_l;
    const p1w  = predictedPumpAtWeeks(p, 1);
    const p2w  = predictedPumpAtWeeks(p, 2);
    const p3w  = predictedPumpAtWeeks(p, 3);
    const featured = predictedPumpAtWeeks(p, weeks);
    const perL = featured - now;
    const perFill = perL * DECISION_REF_LITRES;
    const signal = perL > 0.005 ? "fill" : perL < -0.005 ? "wait" : "neutral";
    const verdict = { fill: "Fill now", wait: "Wait", neutral: "Either way" }[signal];
    const abs = Math.abs(perFill).toFixed(2);
    const centsPerL = Math.abs(perL * 100).toFixed(1);
    const horizonLabel = weeks === 1 ? "1 week" : `${weeks} weeks`;
    const headline = signal === "fill"
        ? `Predicted +${centsPerL}c/L in ~${horizonLabel}. Fill a 60 L tank now, save about €${abs}.`
        : signal === "wait"
            ? `Predicted −${centsPerL}c/L in ~${horizonLabel}. Delay a 60 L fill, save about €${abs}.`
            : "Predicted move is inside the model's noise floor. Fill whenever — timing barely matters.";

    const fuelLabel = fuel === "petrol" ? "Petrol (95)" : "Diesel";
    const conf = Math.round((p.confidence || 0) * 100);
    const updated = (window.__updatedAtLabel || "").replace(/^\s*\(updated at:\s*/, "").replace(/\)\s*$/, "");
    const footerLeft = `Confidence ${conf}%  ·  60 L reference`;
    const footerRight = updated ? `irishfueltrend  ·  ${updated}` : "irishfueltrend";

    return {
        fuel, fuelLabel, weeks, horizonLabel, signal, verdict, headline,
        now, p1w, p2w, p3w,
        footerLeft, footerRight,
    };
}

async function canvasToBlob(canvas) {
    // toBlob is async and callback-based; wrap for await/use.
    return new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
}

// Modal state — lives at module scope so the fuel/horizon chips inside the
// modal can retarget the render without re-opening the dialog. Reset on each
// open(), so a stale pick from a previous session never leaks in.
const _shareState = { fuel: "petrol", weeks: 3, blob: null, objUrl: null };

async function _renderShareCard() {
    if (!predictionData) return;
    const opts = shareCardOptsFor(_shareState.fuel, predictionData, _shareState.weeks);
    if (!opts) return;

    const img   = document.getElementById("share-preview-img");
    const dlBtn = document.getElementById("share-download");
    const shBtn = document.getElementById("share-native");
    const cpBtn = document.getElementById("share-copy");
    if (!img) return;

    await ensureShareFonts();
    const canvas = buildShareCardCanvas(opts);
    const blob = await canvasToBlob(canvas);
    if (!blob) return;

    if (_shareState.objUrl) URL.revokeObjectURL(_shareState.objUrl);
    const objUrl = URL.createObjectURL(blob);
    _shareState.blob = blob;
    _shareState.objUrl = objUrl;
    img.src = objUrl;

    const fname = `irish-fuel-${opts.fuel}-${opts.weeks}w-${opts.signal}.png`;
    const file  = new File([blob], fname, { type: "image/png" });

    const canShareFiles =
        typeof navigator !== "undefined" &&
        typeof navigator.canShare === "function" &&
        navigator.canShare({ files: [file] });
    shBtn.hidden = !canShareFiles;
    shBtn.onclick = async () => {
        try {
            await navigator.share({
                files: [file],
                title: "Irish Fuel Trend",
                text:  `${opts.verdict} — ${opts.headline}`,
            });
        } catch (err) {
            if (err && err.name !== "AbortError") console.error("share failed", err);
        }
    };

    const canCopyImage =
        typeof ClipboardItem !== "undefined" &&
        navigator.clipboard && typeof navigator.clipboard.write === "function";
    cpBtn.hidden = !canCopyImage;
    cpBtn.onclick = async () => {
        try {
            await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
            const orig = cpBtn.textContent;
            cpBtn.textContent = "Copied";
            setTimeout(() => { cpBtn.textContent = orig; }, 1500);
        } catch (err) {
            console.error("copy failed", err);
        }
    };

    dlBtn.onclick = () => {
        const a = document.createElement("a");
        a.href = objUrl;
        a.download = fname;
        document.body.appendChild(a);
        a.click();
        a.remove();
    };
}

function _syncShareChips() {
    document.querySelectorAll("[data-share-fuel-chip]").forEach(el => {
        const active = el.dataset.shareFuelChip === _shareState.fuel;
        el.classList.toggle("is-active", active);
        el.setAttribute("aria-selected", active ? "true" : "false");
    });
    document.querySelectorAll("[data-share-horizon]").forEach(el => {
        const active = parseInt(el.dataset.shareHorizon, 10) === _shareState.weeks;
        el.classList.toggle("is-active", active);
        el.setAttribute("aria-selected", active ? "true" : "false");
    });
}

// Wired once at boot — subsequent openShareModal() calls just re-sync the
// chips, no listener stacking.
function _wireShareChipsOnce() {
    const wrap = document.getElementById("share-controls");
    if (!wrap || wrap.dataset.wired === "1") return;
    wrap.dataset.wired = "1";
    wrap.addEventListener("click", (evt) => {
        const fuelBtn = evt.target.closest("[data-share-fuel-chip]");
        const horBtn  = evt.target.closest("[data-share-horizon]");
        if (fuelBtn) {
            const f = fuelBtn.dataset.shareFuelChip;
            if (!predictionData || !predictionData[f] || predictionData[f].trend === "unknown") return;
            _shareState.fuel = f;
        } else if (horBtn) {
            const w = parseInt(horBtn.dataset.shareHorizon, 10);
            if (!Number.isFinite(w) || w < 1 || w > 3) return;
            _shareState.weeks = w;
        } else {
            return;
        }
        _syncShareChips();
        _renderShareCard().catch(err => console.error(err));
    });
}

async function focusShareCard(fuel) {
    if (!predictionData) return;
    const panel = document.getElementById("share-panel");
    if (!panel) return;
    _shareState.fuel  = fuel || _shareState.fuel || "petrol";
    _wireShareChipsOnce();
    _syncShareChips();
    await _renderShareCard();
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

// First paint of the share panel — runs when predictionData first lands so
// the preview is visible immediately, without the user having to click the
// per-fuel share button. Never scrolls; scroll is reserved for the explicit
// focus path.
async function initSharePanel() {
    if (!predictionData) return;
    if (!document.getElementById("share-panel")) return;
    _wireShareChipsOnce();
    _syncShareChips();
    await _renderShareCard();
}

function wireShareButtons() {
    document.querySelectorAll(".decision-share").forEach(btn => {
        // Show now that predictionData is loaded and a real verdict exists.
        const fuel = btn.dataset.shareFuel;
        const opts = predictionData ? shareCardOptsFor(fuel, predictionData) : null;
        btn.hidden = !opts;
        if (!opts) return;
        // Replace listener defensively — renderDecision may be called again on
        // dev-ingest refresh, and we don't want click handlers stacking up.
        btn.onclick = () => focusShareCard(fuel).catch(err => console.error(err));
    });
}

function renderBacktest(list, points) {
    if (!list) return;
    // `list` is a <ul> containing zero-width LEDs — the state was previously
    // conveyed only by CSS colour keyed off data-hit. Add an aria-label per
    // item so screen readers get the same information, and a role on the
    // list itself so it announces as a list of results rather than markup.
    list.setAttribute("role", "list");
    list.setAttribute("aria-label", "Recent one-week direction calls, oldest to newest");
    list.innerHTML = points.map(pt => {
        const dir  = pt.predicted_return >= 0 ? "up" : "down";
        const act  = pt.actual_return >= 0 ? "up" : "down";
        const err  = (pt.actual_pump_eur_per_l - pt.predicted_pump_eur_per_l);
        const errS = `${err >= 0 ? "+" : ""}${err.toFixed(3)}`;
        const hitTag = pt.direction_correct ? "HIT" : "MISS";
        // Rich per-square popup — surfaces the numbers behind the colour so a
        // reader can judge how big the miss was, not just whether it happened.
        const tip = [
            `${fmtDMY(pt.date)}  ·  ${hitTag}`,
            `Predicted ${dir} (${fmtPct(pt.predicted_return)}) → €${pt.predicted_pump_eur_per_l.toFixed(3)}`,
            `Actual    ${act} (${fmtPct(pt.actual_return)}) → €${pt.actual_pump_eur_per_l.toFixed(3)}`,
            `Error €${errS}`,
        ].join("\n");
        const label = `${fmtDMY(pt.date)}: predicted ${dir}, actually ${act} — ${pt.direction_correct ? "hit" : "miss"}`;
        // tabindex=0 so the same popup opens on keyboard focus; aria-label is
        // the screen-reader equivalent of the visual tooltip.
        return `<li data-hit="${pt.direction_correct}" role="listitem" tabindex="0" aria-label="${escapeHtml(label)}" data-tip="${escapeHtml(tip)}"></li>`;
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

// ------------------ fill log (localStorage-only) ------------------
// Personal fill history. Never leaves the device. Compares the user's
// litre-weighted average against the latest national per-fuel average so a
// "vs national" delta reads as real money saved/overpaid across their 30-day
// volume — not a per-litre curiosity.

const FILL_LOG_KEY = "ift.fills.v1";
const FILL_LOG_WINDOW_DAYS = 30;

function loadFills() {
    try {
        const raw = localStorage.getItem(FILL_LOG_KEY);
        if (!raw) return [];
        const arr = JSON.parse(raw);
        return Array.isArray(arr) ? arr : [];
    } catch {
        // Corrupt JSON in storage (older schema, manual edit) — start fresh
        // rather than break the whole page. The user's data is unrecoverable
        // at this point anyway.
        return [];
    }
}

function saveFills(fills) {
    try {
        localStorage.setItem(FILL_LOG_KEY, JSON.stringify(fills));
    } catch (err) {
        console.warn("fill log save failed", err);
    }
}

function fillLogWithinWindow(fills, days = FILL_LOG_WINDOW_DAYS) {
    const cutoff = new Date();
    cutoff.setUTCDate(cutoff.getUTCDate() - days);
    const cutIso = cutoff.toISOString().slice(0, 10);
    return fills.filter(f => f.date >= cutIso);
}

// Litre-weighted average pump price today across the user's fuel mix. Used as
// the "if you'd paid national average" reference for the savings figure.
function nationalWeightedReference(fills) {
    if (!priceData) return null;
    const latestP = priceData.petrol?.latest?.price_eur_per_litre;
    const latestD = priceData.diesel?.latest?.price_eur_per_litre;
    let litresP = 0, litresD = 0;
    for (const f of fills) {
        if (f.fuel === "petrol") litresP += f.litres;
        else if (f.fuel === "diesel") litresD += f.litres;
    }
    const total = litresP + litresD;
    if (total <= 0) return null;
    // Fallback: if only one fuel has a national price, ignore the other side
    // rather than skew the weighted average with a zero.
    const pRef = typeof latestP === "number" ? latestP : null;
    const dRef = typeof latestD === "number" ? latestD : null;
    if (pRef == null && dRef == null) return null;
    const num = (pRef ?? 0) * litresP + (dRef ?? 0) * litresD;
    const den = (pRef != null ? litresP : 0) + (dRef != null ? litresD : 0);
    return den > 0 ? num / den : null;
}

function computeFillStats(fills) {
    const recent = fillLogWithinWindow(fills);
    const count = recent.length;
    let litres = 0, spent = 0;
    for (const f of recent) {
        litres += f.litres;
        spent  += f.litres * f.price_eur_per_l;
    }
    const avg = litres > 0 ? spent / litres : null;
    const ref = nationalWeightedReference(recent);
    const savedPerL = (avg != null && ref != null) ? (ref - avg) : null;
    const savedTotal = (savedPerL != null) ? savedPerL * litres : null;
    return { count, litres, spent, avg, ref, savedPerL, savedTotal };
}

function renderFillStats(stats) {
    document.getElementById("fl-count").textContent  = String(stats.count);
    document.getElementById("fl-litres").textContent = stats.litres > 0 ? stats.litres.toFixed(1) : "0";
    document.getElementById("fl-spent").textContent  = `€${stats.spent.toFixed(2)}`;
    document.getElementById("fl-avg").textContent    = stats.avg == null ? "—" : `€${stats.avg.toFixed(3)}`;
    const diffEl = document.getElementById("fl-diff");
    if (stats.savedTotal == null) {
        diffEl.textContent = "—";
        diffEl.dataset.sign = "flat";
    } else {
        const abs = Math.abs(stats.savedTotal).toFixed(2);
        const sign = stats.savedTotal > 0.005 ? "down" : stats.savedTotal < -0.005 ? "up" : "flat";
        // "down" = user paid less than reference = good. Mirrors the price
        // colour convention (down = cheaper = green) already used everywhere.
        const verb = stats.savedTotal > 0 ? "saved" : stats.savedTotal < 0 ? "overpaid" : "even";
        diffEl.textContent = sign === "flat" ? "±€0.00" : `${verb} €${abs}`;
        diffEl.dataset.sign = sign;
    }
}

function renderFillList(fills) {
    const list = document.getElementById("fill-log-list");
    if (!list) return;
    // Newest first — the interesting fill is the most recent one.
    const sorted = [...fills].sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
    list.innerHTML = sorted.map(f => {
        const total = (f.litres * f.price_eur_per_l).toFixed(2);
        return `
            <li class="fl-item">
                <span class="fl-item-date">${escapeHtml(fmtDMY(f.date))}</span>
                <span class="fl-item-fuel" data-fuel="${escapeHtml(f.fuel)}">${escapeHtml(f.fuel)}</span>
                <span class="fl-item-litres">${f.litres.toFixed(1)} L</span>
                <span class="fl-item-price">€${f.price_eur_per_l.toFixed(3)}/L</span>
                <span class="fl-item-total">€${total}</span>
                <button type="button" class="fl-item-del" data-id="${escapeHtml(f.id)}" aria-label="Delete this fill">Delete</button>
            </li>`;
    }).join("");
}

function renderFillLog() {
    const fills = loadFills();
    renderFillStats(computeFillStats(fills));
    renderFillList(fills);
}

function newFillId() {
    if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
    return `f-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function wireFillLog() {
    const form = document.getElementById("fill-log-form");
    const list = document.getElementById("fill-log-list");
    if (!form || !list) return;
    // Prefill date input with today so a one-hand mobile add takes two taps.
    const dateEl = document.getElementById("fl-date");
    if (dateEl && !dateEl.value) dateEl.value = new Date().toISOString().slice(0, 10);

    form.addEventListener("submit", (e) => {
        e.preventDefault();
        const date   = document.getElementById("fl-date").value;
        const fuel   = document.getElementById("fl-fuel").value;
        const litres = parseFloat(document.getElementById("fl-litres-in").value);
        const price  = parseFloat(document.getElementById("fl-price-in").value);
        if (!date || !fuel || !(litres > 0) || !(price > 0)) return;
        const fills = loadFills();
        fills.push({
            id: newFillId(),
            date,
            fuel,
            litres,
            price_eur_per_l: price,
        });
        saveFills(fills);
        renderFillLog();
        // Reset the volatile inputs only — leave the date + fuel so a user
        // logging a run of past fills doesn't have to reset every field.
        document.getElementById("fl-litres-in").value = "";
        document.getElementById("fl-price-in").value = "";
        document.getElementById("fl-litres-in").focus();
    });

    list.addEventListener("click", (e) => {
        const btn = e.target.closest(".fl-item-del");
        if (!btn) return;
        const id = btn.dataset.id;
        if (!id) return;
        const fills = loadFills().filter(f => f.id !== id);
        saveFills(fills);
        renderFillLog();
    });
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

// ------------------ ireland heatmap ------------------
//
// Read the counties.json snapshot, compute the chosen metric per county for
// the selected fuel, and tint the SVG paths. Diverging modes (percent
// predictions, level vs national) use green→mid→red; sequential modes
// (confidence, station density) ramp mid→accent. Grey = stale / no data.

const MAP_COLOR_DOWN   = [0x7b, 0xd8, 0x8f];   // matches --down
const MAP_COLOR_MID    = [0x30, 0x35, 0x3c];   // near-black neutral
const MAP_COLOR_UP     = [0xff, 0x5c, 0x4a];   // matches --up
const MAP_COLOR_ACCENT = [0xff, 0xb6, 0x48];   // matches --accent
const MAP_COLOR_STALE  = "#22252b";

// Metric registry — id, chip label, legend labels/stops, colour scheme, and
// value extractor. Adding a mode is one entry here plus a chip in the HTML.
const MAP_METRICS = [
    {
        id: "pct3w",
        subtitle: "(3-week predicted change)",
        type: "div",
        clamp: 0.015,
        low: "Cheaper", high: "Pricier",
        stops: ["−1.5%", "0", "+1.5%"],
        get: f => _pctChange(f.current_pump_eur_per_l, f.predicted_pump_3w_eur_per_l),
        fmt: v => (v > 0 ? "+" : "") + (v * 100).toFixed(2) + "%",
        aria: "predicted 3-week change",
    },
    {
        id: "pct1w",
        subtitle: "(1-week predicted change)",
        type: "div",
        clamp: 0.005,
        low: "Cheaper", high: "Pricier",
        stops: ["−0.5%", "0", "+0.5%"],
        get: f => _pctChange(f.current_pump_eur_per_l, f.predicted_pump_eur_per_l),
        fmt: v => (v > 0 ? "+" : "") + (v * 100).toFixed(2) + "%",
        aria: "predicted 1-week change",
    },
    {
        id: "level",
        subtitle: "(current level vs national)",
        type: "div",
        clamp: 0.05,
        low: "Cheaper", high: "Pricier",
        stops: ["−5¢", "0", "+5¢"],
        get: f => (typeof f.basis_eur_per_litre === "number") ? f.basis_eur_per_litre : null,
        fmt: v => (v >= 0 ? "+" : "−") + "€" + Math.abs(v).toFixed(3),
        aria: "basis vs national",
    },
    {
        id: "conf",
        subtitle: "(model confidence)",
        type: "seq",
        lo: 0, hi: 1,
        low: "Low", high: "High",
        stops: ["0", "0.5", "1"],
        get: f => (typeof f.confidence === "number") ? f.confidence : null,
        fmt: v => v.toFixed(2),
        aria: "confidence",
    },
    {
        id: "stations",
        subtitle: "(reporting stations per county)",
        type: "seq_dyn",
        low: "Few", high: "Many",
        stops: null,   // labelled from data
        get: f => (typeof f.station_count === "number") ? f.station_count : null,
        fmt: v => String(Math.round(v)),
        aria: "station count",
    },
];

let mapCountiesData = null;
let mapCurrentFuel = "petrol";
let mapCurrentMetric = "pct3w";
let mapSvgReady = false;

function _mixRgb(a, b, t) {
    return [
        Math.round(a[0] + (b[0] - a[0]) * t),
        Math.round(a[1] + (b[1] - a[1]) * t),
        Math.round(a[2] + (b[2] - a[2]) * t),
    ];
}

function _pctChange(now, then) {
    if (now == null || then == null || now <= 0) return null;
    return (then - now) / now;
}

function _rgb(arr) { return `rgb(${arr[0]},${arr[1]},${arr[2]})`; }

function _tintDiv(v, clamp) {
    if (v == null || Number.isNaN(v)) return MAP_COLOR_STALE;
    const c = Math.max(-clamp, Math.min(clamp, v));
    const t = (c + clamp) / (2 * clamp);
    const rgb = t <= 0.5
        ? _mixRgb(MAP_COLOR_DOWN, MAP_COLOR_MID, t * 2)
        : _mixRgb(MAP_COLOR_MID, MAP_COLOR_UP, (t - 0.5) * 2);
    return _rgb(rgb);
}

function _tintSeq(v, lo, hi) {
    if (v == null || Number.isNaN(v) || hi <= lo) return MAP_COLOR_STALE;
    const t = Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
    return _rgb(_mixRgb(MAP_COLOR_MID, MAP_COLOR_ACCENT, t));
}

function _metricConfig(id) {
    return MAP_METRICS.find(m => m.id === id) || MAP_METRICS[0];
}

function _countyEntryFor(name) {
    if (!mapCountiesData || !mapCountiesData.counties) return null;
    return mapCountiesData.counties.find(c => c.county === name) || null;
}

// Value used both for tinting and for the tooltip lead row. Staleness here
// means "the crowd median came from a wider window than the standard 30d" —
// the level is older, the basis is noisier. But the percent-change forecasts
// are still national_pred vs national_now (the basis largely cancels), so a
// stale county produces a valid tint on pct modes. Only rows with no forecast
// fields at all (station-fallback carry-forward) return null on pct/conf.
function _countyMetric(entry, fuel, metricId) {
    if (!entry) return null;
    const f = entry[fuel];
    if (!f) return null;
    const metric = _metricConfig(metricId);
    const v = metric.get(f);
    return (v == null || Number.isNaN(v)) ? null : v;
}

function _updateLegend(metric, dynLo, dynHi) {
    const legend = document.getElementById("ireland-map-legend");
    if (!legend) return;
    legend.classList.toggle("is-seq", metric.type === "seq" || metric.type === "seq_dyn");
    const loLbl = legend.querySelector('[data-role="lo"]');
    const hiLbl = legend.querySelector('[data-role="hi"]');
    if (loLbl) loLbl.textContent = metric.low;
    if (hiLbl) hiLbl.textContent = metric.high;
    const stops = document.getElementById("ireland-legend-stops");
    if (stops) {
        let labels = metric.stops;
        if (!labels && metric.type === "seq_dyn") {
            const lo = Number.isFinite(dynLo) ? dynLo : 0;
            const hi = Number.isFinite(dynHi) ? dynHi : 0;
            labels = [String(Math.round(lo)), String(Math.round((lo + hi) / 2)), String(Math.round(hi))];
        }
        stops.innerHTML = (labels || []).map(t => `<span>${t}</span>`).join("");
    }
    const subtitle = document.getElementById("ireland-map-subtitle");
    if (subtitle) subtitle.textContent = metric.subtitle;
}

function paintIrelandMap() {
    if (!mapSvgReady || !mapCountiesData) return;
    const frame = document.getElementById("ireland-map-frame");
    if (!frame) return;
    const metric = _metricConfig(mapCurrentMetric);
    const nodes = frame.querySelectorAll("g.county[data-county]");

    // First pass — gather values so seq_dyn can size its domain.
    let dynLo = Infinity, dynHi = -Infinity;
    const values = new Map();
    nodes.forEach(node => {
        const name = node.dataset.county;
        const v = _countyMetric(_countyEntryFor(name), mapCurrentFuel, mapCurrentMetric);
        values.set(name, v);
        if (v != null && Number.isFinite(v)) {
            if (v < dynLo) dynLo = v;
            if (v > dynHi) dynHi = v;
        }
    });

    nodes.forEach(node => {
        const name = node.dataset.county;
        const entry = _countyEntryFor(name);
        const f = entry ? entry[mapCurrentFuel] : null;
        const v = values.get(name);
        let fill;
        if (metric.type === "div") {
            fill = _tintDiv(v, metric.clamp);
        } else if (metric.type === "seq") {
            fill = _tintSeq(v, metric.lo, metric.hi);
        } else {
            // seq_dyn — pad the domain slightly so the darkest county still
            // reads as distinct from the "no data" grey when the min is 0.
            const lo = Number.isFinite(dynLo) ? dynLo : 0;
            const hi = Number.isFinite(dynHi) ? Math.max(dynHi, lo + 1) : 1;
            fill = _tintSeq(v, lo, hi);
        }
        node.style.fill = fill;
        // Two-state dim: `is-stale` (opacity 0.65) covers both a stale-median
        // county that still paints a tint and a truly no-data county that
        // paints grey. Fresh counties render at full brightness so the tint
        // reads first.
        node.classList.toggle("is-stale", v == null || (f && !!f.stale));
        node.dataset.pct = v == null ? "" : v.toFixed(4);
        const short = v == null
            ? `${name}, no data`
            : `${name}, ${metric.fmt(v)} ${metric.aria}`;
        node.setAttribute("aria-label", short);
    });

    _updateLegend(metric, dynLo, dynHi);
}

function _positionTooltip(tip, evt, frame) {
    const rect = frame.getBoundingClientRect();
    // Position relative to the frame (which the tooltip is nested under).
    const x = evt.clientX - rect.left;
    const y = evt.clientY - rect.top;
    tip.style.left = `${x}px`;
    tip.style.top  = `${y}px`;
}

function _renderTooltip(tip, name) {
    const entry = _countyEntryFor(name);
    const fuel = mapCurrentFuel;
    const f = entry ? entry[fuel] : null;
    if (!f) {
        tip.innerHTML = `<strong>${escapeHtml(name)}</strong><span class="muted">No data</span>`;
        return;
    }
    const metric = _metricConfig(mapCurrentMetric);
    const now = f.current_pump_eur_per_l;
    const then = f.predicted_pump_3w_eur_per_l;
    const v = _countyMetric(entry, fuel, mapCurrentMetric);
    const staleTag = f.stale ? `<span class="ireland-tip-stale">stale</span>` : "";
    const valTxt = v == null ? "—" : metric.fmt(v);
    // Lead colour: diverging metrics use the up/down/flat convention. Sequential
    // metrics (confidence, stations) are neither — leave them accent-toned via
    // the default row class.
    let sign = "";
    if (v != null && metric.type === "div") {
        sign = v > 0 ? "up" : v < 0 ? "down" : "flat";
    }
    const asOfLine = f.origin_snapshot_date
        ? `<span class="ireland-tip-row"><span class="muted">as of</span><span>${escapeHtml(f.origin_snapshot_date)}</span></span>`
        : "";
    // Level lines skipped when both values are absent (station-fallback
    // carry-forward has neither current_pump nor predicted).
    const priceLines = (now != null || then != null) ? `
        <span class="ireland-tip-row">
            <span class="muted">now (${fuel})</span>
            <span>${now == null ? "—" : fmtEur(now)}</span>
        </span>
        <span class="ireland-tip-row">
            <span class="muted">predicted 3w</span>
            <span>${then == null ? "—" : fmtEur(then)}</span>
        </span>` : `
        <span class="ireland-tip-row">
            <span class="muted">last median</span>
            <span>${fmtEur(f.crowd_median_eur_per_litre)}</span>
        </span>`;
    tip.innerHTML = `
        <strong>${escapeHtml(name)} ${staleTag}</strong>
        ${priceLines}
        ${asOfLine}
        <span class="ireland-tip-row is-lead" data-sign="${sign}">
            <span class="muted">${escapeHtml(metric.aria)}</span>
            <span>${valTxt}</span>
        </span>`;
}

function _wireMapInteractions(frame) {
    const tip = document.getElementById("ireland-map-tip");
    if (!tip) return;

    const show = (evt, node) => {
        const name = node.dataset.county;
        _renderTooltip(tip, name);
        tip.hidden = false;
        _positionTooltip(tip, evt, frame);
    };
    const hide = () => { tip.hidden = true; };

    // Track which county is currently painting the tooltip. Any change (move
    // to a different county, or off the map) triggers explicit hide/redraw
    // rather than relying on mouseleave/mouseout timing quirks.
    let currentCounty = null;

    const showForNode = (evt, node) => {
        const name = node.dataset.county;
        if (name !== currentCounty) {
            currentCounty = name;
        }
        show(evt, node);
    };
    const clear = () => {
        if (currentCounty !== null || !tip.hidden) {
            currentCounty = null;
            hide();
        }
    };

    frame.addEventListener("mousemove", (evt) => {
        const node = evt.target.closest("g.county[data-county]");
        if (node) showForNode(evt, node);
        else clear();
    });

    // Multiple hide triggers because a single one is fragile:
    //   * `mouseleave`/`mouseout` on the frame — normal exit path.
    //   * `pointerleave` — fires when a stylus / touch drags off the map,
    //     which mouse events do not cover.
    //   * document-level mousemove — safety net when the cursor moves onto a
    //     non-frame page area and the frame handlers never fire (Chromium bug
    //     when exiting via an SVG <path> child that swallows pointer events).
    //   * window blur + document mouseleave — cursor leaves the browser
    //     window entirely (task bar, alt-tab). No mousemove fires until it
    //     returns, so without these the tooltip is stuck until re-entry.
    frame.addEventListener("mouseleave", clear);
    frame.addEventListener("pointerleave", clear);
    frame.addEventListener("mouseout", (evt) => {
        const to = evt.relatedTarget;
        if (!to || !frame.contains(to)) clear();
    });
    document.addEventListener("mousemove", (evt) => {
        if (tip.hidden) return;
        if (!frame.contains(evt.target)) clear();
    });
    document.addEventListener("mouseleave", clear);
    window.addEventListener("blur", clear);
    document.addEventListener("scroll", clear, { passive: true });

    // Keyboard focus (each <g> is tabindex=0 role=button)
    frame.addEventListener("focusin", (evt) => {
        const node = evt.target.closest("g.county[data-county]");
        if (!node) return;
        const rect = node.getBoundingClientRect();
        _renderTooltip(tip, node.dataset.county);
        tip.hidden = false;
        const frect = frame.getBoundingClientRect();
        tip.style.left = `${rect.left - frect.left + rect.width / 2}px`;
        tip.style.top  = `${rect.top  - frect.top  + rect.height / 2}px`;
    });
    frame.addEventListener("focusout", hide);

    // Click / Enter → open county page
    const go = (node) => {
        const name = node.dataset.county;
        if (!name) return;
        location.href = `county.html?county=${encodeURIComponent(name)}`;
    };
    frame.addEventListener("click", (evt) => {
        const node = evt.target.closest("g.county[data-county]");
        if (node) go(node);
    });
    frame.addEventListener("keydown", (evt) => {
        if (evt.key !== "Enter" && evt.key !== " ") return;
        const node = evt.target.closest("g.county[data-county]");
        if (!node) return;
        evt.preventDefault();
        go(node);
    });
}

function _wireMapFuelToggle() {
    const wrap = document.querySelector(".ireland-map-fuel");
    if (!wrap) return;
    wrap.addEventListener("click", (evt) => {
        const btn = evt.target.closest(".ireland-fuel-chip");
        if (!btn) return;
        const fuel = btn.dataset.fuel;
        if (!fuel || fuel === mapCurrentFuel) return;
        mapCurrentFuel = fuel;
        wrap.querySelectorAll(".ireland-fuel-chip").forEach(el => {
            const active = el.dataset.fuel === fuel;
            el.classList.toggle("is-active", active);
            el.setAttribute("aria-selected", active ? "true" : "false");
        });
        paintIrelandMap();
    });
}

function _wireMapMetricToggle() {
    const wrap = document.querySelector(".ireland-map-metric");
    if (!wrap) return;
    wrap.addEventListener("click", (evt) => {
        const btn = evt.target.closest(".ireland-metric-chip");
        if (!btn) return;
        const metric = btn.dataset.metric;
        if (!metric || metric === mapCurrentMetric) return;
        if (!MAP_METRICS.some(m => m.id === metric)) return;
        mapCurrentMetric = metric;
        wrap.querySelectorAll(".ireland-metric-chip").forEach(el => {
            const active = el.dataset.metric === metric;
            el.classList.toggle("is-active", active);
            el.setAttribute("aria-selected", active ? "true" : "false");
        });
        paintIrelandMap();
    });
}

async function initIrelandMap() {
    const frame = document.getElementById("ireland-map-frame");
    if (!frame) return;
    try {
        const [svgText, counties] = await Promise.all([
            fetch("ireland-counties.svg", { cache: "force-cache" }).then(r => r.text()),
            jget("data/counties.json"),
        ]);
        // innerHTML with a static asset we control — no untrusted content in
        // this SVG. Any user-facing text (tooltip) is rendered separately.
        frame.innerHTML = svgText;
        mapSvgReady = true;
        mapCountiesData = counties;
        _wireMapFuelToggle();
        _wireMapMetricToggle();
        _wireMapInteractions(frame);
        paintIrelandMap();
    } catch (err) {
        console.warn("Ireland map load failed:", err);
        frame.innerHTML = `<p class="ireland-map-loading muted">Map unavailable.</p>`;
    }
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
    return Promise.all([loadPrices(26), loadPrediction(), loadNews(), initIrelandMap()]);
}).then(() => {
    updateCalculator();
    attachDragCompare("price-chart", "dc-popup");
    wireFillLog();
    renderFillLog();
    mountDevIngestButton({
        onDone: async () => {
            priceData = null;
            predictionData = null;
            mapCountiesData = null;
            await Promise.all([
                loadPrices(26),
                loadPrediction(),
                loadNews(),
                jget("data/counties.json").then(c => { mapCountiesData = c; paintIrelandMap(); }),
            ]);
            updateCalculator();
            renderFillLog();
        },
    });
}).catch(err => console.error("Dashboard load failed:", err));
