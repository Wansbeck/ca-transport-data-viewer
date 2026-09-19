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
let segmentFeatures = [];
let serviceFeatures = [];
let opportunityFeatures = [];
let contextLoaded = false;
let serviceLoaded = false;
let opportunityLoaded = false;
let srraLoaded = false;
let urbanLoaded = false;

function num(value) {
  const parsed = Number(String(value ?? '').replace(/,/g, '').trim());
  return Number.isFinite(parsed) ? parsed : 0;
}
function fmt(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toLocaleString('en-US') : '—';
}
function fmtPct(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(1) + '%' : '—';
}
function money(value) {
  const n = Number(value);
  return Number.isFinite(n) ? '$' + n.toLocaleString('en-US') : '—';
}
function matchLabel(method) {
  return method === 'exact' ? 'Exact postmile'
    : method === 'nearest_postmile' ? 'Nearest postmile ≤0.03 mi'
    : method === 'exact_pm_route' ? 'Exact postmile-route geometry'
    : method === 'route_county_fallback' ? 'Route/county geometry fallback'
    : '—';
}

async function loadGeoJson(path) {
  const response = await fetch(path, { cache: 'no-store' });
  if (!response.ok) throw new Error('Failed to load ' + path + ': ' + response.status);
  return response.json();
}
async function dataFileExists(path) {
  try {
    const response = await fetch(path, { method: 'HEAD', cache: 'no-store' });
    return response.ok;
  } catch (_) {
    return false;
  }
}
async function refreshOptionalLayerAvailability() {
  const ids = [
    ['service-layer', 'data/services-osm.geojson'],
    ['opportunity-layer', 'data/opportunity-2024.geojson'],
    ['srra-layer', 'data/caltrans-srra.geojson'],
    ['urban-layer', 'data/urban-areas-2020.geojson']
  ];
  const ready = await Promise.all(ids.map(x => dataFileExists(x[1])));
  ids.forEach((item, i) => {
    const el = document.getElementById(item[0]);
    if (!el) return;
    el.disabled = !ready[i];
    el.title = ready[i] ? '' : 'Dataset is still being built.';
  });
}

function populateRoutes() {
  const routeSelect = document.getElementById('route');
  const routes = [...new Set(
    [...aadtFeatures, ...truckFeatures, ...segmentFeatures]
      .map(f => String(f.properties.RTE || '').trim())
      .filter(Boolean)
  )].sort((a, b) => Number(a) - Number(b));
  routeSelect.innerHTML = '<option value="all">All routes</option>' +
    routes.map(route => '<option value="' + route + '">Route ' + Number(route) + '</option>').join('');
  routeSelect.disabled = false;
}

function updateFilters() {
  const route = document.getElementById('route').value;
  const minAadt = Number(document.getElementById('aadt').value);
  const minTruckPct = Number(document.getElementById('truck-pct').value);
  const minOpportunity = Number(document.getElementById('opportunity-score').value);

  function routeFilter(field, thresholdField, threshold) {
    const filters = [['>=', ['coalesce', ['get', thresholdField], 0], threshold]];
    if (route !== 'all') filters.push(['==', ['get', field], route]);
    return ['all', ...filters];
  }

  if (map.getLayer('traffic-segments')) map.setFilter('traffic-segments', routeFilter('RTE', 'AADT', minAadt));
  if (map.getLayer('aadt-points')) map.setFilter('aadt-points', routeFilter('RTE', 'MAX_AADT', minAadt));
  if (map.getLayer('truck-points')) map.setFilter('truck-points', routeFilter('RTE', 'TRUCK_PERCENT', minTruckPct));

  if (map.getLayer('opportunity-segments')) {
    const filters = [
      ['>=', ['get', 'OPPORTUNITY_SCORE'], minOpportunity],
      ['!=', ['get', 'URBAN_EXCLUDED'], true]
    ];
    if (route !== 'all') filters.push(['==', ['get', 'RTE'], route]);
    map.setFilter('opportunity-segments', ['all', ...filters]);
  }

  const visibleSegments = segmentFeatures.filter(f =>
    (route === 'all' || String(f.properties.RTE) === route) && num(f.properties.AADT) >= minAadt
  ).length;
  const visibleAadt = aadtFeatures.filter(f =>
    (route === 'all' || String(f.properties.RTE) === route) && num(f.properties.MAX_AADT) >= minAadt
  ).length;
  const visibleTruck = truckFeatures.filter(f =>
    (route === 'all' || String(f.properties.RTE) === route) && num(f.properties.TRUCK_PERCENT) >= minTruckPct
  ).length;

  document.getElementById('aadt-value').textContent = minAadt.toLocaleString('en-US');
  document.getElementById('truck-pct-value').textContent = minTruckPct.toLocaleString('en-US');
  document.getElementById('opportunity-score-value').textContent = minOpportunity;
  document.getElementById('visible-count').textContent =
    visibleSegments.toLocaleString('en-US') + ' traffic segments; ' +
    visibleAadt.toLocaleString('en-US') + ' AADT points; ' +
    visibleTruck.toLocaleString('en-US') + ' truck points match the filters';
}

