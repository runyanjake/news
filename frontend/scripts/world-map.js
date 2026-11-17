// Builds src/report-format/world.svg (the /analytics/ heatmap) from Natural Earth's public-domain country outlines.
// Usage: node scripts/world-map.js <ne_110m_admin_0_countries.geojson>
//   (https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_110m_admin_0_countries.geojson)
import { readFileSync, writeFileSync } from 'node:fs';

const [A1, A2, A3, A4, M] = [1.340264, -0.081106, 0.000893, 0.003796, Math.sqrt(3) / 2];
const SCALE = 1000 / (2 * 2.70646); // Equal Earth spans x = ±2.70646 at unit radius; the map is 1000 wide

/** Equal Earth projection (Šavrič, Patterson, Jenny 2018), y pointing down. */
function project([lon, lat]) {
  const t = Math.asin(M * Math.sin((lat * Math.PI) / 180));
  const t2 = t * t, t6 = t2 * t2 * t2;
  const x = (2 * Math.sqrt(3) * ((lon * Math.PI) / 180) * Math.cos(t)) / (3 * (A1 + 3 * A2 * t2 + t6 * (7 * A3 + 9 * A4 * t2)));
  const y = t * (A1 + A2 * t2 + t6 * (A3 + A4 * t2));
  return [x * SCALE + 500, -y * SCALE];
}

const ring = (coords) => `M${coords.map((c) => project(c).map((v) => v.toFixed(1)).join(',')).join('L')}Z`;
const polygons = (g) => (g.type === 'Polygon' ? [g.coordinates] : g.coordinates);
const escape = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');

const { features } = JSON.parse(readFileSync(process.argv[2], 'utf8'));
let top = Infinity, bottom = -Infinity;
const paths = features
  .filter((f) => f.properties.ISO_A2_EH !== 'AQ') // Antarctica: no readers, lots of space
  .map((f) => {
    const rings = polygons(f.geometry).flat();
    for (const [, y] of rings.flat().map(project)) [top, bottom] = [Math.min(top, y), Math.max(bottom, y)];
    const code = f.properties.ISO_A2_EH;
    const id = /^[A-Z]{2}$/.test(code) ? ` data-cc="${code}"` : '';
    return `<path${id} d="${rings.map(ring).join('')}"><title>${escape(f.properties.NAME)}</title></path>`;
  });

const [y0, h] = [Math.floor(top), Math.ceil(bottom - top)];
writeFileSync(
  new URL('../src/report-format/world.svg', import.meta.url),
  `<svg xmlns="http://www.w3.org/2000/svg" class="world" viewBox="0 ${y0} 1000 ${h}" role="img" aria-label="World map of visitors by country">
${paths.join('\n')}
</svg>
`,
);
