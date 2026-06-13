import { createChart, ColorType, LineStyle } from 'lightweight-charts';

/**
 * BTC Quantum Trader — Frontend Application
 * Connects to Python backend via WebSocket.
 * Renders candlestick chart, indicators, predictions, OB/OS alerts, verification.
 */

// ══════════════════════════════════════════════
//  State
// ══════════════════════════════════════════════
let ws = null;
let chart = null;
let candleSeries = null;
let lastCandle = null;
let volumeSeries = null;
let ema9Series = null;
let ema21Series = null;
let bbUpperSeries = null;
let bbLowerSeries = null;
let kronosSeries = null;
let supertrendSeries = null;
let supportLine = null;
let resistanceLine = null;

let rsiChart = null;
let rsiSeries = null;
let rsiObLine = null;
let rsiOsLine = null;

let macdChart = null;
let macdHistSeries = null;

let targetLines = [];
let chartInitialized = false;
let rsiData = [];
let macdData = [];

let currentVerifyTab = 'all';
let lastVerifyData = null;
let currentAppTab = 'technical';
let activePlainTF = 1;
let activePtbHorizon = 5;
let lastPlainData = null;

const API_URL = 'ws://127.0.0.1:8000/ws';
const HTTP_API_BASE = 'http://127.0.0.1:8000';

// ══════════════════════════════════════════════
//  DOM Elements
// ══════════════════════════════════════════════
const els = {
  loading: document.getElementById('loading-screen'),
  dashboard: document.getElementById('dashboard'),
  statusStep: document.getElementById('loading-status'),
  stepsGrid: document.getElementById('loading-steps').children,

  price: document.getElementById('current-price'),
  priceChange: document.getElementById('price-change'),
  vol24: document.getElementById('volume-24h'),
  hi24: document.getElementById('high-24h'),
  lo24: document.getElementById('low-24h'),

  regime: document.getElementById('regime-badge'),
  health: document.getElementById('model-grade'),
  bootTime: document.getElementById('boot-time'),
  backtestStatus: document.getElementById('backtest-status'),
  relearnStatus: document.getElementById('relearn-status'),
  backtestButton: document.getElementById('run-backtest-btn'),
  relearnButton: document.getElementById('relearn-models-btn'),
  liveDot: document.getElementById('live-dot'),
  connection: document.getElementById('connection-status'),

  predictionsGrid: document.getElementById('predictions-grid'),
  directionGrid: document.getElementById('direction-grid'),
  consensusArrow: document.getElementById('consensus-arrow'),
  consensusLabel: document.getElementById('consensus-label'),
  consensusConf: document.getElementById('consensus-conf'),

  depthChart: document.getElementById('depth-chart'),
  imbalanceBar: document.getElementById('imbalance-bar'),
  imbalanceVal: document.getElementById('imbalance-value'),

  tape: document.getElementById('tape-scroll'),
  cvd: document.getElementById('cvd-value'),

  funding: document.getElementById('funding-rate-val'),
  oi: document.getElementById('oi-val'),
  coinbasePrem: document.getElementById('coinbase-prem-val'),
  ls: document.getElementById('ls-ratio-val'),
  liqs: document.getElementById('liquidations-val'),

  fgVal: document.getElementById('fear-greed-value'),
  fgText: document.getElementById('fear-greed-text'),

  sigDir: document.getElementById('signal-direction'),
  sigConf: document.getElementById('conf-value'),
  sigConfBar: document.getElementById('conf-bar'),
  posSize: document.getElementById('position-size'),
  sl: document.getElementById('stop-loss'),
  tp: document.getElementById('take-profit'),

  alertsGrid: document.getElementById('alerts-grid'),
  alertStatusBadge: document.getElementById('alert-status-badge'),

  verifyAccRow: document.getElementById('verify-accuracy-row'),
  verifyLog: document.getElementById('verify-log'),
  verifyPending: document.getElementById('verify-pending'),
  verifyTabs: document.querySelectorAll('.verify-tab'),
  verifyMetrics: document.getElementById('verify-detailed-metrics'),
  vmOverall: document.getElementById('vm-overall'),
  vmUp: document.getElementById('vm-up'),
  vmDown: document.getElementById('vm-down'),
  vmHits: document.getElementById('vm-hits'),
  vmMisses: document.getElementById('vm-misses'),
  vmStreak: document.getElementById('vm-streak'),

  rsiValueLabel: document.getElementById('rsi-value-label'),
  macdValueLabel: document.getElementById('macd-value-label'),
  
  lRetrains: document.getElementById('l-retrains'),
  lSmooth: document.getElementById('l-smooth'),
  lConf: document.getElementById('l-conf'),
  lPulse: document.getElementById('learning-pulse'),

  appTabs: document.querySelectorAll('.app-tab'),
  technicalView: document.getElementById('technical-view'),
  analysisView: document.getElementById('analysis-view'),
  modelsView: document.getElementById('models-view'),
  ptbTabs: document.querySelectorAll('.ptb-tab'),
  ptbGrid: document.getElementById('ptb-grid'),
  ptbRecent: document.getElementById('ptb-recent'),
  ptbConfluenceGrid: document.getElementById('ptb-confluence-grid'),
  longshortGrid: document.getElementById('longshort-grid'),
  forecastScorecard: document.getElementById('forecast-scorecard'),
  feedHealthSummary: document.getElementById('feed-health-summary'),
  feedHealthGrid: document.getElementById('feed-health-grid'),
  modelRoster: document.getElementById('model-roster'),
  actionLog: document.getElementById('action-log'),
  directionalLog: document.getElementById('directional-log'),
  modelInventoryGrid: document.getElementById('model-inventory-grid'),
  analysisVerdict: document.getElementById('analysis-verdict'),
  analysisMeaning: document.getElementById('analysis-meaning'),
  analysisImpact: document.getElementById('analysis-impact'),
  analysisConfidence: document.getElementById('analysis-confidence'),
  analysisAccuracy: document.getElementById('analysis-accuracy'),
  analysisMissRate: document.getElementById('analysis-miss-rate'),
  analysisPriceMatch: document.getElementById('analysis-price-match'),
  analysisAvgError: document.getElementById('analysis-avg-error'),
  analysisUpError: document.getElementById('analysis-up-error'),
  analysisDownError: document.getElementById('analysis-down-error'),
  analysisSignals: document.getElementById('analysis-signals'),
  analysisErrors: document.getElementById('analysis-errors'),
  supportLevel: document.getElementById('support-level'),
  resistanceLevel: document.getElementById('resistance-level'),
  supportMeaning: document.getElementById('support-meaning'),
  resistanceMeaning: document.getElementById('resistance-meaning'),
  indicatorAnalysis: document.getElementById('indicator-analysis'),
  
  // New Plain Analysis Elements
  globalPulseGrid: document.getElementById('global-pulse-grid'),
  forecastCurrentPrice: document.getElementById('forecast-current-price'),
  forecastPulseGrid: document.getElementById('forecast-pulse-grid'),
  kronosStatus: document.getElementById('kronos-status'),
  signalFlowGrid: document.getElementById('signal-flow-grid'),
  tfSubtabs: document.querySelectorAll('.tf-tab'),
  activeTfLabel: document.getElementById('active-tf-label'),
  decisionAction: document.getElementById('decision-action'),
  decisionActionDetail: document.getElementById('decision-action-detail'),
  decisionReason: document.getElementById('decision-reason'),
  decisionReasonDetail: document.getElementById('decision-reason-detail'),
  decisionRisk: document.getElementById('decision-risk'),
  decisionRiskDetail: document.getElementById('decision-risk-detail'),
  decisionNext: document.getElementById('decision-next'),
  decisionNextDetail: document.getElementById('decision-next-detail'),
  trustScore: document.getElementById('trust-score'),
  trustLabel: document.getElementById('trust-label'),
  trustReasons: document.getElementById('trust-reasons'),
  actionReasons: document.getElementById('action-reasons'),
  actionMetrics: document.getElementById('action-metrics'),
  
  curveContainer: document.getElementById('quantile-curve-container'),
  curveStatus: document.getElementById('curve-status'),
  avoidCapitalSaved: document.getElementById('avoid-capital-saved'),
  avoidTotal: document.getElementById('avoid-total'),
  avoidHits: document.getElementById('avoid-hits'),
  avoidRate: document.getElementById('avoid-rate'),
  
  analysisExpectancy: document.getElementById('analysis-expectancy'),
  regimeHealthState: document.getElementById('regime-health-state'),
  regimeHealthPf: document.getElementById('regime-health-pf'),
  regimeHealthDd: document.getElementById('regime-health-dd'),
  labPrimaryAcc: document.getElementById('lab-primary-acc'),
  labChallengerAcc: document.getElementById('lab-challenger-acc'),
  labSignificance: document.getElementById('lab-significance'),
  decisionPrimaryAction: document.getElementById('decision-primary-action'),
  decisionPrimaryMessage: document.getElementById('decision-primary-message'),
  decisionMainCard: document.getElementById('decision-main-card'),
  decisionCockpitAction: document.getElementById('decision-cockpit-action'),
  decisionCockpitActionDetail: document.getElementById('decision-cockpit-action-detail'),
  decisionCockpitTarget: document.getElementById('decision-cockpit-target'),
  decisionCockpitTargetDetail: document.getElementById('decision-cockpit-target-detail'),
  decisionCockpitTrust: document.getElementById('decision-cockpit-trust'),
  decisionCockpitTrustDetail: document.getElementById('decision-cockpit-trust-detail'),
  decisionCockpitRisk: document.getElementById('decision-cockpit-risk'),
  decisionCockpitRiskDetail: document.getElementById('decision-cockpit-risk-detail'),
  decisionCockpitWhy: document.getElementById('decision-cockpit-why'),
  decisionCockpitWhyDetail: document.getElementById('decision-cockpit-why-detail'),
  decisionCockpitInvalid: document.getElementById('decision-cockpit-invalid'),
  decisionCockpitInvalidDetail: document.getElementById('decision-cockpit-invalid-detail'),
  decisionCockpitNext: document.getElementById('decision-cockpit-next'),
  decisionCockpitNextDetail: document.getElementById('decision-cockpit-next-detail'),
  decisionChecklist: document.getElementById('decision-checklist'),
  fsrPpoGrid: document.getElementById('fsr-ppo-grid'),
  fsrPpoRecent: document.getElementById('fsr-ppo-recent'),
};

// ══════════════════════════════════════════════
//  Init
// ══════════════════════════════════════════════
function init() {
  initMainChart();
  initRSIChart();
  initMACDChart();
  connectWebSocket();
  
  els.verifyTabs.forEach(tab => {
    tab.addEventListener('click', (e) => {
      els.verifyTabs.forEach(t => t.classList.remove('active'));
      e.target.classList.add('active');
      currentVerifyTab = e.target.dataset.horizon;
      if (lastVerifyData) renderVerification(lastVerifyData);
    });
  });

  els.appTabs.forEach(tab => {
    tab.addEventListener('click', () => {
      currentAppTab = tab.dataset.view;
      els.appTabs.forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      els.technicalView.classList.toggle('hidden', currentAppTab !== 'technical');
      els.analysisView.classList.toggle('hidden', currentAppTab !== 'analysis');
      if (els.modelsView) els.modelsView.classList.toggle('hidden', currentAppTab !== 'models');
      const bv = document.getElementById('binance-view');
      const pv = document.getElementById('polymarket-view');
      const bpv = document.getElementById('binancepm-view');
      if (bv) bv.classList.toggle('hidden', currentAppTab !== 'binance');
      if (pv) pv.classList.toggle('hidden', currentAppTab !== 'polymarket');
      if (bpv) bpv.classList.toggle('hidden', currentAppTab !== 'binancepm');
      if (currentAppTab === 'technical' && chart) {
        setTimeout(() => chart.timeScale().fitContent(), 50);
      }
      if (currentAppTab === 'analysis' && lastPlainData) {
        renderPlainAnalysis(lastPlainData);
      }
      if (currentAppTab === 'models') {
        if (lastPlainData) renderModelsView(lastPlainData);
        fetchActionLog();
      }
      if (currentAppTab === 'binance' && lastPlainData) renderBinanceView(lastPlainData);
      if (currentAppTab === 'polymarket' && lastPlainData) renderPolymarketView(lastPlainData);
      if (currentAppTab === 'binancepm' && lastPlainData) renderBinancePolymarketView(lastPlainData);
    });
  });

  els.tfSubtabs.forEach(tab => {
    tab.addEventListener('click', (e) => {
      els.tfSubtabs.forEach(t => t.classList.remove('active'));
      e.target.classList.add('active');
      activePlainTF = parseInt(e.target.dataset.tf, 10);
      els.activeTfLabel.textContent = e.target.textContent;
      if (lastPlainData) renderPlainAnalysis(lastPlainData);
    });
  });

  els.ptbTabs.forEach(tab => {
    tab.addEventListener('click', (e) => {
      els.ptbTabs.forEach(t => t.classList.remove('active'));
      e.target.classList.add('active');
      activePtbHorizon = parseInt(e.target.dataset.ptb, 10);
      if (lastPlainData) renderPriceToBeatTabbed(lastPlainData);
    });
  });

  if (els.relearnButton) {
    els.relearnButton.addEventListener('click', triggerRelearn);
  }
  if (els.backtestButton) {
    els.backtestButton.addEventListener('click', triggerBacktest);
  }
}

// ══════════════════════════════════════════════
//  Charts
// ══════════════════════════════════════════════
function initMainChart() {
  const container = document.getElementById('chart-container');
  chart = createChart(container, {
    layout: {
      background: { type: ColorType.Solid, color: 'transparent' },
      textColor: '#8892a6',
    },
    grid: {
      vertLines: { color: 'rgba(255, 255, 255, 0.03)' },
      horzLines: { color: 'rgba(255, 255, 255, 0.03)' },
    },
    timeScale: {
      timeVisible: true,
      secondsVisible: false,
      rightOffset: 6,        // small breathing room, not a huge empty gap
      barSpacing: 7,
      fixLeftEdge: true,
      lockVisibleTimeRangeOnResize: true,
    },
    rightPriceScale: { borderVisible: false },
    crosshair: {
      vertLine: { color: 'rgba(255, 255, 255, 0.1)', style: LineStyle.Dashed },
      horzLine: { color: 'rgba(255, 255, 255, 0.1)', style: LineStyle.Dashed },
    },
  });

  candleSeries = chart.addCandlestickSeries({
    upColor: '#00e676', downColor: '#ff1744',
    borderVisible: false,
    wickUpColor: '#00e676', wickDownColor: '#ff1744',
  });

  volumeSeries = chart.addHistogramSeries({
    priceFormat: { type: 'volume' },
    priceScaleId: 'volume',
  });
  chart.priceScale('volume').applyOptions({
    scaleMargins: { top: 0.85, bottom: 0 },
  });

  // EMA overlays
  ema9Series = chart.addLineSeries({ color: '#00d4ff', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  ema21Series = chart.addLineSeries({ color: '#ff9100', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });

  // Bollinger Band overlays
  bbUpperSeries = chart.addLineSeries({ color: 'rgba(123, 47, 247, 0.4)', lineWidth: 1, lineStyle: LineStyle.Dotted, priceLineVisible: false, lastValueVisible: false });
  bbLowerSeries = chart.addLineSeries({ color: 'rgba(123, 47, 247, 0.4)', lineWidth: 1, lineStyle: LineStyle.Dotted, priceLineVisible: false, lastValueVisible: false });

  // SuperTrend overlay
  supertrendSeries = chart.addLineSeries({ color: '#ffeb3b', lineWidth: 2, lineStyle: LineStyle.Solid, priceLineVisible: false, lastValueVisible: true });

  // Kronos Future Candlestick overlay
  kronosSeries = chart.addCandlestickSeries({
    upColor: 'rgba(0, 230, 118, 0.4)', downColor: 'rgba(255, 23, 68, 0.4)',
    borderUpColor: '#00e676', borderDownColor: '#ff1744',
    wickUpColor: 'rgba(0, 230, 118, 0.4)', wickDownColor: 'rgba(255, 23, 68, 0.4)',
    priceLineVisible: false, lastValueVisible: false
  });

  const resizeObserver = new ResizeObserver(entries => {
    if (entries.length === 0 || entries[0].target !== container) return;
    const r = entries[0].contentRect;
    chart.applyOptions({ width: r.width, height: r.height });
  });
  resizeObserver.observe(container);
}

function initRSIChart() {
  const container = document.getElementById('rsi-chart');
  rsiChart = createChart(container, {
    layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#5a6478' },
    grid: { vertLines: { visible: false }, horzLines: { color: 'rgba(255,255,255,0.02)' } },
    timeScale: { visible: false },
    rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.1, bottom: 0.1 } },
    crosshair: { horzLine: { visible: false }, vertLine: { visible: false } },
  });

  rsiSeries = rsiChart.addLineSeries({ color: '#ffd700', lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false });

  const resizeObserver = new ResizeObserver(entries => {
    if (entries.length === 0) return;
    const r = entries[0].contentRect;
    rsiChart.applyOptions({ width: r.width, height: r.height });
  });
  resizeObserver.observe(container);
}

function initMACDChart() {
  const container = document.getElementById('macd-chart');
  macdChart = createChart(container, {
    layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#5a6478' },
    grid: { vertLines: { visible: false }, horzLines: { color: 'rgba(255,255,255,0.02)' } },
    timeScale: { visible: false },
    rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.1, bottom: 0.1 } },
    crosshair: { horzLine: { visible: false }, vertLine: { visible: false } },
  });

  macdHistSeries = macdChart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });

  const resizeObserver = new ResizeObserver(entries => {
    if (entries.length === 0) return;
    const r = entries[0].contentRect;
    macdChart.applyOptions({ width: r.width, height: r.height });
  });
  resizeObserver.observe(container);
}

// ══════════════════════════════════════════════
//  WebSocket
// ══════════════════════════════════════════════
function clearSplash() {
  if (!els.loading || els.loading.classList.contains('fade-out')) return;
  els.loading.classList.add('fade-out');
  els.dashboard.classList.remove('hidden');
  setTimeout(() => { els.loading.style.display = 'none'; }, 600);
}

function connectWebSocket() {
  ws = new WebSocket(API_URL);

  ws.onopen = () => {
    els.liveDot.classList.remove('disconnected');
    els.connection.className = 'connection-status connected';
    els.connection.textContent = '● Connected';
    // Defensive: if we're connected but the first `update` is slow (e.g. the backend
    // is mid-retrain), force the splash away after a grace period so the user is never
    // trapped on the loading screen. Live updates will populate the dashboard shortly.
    setTimeout(() => {
      if (ws && ws.readyState === WebSocket.OPEN) clearSplash();
    }, 20000);
  };

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === 'status') {
      updateLoadingStatus(msg);
    } else if (msg.type === 'price_tick') {
      // Lightweight, high-frequency price refresh — decoupled from the heavy
      // prediction cycle so the displayed price stays responsive under load.
      updateLivePrice(msg.price);
    } else if (msg.type === 'update') {
      clearSplash();
      // Guard the render so one bad payload can't wedge the whole socket handler
      // (which would otherwise leave the user staring at a frozen dashboard/splash).
      try {
        renderDashboard(msg);
      } catch (err) {
        console.error('renderDashboard failed for this update:', err);
      }
    }
  };

  ws.onclose = () => {
    els.liveDot.classList.add('disconnected');
    els.connection.className = 'connection-status disconnected';
    els.connection.textContent = '● Disconnected';
    setTimeout(connectWebSocket, 3000);
  };

  ws.onerror = () => { /* handled by onclose */ };
}

function updateLoadingStatus(msg) {
  els.statusStep.textContent = msg.msg;
  const stepId = msg.step;
  for (let el of els.stepsGrid) {
    if (el.id === stepId) {
      el.className = 'loading-step active';
      el.textContent = '◆ ' + el.textContent.substring(2);
    } else if (el.classList.contains('active')) {
      el.className = 'loading-step done';
      el.textContent = '✓ ' + el.textContent.substring(2);
    }
  }
}

async function triggerRelearn() {
  if (!els.relearnButton) return;
  els.relearnButton.disabled = true;
  els.relearnButton.textContent = 'Queued...';
  try {
    const res = await fetch(`${HTTP_API_BASE}/api/relearn`, { method: 'POST' });
    const data = await res.json();
    if (!data.scheduled && data.status?.message) {
      els.relearnStatus.textContent = data.status.message;
    }
  } catch (err) {
    els.relearnStatus.textContent = 'Request failed';
    els.relearnButton.disabled = false;
    els.relearnButton.textContent = 'Relearn Models';
  }
}

async function triggerBacktest() {
  if (!els.backtestButton) return;
  els.backtestButton.disabled = true;
  els.backtestButton.textContent = 'Queued...';
  try {
    const res = await fetch(`${HTTP_API_BASE}/api/backtest`, { method: 'POST' });
    const data = await res.json();
    if (!data.scheduled && data.status?.message) {
      els.backtestStatus.textContent = data.status.message;
    }
  } catch (err) {
    els.backtestStatus.textContent = 'Request failed';
    els.backtestButton.disabled = false;
    els.backtestButton.textContent = 'Run Backtest';
  }
}

// ══════════════════════════════════════════════
//  Dashboard Render
// ══════════════════════════════════════════════
function renderDashboard(data) {
  renderPrice(data);
  renderChart(data);
  renderOrderFlow(data);
  renderTape(data);
  renderDerivatives(data);
  renderPredictions(data);
  renderDirectionalAnalysis(data);
  renderAlerts(data);
  renderLearning(data);
  renderBootStatus(data);
  renderRuntimeStatus(data);
  renderPlainAnalysis(data);
  renderScoreboard(data);
  renderExchanges(data);
  renderModelsView(data);
  if (currentAppTab === 'binance') renderBinanceView(data);
  if (currentAppTab === 'polymarket') renderPolymarketView(data);
  if (currentAppTab === 'binancepm') renderBinancePolymarketView(data);

  lastVerifyData = data.verification;
  renderVerification(data.verification);
  renderBacktest(data);
}