function aadtPopup(p) {
  return '<div class="popup-title">Route ' + Number(p.RTE) + ': ' + (p.DESCRIPTION || 'Caltrans traffic count location') + '</div>' +
    '<div class="popup-grid">' +
    '<span>Data year</span><strong>' + (p.YEAR || '—') + '</strong>' +
    '<span>County</span><strong>' + (p.CNTY || '—') + '</strong>' +
    '<span>Postmile</span><strong>' + (p.PM ?? '—') + '</strong>' +
    '<span>Back AADT</span><strong>' + fmt(p.BACK_AADT) + '</strong>' +
    '<span>Ahead AADT</span><strong>' + fmt(p.AHEAD_AADT) + '</strong>' +
    '<span>Location match</span><strong>' + matchLabel(p.MATCH_METHOD) + '</strong></div>';
}
function truckPopup(p) {
  return '<div class="popup-title">Route ' + Number(p.RTE) + ': ' + (p.DESCRIPTION || 'Caltrans truck count location') + '</div>' +
    '<div class="popup-grid">' +
    '<span>Total AADT</span><strong>' + fmt(p.TOTAL_AADT) + '</strong>' +
    '<span>Truck AADT</span><strong>' + fmt(p.TRUCK_AADT) + '</strong>' +
    '<span>Truck share</span><strong>' + fmtPct(p.TRUCK_PERCENT) + '</strong>' +
    '<span>County</span><strong>' + (p.CNTY || '—') + '</strong>' +
    '<span>Postmile</span><strong>' + (p.PM ?? '—') + '</strong></div>';
}
function segmentPopup(p) {
  return '<div class="popup-title">Route ' + Number(p.RTE) + ' derived traffic segment</div>' +
    '<div class="popup-grid">' +
    '<span>Estimated AADT</span><strong>' + fmt(p.AADT) + '</strong>' +
    '<span>County</span><strong>' + (p.CNTY || '—') + '</strong>' +
    '<span>Postmile range</span><strong>' + (p.START_PM ?? '—') + '–' + (p.END_PM ?? '—') + '</strong>' +
    '<span>From</span><strong>' + (p.START_DESC || '—') + '</strong>' +
    '<span>To</span><strong>' + (p.END_DESC || '—') + '</strong></div>' +
    '<div class="source-note" style="margin:10px 0 0 0">Derived analytical segment, not an official Caltrans segment-level AADT record.</div>';
}
function contextPopup(p) {
  return '<div class="popup-title">Census tract ' + (p.NAME || p.GEOID || '') + '</div>' +
    '<div class="popup-grid">' +
    '<span>Median household income</span><strong>' + money(p.MEDIAN_HH_INCOME) + '</strong>' +
    '<span>Population</span><strong>' + fmt(p.POPULATION) + '</strong>' +
    '<span>Population density</span><strong>' + (p.POP_DENSITY_SQMI == null ? '—' : fmt(p.POP_DENSITY_SQMI) + '/sq mi') + '</strong></div>';
}

