// ---- symbol autocomplete -------------------------------------------------
const symbolInput = document.getElementById('symbol');
let symbolTimer = null;
symbolInput.addEventListener('input', () => {
  clearTimeout(symbolTimer);
  symbolTimer = setTimeout(async () => {
    const q = symbolInput.value.trim();
    if (q.length < 2) return;
    try {
      const res = await fetch(`/api/symbols?search=${encodeURIComponent(q)}`);
      const data = await res.json();
      const list = document.getElementById('symbol-list');
      list.innerHTML = '';
      (data.symbols || []).slice(0, 30).forEach(s => {
        const opt = document.createElement('option');
        opt.value = s;
        list.appendChild(opt);
      });
    } catch (e) { /* MT5 not reachable yet — ignore */ }
  }, 250);
});

// default date range: last 30 days
(() => {
  const to = new Date();
  const from = new Date();
  from.setDate(from.getDate() - 30);
  document.getElementById('date_to').value = to.toISOString().slice(0, 10);
  document.getElementById('date_from').value = from.toISOString().slice(0, 10);
})();

// ---- chart setup ----------------------------------------------------------
const chartEl = document.getElementById('chart');
const chart = LightweightCharts.createChart(chartEl, {
  layout: { background: { color: '#161b22' }, textColor: '#e6edf3' },
  grid: { vertLines: { color: '#262c36' }, horzLines: { color: '#262c36' } },
  timeScale: { timeVisible: true, secondsVisible: false },
  autoSize: true,
});
const candleSeries = chart.addCandlestickSeries({
  upColor: '#26a69a', downColor: '#ef5350', borderVisible: false,
  wickUpColor: '#26a69a', wickDownColor: '#ef5350',
});

const equityEl = document.getElementById('equity-chart');
const equityChart = LightweightCharts.createChart(equityEl, {
  layout: { background: { color: '#161b22' }, textColor: '#e6edf3' },
  grid: { vertLines: { color: '#262c36' }, horzLines: { color: '#262c36' } },
  timeScale: { timeVisible: true, secondsVisible: false },
  autoSize: true,
});
const equitySeries = equityChart.addAreaSeries({
  lineColor: '#4f8cff',
  topColor: 'rgba(79,140,255,0.35)',
  bottomColor: 'rgba(79,140,255,0.02)',
});

let markers = [];
const openTradeMarkerById = {};

