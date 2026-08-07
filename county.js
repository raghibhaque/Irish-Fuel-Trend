// County Fuel Predictor controller.
//
// The county payload carries a *basis* (an offset in EUR/L), not a price
// series — see backend/app/routes/counties.py for why. This file adds that
// offset to the national series it already fetches from prices.json, which is
// how the "reconstructed" county history is drawn. Real county snapshots ride
// along in `observations` and are overlaid as points.

const FUELS = ["petrol", "diesel"];
const DEFAULT_COUNTY = "Dublin";
const STORAGE_KEY = "ift.county";
const SPARK_POINTS = 12;

let chart = null;
let nationalPrices = null;   // prices.json
let countyData = null;       // counties.json
let selectedCounty = null;

// ------------------ county selection ------------------
function availableCounties() {
    return (countyData.counties || []).map(c => c.county);
}

function entryFor(name) {
    return (countyData.counties || []).find(c => c.county === name) || null;
}

function initialCounty() {
    const available = availableCounties();
    if (!available.length) return null;
    const fromUrl = new URLSearchParams(location.search).get("county");
    const candidates = [fromUrl, localStorage.getItem(STORAGE_KEY), DEFAULT_COUNTY];
    for (const c of candidates) {
        if (!c) continue;
        const match = available.find(a => a.toLowerCase() === c.toLowerCase());
        if (match) return match;
    }
    return available[0];
}

function buildDropdown() {
    const select = document.getElementById("county-select");
    const withData = availableCounties()
        .map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`);
    const without = (countyData.counties_without_data || [])
        .map(c => `<option value="${escapeHtml(c)}" disabled>${escapeHtml(c)} — no reports</option>`);
    select.innerHTML = withData.join("") + without.join("");
}

function selectCounty(name) {
    selectedCounty = name;
    localStorage.setItem(STORAGE_KEY, name);
    const url = new URL(location.href);
    url.searchParams.set("county", name);
    history.replaceState(null, "", url);
    document.getElementById("county-select").value = name;
    render();
}

// ------------------ reconstruction ------------------
// National weekly series shifted by the county's measured offset.
function reconstruct(fuel, basis, weeks) {
    const iso = cutoffIso(weeks);
    return nationalPrices[fuel].points
        .filter(p => p.date >= iso)
        .map(p => ({ date: p.date, value: p.price_eur_per_litre + basis }));
}

function observedInRange(snapshot, weeks) {
    const iso = cutoffIso(weeks);
    return (snapshot?.observations || [])
        .filter(o => o.date >= iso)
        .map(o => ({ date: o.date, value: o.median_eur_per_litre }));
}

// ------------------ rendering ------------------
function render() {
    const entry = entryFor(selectedCounty);
    if (!entry) return;

    document.getElementById("pred-county-name").textContent = selectedCounty;
    document.getElementById("stations-county-name").textContent = selectedCounty;

    renderPickerMeta(entry);
    renderWarnings(entry);
    renderPriceCards(entry);
    renderPredictions(entry);
    renderStations(entry);
    renderBrandStations(entry);
    renderChart(entry, parseInt(document.getElementById("chart-range").value, 10));
    updateCalculator();
}

// Operator-published forecourt catalogue (Applegreen, Maxol, ...). No prices,
// just a durable name/brand/location list. Sorted by brand then name so a
// county always renders in the same order across reloads.
function renderBrandStations(entry) {
    const list = document.getElementById("brand-list");
    const countEl = document.getElementById("brand-count");
    document.getElementById("brand-county-name").textContent = selectedCounty;
    const items = entry?.brand_stations || [];
    countEl.textContent = items.length;
    if (!items.length) {
        list.innerHTML = `<li class="empty">No operator-published forecourts recorded for ${escapeHtml(selectedCounty)}.</li>`;
        return;
    }
    const brandLabel = (b) => ({
        APPLEGREEN: "Applegreen",
        MAXOL: "Maxol",
        CIRCLE_K: "Circle K",
    }[b] || b);
    list.innerHTML = items.map(s => {
        const where = [s.town, s.address].filter(Boolean).join(" · ");
        const mapUrl = (s.latitude != null && s.longitude != null)
            ? `https://www.google.com/maps?q=${s.latitude},${s.longitude}`
            : s.url;
        const name = mapUrl
            ? `<a href="${escapeHtml(mapUrl)}" target="_blank" rel="noopener">${escapeHtml(s.name)}</a>`
            : escapeHtml(s.name);
        return `<li>
            <span class="brand-chip" data-brand="${escapeHtml(s.source_brand)}">${escapeHtml(brandLabel(s.source_brand))}</span>
            <span class="brand-name">${name}</span>
            <span class="brand-where">${escapeHtml(where)}</span>
        </li>`;
    }).join("");
}