let _lastTickPrice = null;
function updateLivePrice(price) {
  if (price == null || !els.price) return;
  els.price.textContent = `$${price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  // brief color flash on up/down ticks for a "live" feel
  if (_lastTickPrice != null && price !== _lastTickPrice) {
    els.price.style.color = price > _lastTickPrice ? 'var(--green)' : 'var(--red)';
    setTimeout(() => { els.price.style.color = ''; }, 250);
  }
  _lastTickPrice = price;
  // keep the live candle's close in sync between full updates
  if (candleSeries && lastCandle) {
    try { candleSeries.update({ ...lastCandle, close: price, high: Math.max(lastCandle.high, price), low: Math.min(lastCandle.low, price) }); } catch (e) {}
  }
}

function renderPrice(data) {
  const price = data.price;
  els.price.textContent = `$${price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  _lastTickPrice = price;

  const t = data.ticker_24h;
  if (t) {
    els.priceChange.textContent = `${t.price_change_percent >= 0 ? '+' : ''}${t.price_change_percent.toFixed(2)}%`;
    els.priceChange.className = `price-change ${t.price_change_percent >= 0 ? 'up' : 'down'}`;
    els.vol24.textContent = formatNumberShort(t.volume) + ' BTC';
    els.hi24.textContent = Math.round(t.high_price).toLocaleString();
    els.lo24.textContent = Math.round(t.low_price).toLocaleString();
  }

  if (data.regime) {
    els.regime.textContent = data.regime.regime;
    els.regime.className = `regime-badge ${data.regime.regime.toLowerCase().replace(/_/g, '-')}`;
  }
  els.health.textContent = data.health || '--';
}

function renderChart(data) {
  if (!data.klines || data.klines.length === 0) return;

  // Set candlestick data
  candleSeries.setData(data.klines);
  if (data.klines && data.klines.length) lastCandle = data.klines[data.klines.length - 1];

  // Volume
  if (data.volume_data) {
    volumeSeries.setData(data.volume_data);
  }

  // Compute and set EMA / BB overlays from klines
  const closes = data.klines.map(k => k.close);
  const times = data.klines.map(k => k.time);

  // EMA 9
  const ema9 = computeEMA(closes, 9);
  ema9Series.setData(ema9.map((v, i) => v !== null ? { time: times[i], value: v } : null).filter(Boolean));

  // EMA 21
  const ema21 = computeEMA(closes, 21);
  ema21Series.setData(ema21.map((v, i) => v !== null ? { time: times[i], value: v } : null).filter(Boolean));

  // Bollinger Bands
  const bb = computeBB(closes, 20, 2);
  bbUpperSeries.setData(bb.upper.map((v, i) => v !== null ? { time: times[i], value: v } : null).filter(Boolean));
  bbLowerSeries.setData(bb.lower.map((v, i) => v !== null ? { time: times[i], value: v } : null).filter(Boolean));

  // RSI sub-chart. Prefer backend values so chart matches the feature engine.
  const backendRsi = data.indicator_series?.rsi || [];
  const rsiArr = backendRsi.length ? [] : computeRSI(closes, 14);
  const rsiChartData = backendRsi.length
    ? backendRsi
    : rsiArr.map((v, i) => v !== null ? { time: times[i], value: v } : null).filter(Boolean);
  rsiSeries.setData(rsiChartData);
  if (rsiChartData.length > 0) {
    const lastRsi = rsiChartData[rsiChartData.length - 1].value;
    els.rsiValueLabel.textContent = lastRsi.toFixed(1);
    els.rsiValueLabel.style.color = lastRsi > 70 ? '#ff1744' : lastRsi < 30 ? '#00e676' : '#8892a6';
  }

  // MACD sub-chart
  const macdResult = computeMACD(closes, 12, 26, 9);
  const macdChartData = macdResult.histogram.map((v, i) => {
    if (v === null) return null;
    return { time: times[i], value: v, color: v >= 0 ? 'rgba(0, 230, 118, 0.6)' : 'rgba(255, 23, 68, 0.6)' };
  }).filter(Boolean);
  macdHistSeries.setData(macdChartData);
  if (macdChartData.length > 0) {
    const lastMacd = macdChartData[macdChartData.length - 1].value;
    els.macdValueLabel.textContent = lastMacd.toFixed(2);
    els.macdValueLabel.style.color = lastMacd >= 0 ? '#00e676' : '#ff1744';
  }

  // SuperTrend overlay. Prefer backend series so it uses the exact feature formula.
  const supertrendData = data.indicator_series?.supertrend || [];
  if (supertrendData.length > 0) {
    supertrendSeries.setData(supertrendData);
  } else if (data.indicators && data.indicators.supertrend) {
    supertrendSeries.update({ time: times[times.length - 1], value: data.indicators.supertrend });
  }

  // Kronos Forecasts overlay — cap the future projection so the chart isn't dominated
  // by a long empty forecast tail (Kronos returns up to 60 future candles).
  if (data.kronos_forecasts && data.kronos_forecasts.length > 0) {
    kronosSeries.setData(data.kronos_forecasts.slice(0, 15));
  } else {
    kronosSeries.setData([]);
  }

  // Target price lines & Candlestick Markers
  const preds = data.predictions;
  let markers = [];
  if (preds && preds.length > 0) {
    targetLines.forEach(l => { try { candleSeries.removePriceLine(l); } catch(e) {} });
    targetLines = [];

    let best = preds[0];
    for (const p of preds) if (p.confidence > best.confidence) best = p;

    if (best.direction !== 'NEUTRAL') {
      targetLines.push(candleSeries.createPriceLine({ price: best.takeProfit, color: '#00e676', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: 'TP' }));
      targetLines.push(candleSeries.createPriceLine({ price: best.stopLoss, color: '#ff1744', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: 'SL' }));
      targetLines.push(candleSeries.createPriceLine({ price: best.targetPrice, color: '#00d4ff', lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: true, title: 'TGT' }));
      
      // Add a signal marker on the current candle
      markers.push({
        time: times[times.length - 1],
        position: best.direction === 'UP' ? 'belowBar' : 'aboveBar',
        color: best.direction === 'UP' ? '#00e676' : '#ff1744',
        shape: best.direction === 'UP' ? 'arrowUp' : 'arrowDown',
        text: best.direction === 'UP' ? 'BUY' : 'SELL'
      });
    }
  }
  candleSeries.setMarkers(markers);
  
  // Support & Resistance Lines from backend candle pivots + liquidity walls
  const sr = data.support_resistance || {};
  if (sr.support) {
    const support = sr.support;
    if (supportLine) candleSeries.removePriceLine(supportLine);
    supportLine = candleSeries.createPriceLine({ price: support, color: '#ff9800', lineWidth: 2, lineStyle: LineStyle.Solid, axisLabelVisible: true, title: 'SUP' });
  }
  if (sr.resistance) {
    const resistance = sr.resistance;
    if (resistanceLine) candleSeries.removePriceLine(resistanceLine);
    resistanceLine = candleSeries.createPriceLine({ price: resistance, color: '#ff9800', lineWidth: 2, lineStyle: LineStyle.Solid, axisLabelVisible: true, title: 'RES' });
  }

  if (!chartInitialized) {
    chart.timeScale().fitContent();
    chartInitialized = true;
  }
}

function renderOrderFlow(data) {
  const of = data.order_flow;
  if (!of) return;

  els.cvd.textContent = formatNumberShort(of.cvd);
  els.cvd.className = `cvd-value ${of.cvd >= 0 ? 'positive' : 'negative'}`;

  const imb = of.imbalance * 100;
  els.imbalanceVal.textContent = Math.abs(imb).toFixed(1) + '%';
  els.imbalanceBar.style.width = Math.abs(imb) + '%';
  if (imb >= 0) {
    els.imbalanceBar.className = 'imbalance-bar-fill bullish';
    els.imbalanceVal.style.color = 'var(--green)';
  } else {
    els.imbalanceBar.className = 'imbalance-bar-fill bearish';
    els.imbalanceVal.style.color = 'var(--red)';
  }
}

function renderTape(data) {
  if (!data.tape || data.tape.length === 0) return;
  els.tape.innerHTML = '';
  data.tape.reverse().forEach(t => {
    const isBuy = t.is_buy;
    const el = document.createElement('div');
    el.className = `tape-row ${isBuy ? 'buy' : 'sell'} ${t.is_whale ? 'whale' : ''}`;
    const time = new Date(t.time).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const qtyStr = t.quantity >= 10 ? t.quantity.toFixed(1) : t.quantity.toFixed(3);
    el.innerHTML = `<span class="tape-time">${time}</span><span class="tape-price">${t.price.toFixed(1)}</span><span class="tape-qty">${qtyStr}</span><span class="tape-side">${isBuy ? 'BUY' : 'SELL'}</span>`;
    els.tape.appendChild(el);
  });
}

function renderDerivatives(data) {
  const d = data.derivatives;
  if (!d) return;
  if (d.funding_rate) els.funding.textContent = (d.funding_rate.rate * 100).toFixed(4) + '%';
  
  if (d.global_oi != null && d.global_oi > 0) {
    els.oi.textContent = '$' + formatNumberShort(d.global_oi);
  } else if (d.open_interest) {
    els.oi.textContent = formatNumberShort(d.open_interest.open_interest) + ' BTC';
  }

  if (d.coinbase_premium != null) {
    const cp = d.coinbase_premium;
    const sign = cp >= 0 ? '+' : '';
    els.coinbasePrem.textContent = sign + cp.toFixed(2);
    els.coinbasePrem.style.color = cp >= 0 ? 'var(--green)' : 'var(--red)';
  } else {
    els.coinbasePrem.textContent = '---';
    els.coinbasePrem.style.color = 'var(--text-primary)';
  }

  if (d.long_short_ratio?.length) els.ls.textContent = d.long_short_ratio[0].ratio.toFixed(2);
  if (d.liquidations?.length) {
    const sumLiq = d.liquidations.reduce((s, x) => s + (x.qty * x.price), 0);
    els.liqs.textContent = '$' + formatNumberShort(sumLiq);
  }

  const s = data.sentiment;
  if (s && s.fear_greed) {
    els.fgVal.textContent = s.fear_greed.value;
    els.fgText.textContent = s.fear_greed.classification;
  }
}

function renderPredictions(data) {
  const preds = data.predictions;
  if (!preds || preds.length === 0) return;

  els.predictionsGrid.innerHTML = '';
  preds.forEach(p => {
    const isUp = p.direction === 'UP';
    const isDown = p.direction === 'DOWN';
    const cssClass = isUp ? 'bullish' : isDown ? 'bearish' : 'neutral';
    const dirCls = isUp ? 'up' : isDown ? 'down' : 'flat';
    const card = document.createElement('div');
    card.className = `pred-card ${cssClass}`;
    card.innerHTML = `
      <div class="pred-card-top">
        <span class="pred-timeframe">${p.horizon}m Forecast</span>
        <span class="pred-direction ${dirCls}">${p.direction}</span>
      </div>
      <div class="pred-card-body">
        <div class="pred-field"><span class="pred-field-label">Target</span><span class="pred-field-value">$${Math.round(p.targetPrice).toLocaleString()}</span></div>
        <div class="pred-field"><span class="pred-field-label">Expected</span><span class="pred-field-value">±$${Math.round(p.expectedMove)}</span></div>
        <div class="prob-labels"><span class="prob-down-label">${(p.probDown * 100).toFixed(1)}%</span><span class="prob-up-label">${(p.probUp * 100).toFixed(1)}%</span></div>
        <div class="pred-prob-bar"><div class="prob-bar-track"><div class="prob-bar-down" style="width: ${p.probDown * 100}%"></div><div class="prob-bar-up" style="width: ${p.probUp * 100}%"></div></div></div>
        <span class="pred-confidence">${(p.confidence * 100).toFixed(1)}% Confidence</span>
      </div>`;
    els.predictionsGrid.appendChild(card);
  });

  // Main signal
  let best = preds[0];
  for (const p of preds) if (p.confidence > best.confidence) best = p;
  els.sigDir.textContent = best.signal;
  els.sigDir.className = `signal-direction ${best.signal.toLowerCase().replace(/ /g, '-')}`;
  els.sigConf.textContent = Math.round(best.confidence * 100) + '%';
  els.sigConfBar.style.width = Math.round(best.confidence * 100) + '%';
  els.posSize.textContent = best.positionSize + '%';
  els.sl.textContent = '$' + Math.round(best.stopLoss).toLocaleString();
  els.tp.textContent = '$' + Math.round(best.takeProfit).toLocaleString();
}

function renderDirectionalAnalysis(data) {
  const preds = data.predictions;
  if (!preds || preds.length === 0) return;

  // Direction rows
  els.directionGrid.innerHTML = '';
  let upCount = 0, downCount = 0;
  let totalConf = 0;

  preds.forEach(p => {
    const isUp = p.direction === 'UP';
    const isDown = p.direction === 'DOWN';
    if (isUp) upCount++;
    if (isDown) downCount++;
    totalConf += p.confidence;

    const arrowChar = isUp ? '▲' : isDown ? '▼' : '●';
    const dirClass = isUp ? 'up' : isDown ? 'down' : 'neutral';
    const row = document.createElement('div');
    row.className = 'direction-row';
    row.innerHTML = `
      <span class="direction-tf">${p.horizon}m</span>
      <span class="direction-arrow ${dirClass}">${arrowChar}</span>
      <span class="direction-label ${dirClass}">${p.direction}</span>
      <span class="direction-conf">${(p.confidence * 100).toFixed(0)}%</span>`;
    els.directionGrid.appendChild(row);
  });

  // Consensus
  let consensus, consensusClass;
  if (upCount > downCount && upCount >= 2) {
    consensus = 'BULLISH';
    consensusClass = 'up';
  } else if (downCount > upCount && downCount >= 2) {
    consensus = 'BEARISH';
    consensusClass = 'down';
  } else {
    consensus = 'NEUTRAL';
    consensusClass = 'neutral';
  }

  els.consensusArrow.className = `consensus-arrow ${consensusClass}`;
  els.consensusLabel.textContent = consensus;
  els.consensusLabel.className = `consensus-label ${consensusClass}`;
  els.consensusConf.textContent = (totalConf / preds.length * 100).toFixed(0) + '%';
}

function renderAlerts(data) {
  const ind = data.indicators;
  if (!ind) return;

  const alerts = [
    { name: 'RSI', value: ind.rsi, status: ind.rsi_status, display: ind.rsi?.toFixed(1) },
    { name: 'Stoch RSI', value: ind.stoch_rsi, status: ind.stoch_rsi_status, display: ind.stoch_rsi?.toFixed(1) },
    { name: 'MFI', value: ind.mfi, status: ind.mfi_status, display: ind.mfi?.toFixed(1) },
    { name: 'CCI', value: ind.cci, status: ind.cci_status, display: ind.cci?.toFixed(0) },
    { name: 'Williams%R', value: ind.williams_r, status: ind.williams_r_status, display: ind.williams_r?.toFixed(1) },
    { name: 'BB Position', value: ind.bb_position, status: ind.bb_status, display: (ind.bb_position * 100)?.toFixed(0) + '%' },
    { name: 'ADX', value: ind.adx, status: ind.trend_strength, display: ind.adx?.toFixed(1) },
  ];

  els.alertsGrid.innerHTML = '';
  let hasAlert = false;

  alerts.forEach(a => {
    if (a.value == null) return;
    const isOB = a.status === 'overbought';
    const isOS = a.status === 'oversold';
    const isStrong = a.status === 'strong';
    if (isOB || isOS) hasAlert = true;

    const lightClass = isOB ? 'ob' : isOS ? 'os' : 'neutral';
    const rowClass = isOB ? 'overbought' : isOS ? 'oversold' : '';
    const statusClass = isOB ? 'ob' : isOS ? 'os' : 'neutral';
    const statusText = isOB ? 'OB' : isOS ? 'OS' : a.name === 'ADX' ? (a.status === 'strong' ? 'STRONG' : a.status === 'weak' ? 'WEAK' : 'MOD') : '—';

    const row = document.createElement('div');
    row.className = `alert-row ${rowClass}`;
    row.innerHTML = `
      <div class="alert-light ${lightClass}"></div>
      <span class="alert-indicator-name">${a.name}</span>
      <span class="alert-status-label ${statusClass}">${statusText}</span>
      <span class="alert-value">${a.display || '--'}</span>`;
    els.alertsGrid.appendChild(row);
  });

  // Update status badge
  if (hasAlert) {
    els.alertStatusBadge.textContent = 'ALERT';
    els.alertStatusBadge.className = 'panel-badge alert-badge-status alert-active';
  } else {
    els.alertStatusBadge.textContent = 'MONITORING';
    els.alertStatusBadge.className = 'panel-badge alert-badge-status';
  }
}

function renderLearning(data) {
  const v = data.verification;
  if (!v || !v.learning_state) return;
  const ls = v.learning_state;
  
  els.lRetrains.textContent = ls.train_count || 0;
  els.lSmooth.textContent = ls.smoothing_alpha ? ls.smoothing_alpha.toFixed(3) : '--';
  els.lConf.textContent = ls.confidence_threshold ? ls.confidence_threshold.toFixed(3) : '--';
  
  if (ls.retrain_flagged && ls.retrain_flagged.length > 0) {
    els.lPulse.style.background = 'var(--red)';
    els.lPulse.style.boxShadow = '0 0 8px var(--red)';
  } else {
    els.lPulse.style.background = 'var(--purple)';
    els.lPulse.style.boxShadow = '0 0 8px var(--purple)';
  }
}

function renderBootStatus(data) {
  if (!els.bootTime) return;
  const boot = data.boot_status || {};
  if (boot.ready && boot.boot_seconds != null) {
    els.bootTime.textContent = formatDuration(boot.boot_seconds);
  } else if (boot.uptime_seconds != null) {
    els.bootTime.textContent = formatDuration(boot.uptime_seconds);
  } else {
    els.bootTime.textContent = '---';
  }
}

function renderRuntimeStatus(data) {
  const backtest = data.backtest_status || {};
  const relearn = data.relearn_status || {};
  if (els.backtestStatus) {
    const pct = backtest.progress != null ? `${Math.round(backtest.progress * 100)}%` : '';
    els.backtestStatus.textContent = backtest.running
      ? `${pct} ${backtest.message || 'Running'}`
      : (backtest.message || backtest.phase || 'Idle');
    els.backtestStatus.title = backtest.error || backtest.message || '';
  }
  if (els.relearnStatus) {
    const pct = relearn.progress != null ? `${Math.round(relearn.progress * 100)}%` : '';
    els.relearnStatus.textContent = relearn.running
      ? `${pct} ${relearn.message || 'Running'}`
      : (relearn.message || relearn.phase || 'Idle');
    els.relearnStatus.title = relearn.error || relearn.message || '';
  }
  if (els.relearnButton) {
    const locked = !!relearn.running;
    els.relearnButton.disabled = locked;
    els.relearnButton.textContent = locked ? 'Relearning...' : 'Relearn Models';
  }
  if (els.backtestButton) {
    const locked = !!backtest.running;
    els.backtestButton.disabled = locked;
    els.backtestButton.textContent = locked ? 'Backtesting...' : 'Run Backtest';
  }
}

function renderPlainAnalysis(data) {
  if (!els.analysisView) return;
  lastPlainData = data;

  const preds = data.predictions || [];
  
  renderGlobalPulse(preds, data);
  renderForecastPulse(data, preds);

  let activePred = preds.find(p => p.horizon === activePlainTF);
  if (!activePred && preds.length > 0) activePred = preds[0];

  const price = data.price || 0;
  const v = data.verification || {};
  const errors = v.error_summary || {};
  const activeAcc = v.accuracy?.[activePlainTF] || null;

  renderDecisionCockpit(data, activePred, activeAcc);
  renderFsrPpoChallenger(data, activePred);
  renderSignalFlow(data, activePred, activeAcc);
  renderPlainVerdict(activePred, data, errors, activeAcc);
  renderQuantileCurve(activePred);
  renderDecisionGuide(data, activePred, activeAcc);
  renderTrustPanel(data, activePred, activeAcc);
  renderActionReasons(data, activePred, activeAcc);
  renderPlainRates(errors, activeAcc, activePred);
  renderActionMetrics(data.verification || {}, activeAcc);
  renderAvoidSuccess(activeAcc);
  renderRiskAndLab(data);
  renderPlainSignalCards(data, activePred);
  renderPlainErrorRows((v.recent || []).filter(e => e.horizon === activePlainTF));
  renderSupportResistance(data, price);
  renderIndicatorAnalysis(data, activePred);
}

function formatUsd(value, digits = 2) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '--';
  return `$${n.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

function formatSignedUsd(value, digits = 2) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '--';
  const sign = n > 0 ? '+' : n < 0 ? '-' : '';
  return `${sign}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

function getFinalAction(p) {
  if (!p) return 'AVOID';
  const d = p.direction || p.signal || 'NEUTRAL';
  if (d === 'UP') return 'BUY';
  if (d === 'DOWN') return 'SELL';
  return 'AVOID';
}

function getSignedExpectedMove(p) {
  if (!p) return 0;
  if (p.targetPrice != null && p.lastPrice != null) return Number(p.targetPrice) - Number(p.lastPrice);
  if (p.targetPrice != null && p.binancePrice != null) return Number(p.targetPrice) - Number(p.binancePrice);
  const move = Math.abs(Number(p.expectedMove || 0));
  if ((p.direction || p.rawDirection) === 'DOWN') return -move;
  return move;
}

function getKronosAtHorizon(data, horizon) {
  const forecasts = data.kronos_forecasts || [];
  const current = Number(data.price || 0);
  if (!forecasts.length || !current) return null;
  const idx = Math.min(forecasts.length, Math.max(1, Number(horizon) || 1)) - 1;
  const row = forecasts[idx];
  const close = Number(row && row.close);
  if (!Number.isFinite(close)) return null;
  const move = close - current;
  const pct = current ? move / current : 0;
  const direction = pct > 0.0003 ? 'UP' : pct < -0.0003 ? 'DOWN' : 'FLAT';
  return { price: close, move, direction };
}

function calculateDecisionTrust(data, best, activeAcc) {
  if (!best) return { score: 0, label: 'Waiting', tone: 'neutral' };
  const total = activeAcc?.total || 0;
  const accuracy = activeAcc?.accuracy || 0;
  const priceMatch = activeAcc?.price_match_rate || 0;
  const conf = best.confidence || 0;
  const agreement = best.agreement || 0;
  const metaTrust = best.metaTrust ?? 0.5;
  const drift = String(data.drift?.level || data.drift?.status || 'normal').toLowerCase();
  const feedStale = !!data.feed_health?.stale;

  let score = 0;
  score += Math.min(conf, 1) * 30;
  score += Math.min(agreement, 1) * 20;
  score += total >= 500 ? 20 : total >= 100 ? 14 : Math.max(0, total / 100) * 8;
  score += total ? Math.min(Math.max(accuracy, 0), 1) * 15 : 0;
  score += total ? Math.min(Math.max(priceMatch, 0), 1) * 7 : 0;
  score += Math.min(Math.max(metaTrust, 0), 1) * 8;
  if (best.meta_filtered || best.quality_filtered || best.regime_filtered) score -= 25;
  if (feedStale) score -= 12;
  if (drift.includes('high')) score -= 10;

  score = Math.max(0, Math.min(100, Math.round(score)));
  let label = 'Low trust';
  let tone = 'neutral';
  if (score >= 80) { label = 'High trust'; tone = 'up'; }
  else if (score >= 65) { label = 'Good trust'; tone = 'up'; }
  else if (score >= 45) { label = 'Caution'; tone = 'neutral'; }
  else { label = 'Do not trust yet'; tone = 'down'; }
  return { score, label, tone };
}

function renderDecisionCockpit(data, best, activeAcc) {
  if (!els.decisionPrimaryAction) return;
  if (!best) {
    els.decisionPrimaryAction.textContent = 'WAIT';
    els.decisionPrimaryAction.className = 'neutral';
    els.decisionPrimaryMessage.textContent = 'No usable forecast has arrived yet.';
    if (els.decisionChecklist) els.decisionChecklist.innerHTML = '';
    return;
  }

  const action = getFinalAction(best);
  const dir = best.direction || 'NEUTRAL';
  const raw = best.rawDirection || dir;
  const tone = action === 'BUY' ? 'up' : action === 'SELL' ? 'down' : 'neutral';
  const horizon = best.horizon || activePlainTF;
  const confidence = Math.round((best.confidence || 0) * 100);
  const agreement = Math.round((best.agreement || 0) * 100);
  const signedMove = getSignedExpectedMove(best);
  const target = (data.price || 0) + signedMove;
  const absMove = Math.abs(Math.round(signedMove || best.expectedMove || 0));
  const trust = calculateDecisionTrust(data, best, activeAcc);
  const expectancy = Number(best.expectancy_usd || 0);
  const k = getKronosAtHorizon(data, horizon);
  const policy = best.thresholdPolicy ||
    (data.signal_policy?.by_regime || {})[horizon] ||
    (data.signal_policy?.by_regime || {})[String(horizon)] ||
    (data.signal_policy?.by_horizon || {})[horizon] ||
    (data.signal_policy?.by_horizon || {})[String(horizon)] ||
    {};
  const sr = getLiveSupportResistance(data, data.price || 0);
  const cd = ((data.scoreboard || {})[horizon] || (data.scoreboard || {})[String(horizon)] || {}).confluenceDetail || best.confluenceDetail || {};
  const feedStale = !!data.feed_health?.stale;
  const directional = ['UP', 'DOWN'].includes(dir);

  const actionTitle = action === 'BUY'
    ? 'BUY SETUP'
    : action === 'SELL'
      ? 'SELL SETUP'
      : 'WAIT / NO TRADE';
  const primaryMessage = action === 'BUY'
    ? `${horizon}m setup leans UP. Only act if your own risk limit allows it and confirmation stays green.`
    : action === 'SELL'
      ? `${horizon}m setup leans DOWN. Only act if your own risk limit allows it and confirmation stays red.`
      : `${horizon}m signal is not clean enough. The tool is choosing capital preservation over forcing a trade.`;

  const range = best.expectedMoveRange;
  const rangeText = range && range.low != null && range.high != null
    ? `Move range: $${Math.round(range.low).toLocaleString()} to $${Math.round(range.high).toLocaleString()}`
    : `Expected move: about $${absMove.toLocaleString()}`;
  const targetText = data.price ? formatUsd(target, 2) : '--';
  const targetDetail = action === 'AVOID'
    ? `No trade target. If watched only, model zone is near ${targetText}. ${rangeText}.`
    : `${action === 'BUY' ? 'Upside' : 'Downside'} target zone near ${targetText}. ${rangeText}.`;

  let why = 'Mixed evidence';
  let whyDetail = `Confidence ${confidence}%, agreement ${agreement}%, raw model lean ${raw}.`;
  const premium = Number(data.derivatives?.coinbase_premium || 0);
  const imbalance = Number(data.order_flow?.imbalance || 0);
  if (best.skipReason || best.qualityMessage) {
    why = action === 'AVOID' ? 'Safety gate blocked it' : 'Safety gate checked';
    whyDetail = best.skipReason || best.qualityMessage;
  } else if (Math.abs(premium) >= 5) {
    why = premium > 0 ? 'Spot demand supports upside' : 'Spot demand is weak';
    whyDetail = premium > 0
      ? `Coinbase is about $${premium.toFixed(2)} above Binance, which can support BUY/UP.`
      : `Coinbase is about $${Math.abs(premium).toFixed(2)} below Binance, which can support SELL/DOWN or WAIT.`;
  } else if (Math.abs(imbalance) >= 0.08) {
    why = imbalance > 0 ? 'Order book has more bids' : 'Order book has more asks';
    whyDetail = imbalance > 0
      ? 'More visible bid liquidity can support price or slow a drop.'
      : 'More visible ask liquidity can cap price or support downside.';
  } else if ((best.agreement || 0) >= 0.7) {
    why = 'Models mostly agree';
    whyDetail = `The model group agreement is ${agreement}%, which is cleaner than a split vote.`;
  }

  let invalid = 'Evidence does not align';
  let invalidDetail = 'Do not act until models, flow, regime, and trust score point the same way.';
  let next = 'Wait for alignment';
  let nextDetail = 'Best confirmation is model direction, flow, and regime agreeing at the same time.';
  if (action === 'BUY') {
    invalid = sr?.resistance ? 'Resistance rejection' : 'Upside fails';
    invalidDetail = sr?.resistance
      ? `If BTC rejects near ${formatUsd(sr.resistance, 0)} or flow flips negative, ignore the BUY setup.`
      : 'If price cannot keep moving up and buyer flow fades, ignore the BUY setup.';
    next = 'Break and hold higher';
    nextDetail = sr?.resistance
      ? `Best confirmation is a clean push through ${formatUsd(sr.resistance, 0)} with bids still stronger.`
      : 'Best confirmation is price rising while Coinbase premium and order-book bids stay supportive.';
  } else if (action === 'SELL') {
    invalid = sr?.support ? 'Support holds' : 'Downside fails';
    invalidDetail = sr?.support
      ? `If BTC holds near ${formatUsd(sr.support, 0)} or flow flips positive, ignore the SELL setup.`
      : 'If price cannot keep moving down and seller flow fades, ignore the SELL setup.';
    next = 'Break and hold lower';
    nextDetail = sr?.support
      ? `Best confirmation is a clean push below ${formatUsd(sr.support, 0)} with asks still stronger.`
      : 'Best confirmation is price falling while Coinbase premium and order-book flow stay weak.';
  }

  let risk = 'Normal caution';
  let riskDetail = 'Direction and dollar target are scored separately. A correct side can still miss the price target.';
  if (feedStale) {
    risk = 'Feed stale';
    riskDetail = 'Live-signal snapshots are lagging, so the safest action is WAIT until feed health turns fresh.';
  } else if (best.meta_filtered || best.quality_filtered || best.regime_filtered) {
    risk = 'Filtered signal';
    riskDetail = best.qualityMessage || best.skipReason || 'A safety filter reduced this to WAIT.';
  } else if ((activeAcc?.total || 0) < 100) {
    risk = 'Young evidence';
    riskDetail = `Only ${activeAcc?.total || 0}/100 resolved ${horizon}m examples. Treat accuracy as early.`;
  } else if (expectancy < 0 && action !== 'AVOID') {
    risk = 'Negative expectancy';
    riskDetail = `After cost assumptions, this setup estimates about -$${Math.abs(expectancy).toFixed(2)} value.`;
  } else if ((best.quantileSpread || 0) >= 3) {
    risk = 'Wide target range';
    riskDetail = 'The expected move range is too wide, so price target confidence is weak.';
  }

  const flowAgree = cd.flow_agree != null
    ? !!cd.flow_agree
    : directional && ((dir === 'UP' && (imbalance > 0.04 || premium > 3)) || (dir === 'DOWN' && (imbalance < -0.04 || premium < -3)));
  const regimeOk = cd.regime_favorable != null ? !!cd.regime_favorable : !best.regime_filtered;
  const gates = [
    { label: 'Models', ok: cd.models_agree != null ? !!cd.models_agree : (best.agreement || 0) >= (best.agreementThreshold || 0.67), detail: `${agreement}% agreement` },
    { label: 'Flow', ok: !!flowAgree, detail: `book ${(imbalance * 100).toFixed(1)}%, premium ${premium >= 0 ? '+' : ''}$${premium.toFixed(2)}` },
    { label: 'Regime', ok: !!regimeOk, detail: data.regime?.regime || 'unknown' },
    { label: 'Live record', ok: (activeAcc?.total || 0) >= 100 && (activeAcc?.accuracy || 0) >= 0.5, neutral: (activeAcc?.total || 0) < 100, detail: activeAcc?.total ? `${((activeAcc.accuracy || 0) * 100).toFixed(0)}% / ${activeAcc.total}n` : '0n' },
    { label: 'Data fresh', ok: !feedStale, detail: feedStale ? 'snapshot lag' : 'live' },
  ];
  const okCount = gates.filter(g => g.ok).length;
  const rating = okCount >= 5 && trust.score >= 70 ? 'A' : okCount >= 4 && trust.score >= 55 ? 'B' : okCount >= 3 ? 'C' : 'WATCH';

  els.decisionPrimaryAction.textContent = `${actionTitle} - ${rating}`;
  els.decisionPrimaryAction.className = tone;
  els.decisionPrimaryMessage.textContent = primaryMessage;
  if (els.decisionMainCard) els.decisionMainCard.className = `decision-main-card ${tone}`;
  els.decisionCockpitAction.textContent = actionTitle;
  els.decisionCockpitActionDetail.textContent = `Selected timeframe: ${horizon}m. Final ensemble: ${dir}. Raw lean: ${raw}.`;
  els.decisionCockpitTarget.textContent = targetText;
  els.decisionCockpitTargetDetail.textContent = targetDetail;
  els.decisionCockpitTrust.textContent = `${trust.score}%`;
  const policyDetail = policy.ready
    ? ` Learned bar ${Math.round((policy.threshold || 0) * 100)}%, raw precision ${Math.round((policy.precision || 0) * 100)}%.`
    : ' Adaptive signal policy is still collecting resolved examples.';
  els.decisionCockpitTrustDetail.textContent = `${trust.label}. ${okCount}/6 evidence checks are green.${policyDetail}`;
  els.decisionCockpitRisk.textContent = risk;
  els.decisionCockpitRiskDetail.textContent = riskDetail;
  els.decisionCockpitWhy.textContent = why;
  els.decisionCockpitWhyDetail.textContent = whyDetail;
  els.decisionCockpitInvalid.textContent = invalid;
  els.decisionCockpitInvalidDetail.textContent = invalidDetail;
  els.decisionCockpitNext.textContent = next;
  els.decisionCockpitNextDetail.textContent = nextDetail;

  if (els.decisionChecklist) {
    els.decisionChecklist.innerHTML = gates.map(g => {
      const cls = g.ok ? 'ok' : g.neutral ? 'watch' : 'bad';
      const mark = g.ok ? 'PASS' : g.neutral ? 'WAIT' : 'BLOCK';
      return `<div class="decision-check ${cls}">
        <span>${g.label}</span>
        <strong>${mark}</strong>
        <p>${g.detail}</p>
      </div>`;
    }).join('');
  }
}

function renderFsrPpoChallenger(data, activePred) {
  if (!els.fsrPpoGrid) return;
  const block = data.fsr_ppo || {};
  const summary = data.fsr_ppo_summary || {};
  const horizon = activePred?.horizon || activePlainTF;
  const rec = (block.by_horizon || {})[horizon] || (block.by_horizon || {})[String(horizon)] || block.best || null;
  const fsr = block.fsr || {};
  const status = block.status || {};

  // FSR-PPO mothballed in v6 (R3): the backend stub sets status.enabled=false. Show
  // a clear mothballed state instead of a misleading "waiting".
  if (status.enabled === false || (!rec && block.summary && /mothball/i.test(block.summary))) {
    els.fsrPpoGrid.innerHTML = `
      <div class="fsr-ppo-card neutral">
        <span>Strategy call</span>
        <strong>MOTHBALLED</strong>
        <p>The PPO challenger is paused in v6 — a strategy layer is premature until the core model proves its edge. Re-enable with BTC_FSR_PPO=1.</p>
      </div>`;
    if (els.fsrPpoRecent) els.fsrPpoRecent.innerHTML = '';
    return;
  }
  if (!rec) {
    els.fsrPpoGrid.innerHTML = `
      <div class="fsr-ppo-card neutral">
        <span>Strategy call</span>
        <strong>WAITING</strong>
        <p>The PPO challenger will appear after the backend sends ensemble predictions.</p>
      </div>`;
    if (els.fsrPpoRecent) els.fsrPpoRecent.innerHTML = '';
    return;
  }

  const action = rec.action || 'AVOID';
  const side = rec.side || 'AVOID';
  const tone = side === 'BUY' ? 'up' : side === 'SELL' ? 'down' : 'neutral';
  const size = Number(rec.size_fraction || 0);
  const confidence = Math.round(Number(rec.confidence || 0) * 100);
  const quality = Math.round(Number(fsr.signal_quality || 0) * 100);
  const noise = Math.round(Number(fsr.noise_ratio || 0) * 100);
  const reward = Number(rec.expected_reward_usd || 0);
  const total = Number(summary.total || 0);
  const avgReward = Number(summary.avg_reward_usd || 0);
  const acc = total ? Math.round(Number(summary.accuracy || 0) * 100) : null;

  const actionText = action === 'AVOID'
    ? 'AVOID'
    : `${action.replace('_', ' ')}`;
  const sizeText = action === 'AVOID'
    ? 'No trade'
    : `${Math.round(size * 100)}% test size`;
  const proofText = total
    ? `${acc}% hit over ${total} resolved, avg reward ${formatSignedUsd(avgReward)}`
    : 'No resolved PPO examples yet';

  els.fsrPpoGrid.innerHTML = `
    <div class="fsr-ppo-card ${tone}">
      <span>PPO challenger call</span>
      <strong>${actionText}</strong>
      <p>${rec.reason || 'Waiting for clean denoised signal and positive reward.'}</p>
    </div>
    <div class="fsr-ppo-card">
      <span>Suggested size</span>
      <strong>${sizeText}</strong>
      <p>This is paper-policy sizing only. It does not override the main ensemble.</p>
    </div>
    <div class="fsr-ppo-card">
      <span>Denoised signal</span>
      <strong>${quality}% quality</strong>
      <p>Noise ${noise}%, clean momentum ${Number(fsr.clean_momentum || 0).toFixed(2)}, Hurst ${Number(fsr.hurst || 0.5).toFixed(2)}.</p>
    </div>
    <div class="fsr-ppo-card">
      <span>Expected reward</span>
      <strong style="color:${reward > 0 ? 'var(--green)' : reward < 0 ? 'var(--red)' : 'var(--text-primary)'}">${formatSignedUsd(reward)}</strong>
      <p>${rec.risk_note || 'Reward includes cost and overtrading penalties.'}</p>
    </div>
    <div class="fsr-ppo-card">
      <span>Live proof</span>
      <strong>${proofText}</strong>
      <p>Use this only as a challenger until it proves positive live reward.</p>
    </div>
    <div class="fsr-ppo-card">
      <span>Mode</span>
      <strong>${status.mode || 'waiting'}</strong>
      <p>${block.summary || 'FSR-PPO challenger is active after backend restart.'}</p>
    </div>
  `;

  if (els.fsrPpoRecent) {
    const recent = summary.recent || [];
    if (!recent.length) {
      els.fsrPpoRecent.innerHTML = '<div class="plain-empty">PPO reward history will appear after predictions resolve.</div>';
      return;
    }
    els.fsrPpoRecent.innerHTML = `
      <div class="fsr-ppo-recent-title">Recent PPO paper-policy outcomes</div>
      ${recent.slice(0, 6).map(r => {
        const resolved = !!r.resolved;
        const rr = Number(r.reward_usd || 0);
        const cls = !resolved ? 'neutral' : rr >= 0 ? 'up' : 'down';
        return `<div class="fsr-ppo-row ${cls}">
          <span>${r.horizon}m</span>
          <strong>${r.action || 'AVOID'}</strong>
          <span>${resolved ? formatSignedUsd(rr) : 'open'}</span>
          <small>${r.reason || ''}</small>
        </div>`;
      }).join('')}
    `;
  }
}

function renderSignalFlow(data, best, activeAcc) {
  if (!els.signalFlowGrid) return;
  if (!best) {
    els.signalFlowGrid.innerHTML = '<div class="signal-flow-card neutral"><span>Waiting</span><strong>No selected forecast yet</strong><p>The backend has not sent a usable prediction payload.</p></div>';
    return;
  }

  const raw = best.rawDirection || best.direction || 'NEUTRAL';
  const finalDir = best.direction || 'NEUTRAL';
  const action = getFinalAction(best);
  const conf = Math.round((best.confidence || 0) * 100);
  const agreement = Math.round((best.agreement || 0) * 100);
  const signedMove = getSignedExpectedMove(best);
  const modelTarget = (data.price || 0) + signedMove;
  const accText = activeAcc && activeAcc.total
    ? `${Math.round((activeAcc.accuracy || 0) * 100)}% over ${activeAcc.total} resolved`
    : 'Not enough resolved examples yet';
  const targetDetail = action === 'AVOID'
    ? `The model still estimates a possible zone near ${formatUsd(modelTarget)}, but the safety gate says do not act on it.`
    : `If acted on, the model zone is near ${formatUsd(modelTarget)}.`;
  const skip = best.skipReason || best.qualityMessage || (action === 'AVOID' ? 'Confidence, expectancy, regime, or model trust did not clear the safety gate.' : 'Signal passed the current safety gate.');

  const cards = [
    {
      tone: raw === 'UP' ? 'up' : raw === 'DOWN' ? 'down' : 'neutral',
      label: '1. Ensemble lean',
      value: `${raw} (${conf}% confidence)`,
      detail: `This is the model group before/around safety checks. Agreement is ${agreement}%.`
    },
    {
      tone: action === 'BUY' ? 'up' : action === 'SELL' ? 'down' : 'neutral',
      label: '2. Final action',
      value: `${action}${finalDir !== raw ? ` (filtered from ${raw})` : ''}`,
      detail: skip
    },
    {
      tone: raw === 'UP' ? 'up' : raw === 'DOWN' ? 'down' : 'neutral',
      label: '3. Expected move',
      value: signedMove ? `${signedMove >= 0 ? '+' : '-'}$${Math.abs(Math.round(signedMove)).toLocaleString()} → ${formatUsd(modelTarget, 0)}` : 'flat / no edge',
      detail: 'The model\'s typical move size for current conditions (not a path guarantee). Used to judge whether a move can reach the line.'
    },
    {
      tone: 'neutral',
      label: '4. Result scoring',
      value: accText,
      detail: action === 'AVOID'
        ? 'AVOID is scored by whether skipping protected you or avoided a bad raw lean. It is not counted like a normal UP/DOWN bet.'
        : `${targetDetail} Direction and dollar-target error are scored separately.`
    }
  ];

  els.signalFlowGrid.innerHTML = cards.map(c => `
    <div class="signal-flow-card ${c.tone}">
      <span>${c.label}</span>
      <strong>${c.value}</strong>
      <p>${c.detail}</p>
    </div>
  `).join('');
}

function renderGlobalPulse(preds, data) {
  if (!els.globalPulseGrid) return;
  const curPrice = (data && data.price) || 0;
  els.globalPulseGrid.innerHTML = preds.map(p => {
    const dirClass = p.direction === 'UP' ? 'up' : p.direction === 'DOWN' ? 'down' : 'neutral';
    const conf = (p.confidence * 100).toFixed(0);
    const raw = p.rawDirection || p.direction || 'NEUTRAL';
    const action = getFinalAction(p);
    const signedMove = getSignedExpectedMove(p);
    const expPrice = curPrice
      ? (curPrice + signedMove).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
      : '--';
    const actionClass = action === 'BUY' ? 'up' : action === 'SELL' ? 'down' : 'neutral';
    const filtered = raw !== p.direction && raw !== 'NEUTRAL';
    return `<div class="pulse-card ${dirClass}">
      <span class="pulse-tf">${p.horizon}m</span>
      <span class="pulse-dir">${dirArrow(p.direction)} Ensemble ${p.direction}</span>
      <span class="pulse-signal ${actionClass}">Action: ${action}</span>
      <span class="pulse-conf">${conf}% conf</span>
      <span class="pulse-target">→ $${expPrice}</span>
    </div>`;
  }).join('');
}

function renderForecastPulse(data, preds) {
  if (!els.forecastPulseGrid || !els.forecastCurrentPrice) return;
  
  const forecastPrice = data.price;
  if (forecastPrice) {
    els.forecastCurrentPrice.textContent = `$${forecastPrice.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
  }

  if (els.kronosStatus) {
    const ks = data.kronos_status || {};
    const label = ks.loaded ? 'Kronos model active' : 'Fallback projection active';
    els.kronosStatus.textContent = `Forecast engine status: ${label}. ${ks.message || ''}`;
    els.kronosStatus.className = `kronos-status ${ks.loaded ? 'active' : 'fallback'}`;
  }
  
  const horizons = [1, 3, 5, 7, 10, 15];
  els.forecastPulseGrid.innerHTML = horizons.map(h => {
    const k = getKronosAtHorizon(data, h);
    if (!k) {
      return `<div class="pulse-card neutral">
        <span class="pulse-tf">${h}m Kronos</span>
        <span class="pulse-dir">waiting</span>
        <span class="pulse-target">--</span>
      </div>`;
    }
    const dirClass = k.direction === 'UP' ? 'up' : k.direction === 'DOWN' ? 'down' : 'neutral';
    const moveTxt = `${k.move >= 0 ? '+' : '-'}$${Math.abs(Math.round(k.move)).toLocaleString()}`;
    return `<div class="pulse-card ${dirClass}">
      <span class="pulse-tf">${h}m Kronos</span>
      <span class="pulse-dir">${dirArrow(k.direction)} ${k.direction}</span>
      <span class="pulse-target">${formatUsd(k.price)}</span>
      <span class="pulse-sub">${moveTxt} path move</span>
    </div>`;
  }).join('');
}

function getBestPrediction(preds) {
  if (!preds || preds.length === 0) return null;
  let best = preds[0];
  for (const p of preds) {
    if (p.confidence > best.confidence) best = p;
  }
  return best;
}

function renderQuantileCurve(best) {
  if (!els.curveContainer) return;
  if (!best || !best.expectedMoveRange || best.expectedMoveRange.median == null) {
    els.curveContainer.innerHTML = '<div style="color: var(--text-muted); text-align: center; padding: 20px;">No variance data available</div>';
    if (els.curveStatus) els.curveStatus.textContent = '--';
    return;
  }

  const range = best.expectedMoveRange;
  const spread = best.quantileSpread || 0;
  
  let statusText = 'Normal Variance';
  let color = 'var(--gold)';
  let strokeColor = '#ffd700';

  if (spread < 2.0) {
    statusText = 'High Target Confidence';
    color = 'var(--green)';
    strokeColor = '#00e676';
  } else if (spread >= 3.0) {
    statusText = 'Extreme Uncertainty (Avoid)';
    color = 'var(--red)';
    strokeColor = '#ff1744';
  } else {
    statusText = 'Caution: Wide Variance';
  }

  if (els.curveStatus) {
    els.curveStatus.textContent = statusText;
    els.curveStatus.style.color = color;
  }

  const width = els.curveContainer.clientWidth || 300;
  const height = 100;
  let path = `M 0 ${height} `;
  
  const stdDev = Math.max(0.5, spread); 
  for (let x = 0; x <= width; x += 5) {
    const normX = (x - width/2) / (width/4);
    const y = Math.exp(-(normX*normX) / (2 * stdDev * stdDev)) / (stdDev * Math.sqrt(2 * Math.PI));
    const drawY = height - (y * height * 1.5);
    path += `L ${x} ${drawY} `;
  }
  
  const midX = width / 2;
  const offset = (width / 4) * stdDev;

  const svg = `<svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
    <path d="${path}" fill="none" stroke="${strokeColor}" stroke-width="3" />
    <line x1="${midX}" y1="0" x2="${midX}" y2="${height}" stroke="var(--text-muted)" stroke-dasharray="4" />
    <text x="${midX}" y="${height-5}" fill="var(--text-primary)" text-anchor="middle" font-size="12">$${Math.round(range.median || 0)}</text>
    <line x1="${Math.max(0, midX - offset)}" y1="20" x2="${Math.max(0, midX - offset)}" y2="${height}" stroke="var(--text-tertiary)" stroke-dasharray="2" />
    <text x="${Math.max(0, midX - offset)}" y="15" fill="var(--text-muted)" text-anchor="middle" font-size="10">$${Math.round(range.low || 0)}</text>
    <line x1="${Math.min(width, midX + offset)}" y1="20" x2="${Math.min(width, midX + offset)}" y2="${height}" stroke="var(--text-tertiary)" stroke-dasharray="2" />
    <text x="${Math.min(width, midX + offset)}" y="15" fill="var(--text-muted)" text-anchor="middle" font-size="10">$${Math.round(range.high || 0)}</text>
  </svg>`;

  els.curveContainer.innerHTML = svg;
}

function renderAvoidSuccess(activeAcc) {
  if (!els.avoidTotal) return;
  if (!activeAcc || !activeAcc.avoid) {
    els.avoidTotal.textContent = '--';
    els.avoidHits.textContent = '--';
    els.avoidRate.textContent = '--';
    els.avoidCapitalSaved.textContent = '--';
    return;
  }
  
  const avoid = activeAcc.avoid;
  els.avoidTotal.textContent = avoid.total || 0;
  els.avoidHits.textContent = avoid.hits || 0;
  els.avoidRate.textContent = avoid.total ? `${(avoid.accuracy * 100).toFixed(1)}%` : '--';
  
  const saved = avoid.capital_saved_usd || 0;
  els.avoidCapitalSaved.textContent = `$${saved.toLocaleString()}`;
  if (saved > 0) els.avoidCapitalSaved.style.color = 'var(--green)';
  else els.avoidCapitalSaved.style.color = 'var(--text-primary)';
}

function renderPlainVerdict(best, data, errors, activeAcc) {
  if (!best) {
    els.analysisVerdict.textContent = 'Waiting for forecast';
    els.analysisMeaning.textContent = 'The model is collecting enough live data before giving a readable market summary.';
    els.analysisImpact.textContent = 'Impact: wait';
    els.analysisConfidence.textContent = '--';
    return;
  }

  const dir = best.direction;
  const conf = best.confidence || 0;
  const agreement = best.agreement || 0;
  const move = Math.round(best.expectedMove || 0);
  const horizon = best.horizon;
  const total = activeAcc?.total || 0;
  const accuracy = total ? activeAcc.accuracy : errors.direction_accuracy || 0;
  const maturity = best.qualityMessage || (total < 100
    ? `Only ${total}/100 verified ${horizon}m predictions. Treat this as an early read.`
    : `${horizon}m has enough verified outcomes for decision support.`);

  const action = dir === 'UP' ? 'Price may move up' : dir === 'DOWN' ? 'Price may move down' : 'No clear direction';
  const impact = dir === 'UP'
    ? 'Impact: possible buy pressure. Avoid chasing if confidence or agreement is weak.'
    : dir === 'DOWN'
      ? 'Impact: possible sell pressure. Watch for support before assuming a larger drop.'
      : 'Impact: market is mixed. Waiting is usually safer than forcing a trade.';

  els.analysisVerdict.textContent = action;
  els.analysisVerdict.className = `plain-verdict-title ${dir.toLowerCase()}`;
  els.analysisMeaning.textContent = `${horizon}m forecast expects about $${move.toLocaleString()} of movement. Confidence is ${(conf * 100).toFixed(0)}%, model agreement is ${(agreement * 100).toFixed(0)}%, and ${total ? `this horizon's live direction accuracy is ${(accuracy * 100).toFixed(0)}% from ${total} resolved calls` : 'this horizon does not have enough resolved calls yet'}. ${maturity}`;
  els.analysisImpact.textContent = impact;
  els.analysisConfidence.textContent = `${(conf * 100).toFixed(0)}%`;
}

function renderPlainRates(errors, activeAcc, best) {
  const useHorizon = activeAcc && activeAcc.total > 0;
  const stats = useHorizon ? activeAcc : errors;
  // Overall resolved sample (includes AVOID/NEUTRAL outcomes). Since the conviction +
  // expectancy gate forces most calls to AVOID, directional_total is often ~0 — so we
  // show the overall hit accuracy here and reserve the dollar-error cards for the
  // (rarer) directional UP/DOWN calls, which is the only place a price error is defined.
  const total = useHorizon ? (stats.total || 0) : (stats.total || 0);
  const dirTotal = useHorizon ? (stats.directional_total || 0) : (stats.total || 0);
  const overallAcc = useHorizon ? (stats.accuracy || 0) : (stats.direction_accuracy || stats.accuracy || 0);
  const directionAcc = dirTotal > 0
    ? (useHorizon ? (stats.directional_accuracy || 0) : (stats.direction_accuracy || 0))
    : overallAcc;

  els.analysisAccuracy.textContent = total ? `${(directionAcc * 100).toFixed(1)}%` : '--';
  els.analysisMissRate.textContent = total ? `${((1 - directionAcc) * 100).toFixed(1)}%` : '--';

  if (els.analysisExpectancy) {
    els.analysisExpectancy.textContent = best && best.expectancy_usd != null
      ? (best.expectancy_usd > 0 ? `+$${best.expectancy_usd.toFixed(2)}` : `-$${Math.abs(best.expectancy_usd).toFixed(2)}`)
      : '--';
    if (best && best.expectancy_usd != null) {
      els.analysisExpectancy.style.color = best.expectancy_usd > 0 ? 'var(--green)' : 'var(--red)';
    } else {
      els.analysisExpectancy.style.color = 'var(--text-primary)';
    }
  }

  // Dollar-error cards only make sense for actual directional calls.
  els.analysisAvgError.textContent = dirTotal ? `$${Math.round(stats.avg_move_error_usd || 0).toLocaleString()}` : '— (no UP/DOWN calls yet)';
  els.analysisUpError.textContent = (stats.up_total || dirTotal) ? `$${Math.round(stats.up_avg_move_error_usd || 0).toLocaleString()}` : '—';
  els.analysisDownError.textContent = (stats.down_total || dirTotal) ? `$${Math.round(stats.down_avg_move_error_usd || 0).toLocaleString()}` : '—';
}

function renderRiskAndLab(data) {
  if (els.regimeHealthState) {
    els.regimeHealthState.textContent = data.regime ? data.regime.regime : '--';
  }
  
  if (data.simulator_metrics) {
    if (els.regimeHealthPf) els.regimeHealthPf.textContent = data.simulator_metrics.profit_factor?.toFixed(2) || '--';
    if (els.regimeHealthDd) els.regimeHealthDd.textContent = data.simulator_metrics.max_drawdown_pct ? `${data.simulator_metrics.max_drawdown_pct.toFixed(2)}%` : '--';
  } else {
    if (els.regimeHealthPf) els.regimeHealthPf.textContent = '--';
    if (els.regimeHealthDd) els.regimeHealthDd.textContent = '--';
  }
  
  const ab = data.ab_test;
  if (ab && els.labPrimaryAcc) {
    const pAcc = ab.primary ? (ab.primary.accuracy * 100).toFixed(1) : '--';
    els.labPrimaryAcc.textContent = pAcc !== '--' ? `${pAcc}% (${ab.primary.verified} runs)` : '--';
    // P3.2 — the challenger ensemble is only meaningful once it is actually trained.
    // When the backend reports enabled:false (reason: challenger_not_trained) we must
    // NOT show "0.0% (0 runs)" — that falsely implies a live A/B comparison is running.
    if (ab.enabled === false) {
      const reasonLabel = ab.reason === 'challenger_not_trained'
        ? 'Not active — challenger not trained'
        : 'Not active';
      els.labChallengerAcc.textContent = reasonLabel;
      if (els.labSignificance) {
        els.labSignificance.textContent = 'Inactive';
        els.labSignificance.style.color = 'var(--text-secondary)';
      }
    } else {
      const cAcc = ab.challenger ? (ab.challenger.accuracy * 100).toFixed(1) : '--';
      els.labChallengerAcc.textContent = cAcc !== '--' ? `${cAcc}% (${ab.challenger.verified} runs)` : '--';
      if (els.labSignificance) {
        els.labSignificance.textContent = ab.significant ? "Significant" : "Testing";
        els.labSignificance.style.color = ab.significant ? "var(--green)" : "var(--text-secondary)";
      }
    }
  }
}

function renderDecisionGuide(data, best, activeAcc) {
  if (!els.decisionAction || !best) return;

  const dir = best.direction;
  const conf = best.confidence || 0;
  const agreement = best.agreement || 0;
  const d = data.derivatives || {};
  const of = data.order_flow || {};
  const total = activeAcc?.total || 0;
  const priceMatch = activeAcc?.price_match_rate || 0;
  const avgError = Math.round(activeAcc?.avg_move_error_usd || 0);
  const threshold = best.requiredConfidence || 0.6;

  let action = 'Wait';
  let actionDetail = 'The model does not have a clean enough signal for this timeframe.';
  if (dir === 'UP') {
    action = 'Watch for upside';
    actionDetail = `The app expects an upward move of about $${Math.round(best.expectedMove || 0).toLocaleString()} over ${best.horizon} minutes. This is a possible buy-pressure read, not a guarantee.`;
  } else if (dir === 'DOWN') {
    action = 'Watch for downside';
    actionDetail = `The app expects a downward move of about $${Math.round(best.expectedMove || 0).toLocaleString()} over ${best.horizon} minutes. This is a possible sell-pressure read, not a guarantee.`;
  }

  const premium = d.coinbase_premium || 0;
  const imbalance = of.imbalance || 0;
  let reason = 'Mixed inputs';
  let reasonDetail = 'No single live driver is clearly dominant yet.';
  if (Math.abs(premium) >= 5) {
    reason = premium > 0 ? 'Coinbase buying pressure' : 'Coinbase weakness';
    reasonDetail = premium > 0
      ? 'Coinbase is pricing BTC above Binance, often a sign that US spot demand is supportive.'
      : 'Coinbase is pricing BTC below Binance, which can mean US spot demand is weaker right now.';
  } else if (Math.abs(imbalance) >= 0.08) {
    reason = imbalance > 0 ? 'More visible buy support' : 'More visible sell pressure';
    reasonDetail = imbalance > 0
      ? 'The order book currently shows more bids near price, which can slow downside.'
      : 'The order book currently shows more asks near price, which can slow upside.';
  } else if (agreement >= 0.7) {
    reason = 'Models agree';
    reasonDetail = 'The model group is mostly voting the same way, which is better than a split vote.';
  }

  let risk = 'Early sample';
  let riskDetail = `Only ${total}/100 resolved ${best.horizon}m forecasts. Accuracy numbers are still young.`;
  if (best.meta_filtered || best.quality_filtered || best.regime_filtered) {
    risk = 'Signal was filtered';
    riskDetail = best.qualityMessage || 'The app changed this to WAIT because the evidence did not clear the safety filters.';
  } else if (total >= 100 && priceMatch < 0.35) {
    risk = 'Dollar target is loose';
    riskDetail = `Direction may be useful, but expected move size is often off. Average miss is about $${avgError.toLocaleString()}.`;
  } else if (conf < threshold) {
    risk = 'Confidence below bar';
    riskDetail = `This signal needs roughly ${(threshold * 100).toFixed(0)}% confidence, but has ${(conf * 100).toFixed(0)}%.`;
  }

  let next = 'Wait for confirmation';
  let nextDetail = 'Look for confidence, agreement, and buyer/seller flow to point the same way.';
  if (dir === 'UP') {
    next = 'Upside confirmation';
    nextDetail = 'Better confirmation: Coinbase premium stays positive, order-book bids remain stronger, and price breaks nearby resistance.';
  } else if (dir === 'DOWN') {
    next = 'Downside confirmation';
    nextDetail = 'Better confirmation: Coinbase premium weakens, asks remain stronger, and price breaks nearby support.';
  }

  els.decisionAction.textContent = action;
  els.decisionActionDetail.textContent = actionDetail;
  els.decisionReason.textContent = reason;
  els.decisionReasonDetail.textContent = reasonDetail;
  els.decisionRisk.textContent = risk;
  els.decisionRiskDetail.textContent = riskDetail;
  els.decisionNext.textContent = next;
  els.decisionNextDetail.textContent = nextDetail;
}

function renderTrustPanel(data, best, activeAcc) {
  if (!els.trustScore || !els.trustReasons || !best) return;

  const quality = data.verification?.data_quality?.horizons?.[best.horizon] || {};
  const signalHistory = data.signal_history || {};
  const total = activeAcc?.total || 0;
  const accuracy = activeAcc?.accuracy || 0;
  const priceMatch = activeAcc?.price_match_rate || 0;
  const conf = best.confidence || 0;
  const agreement = best.agreement || 0;
  const metaTrust = best.metaTrust ?? 0.5;
  const modelVotes = best.agreementModelCount
    ? `${best.agreementVotes || 0}/${best.agreementModelCount}`
    : '--';
  const drift = data.drift || {};
  const driftLevel = drift.level || drift.status || 'normal';

  let score = 0;
  score += Math.min(conf, 1) * 30;
  score += Math.min(agreement, 1) * 20;
  score += total >= 500 ? 20 : total >= 100 ? 14 : Math.max(0, total / 100) * 8;
  score += total ? Math.min(Math.max(accuracy, 0), 1) * 15 : 0;
  score += total ? Math.min(Math.max(priceMatch, 0), 1) * 10 : 0;
  score += Math.min(Math.max(metaTrust, 0), 1) * 5;

  if (best.meta_filtered || best.quality_filtered || best.regime_filtered) score -= 25;
  if (String(driftLevel).toLowerCase().includes('high')) score -= 10;
  score = Math.max(0, Math.min(100, Math.round(score)));

  let label = 'Low trust: wait';
  if (score >= 75) label = 'Higher trust: signal is cleaner';
  else if (score >= 55) label = 'Medium trust: use caution';
  else if (score >= 35) label = 'Low-medium trust: needs confirmation';

  const reasons = [
    {
      label: 'Live sample',
      value: `${total}/100`,
      detail: quality.message || (total >= 100 ? 'Enough resolved calls for an early read.' : 'Not enough resolved calls yet.'),
      tone: total >= 100 ? 'up' : 'neutral',
    },
    {
      label: 'Live history',
      value: `${signalHistory.coverage_pct ?? 0}%`,
      detail: `${signalHistory.snapshots || 0} saved live-signal snapshots are available after restart.`,
      tone: (signalHistory.coverage_pct || 0) >= 20 ? 'up' : 'neutral',
    },
    {
      label: 'Direction record',
      value: total ? `${(accuracy * 100).toFixed(1)}%` : '--',
      detail: total ? 'How often this timeframe picked UP/DOWN correctly.' : 'Appears after predictions resolve.',
      tone: total && accuracy >= 0.55 ? 'up' : total && accuracy < 0.50 ? 'down' : 'neutral',
    },
    {
      label: 'Dollar target record',
      value: total ? `${(priceMatch * 100).toFixed(1)}%` : '--',
      detail: 'How often direction was right and the move size was close.',
      tone: total && priceMatch >= 0.45 ? 'up' : total && priceMatch < 0.30 ? 'down' : 'neutral',
    },
    {
      label: 'Safety filters',
      value: best.meta_filtered || best.quality_filtered || best.regime_filtered ? 'Active' : 'Clear',
      detail: best.qualityMessage || 'No trust filter is currently blocking this signal.',
      tone: best.meta_filtered || best.quality_filtered || best.regime_filtered ? 'down' : 'up',
    },
    {
      label: 'Meta trust',
      value: `${(metaTrust * 100).toFixed(0)}%`,
      detail: 'Second model estimates whether the main signal deserves trust.',
      tone: metaTrust >= 0.58 ? 'up' : metaTrust >= 0.5 ? 'neutral' : 'down',
    },
    {
      label: 'Model votes',
      value: modelVotes,
      detail: best.agreementThreshold
        ? `Needs roughly ${(best.agreementThreshold * 100).toFixed(0)}% agreement for a cleaner read.`
        : 'Shows how many active models voted with the majority.',
      tone: agreement >= (best.agreementThreshold || 0.67) ? 'up' : 'neutral',
    },
  ];

  els.trustScore.textContent = `${score}%`;
  els.trustLabel.textContent = label;
  els.trustReasons.innerHTML = reasons.map(r => `
    <div class="trust-reason ${r.tone}">
      <span>${r.label}</span>
      <strong>${r.value}</strong>
      <p>${r.detail}</p>
    </div>
  `).join('');
}

function renderActionReasons(data, best, activeAcc) {
  if (!els.actionReasons || !best) return;

  const ind = data.indicators || {};
  const d = data.derivatives || {};
  const of = data.order_flow || {};
  const price = data.price || 0;
  const klines = data.klines || [];
  const drift = data.drift || {};
  const driftLabel = String(drift.level || drift.status || 'normal').toLowerCase();
  const dir = best.direction;
  const rawDir = best.rawDirection || dir;
  const action = dir === 'UP' ? 'BUY / UP' : dir === 'DOWN' ? 'SELL / DOWN' : 'AVOID / SKIP';
  const reasons = [];

  reasons.push({
    title: action,
    value: `${((best.confidence || 0) * 100).toFixed(0)}%`,
    tone: dir === 'UP' ? 'up' : dir === 'DOWN' ? 'down' : 'neutral',
    text: dir === 'UP'
      ? 'The selected timeframe currently leans upward, so this is a possible buy-pressure read.'
      : dir === 'DOWN'
        ? 'The selected timeframe currently leans downward, so this is a possible sell-pressure read.'
        : `The app is avoiding action. ${best.skipReason || best.qualityMessage || 'The evidence is not clean enough for a directional call.'}`,
  });

  if (rawDir !== dir) {
    reasons.push({
      title: 'Raw model blocked',
      value: `${rawDir} -> ${dir}`,
      tone: 'down',
      text: best.skipReason || best.qualityMessage || 'A safety filter changed the raw model call to AVOID because the trust checks were weak.',
    });
  }

  const agreement = best.agreement || 0;
  reasons.push({
    title: 'Model agreement',
    value: `${(agreement * 100).toFixed(0)}%`,
    tone: agreement >= 0.70 ? 'up' : agreement >= 0.50 ? 'neutral' : 'down',
    text: agreement >= 0.70
      ? 'Most models are voting in the same direction.'
      : agreement >= 0.50
        ? 'The models are partly aligned, but not strongly.'
        : 'The models are split, so the signal is less reliable.',
  });

  const policy = best.thresholdPolicy ||
    (data.signal_policy?.by_regime || {})[best.horizon] ||
    (data.signal_policy?.by_regime || {})[String(best.horizon)] ||
    (data.signal_policy?.by_horizon || {})[best.horizon] ||
    (data.signal_policy?.by_horizon || {})[String(best.horizon)] ||
    {};
  reasons.push({
    title: 'Learned signal bar',
    value: policy.ready && policy.threshold != null ? `${(policy.threshold * 100).toFixed(0)}%` : 'Learning',
    tone: policy.ready && (policy.precision || 0) >= (policy.target_precision || 0.57) ? 'up' : 'neutral',
    text: policy.ready
      ? `From ${policy.samples} resolved raw leans: ${(policy.precision * 100).toFixed(1)}% precision at ${(policy.action_rate * 100).toFixed(1)}% action rate.`
      : 'The app is still collecting enough resolved raw UP/DOWN leans to learn a better threshold.',
  });

  const neutralReasons = data.verification?.neutral_summary?.reasons || [];
  if (dir === 'NEUTRAL' && neutralReasons.length) {
    const top = neutralReasons[0];
    reasons.push({
      title: 'Most common WAIT reason',
      value: String(top.code || 'unknown').replaceAll('_', ' '),
      tone: 'neutral',
      text: `${top.count} of recent WAIT outcomes used this blocker. Use this to see what is causing too many neutral calls.`,
    });
  }

  if (activeAcc?.total) {
    const actionAcc = dir === 'UP'
      ? activeAcc.up_accuracy
      : dir === 'DOWN'
        ? activeAcc.down_accuracy
        : activeAcc.avoid_accuracy;
    const actionTotal = dir === 'UP'
      ? activeAcc.up_total
      : dir === 'DOWN'
        ? activeAcc.down_total
        : activeAcc.avoid_total;
    reasons.push({
      title: 'Recent action record',
      value: actionTotal ? `${((actionAcc || 0) * 100).toFixed(1)}%` : '--',
      tone: actionTotal && actionAcc >= 0.55 ? 'up' : actionTotal && actionAcc < 0.50 ? 'down' : 'neutral',
      text: actionTotal
        ? `This exact action type has ${actionTotal} resolved examples on the selected timeframe.`
        : 'This action type does not have enough resolved examples yet.',
    });
  }

  if (best.expectedMoveRange) {
    const r = best.expectedMoveRange;
    const spread = best.quantileSpread || 0;
    const tone = spread >= 3 ? 'down' : spread >= 2 ? 'neutral' : 'up';
    reasons.push({
      title: 'Move-size range',
      value: `$${Math.round(r.low || 0)}-$${Math.round(r.high || 0)}`,
      tone,
      text: spread >= 3
        ? 'The expected move range is very wide, so the app should avoid trusting the dollar target.'
        : spread >= 2
          ? 'The direction may still be useful, but the dollar target is uncertain.'
          : 'The move-size estimate is relatively tight compared with the expected median move.',
    });
  }

  if (ind.rsi != null) {
    const overbought = ind.rsi >= 70;
    const oversold = ind.rsi <= 30;
    reasons.push({
      title: overbought ? 'Overbought warning' : oversold ? 'Oversold warning' : 'RSI condition',
      value: ind.rsi.toFixed(1),
      tone: overbought ? 'down' : oversold ? 'up' : 'neutral',
      text: overbought
        ? 'Price has been rising quickly. Buying here can be riskier because a pullback is more likely.'
        : oversold
          ? 'Price has been falling quickly. Selling here can be riskier because a bounce is more likely.'
          : 'Momentum is not stretched into a classic overbought or oversold zone.',
    });
  }

  const sr = getLiveSupportResistance(data, price);
  if (sr) {
    const nearResistance = sr.resistanceDist >= 0 && sr.resistanceDist < Math.max(80, Math.abs(best.expectedMove || 0));
    const nearSupport = sr.supportDist >= 0 && sr.supportDist < Math.max(80, Math.abs(best.expectedMove || 0));
    reasons.push({
      title: dir === 'UP' ? 'Resistance check' : dir === 'DOWN' ? 'Support check' : 'Support / resistance',
      value: dir === 'UP' ? `$${Math.round(sr.resistance).toLocaleString()}` : `$${Math.round(sr.support).toLocaleString()}`,
      tone: (dir === 'UP' && nearResistance) || (dir === 'DOWN' && nearSupport) ? 'down' : 'neutral',
      text: dir === 'UP'
        ? nearResistance
          ? `Price is close to resistance, about $${Math.round(sr.resistanceDist).toLocaleString()} above. Upside may slow there.`
          : `Nearest resistance is about $${Math.round(sr.resistanceDist).toLocaleString()} above, leaving more room before the next likely pause.`
        : dir === 'DOWN'
          ? nearSupport
            ? `Price is close to support, about $${Math.round(sr.supportDist).toLocaleString()} below. Downside may slow there.`
            : `Nearest support is about $${Math.round(sr.supportDist).toLocaleString()} below, leaving more room before the next likely pause.`
          : 'Support and resistance are being used as wait/confirmation zones.',
    });
  }

  const premium = d.coinbase_premium || 0;
  reasons.push({
    title: 'Coinbase pressure',
    value: `${premium >= 0 ? '+' : ''}$${premium.toFixed(2)}`,
    tone: Math.abs(premium) < 3 ? 'neutral' : premium > 0 ? 'up' : 'down',
    text: Math.abs(premium) < 3
      ? 'Coinbase and Binance are close, so US spot flow is not giving a strong clue.'
      : premium > 0
        ? 'Coinbase is above Binance, which can support UP/BUY reads.'
        : 'Coinbase is below Binance, which can support DOWN/SELL or AVOID reads.',
  });

  const imbalance = of.imbalance || 0;
  reasons.push({
    title: 'Order book pressure',
    value: `${(imbalance * 100).toFixed(1)}%`,
    tone: Math.abs(imbalance) < 0.05 ? 'neutral' : imbalance > 0 ? 'up' : 'down',
    text: Math.abs(imbalance) < 0.05
      ? 'Buy and sell liquidity look fairly balanced near the current price.'
      : imbalance > 0
        ? 'More bids are visible near price, which can support upward movement or slow drops.'
        : 'More asks are visible near price, which can slow upward movement or support downside.',
  });

  if (activeAcc?.total && (activeAcc.accuracy < 0.50 || activeAcc.price_match_rate < 0.35)) {
    reasons.push({
      title: 'Model weakness',
      value: activeAcc.accuracy < 0.50 ? 'Direction' : 'Target',
      tone: 'down',
      text: activeAcc.accuracy < 0.50
        ? 'This timeframe has been below 50% direction accuracy recently, so its calls need extra caution.'
        : 'Direction may be useful, but dollar targets have often been too far off.',
    });
  }

  reasons.push({
    title: 'Drift check',
    value: driftLabel.includes('high') ? 'High' : driftLabel.includes('moderate') ? 'Medium' : 'Normal',
    tone: driftLabel.includes('high') ? 'down' : driftLabel.includes('moderate') ? 'neutral' : 'up',
    text: driftLabel.includes('high')
      ? 'Live market behavior looks different from training reference data. Trust should be reduced.'
      : driftLabel.includes('moderate')
        ? 'Some data drift is present. Use confirmation before acting.'
        : 'No major training/live mismatch is being flagged right now.',
  });

  els.actionReasons.innerHTML = reasons.slice(0, 10).map(r => `
    <div class="action-reason-card ${r.tone}">
      <span>${r.title}</span>
      <strong>${r.value}</strong>
      <p>${r.text}</p>
    </div>
  `).join('');
}

function renderActionMetrics(verification, activeAcc) {
  if (!els.actionMetrics) return;
  const actions = verification.action_summary || {};
  const activeCards = activeAcc ? [
    {
      label: `${activePlainTF}m BUY/UP`,
      stats: { accuracy: activeAcc.up_accuracy, total: activeAcc.up_total, hits: activeAcc.up_hits },
      detail: 'Selected timeframe only.',
    },
    {
      label: `${activePlainTF}m SELL/DOWN`,
      stats: { accuracy: activeAcc.down_accuracy, total: activeAcc.down_total, hits: activeAcc.down_hits },
      detail: 'Selected timeframe only.',
    },
    {
      label: `${activePlainTF}m AVOID/SKIP`,
      stats: { accuracy: activeAcc.avoid_accuracy, total: activeAcc.avoid_total, hits: activeAcc.avoid_hits },
      detail: 'Good avoid means price stayed neutral or the blocked raw direction would have been wrong.',
    },
  ] : [];

  const cards = [
    { label: 'All signals', stats: actions.all, detail: 'Every resolved action across all timeframes.' },
    { label: 'BUY / UP', stats: actions.buy || actions.up, detail: 'How often upward calls were correct.' },
    { label: 'SELL / DOWN', stats: actions.sell || actions.down, detail: 'How often downward calls were correct.' },
    { label: 'AVOID / SKIP', stats: actions.avoid || actions.skip, detail: 'How often waiting avoided a bad or flat setup.' },
    ...activeCards,
  ];

  els.actionMetrics.innerHTML = cards.map(c => {
    const s = c.stats || {};
    const total = s.total || 0;
    const acc = total ? `${((s.accuracy || 0) * 100).toFixed(1)}%` : '--';
    const tone = total && (s.accuracy || 0) >= 0.55 ? 'up' : total && (s.accuracy || 0) < 0.50 ? 'down' : 'neutral';
    return `
      <div class="action-metric-card ${tone}">
        <span>${c.label}</span>
        <strong>${acc}</strong>
        <p>${total ? `${s.hits || 0}/${total} correct. ${c.detail}` : `Waiting for resolved examples. ${c.detail}`}</p>
      </div>
    `;
  }).join('');
}

function renderPlainSignalCards(data, best) {
  const d = data.derivatives || {};
  const of = data.order_flow || {};
  const cards = [];

  if (best) {
    cards.push({
      label: 'Main forecast',
      value: best.direction,
      tone: best.direction === 'UP' ? 'up' : best.direction === 'DOWN' ? 'down' : 'neutral',
      detail: `Expected move near $${Math.round(best.expectedMove || 0).toLocaleString()} over ${best.horizon} minutes.`
    });
    cards.push({
      label: 'Model agreement',
      value: `${((best.agreement || 0) * 100).toFixed(0)}%`,
      tone: (best.agreement || 0) >= 0.7 ? 'up' : (best.agreement || 0) >= 0.5 ? 'neutral' : 'down',
      detail: 'Higher agreement means the model group is voting in the same direction.'
    });
    cards.push({
      label: 'Trust filter',
      value: best.meta_filtered || best.quality_filtered || best.regime_filtered ? 'Skipped' : 'Allowed',
      tone: best.meta_filtered || best.quality_filtered || best.regime_filtered ? 'down' : 'up',
      detail: best.qualityMessage || `Signal confidence cleared the current ${((best.requiredConfidence || 0.6) * 100).toFixed(0)}% safety bar.`
    });
  }

  const cvd = of.cvd_change || of.cvd || 0;
  cards.push({
    label: 'Buyer vs seller flow',
    value: cvd >= 0 ? 'Buyers active' : 'Sellers active',
    tone: cvd >= 0 ? 'up' : 'down',
    detail: 'This watches live trade pressure, not old candle history.'
  });

  const imb = of.imbalance || 0;
  cards.push({
    label: 'Order book pressure',
    value: imb >= 0 ? 'More bids' : 'More asks',
    tone: imb >= 0 ? 'up' : 'down',
    detail: 'More bids can support price; more asks can cap price.'
  });

  const premium = d.coinbase_premium || 0;
  cards.push({
    label: 'Coinbase premium',
    value: `${premium >= 0 ? '+' : ''}$${premium.toFixed(2)}`,
    tone: premium >= 0 ? 'up' : 'down',
    detail: premium >= 0 ? 'US spot buyers are paying more than Binance.' : 'Coinbase is cheaper than Binance, so US spot demand is weaker.'
  });

  const oiDiv = d.oi_divergence || 0;
  cards.push({
    label: 'Futures positioning',
    value: `${oiDiv >= 0 ? '+' : ''}${oiDiv.toFixed(2)}% delta`,
    tone: Math.abs(oiDiv) < 0.2 ? 'neutral' : oiDiv > 0 ? 'up' : 'down',
    detail: 'This compares Binance and Bybit leverage growth. Big disagreement can warn of unstable moves.'
  });

  els.analysisSignals.innerHTML = cards.map(c => `
    <div class="plain-signal-card ${c.tone}">
      <span class="plain-signal-label">${c.label}</span>
      <strong>${c.value}</strong>
      <p>${c.detail}</p>
    </div>
  `).join('');
}

function renderPlainErrorRows(entries) {
  if (!entries || entries.length === 0) {
    els.analysisErrors.innerHTML = '<div class="plain-empty">Waiting for finished predictions. Error rate appears after forecasts expire.</div>';
    return;
  }

  els.analysisErrors.innerHTML = entries.slice(0, 12).map(e => {
    const expected = Math.round(Math.abs(e.expected_move_usd || e.signed_expected_move_usd || 0));
    const actual = Math.round(Math.abs(e.actual_move_usd || 0));
    const error = Math.round(e.move_error_usd || 0);
    const isAvoid = e.direction === 'NEUTRAL';
    const dirClass = isAvoid ? (e.avoid_success ? 'hit' : 'warn') : (e.hit ? 'hit' : 'miss');
    const priceClass = isAvoid ? (e.avoid_success ? 'hit' : 'warn') : (e.price_match ? 'hit' : 'warn');
    const directionText = isAvoid
      ? (e.avoid_success ? 'Avoid worked' : 'Avoid missed a move')
      : (e.hit ? 'Direction right' : 'Direction wrong');
    const priceText = isAvoid
      ? 'not a directional bet'
      : (e.price_match ? 'price close' : `off by $${error.toLocaleString()}`);
    const expectedLabel = isAvoid ? 'Watched move' : 'Expected';
    const rawText = isAvoid && e.raw_direction && e.raw_direction !== 'NEUTRAL'
      ? ` (raw ${e.raw_direction})`
      : '';
    return `
      <div class="plain-error-row">
        <span class="plain-error-time">${e.horizon}m</span>
        <span class="plain-error-dir ${dirClass}">${isAvoid ? 'AVOID' : e.direction}${rawText} -> ${e.actual_direction}</span>
        <span>${expectedLabel} $${expected.toLocaleString()}</span>
        <span>Actual $${actual.toLocaleString()}</span>
        <span class="${priceClass}">${directionText}, ${priceText}</span>
      </div>
    `;
  }).join('');
}

function renderSupportResistance(data, price) {
  const sr = getLiveSupportResistance(data, price);
  if (!sr) {
    els.supportLevel.textContent = '--';
    els.resistanceLevel.textContent = '--';
    els.supportMeaning.textContent = 'Needs more candles.';
    els.resistanceMeaning.textContent = 'Needs more candles.';
    return;
  }

  els.supportLevel.textContent = `$${Math.round(sr.support).toLocaleString()}`;
  els.resistanceLevel.textContent = `$${Math.round(sr.resistance).toLocaleString()}`;
  els.supportMeaning.textContent = `If price falls, this nearby area may slow the drop. It is about $${Math.round(sr.supportDist).toLocaleString()} below now.`;
  els.resistanceMeaning.textContent = `If price rises, this nearby area may slow the climb. It is about $${Math.round(sr.resistanceDist).toLocaleString()} above now.`;
}

function getLiveSupportResistance(data, price) {
  const sr = data?.support_resistance;
  if (sr?.support && sr?.resistance) {
    return {
      support: sr.support,
      resistance: sr.resistance,
      supportDist: sr.support_distance_usd ?? Math.max((price || 0) - sr.support, 0),
      resistanceDist: sr.resistance_distance_usd ?? Math.max(sr.resistance - (price || 0), 0),
    };
  }
  return getSupportResistanceLevels(data?.klines || [], price);
}

function getSupportResistanceLevels(klines, price) {
  if (!klines || klines.length < 20 || !price) return null;
  const recent = klines.slice(-80);
  const lows = recent.map(k => k.low).filter(v => v < price).sort((a, b) => b - a);
  const highs = recent.map(k => k.high).filter(v => v > price).sort((a, b) => a - b);
  const support = lows[0] || Math.min(...recent.map(k => k.low));
  const resistance = highs[0] || Math.max(...recent.map(k => k.high));
  const supportDist = price - support;
  const resistanceDist = resistance - price;
  return { support, resistance, supportDist, resistanceDist };
}

function renderIndicatorAnalysis(data, best) {
  const ind = data.indicators || {};
  const d = data.derivatives || {};
  const of = data.order_flow || {};
  const items = [];

  if (ind.rsi != null) {
    const status = ind.rsi > 70 ? 'hot' : ind.rsi < 30 ? 'cool' : 'normal';
    items.push({
      name: 'RSI',
      value: ind.rsi.toFixed(1),
      tone: ind.rsi > 70 ? 'down' : ind.rsi < 30 ? 'up' : 'neutral',
      meaning: status === 'hot' ? 'Price has been rising fast. Pullback risk is higher.' : status === 'cool' ? 'Price has been falling fast. Bounce risk is higher.' : 'Momentum is not stretched.'
    });
  }

  if (ind.adx != null) {
    items.push({
      name: 'Trend strength',
      value: ind.adx.toFixed(1),
      tone: ind.adx >= 25 ? (best?.direction === 'DOWN' ? 'down' : 'up') : 'neutral',
      meaning: ind.adx >= 25 ? 'The market is trending, so forecasts can carry farther.' : 'The market is choppy, so targets may miss more often.'
    });
  }

  if (of.imbalance != null) {
    items.push({
      name: 'Order book',
      value: `${(of.imbalance * 100).toFixed(1)}%`,
      tone: of.imbalance >= 0 ? 'up' : 'down',
      meaning: of.imbalance >= 0 ? 'There is more visible buy support near price.' : 'There is more visible sell pressure near price.'
    });
  }

  if (d.coinbase_premium != null) {
    items.push({
      name: 'Coinbase premium',
      value: `${d.coinbase_premium >= 0 ? '+' : ''}$${d.coinbase_premium.toFixed(2)}`,
      tone: d.coinbase_premium >= 0 ? 'up' : 'down',
      meaning: d.coinbase_premium >= 0 ? 'US spot demand is supportive.' : 'US spot demand is not supporting price right now.'
    });
  }

  if (d.global_oi_change != null) {
    items.push({
      name: 'Open interest',
      value: `${d.global_oi_change >= 0 ? '+' : ''}${d.global_oi_change.toFixed(2)}%`,
      tone: d.global_oi_change >= 0 ? 'neutral' : 'down',
      meaning: d.global_oi_change >= 0 ? 'More futures positions are opening. Moves can become stronger but riskier.' : 'Futures positions are closing. Moves may be less supported by leverage.'
    });
  }

  els.indicatorAnalysis.innerHTML = items.slice(0, 6).map(i => `
    <div class="plain-indicator ${i.tone}">
      <div>
        <span>${i.name}</span>
        <strong>${i.value}</strong>
      </div>
      <p>${i.meaning}</p>
    </div>
  `).join('');
}

function renderVerification(v) {
  if (!v) return;

  // Pending count
  const isAll = currentVerifyTab === 'all';
  if (isAll) {
    els.verifyPending.textContent = `${v.pending} pending`;
  } else {
    const pCount = v.pending_by_horizon?.[currentVerifyTab] || 0;
    els.verifyPending.textContent = `${pCount} pending`;
  }

  // View toggle: All vs Specific Horizon
  if (isAll) {
    els.verifyMetrics.classList.add('hidden');
    els.verifyAccRow.classList.remove('hidden');
    
    // High-level Accuracy chips
    els.verifyAccRow.innerHTML = '';
    [1, 3, 5, 7, 10, 15].forEach(h => {
      const acc = v.accuracy?.[h];
      const chip = document.createElement('div');
      chip.className = 'verify-acc-chip';
      const pct = acc ? (acc.accuracy * 100).toFixed(0) : '--';
      const total = acc ? acc.total : 0;
      const colorClass = !acc || total < 3 ? '' : acc.accuracy >= 0.55 ? 'good' : acc.accuracy >= 0.45 ? 'ok' : 'poor';
      chip.innerHTML = `<span class="verify-acc-label">${h}m</span><span class="verify-acc-value ${colorClass}">${pct}%</span>`;
      els.verifyAccRow.appendChild(chip);
    });
    
    renderVerificationLog(v.recent);
  } else {
    els.verifyMetrics.classList.remove('hidden');
    els.verifyAccRow.classList.add('hidden');
    
    const h = parseInt(currentVerifyTab);
    const acc = v.accuracy?.[h];
    
    if (acc && acc.total > 0) {
      els.vmOverall.textContent = (acc.accuracy * 100).toFixed(1) + '%';
      els.vmOverall.style.color = acc.accuracy >= 0.55 ? 'var(--green)' : acc.accuracy >= 0.45 ? 'var(--gold)' : 'var(--red)';
      els.vmUp.textContent = acc.up_total > 0 ? (acc.up_accuracy * 100).toFixed(1) + '%' : '--';
      els.vmDown.textContent = acc.down_total > 0 ? (acc.down_accuracy * 100).toFixed(1) + '%' : '--';
      els.vmHits.textContent = acc.hits;
      els.vmMisses.textContent = acc.misses;
      els.vmStreak.textContent = `${acc.current_streak} ${acc.streak_type.toUpperCase()}`;
      els.vmStreak.style.color = acc.streak_type === 'hit' ? 'var(--green)' : acc.streak_type === 'miss' ? 'var(--red)' : 'var(--text-tertiary)';
    } else {
      els.vmOverall.textContent = '--';
      els.vmUp.textContent = '--';
      els.vmDown.textContent = '--';
      els.vmHits.textContent = '0';
      els.vmMisses.textContent = '0';
      els.vmStreak.textContent = '--';
      els.vmStreak.style.color = 'var(--text-tertiary)';
    }
    
    const history = v.histories?.[h] || [];
    renderVerificationLog(history);
  }
}

function renderVerificationLog(entries) {
  els.verifyLog.innerHTML = '';
  if (entries && entries.length > 0) {
    entries.forEach(entry => {
      const row = document.createElement('div');
      row.className = 'verify-entry';
      const icon = entry.hit ? '✓' : '✗';
      const iconClass = entry.hit ? 'hit' : 'miss';
      const dirClass = entry.direction === 'UP' ? 'up' : entry.direction === 'DOWN' ? 'down' : 'neutral';
      const changeClass = entry.actual_change_pct >= 0 ? 'positive' : 'negative';
      row.innerHTML = `
        <span class="verify-icon ${iconClass}">${icon}</span>
        <span class="verify-horizon">${entry.horizon}m</span>
        <span class="verify-pred-dir ${dirClass}">${entry.direction}</span>
        <span class="verify-actual">→ ${entry.actual_direction}</span>
        <span class="verify-change ${changeClass}">${entry.actual_change_pct >= 0 ? '+' : ''}${entry.actual_change_pct.toFixed(3)}%</span>`;
      els.verifyLog.appendChild(row);
    });
  } else {
    els.verifyLog.innerHTML = '<div class="verify-entry" style="color: var(--text-muted); justify-content: center;">Waiting for predictions to verify...</div>';
  }
}

function renderBacktest(data) {
  const bt = data.backtest;
  if (!bt) return;
  if (bt[1]) document.getElementById('bt-1m').textContent = (bt[1].accuracy * 100).toFixed(1) + '%';
  if (bt[5]) document.getElementById('bt-5m').textContent = (bt[5].accuracy * 100).toFixed(1) + '%';
  if (bt[10]) document.getElementById('bt-10m').textContent = (bt[10].accuracy * 100).toFixed(1) + '%';
  if (bt[15]) document.getElementById('bt-15m').textContent = (bt[15].accuracy * 100).toFixed(1) + '%';
  if (bt.sharpe != null) document.getElementById('bt-sharpe').textContent = typeof bt.sharpe === 'number' ? bt.sharpe.toFixed(2) : '--';
  if (bt[15]) {
    document.getElementById('bt-winrate').textContent = (bt[15].win_rate * 100).toFixed(1) + '%';
    document.getElementById('bt-pf').textContent = bt[15].profit_factor.toFixed(2);
    document.getElementById('bt-mdd').textContent = (bt[15].max_drawdown * 100).toFixed(2) + '%';
  }
}

// ══════════════════════════════════════════════
//  Inline Technical Indicators (for chart overlays)
// ══════════════════════════════════════════════
function computeEMA(data, period) {
  const result = [];
  const k = 2 / (period + 1);
  let emaVal = null;
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) { result.push(null); continue; }
    if (emaVal === null) {
      let sum = 0;
      for (let j = i - period + 1; j <= i; j++) sum += data[j];
      emaVal = sum / period;
    } else {
      emaVal = data[i] * k + emaVal * (1 - k);
    }
    result.push(emaVal);
  }
  return result;
}

function computeRSI(closes, period = 14) {
  const result = [];
  if (closes.length < period + 1) return closes.map(() => null);
  let avgGain = 0, avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const change = closes[i] - closes[i - 1];
    if (change > 0) avgGain += change; else avgLoss += Math.abs(change);
  }
  avgGain /= period; avgLoss /= period;
  for (let i = 0; i < period; i++) result.push(null);
  const rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
  result.push(100 - 100 / (1 + rs));
  for (let i = period + 1; i < closes.length; i++) {
    const change = closes[i] - closes[i - 1];
    const gain = change > 0 ? change : 0;
    const loss = change < 0 ? Math.abs(change) : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    const rs2 = avgLoss === 0 ? 100 : avgGain / avgLoss;
    result.push(100 - 100 / (1 + rs2));
  }
  return result;
}

function computeMACD(closes, fast = 12, slow = 26, signal = 9) {
  const emaFast = computeEMA(closes, fast);
  const emaSlow = computeEMA(closes, slow);
  const macdLine = [];
  for (let i = 0; i < closes.length; i++) {
    if (emaFast[i] === null || emaSlow[i] === null) macdLine.push(null);
    else macdLine.push(emaFast[i] - emaSlow[i]);
  }
  const validMacd = macdLine.filter(v => v !== null);
  const signalLine = computeEMA(validMacd, signal);
  const padded = [];
  let vi = 0;
  for (let i = 0; i < macdLine.length; i++) {
    if (macdLine[i] === null) padded.push(null);
    else { padded.push(signalLine[vi] !== undefined ? signalLine[vi] : null); vi++; }
  }
  const histogram = [];
  for (let i = 0; i < closes.length; i++) {
    if (macdLine[i] === null || padded[i] === null) histogram.push(null);
    else histogram.push(macdLine[i] - padded[i]);
  }
  return { macdLine, signalLine: padded, histogram };
}

function computeBB(closes, period = 20, stdDev = 2) {
  const sma = [];
  const upper = [], lower = [];
  for (let i = 0; i < closes.length; i++) {
    if (i < period - 1) { sma.push(null); upper.push(null); lower.push(null); continue; }
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += closes[j];
    const mean = sum / period;
    sma.push(mean);
    let sumSq = 0;
    for (let j = i - period + 1; j <= i; j++) sumSq += Math.pow(closes[j] - mean, 2);
    const std = Math.sqrt(sumSq / period);
    upper.push(mean + stdDev * std);
    lower.push(mean - stdDev * std);
  }
  return { upper, middle: sma, lower };
}

// ══════════════════════════════════════════════
//  Polymarket
// ══════════════════════════════════════════════
function renderScoreboard(data) {
  const grid = document.getElementById('scoreboard-grid');
  if (!grid) return;
  const sb = data.scoreboard || {};
  const horizons = [5, 15];
  const dirColor = (d) => d === 'UP' ? '#00e676' : d === 'DOWN' ? '#ff1744' : '#8892a6';
  const dirArrow = (d) => d === 'UP' ? '▲' : d === 'DOWN' ? '▼' : '●';
  const gradeColor = (g) => ({ 'A+': '#00e676', 'A': '#26c281', 'B': '#ffd700', 'C': '#ff9100', 'WATCH': '#8892a6' }[g] || '#8892a6');

  grid.innerHTML = horizons.map((h) => {
    const s = sb[h] || {};
    const dir = s.direction || 'NEUTRAL';
    const directional = ['UP', 'DOWN'].includes(dir);
    const actionable = !!s.actionable && directional;
    const conv = Math.round(s.conviction || 0);
    const grade = s.convictionGrade || 'WATCH';
    const ourAcc = s.ourAccuracy != null ? (s.ourAccuracy * 100).toFixed(0) + '%' : '--';
    const raw = s.rawDirection || dir;
    const cd = s.confluenceDetail || {};
    const chip = (label, ok) => `<span class="cf-chip ${ok ? 'ok' : 'no'}">${ok ? '✓' : '✗'} ${label}</span>`;
    const actionLabel = actionable
      ? (dir === 'UP' ? 'STRONG BUY' : 'STRONG SELL')
      : (dir === 'NEUTRAL' ? 'WAIT' : (dir === 'UP' ? 'lean up (low conviction)' : 'lean down (low conviction)'));

    // Live in-window state from the price-to-beat tracker (same horizon): expected price
    // (live price + the model's current signed expected move) and HOLD/EXIT advice.
    const ptbLatest = data.price_to_beat && data.price_to_beat.latest || {};
    const ptb = ptbLatest[h] || ptbLatest[String(h)] || {};
    const livePrice = Number(ptb.current_price || data.price || 0);
    const expDir = ptb.live_lean || dir;
    const expMag = Math.abs(Number(ptb.live_expected_move || 0));
    const expPrice = (livePrice && expMag) ? (livePrice + (expDir === 'UP' ? expMag : expDir === 'DOWN' ? -expMag : 0)) : null;
    const fmtSecs = (sec) => { sec = Math.max(0, Math.round(sec || 0)); const m = Math.floor(sec / 60); return m > 0 ? `${m}m ${sec % 60}s` : `${sec}s`; };
    const expRow = (expPrice != null) ? `
        <div class="sb-exp" style="display:flex;justify-content:space-between;align-items:center;margin-top:.45rem;font-size:.85em">
          <span style="color:var(--text-secondary)">Expected price · ${fmtSecs(ptb.seconds_left)} left</span>
          <strong style="color:${dirColor(expDir)}">$${Math.round(expPrice).toLocaleString()} <small style="color:var(--text-secondary)">(${expDir === 'UP' ? '+' : expDir === 'DOWN' ? '-' : '±'}$${Math.round(expMag)})</small></strong>
        </div>` : '';
    const adv = ptb.advice || {};
    const advTone = adv.tone === 'good' ? '#00e676' : adv.tone === 'bad' ? '#ff1744' : adv.tone === 'warn' ? '#ffb74d' : '#8892a6';
    const cm = Number(ptb.current_move || 0);
    const adviceStrip = adv.action ? `
        <div class="sb-advice" style="margin-top:.5rem;padding:.5rem .65rem;border:1px solid ${advTone};border-radius:6px;background:rgba(255,255,255,0.025)">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <span style="font-weight:700;color:${advTone};letter-spacing:.3px">${adv.action}</span>
            <span style="font-size:.8em;color:var(--text-secondary)">${cm >= 0 ? '+' : ''}$${Math.round(cm)} (${ptb.current_position || '--'})</span>
          </div>
          <div style="font-size:.82em;margin-top:.25rem">${adv.text || ''}</div>
        </div>` : '';
    return `
      <div class="sb-card ${actionable ? 'actionable' : ''}" style="border-color:${actionable ? dirColor(dir) : 'var(--border-primary)'}">
        <div class="sb-head">
          <span class="sb-tf">${h}m</span>
          <span class="sb-grade" style="background:${gradeColor(grade)}">${grade}</span>
        </div>
        <div class="sb-dir" style="color:${dirColor(dir)}">${dirArrow(dir)} ${actionLabel}</div>
        <div class="sb-conv">
          <span>Conviction</span>
          <div class="sb-bar"><i style="width:${conv}%; background:${gradeColor(grade)}"></i></div>
          <strong>${conv}</strong>
        </div>
        <div class="sb-confluence">
          ${chip('models', cd.models_agree)} ${chip('flow', cd.flow_agree)} ${chip('regime', cd.regime_favorable)}
        </div>
        <div class="sb-vs">
          <div class="sb-vs-col"><span>Ensemble final</span><strong style="color:${dirColor(dir)}">${dir}</strong><small>${ourAcc} acc · ${s.ourSamples || 0}n</small></div>
          <div class="sb-vs-col"><span>Raw lean</span><strong style="color:${dirColor(raw)}">${raw}</strong><small>before the gate</small></div>
        </div>
        ${expRow}
        ${adviceStrip}
      </div>`;
  }).join('');
}

function renderExchanges(data) {
  const strip = document.getElementById('exchange-strip');
  if (!strip) return;
  const ex = data.exchanges || {};
  const venues = ex.venues || {};
  const consensus = ex.consensus;
  const order = ['binance', 'coinbase', 'bybit', 'kucoin', 'chainlink'];
  const lead = ex.lead_venue ? `${ex.lead_venue} (+${ex.lead_bps}bps)` : '--';
  const frag = ex.fragmentation_bps != null ? `${ex.fragmentation_bps}bps` : '--';
  const head = `<div class="ex-consensus">Consensus <strong>${consensus ? '$' + Number(consensus).toLocaleString() : '--'}</strong>` +
    `<small style="color:var(--text-muted)">lead: ${lead} · spread: ${frag}</small></div>`;
  const exAcc = data.exchange_accuracy || {};
  strip.innerHTML = head + order.map((name) => {
    const v = venues[name] || {};
    if (v.price == null) return `<div class="ex-card off"><span>${name}</span><strong>--</strong></div>`;
    const dev = v.deviation_bps;
    const col = dev > 0 ? '#00e676' : dev < 0 ? '#ff1744' : '#8892a6';
    const a5 = ((exAcc[name] || {})[5] || {});
    const conf = a5.total ? `${(a5.accuracy * 100).toFixed(0)}% conf · ${a5.total}n` : '';
    return `<div class="ex-card"><span>${name}</span><strong>$${Number(v.price).toLocaleString()}</strong>` +
      `<small style="color:${col}">${dev > 0 ? '+' : ''}${dev}bps</small>` +
      (conf ? `<small style="color:var(--text-muted)">5m ${conf}</small>` : '') + `</div>`;
  }).join('');
}

// ══════════════════════════════════════════════
//  Models & Signals tab
// ══════════════════════════════════════════════
const MODEL_LABELS = {
  xgb: 'XGBoost', lgb: 'LightGBM', cat: 'CatBoost', histgb: 'HistGradientBoosting',
  dl: 'TCN / Sequence (deep)', lr: 'Logistic Regression',
};
const PTB_HORIZONS = [5, 15];
const ROSTER_HORIZONS = [1, 3, 5, 7, 10, 15];

// ══════════════════════════════════════════════
//  BINANCE VIEW — 6-horizon model predictions (spot/perp, long/short entries)
// ══════════════════════════════════════════════
function renderBinanceView(data) {
  const grid = document.getElementById('binance-grid');
  const strip = document.getElementById('binance-price-strip');
  if (!grid) return;
  const px = Number(data.price || 0);
  if (strip) {
    // ticker_24h uses snake_case keys (see data_ingestion.fetch_ticker_24h)
    const t = data.ticker_24h || {};
    const ch = t.price_change_percent != null ? Number(t.price_change_percent) : null;
    const hi = t.high_price != null ? Number(t.high_price) : null;
    const lo = t.low_price != null ? Number(t.low_price) : null;
    const vol = t.volume != null ? Number(t.volume) : null;
    strip.innerHTML = `<span style="color:var(--text-secondary);font-size:.7em;text-transform:uppercase;letter-spacing:.6px">Binance BTCUSDT</span>
      <strong style="font-size:1.4em;margin-left:.5rem">$${px.toLocaleString(undefined,{minimumFractionDigits:2})}</strong>
      ${ch!=null?`<span style="color:${ch>=0?'#00e676':'#ff5252'};margin-left:.5rem">${ch>=0?'+':''}${ch.toFixed(2)}% 24h</span>`:''}
      ${hi!=null&&lo!=null?`<span style="color:var(--text-secondary);margin-left:1rem;font-size:.8em">24h range $${Math.round(lo).toLocaleString()} – $${Math.round(hi).toLocaleString()}</span>`:''}
      ${vol!=null?`<span style="color:var(--text-secondary);margin-left:1rem;font-size:.8em">vol ${Math.round(vol).toLocaleString()} BTC</span>`:''}`;
  }
  const preds = (data.predictions || []).slice().sort((a,b)=>(a.horizon||0)-(b.horizon||0));
  // Per-horizon accuracy lives at verification.accuracy (directional_* = committed
  // UP/DOWN calls only — the clean subset of `hit`, valid for betting decisions).
  const accMap = ((data.verification || {}).accuracy) || {};
  if (!preds.length) {
    // NO early return: indicators / flow / accuracy / calls-log below must still
    // render (during a (re)train the model has no predictions for hours).
    const rl = data.relearn_status || {};
    const pct = rl.progress != null ? ` (${Math.round(rl.progress * 100)}%)` : '';
    grid.innerHTML = `<div class="fh-empty">${rl.running
      ? `🧠 Model is training${pct} — ${rl.message || ''}. Predictions appear when it completes; live data below keeps streaming.`
      : 'Waiting for model predictions…'}</div>`;
  } else grid.innerHTML = preds.map(p => {
    const dir = p.rawDirection || p.direction || 'NEUTRAL';
    const col = dir==='UP'?'#00e676':dir==='DOWN'?'#ff5252':'#8892a6';
    const sig = p.signal || 'WAIT';
    const action = sig==='TRADE BUY'||sig==='UP'?'BUY':sig==='TRADE SELL'||sig==='DOWN'?'SELL':(p.actionable?'TRADE':'WAIT');
    const actCol = action==='BUY'?'#00e676':action==='SELL'?'#ff5252':'#8892a6';
    const conf = p.calibratedConfidence!=null?p.calibratedConfidence:p.confidence;
    const expMove = p.expectedMove!=null?p.expectedMove:0;
    const target = px && expMove ? px + (dir==='DOWN'?-1:1)*Math.abs(expMove) : (p.targetPrice||null);
    const cfl = p.confluence||{}; const a = accMap[p.horizon]||accMap[String(p.horizon)]||{};
    const accStr = a.directional_total?`${((a.directional_accuracy||0)*100).toFixed(0)}% (${a.directional_total})`:'—';
    const ep = p.expectedPrecision;        // measured P(win) for this setup cell, when known
    const agree = p.agreement;
    const grade = p.convictionGrade || cfl.grade;
    return `<div style="border:1px solid ${col}33;border-left:4px solid ${col};border-radius:10px;padding:.9rem 1.1rem;background:rgba(255,255,255,.02)">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <strong style="font-size:1.2em">${p.horizon}m</strong>
        <span style="color:${col};font-weight:700;font-size:1.1em">${dirArrow(dir)} ${dir==='NEUTRAL'?'NO LEAN':dir}</span>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:.4rem;margin-top:.6rem;font-size:.85em">
        <div><span style="color:var(--text-secondary)">Gated action</span><br><strong style="color:${actCol}">${action}</strong></div>
        <div><span style="color:var(--text-secondary)">${p.calibratedConfidence!=null?'Calib. conf':'Confidence'}</span><br><strong>${conf!=null?(conf*100).toFixed(0)+'%':'—'}</strong></div>
        <div><span style="color:var(--text-secondary)">Expected move</span><br><strong>${expMove?'±$'+Math.round(Math.abs(expMove)):'—'}</strong></div>
        <div><span style="color:var(--text-secondary)">Target</span><br><strong>${target?'$'+Math.round(target).toLocaleString():'—'}</strong></div>
        <div><span style="color:var(--text-secondary)">Setup grade</span><br><strong>${grade||'—'}${cfl.score!=null?` (${cfl.score}/5)`:''}</strong></div>
        <div><span style="color:var(--text-secondary)">Measured P(win)</span><br><strong>${ep!=null?(ep*100).toFixed(0)+'%':'—'}</strong></div>
        <div><span style="color:var(--text-secondary)">Model agreement</span><br><strong>${agree!=null?(agree*100).toFixed(0)+'%':'—'}</strong></div>
        <div><span style="color:var(--text-secondary)">Live acc (committed)</span><br><strong>${accStr}</strong></div>
      </div>
    </div>`;
  }).join('');

  const ind = document.getElementById('binance-indicators');
  const i = data.indicators || {};
  const cell = (label,val,col) => `<div style="background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07);border-radius:8px;padding:.5rem .7rem">
    <div style="font-size:.7em;color:var(--text-secondary)">${label}</div><div style="font-weight:700;color:${col||'var(--text-primary)'}">${val}</div></div>`;
  if (ind) {
    const rg = (data.regime||{}).regime || '—';
    const rgCol = rg.includes('TREND')?'#00e676':rg==='HIGH_VOLATILITY'?'#ffb74d':rg==='LOW_VOLATILITY'?'#8892a6':'';
    const emaX = (i.ema9!=null&&i.ema21!=null)?(i.ema9>i.ema21?'BULL':'BEAR'):null;
    ind.innerHTML = [
      cell('Regime', rg, rgCol),
      cell('RSI', i.rsi!=null?i.rsi.toFixed(0):'—', i.rsi>70?'#ff5252':i.rsi<30?'#00e676':''),
      cell('Stoch RSI', i.stoch_rsi!=null?i.stoch_rsi.toFixed(0):'—', i.stoch_rsi>80?'#ff5252':i.stoch_rsi<20?'#00e676':''),
      cell('MFI', i.mfi!=null?i.mfi.toFixed(0):'—', i.mfi>80?'#ff5252':i.mfi<20?'#00e676':''),
      cell('CCI', i.cci!=null?i.cci.toFixed(0):'—', i.cci>100?'#ff5252':i.cci<-100?'#00e676':''),
      cell('ADX (trend)', i.adx!=null?`${i.adx.toFixed(0)} (${i.trend_strength||'—'})`:'—', i.adx>25?'#00e676':''),
      cell('MACD hist', i.macd_hist!=null?i.macd_hist.toFixed(1):'—', i.macd_hist>0?'#00e676':i.macd_hist<0?'#ff5252':''),
      cell('BB position', i.bb_position!=null?(i.bb_position*100).toFixed(0)+'%':'—', i.bb_position>0.95?'#ff5252':i.bb_position<0.05?'#00e676':''),
      cell('ATR (1m)', i.atr!=null?'$'+i.atr.toFixed(0):'—'),
      cell('EMA 9/21', emaX||'—', emaX==='BULL'?'#00e676':emaX==='BEAR'?'#ff5252':''),
      cell('SuperTrend', i.supertrend!=null?(px>i.supertrend?'UP':'DOWN'):'—', i.supertrend!=null?(px>i.supertrend?'#00e676':'#ff5252'):''),
      cell('Williams %R', i.williams_r!=null?i.williams_r.toFixed(0):'—', i.williams_r>-20?'#ff5252':i.williams_r<-80?'#00e676':''),
    ].join('');
  }

  // Order-flow / derivatives strip — the SAME per-candle values the model trains on
  // (payload.training_signals), so what you see here is what the model sees.
  const flow = document.getElementById('binance-flow');
  if (flow) {
    const ts = data.training_signals || {};
    const sCell = (label, key, dp, signed, fmt) => {
      const raw = ts[key];
      if (raw == null) return '';
      const v = Number(raw);
      const col = !signed ? '' : v > 0 ? '#00e676' : v < 0 ? '#ff5252' : '';
      const shown = fmt ? fmt(v) : (Math.abs(v) >= 1e6 ? (v/1e6).toFixed(1)+'M' : Math.abs(v) >= 1e4 ? Math.round(v).toLocaleString() : v.toFixed(dp));
      return cell(label, (signed && v > 0 ? '+' : '') + shown, col);
    };
    flow.innerHTML = [
      sCell('CVD 1m (BTC)', 'cvd_1m', 2, true),
      sCell('CVD 5m (BTC)', 'cvd_5m', 2, true),
      sCell('Book imbalance', 'imbalance', 3, true),
      sCell('OBI top-5', 'obi_5', 3, true),
      sCell('Large-trade delta', 'large_trade_delta', 3, true),
      sCell('VPIN (toxicity)', 'vpin', 3, false),
      sCell('Absorption', 'absorption_ratio', 3, false),
      sCell('Spread (bps)', 'spread_bps', 2, false),
      sCell('Funding rate', 'funding_rate', 6, true),
      sCell('OI change %', 'oi_change', 3, true),
      sCell('Liq imbalance $', 'liq_imbalance', 0, true),
      sCell('Coinbase premium $', 'coinbase_premium', 2, true),
    ].join('') || '<div class="fh-empty">Waiting for live flow data…</div>';
  }

  // ── Per-horizon model accuracy — LEAN sign-truth first (every raw lean, the
  // betting metric), committed-call accuracy second (only when the gate fired).
  const accDiv = document.getElementById('binance-accuracy');
  if (accDiv) {
    accDiv.innerHTML = ROSTER_HORIZONS.map(h => {
      const a = accMap[h] || accMap[String(h)] || {};
      if (!a.lean_total && !a.directional_total) {
        return `<div style="background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07);border-radius:8px;padding:.6rem .8rem">
          <strong>${h}m</strong><div style="color:var(--text-secondary);font-size:.8em">no resolved leans yet</div></div>`;
      }
      const l = (a.lean_accuracy || 0) * 100;
      const lCol = l >= 55 ? '#00e676' : l >= 48 ? '#ffb74d' : '#ff5252';
      const upS = a.lean_up_total ? `${((a.lean_up_accuracy||0)*100).toFixed(0)}% (${a.lean_up_total})` : '—';
      const dnS = a.lean_down_total ? `${((a.lean_down_accuracy||0)*100).toFixed(0)}% (${a.lean_down_total})` : '—';
      const com = a.directional_total
        ? `committed ${((a.directional_accuracy||0)*100).toFixed(0)}% (${a.directional_total})`
        : 'gate: all waits so far';
      return `<div style="background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07);border-radius:8px;padding:.6rem .8rem">
        <div style="display:flex;justify-content:space-between"><strong>${h}m</strong>
          <strong style="color:${a.lean_total?lCol:'var(--text-secondary)'}">${a.lean_total?`${l.toFixed(0)}% <span style="font-weight:400;color:var(--text-secondary)">(${a.lean_total} leans)</span>`:'—'}</strong></div>
        <div style="font-size:.78em;color:var(--text-secondary);margin-top:.3rem">
          UP <span style="color:#00e676">${upS}</span> · DOWN <span style="color:#ff5252">${dnS}</span></div>
        <div style="font-size:.72em;color:var(--text-secondary);margin-top:.2rem">${com}</div>
      </div>`;
    }).join('');
  }

  // ── Recent calls log: per-timeframe tabs, every resolved lean graded by SIGN-TRUTH.
  // Sourced from verification.histories (up to 30 per horizon) so each tab is full.
  const logDiv = document.getElementById('binance-log');
  if (logDiv) {
    const hist = (data.verification || {}).histories || {};
    const allRows = [];
    ROSTER_HORIZONS.forEach(h => {
      (hist[h] || hist[String(h)] || []).forEach(r => { if (r.horizon == null) r.horizon = h; allRows.push(r); });
    });
    // Only committed directional leans grade as correct/incorrect.
    const dirRows = allRows.filter(r => r.raw_direction === 'UP' || r.raw_direction === 'DOWN');
    const tfs = ['all', 1, 3, 5, 7, 10, 15];
    const tabs = tfs.map(tf => {
      const cnt = tf === 'all' ? dirRows.length : dirRows.filter(r => r.horizon === tf).length;
      const on = String(binanceLogTF) === String(tf);
      return `<button onclick="window.__binanceLogTF('${tf}')" style="background:${on?'rgba(243,186,47,.2)':'rgba(255,255,255,.04)'};
        border:1px solid ${on?'#f3ba2f':'rgba(255,255,255,.1)'};color:${on?'#f3ba2f':'var(--text-secondary)'};
        border-radius:6px;padding:.15rem .7rem;margin-right:.4rem;cursor:pointer;font-size:.8em">${tf==='all'?'All':tf+'m'} (${cnt})</button>`;
    }).join('');
    const sel = binanceLogTF === 'all' ? dirRows : dirRows.filter(r => String(r.horizon) === String(binanceLogTF));
    sel.sort((a,b) => (b.verified_at||b.timestamp||0) - (a.verified_at||a.timestamp||0));
    const winOf = (r) => {
      const move = Number(r.actual_move_usd || 0);
      return r.lean_hit != null ? r.lean_hit : (move !== 0 ? ((r.raw_direction === 'UP') === (move > 0)) : null);
    };
    // Per-tab W/L summary line (like the Polymarket table)
    let summary = '';
    if (sel.length) {
      const graded = sel.filter(r => winOf(r) !== null);
      const w = graded.filter(r => winOf(r) === true).length;
      const up = graded.filter(r => r.raw_direction === 'UP'), dn = graded.filter(r => r.raw_direction === 'DOWN');
      const pct = (a,b) => b ? `${(a/b*100).toFixed(0)}%` : '—';
      summary = `<div style="margin:.2rem 0 .5rem;font-size:.85em;color:var(--text-secondary)">
        <strong style="color:var(--text-primary)">${graded.length} graded</strong> ·
        <span style="color:#00e676">${w} ✓</span> / <span style="color:#ff5252">${graded.length-w} ✗</span> (${pct(w,graded.length)})
        · UP <span style="color:#00e676">${pct(up.filter(r=>winOf(r)).length, up.length)}</span> (${up.length})
        · DOWN <span style="color:#ff5252">${pct(dn.filter(r=>winOf(r)).length, dn.length)}</span> (${dn.length})</div>`;
    }
    const rows = sel.slice(0, 25).map(r => {
      const move = Number(r.actual_move_usd || 0);
      const win = winOf(r);
      const act = (r.signal === 'NEUTRAL' || r.signal === 'WAIT' || r.direction === 'NEUTRAL') ? 'waited' : 'traded';
      return `<div class="log-row ${win===true?'log-hit-row':win===false?'log-miss-row':''}">
        <span>${etTime(r.verified_at || r.timestamp)}</span>
        <span>${r.horizon}m</span>
        <span style="color:${r.raw_direction==='UP'?'#00e676':'#ff5252'}">${r.raw_direction} <span style="color:var(--text-secondary)">(${act})</span></span>
        <span>${r.confidence!=null?(r.confidence*100).toFixed(0)+'%':'—'}</span>
        <span style="color:${move>=0?'#00e676':'#ff5252'}">${move>=0?'+':''}$${Math.abs(move).toFixed(0)}${move<0?' ↓':' ↑'}</span>
        <span class="${win===true?'log-hit':win===false?'log-miss':'log-pending'}">${win===true?'✓ CORRECT':win===false?'✗ WRONG':'— flat'}</span>
      </div>`;
    }).join('');
    logDiv.innerHTML = `<div style="margin-bottom:.5rem">${tabs}</div>${summary}`
      + (rows ? `<div class="log-row log-head"><span>Time</span><span>TF</span><span>Lean</span><span>Conf</span><span>Realized</span><span>Result</span></div>${rows}`
              : '<div class="log-empty">No resolved directional calls for this timeframe yet.</div>');
  }
}

