// Configuration
const API_BASE = '';

// The stored world number is what the server list calls it ("Old School 2");
// the world people know is that plus 300.
const WORLD_OFFSET = 300;

// Collapsible About section
document.addEventListener('DOMContentLoaded', function() {
    const toggleBtn = document.getElementById('about-toggle');
    const aboutContent = document.getElementById('about-content');
    let collapsed = true;
    toggleBtn.addEventListener('click', function() {
        collapsed = !collapsed;
        aboutContent.style.display = collapsed ? 'none' : 'block';
        toggleBtn.textContent = collapsed ? 'More about this site' : 'Less about this site';
    });
});

// Globals
let populationChart = null;
let rawHistory = []; // cache of last fetched raw points
let globalMetadata = { locations: [], worlds: [], activity_groups: [] }; // Store metadata for comparison logic

// Line colours, in the order series are plotted. The picker caps selections at
// this length so no two hand-picked series ever share a colour.
const SERIES_COLORS = ['#ffff00', '#00ff00', '#00ffff', '#ff00ff', '#ff981f', '#ff0000', '#ffffff', '#aaaaaa'];
const MAX_SERIES = SERIES_COLORS.length;

// Each hand-picked series is its own /api/history call, and each of those is a
// world_data scan. Firing eight at once would occupy eight PythonAnywhere
// workers simultaneously; a pool keeps the total work the same but the site up.
const SERIES_CONCURRENCY = 3;

// Utility: format JS Date -> ISO used by datetime-local (without seconds)
function toLocalInputISO(date) {
    const pad = n => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

// Fetch metadata for filters
async function fetchMetadata() {
    try {
        const response = await fetch(`${API_BASE}/api/metadata`);
        const data = await response.json();
        globalMetadata = data; // Save for later use
        
        // Populate Worlds
        const worldSelect = document.getElementById('worldSelect');
        data.worlds.forEach(w => {
            const opt = document.createElement('option');
            opt.value = w;
            opt.textContent = `World ${parseInt(w) + WORLD_OFFSET}`;
            worldSelect.appendChild(opt);
        });

        // Populate Locations
        const locSelect = document.getElementById('locationSelect');
        data.locations.forEach(loc => {
            const opt = document.createElement('option');
            opt.value = loc.id;
            opt.textContent = loc.name;
            locSelect.appendChild(opt);
        });

        // Populate Activities. Uses the pre-grouped list, not data.activities:
        // the value is every member id of one dropdown entry, comma-separated.
        const activitySelect = document.getElementById('activitySelect');
        (data.activity_groups || []).forEach(group => {
            const opt = document.createElement('option');
            opt.value = group.ids.join(',');
            opt.textContent = group.name;
            activitySelect.appendChild(opt);
        });

        seriesCatalog = buildSeriesCatalog(data);
        renderSeriesResults();
    } catch (error) {
        console.error('Error fetching metadata:', error);
    }
}

// ---------------------------------------------------------------------------
// Series picker
//
// A hand-picked comparison is 2-8 series, and each one is an ordinary
// /api/history call with a filter the endpoint already supports: a world is
// world_id, an activity group is its member ids (activity_id takes a set). So
// this is entirely frontend — no new endpoint, and going through /api/history
// per selection keeps the display groups intact, which a server-side grouping
// would split back into raw activity strings.
// ---------------------------------------------------------------------------

let seriesCatalog = [];   // everything selectable
let selectedSeries = [];  // what is plotted, in colour order
let resultsCursor = -1;   // keyboard highlight within the visible results

// Enough to scroll through, few enough that 447 worlds don't build 447 nodes.
const MAX_RESULTS = 40;

function buildSeriesCatalog(data) {
    const worlds = (data.worlds || []).map(w => ({
        key: `w:${w}`,
        kind: 'world',
        label: `World ${parseInt(w) + WORLD_OFFSET}`,
        params: { world_id: w }
    }));
    // Keyed by name, not by ids: the ids are autoincrement surrogates, and a
    // group's membership shifts when Jagex adds a sibling activity.
    const activities = (data.activity_groups || []).map(group => ({
        key: `a:${group.name}`,
        kind: 'activity',
        label: group.name,
        params: { activity_id: group.ids.join(',') }
    }));
    return worlds.concat(activities);
}

function matchingSeries(query) {
    const q = query.trim().toLowerCase();
    const chosen = new Set(selectedSeries.map(s => s.key));
    return seriesCatalog.filter(item =>
        !chosen.has(item.key) && (!q || item.label.toLowerCase().includes(q)));
}

function renderSeriesResults() {
    const input = document.getElementById('seriesSearch');
    const list = document.getElementById('seriesResults');
    if (!input || !list) return;

    const open = document.activeElement === input && !input.disabled;
    list.innerHTML = '';
    if (!open) {
        list.style.display = 'none';
        input.setAttribute('aria-expanded', 'false');
        return;
    }

    if (selectedSeries.length >= MAX_SERIES) {
        list.appendChild(noteItem(`Limit of ${MAX_SERIES} series reached — remove one to add another.`));
    } else {
        const matches = matchingSeries(input.value);
        if (matches.length === 0) {
            list.appendChild(noteItem('Nothing matches.'));
        }
        matches.slice(0, MAX_RESULTS).forEach((item, idx) => {
            const li = document.createElement('li');
            li.setAttribute('role', 'option');
            li.textContent = item.label;
            const kind = document.createElement('span');
            kind.className = 'picker-kind';
            kind.textContent = item.kind === 'world' ? 'world' : 'activity';
            li.appendChild(kind);
            if (idx === resultsCursor) {
                li.classList.add('active');
                li.setAttribute('aria-selected', 'true');
            }
            // mousedown, not click: the input's blur would tear the list down first.
            li.addEventListener('mousedown', ev => {
                ev.preventDefault();
                addSeries(item);
            });
            list.appendChild(li);
        });
        if (matches.length > MAX_RESULTS) {
            list.appendChild(noteItem(`…and ${matches.length - MAX_RESULTS} more — keep typing.`));
        }
    }

    list.style.display = 'block';
    input.setAttribute('aria-expanded', 'true');
}

function noteItem(text) {
    const li = document.createElement('li');
    li.className = 'picker-note';
    li.textContent = text;
    return li;
}

function renderSeriesChips() {
    const chips = document.getElementById('seriesChips');
    const count = document.getElementById('seriesCount');
    if (!chips) return;

    chips.innerHTML = '';
    selectedSeries.forEach((item, idx) => {
        const chip = document.createElement('span');
        chip.className = 'picker-chip';

        const swatch = document.createElement('span');
        swatch.className = 'picker-swatch';
        swatch.style.background = SERIES_COLORS[idx];
        chip.appendChild(swatch);
        chip.appendChild(document.createTextNode(item.label));

        const remove = document.createElement('button');
        remove.type = 'button';
        remove.textContent = '×';
        remove.setAttribute('aria-label', `Remove ${item.label}`);
        remove.addEventListener('click', () => removeSeries(item.key));
        chip.appendChild(remove);

        chips.appendChild(chip);
    });

    if (count) {
        count.textContent = selectedSeries.length
            ? `${selectedSeries.length} of ${MAX_SERIES} selected`
            : `none selected (up to ${MAX_SERIES})`;
    }
}

function addSeries(item) {
    if (selectedSeries.length >= MAX_SERIES) return;
    if (selectedSeries.some(s => s.key === item.key)) return;
    selectedSeries.push(item);

    const input = document.getElementById('seriesSearch');
    if (input) input.value = '';
    resultsCursor = 0;
    renderSeriesChips();
    renderSeriesResults();
    updateFromInputs();
}

function removeSeries(key) {
    selectedSeries = selectedSeries.filter(s => s.key !== key);
    renderSeriesChips();
    renderSeriesResults();
    updateFromInputs();
}

function onSearchKeydown(ev) {
    const visible = Math.min(matchingSeries(ev.target.value).length, MAX_RESULTS);
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        ev.preventDefault();
        if (visible === 0) return;
        const delta = ev.key === 'ArrowDown' ? 1 : -1;
        resultsCursor = (resultsCursor + delta + visible) % visible;
        renderSeriesResults();
        const active = document.querySelector('#seriesResults li.active');
        if (active) active.scrollIntoView({ block: 'nearest' });
    } else if (ev.key === 'Enter') {
        ev.preventDefault();
        const matches = matchingSeries(ev.target.value);
        const pick = matches[resultsCursor >= 0 ? resultsCursor : 0];
        if (pick) addSeries(pick);
    } else if (ev.key === 'Escape') {
        ev.target.blur();
    }
}

