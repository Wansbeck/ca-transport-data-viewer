const AADT_SERVICE =
  'https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CHhighway/Traffic_AADT/FeatureServer/0/query';

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

let allFeatures = [];

function num(value) {
  const cleaned = String(value ?? '').replace(/,/g, '').trim();
  const parsed = Number(cleaned);
  return Number.isFinite(parsed) ? parsed : 0;
}

function maxAadt(properties) {
  return Math.max(num(properties.BACK_AADT), num(properties.AHEAD_AADT));
}

function fmt(value) {
  const n = num(value);
  return n ? n.toLocaleString('en-US') : '—';
}

async function fetchAadtPage(offset) {
  const params = new URLSearchParams({
    where: '1=1',
    outFields: '*',
    returnGeometry: 'true',
    outSR: '4326',
    f: 'geojson',
    resultOffset: String(offset),
    resultRecordCount: '2000'
  });

  const response = await fetch(`${AADT_SERVICE}?${params.toString()}`);
  if (!response.ok) throw new Error(`Caltrans request failed: ${response.status}`);
  return response.json();
}

async function loadAllAadt() {
  try {
    const response = await fetch('data/aadt-2024.geojson', { cache: 'no-store' });
    if (!response.ok) throw new Error(`2024 local dataset failed: ${response.status}`);
    const data = await response.json();
    return {
      features: data.features || [],
      year: 2024,
      source: 'official 2024 Caltrans census workbook'
    };
  } catch (error) {
    console.warn('Falling back to live Caltrans GIS layer:', error);
    const features = [];
    let offset = 0;

    while (true) {
      const page = await fetchAadtPage(offset);
      const batch = page.features || [];
      features.push(...batch);
      if (batch.length < 2000) break;
      offset += batch.length;
    }

    return {
      features: features.map(feature => ({
        ...feature,
        properties: {
          ...feature.properties,
          MAX_AADT: maxAadt(feature.properties),
          YEAR: 2023,
          MATCH_METHOD: 'live_gis_fallback'
        }
      })),
      year: 2023,
      source: 'live Caltrans GIS fallback'
    };
  }
}

function updateFilter() {
  if (!map.getLayer('aadt-points')) return;

  const route = document.getElementById('route').value;
  const minimum = Number(document.getElementById('aadt').value);

  const filters = [['>=', ['get', 'MAX_AADT'], minimum]];
  if (route !== 'all') {
    filters.push(['==', ['get', 'RTE'], route]);
  }

  map.setFilter('aadt-points', ['all', ...filters]);

  const visible = allFeatures.filter(f => {
    const routeOk = route === 'all' || String(f.properties.RTE) === route;
    return routeOk && f.properties.MAX_AADT >= minimum;
  }).length;

  document.getElementById('aadt-value').textContent = minimum.toLocaleString('en-US');
  document.getElementById('visible-count').textContent =
    `${visible.toLocaleString('en-US')} count locations match the filter`;
}

function populateRoutes() {
  const routeSelect = document.getElementById('route');
  const routes = [...new Set(allFeatures.map(f => String(f.properties.RTE || '').trim()))]
    .filter(Boolean)
    .sort((a, b) => Number(a) - Number(b));

  routeSelect.innerHTML =
    '<option value="all">All routes</option>' +
    routes.map(route => `<option value="${route}">Route ${Number(route)}</option>`).join('');

  routeSelect.disabled = false;
}

function popupHtml(p) {
  const route = Number(p.RTE);
  const description = p.DESCRIPTION || 'Caltrans traffic count location';

  const matchLabel =
    p.MATCH_METHOD === 'exact' ? 'Exact postmile' :
    p.MATCH_METHOD === 'nearest_postmile' ? 'Nearest postmile ≤0.03 mi' :
    p.MATCH_METHOD === 'live_gis_fallback' ? 'Live GIS fallback' : '—';

  return `
    <div class="popup-title">Route ${route}: ${description}</div>
    <div class="popup-grid">
      <span>Data year</span><strong>${p.YEAR || '—'}</strong>
      <span>County</span><strong>${p.CNTY || '—'}</strong>
      <span>Postmile</span><strong>${[p.PM_PFX, p.PM, p.PM_SFX].filter(Boolean).join('') || '—'}</strong>
      <span>Back AADT</span><strong>${fmt(p.BACK_AADT)}</strong>
      <span>Ahead AADT</span><strong>${fmt(p.AHEAD_AADT)}</strong>
      <span>Back peak hour</span><strong>${fmt(p.BACK_PEAK_HOUR)}</strong>
      <span>Ahead peak hour</span><strong>${fmt(p.AHEAD_PEAK_HOUR)}</strong>
      <span>Location match</span><strong>${matchLabel}</strong>
    </div>
  `;
}

map.on('load', async () => {
  map.fitBounds(
    [[-124.48, 32.52], [-114.13, 42.01]],
    { padding: 36, duration: 0 }
  );

  const status = document.getElementById('status-message');

  try {
    const dataset = await loadAllAadt();
    allFeatures = dataset.features;

    map.addSource('aadt', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: allFeatures }
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

    populateRoutes();
    document.getElementById('aadt').disabled = false;
    updateFilter();

    status.textContent =
      `${allFeatures.length.toLocaleString('en-US')} Caltrans ${dataset.year} AADT count locations loaded from the ${dataset.source}.`;

    map.on('click', 'aadt-points', e => {
      const feature = e.features && e.features[0];
      if (!feature) return;
      new maplibregl.Popup()
        .setLngLat(feature.geometry.coordinates)
        .setHTML(popupHtml(feature.properties))
        .addTo(map);
    });

    map.on('mouseenter', 'aadt-points', () => {
      map.getCanvas().style.cursor = 'pointer';
    });

    map.on('mouseleave', 'aadt-points', () => {
      map.getCanvas().style.cursor = '';
    });
  } catch (error) {
    console.error(error);
    status.textContent =
      'The Caltrans traffic layer could not be loaded. The base map is still available.';
  }
});

document.getElementById('route').addEventListener('change', updateFilter);
document.getElementById('aadt').addEventListener('input', updateFilter);
document.getElementById('aadt-layer').addEventListener('change', event => {
  if (!map.getLayer('aadt-points')) return;
  map.setLayoutProperty(
    'aadt-points',
    'visibility',
    event.target.checked ? 'visible' : 'none'
  );
});