function renderPickerMeta(entry) {
    const parts = FUELS
        .filter(f => entry[f])
        .map(f => `${f} €${entry[f].crowd_median_eur_per_litre.toFixed(3)} ` +
                  `(${entry[f].station_count} station${entry[f].station_count === 1 ? "" : "s"})`);
    const asOf = countyData.snapshot_date ? ` · snapshot ${fmtDate(countyData.snapshot_date)}` : "";
    document.getElementById("picker-meta").textContent =
        parts.length ? `Crowd median: ${parts.join(" · ")}${asOf}` : "No median for this county.";
}

function renderWarnings(entry) {
    const el = document.getElementById("sample-warn");
    const msgs = [];
    const thin = FUELS.filter(f => entry[f]?.low_sample);
    if (thin.length) {
        const counts = thin.map(f => `${entry[f].station_count} for ${f}`).join(", ");
        msgs.push(`Low sample — only ${counts}. The county offset is shrunk toward the ` +
                  `national average and the forecast band widened to match.`);
    }
    const stale = FUELS.filter(f => entry[f]?.stale);
    if (stale.length) {
        const windows = stale.map(f => `${f} uses a ${entry[f].window_days}-day window`).join(", ");
        msgs.push(`Not enough recent reports for a 30-day median: ${windows}.`);
    }
    el.textContent = msgs.join(" ");
    el.hidden = msgs.length === 0;
}

function renderPriceCards(entry) {
    FUELS.forEach(fuel => {
        const snap = entry[fuel];
        const valueEl = document.getElementById(`current-${fuel}`);
        const asofEl = document.getElementById(`asof-${fuel}`);
        if (!snap) {
            valueEl.textContent = "—";
            asofEl.textContent = "no reports for this fuel";
            document.getElementById(`spark-${fuel}`).innerHTML = "";
            return;
        }
        const price = snap.current_pump_eur_per_l ?? snap.crowd_median_eur_per_litre;
        valueEl.textContent = fmtEur(price);
        const upd = window.__updatedAtLabel || "";
        asofEl.textContent =
            `${fmtCents(snap.basis_eur_per_litre)} vs national · ${snap.station_count} stations${upd}`;

        // Sparkline uses the full national series so it stays stable across
        // range changes, matching the national dashboard's behaviour.
        const recent = nationalPrices[fuel].points.slice(-SPARK_POINTS)
            .map(p => p.price_eur_per_litre + snap.basis_eur_per_litre);
        renderSparklineValues(`spark-${fuel}`, recent);
    });
}

