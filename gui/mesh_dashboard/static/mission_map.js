(function () {
  "use strict";
  const bounds = { minLat: 32.5, maxLat: 32.95, minLon: -117.65, maxLon: -117.05 };
  let polygons = [];
  let ready = false;
  const project = ([lat, lon]) => ({
    x: (lon - bounds.minLon) / (bounds.maxLon - bounds.minLon) * 1000,
    y: (bounds.maxLat - lat) / (bounds.maxLat - bounds.minLat) * 700,
  });
  const coordinate = ({ x, y }) => [
    bounds.maxLat - y / 700 * (bounds.maxLat - bounds.minLat),
    bounds.minLon + x / 1000 * (bounds.maxLon - bounds.minLon),
  ];

  function inRing(point, ring) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const a = ring[i], b = ring[j];
      if ((a.y > point.y) !== (b.y > point.y) &&
          point.x < (b.x - a.x) * (point.y - a.y) / (b.y - a.y) + a.x) inside = !inside;
    }
    return inside;
  }

  function isLand(point) {
    return polygons.some((rings) => inRing(point, rings[0]) &&
      !rings.slice(1).some((ring) => inRing(point, ring)));
  }

  function waterSegment(a, b) {
    const steps = Math.max(1, Math.ceil(Math.hypot(b.x - a.x, b.y - a.y)));
    for (let i = 0; i <= steps; i++) {
      const t = i / steps;
      if (isLand({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t })) return false;
    }
    return true;
  }

  function detour(start, finish) {
    if (!ready) throw new Error("Coastline is not loaded.");
    if (isLand(start) || isLand(finish)) throw new Error("Route endpoints must be on water.");
    if (waterSegment(start, finish)) return [finish];
    // A small eight-connected grid is sufficient for this coarse demo coastline.
    const cols = 101, rows = 71, step = 10;
    const point = (key) => ({ x: key % cols * step, y: Math.floor(key / cols) * step });
    function nearest(p) {
      let best = null, distance = Infinity;
      for (let key = 0; key < cols * rows; key++) {
        const q = point(key), d = Math.hypot(p.x - q.x, p.y - q.y);
        if (d < distance && !isLand(q) && waterSegment(p, q)) { best = key; distance = d; }
      }
      if (best === null) throw new Error("No water connection to the planning grid.");
      return best;
    }
    const first = nearest(start), last = nearest(finish);
    const open = new Set([first]), closed = new Set(), cost = new Map([[first, 0]]), parent = new Map();
    while (open.size) {
      let current = null, score = Infinity;
      for (const key of open) {
        const q = point(key), f = cost.get(key) + Math.hypot(q.x - point(last).x, q.y - point(last).y);
        if (f < score) { current = key; score = f; }
      }
      if (current === last) {
        const path = [finish];
        for (let key = last; key !== undefined; key = parent.get(key)) path.unshift(point(key));
        path.unshift(start);
        const simplified = [];
        for (let i = 0; i < path.length - 1;) {
          let next = path.length - 1;
          while (next > i + 1 && !waterSegment(path[i], path[next])) next--;
          simplified.push(path[next]); i = next;
        }
        return simplified;
      }
      open.delete(current); closed.add(current);
      const cx = current % cols, cy = Math.floor(current / cols);
      for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
        const x = cx + dx, y = cy + dy, key = y * cols + x;
        if ((!dx && !dy) || x < 0 || y < 0 || x >= cols || y >= rows || closed.has(key)) continue;
        if (!waterSegment(point(current), point(key))) continue;
        const distance = cost.get(current) + Math.hypot(dx, dy) * step;
        if (distance < (cost.get(key) ?? Infinity)) {
          cost.set(key, distance); parent.set(key, current); open.add(key);
        }
      }
    }
    throw new Error("No water-only detour was found inside this map.");
  }

  async function load() {
    const response = await fetch("san-diego-land.geojson");
    if (!response.ok) throw new Error(`Coastline unavailable (HTTP ${response.status}).`);
    const data = await response.json();
    polygons = data.features.map((feature) =>
      feature.geometry.coordinates.map((ring) => ring.map(([lon, lat]) => project([lat, lon]))));
    ready = true;
  }

  function landPath() {
    return polygons.map((rings) => rings.map((ring) =>
      `M${ring.map((p) => `${p.x},${p.y}`).join(" L")} Z`).join(" ")).join(" ");
  }

  window.MissionMap = { load, project, coordinate, isLand, waterSegment, detour, landPath, bounds };
})();