// Per-timeframe filter for the Binance recent-calls log
let binanceLogTF = 'all';
window.__binanceLogTF = (tf) => {
  binanceLogTF = (tf === 'all') ? 'all' : Number(tf);
  if (lastPlainData) renderBinanceView(lastPlainData);
};

// ══════════════════════════════════════════════
//  POLYMARKET VIEW — Pyth-anchored 5m/15m price-to-beat
// ══════════════════════════════════════════════
// Two feed-anchored variants of the SAME up/down game. The Pyth one (existing
// Polymarket tab) is unchanged; the Binance one reads a parallel payload key and the
// live Binance price. Shared core so they never drift.
const PM_CFG = {
  pyth:    {p:'pm',  ptbKey:'price_to_beat',         priceField:'pyth_price', hasAge:true,
            stripLabel:'Pyth BTC/USD (Polymarket settlement proxy)', beatLabel:'Pyth'},
  binance: {p:'bpm', ptbKey:'price_to_beat_binance', priceField:'price',      hasAge:false,
            stripLabel:"Binance BTC/USD (live exchange feed — the model's native data)", beatLabel:'Binance'},
};
function renderPolymarketView(data){ renderPMCore(data, PM_CFG.pyth); }
function renderBinancePolymarketView(data){ renderPMCore(data, PM_CFG.binance); }
function renderPMCore(data, cfg) {
  const P = cfg.p;
  const grid = document.getElementById(P+'-grid');
  const strip = document.getElementById(P+'-price-strip');
  if (!grid) return;
  const anchor = data[cfg.priceField]!=null?Number(data[cfg.priceField]):null;
  const pyth = data.pyth_price!=null?Number(data.pyth_price):null;
  const binance = Number(data.price||0);
  if (strip) {
    const age = cfg.hasAge ? data.pyth_price_age_s : null;
    const stale = age!=null && age>10;
    const otherTxt = cfg.p==='pm'
      ? `<span style="color:var(--text-secondary);margin-left:1rem;font-size:.8em">Binance: $${binance.toLocaleString()}${pyth!=null?` (Δ ${(binance-pyth)>=0?'+':''}${(binance-pyth).toFixed(0)})`:''}</span>`
      : `<span style="color:var(--text-secondary);margin-left:1rem;font-size:.8em">Pyth: ${pyth!=null?'$'+pyth.toLocaleString():'—'}${pyth!=null?` (Δ ${(binance-pyth)>=0?'+':''}${(binance-pyth).toFixed(0)})`:''}</span>`;
    strip.innerHTML = `<span style="color:var(--text-secondary);font-size:.7em;text-transform:uppercase;letter-spacing:.6px">${cfg.stripLabel}</span>
      <strong style="font-size:1.4em;margin-left:.5rem;color:${stale?'#ffb74d':'#00e676'}">${anchor!=null?'$'+anchor.toLocaleString(undefined,{minimumFractionDigits:2}):'connecting…'}</strong>
      ${stale?'<span style="color:#ffb74d;font-size:.7em"> (stale '+age+'s → using Binance)</span>':''}
      ${otherTxt}`;
  }
  const ptb = data[cfg.ptbKey] || {};
  const latest = ptb.latest || {};
  const acc = ptb.accuracy || {};
  // Only 5m/15m are real Polymarket markets; 1m/3m/7m/10m are PRACTICE mirrors —
  // same rule + grading, fast evidence, not bettable.
  grid.innerHTML = [1,3,5,7,10,15].map(h => {
    const r = latest[h] || latest[String(h)];
    const a = acc[h] || acc[String(h)] || {};
    const accStr = a.total ? `model ${((a.model_accuracy||0)*100).toFixed(0)}% (${a.model_total||0}) · all ${((a.accuracy||0)*100).toFixed(0)}% (${a.total})` : 'no rounds yet';
    if (!r) return `<div style="border:1px solid #334;border-radius:10px;padding:1rem"><strong>${h}m</strong><div class="fh-empty">Waiting for first ${h}m window…</div></div>`;
    const dir = r.our_direction||'NEUTRAL';
    const col = dir==='UP'?'#00e676':dir==='DOWN'?'#ff5252':'#8892a6';
    const cfl = r.confluence||{}; const po = r.path_outlook||{}; const adv = r.advice||{};
    const tone = adv.tone==='good'?'#00e676':adv.tone==='bad'?'#ff5252':adv.tone==='warn'?'#ffb74d':'var(--text-secondary)';
    const resolved = r.status==='resolved';
    const srcBadge = r.lean_source==='fallback'
      ? '<span style="background:rgba(255,183,77,.15);color:#ffb74d;border-radius:4px;padding:0 .35rem;font-size:.7em">⚠ WEAK (fallback) — skip</span>'
      : dir!=='NEUTRAL' ? '<span style="background:rgba(0,230,118,.12);color:#00e676;border-radius:4px;padding:0 .35rem;font-size:.7em">MODEL lean</span>' : '';
    const phPct = (r.p_hold!=null) ? Math.round(r.p_hold*100) : null;
    const lateChip = r.late_entry
      ? `<span style="background:rgba(100,181,246,.15);color:#64b5f6;border-radius:4px;padding:0 .35rem;font-size:.7em;margin-left:.4rem">⚡ LATE-ENTRY edge${phPct!=null?` · ${phPct}% hold`:''}</span>` : '';
    const practice = (h!==5&&h!==15) ? ' <span style="background:rgba(255,255,255,.08);color:var(--text-secondary);border-radius:4px;padding:0 .35rem;font-size:.6em;vertical-align:middle">PRACTICE — no real market</span>' : '';
    return `<div style="border:1px solid ${col}44;border-left:4px solid ${col};border-radius:10px;padding:1rem 1.2rem;background:rgba(255,255,255,.02)">
      <div style="display:flex;justify-content:space-between"><strong style="font-size:1.15em">${h}m · ${r.window_label||''}${practice}</strong>
        <span style="font-size:.8em;color:var(--text-secondary)">${accStr}</span></div>
      <div style="margin:.5rem 0"><span style="color:var(--text-secondary)">Price to beat (${cfg.beatLabel}):</span>
        <strong style="font-size:1.15em"> $${Number(r.price_to_beat||0).toLocaleString()}</strong>
        ${r.ref_captured_late_ms?`<span style="color:#ffb74d;font-size:.7em"> (late anchor capture +${(r.ref_captured_late_ms/1000).toFixed(1)}s)</span>`:''}</div>
      <div style="font-size:1.2em;color:${col};font-weight:700">${dirArrow(dir)} ${dir==='NEUTRAL'?'NO LEAN — no bet':dir} ${cfl.grade?`· Grade ${cfl.grade}`:''} ${srcBadge}${lateChip}</div>
      ${!resolved&&r.current_price!=null?`<div style="margin-top:.4rem;font-size:.9em">Now: <strong>$${Number(r.current_price).toLocaleString()}</strong>
        <span style="color:${(r.current_move||0)>=0?'#00e676':'#ff5252'}"> (${(r.current_move||0)>=0?'+':''}$${Math.round(r.current_move||0)} → ${r.current_position||''} side)</span>
        · <strong>${r.seconds_left!=null?Math.max(0,Math.round(r.seconds_left))+'s left':''}</strong>
        ${r.live_lean&&r.live_lean!==dir?`<span style="color:#ffb74d"> · live lean now ${r.live_lean}</span>`:''}</div>`:''}
      ${!resolved&&r.p_hold!=null&&r.current_position?`<div style="margin-top:.25rem;font-size:.82em;color:var(--text-secondary)">🎯 Calibrated <strong style="color:${r.p_hold>=0.93?'#64b5f6':'var(--text-secondary)'}">P(hold ${r.current_position})=${Math.round(r.p_hold*100)}%</strong> — odds the ${r.current_position} side survives to close (A1/T3 persistence model; ⚡ fires at ≥93%)</div>`:''}
      ${!resolved&&r.live_expected_move!=null&&dir!=='NEUTRAL'?`<div style="margin-top:.4rem;padding:.4rem .6rem;border-radius:6px;background:rgba(255,255,255,.03);font-size:.85em">
        📐 Typical <strong style="color:${col}">${dir==='UP'?'rise':'drop'} for this setup ≈ $${Math.round(Math.abs(r.live_expected_move))}</strong>${r.expected_move_range?` <span style="color:var(--text-secondary)">· 50% band $${Math.round(Math.abs(r.expected_move_range.low))}–$${Math.round(Math.abs(r.expected_move_range.high))} (tails run larger)</span>`:''}
        ${r.projected_close!=null?`→ projects close <strong>$${Number(r.projected_close).toLocaleString()}</strong>
          <span style="color:${(r.projected_vs_beat||0)>=0?'#00e676':'#ff5252'}">(${(r.projected_vs_beat||0)>=0?'+':''}$${Math.round(r.projected_vs_beat||0)} vs beat → ${(r.projected_vs_beat||0)>=0?'UP':'DOWN'} resolves)</span>`:''}
        <div style="color:var(--text-secondary);font-size:.85em;margin-top:.2rem">This is the median move size for current conditions, not a path call — a $100+ window lands outside the band ~50% of the time by design.</div></div>`:''}
      ${po.scenario?`<div style="margin-top:.5rem;padding:.4rem .6rem;border-radius:6px;background:rgba(100,181,246,.08);font-size:.85em">🧭 <strong>${po.scenario}</strong> — ${po.text||''}</div>`:''}
      ${!resolved&&adv.action?`<div style="margin-top:.5rem;padding:.4rem .6rem;border:1px solid ${tone};border-radius:6px;font-size:.85em"><strong style="color:${tone}">${adv.action}</strong> — ${adv.text||''}</div>`:''}
      ${resolved?`<div style="margin-top:.5rem;font-weight:700;color:${r.hit?'#00e676':r.hit===false?'#ff5252':'#8892a6'}">${r.hit?'✓ WON':r.hit===false?'✗ LOST':'— no bet'} (closed $${Number(r.actual_price||0).toLocaleString()}, ${(r.move||0)>=0?'+':''}$${Math.round(r.move||0)})</div>`:''}
    </div>`;
  }).join('');

  // win-rate strips: per-horizon model/all split + win rate by setup grade & source
  const rec = ptb.recent || [];
  const accDiv = document.getElementById(P+'-accuracy');
  if (accDiv) {
    accDiv.innerHTML = [1,3,5,7,10,15].map(h=>{
      const a=acc[h]||acc[String(h)]||{};
      if(!a.total) return '';
      // "model 0% (0)" read as a recording failure — show "—" when the model has
      // never committed a lean at this horizon (1m: NEUTRAL is usually its honest
      // answer, so the mirror runs on ⚠ fallback tilts only).
      const m = a.model_total ? `<strong style="color:#00e676">${((a.model_accuracy||0)*100).toFixed(0)}%</strong> (${a.model_total})` : `<span style="color:var(--text-secondary)">— (no committed leans)</span>`;
      return `<span style="margin-right:1.5rem"><strong>${h}m${(h!==5&&h!==15)?'*':''}:</strong> model ${m} · all ${((a.accuracy||0)*100).toFixed(0)}% (${a.total})</span>`;
    }).join('')
      + '<span style="color:var(--text-secondary);font-size:.75em">* practice mirror — not a real Polymarket market</span>';
  }
  const gradeDiv = document.getElementById(P+'-grade-stats');
  if (gradeDiv) {
    // Headline stats over REAL markets ONLY (5m/15m). The 1m/3m practice mirrors
    // fire ~5x faster and were flooding the rolling window (25 of "last 40"),
    // making the grade/lean summary describe practice luck instead of bettable
    // markets. Practice gets its own muted line.
    const resolvedRows = rec.filter(r=>r.hit!=null);
    const realRows = resolvedRows.filter(r=>r.horizon===5||r.horizon===15);
    const pracRows = resolvedRows.filter(r=>r.horizon!==5&&r.horizon!==15);
    const bucket = (label, rows) => {
      const n = rows.length; if (!n) return '';
      const w = rows.filter(r=>r.hit===true).length;
      return `<span style="margin-right:1.2rem">${label}: <strong style="color:${w/n>=0.5?'#00e676':'#ff5252'}">${(w/n*100).toFixed(0)}%</strong> (${w}/${n})</span>`;
    };
    const html = [
      bucket('Grade A', realRows.filter(r=>((r.confluence||{}).grade||'').startsWith('A'))),
      bucket('Grade B', realRows.filter(r=>(r.confluence||{}).grade==='B')),
      bucket('Grade C', realRows.filter(r=>(r.confluence||{}).grade==='C')),
      bucket('Model leans', realRows.filter(r=>r.lean_source!=='fallback')),
      bucket('Fallback leans', realRows.filter(r=>r.lean_source==='fallback')),
    ].join('');
    const prac = pracRows.length
      ? `<div style="color:var(--text-secondary);font-size:.75em;margin-top:.15rem">practice mirrors (1m/3m/7m/10m, not bettable): ${bucket('model', pracRows.filter(r=>r.lean_source!=='fallback'))}${bucket('⚠ fallback', pracRows.filter(r=>r.lean_source==='fallback'))}</div>`
      : '';
    gradeDiv.innerHTML = (html ? `<span style="color:var(--text-secondary);font-size:.8em;margin-right:.8rem">Last ${realRows.length} REAL rounds (5m/15m):</span>${html}` : '') + prac;
  }
  const recDiv = document.getElementById(P+'-recent');
  if (recDiv) {
    const resolved = rec.filter(r=>r.hit!=null);
    const tfs = ['all', 1, 3, 5, 7, 10, 15];
    const tabs = tfs.map(tf => {
      const cnt = tf==='all' ? resolved.length : resolved.filter(r=>r.horizon===tf).length;
      const on = String(pmLogTF)===String(tf);
      return `<button onclick="window.__pmLogTF('${tf}')" style="background:${on?'rgba(100,181,246,.2)':'rgba(255,255,255,.04)'};
        border:1px solid ${on?'#64b5f6':'rgba(255,255,255,.1)'};color:${on?'#64b5f6':'var(--text-secondary)'};
        border-radius:6px;padding:.15rem .7rem;margin-right:.4rem;cursor:pointer;font-size:.8em">${tf==='all'?'All':tf+'m'} (${cnt})</button>`;
    }).join('');
    const tfRows = (pmLogTF==='all' ? resolved : resolved.filter(r=>String(r.horizon)===String(pmLogTF)));
    // Per-tab W/L summary: gives every timeframe (incl. 1m, where the model never
    // commits) its full win/lose record, split by lean source — without faking a
    // "model %" where no committed leans exist.
    let tfSummary = '';
    if (tfRows.length) {
      const w = tfRows.filter(r=>r.hit===true).length;
      const mdl = tfRows.filter(r=>r.lean_source!=='fallback');
      const fb = tfRows.filter(r=>r.lean_source==='fallback');
      const mw = mdl.filter(r=>r.hit===true).length, fw = fb.filter(r=>r.hit===true).length;
      const pct = (a,b)=>b?`${(a/b*100).toFixed(0)}%`:'—';
      tfSummary = `<div style="margin:.2rem 0 .5rem;font-size:.85em;color:var(--text-secondary)">
        <strong style="color:var(--text-primary)">${tfRows.length} rounds</strong> ·
        <span style="color:#00e676">${w} WON</span> / <span style="color:#ff5252">${tfRows.length-w} LOST</span> (${pct(w,tfRows.length)})
        · model: ${mdl.length?`<strong style="color:#00e676">${pct(mw,mdl.length)}</strong> (${mw}/${mdl.length})`:'<span>— none committed</span>'}
        · ⚠ fallback: ${fb.length?`${pct(fw,fb.length)} (${fw}/${fb.length})`:'—'}</div>`;
    }
    const shown = tfRows.slice(0,25);  // up to 25 per timeframe; container scrolls
    const rows = shown.map(r=>{
      const win=r.hit===true; const col=win?'#00e676':'#ff5252';
      const src = r.lean_source==='fallback'?' ⚠':'';
      return `<div class="log-row ${win?'log-hit-row':'log-miss-row'}"><span>${etTime(r.timestamp)}</span><span>${r.horizon}m</span>
        <span style="color:${r.our_direction==='UP'?'#00e676':'#ff5252'}">${r.our_direction}${src}</span>
        <span>${(r.confluence||{}).grade||'-'}</span>
        <span>$${Number(r.price_to_beat||0).toLocaleString()} → $${Number(r.actual_price||0).toLocaleString()}</span>
        <span style="color:${col};font-weight:600">${win?'✓ WON':'✗ LOST'}</span></div>`;
    }).join('');
    recDiv.innerHTML = `<div style="margin-bottom:.5rem">${tabs}</div>${tfSummary}`
      + (rows ? `<div class="log-row log-head"><span>Time</span><span>TF</span><span>Lean</span><span>Grade</span><span>Beat → Close</span><span>Result</span></div>${rows}`
              : '<div class="log-empty">No resolved rounds for this timeframe yet.</div>');
  }
}

