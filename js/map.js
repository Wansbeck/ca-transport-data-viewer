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
let contextLoaded = false;

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
      : method === 'exact_pm_route'
        ? 'Exact postmile-route geometry'
        : method === 'route_county_fallback'
          ? 'Route/county geometry fallback'
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
    [...aadtFeatures, ...truckFeatures, ...segmentFeatures]
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

  if (map.getLayer('traffic-segments')) {
    const filters = [['>=', ['get', 'AADT'], minAadt]];
    if (route !== 'all') filters.push(['==', ['get', 'RTE'], route]);
    map.setFilter('traffic-segments', ['all', ...filters]);
  }

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

  const visibleSegments = segmentFeatures.filter(f => {
    const routeOk = route === 'all' || String(f.properties.RTE) === route;
    return routeOk && num(f.properties.AADT) >= minAadt;
  }).length;

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
    `${visibleSegments.toLocaleString('en-US')} traffic segments; ${visibleAadt.toLocaleString('en-US')} AADT points; ${visibleTruck.toLocaleString('en-US')} truck points match the filters`;
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

function segmentPopup(p) {
  return `
    <div class="popup-title">Route ${Number(p.RTE)} derived traffic segment</div>
    <div class="popup-grid">
      <span>Data year</span><strong>${p.YEAR || '—'}</strong>
      <span>County</span><strong>${p.CNTY || '—'}</strong>
      <span>Postmile range</span><strong>${p.START_PM ?? '—'}–${p.END_PM ?? '—'}</strong>
      <span>Estimated AADT</span><strong>${fmt(p.AADT)}</strong>
      <span>From</span><strong>${p.START_DESC || '—'}</strong>
      <span>To</span><strong>${p.END_DESC || '—'}</strong>
      <span>Geometry match</span><strong>${matchLabel(p.MATCH_METHOD)}</strong>
    </div>
    <div class="source-note" style="margin:10px 0 0 0">
      Derived analytical segment, not an official Caltrans segment-level AADT record.
    </div>
  `;
}

