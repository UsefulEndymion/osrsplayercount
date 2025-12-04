// Configuration
const API_BASE = '';

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
let globalMetadata = { locations: [], worlds: [] }; // Store metadata for comparison logic

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
            opt.textContent = `World ${parseInt(w) + 300}`;
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

        // Activities are available in data.activities if we want to add them later
    } catch (error) {
        console.error('Error fetching metadata:', error);
    }
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
async function fetchHistory({start=null, end=null, unit=null, step=null, limit=null, agg=null, world_id=null, location_id=null, is_f2p=null} = {}) {
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
                    pan: { enabled: true, mode: 'x' },
                    zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: 'x' }
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
    const ids = ['applyRangeBtn','resetZoomBtn','granularitySelect','aggregationSelect','startInput','endInput','presetSelect', 'worldSelect', 'locationSelect', 'f2pSelect', 'compareSelect'];
    ids.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.disabled = !enabled;
    });
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

// Update chart using inputs (gracefully handle 400 responses from server)
async function updateFromInputs() {
    const gran = document.getElementById('granularitySelect').value;
    const agg = document.getElementById('aggregationSelect').value;
    const startVal = document.getElementById('startInput').value;
    const endVal = document.getElementById('endInput').value;
    
    const worldId = document.getElementById('worldSelect').value;
    const locationId = document.getElementById('locationSelect').value;
    const isF2p = document.getElementById('f2pSelect').value;
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
        const colors = ['#ffff00', '#00ff00', '#00ffff', '#ff00ff', '#ff981f', '#ff0000', '#ffffff', '#aaaaaa'];

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
                is_f2p: isF2p
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
                fetchHistory({ start: startISO, end: endISO, unit, step, agg, world_id: worldId, location_id: locationId, is_f2p: 1 }),
                fetchHistory({ start: startISO, end: endISO, unit, step, agg, world_id: worldId, location_id: locationId, is_f2p: 0 })
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
            
            // Use globalMetadata.locations to get list
            const locs = globalMetadata.locations;
            const requests = locs.map(loc => 
                fetchHistory({ start: startISO, end: endISO, unit, step, agg, world_id: worldId, is_f2p: isF2p, location_id: loc.id })
                    .then(data => ({ loc, data }))
            );
            
            const results = await Promise.all(requests);
            
            datasets = results.map((res, idx) => ({
                label: res.loc.name,
                data: res.data.map(p => ({ x: new Date(p.timestamp), y: p.count })),
                borderColor: colors[idx % colors.length],
                backgroundColor: null // No fill for many lines to avoid clutter
            }));
        } else if (compareMode === 'worlds') {
            // Compare All Worlds (Filtered by other selections)
            // We iterate all known worlds and fetch data for each, respecting location/f2p filters.
            // If a world doesn't match the filters, the API returns empty data, and we skip it.
            
            const worlds = globalMetadata.worlds;
            // Create a promise for each world
            const requests = worlds.map(w => 
                fetchHistory({ 
                    start: startISO, end: endISO, unit, step, agg, 
                    world_id: w, 
                    location_id: locationId, 
                    is_f2p: isF2p 
                })
                .then(data => ({ world: w, data }))
                .catch(e => null)
            );
            
            const results = await Promise.all(requests);
            
            datasets = results
                .filter(r => r && r.data && r.data.length > 0)
                .map((res, idx) => ({
                    label: `World ${parseInt(res.world) + 300}`,
                    data: res.data.map(p => ({ x: new Date(p.timestamp), y: p.count })),
                    borderColor: colors[idx % colors.length],
                    backgroundColor: null,
                    borderWidth: 1, // Thinner lines for mass comparison
                    pointRadius: 0
                }));
        }

        buildChart(datasets, { unit, step });
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

function applyPreset(v) {
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

    // Wire up controls
    document.getElementById('applyRangeBtn').addEventListener('click', updateFromInputs);
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
            document.getElementById('compareSelect').value = "none";
        }
        updateFromInputs();
    });

    document.getElementById('locationSelect').addEventListener('change', updateFromInputs);
    document.getElementById('f2pSelect').addEventListener('change', updateFromInputs);
    document.getElementById('compareSelect').addEventListener('change', updateFromInputs);
    document.getElementById('resetZoomBtn').addEventListener('click', () => { if (populationChart) populationChart.resetZoom(); });
    
    // Recalculate availability when user edits start/end inputs
    const onInputChange = () => {
        updateGranularityAvailability();
        if (presetEl) presetEl.value = 'custom';
    };
    document.getElementById('startInput').addEventListener('change', onInputChange);
    document.getElementById('endInput').addEventListener('change', onInputChange);

    // Initial fetch and render
    await fetchMetadata();
    await fetchLatest();
    await updateFromInputs();

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

// Easter Egg: Play sound after 20 minutes
setTimeout(() => {
    const audio = new Audio('https://oldschool.runescape.wiki/images/Armadyl_Eye_sound.ogg?37997');
    audio.volume = 0.5;
    audio.play().catch(e => console.error("Audio play failed (autoplay policy?):", e));
}, 60 * 60 * 1000);