// Per-timeframe filter for the Polymarket resolved-rounds log
let pmLogTF = 'all';
window.__pmLogTF = (tf) => {
  pmLogTF = (tf === 'all') ? 'all' : Number(tf);
  if (!lastPlainData) return;
  if (currentAppTab === 'binancepm') renderBinancePolymarketView(lastPlainData);
  else renderPolymarketView(lastPlainData);
};

function renderModelsView(data) {
  renderFeedHealth(data);
  renderTrainingSignals(data);
  renderBestLongShort(data);
  renderForecastScorecard(data);
  renderPriceToBeatTabbed(data);
  renderPriceToBeatConfluence(data);
  renderModelRoster(data);
  renderModelInventory(data);
}

// ── Live Training Signals: the exact per-candle values the recorder writes ──
// Grouped + labeled subset of payload.training_signals, color-coded by sign so the
// operator can watch the model's inputs move in real time.
const TS_GROUPS = [
  ['Order Flow', [
    ['cvd_1m', 'CVD 1m (buy−sell BTC)', 2, true],
    ['cvd_5m', 'CVD 5m', 2, true],
    ['imbalance', 'Book imbalance', 3, true],
    ['obi_5', 'OBI top-5', 3, true],
    ['trade_intensity', 'Trades / sec', 1, false],
    ['spread_bps', 'Spread (bps)', 2, false],
  ]],
  ['Big Players & Toxicity', [
    ['large_trade_delta', 'Large-trade delta', 3, true],
    ['large_trade_imbalance', 'Large-trade imbalance', 3, true],
    ['vpin', 'VPIN (toxicity 0-1)', 3, false],
    ['absorption_ratio', 'Absorption', 3, false],
    ['spoof_score', 'Spoof score', 3, false],
    ['liquidity_sweep_bullish', 'Bull sweep (60s)', 0, true],
  ]],
  ['L2 Microstructure (recorded for V2)', [
    ['microprice_edge_bps', 'Microprice edge (bps)', 3, true],
    ['ofi_best', 'OFI best-level', 3, true],
    ['wall_imbalance', 'Wall imbalance', 3, true],
    ['queue_pressure', 'Queue pressure', 3, true],
  ]],
  ['Derivatives & Macro', [
    ['funding_rate', 'Funding rate', 6, true],
    ['oi_change', 'OI change %', 3, true],
    ['liq_imbalance', 'Liq imbalance $', 0, true],
    ['coinbase_premium', 'Coinbase premium $', 2, true],
    ['fear_greed', 'Fear & Greed', 0, false],
    ['ls_ratio', 'Long/Short ratio', 3, false],
  ]],
];