const serviceLabels = {
  fuel: 'Fuel station',
  truck_service: 'Truck-oriented service',
  service_area: 'Service area',
  rest_area: 'OSM rest area',
  ev_charging: 'EV charging',
  food_cluster: 'Food cluster'
};
function servicePopup(p) {
  if (p.CATEGORY === 'food_cluster') {
    return '<div class="popup-title">' + (p.NAME || 'Mapped food cluster') + '</div>' +
      '<div class="popup-grid">' +
      '<span>Restaurants</span><strong>' + fmt(p.RESTAURANTS) + '</strong>' +
      '<span>Fast food</span><strong>' + fmt(p.FAST_FOOD) + '</strong>' +
      '<span>Cafés</span><strong>' + fmt(p.CAFES) + '</strong>' +
      '<span>Competition strength</span><strong>' + (p.COMPETITION_STRENGTH ?? '—') + '</strong>' +
      '<span>Sample venues</span><strong>' + (p.SAMPLE_NAMES || '—') + '</strong></div>';
  }
  return '<div class="popup-title">' + (p.NAME || serviceLabels[p.CATEGORY] || 'Service location') + '</div>' +
    '<div class="popup-grid">' +
    '<span>Category</span><strong>' + (serviceLabels[p.CATEGORY] || p.CATEGORY || '—') + '</strong>' +
    '<span>Brand</span><strong>' + (p.BRAND || '—') + '</strong>' +
    '<span>Operator</span><strong>' + (p.OPERATOR || '—') + '</strong>' +
    '<span>Competition quality</span><strong>' + (p.QUALITY_TIER || '—') + '</strong>' +
    '<span>Competition strength</span><strong>' + (p.COMPETITION_STRENGTH ?? '—') + '</strong></div>' +
    '<div class="source-note" style="margin:10px 0 0 0">OpenStreetMap record; completeness and tagging vary.</div>';
}

function opportunityPopup(p) {
  const urbanText = p.URBAN_EXCLUDED === true || p.URBAN_EXCLUDED === 'true'
    ? 'EXCLUDED: dense major-urban core'
    : String(p.URBAN_STATUS || '—').replaceAll('_', ' ');
  return '<div class="popup-title">Route ' + Number(p.RTE) + ' opportunity: ' + Number(p.OPPORTUNITY_SCORE || 0).toFixed(1) + '/100</div>' +
    '<div class="popup-grid">' +
    '<span>Urban suitability</span><strong>' + urbanText + '</strong>' +
    '<span>Raw AADT</span><strong>' + fmt(p.AADT) + '</strong>' +
    '<span>Modelled long-distance share</span><strong>' + fmtPct(Number(p.LONG_DISTANCE_SHARE || 0) * 100) + '</strong>' +
    '<span>Addressable passing traffic</span><strong>' + fmt(p.ADDRESSABLE_AADT) + '/day</strong>' +
    '<span>Nearest commercial service</span><strong>' + (p.NEAREST_SERVICE_MI == null ? '—' : Number(p.NEAREST_SERVICE_MI).toFixed(1) + ' mi') + '</strong>' +
    '<span>Nearest Caltrans SRRA</span><strong>' + (p.NEAREST_SRRA_MI == null ? '—' : Number(p.NEAREST_SRRA_MI).toFixed(1) + ' mi') + '</strong>' +
    '<span>Nearest interchange</span><strong>' + (p.NEAREST_INTERCHANGE_MI == null ? '—' : Number(p.NEAREST_INTERCHANGE_MI).toFixed(1) + ' mi') + '</strong>' +
    '<span>Median household income</span><strong>' + money(p.MEDIAN_HH_INCOME) + '</strong>' +
    '<span>Truck share</span><strong>' + fmtPct(p.TRUCK_PERCENT_NEARBY) + '</strong>' +
    '<span>Long-distance demand</span><strong>' + Number(p.LONG_DISTANCE_SCORE || 0).toFixed(1) + ' / 25</strong>' +
    '<span>Service-gap score</span><strong>' + Number(p.SERVICE_GAP_SCORE || 0).toFixed(1) + ' / 20</strong>' +
    '<span>Competition score</span><strong>' + Number(p.COMPETITION_SCORE || 0).toFixed(1) + ' / 15</strong>' +
    '<span>Total AADT score</span><strong>' + Number(p.AADT_SCORE || 0).toFixed(1) + ' / 15</strong>' +
    '<span>Accessibility score</span><strong>' + Number(p.ACCESS_SCORE || 0).toFixed(1) + ' / 10</strong>' +
    '<span>Tourism score</span><strong>' + Number(p.TOURISM_SCORE || 0).toFixed(1) + ' / 5</strong>' +
    '<span>Income score</span><strong>' + Number(p.INCOME_SCORE || 0).toFixed(1) + ' / 5</strong>' +
    '<span>Truck score</span><strong>' + Number(p.TRUCK_SCORE || 0).toFixed(1) + ' / 5</strong></div>' +
    '<div class="source-note" style="margin:10px 0 0 0">Model v0.3. Long-distance shares are screening assumptions, not measured trip-purpose data. Dense major-urban cores are categorically excluded.</div>';
}