function fmtMoney(v) {
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toFixed(2)}`;
}

function setOpenTradePanel(trade) {
  const el = document.getElementById('open-trade-panel');
  if (!trade) {
    el.className = 'empty';
    el.textContent = 'No open trade';
    return;
  }
  el.className = 'open-trade';
  el.innerHTML = `
    <div><span class="dir-${trade.direction}">${trade.direction.toUpperCase()}</span> @ ${trade.entry_price.toFixed(5)}</div>
    <div>SL: <span id="open-sl-inline">${trade.stop_loss.toFixed(5)}</span> &nbsp; TP: ${trade.take_profit.toFixed(5)}</div>
    <div>Vol remaining: <span id="open-vol-inline">${trade.volume}</span></div>
    <div>Reason: ${trade.reason}</div>
    <div>Unrealized (incl. booked partials): <strong id="open-pnl-inline">—</strong></div>
  `;
}

function addTradeLogRow(trade) {
  const log = document.getElementById('trade-log');
  const row = document.createElement('div');
  row.className = 'trade-row';
  const pnlClass = trade.pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
  const partialNote = trade.partial_pnl ? ` (incl. ${fmtMoney(trade.partial_pnl)} from partials)` : '';
  row.innerHTML = `
    <div class="head"><span>${trade.direction.toUpperCase()} #${trade.id}</span><span class="${pnlClass}">${fmtMoney(trade.pnl)}</span></div>
    <div class="meta">${trade.entry_price.toFixed(5)} → ${trade.exit_price.toFixed(5)} (${trade.exit_reason})${partialNote}</div>
    <div class="meta">${trade.reason}</div>
  `;
  log.prepend(row);
}

function addSystemLogNote(text) {
  const log = document.getElementById('trade-log');
  const row = document.createElement('div');
  row.className = 'trade-row note';
  row.innerHTML = `<div class="meta">${text}</div>`;
  log.prepend(row);
}

function renderReport(r) {
  const grid = document.getElementById('report-grid');
  const items = [
    ['Net PnL', fmtMoney(r.net_pnl), r.net_pnl >= 0 ? 'pos' : 'neg'],
    ['Ending Balance', r.ending_balance.toFixed(2), ''],
    ['Total Trades', r.total_trades, ''],
    ['Win Rate', r.win_rate + '%', ''],
    ['Wins / Losses', `${r.wins} / ${r.losses}`, ''],
    ['Profit Factor', r.profit_factor === null ? '∞' : r.profit_factor, ''],
    ['Max Drawdown', r.max_drawdown.toFixed(2), 'neg'],
  ];
  grid.innerHTML = items.map(([label, value, cls]) => `
    <div class="report-item">
      <div class="label">${label}</div>
      <div class="value ${cls}">${value}</div>
    </div>
  `).join('');
  document.getElementById('report').classList.remove('hidden');
}

// ── helpers ────────────────────────────────────────────────────────────────
function num(id)  { return Number(document.getElementById(id).value); }
function chk(id)  { return document.getElementById(id).checked; }

// ── collect VSA + Wyckoff strategy params from the form ───────────────────
function readStrategyParams() {
  const fixedVol    = num('fixed_volume');
  const partial2Pct = num('partial2_pct');

  const partialLevels = [
    { r_multiple: num('partial1_r'), percent: num('partial1_pct') },
  ];
  if (partial2Pct > 0) {
    partialLevels.push({ r_multiple: num('partial2_r'), percent: partial2Pct });
  }

  return {
    // ── Wyckoff Bank Levels ──────────────────────────────────────────────
    swing_lookback:                num('swing_lookback'),
    level_lookback_bars:           num('level_lookback_bars'),
    use_prev_day_levels:           chk('use_prev_day_levels'),
    use_prev_week_levels:          chk('use_prev_week_levels'),
    use_hvn_levels:                chk('use_hvn_levels'),
    hvn_bins:                      num('hvn_bins'),
    hvn_volume_threshold:          num('hvn_volume_threshold'),
    use_trading_range:             chk('use_trading_range'),
    range_lookback:                num('range_lookback'),
    range_min_atr_width:           num('range_min_atr_width'),
    range_max_atr_width:           num('range_max_atr_width'),
    zone_cluster_atr_multiplier:   num('zone_cluster_atr_multiplier'),
    zone_max_width_atr_multiplier: num('zone_max_width_atr_multiplier'),
    zone_tolerance_atr_multiplier: num('zone_tolerance_atr_multiplier'),
    min_zone_touches:              num('min_zone_touches'),

    // ── VSA Spread / Volume Thresholds ──────────────────────────────────
    vsa_wide_spread_mult:          num('vsa_wide_spread_mult'),
    vsa_narrow_spread_mult:        num('vsa_narrow_spread_mult'),
    vsa_high_volume_mult:          num('vsa_high_volume_mult'),
    vsa_ultra_high_volume_mult:    num('vsa_ultra_high_volume_mult'),
    vsa_low_volume_mult:           num('vsa_low_volume_mult'),
    volume_avg_period:             num('volume_avg_period'),

    // ── VSA / Wyckoff Signals ────────────────────────────────────────────
    enable_spring:                 chk('enable_spring'),
    spring_min_penetration_atr:    num('spring_min_penetration_atr'),
    enable_upthrust:               chk('enable_upthrust'),
    upthrust_min_penetration_atr:  num('upthrust_min_penetration_atr'),
    enable_no_supply:              chk('enable_no_supply'),
    enable_no_demand:              chk('enable_no_demand'),
    enable_selling_climax:         chk('enable_selling_climax'),
    enable_buying_climax:          chk('enable_buying_climax'),
    enable_sos:                    chk('enable_sos'),
    enable_sow:                    chk('enable_sow'),
    long_only:                     chk('long_only'),
    reverse_signals:               chk('reverse_signals'),

    // ── Risk / Stop Management ───────────────────────────────────────────
    use_atr_stops:                 chk('use_atr_stops'),
    atr_period:                    num('atr_period'),
    atr_sl_multiplier:             num('atr_sl_multiplier'),
    atr_tp_multiplier:             num('atr_tp_multiplier'),
    risk_reward:                   num('risk_reward'),
    sl_buffer_pips:                num('sl_buffer_pips'),
    fixed_volume:                  fixedVol > 0 ? fixedVol : null,
    starting_balance:              num('starting_balance'),
    playback_ms_per_bar:           num('playback_ms_per_bar'),

    // ── Partial TP & Trailing ─────────────────────────────────────────────
    enable_partial_tp:             chk('enable_partial_tp'),
    partial_tp_levels:             partialLevels,
    enable_sl_trailing:            chk('enable_sl_trailing'),
    breakeven_at_r:                num('breakeven_at_r'),
    trail_start_at_r:              num('trail_start_at_r'),
    trail_atr_multiplier:          num('trail_atr_multiplier'),

    // ── Optional Filters ─────────────────────────────────────────────────
    enable_ema_filter:             chk('enable_ema_filter'),
    ema_period:                    num('ema_period'),
    enable_rsi_filter:             chk('enable_rsi_filter'),
    rsi_period:                    num('rsi_period'),
    rsi_oversold_max:              num('rsi_oversold_max'),
    enable_volume_filter:          chk('enable_volume_filter'),
    volume_multiplier:             num('volume_multiplier'),
    enable_htf_trend_filter:       chk('enable_htf_trend_filter'),
    htf_minutes:                   num('htf_minutes'),
    htf_ema_period:                num('htf_ema_period'),
  };
}

// ── run backtest ─────────────────────────────────────────────────────────
const form      = document.getElementById('backtest-form');
const runBtn    = document.getElementById('run-btn');
const statusMsg = document.getElementById('status-msg');

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  runBtn.disabled = true;
  statusMsg.textContent = 'Fetching history from MT5…';
  document.getElementById('report').classList.add('hidden');
  document.getElementById('trade-log').innerHTML = '';
  candleSeries.setData([]);
  equitySeries.setData([]);
  markers = [];
  setOpenTradePanel(null);
  document.getElementById('equity-val').textContent = '—';
  document.getElementById('open-pnl-val').textContent = '—';

  const payload = {
    symbol:    document.getElementById('symbol').value.trim(),
    timeframe: document.getElementById('timeframe').value,
    date_from: document.getElementById('date_from').value,
    date_to:   document.getElementById('date_to').value,
    strategy:  readStrategyParams(),
  };
  console.log('[backtest] sending params:', payload.strategy);

  let sessionId;
  try {
    const res = await fetch('/api/backtest/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Failed to start backtest');
    }
    const data = await res.json();
    sessionId = data.session_id;
    console.log('[backtest] server confirmed params_used:', data.params_used);
    statusMsg.textContent = `Replaying ${data.bar_count} bars…`;
  } catch (err) {
    statusMsg.textContent = `Error: ${err.message}`;
    runBtn.disabled = false;
    return;
  }

  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${window.location.host}/ws/backtest/${sessionId}`);
  let equity = payload.strategy.starting_balance;
  let openTrades = {};

  ws.onmessage = (msg) => {
    const evt = JSON.parse(msg.data);

    if (evt.type === 'bar') {
      candleSeries.update({
        time: evt.time, open: evt.open, high: evt.high,
        low: evt.low, close: evt.close,
      });

    } else if (evt.type === 'trade_opened') {
      const t = evt.trade;
      openTrades[t.id] = t;
      setOpenTradePanel(t);
      markers.push({
        time: t.entry_time,
        position: t.direction === 'buy' ? 'belowBar' : 'aboveBar',
        color:    t.direction === 'buy' ? '#26a69a' : '#ef5350',
        shape:    t.direction === 'buy' ? 'arrowUp'  : 'arrowDown',
        text: `${t.direction.toUpperCase()} #${t.id}`,
      });
      candleSeries.setMarkers(markers);

    } else if (evt.type === 'trade_update') {
      const inline = document.getElementById('open-pnl-inline');
      if (inline) {
        inline.textContent = fmtMoney(evt.unrealized_pnl);
        inline.style.color = evt.unrealized_pnl >= 0 ? '#26a69a' : '#ef5350';
      }
      document.getElementById('open-pnl-val').textContent = fmtMoney(evt.unrealized_pnl);

    } else if (evt.type === 'trade_partial') {
      const t = openTrades[evt.id];
      if (t) {
        t.volume = evt.remaining_volume;
        setOpenTradePanel(t);
      }
      addSystemLogNote(
        `Partial close #${evt.id}: ${evt.volume_closed} lots @ ${evt.price.toFixed(5)} ` +
        `(${evt.r_multiple}R) → ${fmtMoney(evt.pnl)}`
      );
      markers.push({ time: evt.time, position: 'inBar', color: '#4f8cff', shape: 'circle', text: `P${evt.r_multiple}R` });
      candleSeries.setMarkers(markers);

    } else if (evt.type === 'trade_sl_moved') {
      const t = openTrades[evt.id];
      if (t) {
        t.stop_loss = evt.new_sl;
        const slInline = document.getElementById('open-sl-inline');
        if (slInline) slInline.textContent = evt.new_sl.toFixed(5);
      }
      if (evt.reason === 'breakeven') {
        addSystemLogNote(`SL #${evt.id} moved to breakeven (${evt.new_sl.toFixed(5)})`);
      }

    } else if (evt.type === 'trade_closed') {
      const t = evt.trade;
      delete openTrades[t.id];
      setOpenTradePanel(Object.values(openTrades)[0] || null);
      document.getElementById('open-pnl-val').textContent = '—';
      addTradeLogRow(t);
      equity = evt.equity;
      document.getElementById('equity-val').textContent = equity.toFixed(2);
      markers.push({
        time:     t.exit_time,
        position: t.direction === 'buy' ? 'aboveBar' : 'belowBar',
        color:    t.pnl >= 0 ? '#26a69a' : '#ef5350',
        shape:    'circle',
        text:     fmtMoney(t.pnl),
      });
      candleSeries.setMarkers(markers);
      equitySeries.update({ time: t.exit_time, value: equity });

    } else if (evt.type === 'report') {
      currentRun = {
        id: evt.run_id || null,
        trades: evt.segmented ? evt.segmented.trades : evt.trades,
      };
      renderReport(evt);
      document.getElementById('report-title').textContent = 'Backtest Report';
      showSavedState(evt.run_id ? 'Saved to history' : (evt.db_error || 'Not saved'));
      resetFilters();
      renderSegmented(evt.segmented);
      document.getElementById('report').scrollIntoView({ behavior: 'smooth' });
      statusMsg.textContent = 'Backtest complete.';
      runBtn.disabled = false;
      if (evt.run_id) { activeRunId = evt.run_id; loadHistory(); }

    } else if (evt.type === 'error') {
      statusMsg.textContent = `Error: ${evt.message}`;
      runBtn.disabled = false;
    }
  };

  ws.onerror = () => {
    statusMsg.textContent = 'WebSocket error — check the server logs.';
    runBtn.disabled = false;
  };
});