function renderTrainingSignals(data) {
  const grid = document.getElementById('training-signals-grid');
  if (!grid) return;
  const ts = data.training_signals;
  if (!ts || !Object.keys(ts).length) {
    grid.innerHTML = '<div class="fh-empty">Waiting for live data…</div>';
    return;
  }
  let html = '';
  for (const [group, fields] of TS_GROUPS) {
    html += `<div style="grid-column:1/-1;color:var(--text-secondary);font-size:.78em;
      text-transform:uppercase;letter-spacing:.6px;margin-top:.4rem">${group}</div>`;
    for (const [key, label, dp, signed] of fields) {
      const raw = ts[key];
      if (raw == null) continue;
      const v = Number(raw);
      const col = !signed ? 'var(--text-primary)'
        : v > 0 ? '#00e676' : v < 0 ? '#ff5252' : 'var(--text-secondary)';
      const shown = Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(1) + 'M'
        : Math.abs(v) >= 1e4 ? Math.round(v).toLocaleString()
        : v.toFixed(dp);
      html += `<div style="background:var(--bg-secondary,rgba(255,255,255,.03));
          border:1px solid rgba(255,255,255,.07);border-radius:8px;padding:.5rem .7rem">
        <div style="font-size:.72em;color:var(--text-secondary)">${label}</div>
        <div style="font-weight:700;font-size:1.05em;color:${col}">${signed && v > 0 ? '+' : ''}${shown}</div>
      </div>`;
    }
  }
  grid.innerHTML = html;
}