function srraPopup(p) {
  const name = p.MAP_LABEL_NAME || p.REST_AREA_NAME || p.NAME || p.FACILITY_NAME || 'Caltrans Safety Roadside Rest Area';
  return '<div class="popup-title">' + name + '</div><div class="popup-grid">' +
    '<span>Route</span><strong>' + (p.ROUTE || p.RTE || p.Route || '—') + '</strong>' +
    '<span>County</span><strong>' + (p.COUNTY || p.CNTY || p.County || '—') + '</strong>' +
    '<span>Direction</span><strong>' + (p.DIRECTION || p.Direction || p.DIR || '—') + '</strong>' +
    '<span>Status</span><strong>' + (p.STATUS || p.Status || '—') + '</strong></div>' +
    '<div class="source-note" style="margin:10px 0 0 0">Official Caltrans Safety Roadside Rest Area dataset.</div>';
}
function urbanPopup(p) {
  return '<div class="popup-title">' + (p.NAME || p.BASENAME || '2020 Census Urban Area') + '</div>' +
    '<div class="popup-grid"><span>2020 population</span><strong>' + fmt(p.POP100) + '</strong>' +
    '<span>Model classification</span><strong>' + (p.MAJOR_URBAN_AREA === true || p.MAJOR_URBAN_AREA === 'true' ? 'Major urban area' : 'Other urban area') + '</strong></div>';
}

function bindLayerPopup(layerId, htmlFn) {
  map.on('click', layerId, e => {
    const f = e.features && e.features[0];
    if (!f) return;
    new maplibregl.Popup().setLngLat(e.lngLat).setHTML(htmlFn(f.properties)).addTo(map);
  });
  map.on('mouseenter', layerId, () => { map.getCanvas().style.cursor = 'pointer'; });
  map.on('mouseleave', layerId, () => { map.getCanvas().style.cursor = ''; });
}

function incomeColorExpression() {
  return ['step', ['coalesce', ['get', 'MEDIAN_HH_INCOME'], -1],
    '#d9d9d9', 0, '#f1eef6', 50000, '#d7b5d8', 75000, '#df65b0',
    100000, '#ce1256', 150000, '#980043', 200000, '#67001f'];
}
function densityColorExpression() {
  return ['step', ['coalesce', ['get', 'POP_DENSITY_SQMI'], -1],
    '#d9d9d9', 0, '#ffffcc', 100, '#c2e699', 500, '#78c679',
    2000, '#31a354', 5000, '#006837'];
}
function updateContextLegend() {
  const metric = document.getElementById('context-metric').value;
  if (map.getLayer('acs-context-fill')) {
    map.setPaintProperty('acs-context-fill', 'fill-color', metric === 'density' ? densityColorExpression() : incomeColorExpression());
  }
}

async function ensureContextLayer() {
  if (contextLoaded) return;
  const data = await loadGeoJson('data/acs-2024-tract-context.geojson');
  map.addSource('acs-context', { type: 'geojson', data });
  map.addLayer({
    id: 'acs-context-fill', type: 'fill', source: 'acs-context',
    layout: { visibility: 'none' },
    paint: { 'fill-color': incomeColorExpression(), 'fill-opacity': 0.42 }
  }, 'traffic-segments');
  map.addLayer({
    id: 'acs-context-outline', type: 'line', source: 'acs-context',
    layout: { visibility: 'none' },
    paint: { 'line-color': '#ffffff', 'line-opacity': 0.28, 'line-width': 0.5 }
  }, 'traffic-segments');
  bindLayerPopup('acs-context-fill', contextPopup);
  contextLoaded = true;
}

