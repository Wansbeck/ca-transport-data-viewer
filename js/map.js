const map = new maplibregl.Map({
  container: 'map',
  style: {
    version: 8,
    sources: {
      osm: {
        type: 'raster',
        tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
        tileSize: 256,
        attribution: '© OpenStreetMap contributors'
      }
    },
    layers: [{ id: 'osm', type: 'raster', source: 'osm' }]
  },
  center: [-119.5, 37.2],
  zoom: 5.2,
  minZoom: 4,
  maxZoom: 18
});

map.addControl(new maplibregl.NavigationControl(), 'top-right');
map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: 'imperial' }));

let aadtFeatures = [];
let truckFeatures = [];

function num(value) {
  const cleaned = String(value ?? '').replace(/,/g, '').trim();
  const parsed = Number(cleaned);
  return Number.isFinite(parsed) ? parsed : 0;
}

function fmt(value) {
  const n = num(value);
  return n ? n.toLocaleString('en-US') : '—';
}

function fmtPct(value) {
  const n = Number(value);
  return Number.isFinite(n) ? `${n.toFixed(1)}%` : '—';
}

function matchLabel(method) {
  return method === 'exact'
    ? 'Exact postmile'
    : method === 'nearest_postmile'
      ? 'Nearest postmile ≤0.03 mi'
      : '—';
}

async function loadGeoJson(path) {
  const response = await fetch(path, { cache: 'no-store' });
  if (!response.ok) throw new Error(`Failed to load ${path}: ${response.status}`);
  return response.json();
}

function populateRoutes() {
  const routeSelect = document.getElementById('route');
  const routes = [...new Set(
    [...aadtFeatures, ...truckFeatures]
      .map(f => String(f.properties.RTE || '').trim())
      .filter(Boolean)
  )].sort((a, b) => Number(a) - Number(b));

  routeSelect.innerHTML =
    '<option value="all">All routes</option>' +
    routes.map(route => `<option value="${route}">Route ${Number(route)}</option>`).join('');

  routeSelect.disabled = false;
}

function updateFilters() {
  const route = document.getElementById('route').value;
  const minAadt = Number(document.getElementById('aadt').value);
  const minTruckPct = Number(document.getElementById('truck-pct').value);

  if (map.getLayer('aadt-points')) {
    const filters = [['>=', ['get', 'MAX_AADT'], minAadt]];
    if (route !== 'all') filters.push(['==', ['get', 'RTE'], route]);
    map.setFilter('aadt-points', ['all', ...filters]);
  }

  if (map.getLayer('truck-points')) {
    const filters = [['>=', ['coalesce', ['get', 'TRUCK_PERCENT'], 0], minTruckPct]];
    if (route !== 'all') filters.push(['==', ['get', 'RTE'], route]);
    map.setFilter('truck-points', ['all', ...filters]);
  }

  const visibleAadt = aadtFeatures.filter(f => {
    const routeOk = route === 'all' || String(f.properties.RTE) === route;
    return routeOk && num(f.properties.MAX_AADT) >= minAadt;
  }).length;

  const visibleTruck = truckFeatures.filter(f => {
    const routeOk = route === 'all' || String(f.properties.RTE) === route;
    return routeOk && num(f.properties.TRUCK_PERCENT) >= minTruckPct;
  }).length;

  document.getElementById('aadt-value').textContent = minAadt.toLocaleString('en-US');
  document.getElementById('truck-pct-value').textContent = minTruckPct.toLocaleString('en-US');
  document.getElementById('visible-count').textContent =
    `${visibleAadt.toLocaleString('en-US')} AADT locations; ${visibleTruck.toLocaleString('en-US')} truck locations match the filters`;
}

function aadtPopup(p) {
  return `
    <div class="popup-title">Route ${Number(p.RTE)}: ${p.DESCRIPTION || 'Caltrans traffic count location'}</div>
    <div class="popup-grid">
      <span>Data year</span><strong>${p.YEAR || '—'}</strong>
      <span>County</span><strong>${p.CNTY || '—'}</strong>
      <span>Postmile</span><strong>${p.PM ?? '—'}</strong>
      <span>Back AADT</span><strong>${fmt(p.BACK_AADT)}</strong>
      <span>Ahead AADT</span><strong>${fmt(p.AHEAD_AADT)}</strong>
      <span>Back peak hour</span><strong>${fmt(p.BACK_PEAK_HOUR)}</strong>
      <span>Ahead peak hour</span><strong>${fmt(p.AHEAD_PEAK_HOUR)}</strong>
      <span>Location match</span><strong>${matchLabel(p.MATCH_METHOD)}</strong>
    </div>
  `;
}

