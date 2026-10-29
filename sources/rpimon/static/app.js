/* rpimon - interface web (uPlot, sans dépendance réseau) */
(function () {
  'use strict';

  // ------------------------------------------------------------------ config
  var CHARTS = [
    { id: 'temp',  title: 'Température SoC', unit: '°C', keys: ['temp'], wide: true, band: true, dec: 1, h: 250 },
    { id: 'cpu',   title: 'Utilisation CPU', unit: '%', keys: ['cpu'], band: true, dec: 0, range: [0, 100] },
    { id: 'load',  title: 'Charge système', unit: 'moyenne 1 min', keys: ['load1'], band: true, dec: 2, zero: true },
    { id: 'mem',   title: 'Mémoire', unit: '%', keys: ['mem', 'swap'], labels: ['RAM', 'Swap'], dec: 0, range: [0, 100] },
    { id: 'freq',  title: 'Fréquence ARM', unit: 'MHz', keys: ['freq'], band: true, dec: 0, zero: true },
    { id: 'net',   title: 'Réseau', unit: 'ko/s', keys: ['net_rx', 'net_tx'], labels: ['Reçu', 'Émis'], rate: true, zero: true },
    { id: 'io',    title: 'E/S disque', unit: 'ko/s', keys: ['io_read', 'io_write'], labels: ['Lecture', 'Écriture'], rate: true, zero: true },
    { id: 'disk',  title: 'Espace disque utilisé', unit: '%', keys: ['disk'], dec: 1 },
    { id: 'power', title: 'Alimentation', unit: '% du temps', keys: ['undervolt', 'throttled'], labels: ['Sous-tension', 'Bridage'], pct: true, dec: 0, range: [0, 100] }
  ];
  var KEYS = [];
  CHARTS.forEach(function (c) { c.keys.forEach(function (k) { if (KEYS.indexOf(k) < 0) KEYS.push(k); }); });

  var RES_LABEL = { raw: 'mesures brutes', hourly: 'moyennes horaires', daily: 'moyennes journalières' };
  var FR = {
    MMMM: ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'],
    MMM: ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.'],
    WWWW: ['dimanche', 'lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi'],
    WWW: ['dim.', 'lun.', 'mar.', 'mer.', 'jeu.', 'ven.', 'sam.']
  };
  var _ = null;
  var X_VALUES = [
    [3600 * 24 * 365, '{YYYY}', _, _, _, _, _, _, 1],
    [3600 * 24 * 28, '{MMM}', '\n{YYYY}', _, _, _, _, _, 1],
    [3600 * 24, '{D} {MMM}', '\n{YYYY}', _, _, _, _, _, 1],
    [3600, '{HH}h', '\n{D} {MMM} {YYYY}', _, '\n{D} {MMM}', _, _, _, 1],
    [60, '{HH}:{mm}', '\n{D} {MMM} {YYYY}', _, '\n{D} {MMM}', _, _, _, 1],
    [1, '{HH}:{mm}:{ss}', '\n{D} {MMM} {YYYY}', _, '\n{D} {MMM}', _, _, _, 1]
  ];

  // ------------------------------------------------------------------ état
  var state = { preset: 86400, custom: null };
  var meta = null, lastSeries = null, lastCurrent = null, hovered = null;
  var charts = {};           // id -> { u, sig }
  var themeVersion = 0;

  // ------------------------------------------------------------------ format
  var nfCache = {};
  function nf(d) {
    return nfCache[d] || (nfCache[d] = new Intl.NumberFormat('fr-FR', { minimumFractionDigits: d, maximumFractionDigits: d }));
  }
  function fmt(v, d) { return v == null || isNaN(v) ? '–' : nf(d == null ? 1 : d).format(v); }
  function fmtRate(v) {
    if (v == null) return '–';
    if (v >= 1000) return fmt(v / 1000, v >= 10000 ? 0 : 1) + ' Mo/s';
    return fmt(v, v < 10 ? 1 : 0) + ' ko/s';
  }
  function fmtBytes(n) {
    if (n == null) return '–';
    var u = ['o', 'ko', 'Mo', 'Go', 'To'], i = 0;
    while (n >= 1000 && i < u.length - 1) { n /= 1000; i++; }
    return fmt(n, i === 0 ? 0 : 1) + ' ' + u[i];
  }
  function fmtDuration(s) {
    s = Math.max(0, Math.round(s));
    var d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
    if (d) return d + ' j ' + h + ' h';
    if (h) return h + ' h ' + m + ' min';
    if (m) return m + ' min';
    return s + ' s';
  }
  var DF = {
    raw: new Intl.DateTimeFormat('fr-FR', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }),
    hourly: new Intl.DateTimeFormat('fr-FR', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }),
    daily: new Intl.DateTimeFormat('fr-FR', { weekday: 'short', day: 'numeric', month: 'long', year: 'numeric' }),
    short: new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' })
  };
  function fmtValue(c, v) {
    if (v == null) return '–';
    if (c.rate) return fmtRate(v);
    var unit = c.unit === '%' || c.pct ? ' %' : c.unit === '°C' ? ' °C' : c.unit === 'MHz' ? ' MHz' : '';
    return fmt(v, c.dec) + unit;
  }
  function fmtAxis(c, v) {
    if (c.rate) return fmt(v, v < 10 && v % 1 ? 1 : 0);
    var d = Math.abs(v) < 10 && v % 1 ? (c.dec > 1 ? 2 : 1) : 0;
    return fmt(v, d);
  }

  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function alpha(hex, a) {
    var h = hex.replace('#', '');
    if (h.length === 3) h = h.replace(/./g, '$&$&');
    var n = parseInt(h, 16);
    return 'rgba(' + (n >> 16 & 255) + ',' + (n >> 8 & 255) + ',' + (n & 255) + ',' + a + ')';
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (ch) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]; }); }
  function el(tag, cls, html) { var e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }

  function getJSON(path) {
    return fetch(path, { credentials: 'same-origin', cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error(r.status + ' ' + r.statusText);
      return r.json();
    });
  }

  // ------------------------------------------------------------------ statut
  var ICONS = {
    good: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="var(--good)"/><path d="M4.8 8.2l2.1 2.1 4.3-4.4" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    warning: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.5l7 12.5H1z" fill="var(--warning)"/><path d="M8 6v3.6" stroke="#0b0b0b" stroke-width="1.7" stroke-linecap="round"/><circle cx="8" cy="11.8" r="1" fill="#0b0b0b"/></svg>',
    serious: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.5l7 12.5H1z" fill="var(--serious)"/><path d="M8 6v3.6" stroke="#fff" stroke-width="1.7" stroke-linecap="round"/><circle cx="8" cy="11.8" r="1" fill="#fff"/></svg>',
    critical: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="var(--critical)"/><path d="M5.5 5.5l5 5M10.5 5.5l-5 5" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/></svg>',
    neutral: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="none" stroke="var(--neutral)" stroke-width="1.6"/><path d="M5 8h6" stroke="var(--neutral)" stroke-width="1.6" stroke-linecap="round"/></svg>'
  };
  function chip(level, label) { return '<span class="chip">' + ICONS[level] + esc(label) + '</span>'; }

  function tempStatus(t) {
    if (t == null) return ['neutral', 'Pas de mesure'];
    if (t >= 80) return ['critical', 'Critique - bridage probable'];
    if (t >= 70) return ['warning', 'Élevée'];
    return ['good', 'Normale'];
  }
  function powerStatus(cur) {
    var f = cur.flags;
    if (!cur.vcgencmd) return ['neutral', 'vcgencmd indisponible'];
    if (!f) return ['neutral', 'En attente'];
    if (f.undervolt_now) return ['critical', 'Sous-tension en cours'];
    if (f.throttled_now || f.freq_capped_now || f.soft_temp_now) return ['serious', 'Bridage en cours'];
    if (f.undervolt_boot) return ['warning', 'Sous-tension depuis le démarrage'];
    if (f.throttled_boot || f.freq_capped_boot || f.soft_temp_boot) return ['warning', 'Bridage depuis le démarrage'];
    return ['good', 'Stable'];
  }
  function meter(pct) {
    var cls = pct >= 90 ? ' crit' : pct >= 80 ? ' warn' : '';
    return '<div class="meter' + cls + '" role="presentation"><i style="width:' + Math.min(100, Math.max(0, pct || 0)) + '%"></i></div>';
  }

  function tile(label, value, unit, foot, extra) {
    return '<div class="tile"><div class="t-label">' + esc(label) + '</div>' +
      '<div class="t-value">' + value + (unit ? '<span class="u">' + esc(unit) + '</span>' : '') + '</div>' +
      (extra || '') + '<div class="t-foot">' + foot + '</div></div>';
  }

  function renderCurrent(cur) {
    lastCurrent = cur;
    var v = cur.v || {};
    var ts = tempStatus(v.temp), ps = powerStatus(cur);
    var swap = cur.swap_total ? ' · swap ' + fmt(v.swap, 0) + ' %' : '';
    var html = [
      tile('Température', fmt(v.temp, 1), '°C', chip(ts[0], ts[1])),
      tile('CPU', fmt(v.cpu, 0), '%', 'charge ' + cur.load.map(function (x) { return fmt(x, 2); }).join(' · ')),
      tile('Mémoire', fmt(v.mem, 0), '%', 'sur ' + fmtBytes(cur.mem_total) + swap, meter(v.mem)),
      tile('Disque', fmt(v.disk, 0), '%', fmt(v.disk_free, 1) + ' Go libres sur ' + fmtBytes(cur.disk_total), meter(v.disk)),
      tile('Réseau', '↓ ' + fmtRate(v.net_rx).replace(' ', ' '), '', '↑ ' + fmtRate(v.net_tx) + ' · dernière minute'),
      tile('Fréquence', fmt(v.freq, 0), 'MHz', cur.freq_max ? 'max ' + fmt(cur.freq_max, 0) + ' MHz' : 'ARM'),
      tile('Alimentation', ps[0] === 'good' ? 'OK' : ps[0] === 'neutral' ? '–' : 'Alerte', '', chip(ps[0], ps[1]) +
        (cur.flags ? '<br><span title="vcgencmd get_throttled">throttled = ' + esc(cur.flags.raw) + '</span>' : ''))
    ];
    document.getElementById('tiles').innerHTML = html.join('');

    var live = document.getElementById('live'), txt = document.getElementById('live-text');
    var age = cur.ts ? cur.now - cur.ts : null;
    var interval = meta ? meta.interval : 60;
    live.className = 'live' + (age == null || age > interval * 5 ? ' down' : age > interval * 2.5 ? ' stale' : '');
    txt.textContent = (age == null ? 'Aucune mesure' : 'Mesure il y a ' + fmtDuration(age)) +
      ' · allumé depuis ' + fmtDuration(cur.uptime);
  }

  function renderMeta(m) {
    meta = m;
    document.getElementById('host').textContent = m.hostname;
    document.title = m.hostname + ' · rpimon';
    document.getElementById('sysinfo').textContent = [m.model, m.os, m.kernel && 'noyau ' + m.kernel].filter(Boolean).join(' · ');
    var r = m.retention, oldest = dataStart();
    document.getElementById('foot').textContent = 'rpimon ' + m.version + ' · base ' + fmtBytes(m.db_size) +
      ' · conservation : brut ' + r.raw + ' j, horaire ' + (r.hourly ? r.hourly + ' j' : 'illimité') +
      ', journalier ' + (r.daily ? r.daily + ' j' : 'illimité') +
      (oldest ? ' · données depuis le ' + DF.short.format(oldest * 1000) : '');
  }

  function dataStart() {
    if (!meta) return null;
    var r = meta.ranges, c = [r.raw.from, r.hourly.from, r.daily.from].filter(function (x) { return x != null; });
    return c.length ? Math.min.apply(null, c) : null;
  }

  // ------------------------------------------------------------------ graphiques
  function currentRange() {
    if (state.custom) return state.custom;
    var to = Math.floor(Date.now() / 1000);
    if (state.preset === 'all') {
      var s = dataStart();
      return { from: s != null ? Math.min(s, to - 86400) : to - 86400, to: to };
    }
    return { from: to - state.preset, to: to };
  }

  function buildCards() {
    var root = document.getElementById('charts');
    CHARTS.forEach(function (c) {
      var card = el('article', 'card' + (c.wide ? ' wide' : ''));
      card.id = 'c-' + c.id;
      var keys = '';
      if (c.keys.length > 1) {
        keys = '<div class="keys">' + c.keys.map(function (k, i) {
          return '<span class="key"><i style="background:var(--s' + (i + 1) + ')"></i>' + esc(c.labels[i]) + '</span>';
        }).join('') + '</div>';
      }
      card.innerHTML = '<header><h2>' + esc(c.title) + '</h2><span class="meta">' + esc(c.unit) + '</span>' + keys + '</header>' +
        '<div class="plot"></div><div class="empty-msg">Pas de données sur cette période</div>';
      root.appendChild(card);
    });
    var disk = el('article', 'card wide disk empty');
    disk.id = 'disk-usage';
    disk.innerHTML = '<header><h2>Occupation du disque</h2><span class="meta" id="disk-meta"></span></header>' +
      '<div class="disk-body"><svg class="donut" viewBox="0 0 220 220" role="img" aria-labelledby="disk-title">' +
      '<title id="disk-title">Répartition de l\'espace disque</title></svg><table class="disk-legend"></table></div>' +
      '<p class="disk-note" id="disk-note">Première analyse en cours : quelques minutes, puis chaque nuit.</p>';
    root.appendChild(disk);
  }

  function tooltip(c, band) {
    var tip;
    return {
      hooks: {
        init: function (u) {
          tip = el('div', 'tip');
          tip.hidden = true;
          u.over.appendChild(tip);
          u.over.addEventListener('mouseenter', function () { hovered = u; });
          u.over.addEventListener('mouseleave', function () { hovered = null; tip.hidden = true; });
        },
        setCursor: function (u) {
          var i = u.cursor.idx;
          if (hovered !== u || i == null) { tip.hidden = true; return; }
          var res = lastSeries ? lastSeries.res : 'raw';
          var rows = '', any = false;
          c.keys.forEach(function (k, j) {
            var base = band ? 1 + j * 3 : 1 + j;
            var v = u.data[base][i];
            if (v != null) any = true;
            rows += '<div class="tip-row"><i style="background:var(--s' + (j + 1) + ')"></i>' +
              esc(c.labels ? c.labels[j] : (band ? 'Moyenne' : c.title)) + '<b>' + fmtValue(c, v) + '</b></div>';
            if (band && v != null) {
              rows += '<div class="tip-sub">min ' + fmtValue(c, u.data[base + 1][i]) + ' · max ' + fmtValue(c, u.data[base + 2][i]) + '</div>';
            }
          });
          if (!any) { tip.hidden = true; return; }
          var t = u.data[0][i] * 1000, when;
          if (res === 'hourly') {
            var hh = new Date(t).getHours();
            when = DF.daily.format(t) + ', ' + hh + ' h – ' + ((hh + 1) % 24) + ' h';
          } else {
            when = DF[res].format(t);
          }
          tip.innerHTML = '<div class="tip-date">' + esc(when) + '</div>' + rows;
          tip.hidden = false;
          var x = u.cursor.left, w = tip.offsetWidth, W = u.over.clientWidth;
          tip.style.left = (x + 16 + w > W ? Math.max(0, x - w - 16) : x + 16) + 'px';
        }
      }
    };
  }

  function makeOpts(c, band, width) {
    var colors = [css('--s1'), css('--s2')], surface = css('--surface');
    var grid = { stroke: css('--grid'), width: 1 };
    var font = '11px system-ui, -apple-system, "Segoe UI", sans-serif';
    var series = [{}];
    c.keys.forEach(function (k, i) {
      series.push({
        label: c.labels ? c.labels[i] : c.title,
        stroke: colors[i], width: 2, spanGaps: false,
        points: { size: 8, width: 2, stroke: surface, fill: colors[i] }
      });
      if (band) {
        series.push({ label: 'min', stroke: 'transparent', width: 0, points: { show: false } });
        series.push({ label: 'max', stroke: 'transparent', width: 0, points: { show: false } });
      }
    });
    var yRange;
    if (c.range) yRange = function () { return c.range.slice(); };
    else if (c.zero) yRange = function (u, mn, mx) { return [0, mx > 0 ? mx * 1.12 : 1]; };
    else yRange = function (u, mn, mx) {
      if (mn == null || mx == null) return [0, 1];
      var pad = Math.max((mx - mn) * 0.12, c.id === 'temp' ? 1.5 : 0.5);
      return [Math.floor(mn - pad), Math.ceil(mx + pad)];
    };
    return {
      width: width,
      height: c.h || 190,
      series: series,
      bands: band ? [{ series: [3, 2], fill: alpha(colors[0], 0.14) }] : [],
      legend: { show: false },
      padding: [8, 8, 0, 0],
      scales: {
        x: { time: true, range: function () { var r = currentRange(); return [r.from, r.to]; } },
        y: { range: yRange }
      },
      axes: [
        { stroke: css('--muted'), font: font, grid: grid, ticks: { stroke: css('--axis'), width: 1, size: 4 }, values: X_VALUES, space: 64 },
        { stroke: css('--muted'), font: font, grid: grid, ticks: { show: false }, size: 46, space: 32,
          values: function (u, splits) { return splits.map(function (v) { return fmtAxis(c, v); }); } }
      ],
      cursor: {
        y: false,
        sync: { key: 'rpimon' },
        drag: { x: true, y: false, setScale: false },
        points: { size: 8, width: 2, stroke: surface }
      },
      fmtDate: function (tpl) { return uPlot.fmtDate(tpl, FR); },
      hooks: {
        setSelect: [function (u) {
          if (u.select.width < 6) return;
          var a = Math.round(u.posToVal(u.select.left, 'x')), b = Math.round(u.posToVal(u.select.left + u.select.width, 'x'));
          u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
          if (b - a < 600) { var mid = (a + b) / 2; a = Math.round(mid - 300); b = Math.round(mid + 300); }
          state.custom = { from: a, to: b };
          syncButtons();
          loadSeries();
        }]
      },
      plugins: [tooltip(c, band)]
    };
  }

  function buildData(c, r, band) {
    var data = [r.t], has = false;
    c.keys.forEach(function (k) {
      var s = r.s[k] || {};
      var conv = c.pct ? function (a) { return a.map(function (v) { return v == null ? null : v * 100; }); } : function (a) { return a; };
      var avg = conv(s.avg || r.t.map(function () { return null; }));
      if (!has) has = avg.some(function (v) { return v != null; });
      data.push(avg);
      if (band) { data.push(conv(s.min)); data.push(conv(s.max)); }
    });
    return { data: data, has: has };
  }

  function renderChart(c, r) {
    var band = !!c.band && r.res !== 'raw';
    var card = document.getElementById('c-' + c.id), plot = card.querySelector('.plot');
    var built = buildData(c, r, band);
    card.classList.toggle('empty', !built.has);
    if (c.id === 'power' && lastCurrent && !lastCurrent.vcgencmd) {
      card.querySelector('.empty-msg').textContent = 'vcgencmd indisponible : ajoute l\'utilisateur rpimon au groupe video';
    }
    var sig = band + '|' + themeVersion;
    var ch = charts[c.id];
    if (ch && ch.sig === sig) { ch.u.setData(built.data); return; }
    if (ch) ch.u.destroy();
    var u = new uPlot(makeOpts(c, band, plot.clientWidth || 600), built.data, plot);
    charts[c.id] = { u: u, sig: sig };
  }

  function renderSeries(r) {
    lastSeries = r;
    CHARTS.forEach(function (c) { renderChart(c, r); });
    var rr = currentRange();
    var label = RES_LABEL[r.res] || r.res;
    if (r.res !== 'raw') label += ' · zone = min/max';
    if (state.custom) label = DF.short.format(rr.from * 1000) + ' → ' + DF.short.format(rr.to * 1000) + ' · ' + label;
    document.getElementById('range-label').textContent = label;
    document.getElementById('export').href = 'api/export.csv?from=' + rr.from + '&to=' + rr.to;
  }

  // ------------------------------------------------------------------ disque
  // Ordre fixe des couleurs (validé daltonisme), « Autres » en gris, « Libre » en retrait.
  var DISK_COLORS = ['--s1', '--s2', '--s3', '--s4', '--s5', '--s6', '--s7', '--s8'];
  var DF_SCAN = new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  var R_OUT = 106, R_IN = 72, C = 110;

  function arcPath(a0, a1) {
    a1 = Math.min(a1, a0 + 2 * Math.PI - 1e-4);          // un anneau complet se dessine en presque-cercle
    var large = a1 - a0 > Math.PI ? 1 : 0;
    function pt(r, a) { return (C + r * Math.cos(a)).toFixed(2) + ' ' + (C + r * Math.sin(a)).toFixed(2); }
    return 'M' + pt(R_OUT, a0) + 'A' + R_OUT + ' ' + R_OUT + ' 0 ' + large + ' 1 ' + pt(R_OUT, a1) +
      'L' + pt(R_IN, a1) + 'A' + R_IN + ' ' + R_IN + ' 0 ' + large + ' 0 ' + pt(R_IN, a0) + 'Z';
  }
  function clip(s, n) { return s.length > n ? s.slice(0, n - 1) + '…' : s; }

  // Survol ou focus d'une part (anneau) ou d'une ligne (légende) : les deux s'allument ensemble.
  var diskHighlight = function () {};
  function bindDiskHover() {
    var card = document.getElementById('disk-usage');
    function pick(e) { var t = e.target.closest('[data-i]'); return t ? parseInt(t.getAttribute('data-i'), 10) : null; }
    [card.querySelector('.donut'), card.querySelector('.disk-legend')].forEach(function (zone) {
      zone.addEventListener('mouseover', function (e) { diskHighlight(pick(e)); });
      zone.addEventListener('focusin', function (e) { diskHighlight(pick(e)); });
      zone.addEventListener('mouseleave', function () { diskHighlight(null); });
      zone.addEventListener('focusout', function () { diskHighlight(null); });
    });
  }

  function renderDisk(d) {
    var card = document.getElementById('disk-usage');
    if (!d || !d.ts || !d.items) { card.classList.add('empty'); return; }
    card.classList.remove('empty');
    var total = d.used + d.free;
    var slices = d.items.map(function (it, i) {
      return { label: it.label, path: it.path, size: it.size, color: it.path ? 'var(' + DISK_COLORS[i] + ')' : 'var(--neutral)' };
    });
    slices.push({ label: 'Libre', path: null, size: d.free, color: 'var(--free)' });

    var svg = card.querySelector('.donut'), a = -Math.PI / 2, paths = '';
    slices.forEach(function (s, i) {
      var b = a + 2 * Math.PI * s.size / total;
      paths += '<path data-i="' + i + '" d="' + arcPath(a, b) + '" style="fill:' + s.color + '"></path>';
      a = b;
    });
    var center = '<text class="c-val" x="' + C + '" y="' + (C + 4) + '" text-anchor="middle"></text>' +
      '<text class="c-sub" x="' + C + '" y="' + (C + 24) + '" text-anchor="middle"></text>';
    svg.innerHTML = '<title id="disk-title">Répartition de l\'espace disque</title>' + paths + center;

    var rows = slices.map(function (s, i) {
      return '<tr data-i="' + i + '" tabindex="0"><td><i class="sw" style="background:' + s.color + '"></i></td>' +
        '<td>' + esc(s.label) + (s.path ? '<span class="path">' + esc(s.path) + '</span>' : '') + '</td>' +
        '<td class="num">' + fmtBytes(s.size) + '</td><td class="num pct">' + fmt(100 * s.size / total, 1) + ' %</td></tr>';
    });
    var table = card.querySelector('.disk-legend');
    table.innerHTML = rows.join('');

    var val = svg.querySelector('.c-val'), sub = svg.querySelector('.c-sub');
    function highlight(i) {
      svg.classList.toggle('hl', i != null);
      Array.prototype.forEach.call(card.querySelectorAll('[data-i]'), function (e) {
        e.classList.toggle('on', i != null && e.getAttribute('data-i') === String(i));
      });
      if (i == null) {
        val.textContent = fmt(100 * d.used / total, 0) + ' %';
        sub.textContent = fmtBytes(d.used) + ' sur ' + fmtBytes(total);
      } else {
        val.textContent = fmtBytes(slices[i].size);
        sub.textContent = clip(slices[i].label, 24);
      }
    }
    diskHighlight = highlight;
    highlight(null);

    document.getElementById('disk-meta').textContent = 'partition ' + d.mount + ' · analysée le ' + DF_SCAN.format(d.ts * 1000) +
      ' · recalculée chaque nuit';
    var note = document.getElementById('disk-note');
    note.textContent = d.errors ? fmt(d.errors, 0) + ' élément(s) illisible(s) ignoré(s) lors de l\'analyse.' : '';
    note.hidden = !d.errors;
  }
  function loadDisk() {
    return getJSON('api/disk').then(renderDisk).catch(function (e) { console.error('rpimon: disque', e); });
  }

  var loading = null;
  function loadSeries() {
    var r = currentRange();
    var q = 'api/series?keys=' + KEYS.join(',') + '&from=' + r.from + '&to=' + r.to;
    var p = loading = getJSON(q).then(function (data) {
      if (p === loading) renderSeries(data);
    }).catch(function (e) { console.error('rpimon: séries', e); });
    return p;
  }
  function loadCurrent() {
    return getJSON('api/current').then(renderCurrent).catch(function (e) {
      console.error('rpimon: état courant', e);
      var live = document.getElementById('live');
      live.className = 'live down';
      document.getElementById('live-text').textContent = 'Service injoignable';
    });
  }
  function loadMeta() { return getJSON('api/meta').then(renderMeta); }

  function syncButtons() {
    Array.prototype.forEach.call(document.querySelectorAll('#ranges button'), function (b) {
      var r = b.getAttribute('data-r');
      b.classList.toggle('on', !state.custom && String(state.preset) === r);
    });
    document.getElementById('reset').hidden = !state.custom;
  }

  // ------------------------------------------------------------------ démarrage
  function init() {
    buildCards();
    bindDiskHover();
    document.getElementById('ranges').addEventListener('click', function (e) {
      var b = e.target.closest('button');
      if (!b) return;
      var r = b.getAttribute('data-r');
      state.preset = r === 'all' ? 'all' : parseInt(r, 10);
      state.custom = null;
      syncButtons();
      loadSeries();
    });
    document.getElementById('reset').addEventListener('click', function () {
      state.custom = null; syncButtons(); loadSeries();
    });

    var raf = 0;
    new ResizeObserver(function () {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(function () {
        CHARTS.forEach(function (c) {
          var ch = charts[c.id];
          if (!ch) return;
          var w = document.querySelector('#c-' + c.id + ' .plot').clientWidth;
          if (w && Math.abs(w - ch.u.width) > 1) ch.u.setSize({ width: w, height: c.h || 190 });
        });
      });
    }).observe(document.getElementById('charts'));

    var mq = window.matchMedia('(prefers-color-scheme: dark)');
    var onTheme = function () { themeVersion++; if (lastSeries) renderSeries(lastSeries); };
    if (mq.addEventListener) mq.addEventListener('change', onTheme); else if (mq.addListener) mq.addListener(onTheme);

    loadMeta().catch(function () {}).then(function () { return Promise.all([loadCurrent(), loadSeries(), loadDisk()]); });

    var tick = 0;
    setInterval(function () {
      if (document.hidden) return;
      tick++;
      loadCurrent();
      var short = !state.custom && state.preset !== 'all' && state.preset <= 172800;
      if (short || tick % 20 === 0) loadSeries();   // 30 s pour ≤ 48 h, sinon toutes les 10 min
      if (tick % 20 === 0) { loadMeta().catch(function () {}); loadDisk(); }
    }, 30000);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) { loadCurrent(); loadSeries(); } });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