function selectedServiceCategories() {
  return [...document.querySelectorAll('.service-category:checked')].map(el => el.value);
}
function updateServiceFilter() {
  if (!map.getLayer('service-points')) return;
  const categories = selectedServiceCategories();
  map.setFilter('service-points', categories.length
    ? ['in', ['get', 'CATEGORY'], ['literal', categories]]
    : ['==', ['get', 'CATEGORY'], '__none__']);
}
async function ensureServiceLayer() {
  if (serviceLoaded) return;
  const data = await loadGeoJson('data/services-osm.geojson');
  serviceFeatures = data.features || [];
  map.addSource('services', { type: 'geojson', data });
  map.addLayer({
    id: 'service-points', type: 'circle', source: 'services',
    layout: { visibility: 'none' },
    paint: {
      'circle-radius': ['match', ['get', 'CATEGORY'],
        'food_cluster', ['interpolate', ['linear'], ['coalesce', ['get', 'COUNT'], 3], 3, 4, 10, 7, 30, 11],
        'truck_service', 7, 'service_area', 7, 'rest_area', 6, 'ev_charging', 5, 5],
      'circle-color': ['match', ['get', 'CATEGORY'],
        'fuel', '#8c6d31', 'truck_service', '#54278f', 'service_area', '#756bb1',
        'rest_area', '#31a354', 'ev_charging', '#2b8cbe', 'food_cluster', '#de2d26', '#636363'],
      'circle-opacity': 0.82, 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 0.8
    }
  });
  bindLayerPopup('service-points', servicePopup);
  updateServiceFilter();
  serviceLoaded = true;
}

async function ensureOpportunityLayer() {
  if (opportunityLoaded) return;
  const data = await loadGeoJson('data/opportunity-2024.geojson');
  opportunityFeatures = data.features || [];
  map.addSource('opportunity', { type: 'geojson', data });
  map.addLayer({
    id: 'opportunity-segments', type: 'line', source: 'opportunity',
    layout: { visibility: 'none' },
    paint: {
      'line-color': ['step', ['get', 'OPPORTUNITY_SCORE'],
        '#bdbdbd', 50, '#fed976', 65, '#fd8d3c', 80, '#bd0026'],
      'line-width': ['interpolate', ['linear'], ['zoom'], 4, 4.5, 8, 7.5, 12, 11],
      'line-opacity': 0.92
    }
  });
  bindLayerPopup('opportunity-segments', opportunityPopup);
  document.getElementById('opportunity-score').disabled = false;
  updateFilters();
  opportunityLoaded = true;
}

async function ensureSrraLayer() {
  if (srraLoaded) return;
  const data = await loadGeoJson('data/caltrans-srra.geojson');
  map.addSource('caltrans-srra', { type: 'geojson', data });
  map.addLayer({
    id: 'caltrans-srra-points', type: 'circle', source: 'caltrans-srra',
    layout: { visibility: 'none' },
    paint: {
      'circle-radius': 7, 'circle-color': '#006837',
      'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.5, 'circle-opacity': 0.95
    }
  });
  bindLayerPopup('caltrans-srra-points', srraPopup);
  srraLoaded = true;
}

async function ensureUrbanLayer() {
  if (urbanLoaded) return;
  const data = await loadGeoJson('data/urban-areas-2020.geojson');
  map.addSource('urban-areas', { type: 'geojson', data });
  map.addLayer({
    id: 'urban-areas-fill', type: 'fill', source: 'urban-areas',
    layout: { visibility: 'none' },
    paint: {
      'fill-color': ['case', ['==', ['get', 'MAJOR_URBAN_AREA'], true], '#756bb1', '#9ecae1'],
      'fill-opacity': 0.2
    }
  }, 'traffic-segments');
  map.addLayer({
    id: 'urban-areas-outline', type: 'line', source: 'urban-areas',
    layout: { visibility: 'none' },
    paint: { 'line-color': '#756bb1', 'line-width': 1, 'line-opacity': 0.55 }
  }, 'traffic-segments');
  bindLayerPopup('urban-areas-fill', urbanPopup);
  urbanLoaded = true;
}