function contextPopup(p) {
  return `
    <div class="popup-title">Census tract ${p.NAME || p.GEOID || ''}</div>
    <div class="popup-grid">
      <span>ACS vintage</span><strong>2024 5-year</strong>
      <span>Median household income</span><strong>${p.MEDIAN_HH_INCOME == null ? '—' : '
  map.on('click', layerId, e => {
    const feature = e.features && e.features[0];
    if (!feature) return;
    new maplibregl.Popup()
      .setLngLat(e.lngLat)
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
    const [aadtData, truckData, segmentData] = await Promise.all([
      loadGeoJson('data/aadt-2024.geojson'),
      loadGeoJson('data/truck-2024.geojson'),
      loadGeoJson('data/aadt-2024-segments.geojson')
    ]);

    aadtFeatures = aadtData.features || [];
    truckFeatures = truckData.features || [];
    segmentFeatures = segmentData.features || [];

    map.addSource('traffic-segments', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: segmentFeatures }
    });

    map.addLayer({
      id: 'traffic-segments',
      type: 'line',
      source: 'traffic-segments',
      paint: {
        'line-color': [
          'step', ['get', 'AADT'],
          '#69b3a2',
          25000, '#f0c05a',
          75000, '#e8824f',
          150000, '#b94040'
        ],
        'line-width': [
          'interpolate', ['linear'], ['zoom'],
          4, 2.0,
          8, 4.0,
          12, 7.0
        ],
        'line-opacity': 0.88
      }
    });

    map.addSource('aadt', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: aadtFeatures }
    });

    map.addLayer({
      id: 'aadt-points',
      type: 'circle',
      source: 'aadt',
      layout: { visibility: 'none' },
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
        'circle-opacity': 0.82,
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 0.8
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

    bindLayerPopup('traffic-segments', segmentPopup);
    bindLayerPopup('aadt-points', aadtPopup);
    bindLayerPopup('truck-points', truckPopup);

    status.textContent =
      `${segmentFeatures.length.toLocaleString('en-US')} derived traffic segments, ${aadtFeatures.length.toLocaleString('en-US')} 2024 AADT locations, and ${truckFeatures.length.toLocaleString('en-US')} 2024 truck locations loaded.`;
  } catch (error) {
    console.error(error);
    status.textContent = 'The Caltrans traffic data could not be loaded. The base map is still available.';
  }
});

document.getElementById('route').addEventListener('change', updateFilters);
document.getElementById('aadt').addEventListener('input', updateFilters);
document.getElementById('truck-pct').addEventListener('input', updateFilters);

document.getElementById('segment-layer').addEventListener('change', event => {
  if (!map.getLayer('traffic-segments')) return;
  map.setLayoutProperty('traffic-segments', 'visibility', event.target.checked ? 'visible' : 'none');
});

document.getElementById('aadt-layer').addEventListener('change', event => {
  if (!map.getLayer('aadt-points')) return;
  map.setLayoutProperty('aadt-points', 'visibility', event.target.checked ? 'visible' : 'none');
});

document.getElementById('truck-layer').addEventListener('change', event => {
  if (!map.getLayer('truck-points')) return;
  map.setLayoutProperty('truck-points', 'visibility', event.target.checked ? 'visible' : 'none');
});
 + Number(p.MEDIAN_HH_INCOME).toLocaleString('en-US')}</strong>
      <span>Income MOE</span><strong>${p.MEDIAN_HH_INCOME_MOE == null ? '—' : '±
  map.on('click', layerId, e => {
    const feature = e.features && e.features[0];
    if (!feature) return;
    new maplibregl.Popup()
      .setLngLat(e.lngLat)
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
    const [aadtData, truckData, segmentData] = await Promise.all([
      loadGeoJson('data/aadt-2024.geojson'),
      loadGeoJson('data/truck-2024.geojson'),
      loadGeoJson('data/aadt-2024-segments.geojson')
    ]);

    aadtFeatures = aadtData.features || [];
    truckFeatures = truckData.features || [];
    segmentFeatures = segmentData.features || [];

    map.addSource('traffic-segments', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: segmentFeatures }
    });

    map.addLayer({
      id: 'traffic-segments',
      type: 'line',
      source: 'traffic-segments',
      paint: {
        'line-color': [
          'step', ['get', 'AADT'],
          '#69b3a2',
          25000, '#f0c05a',
          75000, '#e8824f',
          150000, '#b94040'
        ],
        'line-width': [
          'interpolate', ['linear'], ['zoom'],
          4, 2.0,
          8, 4.0,
          12, 7.0
        ],
        'line-opacity': 0.88
      }
    });

    map.addSource('aadt', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: aadtFeatures }
    });

    map.addLayer({
      id: 'aadt-points',
      type: 'circle',
      source: 'aadt',
      layout: { visibility: 'none' },
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
        'circle-opacity': 0.82,
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 0.8
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

    bindLayerPopup('traffic-segments', segmentPopup);
    bindLayerPopup('aadt-points', aadtPopup);
    bindLayerPopup('truck-points', truckPopup);

    status.textContent =
      `${segmentFeatures.length.toLocaleString('en-US')} derived traffic segments, ${aadtFeatures.length.toLocaleString('en-US')} 2024 AADT locations, and ${truckFeatures.length.toLocaleString('en-US')} 2024 truck locations loaded.`;
  } catch (error) {
    console.error(error);
    status.textContent = 'The Caltrans traffic data could not be loaded. The base map is still available.';
  }
});

document.getElementById('route').addEventListener('change', updateFilters);
document.getElementById('aadt').addEventListener('input', updateFilters);
document.getElementById('truck-pct').addEventListener('input', updateFilters);

document.getElementById('segment-layer').addEventListener('change', event => {
  if (!map.getLayer('traffic-segments')) return;
  map.setLayoutProperty('traffic-segments', 'visibility', event.target.checked ? 'visible' : 'none');
});

document.getElementById('aadt-layer').addEventListener('change', event => {
  if (!map.getLayer('aadt-points')) return;
  map.setLayoutProperty('aadt-points', 'visibility', event.target.checked ? 'visible' : 'none');
});

document.getElementById('truck-layer').addEventListener('change', event => {
  if (!map.getLayer('truck-points')) return;
  map.setLayoutProperty('truck-points', 'visibility', event.target.checked ? 'visible' : 'none');
});
 + Number(p.MEDIAN_HH_INCOME_MOE).toLocaleString('en-US')}</strong>
      <span>Population</span><strong>${fmt(p.POPULATION)}</strong>
      <span>Population density</span><strong>${p.POP_DENSITY_SQMI == null ? '—' : Number(p.POP_DENSITY_SQMI).toLocaleString('en-US') + '/sq mi'}</strong>
    </div>
    <div class="source-note" style="margin:10px 0 0 0">
      American Community Survey estimate; margins of error apply.
    </div>
  `;
}