function renderFeedHealth(data) {
  if (!els.feedHealthGrid) return;
  const fh = data.feed_health;
  if (!fh) { els.feedHealthGrid.innerHTML = '<div class="fh-empty">Waiting for buffer…</div>'; return; }
  const s = fh.summary || {};
  const cov = fh.candle_coverage_pct;
  const staleReason = fh.stale
    ? 'No closed 1-minute signal snapshot for over 3 minutes. Usual causes: Binance kline stream disconnected, backend loop blocked by heavy work, or the app is waiting for the next candle-close snapshot after restart.'
    : 'Closed-candle signal snapshots are current.';
  const staleTxt = fh.stale ? '<span class="fh-stale">⚠ stale</span>' : '<span class="fh-fresh">● live</span>';
  const ageTxt = fh.last_snapshot_age_s != null ? `${Math.round(fh.last_snapshot_age_s)}s ago` : '—';

  if (els.feedHealthSummary) {
    els.feedHealthSummary.innerHTML = `
      <div class="fh-stat"><span>Candle coverage</span><strong>${cov != null ? cov.toFixed(1) + '%' : '—'}</strong><small>live snapshots aligned to training candles</small></div>
      <div class="fh-stat"><span>Snapshots buffered</span><strong>${(fh.snapshots || 0).toLocaleString()}</strong><small>≈ minutes of live coverage accrued</small></div>
      <div class="fh-stat"><span>Live fields</span><strong>${s.alive || 0}/${s.total || 0}</strong><small>${s.sparse || 0} sparse · ${s.dead || 0} dead · ${s.absent || 0} absent</small></div>
      <div class="fh-stat"><span>Feed status</span><strong>${staleTxt}</strong><small>last snapshot ${ageTxt}</small></div>`;
    els.feedHealthSummary.insertAdjacentHTML(
      'beforeend',
      `<div class="fh-note ${fh.stale ? 'stale' : 'fresh'}">${staleReason}</div>`
    );
  }

  const order = { alive: 0, sparse: 1, dead: 2, absent: 3 };
  const entries = Object.entries(fh.fields || {})
    .sort((a, b) => (order[a[1].status] - order[b[1].status]) || a[0].localeCompare(b[0]));
  els.feedHealthGrid.innerHTML = entries.map(([name, f]) => {
    const col = { alive: 'var(--green)', sparse: 'var(--gold)', dead: 'var(--red)', absent: 'var(--text-muted)' }[f.status] || 'var(--text-muted)';
    return `<div class="fh-cell" title="${name}: present ${f.present}%, non-zero ${f.nonzero}%">
      <span class="fh-dot" style="background:${col}"></span>
      <span class="fh-name">${name}</span>
      <span class="fh-pct">${f.nonzero}%</span>
    </div>`;
  }).join('');
}