// ══ Segregated report (session / weekday / day) + filters ═════════════════
let currentRun = null;   // { id, trades }
let currentSegmented = null;
let activeTab = 'session';

function esc(v) {
  return String(v).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function pnlCell(v) { return `<td class="${v > 0 ? 'pos' : v < 0 ? 'neg' : ''}">${fmtMoney(v)}</td>`; }
function pf(v) { return v === null || v === undefined ? '∞' : v; }

function showSavedState(text) {
  document.getElementById('report-saved').textContent = text || '';
}

const METRIC_HEAD = '<th>Trades</th><th>Wins</th><th>Losses</th><th>Win %</th><th>Net PnL</th>' +
  '<th>Profit factor</th><th>Avg win</th><th>Avg loss</th><th>Expectancy</th><th>Max DD</th>';
function metricCells(m) {
  return `<td>${m.total_trades}</td><td>${m.wins}</td><td>${m.losses}</td><td>${m.win_rate}%</td>` +
    pnlCell(m.net_pnl) + `<td>${pf(m.profit_factor)}</td>` + pnlCell(m.avg_win) + pnlCell(m.avg_loss) +
    pnlCell(m.expectancy) + `<td class="neg">${m.max_drawdown.toFixed(2)}</td>`;
}

function renderTable() {
  const wrap = document.getElementById('report-table');
  const r = currentSegmented;
  if (!r) { wrap.innerHTML = ''; return; }
  let html = '';
  if (activeTab === 'session') {
    html = `<table><thead><tr><th>Session</th><th>Hours (UTC)</th>${METRIC_HEAD}</tr></thead><tbody>` +
      r.by_session.map(s => `<tr><td>${esc(s.label)}</td><td>${esc(s.hours_utc)}</td>${metricCells(s)}</tr>`).join('') +
      '</tbody></table>';
  } else if (activeTab === 'weekday') {
    html = `<table><thead><tr><th>Weekday</th>${METRIC_HEAD}</tr></thead><tbody>` +
      r.by_weekday.map(d => `<tr><td>${d.key}</td>${metricCells(d)}</tr>`).join('') + '</tbody></table>';
  } else if (activeTab === 'date') {
    html = `<table><thead><tr><th>Date (UTC)</th><th>Day</th>${METRIC_HEAD}</tr></thead><tbody>` +
      r.by_date.map(d => `<tr><td>${d.key}</td><td>${d.weekday}</td>${metricCells(d)}</tr>`).join('') + '</tbody></table>';
  } else {
    const labels = Object.fromEntries(Object.entries(r.sessions_config).map(([k, v]) => [k, v.label]));
    labels.off_hours = 'Off hours';
    html = '<table><thead><tr><th>#</th><th>Entry (UTC)</th><th>Day</th><th>Session</th><th>Dir</th>' +
      '<th>Entry</th><th>Exit</th><th>Exit reason</th><th>PnL</th><th>Signal</th></tr></thead><tbody>' +
      r.trades.map(t => `<tr><td>${t.id}</td><td>${t.entry_date} ${t.entry_hhmm}</td><td>${t.weekday}</td>` +
        `<td>${t.sessions.map(k => esc(labels[k] || k)).join(' + ')}</td><td>${t.direction.toUpperCase()}</td>` +
        `<td>${t.entry_price.toFixed(5)}</td><td>${t.exit_price.toFixed(5)}</td><td>${esc(t.exit_reason)}</td>` +
        pnlCell(t.pnl) + `<td style="text-align:left">${esc(t.reason)}</td></tr>`).join('') + '</tbody></table>';
  }
  const rows = { session: r.by_session, weekday: r.by_weekday, date: r.by_date, trades: r.trades }[activeTab];
  wrap.innerHTML = rows.length ? html : '<p class="muted">No trades match these filters.</p>';
}

function renderSegmented(seg) {
  currentSegmented = seg;
  const box = document.getElementById('filtered-summary');
  if (!seg) { box.innerHTML = ''; renderTable(); return; }
  const m = seg.overall;
  const filtered = Object.keys(seg.filters || {}).length > 0;
  const items = [
    [filtered ? 'Filtered trades' : 'Trades', m.total_trades, ''],
    ['Net PnL', fmtMoney(m.net_pnl), m.net_pnl >= 0 ? 'pos' : 'neg'],
    ['Win Rate', m.win_rate + '%', ''],
    ['Profit Factor', pf(m.profit_factor), ''],
    ['Expectancy', fmtMoney(m.expectancy), m.expectancy >= 0 ? 'pos' : 'neg'],
    ['Max DD (closed trades)', m.max_drawdown.toFixed(2), 'neg'],
  ];
  box.innerHTML = items.map(([label, value, cls]) => `
    <div class="report-item"><div class="label">${label}</div><div class="value ${cls}">${value}</div></div>`).join('');
  renderTable();
}

document.getElementById('report-tabs').addEventListener('click', (e) => {
  const btn = e.target.closest('.tab');
  if (!btn) return;
  activeTab = btn.dataset.tab;
  document.querySelectorAll('#report-tabs .tab').forEach(b => b.classList.toggle('active', b === btn));
  renderTable();
});

function readFilters() {
  const val = id => document.getElementById(id).value || null;
  const checked = name => [...document.querySelectorAll(`input[name=${name}]:checked`)].map(i => i.value);
  const sessions = checked('f_session');
  const weekdays = checked('f_weekday');
  return {
    date_from: val('f_date_from'), date_to: val('f_date_to'),
    time_from: val('f_time_from'), time_to: val('f_time_to'),
    sessions: sessions.length ? sessions : null,
    weekdays: weekdays.length ? weekdays : null,
  };
}

function resetFilters() {
  document.getElementById('report-filters').reset();
}

async function applyFilters() {
  if (!currentRun) return;
  const body = currentRun.id ? { run_id: currentRun.id } : { trades: currentRun.trades };
  body.filters = readFilters();
  try {
    const res = await fetch('/api/report', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Report failed');
    renderSegmented(data);
  } catch (err) {
    document.getElementById('report-table').innerHTML = `<p class="neg">${esc(err.message)}</p>`;
  }
}

document.getElementById('report-filters').addEventListener('submit', (e) => { e.preventDefault(); applyFilters(); });
document.getElementById('f_reset').addEventListener('click', () => { resetFilters(); applyFilters(); });

// ══ Run history sidebar (MongoDB) ════════════════════════════════════════
let historyRuns = [];
let activeRunId = null;

function renderHistory() {
  const list = document.getElementById('history-list');
  const q = document.getElementById('history-search').value.trim().toLowerCase();
  const runs = historyRuns.filter(r => !q || `${r.name} ${r.symbol} ${r.timeframe}`.toLowerCase().includes(q));
  if (!runs.length) {
    list.innerHTML = `<div class="empty">${historyRuns.length ? 'No matching runs.' : 'No saved runs yet. Run a backtest to start your history.'}</div>`;
    return;
  }
  list.innerHTML = runs.map(r => {
    const m = r.metrics || {};
    const when = new Date(r.created_at).toLocaleString();
    return `<div class="run-item ${r.id === activeRunId ? 'active' : ''}" data-id="${r.id}">
      <div class="name">${esc(r.name)}</div>
      <div class="actions">
        <button type="button" data-act="rename" title="Rename">✎</button>
        <button type="button" data-act="delete" title="Delete">✕</button>
      </div>
      <div class="meta"><span>${m.total_trades ?? 0} trades · ${m.win_rate ?? 0}%</span>
        <span class="${(m.net_pnl || 0) >= 0 ? 'pos' : 'neg'}">${fmtMoney(m.net_pnl || 0)}</span></div>
      <div class="meta"><span>${esc(when)}</span></div>
    </div>`;
  }).join('');
}

async function loadHistory() {
  const list = document.getElementById('history-list');
  try {
    const res = await fetch('/api/runs');
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Could not load history');
    historyRuns = data.runs;
    renderHistory();
  } catch (err) {
    list.innerHTML = `<div class="empty neg">${esc(err.message)}</div>`;
  }
}

function writeStrategyParams(p) {
  Object.entries(p).forEach(([key, value]) => {
    const el = document.getElementById(key);
    if (!el) return;
    if (el.type === 'checkbox') el.checked = !!value;
    else el.value = value === null ? 0 : value;
  });
  const levels = p.partial_tp_levels || [];
  const set = (id, v) => { document.getElementById(id).value = v; };
  if (levels[0]) { set('partial1_r', levels[0].r_multiple); set('partial1_pct', levels[0].percent); }
  set('partial2_r', levels[1] ? levels[1].r_multiple : 2.0);
  set('partial2_pct', levels[1] ? levels[1].percent : 0);
}

async function openRun(id) {
  statusMsg.textContent = 'Loading saved run…';
  try {
    const res = await fetch(`/api/runs/${id}`);
    const run = await res.json();
    if (!res.ok) throw new Error(run.detail || 'Could not load run');
    activeRunId = id;
    renderHistory();

    document.getElementById('symbol').value = run.symbol;
    document.getElementById('timeframe').value = run.timeframe;
    document.getElementById('date_from').value = run.date_from;
    document.getElementById('date_to').value = run.date_to;
    writeStrategyParams(run.params);
    document.querySelector('.strategy-params').open = true;

    // Candles aren't stored; show the equity curve and trade log from the saved run.
    candleSeries.setData([]);
    markers = [];
    candleSeries.setMarkers(markers);
    equitySeries.setData((run.equity_curve || []).map(p => ({ time: p.time, value: p.equity })));
    equityChart.timeScale().fitContent();
    document.getElementById('trade-log').innerHTML = '';
    run.trades.forEach(addTradeLogRow);
    setOpenTradePanel(null);
    document.getElementById('equity-val').textContent = run.metrics.ending_balance.toFixed(2);
    document.getElementById('open-pnl-val').textContent = '—';

    currentRun = { id, trades: run.trades };
    renderReport({ ...run.metrics,
      net_pnl: Math.round((run.metrics.ending_balance - run.metrics.starting_balance) * 100) / 100 });
    document.getElementById('report-title').textContent = run.name;
    showSavedState(`Saved ${new Date(run.created_at).toLocaleString()}`);
    resetFilters();
    await applyFilters();
    statusMsg.textContent = 'Loaded saved run. Press Run Backtest to replay it with candles.';
    document.getElementById('report').scrollIntoView({ behavior: 'smooth' });
  } catch (err) {
    statusMsg.textContent = `Error: ${err.message}`;
  }
}

document.getElementById('history-list').addEventListener('click', async (e) => {
  const item = e.target.closest('.run-item');
  if (!item) return;
  const id = item.dataset.id;
  const act = e.target.closest('button')?.dataset.act;
  if (act === 'rename') {
    const run = historyRuns.find(r => r.id === id);
    const name = prompt('Rename run', run ? run.name : '');
    if (!name || !name.trim()) return;
    await fetch(`/api/runs/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name.trim() }) });
    if (id === activeRunId) document.getElementById('report-title').textContent = name.trim();
    loadHistory();
  } else if (act === 'delete') {
    if (!confirm('Delete this saved run? This cannot be undone.')) return;
    await fetch(`/api/runs/${id}`, { method: 'DELETE' });
    if (id === activeRunId) activeRunId = null;
    loadHistory();
  } else {
    openRun(id);
  }
});
document.getElementById('history-search').addEventListener('input', renderHistory);
document.getElementById('history-refresh').addEventListener('click', loadHistory);
loadHistory();
