/* MonoraMap: thin wrapper around MapLibre. Data comes from web.data.map_payload (lat,lon pairs). */
(function () {
  const STYLES = {
    light: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
    dark: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
  };
  const STATUS = { ON_TRACK: '#10b981', ATTENTION: '#f59e0b', PROBLEM: '#e11d48', ARRIVED: '#8a8a82' };
  const MODE_SVG = {
    SEA: '<path d="M2 21c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1s1.2 1 2.5 1c2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1"/><path d="M19.38 20A11.6 11.6 0 0 0 21 14l-9-4-9 4c0 2.9.94 5.34 2.81 7.76"/><path d="M19 13V7a2 2 0 0 0-2-2H7a2 2 0 0 0-2 2v6"/><path d="M12 10v4"/><path d="M12 2v3"/>',
    AIR: '<path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z"/>',
    ROAD: '<path d="M14 18V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v11a1 1 0 0 0 1 1h2"/><path d="M15 18H9"/><path d="M19 18h2a1 1 0 0 0 1-1v-3.65a1 1 0 0 0-.22-.624l-3.48-4.35A1 1 0 0 0 17.52 8H14"/><circle cx="17" cy="18" r="2"/><circle cx="7" cy="18" r="2"/>',
  };
  const ll = (p) => [p[1], p[0]];

  // Catmull-Rom spline through the waypoints -> soft curves instead of hard corners
  function smooth(pts, steps = 14) {
    if (pts.length < 3) return pts;
    const out = [];
    for (let i = 0; i < pts.length - 1; i++) {
      const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
      for (let t = 0; t < 1; t += 1 / steps) {
        const t2 = t * t, t3 = t2 * t;
        out.push([0, 1].map((k) => 0.5 * ((2 * p1[k]) + (-p0[k] + p2[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2 + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3)));
      }
    }
    out.push(pts[pts.length - 1]);
    return out;
  }
  // dash sequence that makes the best route look like it is flowing
  const DASH = [[0, 4, 3], [0.5, 4, 2.5], [1, 4, 2], [1.5, 4, 1.5], [2, 4, 1], [2.5, 4, 0.5], [3, 4, 0], [0, 0.5, 3, 3.5], [0, 1, 3, 3], [0, 1.5, 3, 2.5], [0, 2, 3, 2], [0, 2.5, 3, 1.5], [0, 3, 3, 1], [0, 3.5, 3, 0.5]];
  const calm = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const isDark = () => document.documentElement.classList.contains('dark');

  function markerEl(s, selected) {
    const el = document.createElement('button');
    el.type = 'button';
    el.className = 'monora-marker';
    el.title = s.ref;
    el.dataset.ref = s.ref;
    el.style.cssText = `--c:${STATUS[s.status] || '#888'}`;
    el.innerHTML = `<span class="pulse ${s.status === 'ATTENTION' || s.status === 'PROBLEM' ? 'on' : ''}"></span>
      <span class="dot"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${MODE_SVG[s.mode]}</svg></span>
      <span class="lbl">${s.ref}</span>`;
    if (selected) el.classList.add('sel');
    if (s.live) el.classList.add('live');
    return el;
  }

  function init(el, data, opts = {}) {
    const map = new maplibregl.Map({
      container: el, style: isDark() ? STYLES.dark : STYLES.light, attributionControl: { compact: true },
      center: [40, 20], zoom: 1.6, cooperativeGestures: !!opts.cooperative, dragRotate: false,
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), opts.controls || 'top-right');
    new ResizeObserver(() => map.resize()).observe(el);
    const markers = {};
    let selected = opts.selected || null;

    const usedCorridors = new Set(data.shipments.map((s) => s.corridor));
    const corridorFeatures = () => data.corridors
      .filter((c) => opts.corridors !== 'none' && (opts.corridors === 'all' || usedCorridors.has(c.code) || c.status !== 'OPEN'))
      .map((c) => ({ type: 'Feature', properties: { code: c.code, status: c.status, name: c.name }, geometry: { type: 'LineString', coordinates: c.waypoints.map(ll) } }));

    let traffic = { type: 'FeatureCollection', features: [] };
    // tint CARTO dark-matter to the app's navy
    function navyBasemap() {
      if (!isDark()) return;
      map.getStyle().layers.forEach((l) => {
        try {
          if (l.type === 'background') map.setPaintProperty(l.id, 'background-color', '#1c2639');
          else if (/water/.test(l.id) && l.type === 'fill') map.setPaintProperty(l.id, 'fill-color', '#060a13');
          else if (/water/.test(l.id) && l.type === 'line') map.setPaintProperty(l.id, 'line-color', '#060a13');
          else if (/landcover|landuse|park/.test(l.id) && l.type === 'fill') map.setPaintProperty(l.id, 'fill-color', '#1f2a3f');
          else if (/boundary|admin/.test(l.id) && l.type === 'line') map.setPaintProperty(l.id, 'line-color', '#3a4763');
          else if (l.type === 'symbol') map.setPaintProperty(l.id, 'text-color', '#8f98aa');
        } catch (e) {}
      });
    }
    // ---- multimodal legs (sea = solid cyan, truck = dashed amber) ----
    const LEG = { SEA: '#3ec7e0', ROAD: '#f5c542' };
    function legFeatures(legs) {
      return { type: 'FeatureCollection', features: legs.map((l) => ({ type: 'Feature', properties: { mode: l.mode, color: LEG[l.mode] || '#8f98aa' }, geometry: { type: 'LineString', coordinates: (l.mode === 'SEA' ? smooth(l.coords.map(ll), 6) : l.coords.map(ll)) } })) };
    }
    function planFeatures(g) {
      const fc = legFeatures(g.legs);
      const pt = (p, kind, name) => ({ type: 'Feature', properties: { kind, name }, geometry: { type: 'Point', coordinates: ll(p) } });
      fc.features.push(pt(g.port, 'port', g.port_name), pt(g.dest, 'dest', g.dest_name));
      return fc;
    }
    function addLegLayers(id, fc) {
      if (map.getSource(id)) return;
      map.addSource(id, { type: 'geojson', data: fc });
      const line = ['==', ['geometry-type'], 'LineString'];
      map.addLayer({ id: id + '-glow', type: 'line', source: id, filter: line, layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': ['get', 'color'], 'line-width': 10, 'line-blur': 7, 'line-opacity': 0.25 } });
      map.addLayer({ id: id + '-sea', type: 'line', source: id, filter: ['all', line, ['==', ['get', 'mode'], 'SEA']], layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': ['get', 'color'], 'line-width': 3.2 } });
      map.addLayer({ id: id + '-road', type: 'line', source: id, filter: ['all', line, ['==', ['get', 'mode'], 'ROAD']], layout: { 'line-cap': 'butt', 'line-join': 'round' }, paint: { 'line-color': ['get', 'color'], 'line-width': 3.2, 'line-dasharray': [1.4, 1] } });
      map.addLayer({ id: id + '-pts', type: 'circle', source: id, filter: ['==', ['geometry-type'], 'Point'], paint: { 'circle-radius': 6, 'circle-color': ['match', ['get', 'kind'], 'port', LEG.SEA, LEG.ROAD], 'circle-stroke-width': 2.5, 'circle-stroke-color': isDark() ? '#090e1a' : '#ffffff' } });
      map.addLayer({ id: id + '-lbl', type: 'symbol', source: id, filter: ['==', ['geometry-type'], 'Point'], layout: { 'text-field': ['get', 'name'], 'text-size': 12, 'text-offset': [0, 1.3], 'text-anchor': 'top', 'text-font': styleFont() }, paint: { 'text-color': isDark() ? '#eceef3' : '#131a2a', 'text-halo-color': isDark() ? '#090e1a' : '#ffffff', 'text-halo-width': 1.5 } });
    }
    let planGeo = null;
    // reuse a font the basemap actually ships glyphs for
    function styleFont() {
      const l = map.getStyle().layers.find((x) => x.type === 'symbol' && x.layout && Array.isArray(x.layout['text-font']));
      return l ? l.layout['text-font'] : ['Open Sans Regular'];
    }

    let flowStarted = false;
    function animateFlow() {
      if (flowStarted || calm) return;
      flowStarted = true;
      let step = -1, last = 0;
      const tick = (ts) => {
        if (ts - last > 70 && map.getLayer('options-flow')) {
          last = ts;
          step = (step + 1) % DASH.length;
          map.setPaintProperty('options-flow', 'line-dasharray', DASH[step]);
        }
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    }
    function addLayers() {
      navyBasemap();
      map.addSource('ais', { type: 'geojson', data: traffic });
      map.addLayer({ id: 'ais', type: 'circle', source: 'ais', paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 1, 2, 4, 3, 8, 4.5],
        'circle-color': ['case', ['>', ['get', 'sog'], 1], isDark() ? '#7d8fb8' : '#34509a', isDark() ? '#3a445c' : '#9a9aa2'],
        'circle-opacity': 0.55, 'circle-stroke-width': 0.5, 'circle-stroke-color': isDark() ? '#090e1a' : '#ffffff',
      } });
      map.addSource('corridors', { type: 'geojson', data: { type: 'FeatureCollection', features: corridorFeatures() } });
      const op = data.options.length ? 0.35 : 0.9;
      map.addLayer({ id: 'corridors-open', type: 'line', source: 'corridors', filter: ['==', ['get', 'status'], 'OPEN'], layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': isDark() ? '#475472' : '#a3a39b', 'line-width': 1.6, 'line-dasharray': [2, 2], 'line-opacity': op } });
      map.addLayer({ id: 'corridors-alert', type: 'line', source: 'corridors', filter: ['!=', ['get', 'status'], 'OPEN'], layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': ['match', ['get', 'status'], 'CLOSED', '#e11d48', '#f59e0b'], 'line-width': 2.6, 'line-dasharray': [1, 1.5], 'line-opacity': op } });
      if (data.options.length) {
        const feats = data.options.map((o) => ({ type: 'Feature', properties: { id: o.id, color: o.color, role: o.role || 'alt' }, geometry: { type: 'LineString', coordinates: smooth(o.waypoints.map(ll)) } }));
        // draw best route last so it sits on top
        const rank = { alt: 0, problem: 1, best: 2 };
        feats.sort((a, b) => rank[a.properties.role] - rank[b.properties.role]);
        map.addSource('options', { type: 'geojson', data: { type: 'FeatureCollection', features: feats } });
        const lay = { 'line-cap': 'round', 'line-join': 'round', 'line-sort-key': ['match', ['get', 'role'], 'best', 2, 'problem', 1, 0] };
        map.addLayer({ id: 'options-glow', type: 'line', source: 'options', layout: lay,
          paint: { 'line-color': ['get', 'color'], 'line-width': ['match', ['get', 'role'], 'best', 14, 10], 'line-blur': 8, 'line-opacity': 0.28 } });
        map.addLayer({ id: 'options', type: 'line', source: 'options', filter: ['!=', ['get', 'role'], 'problem'], layout: lay,
          paint: { 'line-color': ['get', 'color'], 'line-width': ['match', ['get', 'role'], 'best', 3.6, 2.6], 'line-opacity': 0.95 } });
        map.addLayer({ id: 'options-problem', type: 'line', source: 'options', filter: ['==', ['get', 'role'], 'problem'], layout: { 'line-cap': 'butt', 'line-join': 'round' },
          paint: { 'line-color': ['get', 'color'], 'line-width': 3, 'line-dasharray': [1.6, 1.4], 'line-opacity': 0.95 } });
        map.addLayer({ id: 'options-flow', type: 'line', source: 'options', filter: ['==', ['get', 'role'], 'best'], layout: { 'line-cap': 'butt', 'line-join': 'round' },
          paint: { 'line-color': '#eafff5', 'line-width': 1.4, 'line-opacity': 0.75, 'line-dasharray': DASH[0] } });
        animateFlow();
      }
      addLegLayers('legs', legFeatures(data.shipments.flatMap((sh) => sh.legs || [])));
      addLegLayers('plan', { type: 'FeatureCollection', features: [] });
      if (planGeo) map.getSource('plan').setData(planFeatures(planGeo));
      // origin/destination dots
      map.addSource('ends', { type: 'geojson', data: { type: 'FeatureCollection', features: data.shipments.flatMap((s) => [s.o, s.d].map((p) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: ll(p) } }))) } });
      map.addLayer({ id: 'ends', type: 'circle', source: 'ends', paint: { 'circle-radius': 3.5, 'circle-color': isDark() ? '#0e1524' : '#fff', 'circle-stroke-width': 1.5, 'circle-stroke-color': isDark() ? '#8f98aa' : '#384052' } });
    }

    map.on('style.load', addLayers);
    map.on('idle', function once() { navyBasemap(); map.off('idle', once); });

    // hover card for real AIS traffic
    const pop = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 8, className: 'ais-pop' });
    map.on('mouseenter', 'ais', (e) => {
      map.getCanvas().style.cursor = 'pointer';
      const p = e.features[0].properties;
      pop.setLngLat(e.features[0].geometry.coordinates).setHTML(`<b>${p.name || 'Unknown vessel'}</b><br><span>MMSI ${p.mmsi} · ${p.sog} kn</span>`).addTo(map);
    });
    map.on('mouseleave', 'ais', () => { map.getCanvas().style.cursor = ''; pop.remove(); });

    data.shipments.forEach((s) => {
      const el = markerEl(s, s.id === selected);
      el.addEventListener('click', (e) => { e.stopPropagation(); select(s.id); opts.onSelect && opts.onSelect(s); });
      markers[s.id] = new maplibregl.Marker({ element: el, anchor: 'center' }).setLngLat(ll(s.pos)).addTo(map);
    });

    function select(id) {
      selected = id;
      Object.entries(markers).forEach(([k, m]) => m.getElement().classList.toggle('sel', +k === id));
    }

    function fit() {
      const pts = [];
      const ships = data.focus ? data.shipments.filter((s) => s.id === data.focus) : data.shipments;
      ships.forEach((s) => { pts.push(ll(s.pos), ll(s.o), ll(s.d)); (s.legs || []).forEach((l) => l.coords.forEach((p) => pts.push(ll(p)))); });
      data.options.forEach((o) => o.waypoints.forEach((p) => pts.push(ll(p))));
      if (!pts.length) return;
      const b = pts.reduce((b, p) => b.extend(p), new maplibregl.LngLatBounds(pts[0], pts[0]));
      map.fitBounds(b, { padding: opts.padding || 48, maxZoom: 6, duration: 0 });
    }
    fit();

    window.addEventListener('monora:theme', () => map.setStyle(isDark() ? STYLES.dark : STYLES.light));

    // live positions (server keeps the aisstream connection; the key never reaches the browser)
    async function poll() {
      try {
        const b = map.getBounds();
        const q = `traffic=${opts.traffic ? 1 : 0}&bbox=${b.getWest().toFixed(2)},${b.getSouth().toFixed(2)},${b.getEast().toFixed(2)},${b.getNorth().toFixed(2)}`;
        const r = await fetch(`/api/live/?${q}`, { credentials: 'same-origin' });
        if (!r.ok) return;
        const j = await r.json();
        j.shipments.forEach((s) => {
          const mk = markers[s.id]; if (!mk) return;
          mk.setLngLat(ll(s.pos));
          const el = mk.getElement();
          el.classList.toggle('live', s.live);
          if (s.live) el.title = `${el.dataset.ref} · tracking ${s.vessel || 'MMSI ' + s.mmsi} (live AIS)`;
        });
        if (opts.traffic) {
          const mine = new Set(j.shipments.filter((s) => s.mmsi).map((s) => s.mmsi));
          traffic = { type: 'FeatureCollection', features: j.traffic.filter((v) => !mine.has(v[0])).map((v) => ({ type: 'Feature', properties: { mmsi: v[0], cog: v[3], sog: v[4], name: v[5] }, geometry: { type: 'Point', coordinates: [v[2], v[1]] } })) };
          map.getSource('ais')?.setData(traffic);
        }
        const n = document.querySelector('[data-ais-count]'); if (n) n.textContent = j.status.vessels.toLocaleString('en');
      } catch (e) { /* offline: keep last positions */ }
    }
    if (opts.live !== false) {
      map.once('load', poll);
      setInterval(poll, opts.interval || 8000);
      if (opts.traffic) map.on('moveend', poll);
    }

    return {
      map, select, fit,
      highlight(id) {
        if (!map.getSource('options')) return;
        const on = (a, b) => (id ? ['case', ['==', ['get', 'id'], id], a, b] : a);
        map.setPaintProperty('options', 'line-opacity', on(0.95, 0.15));
        map.setPaintProperty('options-problem', 'line-opacity', on(0.95, 0.15));
        map.setPaintProperty('options-flow', 'line-opacity', on(0.75, 0));
        map.setPaintProperty('options-glow', 'line-opacity', on(id ? 0.45 : 0.28, 0.05));
      },
      showPlan(g) {
        planGeo = g;
        const src = map.getSource('plan');
        if (src) src.setData(g ? planFeatures(g) : { type: 'FeatureCollection', features: [] });
        if (!g) return;
        const pts = g.legs.flatMap((l) => l.coords.map(ll));
        const b = pts.reduce((b, p) => b.extend(p), new maplibregl.LngLatBounds(pts[0], pts[0]));
        map.fitBounds(b, { padding: opts.planPadding || 80, maxZoom: 6.5, duration: 900 });
      },
      flyTo(id) { const s = data.shipments.find((x) => x.id === id); if (s) map.flyTo({ center: ll(s.pos), zoom: Math.max(map.getZoom(), 3.2), speed: 1.4 }); },
    };
  }

  window.MonoraMap = { init, STATUS };
})();
