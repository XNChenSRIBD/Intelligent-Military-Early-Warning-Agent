# Local map assets

Retrieved 2026-09-13. These assets render without external map tiles, API keys or a package install.

## Leaflet 1.9.4

- Official stable release: https://leafletjs.com/download.html
- Distribution files: https://unpkg.com/leaflet@1.9.4/dist/
- Upstream license: https://github.com/Leaflet/Leaflet/blob/v1.9.4/LICENSE
- License: BSD-2-Clause; the complete notice is retained in `vendor/leaflet/LICENSE`.
- `vendor/leaflet/leaflet.js`, `leaflet.css` and the five referenced PNG images are unmodified distribution files.

Load `/static/vendor/leaflet/leaflet.css` and then `/static/vendor/leaflet/leaflet.js`; the browser API is `window.L`.

## Natural Earth 1:50m admin-0 countries

- Source: https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_50m_admin_0_countries.geojson
- Download: https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_0_countries.geojson
- Terms: https://www.naturalearthdata.com/about/terms-of-use/
- License: public domain. Attribution text: “Made with Natural Earth.”

`replay-world.geojson` contains all 242 source features in longitude/latitude GeoJSON coordinates. Properties retain `name` and `iso_a3`. All polygons, rings and vertices are retained; coordinates are rounded to four decimal places, retaining original precision for any ring that would collapse. No countries, islands or borders are drawn or invented by this project.

Load `/static/replay-world.geojson` with `L.geoJSON`. This is a small-scale reference basemap, not imagery or a street map; country outlines follow the upstream dataset.