map.on('load', async () => {
  map.fitBounds([[-124.48, 32.52], [-114.13, 42.01]], { padding: 36, duration: 0 });
  const status = document.getElementById('status-message');
  try {
    const [aadtData, truckData, segmentData] = await Promise.all([
      loadGeoJson('data/aadt-2024.geojson'),
      loadGeoJson('data/truck-2024.geojson'),
      loadGeoJson('data/aadt-2024-segments.geojson')
    ]);
    aadtFeatures = aadtData.features || [];
    truckFeatures = truckData.features || [];
    segmentFeatures = segmentData.features || [];

    map.addSource('traffic-segments', { type: 'geojson', data: { type: 'FeatureCollection', features: segmentFeatures } });
    map.addLayer({
      id: 'traffic-segments', type: 'line', source: 'traffic-segments',
      paint: {
        'line-color': ['step', ['get', 'AADT'], '#69b3a2', 25000, '#f0c05a', 75000, '#e8824f', 150000, '#b94040'],
        'line-width': ['interpolate', ['linear'], ['zoom'], 4, 2, 8, 4, 12, 7],
        'line-opacity': 0.88
      }
    });
    map.addSource('aadt', { type: 'geojson', data: { type: 'FeatureCollection', features: aadtFeatures } });
    map.addLayer({
      id: 'aadt-points', type: 'circle', source: 'aadt', layout: { visibility: 'none' },
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['get', 'MAX_AADT'], 0, 3, 25000, 4, 75000, 6, 150000, 8, 300000, 11],
        'circle-color': ['step', ['get', 'MAX_AADT'], '#69b3a2', 25000, '#f0c05a', 75000, '#e8824f', 150000, '#b94040'],
        'circle-opacity': 0.82, 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 0.8
      }
    });
    map.addSource('truck', { type: 'geojson', data: { type: 'FeatureCollection', features: truckFeatures } });
    map.addLayer({
      id: 'truck-points', type: 'circle', source: 'truck', layout: { visibility: 'none' },
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['coalesce', ['get', 'TRUCK_AADT'], 0], 0, 3, 1000, 4, 5000, 6, 10000, 8, 25000, 11],
        'circle-color': ['step', ['coalesce', ['get', 'TRUCK_PERCENT'], 0], '#b9c7d8', 5, '#7ba0c7', 10, '#477aa8', 20, '#244c73'],
        'circle-opacity': 0.82, 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 0.8
      }
    });

    populateRoutes();
    document.getElementById('aadt').disabled = false;
    document.getElementById('truck-pct').disabled = false;
    bindLayerPopup('traffic-segments', segmentPopup);
    bindLayerPopup('aadt-points', aadtPopup);
    bindLayerPopup('truck-points', truckPopup);
    updateFilters();
    await refreshOptionalLayerAvailability();
    status.textContent = segmentFeatures.length.toLocaleString('en-US') + ' traffic segments loaded.';
  } catch (error) {
    console.error(error);
    status.textContent = 'The Caltrans traffic data could not be loaded. The base map is still available.';
  }
});

document.getElementById('route').addEventListener('change', updateFilters);
document.getElementById('aadt').addEventListener('input', updateFilters);
document.getElementById('truck-pct').addEventListener('input', updateFilters);
document.getElementById('opportunity-score').addEventListener('input', updateFilters);

document.getElementById('segment-layer').addEventListener('change', e => {
  if (map.getLayer('traffic-segments')) map.setLayoutProperty('traffic-segments', 'visibility', e.target.checked ? 'visible' : 'none');
});
document.getElementById('aadt-layer').addEventListener('change', e => {
  if (map.getLayer('aadt-points')) map.setLayoutProperty('aadt-points', 'visibility', e.target.checked ? 'visible' : 'none');
});
document.getElementById('truck-layer').addEventListener('change', e => {
  if (map.getLayer('truck-points')) map.setLayoutProperty('truck-points', 'visibility', e.target.checked ? 'visible' : 'none');
});