function incomeColorExpression() {
  return [
    'case',
    ['step', ['coalesce', ['get', 'MEDIAN_HH_INCOME'], -1],
      '#d9d9d9',
      0, '#f1eef6',
      50000, '#d7b5d8',
      75000, '#df65b0',
      100000, '#ce1256',
      150000, '#980043',
      200000, '#67001f'
    ]
  ];
}

function densityColorExpression() {
  return [
    'case',
    ['step', ['coalesce', ['get', 'POP_DENSITY_SQMI'], -1],
      '#d9d9d9',
      0, '#ffffcc',
      100, '#c2e699',
      500, '#78c679',
      2000, '#31a354',
      5000, '#006837'
    ]
  ];
}

function updateContextLegend() {
  const metric = document.getElementById('context-metric').value;
  const title = document.getElementById('context-legend-title');
  const rows = document.getElementById('context-legend-rows');

  if (metric === 'density') {
    title.textContent = 'Population density';
    rows.innerHTML = `
      <div><span class="swatch density-1"></span> Under 100/sq mi</div>
      <div><span class="swatch density-2"></span> 100–500/sq mi</div>
      <div><span class="swatch density-3"></span> 500–2,000/sq mi</div>
      <div><span class="swatch density-4"></span> 2,000–5,000/sq mi</div>
      <div><span class="swatch density-5"></span> 5,000+/sq mi</div>
    `;
  } else {
    title.textContent = 'Median household income';
    rows.innerHTML = `
      <div><span class="swatch income-1"></span> Under $50k</div>
      <div><span class="swatch income-2"></span> $50k–$75k</div>
      <div><span class="swatch income-3"></span> $75k–$100k</div>
      <div><span class="swatch income-4"></span> $100k–$150k</div>
      <div><span class="swatch income-5"></span> $150k–$200k</div>
      <div><span class="swatch income-6"></span> $200k+</div>
    `;
  }

  if (map.getLayer('acs-context-fill')) {
    map.setPaintProperty(
      'acs-context-fill',
      'fill-color',
      metric === 'density' ? densityColorExpression() : incomeColorExpression()
    );
  }
}

async function ensureContextLayer() {
  if (contextLoaded) return;

  const data = await loadGeoJson('data/acs-2024-tract-context.geojson');

  map.addSource('acs-context', {
    type: 'geojson',
    data
  });

  map.addLayer({
    id: 'acs-context-fill',
    type: 'fill',
    source: 'acs-context',
    layout: { visibility: 'none' },
    paint: {
      'fill-color': incomeColorExpression(),
      'fill-opacity': 0.42
    }
  }, 'traffic-segments');

  map.addLayer({
    id: 'acs-context-outline',
    type: 'line',
    source: 'acs-context',
    layout: { visibility: 'none' },
    paint: {
      'line-color': '#ffffff',
      'line-opacity': 0.28,
      'line-width': 0.5
    }
  }, 'traffic-segments');

  map.on('click', 'acs-context-fill', e => {
    const blocking = map.queryRenderedFeatures(e.point, {
      layers: ['traffic-segments', 'aadt-points', 'truck-points'].filter(id => map.getLayer(id))
    });
    if (blocking.length) return;

    const feature = e.features && e.features[0];
    if (!feature) return;

    new maplibregl.Popup()
      .setLngLat(e.lngLat)
      .setHTML(contextPopup(feature.properties))
      .addTo(map);
  });

  contextLoaded = true;
}