function truckPopup(p) {
  return `
    <div class="popup-title">Route ${Number(p.RTE)}: ${p.DESCRIPTION || 'Caltrans truck count location'}</div>
    <div class="popup-grid">
      <span>Data year</span><strong>${p.YEAR || '—'}</strong>
      <span>County</span><strong>${p.CNTY || '—'}</strong>
      <span>Postmile</span><strong>${p.PM ?? '—'}</strong>
      <span>Total AADT</span><strong>${fmt(p.TOTAL_AADT)}</strong>
      <span>Truck AADT</span><strong>${fmt(p.TRUCK_AADT)}</strong>
      <span>Truck share</span><strong>${fmtPct(p.TRUCK_PERCENT)}</strong>
      <span>2-axle trucks</span><strong>${fmt(p.TRK_2_AXLE)}</strong>
      <span>3-axle trucks</span><strong>${fmt(p.TRK_3_AXLE)}</strong>
      <span>4-axle trucks</span><strong>${fmt(p.TRK_4_AXLE)}</strong>
      <span>5-axle trucks</span><strong>${fmt(p.TRK_5_AXLE)}</strong>
      <span>Location match</span><strong>${matchLabel(p.MATCH_METHOD)}</strong>
    </div>
  `;
}

function bindLayerPopup(layerId, htmlFn) {
  map.on('click', layerId, e => {
    const feature = e.features && e.features[0];
    if (!feature) return;
    new maplibregl.Popup()
      .setLngLat(feature.geometry.coordinates)
      .setHTML(htmlFn(feature.properties))
      .addTo(map);
  });

  map.on('mouseenter', layerId, () => {
    map.getCanvas().style.cursor = 'pointer';
  });

  map.on('mouseleave', layerId, () => {
    map.getCanvas().style.cursor = '';
  });
}

map.on('load', async () => {
  map.fitBounds([[-124.48, 32.52], [-114.13, 42.01]], {
    padding: 36,
    duration: 0
  });

  const status = document.getElementById('status-message');

  try {
    const [aadtData, truckData] = await Promise.all([
      loadGeoJson('data/aadt-2024.geojson'),
      loadGeoJson('data/truck-2024.geojson')
    ]);

    aadtFeatures = aadtData.features || [];
    truckFeatures = truckData.features || [];

    map.addSource('aadt', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: aadtFeatures }
    });

    map.addLayer({
      id: 'aadt-points',
      type: 'circle',
      source: 'aadt',
      paint: {
        'circle-radius': [
          'interpolate', ['linear'], ['get', 'MAX_AADT'],
          0, 3,
          25000, 4,
          75000, 6,
          150000, 8,
          300000, 11
        ],
        'circle-color': [
          'step', ['get', 'MAX_AADT'],
          '#69b3a2',
          25000, '#f0c05a',
          75000, '#e8824f',
          150000, '#b94040'
        ],
        'circle-opacity': 0.78,
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 0.7
      }
    });

    map.addSource('truck', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: truckFeatures }
    });

    map.addLayer({
      id: 'truck-points',
      type: 'circle',
      source: 'truck',
      layout: { visibility: 'none' },
      paint: {
        'circle-radius': [
          'interpolate', ['linear'], ['coalesce', ['get', 'TRUCK_AADT'], 0],
          0, 3,
          1000, 4,
          5000, 6,
          10000, 8,
          25000, 11
        ],
        'circle-color': [
          'step', ['coalesce', ['get', 'TRUCK_PERCENT'], 0],
          '#b9c7d8',
          5, '#7ba0c7',
          10, '#477aa8',
          20, '#244c73'
        ],
        'circle-opacity': 0.82,
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 0.8
      }
    });

    populateRoutes();
    document.getElementById('aadt').disabled = false;
    document.getElementById('truck-pct').disabled = false;
    updateFilters();

    bindLayerPopup('aadt-points', aadtPopup);
    bindLayerPopup('truck-points', truckPopup);

    status.textContent =
      `${aadtFeatures.length.toLocaleString('en-US')} 2024 AADT locations and ${truckFeatures.length.toLocaleString('en-US')} 2024 truck locations loaded.`;
  } catch (error) {
    console.error(error);
    status.textContent = 'The Caltrans traffic data could not be loaded. The base map is still available.';
  }
});

document.getElementById('route').addEventListener('change', updateFilters);
document.getElementById('aadt').addEventListener('input', updateFilters);
document.getElementById('truck-pct').addEventListener('input', updateFilters);

document.getElementById('aadt-layer').addEventListener('change', event => {
  if (!map.getLayer('aadt-points')) return;
  map.setLayoutProperty('aadt-points', 'visibility', event.target.checked ? 'visible' : 'none');
});

document.getElementById('truck-layer').addEventListener('change', event => {
  if (!map.getLayer('truck-points')) return;
  map.setLayoutProperty('truck-points', 'visibility', event.target.checked ? 'visible' : 'none');
});
