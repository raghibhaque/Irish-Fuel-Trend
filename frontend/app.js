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
    cursorY += 150;
    ctx.fillStyle = signalColor;
    ctx.font = '700 140px "IBM Plex Sans Condensed", "IBM Plex Sans", ui-sans-serif, sans-serif';
    ctx.fillText(opts.verdict.toUpperCase(), PAD_X, cursorY);

    // ---- headline savings line ----
    cursorY += 70;
    ctx.fillStyle = INK;
    ctx.font = '600 40px "IBM Plex Sans", ui-sans-serif, system-ui, sans-serif';
    const headline = opts.headline;
    drawWrappedText(ctx, headline, PAD_X, cursorY, SHARE_W - PAD_X * 2, 52);

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
function shareCardOptsFor(fuel, data) {
    const p = data[fuel];
    if (!p || p.trend === "unknown") return null;

    const now  = p.current_pump_eur_per_l;
    const then = p.predicted_pump_3w_eur_per_l;
    const perL = then - now;
    const perFill = perL * DECISION_REF_LITRES;
    const signal = perL > 0.005 ? "fill" : perL < -0.005 ? "wait" : "neutral";
    const verdict = { fill: "Fill now", wait: "Wait", neutral: "Either way" }[signal];
    const abs = Math.abs(perFill).toFixed(2);
    const centsPerL = Math.abs(perL * 100).toFixed(1);
    const headline = signal === "fill"
        ? `Predicted +${centsPerL}c/L in ~3 weeks. Fill a 60 L tank now, save about €${abs}.`
        : signal === "wait"
            ? `Predicted −${centsPerL}c/L in ~3 weeks. Delay a 60 L fill, save about €${abs}.`
            : "Predicted move is inside the model's noise floor. Fill whenever — timing barely matters.";

    const fuelLabel = fuel === "petrol" ? "Petrol (95)" : "Diesel";
    const conf = Math.round((p.confidence || 0) * 100);
    const updated = (window.__updatedAtLabel || "").replace(/^\s*\(updated at:\s*/, "").replace(/\)\s*$/, "");
    const footerLeft = `Now €${now.toFixed(3)}/L  ·  Confidence ${conf}%`;
    const footerRight = updated ? `irishfueltrend  ·  ${updated}` : "irishfueltrend";

    return { fuel, fuelLabel, signal, verdict, headline, footerLeft, footerRight };
}

async function canvasToBlob(canvas) {
    // toBlob is async and callback-based; wrap for await/use.
    return new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
}

async function openShareModal(fuel) {
    if (!predictionData) return;
    const opts = shareCardOptsFor(fuel, predictionData);
    if (!opts) return;

    const modal = document.getElementById("share-modal");
    const img   = document.getElementById("share-preview-img");
    const dlBtn = document.getElementById("share-download");
    const shBtn = document.getElementById("share-native");
    const cpBtn = document.getElementById("share-copy");
    if (!modal || !img) return;

    await ensureShareFonts();
    const canvas = buildShareCardCanvas(opts);
    const blob = await canvasToBlob(canvas);
    if (!blob) return;

    // Revoke any previous object URL to avoid a slow-growing leak across many
    // open/close cycles.
    if (img.dataset.objurl) URL.revokeObjectURL(img.dataset.objurl);
    const objUrl = URL.createObjectURL(blob);
    img.src = objUrl;
    img.dataset.objurl = objUrl;

    const fname = `irish-fuel-${fuel}-${opts.signal}.png`;
    const file  = new File([blob], fname, { type: "image/png" });

    // ---- Web Share API (mobile: opens native share sheet incl. WhatsApp) ----
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
            // User cancelling the sheet throws AbortError — that's not a real
            // failure, so stay silent. Anything else is worth logging.
            if (err && err.name !== "AbortError") console.error("share failed", err);
        }
    };

    // ---- Clipboard image copy (desktop Chromium / Safari 16+) ----
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

    // ---- Download fallback (always available) ----
    dlBtn.onclick = () => {
        const a = document.createElement("a");
        a.href = objUrl;
        a.download = fname;
        document.body.appendChild(a);
        a.click();
        a.remove();
    };

    // <dialog> supports native modal semantics + Esc-to-close for free.
    if (typeof modal.showModal === "function") modal.showModal();
    else modal.setAttribute("open", "");
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
        btn.onclick = () => openShareModal(fuel).catch(err => console.error(err));
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