function renderPredictions(entry) {
    FUELS.forEach(fuel => {
        const snap = entry[fuel];
        const card = document.getElementById(`pred-${fuel}`);
        const pill = card.querySelector(".trend-pill");

        if (!snap) {
            pill.textContent = "n/a";
            pill.setAttribute("data-trend", "flat");
            card.querySelector(".pred-explain").textContent =
                `No crowd-sourced ${fuel} reports for ${selectedCounty}.`;
            card.querySelectorAll(".pred-meta dd").forEach(dd => (dd.textContent = "—"));
            return;
        }

        pill.textContent = snap.trend || "n/a";
        pill.setAttribute("data-trend", snap.trend || "flat");

        card.querySelector(".pred-basis").textContent =
            `${fmtCents(snap.basis_eur_per_litre)} (raw ${fmtCents(snap.basis_raw_eur_per_litre)})`;
        card.querySelector(".pred-median").textContent =
            `${fmtEur(snap.crowd_median_eur_per_litre)} · ${snap.window_days}d · n=${snap.station_count}`;

        if (snap.predicted_pump_eur_per_l == null) {
            card.querySelector(".pred-explain").textContent =
                "National model unavailable, so no forecast. The measured county offset is still shown.";
            [".pred-price", ".pred-band", ".pred-delta", ".pred-conf", ".pred-price-3w", ".pred-r2"]
                .forEach(sel => (card.querySelector(sel).textContent = "—"));
            return;
        }

        const arrow = snap.predicted_pump_eur_per_l >= snap.current_pump_eur_per_l ? "▲" : "▼";
        card.querySelector(".pred-price").textContent =
            `€${snap.predicted_pump_eur_per_l.toFixed(3)} ${arrow} (from €${snap.current_pump_eur_per_l.toFixed(3)})`;
        card.querySelector(".pred-band").textContent =
            `€${snap.predicted_pump_low_eur_per_l.toFixed(3)} – €${snap.predicted_pump_high_eur_per_l.toFixed(3)}`;
        card.querySelector(".pred-delta").textContent = fmtPct(snap.predicted_weekly_return);
        card.querySelector(".pred-conf").textContent = `${Math.round(snap.confidence * 100)}%`;
        card.querySelector(".pred-price-3w").textContent = fmtEur(snap.predicted_pump_3w_eur_per_l);
        card.querySelector(".pred-r2").textContent = snap.r2.toFixed(2);
        card.querySelector(".pred-explain").textContent =
            `${selectedCounty} runs ${fmtCents(snap.basis_eur_per_litre)} against the national ` +
            `crowd-sourced median across ${snap.station_count} station` +
            `${snap.station_count === 1 ? "" : "s"}. Direction is the national model's — a fixed ` +
            `county offset shifts the level, not the trend.`;
    });

    document.getElementById("prediction-notes").textContent = (countyData.notes || []).join("  ");
}

function renderStations(entry) {
    FUELS.forEach(fuel => {
        const list = document.getElementById(`stations-${fuel}`);
        const stations = entry[fuel]?.stations || [];
        if (!stations.length) {
            list.innerHTML = `<li class="empty">No recent ${fuel} reports in ${escapeHtml(selectedCounty)}.</li>`;
            return;
        }
        list.innerHTML = stations.map(s => {
            const brand = s.brand && s.brand !== s.name ? ` · ${escapeHtml(s.brand)}` : "";
            const when = s.reported_at ? fmtDate(s.reported_at.slice(0, 10)) : "";
            return `<li>
                <span class="station-name">${escapeHtml(s.name)}${brand}</span>
                <span class="station-price">€${s.price_eur_per_litre.toFixed(3)}</span>
                <span class="station-when">${escapeHtml(when)}</span>
            </li>`;
        }).join("");
    });
}

function renderChart(entry, weeks) {
    // National dates are weekly, our own county snapshots are daily, so the
    // two never line up. Build a sorted union of every date in range and map
    // each dataset onto it, leaving gaps as null.
    const series = {};
    const observed = {};
    const dates = new Set();
    FUELS.forEach(fuel => {
        const snap = entry[fuel];
        const basis = snap ? snap.basis_eur_per_litre : 0;
        series[fuel] = snap ? reconstruct(fuel, basis, weeks) : [];
        observed[fuel] = observedInRange(snap, weeks);
        series[fuel].forEach(p => dates.add(p.date));
        observed[fuel].forEach(p => dates.add(p.date));
    });

    const labels = [...dates].sort();
    const align = (points) => {
        const byDate = new Map(points.map(p => [p.date, p.value]));
        return labels.map(d => (byDate.has(d) ? byDate.get(d) : null));
    };

    const datasets = [];
    FUELS.forEach(fuel => {
        if (!entry[fuel]) return;
        const label = fuel === "petrol" ? "Petrol (95)" : "Diesel";
        datasets.push({
            label: `${label} — reconstructed`,
            data: align(series[fuel]),
            borderColor: FUEL_COLORS[fuel].line,
            backgroundColor: FUEL_COLORS[fuel].fill,
            borderDash: [6, 4],
            tension: 0.25,
            pointRadius: 0,
            borderWidth: 2,
            spanGaps: true,
            fill: true,
        });
        if (observed[fuel].length) {
            datasets.push({
                label: `${label} — observed`,
                data: align(observed[fuel]),
                borderColor: FUEL_COLORS[fuel].line,
                backgroundColor: FUEL_COLORS[fuel].line,
                showLine: false,
                pointRadius: 3,
                pointHoverRadius: 5,
                spanGaps: false,
            });
        }
    });

    const ctx = document.getElementById("price-chart").getContext("2d");
    if (chart) chart.destroy();
    chart = new Chart(ctx, {
        type: "line",
        data: { labels: labels.map(fmtDMY), datasets },
        options: baseChartOptions(),
    });

    const observedCount = FUELS.reduce((n, f) => n + observed[f].length, 0);
    document.getElementById("chart-subtitle").textContent =
        observedCount
            ? `(reconstructed + ${observedCount} observed snapshot${observedCount === 1 ? "" : "s"})`
            : "(reconstructed — no county snapshots banked yet)";
}