// Custom compare supersedes the four filter dropdowns: each series carries its
// own filter, and applying a global one on top would silently empty any series
// it doesn't match (a world has exactly one region, type and activity).
const SUPERSEDED_FILTERS = ['worldSelect', 'locationSelect', 'f2pSelect', 'activitySelect'];

function syncCompareUI() {
    const custom = document.getElementById('compareSelect').value === 'custom';
    const picker = document.getElementById('comparePicker');
    if (picker) picker.style.display = custom ? 'block' : 'none';
    SUPERSEDED_FILTERS.forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        el.disabled = custom;
        if (el.parentElement) el.parentElement.classList.toggle('control-muted', custom);
    });
}

// Fetch the selected series through a small pool. Returns one entry per
// selection, in selection order, with failures carried rather than thrown so a
// single bad series doesn't take the whole chart down.
async function fetchSelectedSeries(range) {
    const results = new Array(selectedSeries.length);
    let next = 0;

    const worker = async () => {
        while (next < selectedSeries.length) {
            const idx = next++;
            const item = selectedSeries[idx];
            try {
                const data = await fetchHistory(Object.assign({}, range, item.params));
                results[idx] = { item, data };
            } catch (err) {
                results[idx] = { item, data: [], error: err.message || 'failed to load' };
            }
        }
    };

    const workers = Math.min(SERIES_CONCURRENCY, selectedSeries.length);
    await Promise.all(Array.from({ length: workers }, worker));
    return results;
}

// A series with no points keeps its legend entry and says why, rather than
// vanishing — a retired world or a seasonal activity is an answer, not a gap.
function seriesLabel(result) {
    const base = result.item.kind === 'activity'
        ? `${result.item.label} (activity)`
        : result.item.label;
    if (result.error) return `${base} — failed to load`;
    if (result.data.length === 0) return `${base} — no data in range`;
    return base;
}

function customDatasets(results) {
    return results.map((result, idx) => ({
        label: seriesLabel(result),
        data: result.data.map(p => ({ x: new Date(p.timestamp), y: p.count })),
        borderColor: SERIES_COLORS[idx],
        backgroundColor: null
    }));
}

function customNotice(results, startISO) {
    if (results.length === 0) {
        return `Search for worlds or activities above — up to ${MAX_SERIES} at a time.`;
    }
    const failed = results.filter(r => r.error);
    if (failed.length) {
        return `${failed.length} of ${results.length} series failed to load.`;
    }
    if (results.every(r => r.data.length === 0)) {
        return 'None of these series have data in the selected range. Seasonal ' +
               'activities and retired worlds only cover the period they ran — try a wider range.';
    }
    return filteredRangeNotice(startISO);
}