function renderBestLongShort(data) {
  if (!els.longshortGrid) return;
  const preds = data.predictions || [];
  if (!preds.length) {
    els.longshortGrid.innerHTML = '<div class="ls-empty">Waiting for predictions…</div>';
    return;
  }
  const curPrice = data.price || 0;
  // Rank by the model's own up/down probability so we always have a long & a short view,
  // even when the final gated signal is AVOID.
  const bestLong = [...preds].sort((a, b) => (b.probUp || 0) - (a.probUp || 0))[0];
  const bestShort = [...preds].sort((a, b) => (b.probDown || 0) - (a.probDown || 0))[0];

  const card = (side, p, prob) => {
    const col = side === 'LONG' ? 'var(--green)' : 'var(--red)';
    const move = Math.abs(p.expectedMove || 0);
    const target = side === 'LONG' ? curPrice + move : curPrice - move;
    const conv = Math.round(p.conviction || 0);
    const grade = p.convictionGrade || 'WATCH';
    const gated = p.actionable ? 'ACTIONABLE (passes risk gate)' : 'lean only — gate says AVOID';
    return `<div class="ls-card" style="border-color:${col}">
      <div class="ls-head"><span class="ls-side" style="color:${col}">${side === 'LONG' ? '▲ BEST LONG' : '▼ BEST SHORT'}</span><span class="ls-tf">${p.horizon}m</span></div>
      <div class="ls-prob" style="color:${col}">${(prob * 100).toFixed(0)}%<small>model ${side === 'LONG' ? 'P(up)' : 'P(down)'}</small></div>
      <div class="ls-meta">
        <span>Entry ≈ <strong>$${curPrice.toLocaleString(undefined,{maximumFractionDigits:0})}</strong></span>
        <span>Target ≈ <strong style="color:${col}">$${target.toLocaleString(undefined,{maximumFractionDigits:0})}</strong></span>
        <span>Move ≈ <strong>$${Math.round(move).toLocaleString()}</strong></span>
        <span>Conviction <strong>${conv}</strong> (${grade})</span>
      </div>
      <div class="ls-gate ${p.actionable ? 'on' : 'off'}">${gated}</div>
    </div>`;
  };

  els.longshortGrid.innerHTML =
    card('LONG', bestLong, bestLong.probUp || 0) +
    card('SHORT', bestShort, bestShort.probDown || 0);
}

function renderForecastScorecard(data) {
  if (!els.forecastScorecard) return;
  const ens = (data.verification && data.verification.accuracy) || {};
  const horizons = [1, 3, 5, 7, 10, 15];
  const pct = (v) => (v != null ? `${(v * 100).toFixed(0)}%` : '—');
  const usd = (v) => (v ? `$${Math.round(v).toLocaleString()}` : '—');

  const head = `<div class="sc-row sc-head">
    <span>Horizon</span><span>Directional acc</span><span>UP acc</span><span>DOWN acc</span><span>Avg move err</span>
  </div>`;
  const rows = horizons.map((h) => {
    const e = ens[h] || ens[String(h)] || {};
    const dAcc = e.directional_total ? pct(e.directional_accuracy) : '—';
    const uAcc = e.up_total ? pct(e.up_accuracy) : '—';
    const dnAcc = e.down_total ? pct(e.down_accuracy) : '—';
    const eErr = (e.directional_total && e.avg_move_error_usd) ? usd(e.avg_move_error_usd) : '—';
    return `<div class="sc-row">
      <span class="sc-h">${h}m</span>
      <span>${dAcc}<small>${e.directional_total ? e.directional_total + 'n' : ''}</small></span>
      <span style="color:#00e676">${uAcc}<small>${e.up_total ? e.up_total + 'n' : ''}</small></span>
      <span style="color:#ff5252">${dnAcc}<small>${e.down_total ? e.down_total + 'n' : ''}</small></span>
      <span>${eErr}</span>
    </div>`;
  }).join('');
  els.forecastScorecard.innerHTML = head + rows +
    `<div class="sc-note">Committed UP/DOWN calls graded by realized direction. UP/DOWN split is the bias watch. "Err" = average |forecast − actual| in USD.</div>`;
}

function dirColor(d) {
  return d === 'UP' ? '#00e676' : d === 'DOWN' ? '#ff1744' : '#8892a6';
}
function dirArrow(d) {
  return d === 'UP' ? '▲' : d === 'DOWN' ? '▼' : '●';
}

function renderPriceToBeat(data) {
  if (!els.ptbGrid) return;
  const ptb = data.price_to_beat || {};
  const latest = ptb.latest || {};
  const acc = ptb.accuracy || {};

  els.ptbGrid.innerHTML = PTB_HORIZONS.map((h) => {
    const r = latest[h];
    const a = acc[h] || {};
    const accStr = a.total
      ? (a.model_total
          ? `model ${(a.model_accuracy * 100).toFixed(0)}% (${a.model_total}) · all ${(a.accuracy * 100).toFixed(0)}% (${a.total})`
          : `${(a.accuracy * 100).toFixed(0)}% · ${a.hits}/${a.total}`)
      : 'no resolved rounds yet';
    if (!r) {
      return `<div class="ptb-card">
        <div class="ptb-head"><span class="ptb-tf">${h}m</span><span class="ptb-acc">${accStr}</span></div>
        <div class="ptb-empty">Waiting for the first ${h}m window…</div>
      </div>`;
    }
    const dir = r.our_direction || 'NEUTRAL';
    const action = r.actionable
      ? (dir === 'UP' ? 'STRONG BUY' : dir === 'DOWN' ? 'STRONG SELL' : 'WAIT')
      : (dir === 'NEUTRAL' ? 'WAIT' : `lean ${dir.toLowerCase()}`);
    const win = r.window_label || '';
    const resolved = r.status === 'resolved';
    let result = `<span class="ptb-pending">⏳ open — window ${win}, resolves ${etTime(r.verify_at)}</span>`;
    if (resolved) {
      result = r.hit
        ? `<span class="ptb-hit">✓ correct — closed ${r.actual_direction} ($${Number(r.actual_price).toLocaleString()})</span>`
        : `<span class="ptb-miss">✗ wrong — closed ${r.actual_direction} ($${Number(r.actual_price).toLocaleString()})</span>`;
    }
    return `<div class="ptb-card" style="border-color:${dirColor(dir)}">
      <div class="ptb-head"><span class="ptb-tf">${h}m · ${win}</span><span class="ptb-acc">${accStr}</span></div>
      <div class="ptb-beat">Reference price to beat <strong>$${Number(r.price_to_beat).toLocaleString()}</strong></div>
      <div class="ptb-call" style="color:${dirColor(dir)}">${dirArrow(dir)} ${action}</div>
      <div class="ptb-meta">
        <span>Our call: <strong style="color:${dirColor(dir)}">${dir}</strong></span>
        <span>Kronos: <strong style="color:${dirColor(r.kronos_direction)}">${r.kronos_direction || 'NONE'}</strong></span>
        <span>Conviction: <strong>${Math.round(r.conviction || 0)}</strong></span>
      </div>
      <div class="ptb-result">${result}</div>
    </div>`;
  }).join('');

  // Recent resolved rounds
  if (els.ptbRecent) {
    const recent = (ptb.recent || []).filter(r => r.status === 'resolved').slice(0, 12);
    if (recent.length === 0) {
      els.ptbRecent.innerHTML = '';
    } else {
      els.ptbRecent.innerHTML = `<div class="ptb-recent-head">Recent resolved rounds</div>` +
        recent.map(r => {
          const t = new Date(r.timestamp).toLocaleTimeString();
          const cls = r.hit ? 'log-hit' : 'log-miss';
          const mark = r.hit ? '✓' : '✗';
          return `<div class="ptb-recent-row ${cls}">
            <span>${t}</span><span>${r.horizon}m</span>
            <span style="color:${dirColor(r.our_direction)}">${r.our_direction}</span>
            <span>beat $${Number(r.price_to_beat).toLocaleString()}</span>
            <span>→ $${Number(r.actual_price).toLocaleString()}</span>
            <span>${mark}</span>
          </div>`;
        }).join('');
    }
  }
}

