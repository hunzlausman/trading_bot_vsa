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
    } catch (e) {}
  }, 250);
});

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

const form = document.getElementById('live-form');
const startBtn = document.getElementById('start-btn');
const stopBtn = document.getElementById('stop-btn');
const statusMsg = document.getElementById('status-msg');
let currentSessionId = null;
let ws = null;

function logLine(text, cls = '') {
  const log = document.getElementById('live-log');
  const row = document.createElement('div');
  row.className = 'trade-row';
  if (cls) row.classList.add(cls);
  row.innerHTML = `<div class="meta">${new Date().toLocaleTimeString()}</div><div>${text}</div>`;
  log.prepend(row);
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  startBtn.disabled = true;
  statusMsg.textContent = 'Starting live loop…';

  const payload = {
    symbol: document.getElementById('symbol').value.trim(),
    timeframe: document.getElementById('timeframe').value,
    volume: Number(document.getElementById('volume').value),
    dry_run: document.getElementById('dry_run').checked,
    strategy: {},
  };

  try {
    const res = await fetch('/api/live/start', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    });
    const data = await res.json();
    currentSessionId = data.session_id;
  } catch (err) {
    statusMsg.textContent = `Error: ${err.message}`;
    startBtn.disabled = false;
    return;
  }

  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${window.location.host}/ws/live/${currentSessionId}`);

  ws.onmessage = (msg) => {
    const evt = JSON.parse(msg.data);
    if (evt.type === 'bar') {
      candleSeries.update({ time: evt.time, open: evt.open, high: evt.high, low: evt.low, close: evt.close });
    } else if (evt.type === 'signal') {
      logLine(`Signal: <b>${evt.direction.toUpperCase()}</b> @ ${evt.entry_price.toFixed(5)} — ${evt.reason}`, 'pnl-pos');
    } else if (evt.type === 'trade_opened') {
      logLine(`Order placed: ${evt.direction.toUpperCase()} ${evt.volume} lots @ ${evt.price.toFixed(5)} (ticket ${evt.ticket})`);
    } else if (evt.type === 'status') {
      statusMsg.textContent = evt.message;
      logLine(evt.message);
    } else if (evt.type === 'error') {
      logLine(`⚠ ${evt.message}`, 'pnl-neg');
    }
  };

  stopBtn.disabled = false;
  statusMsg.textContent = 'Live loop running.';
});

stopBtn.addEventListener('click', async () => {
  if (!currentSessionId) return;
  await fetch(`/api/live/stop/${currentSessionId}`, { method: 'POST' });
  stopBtn.disabled = true;
  startBtn.disabled = false;
  statusMsg.textContent = 'Stopped.';
  if (ws) ws.close();
});