function bindLayerPopup(layerId, htmlFn) {
  map.on('click', layerId, e => {
    const feature = e.features && e.features[0];
    if (!feature) return;
    new maplibregl.Popup()
      .setLngLat(e.lngLat)
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
    const [aadtData, truckData, segmentData] = await Promise.all([
      loadGeoJson('data/aadt-2024.geojson'),
      loadGeoJson('data/truck-2024.geojson'),
      loadGeoJson('data/aadt-2024-segments.geojson')
    ]);

    aadtFeatures = aadtData.features || [];
    truckFeatures = truckData.features || [];
    segmentFeatures = segmentData.features || [];

    map.addSource('traffic-segments', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: segmentFeatures }
    });

    map.addLayer({
      id: 'traffic-segments',
      type: 'line',
      source: 'traffic-segments',
      paint: {
        'line-color': [
          'step', ['get', 'AADT'],
          '#69b3a2',
          25000, '#f0c05a',
          75000, '#e8824f',
          150000, '#b94040'
        ],
        'line-width': [
          'interpolate', ['linear'], ['zoom'],
          4, 2.0,
          8, 4.0,
          12, 7.0
        ],
        'line-opacity': 0.88
      }
    });

    map.addSource('aadt', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: aadtFeatures }
    });

    map.addLayer({
      id: 'aadt-points',
      type: 'circle',
      source: 'aadt',
      layout: { visibility: 'none' },
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
        'circle-opacity': 0.82,
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 0.8
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

    bindLayerPopup('traffic-segments', segmentPopup);
    bindLayerPopup('aadt-points', aadtPopup);
    bindLayerPopup('truck-points', truckPopup);

    status.textContent =
      `${segmentFeatures.length.toLocaleString('en-US')} derived traffic segments, ${aadtFeatures.length.toLocaleString('en-US')} 2024 AADT locations, and ${truckFeatures.length.toLocaleString('en-US')} 2024 truck locations loaded.`;
  } catch (error) {
    console.error(error);
    status.textContent = 'The Caltrans traffic data could not be loaded. The base map is still available.';
  }
});

document.getElementById('route').addEventListener('change', updateFilters);
document.getElementById('aadt').addEventListener('input', updateFilters);
document.getElementById('truck-pct').addEventListener('input', updateFilters);

document.getElementById('segment-layer').addEventListener('change', event => {
  if (!map.getLayer('traffic-segments')) return;
  map.setLayoutProperty('traffic-segments', 'visibility', event.target.checked ? 'visible' : 'none');
});

document.getElementById('aadt-layer').addEventListener('change', event => {
  if (!map.getLayer('aadt-points')) return;
  map.setLayoutProperty('aadt-points', 'visibility', event.target.checked ? 'visible' : 'none');
});

document.getElementById('truck-layer').addEventListener('change', event => {
  if (!map.getLayer('truck-points')) return;
  map.setLayoutProperty('truck-points', 'visibility', event.target.checked ? 'visible' : 'none');
});


document.getElementById('context-layer').addEventListener('change', async event => {
  const status = document.getElementById('status-message');
  try {
    if (event.target.checked) {
      status.textContent = 'Loading 2024 ACS tract context…';
      await ensureContextLayer();
      map.setLayoutProperty('acs-context-fill', 'visibility', 'visible');
      map.setLayoutProperty('acs-context-outline', 'visibility', 'visible');
      updateContextLegend();
      status.textContent = `${segmentFeatures.length.toLocaleString('en-US')} derived traffic segments, ${aadtFeatures.length.toLocaleString('en-US')} AADT points, ${truckFeatures.length.toLocaleString('en-US')} truck points, and 2024 ACS tract context available.`;
    } else if (contextLoaded) {
      map.setLayoutProperty('acs-context-fill', 'visibility', 'none');
      map.setLayoutProperty('acs-context-outline', 'visibility', 'none');
    }
  } catch (error) {
    console.error(error);
    event.target.checked = false;
    status.textContent = 'The ACS context layer could not be loaded.';
  }
});

document.getElementById('context-metric').addEventListener('change', updateContextLegend);