// Fetch latest player count
async function fetchLatest() {
    try {
        const response = await fetch(`${API_BASE}/api/latest`);
        const data = await response.json();
        document.getElementById('player-count').innerText = data.count.toLocaleString();
        
        // Update breakdown if available
        if (data.f2p_count !== undefined && data.members_count !== undefined) {
            document.getElementById('f2p-count').innerText = data.f2p_count.toLocaleString();
            document.getElementById('members-count').innerText = data.members_count.toLocaleString();
            document.getElementById('player-breakdown').style.display = 'block';

            // Update breakdown timestamp
            if (data.breakdown_timestamp) {
                try {
                    const bdDate = new Date(data.breakdown_timestamp);
                    const bdStr = bdDate.toLocaleString([], { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short' });
                    const bdEl = document.getElementById('breakdown-updated');
                    bdEl.innerText = `(Breakdown updated: ${bdStr})`;
                    bdEl.style.display = 'block';
                } catch (e) {
                    console.error("Error parsing breakdown timestamp", e);
                }
            }
        }

        // Parse the timestamp returned by the API (UTC ISO 8601) and
        // display it in the viewer's local timezone.
        try {
            const lastDate = new Date(data.timestamp);
            const lastUpdatedStr = lastDate.toLocaleString([], { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short' });
            document.getElementById('last-updated').innerText = `Last updated: ${lastUpdatedStr}`;
        } catch (e) {
            document.getElementById('last-updated').innerText = `Last updated: ${data.timestamp}`;
        }
    } catch (error) {
        console.error('Error fetching latest:', error);
        document.getElementById('player-count').innerText = "Offline";
    }
}

// Fetch history from API with optional start/end (ISO) and unit/step for server-side aggregation
async function fetchHistory({start=null, end=null, unit=null, step=null, limit=null, agg=null, world_id=null, location_id=null, is_f2p=null, activity_id=null} = {}) {
    try {
        const params = new URLSearchParams();
        if (start) params.set('start', start);
        if (end) params.set('end', end);
        if (unit) params.set('unit', unit);
        if (step) params.set('step', step);
        if (limit) params.set('limit', limit);
        if (agg) params.set('agg', agg);
        
        if (world_id) params.set('world_id', world_id);
        if (location_id) params.set('location_id', location_id);
        if (is_f2p !== null && is_f2p !== "") params.set('is_f2p', is_f2p);
        if (activity_id) params.set('activity_id', activity_id);

        const response = await fetch(`${API_BASE}/api/history?${params.toString()}`);
        const contentType = response.headers.get('content-type') || '';
        // If server returned non-OK (e.g., 400), try to show the server message
        if (!response.ok) {
            if (contentType.includes('application/json')) {
                const err = await response.json();
                throw new Error(err.error || err.message || `Server responded ${response.status}`);
            } else {
                const txt = await response.text();
                throw new Error(txt || `Server responded ${response.status}`);
            }
        }
        const data = await response.json();
        // Expecting [{timestamp: ISO, count: number}, ...]
        rawHistory = data;
        return data;
    } catch (err) {
        console.error('Error fetching history:', err);
        throw err;
    }
}

// Fetch every series of a comparison in one request. Returns an array of
// {key, data:[{x,y}]}, dropping series with no points in the range.
async function fetchGroupedHistory({group_by, start=null, end=null, unit=null, step=null, agg=null, world_id=null, location_id=null, is_f2p=null, activity_id=null} = {}) {
    const params = new URLSearchParams();
    params.set('group_by', group_by);
    if (start) params.set('start', start);
    if (end) params.set('end', end);
    if (unit) params.set('unit', unit);
    if (step) params.set('step', step);
    if (agg) params.set('agg', agg);
    if (world_id) params.set('world_id', world_id);
    if (location_id) params.set('location_id', location_id);
    if (is_f2p !== null && is_f2p !== "") params.set('is_f2p', is_f2p);
    if (activity_id) params.set('activity_id', activity_id);

    const response = await fetch(`${API_BASE}/api/history/grouped?${params.toString()}`);
    if (!response.ok) {
        const contentType = response.headers.get('content-type') || '';
        if (contentType.includes('application/json')) {
            const err = await response.json();
            throw new Error(err.error || err.message || `Server responded ${response.status}`);
        }
        throw new Error((await response.text()) || `Server responded ${response.status}`);
    }

    const payload = await response.json();
    const times = payload.timestamps.map(t => new Date(t));
    return payload.series.map(s => ({
        key: s.key,
        data: s.counts.reduce((pts, c, i) => {
            if (c !== null) pts.push({ x: times[i], y: c });
            return pts;
        }, [])
    })).filter(s => s.data.length > 0);
}

// The range boxes no longer describe what is on screen.
function markRangeCustom() {
    const presetEl = document.getElementById('presetSelect');
    if (presetEl) presetEl.value = 'custom';
}

function buildChart(datasets, granularityInfo) {
    const ctx = document.getElementById('populationChart').getContext('2d');
    const viewerTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Local';
    document.getElementById('chart-timezone').innerText = `Times shown in: ${viewerTimeZone}`;

    // If we receive a single array of points (legacy call), wrap it
    if (Array.isArray(datasets) && datasets.length > 0 && datasets[0].timestamp) {
        datasets = [{
            label: 'Online Players',
            data: datasets.map(p => ({ x: new Date(p.timestamp), y: p.count })),
            borderColor: '#ffff00',
            backgroundColor: 'rgba(255, 255, 0, 0.1)'
        }];
    }

    // Calculate global peak across all datasets
    let peak = null;
    let peakTime = null;
    let peakValue = -1;

    datasets.forEach(ds => {
        if (ds.data.length > 0) {
            const localPeak = ds.data.reduce((max, p) => p.y > max.y ? p : max, ds.data[0]);
            if (localPeak.y > peakValue) {
                peakValue = localPeak.y;
                peak = localPeak;
                peakTime = localPeak.x.getTime();
            }
        }
    });

    // Chart.js config with annotation for peak
    const cfg = {
        type: 'line',
        data: {
            datasets: datasets.map(ds => ({
                label: ds.label,
                data: ds.data,
                borderColor: ds.borderColor,
                backgroundColor: ds.backgroundColor || 'rgba(0,0,0,0)',
                borderWidth: 2,
                pointRadius: 0,
                fill: !!ds.backgroundColor, // Only fill if background color provided
                tension: 0.25
            }))
        },
        options: {
            responsive: true,
            interaction: { intersect: false, mode: 'index' },
            plugins: {
                decimation: { enabled: true, algorithm: 'lttb', samples: 1000 },
                zoom: {
                    // Zooming and panning move the view without touching the
                    // range boxes. Marking the preset Custom keeps the dropdown
                    // honest, and means picking a preset afterwards is a real
                    // change event rather than a silent no-op.
                    pan: { enabled: true, mode: 'x', onPanComplete: markRangeCustom },
                    zoom: {
                        wheel: { enabled: true }, pinch: { enabled: true }, mode: 'x',
                        onZoomComplete: markRangeCustom
                    }
                },
                annotation: {
                    annotations: peak ? {
                        peakLine: {
                            type: 'line',
                            xMin: peakTime,
                            xMax: peakTime,
                            borderColor: 'rgba(255, 0, 0, 0.8)', // Red for peak
                            borderWidth: 2,
                            borderDash: [5, 5],
                            label: {
                                display: true,
                                content: `Peak: ${peakValue.toLocaleString()}`,
                                position: '20%',
                                backgroundColor: 'rgba(255, 0, 0, 0.8)',
                                color: 'white',
                                font: {
                                    size: 12,
                                    family: 'RuneScape'
                                }
                            }
                        }
                    } : {}
                },
                tooltip: {
                    backgroundColor: '#5b4a3c',
                    titleColor: '#ff981f',
                    bodyColor: '#ffff00',
                    borderColor: '#383023',
                    borderWidth: 2,
                    titleFont: { family: 'RuneScape' },
                    bodyFont: { family: 'RuneScape' },
                    callbacks: {
                        title: function(context) {
                            if (!context.length) return '';
                            const d = context[0].parsed.x;
                            return new Date(d).toLocaleString([], { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short' });
                        },
                        label: function(context) {
                            return `${context.dataset.label}: ${context.parsed.y.toLocaleString()}`;
                        }
                    }
                },
            },
            scales: {
                x: {
                    type: 'time',
                    time: {
                        tooltipFormat: 'DD T',
                        displayFormats: {
                            minute: 'HH:mm',
                            hour: 'HH:mm',
                            day: 'MMM d',
                            week: 'MMM d',
                            month: 'MMM yyyy'
                        }
                    },
                    // Pin the axis to the range that was asked for. Without this
                    // Chart.js fits the axis to whatever came back, so a series
                    // that only covers part of the range stretches to fill it and
                    // the empty stretches either side vanish.
                    min: granularityInfo && granularityInfo.min ? granularityInfo.min : undefined,
                    max: granularityInfo && granularityInfo.max ? granularityInfo.max : undefined,
                    grid: { color: '#4e453a' },
                    ticks: { color: '#d4d4d4', font: { family: 'RuneScape' } }
                },
                y: {
                    beginAtZero: true, 
                    grid: { color: '#4e453a' },
                    ticks: { color: '#d4d4d4', font: { family: 'RuneScape' } }
                }
            }
        }
    };

    if (populationChart) {
        populationChart.destroy();
    }
    populationChart = new Chart(ctx, cfg);
}

// Small helpers to show/hide errors and to enable/disable controls while fetching
function showChartError(msg) {
    const el = document.getElementById('chartError');
    if (!el) return;
    if (msg) {
        el.innerText = msg;
        el.style.display = 'block';
    } else {
        el.innerText = '';
        el.style.display = 'none';
    }
}

function setControlsEnabled(enabled) {
    const ids = ['applyRangeBtn','resetZoomBtn','granularitySelect','aggregationSelect','startInput','endInput','presetSelect', 'worldSelect', 'locationSelect', 'f2pSelect', 'activitySelect', 'compareSelect', 'seriesSearch', 'seriesClearBtn'];
    ids.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.disabled = !enabled;
    });
    // Re-enabling would otherwise hand the superseded filters back.
    if (enabled) syncCompareUI();
}

// Simple spinner helpers
function showSpinner() {
    const s = document.getElementById('loadingSpinner');
    if (s) s.style.display = 'inline-block';
}
function hideSpinner() {
    const s = document.getElementById('loadingSpinner');
    if (s) s.style.display = 'none';
}

// Disable minute granularity options when selected range > 1 day
function updateGranularityAvailability() {
    const startVal = document.getElementById('startInput').value;
    const endVal = document.getElementById('endInput').value;
    const select = document.getElementById('granularitySelect');
    if (!select) return;

    // If both dates are present, compute duration in ms. If not, assume last-24h (allowed)
        const infoEl = document.getElementById('granularityInfo');
        if (startVal && endVal) {
        const startDt = new Date(startVal);
        const endDt = new Date(endVal);
        const durationMs = endDt - startDt;
        const oneDayMs = 30 * 24 * 60 * 60 * 1000;
        const disableMinutes = durationMs > oneDayMs;

        // iterate options and disable those ending with 'm'
        Array.from(select.options).forEach(opt => {
            if (opt.value.endsWith('m')) opt.disabled = disableMinutes;
        });

        // If current selection is a minute option and now disabled, pick 'hour'
        if (select.value.endsWith('m') && select.options[select.selectedIndex].disabled) {
            select.value = 'hour';
        }
            // Update tooltip text when minute options are disabled/enabled
            // Tooltip text is static and does not change
    } else {
        // No full range provided: enable minute options
        Array.from(select.options).forEach(opt => { if (opt.value.endsWith('m')) opt.disabled = false; });
            // Tooltip text is static and does not change
    }
}

// Per-world tracking started long after the site's history does. Anything before
// it is the imported weekly series, which is one site-wide number with no
// world/region/type/activity breakdown, so every filter silently starts there
// instead of where the range asked. Without this the chart looks broken.
function filteredRangeNotice(startISO) {
    const floor = globalMetadata.world_data_start;
    if (!floor || !startISO || startISO >= floor) return '';
    const shown = new Date(floor).toLocaleDateString([], {
        year: 'numeric', month: 'short', day: 'numeric'
    });
    return `Filtered history begins ${shown}, when per-world tracking started. ` +
           'Earlier data on this site is a site-wide total only.';
}

// Why a selection came back with nothing. Seasonal activities are the common
// case: Leagues and the Deadman tiers ran for a while and then stopped, so they
// are empty under the default 7d range even though the history is there.
function emptyResultMessage(activityId) {
    if (activityId) {
        return 'No data for this activity in the selected range. Seasonal activities ' +
               'run only for a period — try a wider range to find when it was live.';
    }
    return 'No data in the selected range.';
}

// ---------------------------------------------------------------------------
// Shareable view URLs
//
// The URL mirrors the controls, not the canvas. Everything goes in as something
// that survives a database rebuilt from scratch: a world is the number people
// actually see (302, not the stored 2), a region and an activity are their
// names. The ids behind those are autoincrement surrogates, and a grouped
// activity's member ids change the day Jagex adds a sibling world.
//
// Zoom and pan are deliberately not encoded — they already move the view
// without touching the range boxes, so there is no control state to mirror.
// ---------------------------------------------------------------------------

// Defaults are omitted from the URL, so an untouched view shares as a bare "/".
const STATE_DEFAULTS = { range: '7d', gran: 'hour', agg: 'max' };

// Readable stand-ins for the compare values, which are internal names.
const COMPARE_PARAM = { type: 'type', location: 'regions', custom: 'series' };

// Which params can only be resolved once /api/metadata has landed.
const METADATA_PARAMS = ['world', 'region', 'activity', 'series'];

function selectOption(id, predicate) {
    const el = document.getElementById(id);
    if (!el) return null;
    return Array.from(el.options).find(predicate) || null;
}

function optionLabel(id) {
    const el = document.getElementById(id);
    const opt = el && el.selectedOptions[0];
    return opt ? opt.textContent.trim() : '';
}

function seriesToken(item) {
    return item.kind === 'world'
        ? `w:${parseInt(item.params.world_id, 10) + WORLD_OFFSET}`
        : `a:${item.label}`;
}

function seriesFromToken(token) {
    if (token.startsWith('w:')) {
        const n = parseInt(token.slice(2), 10);
        if (!Number.isFinite(n)) return null;
        return seriesCatalog.find(i => i.key === `w:${n - WORLD_OFFSET}`) || null;
    }
    if (token.startsWith('a:')) {
        const want = token.slice(2).trim().toLowerCase();
        return seriesCatalog.find(
            i => i.kind === 'activity' && i.label.toLowerCase() === want) || null;
    }
    return null;
}

// A preset stays relative: "last 7d" should mean that to whoever opens the
// link, not to whoever sent it. Hand-edited boxes are pinned to the absolute
// instants they describe, so the recipient sees them in their own timezone.
function appendRangeParams(params) {
    const preset = document.getElementById('presetSelect').value;
    if (preset && preset !== 'custom') {
        if (preset !== STATE_DEFAULTS.range) params.set('range', preset);
        return;
    }
    const startVal = document.getElementById('startInput').value;
    const endVal = document.getElementById('endInput').value;
    if (startVal) params.set('start', toShareISO(new Date(startVal)));
    if (endVal) params.set('end', toShareISO(new Date(endVal)));
}

// Seconds, not milliseconds: the boxes only go to the minute anyway, and the
// three extra digits are noise in a link someone has to paste somewhere.
function toShareISO(date) {
    return `${date.toISOString().slice(0, 19)}Z`;
}

function appendFilterParams(params) {
    const compare = document.getElementById('compareSelect').value;
    if (COMPARE_PARAM[compare]) params.set('compare', COMPARE_PARAM[compare]);

    // The four filters are superseded in picker mode, so sharing them would
    // hand the recipient controls that are disabled on arrival.
    if (compare === 'custom') {
        selectedSeries.forEach(item => params.append('series', seriesToken(item)));
        return;
    }

    const world = document.getElementById('worldSelect').value;
    if (world) params.set('world', String(parseInt(world, 10) + WORLD_OFFSET));
    if (document.getElementById('locationSelect').value) {
        params.set('region', optionLabel('locationSelect'));
    }
    const isF2p = document.getElementById('f2pSelect').value;
    if (isF2p !== '') params.set('type', isF2p === '1' ? 'f2p' : 'members');
    if (document.getElementById('activitySelect').value) {
        params.set('activity', optionLabel('activitySelect'));
    }
}

function currentViewParams() {
    const params = new URLSearchParams();
    appendRangeParams(params);
    const gran = document.getElementById('granularitySelect').value;
    if (gran !== STATE_DEFAULTS.gran) params.set('gran', gran);
    const agg = document.getElementById('aggregationSelect').value;
    if (agg !== STATE_DEFAULTS.agg) params.set('agg', agg);
    appendFilterParams(params);
    return params;
}

// replaceState, not pushState: every dropdown twiddle would otherwise become a
// back-button entry. The bar is the whole interface — the link to share is
// always the one already showing.
function syncUrl() {
    const query = currentViewParams().toString();
    try {
        window.history.replaceState(null, '', window.location.pathname + (query ? `?${query}` : ''));
    } catch (err) {
        console.error('Could not update the URL:', err);
    }
}

function applyRangeParams(params, ignored) {
    const presetEl = document.getElementById('presetSelect');
    const start = params.get('start');
    const end = params.get('end');

    if (start || end) {
        const startDt = start ? new Date(start) : null;
        const endDt = end ? new Date(end) : null;
        if ((startDt && isNaN(startDt)) || (endDt && isNaN(endDt))) {
            ignored.push('an unreadable date range');
            return;
        }
        if (startDt) document.getElementById('startInput').value = toLocalInputISO(startDt);
        if (endDt) document.getElementById('endInput').value = toLocalInputISO(endDt);
        presetEl.value = 'custom';
        return;
    }

    const range = params.get('range');
    if (!range) return;
    if (selectOption('presetSelect', o => o.value === range && o.value !== 'custom')) {
        presetEl.value = range;
        setPresetRange(range);
    } else {
        ignored.push(`the range "${range}"`);
    }
}

function applySelectParam(id, params, name, toValue, ignored) {
    const raw = params.get(name);
    if (!raw) return;
    const value = toValue(raw);
    if (value !== null && selectOption(id, o => o.value === value)) {
        document.getElementById(id).value = value;
    } else {
        ignored.push(`${name} "${raw}"`);
    }
}

function applySeriesParams(params, ignored) {
    const tokens = params.getAll('series');
    if (tokens.length === 0) return;
    selectedSeries = [];
    tokens.forEach(token => {
        const item = seriesFromToken(token);
        if (!item) {
            ignored.push(`series "${token}"`);
        } else if (selectedSeries.length >= MAX_SERIES) {
            ignored.push(`${item.label} (over the limit of ${MAX_SERIES})`);
        } else if (!selectedSeries.some(s => s.key === item.key)) {
            selectedSeries.push(item);
        }
    });
}

// Anything the link names that no longer exists here is dropped and reported;
// the rest of the view still loads.
function applyViewParams(params) {
    const ignored = [];
    applyRangeParams(params, ignored);
    applySelectParam('granularitySelect', params, 'gran', v => v, ignored);
    applySelectParam('aggregationSelect', params, 'agg', v => v, ignored);
    applySelectParam('compareSelect', params, 'compare', v => {
        const found = Object.keys(COMPARE_PARAM).find(k => COMPARE_PARAM[k] === v);
        return found || null;
    }, ignored);
    applySelectParam('worldSelect', params, 'world', v => {
        const n = parseInt(v, 10);
        return Number.isFinite(n) ? String(n - WORLD_OFFSET) : null;
    }, ignored);
    applySelectParam('locationSelect', params, 'region', v => {
        const opt = selectOption('locationSelect',
            o => o.textContent.trim().toLowerCase() === v.trim().toLowerCase());
        return opt ? opt.value : null;
    }, ignored);
    applySelectParam('f2pSelect', params, 'type', v => {
        if (v === 'f2p') return '1';
        if (v === 'members') return '0';
        return null;
    }, ignored);
    applySelectParam('activitySelect', params, 'activity', v => {
        const opt = selectOption('activitySelect',
            o => o.textContent.trim().toLowerCase() === v.trim().toLowerCase());
        return opt ? opt.value : null;
    }, ignored);
    applySeriesParams(params, ignored);
    return ignored;
}

function showShareNotice(ignored) {
    const el = document.getElementById('shareNotice');
    if (!el || ignored.length === 0) return;
    el.textContent = `This link asked for ${ignored.join(', ')}, which this site ` +
                     'no longer has. Everything else in the link was applied.';
    el.style.display = 'block';
}

// Update chart using inputs (gracefully handle 400 responses from server)
async function updateFromInputs() {
    syncUrl();

    const gran = document.getElementById('granularitySelect').value;
    const agg = document.getElementById('aggregationSelect').value;
    const startVal = document.getElementById('startInput').value;
    const endVal = document.getElementById('endInput').value;
    
    const worldId = document.getElementById('worldSelect').value;
    const locationId = document.getElementById('locationSelect').value;
    const isF2p = document.getElementById('f2pSelect').value;
    const activityId = document.getElementById('activitySelect').value;
    const compareMode = document.getElementById('compareSelect').value;

    const startISO = startVal ? new Date(startVal).toISOString() : null;
    const endISO = endVal ? new Date(endVal).toISOString() : null;

    // Map granularity string to Chart.js unit + optional step (for minute intervals)
    let unit = gran;
    let step = null;
    if (gran.endsWith('m')) {
        unit = 'minute';
        step = parseInt(gran.slice(0, -1), 10);
    }

    setControlsEnabled(false);
    showChartError('');
    showSpinner();
    
    try {
        let datasets = [];
        let customResults = null;

        if (compareMode === 'none') {
            // Standard single series fetch
            const history = await fetchHistory({ 
                start: startISO, 
                end: endISO, 
                unit: unit, 
                step: step, 
                agg: agg,
                world_id: worldId,
                location_id: locationId,
                is_f2p: isF2p,
                activity_id: activityId
            });

            datasets = [{
                label: 'Online Players',
                data: history.map(p => ({ x: new Date(p.timestamp), y: p.count })),
                borderColor: '#ffff00',
                backgroundColor: 'rgba(255, 255, 0, 0.1)'
            }];
        } else if (compareMode === 'type') {
            // Compare F2P vs Members
            // We ignore the 'is_f2p' filter from the dropdown if it's set, as we are splitting by it.
            // We keep world/location filters if set.
            
            const [f2pData, memData] = await Promise.all([
                fetchHistory({ start: startISO, end: endISO, unit, step, agg, world_id: worldId, location_id: locationId, activity_id: activityId, is_f2p: 1 }),
                fetchHistory({ start: startISO, end: endISO, unit, step, agg, world_id: worldId, location_id: locationId, activity_id: activityId, is_f2p: 0 })
            ]);

            datasets = [
                {
                    label: 'Free-to-Play',
                    data: f2pData.map(p => ({ x: new Date(p.timestamp), y: p.count })),
                    borderColor: '#aaaaaa', // Silver/Grey for F2P
                    backgroundColor: 'rgba(170, 170, 170, 0.1)'
                },
                {
                    label: 'Members',
                    data: memData.map(p => ({ x: new Date(p.timestamp), y: p.count })),
                    borderColor: '#ffff00', // Gold for Members
                    backgroundColor: 'rgba(255, 255, 0, 0.1)'
                }
            ];
        } else if (compareMode === 'location') {
            // Compare Regions
            // We ignore 'location_id' filter.
            // We keep world/f2p filters if set (though world implies location, so usually world filter should be empty)
            const locNames = new Map(globalMetadata.locations.map(loc => [String(loc.id), loc.name]));
            const series = await fetchGroupedHistory({
                group_by: 'location',
                start: startISO, end: endISO, unit, step, agg,
                world_id: worldId, is_f2p: isF2p, activity_id: activityId
            });

            datasets = series.map((s, idx) => ({
                label: locNames.get(String(s.key)) || `Location ${s.key}`,
                data: s.data,
                borderColor: SERIES_COLORS[idx % SERIES_COLORS.length],
                backgroundColor: null // No fill for many lines to avoid clutter
            }));
        } else if (compareMode === 'custom') {
            // Hand-picked series. Each carries its own filter, so the dropdowns
            // above are ignored — see SUPERSEDED_FILTERS.
            customResults = await fetchSelectedSeries({
                start: startISO, end: endISO, unit, step, agg
            });
            datasets = customDatasets(customResults);
        }

        // Every comparison mode reads world_data too, so they hit the same floor
        // even with no filter set.
        const filtered = worldId || locationId || isF2p !== "" || activityId
                         || compareMode !== 'none';
        if (customResults) {
            showChartError(customNotice(customResults, startISO));
        } else if (datasets.every(ds => !ds.data || ds.data.length === 0)) {
            showChartError(emptyResultMessage(activityId));
        } else if (filtered) {
            showChartError(filteredRangeNotice(startISO));
        }
        buildChart(datasets, {
            unit, step,
            min: startISO ? new Date(startISO).getTime() : null,
            max: endISO ? new Date(endISO).getTime() : null
        });
    } catch (err) {
        console.error('Update failed:', err);
        showChartError(err.message || 'Failed to load data');
    } finally {
        hideSpinner();
        setControlsEnabled(true);
    }
}

// Preset range buttons
function setPresetHours(hours) {
    const end = new Date();
    const start = new Date(end.getTime() - hours*60*60*1000);
    document.getElementById('startInput').value = toLocalInputISO(start);
    document.getElementById('endInput').value = toLocalInputISO(end);
}

// Preset for months (handles month rollovers)
function setPresetMonths(months) {
    const end = new Date();
    const start = new Date(end.getFullYear(), end.getMonth() - months, end.getDate(), end.getHours(), end.getMinutes(), end.getSeconds());
    document.getElementById('startInput').value = toLocalInputISO(start);
    document.getElementById('endInput').value = toLocalInputISO(end);
}

function setPresetYears(years) {
    const end = new Date();
    const start = new Date(end.getFullYear() - years, end.getMonth(), end.getDate(), end.getHours(), end.getMinutes(), end.getSeconds());
    document.getElementById('startInput').value = toLocalInputISO(start);
    document.getElementById('endInput').value = toLocalInputISO(end);
}

// Fill the range boxes from a preset without redrawing, so restoring a shared
// link can set the range and fetch once rather than twice.
function setPresetRange(v) {
    switch (v) {
        case '3h': setPresetHours(3); break;
        case '6h': setPresetHours(6); break;
        case '12h': setPresetHours(12); break;
        case '24h': setPresetHours(24); break;
        case '7d': setPresetHours(24*7); break;
        case '30d': setPresetHours(24*30); break;
        case '6m': setPresetMonths(6); break;
        case '1y': setPresetYears(1); break;
        case '5y': setPresetYears(5); break;
        case '10y': setPresetYears(10); break;
    }
}

function applyPreset(v) {
    setPresetRange(v);
    updateGranularityAvailability();
    updateFromInputs();
}

// Initialize page: set default inputs and render
async function initializePage() {
    // Default to last 7d with hour granularity
    setPresetHours(24 * 7);
    document.getElementById('granularitySelect').value = 'hour';
    // Ensure minute options availability reflects the default range
    updateGranularityAvailability();

    // Wire up controls. Apply recomputes the active preset rather than replaying
    // the boxes: a preset means "last N from now", and picking the preset that is
    // already showing fires no change event, so this is the only way to refresh
    // one. Hand-edited boxes read as Custom and are used as-is.
    document.getElementById('applyRangeBtn').addEventListener('click', () => {
        const preset = document.getElementById('presetSelect');
        if (preset && preset.value && preset.value !== 'custom') {
            applyPreset(preset.value);
        } else {
            updateFromInputs();
        }
    });
    const presetEl = document.getElementById('presetSelect');
    if (presetEl) {
        // set default preset to Last 7d
        presetEl.value = '7d';
        presetEl.addEventListener('change', () => {
            applyPreset(presetEl.value);
        });
    }
    document.getElementById('granularitySelect').addEventListener('change', updateFromInputs);
    document.getElementById('aggregationSelect').addEventListener('change', updateFromInputs);
    
    // When a specific world is selected, reset other filters as they don't apply
    document.getElementById('worldSelect').addEventListener('change', function() {
        if (this.value) {
            document.getElementById('locationSelect').value = "";
            document.getElementById('f2pSelect').value = "";
            // A single world has exactly one activity, so combining the two
            // yields either that world or nothing.
            document.getElementById('activitySelect').value = "";
            document.getElementById('compareSelect').value = "none";
        }
        updateFromInputs();
    });

    document.getElementById('locationSelect').addEventListener('change', updateFromInputs);
    document.getElementById('f2pSelect').addEventListener('change', updateFromInputs);
    document.getElementById('activitySelect').addEventListener('change', updateFromInputs);
    document.getElementById('compareSelect').addEventListener('change', () => {
        syncCompareUI();
        updateFromInputs();
    });

    // Series picker
    const searchEl = document.getElementById('seriesSearch');
    searchEl.addEventListener('input', () => { resultsCursor = 0; renderSeriesResults(); });
    searchEl.addEventListener('focus', () => { resultsCursor = 0; renderSeriesResults(); });
    searchEl.addEventListener('blur', () => renderSeriesResults());
    searchEl.addEventListener('keydown', onSearchKeydown);
    document.getElementById('seriesClearBtn').addEventListener('click', () => {
        if (selectedSeries.length === 0) return;
        selectedSeries = [];
        renderSeriesChips();
        updateFromInputs();
    });
    document.getElementById('resetZoomBtn').addEventListener('click', () => { if (populationChart) populationChart.resetZoom(); });

    // Recalculate availability when user edits start/end inputs
    const onInputChange = () => {
        updateGranularityAvailability();
        if (presetEl) presetEl.value = 'custom';
    };
    document.getElementById('startInput').addEventListener('change', onInputChange);
    document.getElementById('endInput').addEventListener('change', onInputChange);

    // A shared link naming a world, region, activity or series can only be
    // restored once the option lists exist, so those wait on metadata. Every
    // other link, including a plain load, still fires all three together.
    const params = new URLSearchParams(window.location.search);
    const metadata = fetchMetadata();
    if (METADATA_PARAMS.some(name => params.has(name))) await metadata;

    showShareNotice(applyViewParams(params));
    updateGranularityAvailability();
    renderSeriesChips();
    syncCompareUI();

    await Promise.all([metadata, fetchLatest(), updateFromInputs()]);

    // Auto-refresh every 2 minutes
    setInterval(async () => {
        await fetchLatest();
        // If we are on a preset (not custom), refresh the chart range to keep it "live"
        if (presetEl && presetEl.value !== 'custom') {
            applyPreset(presetEl.value);
        }
    }, 2 * 60 * 1000);
}

// Run initialize on page load
window.addEventListener('DOMContentLoaded', initializePage);

// Easter Egg: Dragon Scimitar Cursor Toggle
document.addEventListener('DOMContentLoaded', function() {
    const toggle = document.getElementById('scimitar-toggle');
    if (toggle) {
        toggle.addEventListener('click', function() {
            document.body.classList.toggle('dragon-cursor');
        });
    }
});

// Easter Egg: Gnome Child Scroll
let scrollCount = 0;
const SCROLL_THRESHOLD = 50; // Number of scroll events to trigger

function handleWheel(e) {
    const gnome = document.getElementById('gnome-child');
    // Check if we are at the bottom (or page is not scrollable)
    const isAtBottom = (window.innerHeight + window.scrollY) >= document.documentElement.scrollHeight - 10;
    
    if (isAtBottom) {
        if (e.deltaY > 0) { // Scrolling down while at bottom
            scrollCount++;
            if (scrollCount > SCROLL_THRESHOLD) {
                gnome.classList.add('peeking');
            }
        } else { // Scrolling up
            scrollCount = 0;
            gnome.classList.remove('peeking');
        }
    } else {
        scrollCount = 0;
        gnome.classList.remove('peeking');
    }
}

// Reset if user scrolls away using scrollbar
window.addEventListener('scroll', () => {
    const gnome = document.getElementById('gnome-child');
    const isAtBottom = (window.innerHeight + window.scrollY) >= document.documentElement.scrollHeight - 10;
    if (!isAtBottom) {
        scrollCount = 0;
        touchScrollDistance = 0;
        gnome.classList.remove('peeking');
    }
});

// Listen for wheel events to catch scrolling even when page doesn't move
window.addEventListener('wheel', handleWheel);

// Mobile Touch Support
let touchStartY = 0;
let touchScrollDistance = 0;
const TOUCH_THRESHOLD = 400; // Pixels

window.addEventListener('touchstart', (e) => {
    touchStartY = e.touches[0].clientY;
}, { passive: true });

window.addEventListener('touchmove', (e) => {
    const gnome = document.getElementById('gnome-child');
    const currentY = e.touches[0].clientY;
    const deltaY = touchStartY - currentY;
    touchStartY = currentY;

    const isAtBottom = (window.innerHeight + window.scrollY) >= document.documentElement.scrollHeight - 10;

    if (isAtBottom) {
        if (deltaY > 0) {
            touchScrollDistance += deltaY;
            if (touchScrollDistance > TOUCH_THRESHOLD) {
                gnome.classList.add('peeking');
            }
        } else if (deltaY < -2) { // Small buffer for jitter
            touchScrollDistance = 0;
            gnome.classList.remove('peeking');
        }
    } else {
        touchScrollDistance = 0;
        gnome.classList.remove('peeking');
    }
}, { passive: true }); 

// Easter Egg: Konami Code
const konamiCode = ['ArrowUp', 'ArrowUp', 'ArrowDown', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'ArrowLeft', 'ArrowRight', 'b', 'a'];
let konamiIndex = 0;

document.addEventListener('keydown', (e) => {
    if (e.key === konamiCode[konamiIndex]) {
        konamiIndex++;
        if (konamiIndex === konamiCode.length) {
            const el = document.getElementById('connection-lost');
            el.style.display = 'block';
            setTimeout(() => { el.style.display = 'none'; }, 5000);
            konamiIndex = 0;
        }
    } else {
        konamiIndex = 0;
    }
});

// Easter Egg: Play sound after 60 minutes
setTimeout(() => {
    const audio = new Audio('https://oldschool.runescape.wiki/images/Armadyl_Eye_sound.ogg?37997');
    audio.volume = 0.5;
    audio.play().catch(e => console.error("Audio play failed (autoplay policy?):", e));
}, 60 * 60 * 1000);
