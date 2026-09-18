const map = new maplibregl.Map({
  container: 'map',
  style: {
    version: 8,
    sources: {
      osm: {
        type: 'raster',
        tiles: [
          'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
        ],
        tileSize: 256,
        attribution: '© OpenStreetMap contributors'
      }
    },
    layers: [
      {
        id: 'osm',
        type: 'raster',
        source: 'osm'
      }
    ]
  },
  center: [-119.5, 37.2],
  zoom: 5.2,
  minZoom: 4,
  maxZoom: 18
});

map.addControl(new maplibregl.NavigationControl(), 'top-right');
map.addControl(new maplibregl.ScaleControl({
  maxWidth: 120,
  unit: 'imperial'
}));

map.on('load', () => {
  map.fitBounds(
    [
      [-124.48, 32.52],
      [-114.13, 42.01]
    ],
    {
      padding: 36,
      duration: 0
    }
  );
});