// US Eastern (ET) time label, to match Polymarket's market clock.
function etTime(ts) {
  try { return new Date(ts).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour12: true }) + ' ET'; }
  catch (e) { return new Date(ts).toLocaleTimeString(); }
}

function renderPriceToBeatTabbed(data) {
  if (!els.ptbGrid) return;
  const ptb = data.price_to_beat || {};
  const latest = ptb.latest || {};
  const acc = ptb.accuracy || {};
  const h = activePtbHorizon || 5;
  const r = latest[h] || latest[String(h)];
  const a = acc[h] || acc[String(h)] || {};
  // Split win rates: "model" = the committed 3-class leans (the bets worth taking);
  // "all" includes the weak two-way fallback leans (near coin-flip in live evidence).
  const accStr = a.total
    ? (a.model_total
        ? `model ${(a.model_accuracy * 100).toFixed(0)}% (${a.model_total}) | all ${(a.accuracy * 100).toFixed(0)}% (${a.total})`
        : `${(a.accuracy * 100).toFixed(0)}% | ${a.hits}/${a.total}`)
    : 'no resolved rounds yet';

  const expectedText = (dir) => {
    if (dir === 'UP') return 'Expected: BEAT price';
    if (dir === 'DOWN') return 'Expected: NOT beat price';
    return 'Expected: WAIT / no clear call';
  };
  const expectedDetail = (dir) => {
    if (dir === 'UP') return 'BTC should finish above the reference price.';
    if (dir === 'DOWN') return 'BTC should finish below the reference price.';
    return 'The app did not choose a side; success means price stayed near reference.';
  };
  const actualText = (dir) => {
    if (dir === 'UP') return 'Met: BEAT price';
    if (dir === 'DOWN') return 'Met: did NOT beat price';
    return 'Met: stayed near reference';
  };
  const actionText = (row) => {
    const dir = row?.our_direction || 'NEUTRAL';
    if (row?.actionable && dir === 'UP') return 'BUY / UP';
    if (row?.actionable && dir === 'DOWN') return 'SELL / DOWN';
    if (dir === 'UP') return 'UP lean only';
    if (dir === 'DOWN') return 'DOWN lean only';
    return 'WAIT / AVOID';
  };

  if (!r) {
    els.ptbGrid.innerHTML = `<div class="ptb-card">
      <div class="ptb-head"><span class="ptb-tf">${h}m</span><span class="ptb-acc">${accStr}</span></div>
      <div class="ptb-empty">Waiting for the first ${h}m price-to-beat window.</div>
    </div>`;
  } else {
    const dir = r.our_direction || 'NEUTRAL';
    const win = r.window_label || '';
    const resolved = r.status === 'resolved';
    const reference = Number(r.price_to_beat || 0);
    const actual = Number(r.actual_price || 0);
    const target = r.target_price ? formatUsd(r.target_price, 2) : '--';
    const result = resolved
      ? (r.hit == null
          ? `<span class="ptb-pending">No bet - ${actualText(r.actual_direction)}</span>`
          : (r.hit
              ? `<span class="ptb-hit">Correct - ${actualText(r.actual_direction)}</span>`
              : `<span class="ptb-miss">Incorrect - ${actualText(r.actual_direction)}</span>`))
      : `<span class="ptb-pending">Open - window ${win}, resolves ${etTime(r.verify_at)}</span>`;

    // Live in-window decision aid for a placed bet (hold vs early-exit).
    const adv = r.advice || {};
    const tone = adv.tone === 'good' ? 'var(--green)'
               : adv.tone === 'bad' ? 'var(--red)'
               : adv.tone === 'warn' ? '#ffb74d' : 'var(--text-secondary)';
    const fmtSecs = (s) => { s = Math.max(0, Math.round(s || 0)); const m = Math.floor(s / 60); return m > 0 ? `${m}m ${s % 60}s` : `${s}s`; };
    const cm = Number(r.current_move || 0);
    const liveBlock = (!resolved && adv.action) ? `
      <div class="ptb-live" style="margin-top:.7rem;padding:.7rem .9rem;border:1px solid ${tone};border-radius:8px;background:rgba(255,255,255,0.025)">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.45rem">
          <span style="font-weight:700;color:${tone};letter-spacing:.4px">${adv.action}</span>
          <span style="color:var(--text-secondary);font-size:.82em">${fmtSecs(r.seconds_left)} left to close</span>
        </div>
        <div style="font-size:.9em;margin-bottom:.55rem;color:var(--text-primary)">${adv.text || ''}</div>
        ${r.path_outlook ? `
        <div style="font-size:.86em;margin-bottom:.55rem;padding:.45rem .6rem;border-radius:6px;background:rgba(100,181,246,0.08);border:1px solid rgba(100,181,246,0.25);color:#9fc9f3">
          🧭 <strong>${r.path_outlook.scenario}</strong> — ${r.path_outlook.text}
        </div>` : ''}
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:.5rem;font-size:.82em">
          <div><span style="color:var(--text-secondary)">Now vs beat</span><br><strong style="color:${cm >= 0 ? 'var(--green)' : 'var(--red)'}">${cm >= 0 ? '+' : ''}$${Math.round(cm)} (${r.current_position || '--'})</strong></div>
          <div><span style="color:var(--text-secondary)">Model now</span><br><strong style="color:${dirColor(r.live_lean || 'NEUTRAL')}">${r.live_lean || 'NEUTRAL'}</strong></div>
          <div><span style="color:var(--text-secondary)">Exp. move</span><br><strong>${r.live_expected_move != null ? '$' + Math.round(Math.abs(r.live_expected_move)) : '--'}</strong></div>
        </div>
        ${r.p_hold != null && r.current_position ? `
        <div style="margin-top:.55rem;font-size:.84em;color:var(--text-secondary)">🎯 Calibrated <strong style="color:${r.p_hold >= 0.93 ? '#64b5f6' : 'var(--text-primary)'}">P(hold ${r.current_position}) = ${Math.round(r.p_hold * 100)}%</strong> — odds the ${r.current_position} side survives to close (A1/T3 model; ⚡ late-entry fires at ≥93%)</div>` : ''}
      </div>` : '';

    els.ptbGrid.innerHTML = `<div class="ptb-card ptb-card-wide" style="border-color:${dirColor(dir)}">
      <div class="ptb-head"><span class="ptb-tf">${h}m | ${win}</span><span class="ptb-acc">${accStr}</span></div>
      <div class="ptb-beat">Reference price to beat <strong>${formatUsd(reference, 2)}</strong></div>
      <div class="ptb-call" style="color:${dirColor(dir)}">${dirArrow(dir)} ${actionText(r)}</div>
      <div class="ptb-explain-grid">
        <div><span>Signal</span><strong style="color:${dirColor(dir)}">${dir}</strong><small>${r.actionable ? 'passed risk gate' : 'not actionable / wait'}</small></div>
        <div><span>Expected</span><strong>${expectedText(dir)}</strong><small>${expectedDetail(dir)} Target: ${target}</small></div>
        <div><span>Actual / met</span><strong>${resolved ? actualText(r.actual_direction) : 'Waiting for close'}</strong><small>${resolved ? `Closed ${formatUsd(actual, 2)}` : 'Round still open'}</small></div>
        <div><span>Result</span><strong>${result}</strong><small>Kronos: ${r.kronos_direction || 'NONE'} | Conviction: ${Math.round(r.conviction || 0)}</small></div>
      </div>
      ${liveBlock}
    </div>`;
  }

  if (els.ptbRecent) {
    const recent = (ptb.recent || [])
      .filter(row => row.status === 'resolved' && Number(row.horizon) === h)
      .slice(0, 18);
    if (recent.length === 0) {
      els.ptbRecent.innerHTML = `<div class="ptb-recent-head">${h}m resolved rounds</div><div class="ptb-empty">No resolved ${h}m rounds yet.</div>`;
    } else {
      els.ptbRecent.innerHTML = `<div class="ptb-recent-head">${h}m resolved rounds</div>
        <div class="ptb-recent-row ptb-recent-header">
          <span>Time</span><span>Signal</span><span>Expected</span><span>Actual</span><span>Ref -> Close</span><span>Result</span>
        </div>` +
        recent.map(row => {
          const t = etTime(row.timestamp);
          // hit === null means we had no directional lean = "no bet" (excluded from win-rate).
          const noBet = row.hit == null;
          const cls = noBet ? '' : (row.hit ? 'log-hit' : 'log-miss');
          const mark = noBet ? 'No bet' : (row.hit ? 'Correct' : 'Incorrect');
          const ref = Number(row.price_to_beat || 0);
          const close = Number(row.actual_price || 0);
          return `<div class="ptb-recent-row ${cls}">
            <span>${t}</span>
            <span style="color:${dirColor(row.our_direction)}">${actionText(row)}</span>
            <span>${expectedText(row.our_direction)}</span>
            <span>${actualText(row.actual_direction)}</span>
            <span>${formatUsd(ref, 0)} -> ${formatUsd(close, 0)}</span>
            <span>${mark}</span>
          </div>`;
        }).join('');
    }
  }
}

function renderPriceToBeatConfluence(data) {
  if (!els.ptbConfluenceGrid) return;
  const sb = data.scoreboard || {};
  const ptb = data.price_to_beat || {};
  const latest = ptb.latest || {};
  const acc = ptb.accuracy || {};
  const gradeColor = (g) => ({ 'A+': '#00e676', 'A': '#26c281', 'B': '#ffd700', 'C': '#ff9100', 'WATCH': '#8892a6' }[g] || '#8892a6');
  const chip = (label, ok) => `<span class="cf-chip ${ok ? 'ok' : 'no'}">${ok ? 'ok' : 'x'} ${label}</span>`;
  const expectedText = (dir) => {
    if (dir === 'UP') return 'BEAT price';
    if (dir === 'DOWN') return 'NOT beat price';
    return 'WAIT / no side';
  };

  els.ptbConfluenceGrid.innerHTML = PTB_HORIZONS.map((h) => {
    const s = sb[h] || sb[String(h)] || {};
    const r = latest[h] || latest[String(h)] || {};
    const a = acc[h] || acc[String(h)] || {};
    const dir = s.direction || r.our_direction || 'NEUTRAL';
    const raw = s.rawDirection || dir;
    const directional = ['UP', 'DOWN'].includes(dir);
    const actionable = !!s.actionable && directional;
    const conv = Math.round(s.conviction || r.conviction || 0);
    const grade = s.convictionGrade || (conv >= 85 ? 'A+' : conv >= 70 ? 'A' : conv >= 55 ? 'B' : conv >= 40 ? 'C' : 'WATCH');
    const cd = s.confluenceDetail || {};
    const ourAcc = s.ourAccuracy != null
      ? `${(s.ourAccuracy * 100).toFixed(0)}%`
      : (a.total ? `${(a.accuracy * 100).toFixed(0)}%` : '--');
    const signal = actionable
      ? (dir === 'UP' ? 'BUY / BEAT' : 'SELL / NOT BEAT')
      : (dir === 'NEUTRAL' ? 'WAIT' : `${dir} lean only`);
    const ref = r.price_to_beat ? formatUsd(r.price_to_beat, 2) : '--';
    return `<div class="ptb-conf-card ${actionable ? 'actionable' : ''}" style="border-color:${actionable ? dirColor(dir) : 'var(--border-primary)'}">
      <div class="ptb-conf-head">
        <span>${h}m price-to-beat</span>
        <strong style="background:${gradeColor(grade)}">${grade}</strong>
      </div>
      <div class="ptb-conf-signal" style="color:${dirColor(dir)}">${dirArrow(dir)} ${signal}</div>
      <div class="ptb-conf-bar"><i style="width:${Math.min(100, conv)}%; background:${gradeColor(grade)}"></i><b>${conv}</b></div>
      <div class="ptb-conf-meta">
        <span>Reference <strong>${ref}</strong></span>
        <span>Expected <strong>${expectedText(dir)}</strong></span>
        <span>Raw lean <strong style="color:${dirColor(raw)}">${raw}</strong></span>
      </div>
      <div class="sb-confluence">
        ${chip('models', cd.models_agree)} ${chip('flow', cd.flow_agree)} ${chip('regime', cd.regime_favorable)}
      </div>
      <div class="ptb-conf-acc">
        <span>Ensemble acc ${ourAcc} (${s.ourSamples || a.total || 0}n)</span>
      </div>
    </div>`;
  }).join('');
}

function renderModelRoster(data) {
  if (!els.modelRoster) return;
  const macc = data.model_accuracy || {};
  const dirLabel = (v) => v == null ? '·' : v;

  const header = `<div class="roster-row roster-head">
    <span class="roster-name">Model</span>
    ${ROSTER_HORIZONS.map(h => `<span class="roster-cell">${h}m</span>`).join('')}
  </div>`;

  const renderRow = (label, key, cellsHtml, extraClass = '') => `<div class="roster-row ${extraClass}">
      <span class="roster-name">${label}<small>${key}</small></span>
      ${cellsHtml}
    </div>`;

  const predsByH = {};
  (data.predictions || []).forEach((p) => { predsByH[p.horizon] = p; predsByH[String(p.horizon)] = p; });
  const ensAcc = (data.verification && data.verification.accuracy) || {};

  const ensembleCells = ROSTER_HORIZONS.map((h) => {
    const p = predsByH[h] || predsByH[String(h)] || {};
    const cell = ensAcc[h] || ensAcc[String(h)] || {};
    const vote = p.direction || 'NEUTRAL';
    const accTxt = cell.total ? `${(cell.accuracy * 100).toFixed(0)}%` : '-';
    const accSub = cell.total ? `${cell.hits}/${cell.total}` : 'final gated';
    return `<span class="roster-cell roster-summary-cell">
      <em style="color:${dirColor(vote)}">${vote}</em>
      <b>${accTxt}</b><small>${accSub}</small>
    </span>`;
  }).join('');

  const summaryRows =
    renderRow('Ensemble final', 'gated', ensembleCells, 'roster-summary-row');

  const rows = Object.keys(MODEL_LABELS).map((name) => {
    const row = macc[name] || {};
    const cells = ROSTER_HORIZONS.map((h) => {
      const cell = row[h] || {};
      const vote = dirLabel(cell.latest_vote);
      const accTxt = cell.total ? `${(cell.accuracy * 100).toFixed(0)}%` : '–';
      const accSub = cell.total ? `${cell.hits}/${cell.total}` : '';
      const vc = dirColor(cell.latest_vote);
      return `<span class="roster-cell">
        <em style="color:${vc}">${vote}</em>
        <b>${accTxt}</b><small>${accSub}</small>
      </span>`;
    }).join('');
    return renderRow(MODEL_LABELS[name], name, cells);
  }).join('');

  els.modelRoster.innerHTML = header + summaryRows + rows +
    `<div class="roster-note">Top rows reconcile the final gated ensemble and Kronos path. Base-model rows show each model's <em>current vote</em> and <b>live accuracy</b> (hits/resolved) for that horizon.</div>`;
}

// inventory `installed` uses full names for some models; trained counts are nested by regime
const INV_AVAIL_KEY = { xgb: 'xgboost', lgb: 'lightgbm', cat: 'catboost', histgb: 'histgb', dl: 'dl', lr: 'lr', sgd: 'sgd' };
function renderModelInventory(data) {
  if (!els.modelInventoryGrid) return;
  const inv = data.model_inventory || {};
  const installed = inv.installed || {};
  const byRegime = inv.trained_by_regime || {};
  const device = inv.lightgbm_device || '--';

  const trainedCount = (key) => {
    let n = 0;
    for (const reg of Object.keys(byRegime)) {
      const c = (byRegime[reg] || {})[key];
      if (typeof c === 'number') n += c;
    }
    return n;
  };

  const chips = Object.keys(MODEL_LABELS).map((name) => {
    const isAvail = installed[INV_AVAIL_KEY[name]] !== false;
    return `<div class="inv-card ${isAvail ? '' : 'inv-off'}">
      <span class="inv-name">${MODEL_LABELS[name]}</span>
      <strong class="inv-state">${isAvail ? 'available' : 'not installed'}</strong>
      <small>trained heads: ${trainedCount(name)}</small>
    </div>`;
  }).join('');

  els.modelInventoryGrid.innerHTML = chips +
    `<div class="inv-card"><span class="inv-name">LightGBM device</span><strong class="inv-state">${device}</strong><small>execution mode</small></div>` +
    `<div class="inv-card"><span class="inv-name">Deep model</span><strong class="inv-state">${inv.deep_model_arch || 'n/a'}</strong><small>sequence architecture</small></div>`;
}

// ── Action & Trade log (REST: /api/action-log) ──
let _actionLogTimer = null;
async function fetchActionLog() {
  if (!els.actionLog) return;
  let ok = false;
  try {
    // Abort a hung request so a brief DB lock-race can't stall the poll forever.
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 8000);
    const res = await fetch('/api/action-log?limit=120', { signal: ctrl.signal });
    clearTimeout(t);
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const json = await res.json();
    const items = json.items || [];
    renderActionLog(items.slice(0, 60));
    // Directional-calls log: only rows where the model actually leaned UP or DOWN
    // (either the final signal or the raw lean is directional), so the user can see
    // every real call separately from the WAIT/AVOID noise.
    renderDirectionalLog(items.filter(
      (it) => ['UP', 'DOWN'].includes(it.signal) || ['UP', 'DOWN'].includes(it.raw_direction)
    ));
    ok = true;
  } catch (e) {
    // Transient poll failure (DB lock-race / brief unavailability during a write).
    // Do NOT wipe an already-populated panel — only show a soft message on first load,
    // and retry sooner. The data is still there; the next poll almost always succeeds.
    const blank = (el) => el && !el.querySelector('.log-row');
    if (blank(els.actionLog)) els.actionLog.innerHTML = `<div class="log-empty">Loading recent actions…</div>`;
    if (blank(els.directionalLog)) els.directionalLog.innerHTML = `<div class="log-empty">Loading directional calls…</div>`;
  }
  // keep it fresh while the user is on this tab; back off to a quick retry on failure
  if (_actionLogTimer) clearTimeout(_actionLogTimer);
  if (currentAppTab === 'models') {
    _actionLogTimer = setTimeout(fetchActionLog, ok ? 15000 : 4000);
  }
}

function renderActionLog(items) {
  if (!els.actionLog) return;
  if (!items.length) {
    els.actionLog.innerHTML = `<div class="log-empty">No recorded actions yet.</div>`;
    return;
  }
  const head = `<div class="log-row log-head">
    <span>Time</span><span>TF</span><span>Action</span><span>Expected</span>
    <span>Reference</span><span>Result</span>
  </div>`;
  const rows = items.map((it) => {
    const t = etTime(it.timestamp);
    const sig = it.signal || 'NEUTRAL';
    const sigCol = sig === 'UP' ? '#00e676' : sig === 'DOWN' ? '#ff1744' : '#8892a6';
    const action = sig === 'UP' ? 'BUY' : sig === 'DOWN' ? 'SELL' : 'AVOID';
    const exp = (it.expected_move != null && it.expected_move !== 0)
      ? `${it.expected_move > 0 ? '+' : ''}$${Math.round(it.expected_move)}`
      : '–';
    const ref = it.reference_price != null ? `$${Number(it.reference_price).toLocaleString()}` : '–';
    let result = `<span class="log-pending">⏳ pending</span>`;
    let rowCls = '';
    if (it.resolved) {
      const moved = it.actual_move != null ? `${it.actual_move > 0 ? '+' : ''}$${Math.round(it.actual_move)}` : '';
      if (it.hit === true) { result = `<span class="log-hit">✓ ${moved}</span>`; rowCls = 'log-hit-row'; }
      else if (it.hit === false) { result = `<span class="log-miss">✗ ${moved}</span>`; rowCls = 'log-miss-row'; }
      else { result = `<span>${moved || 'resolved'}</span>`; }
    }
    return `<div class="log-row ${rowCls}">
      <span>${t}</span>
      <span>${it.horizon}m</span>
      <span style="color:${sigCol};font-weight:600">${action}</span>
      <span>${exp}</span>
      <span>${ref}</span>
      <span>${result}</span>
    </div>`;
  }).join('');
  els.actionLog.innerHTML = head + rows;
}

function renderDirectionalLog(items) {
  if (!els.directionalLog) return;
  if (!items.length) {
    els.directionalLog.innerHTML = `<div class="log-empty">No UP/DOWN calls yet — the model is holding NEUTRAL. Directional leans will appear here as they happen.</div>`;
    return;
  }
  const head = `<div class="log-row log-head" style="grid-template-columns:1.1fr .6fr .8fr .9fr 1fr 1fr;">
    <span>Time</span><span>TF</span><span>Lean</span><span>Action</span><span>Expected</span><span>Result</span>
  </div>`;
  const rows = items.map((it) => {
    const t = etTime(it.timestamp);
    const lean = it.raw_direction || 'NEUTRAL';
    const leanCol = lean === 'UP' ? '#00e676' : lean === 'DOWN' ? '#ff1744' : '#8892a6';
    // Final committed action: only UP/DOWN final signals are real BUY/SELL trades;
    // a directional lean that the gate held shows as WAIT (the intentional lean-vs-action split).
    const sig = it.signal || 'NEUTRAL';
    const action = sig === 'UP' ? 'BUY' : sig === 'DOWN' ? 'SELL' : 'WAIT';
    const actCol = sig === 'UP' ? '#00e676' : sig === 'DOWN' ? '#ff1744' : '#8892a6';
    const exp = (it.expected_move != null && it.expected_move !== 0)
      ? `${it.expected_move > 0 ? '+' : ''}$${Math.round(it.expected_move)}` : '–';
    let result = `<span class="log-pending">⏳ pending</span>`;
    let rowCls = '';
    if (it.resolved) {
      const moved = it.actual_move != null ? `${it.actual_move > 0 ? '+' : ''}$${Math.round(it.actual_move)}` : '';
      if (it.hit === true) { result = `<span class="log-hit">✓ ${moved}</span>`; rowCls = 'log-hit-row'; }
      else if (it.hit === false) { result = `<span class="log-miss">✗ ${moved}</span>`; rowCls = 'log-miss-row'; }
      else { result = `<span>${moved || 'resolved'}</span>`; }
    }
    return `<div class="log-row ${rowCls}" style="grid-template-columns:1.1fr .6fr .8fr .9fr 1fr 1fr;">
      <span>${t}</span>
      <span>${it.horizon}m</span>
      <span style="color:${leanCol};font-weight:600">${lean}</span>
      <span style="color:${actCol};font-weight:600">${action}</span>
      <span>${exp}</span>
      <span>${result}</span>
    </div>`;
  }).join('');
  els.directionalLog.innerHTML = head + rows;
}

// ══════════════════════════════════════════════
//  Utilities
// ══════════════════════════════════════════════
function formatNumberShort(num) {
  if (num == null || isNaN(num)) return '--';
  const absNum = Math.abs(num);
  let formatted;
  if (absNum >= 1e9) formatted = (absNum / 1e9).toFixed(2) + 'B';
  else if (absNum >= 1e6) formatted = (absNum / 1e6).toFixed(2) + 'M';
  else if (absNum >= 1e3) formatted = (absNum / 1e3).toFixed(1) + 'k';
  else formatted = absNum.toFixed(2);
  return num < 0 ? '-' + formatted : formatted;
}

function formatDuration(seconds) {
  if (seconds == null || isNaN(seconds)) return '--';
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m <= 0) return `${r}s`;
  return `${m}m ${r}s`;
}

// ══════════════════════════════════════════════
//  Start
// ══════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', init);