// ------------------ fill-up calculator ------------------
function updateCalculator() {
    const entry = entryFor(selectedCounty);
    if (!entry) return;
    const fuel = document.getElementById("calc-fuel").value;
    const snap = entry[fuel];
    const nowEl = document.getElementById("calc-now");
    const thenEl = document.getElementById("calc-then");
    const diffEl = document.getElementById("calc-diff");

    if (!snap || snap.current_pump_eur_per_l == null || snap.predicted_pump_3w_eur_per_l == null) {
        nowEl.textContent = "Today: —";
        thenEl.textContent = "In ~3 weeks: —";
        diffEl.textContent = "Difference: —";
        diffEl.removeAttribute("data-sign");
        return;
    }

    const litres = parseFloat(document.getElementById("calc-litres").value) || 0;
    const nowCost = litres * snap.current_pump_eur_per_l;
    const thenCost = litres * snap.predicted_pump_3w_eur_per_l;
    const diff = thenCost - nowCost;
    const sign = diff > 0.005 ? "up" : diff < -0.005 ? "down" : "flat";
    const verb = diff > 0 ? "more" : diff < 0 ? "less" : "same";

    nowEl.textContent = `Today: €${nowCost.toFixed(2)}`;
    thenEl.textContent = `In ~3 weeks: €${thenCost.toFixed(2)}`;
    diffEl.textContent = diff === 0
        ? "Difference: €0.00"
        : `Difference: €${diff >= 0 ? "+" : ""}${diff.toFixed(2)} (${verb})`;
    diffEl.setAttribute("data-sign", sign);
}

// ------------------ boot ------------------
document.getElementById("county-select").addEventListener("change", (e) => selectCounty(e.target.value));
document.getElementById("chart-range").addEventListener("change", () => render());
document.getElementById("calc-litres").addEventListener("input", updateCalculator);
document.getElementById("calc-fuel").addEventListener("change", updateCalculator);

async function boot() {
    const [prices, counties, manifest] = await Promise.all([
        jget("data/prices.json"),
        jget("data/counties.json"),
        loadManifest(),
    ]);
    nationalPrices = prices;
    countyData = counties;
    if (manifest && manifest.generated_at) {
        window.__updatedAtLabel = ` (updated at: ${fmtDateTime(manifest.generated_at)})`;
        const chip = document.getElementById("hdr-updated");
        if (chip) chip.textContent = `Updated ${fmtDateTime(manifest.generated_at)}`;
    } else {
        const chip = document.getElementById("hdr-updated");
        if (chip) { chip.textContent = "Awaiting refresh"; chip.dataset.tone = "warn"; }
    }

    if (!(countyData.counties || []).length) {
        document.getElementById("picker-meta").textContent =
            "No county snapshots yet — run `python ingest.py counties` and re-export.";
        document.getElementById("prediction-notes").textContent =
            (countyData.notes || []).join("  ");
        return;
    }

    buildDropdown();
    selectCounty(initialCounty());
    attachDragCompare("price-chart", "dc-popup");
    mountDevIngestButton({
        onDone: async () => {
            const [prices, counties] = await Promise.all([
                jget("data/prices.json"),
                jget("data/counties.json"),
            ]);
            nationalPrices = prices;
            countyData = counties;
            render();
        },
    });
}

boot().catch(err => {
    console.error("County page load failed:", err);
    document.getElementById("picker-meta").textContent =
        "Could not load county data. See the browser console.";
});