document.getElementById('opportunity-layer').addEventListener('change', async e => {
  const status = document.getElementById('status-message');
  try {
    if (e.target.checked) {
      status.textContent = 'Loading corridor opportunity scores…';
      await ensureOpportunityLayer();

      // Make the opportunity layer visually unambiguous by hiding the
      // ordinary AADT line layer while opportunity scoring is displayed.
      if (map.getLayer('traffic-segments')) {
        map.setLayoutProperty('traffic-segments', 'visibility', 'none');
        const segmentToggle = document.getElementById('segment-layer');
        if (segmentToggle) segmentToggle.checked = false;
      }

      map.setLayoutProperty('opportunity-segments', 'visibility', 'visible');
      updateFilters();

      const minScore = Number(document.getElementById('opportunity-score').value);
      const visible = opportunityFeatures.filter(f =>
        num(f.properties.OPPORTUNITY_SCORE) >= minScore &&
        !(f.properties.URBAN_EXCLUDED === true || f.properties.URBAN_EXCLUDED === 'true')
      ).length;

      status.textContent =
        visible.toLocaleString('en-US') +
        ' non-excluded corridor segments score ' +
        minScore +
        '+. Lower the threshold to see more corridors.';
    } else if (opportunityLoaded) {
      map.setLayoutProperty('opportunity-segments', 'visibility', 'none');
    }
  } catch (error) {
    console.error(error);
    e.target.checked = false;
    status.textContent = 'Opportunity scores are not available yet.';
  }
});

document.getElementById('service-layer').addEventListener('change', async e => {
  const status = document.getElementById('status-message');
  try {
    if (e.target.checked) {
      status.textContent = 'Loading service and competition data…';
      await ensureServiceLayer();
      map.setLayoutProperty('service-points', 'visibility', 'visible');
      updateServiceFilter();
      status.textContent = serviceFeatures.length.toLocaleString('en-US') + ' service / competition features loaded.';
    } else if (serviceLoaded) {
      map.setLayoutProperty('service-points', 'visibility', 'none');
    }
  } catch (error) {
    console.error(error); e.target.checked = false;
    status.textContent = 'Service data is not available yet.';
  }
});
document.querySelectorAll('.service-category').forEach(el => el.addEventListener('change', updateServiceFilter));

document.getElementById('srra-layer').addEventListener('change', async e => {
  const status = document.getElementById('status-message');
  try {
    if (e.target.checked) {
      await ensureSrraLayer();
      map.setLayoutProperty('caltrans-srra-points', 'visibility', 'visible');
      status.textContent = 'Official Caltrans Safety Roadside Rest Areas loaded.';
    } else if (srraLoaded) {
      map.setLayoutProperty('caltrans-srra-points', 'visibility', 'none');
    }
  } catch (error) {
    console.error(error); e.target.checked = false;
    status.textContent = 'Caltrans SRRA data is not available yet.';
  }
});

document.getElementById('urban-layer').addEventListener('change', async e => {
  const status = document.getElementById('status-message');
  try {
    if (e.target.checked) {
      await ensureUrbanLayer();
      map.setLayoutProperty('urban-areas-fill', 'visibility', 'visible');
      map.setLayoutProperty('urban-areas-outline', 'visibility', 'visible');
      status.textContent = '2020 Census urban suitability areas loaded.';
    } else if (urbanLoaded) {
      map.setLayoutProperty('urban-areas-fill', 'visibility', 'none');
      map.setLayoutProperty('urban-areas-outline', 'visibility', 'none');
    }
  } catch (error) {
    console.error(error); e.target.checked = false;
    status.textContent = 'Urban-area data is not available yet.';
  }
});

document.getElementById('context-layer').addEventListener('change', async e => {
  const status = document.getElementById('status-message');
  try {
    if (e.target.checked) {
      await ensureContextLayer();
      map.setLayoutProperty('acs-context-fill', 'visibility', 'visible');
      map.setLayoutProperty('acs-context-outline', 'visibility', 'visible');
      updateContextLegend();
      status.textContent = '2024 ACS tract context loaded.';
    } else if (contextLoaded) {
      map.setLayoutProperty('acs-context-fill', 'visibility', 'none');
      map.setLayoutProperty('acs-context-outline', 'visibility', 'none');
    }
  } catch (error) {
    console.error(error); e.target.checked = false;
    status.textContent = 'ACS context could not be loaded.';
  }
});
document.getElementById('context-metric').addEventListener('change', updateContextLegend);
