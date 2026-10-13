import { createHand } from './hand.js';
import { Ads1298Serial } from './acquisition.js';

const $ = selector => document.querySelector(selector);
const main = $('#main');
const t = (zh, en) => state.preferences.language === 'zh' ? zh : en;
const name = action => {
  const label = capturePlan().action_names?.[String(action)];
  return label ? t(label.zh, label.en) : `${t('动作', 'Action')} ${action}`;
};
function capturePlan() {
  return state.session?.capture_plan || state.templates?.capture_plan || {
    profile: 'full', actions: Array.from({ length: 40 }, (_, i) => i + 1),
    groups: 1, repetitions_per_group: 6, repetitions: 6, total: 240,
  };
}
function trialTitle() {
  const plan = capturePlan(), within = (state.repetition - 1) % plan.repetitions_per_group + 1;
  const group = Math.floor((state.repetition - 1) / plan.repetitions_per_group) + 1;
  return `${name(state.action)} · ${plan.groups > 1 ? t(`第 ${group}/${plan.groups} 组 · `, `Group ${group}/${plan.groups} · `) : ''}${t(`第 ${within}/${plan.repetitions_per_group} 次`, `Rep ${within}/${plan.repetitions_per_group}`)}`;
}
function planSummary() {
  const plan = capturePlan(), seconds = plan.total * (state.templates?.duration_s || 9);
  const minutes = Math.floor(seconds / 60), remainder = seconds % 60;
  return `${plan.actions.length} × ${plan.groups} × ${plan.repetitions_per_group} · ${t(`约 ${minutes} 分${remainder ? ` ${remainder} 秒` : ''}`, `about ${minutes} min${remainder ? ` ${remainder} sec` : ''}`)}`;
}
const save = () => { try { localStorage.setItem('antikythera.preferences.v1', JSON.stringify(state.preferences)); } catch (_) {} };
const load = () => { try { const saved=JSON.parse(localStorage.getItem('antikythera.preferences.v1')); return { ...state.preferences, hand:['left','right'].includes(saved.hand)?saved.hand:'right', language:['zh','en'].includes(saved.language)?saved.language:'zh', gender:['','male','female'].includes(saved.gender)?saved.gender:'' }; } catch (_) { return state.preferences; } };
const state = { screen: 'intro', preferences: { hand: 'right', language: 'zh', gender: '' }, templates: null, session: null, serial: null, segment: 0, action: 1, repetition: 1, phase: 'idle', timer: null, due: [], received: 0, saved: 0, batch: 0, queue: Promise.resolve(), pending: [], pendingStart: null, pendingAt: 0, queued: 0, generation: 0, lastReceivedAt: 0, startedAt: 0, completed: [], trace: Array.from({ length: 8 }, () => []), status: null, error: '' };
state.preferences = load();
let hand = null, handCanvas = null, handFailure = '', pauseTask = null, toastTimer;
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
const escapeHTML = value => String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

async function api(path, body, method = 'POST') {
  const response = await fetch(path, { method, headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(4000) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) { const error = new Error(data.message || data.error || `${response.status}`); error.code = data.error; throw error; }
  return data;
}
function enqueue(path, body, control = false) {
  const generation = state.generation;
  state.queued++;
  const job = state.queue.then(async () => {
    if (!control && generation !== state.generation) return null;
    return api(path, body);
  });
  state.queue = job.catch(error => {
    if (!control && generation === state.generation) {
      state.generation++;
      void pauseCapture('save_error', error.message);
    }
  }).finally(() => { state.queued--; });
  // Callers may observe the result; fire-and-forget waveform writes are handled.
  job.catch(() => {});
  return job;
}
function toast(message) { clearTimeout(toastTimer); const node = $('#toast'); node.textContent = message; node.classList.add('show'); toastTimer = setTimeout(() => node.classList.remove('show'), 3200); }
function statusText() { return escapeHTML(state.error || (state.phase === 'running' ? t('正在采集', 'Recording') : state.phase === 'paused' ? t('已暂停，本次需要重做', 'Paused; repeat this trial') : t('等待连接设备', 'Waiting for device'))); }
function stage(title) { return `<div class="hand-stage"><div class="stage-top"><span class="stage-label">${title}</span><span class="stage-pill">${state.preferences.hand === 'left' ? t('左手', 'LEFT HAND') : t('右手', 'RIGHT HAND')}</span></div><div id="hand-mount"></div><div class="stage-bottom"><p>${state.screen === 'guide' ? t('公共模板 · 动作引导', 'Public template · movement guide') : t('尚未启用模型预测', 'Model prediction is not enabled')}</p><span class="serial">8 CH · RAW µV</span></div></div>`; }
function intro() { return `<section class="page hero"><div class="hero-copy"><p class="eyebrow">REAL-TIME EMG</p><h1>${t('让意图，<em>触手可及。</em>', 'Make intention<br><em>visible.</em>')}</h1><p class="lead">${capturePlan().profile === 'two-actions' ? t('采集握拳与张手的八通道肌电，分 3 组、每组每个动作 6 次，为 NCP512 连续关节特调保存原始数据。', 'Record eight-channel EMG for hand closing and opening in 3 groups of 6 repetitions per action, for NCP512 continuous joint adaptation.') : t('采集八通道肌电，完成 40 个动作、每个 6 次的真实引导。训练和实时控制仅在证据齐备后启用。', 'Capture eight-channel EMG through all 40 actions, six times each. Training and live control only enable when their evidence is complete.') }</p><div class="button-row"><button class="button" data-action="begin">${t('开始设置', 'Set up')} <span class="arrow">→</span></button></div></div><div>${stage(t('连续虚拟手', 'CONTINUOUS VIRTUAL HAND'))}</div></section>`; }
function preferences() { const p = state.preferences; return `<section class="page setup-grid"><div><p class="eyebrow">01 / ${t('设置', 'SETUP')}</p><h1>${t('先把体验调成你的。', 'Set the experience<br>to fit you.')}</h1><form class="preferences"><fieldset class="preference"><legend>${t('使用哪只手？', 'Which hand?')}</legend><div class="choice-row">${['right','left'].map(v => `<label class="choice"><input type="radio" name="hand" value="${v}" ${p.hand === v ? 'checked' : ''}><span class="choice-text">${v === 'right' ? t('右手','Right hand') : t('左手','Left hand')}</span><span class="choice-check"></span></label>`).join('')}</div></fieldset><fieldset class="preference"><legend>${t('界面语言', 'Language')}</legend><div class="choice-row">${[['zh','中文'],['en','English']].map(([v,label]) => `<label class="choice"><input type="radio" name="language" value="${v}" ${p.language === v ? 'checked' : ''}><span class="choice-text">${label}</span><span class="choice-check"></span></label>`).join('')}</div></fieldset><fieldset class="preference"><legend>${t('偏好（仅保存在此浏览器）', 'Preference (stored only in this browser)')}</legend><div class="choice-row">${[['female',t('女性','Female')],['male',t('男性','Male')]].map(([v,label]) => `<label class="choice"><input type="radio" name="gender" value="${v}" ${p.gender === v ? 'checked' : ''}><span class="choice-text">${label}</span><span class="choice-check"></span></label>`).join('')}</div></fieldset></form><div class="button-row"><button class="button" data-action="guide">${t('进入采集引导', 'Open capture guide')} <span class="arrow">→</span></button></div></div><div>${stage(t('连续虚拟手', 'CONTINUOUS VIRTUAL HAND'))}</div></section>`; }
function guide() { const plan = capturePlan(), done = state.completed.length; const duration = state.templates?.duration_s || 9; const startAction=state.phase==='paused'?'resume':'connect'; return `<section class="page"><div class="screen-heading"><div><p class="eyebrow">02 / ${t('采集引导', 'CAPTURE GUIDE')}</p><h1>${t('跟随动作，记录真实信号。', 'Follow each action and record real signal.')}</h1><p class="lead">${t('每个动作 9 秒：准备 2 秒、跟随 5 秒、休息 2 秒。暂停或断开会让当前重复从头开始。', 'Each repetition lasts 9 seconds: 2 prepare, 5 follow, 2 rest. Pause or disconnect restarts the current repetition.')}</p></div><span class="status"><i class="status-dot"></i>${statusText()}</span></div><div class="guide-layout capture-layout"><aside class="guide-details"><span id="phase-badge" class="phase-badge">${state.phase.toUpperCase()}</span><div id="clock" class="clock">${duration}<span>S</span></div><h3 id="action-title">${escapeHTML(trialTitle())}</h3><p><span id="completed-count">${done}</span> / ${plan.total} ${t('个已完成', 'completed')}</p><button class="button" data-action="${startAction}" ${state.phase === 'running' ? 'disabled' : ''}>${state.phase==='paused'?t('重新开始本次', 'Restart repetition'):t('连接并开始', 'Connect and start')}</button><button class="text-button" data-action="pause" ${state.phase !== 'running' ? 'disabled' : ''}>${t('暂停当前重复', 'Pause this repetition')}</button></aside><div class="guide-stage">${stage(t('动作目标', 'ACTION TARGET'))}<div class="phase-strip"><span></span><span></span><span></span></div><div class="phase-labels"><span>${t('准备', 'Prepare')}</span><span>${t('跟随', 'Follow')}</span><span>${t('休息', 'Rest')}</span></div></div><aside class="guide-details capture-status"><h3>${t('采集状态', 'Capture status')}</h3><p>${state.session ? `${t('会话', 'Session')} ${escapeHTML(state.session.session_id)}` : t('尚未创建会话。选择串口后才开始记录。', 'No session exists until a serial port is selected.')}</p><div class="capture-counts"><p>${t("已接收", "Received")} <strong id="received-count">${state.received}</strong></p><p>${t("已保存", "Saved")} <strong id="saved-count">${state.saved}</strong></p></div><canvas id="signal-chart" class="capture-trace" role="img" aria-label="Eight-channel microvolt waveform, centered and independently auto-scaled"></canvas><p class="tiny-note capture-note">${t('各通道显示居中、独立自动缩放，旁边标出 ±µV 范围；保存值不变。', 'Each trace is centered and independently auto-scaled; ±µV ranges are shown. Saved values are unchanged.')}</p></aside></div><div class="guide-bottom"><label class="progress-label">${t("全部动作完成进度", "Complete action coverage")}<progress id="guide-progress" max="${plan.total}" value="${done}"></progress></label><span class="subtle">${escapeHTML(planSummary())}</span></div><p id="capture-message" class="notice" role="status" aria-live="polite">${escapeHTML(state.error)}</p></section>`; }
function adapt() { return `<section class="page ready-layout"><div class="ready-copy"><p class="eyebrow">03 / ${t('适配', 'ADAPTATION')}</p><h1>${t('准备好后，开始适配。', 'Adapt when everything is ready.')}</h1><p class="lead">${t('采集数据会保存在本机。肌电单位换算还需确认，适配暂未启用。', 'Recordings stay on this computer. EMG unit conversion still needs confirmation before adaptation can start.')}</p><div class="notice" id="training-gate">${escapeHTML(state.error) || t('缺少单位证据：训练与实时预测被明确阻塞。', 'Unit evidence is missing: training and live prediction are explicitly blocked.')}</div><div class="button-row"><button class="button secondary" data-action="train">${t('开始适配', 'Start adaptation')}</button><button class="button" data-action="live">${t('查看实时工作区', 'Open live workspace')}</button></div></div><div class="result-panel"><h3>${t('输出目标', 'Output targets')}</h3><p class="subtle">J1–J10 · ${t('待真实评估', 'awaiting real evaluation')}</p><table class="result-table"><tbody>${Array.from({length:10},(_,i)=>`<tr><td>J${i+1}</td><td>—</td><td>${t('未启用', 'Not enabled')}</td></tr>`).join('')}</tbody></table></div></section>`; }
function live() { return `<section class="page"><div class="screen-heading live-heading"><div><p class="eyebrow">04 / ${t('实时工作区', 'LIVE WORKSPACE')}</p><h1>${t('真实信号，等待可验证的控制。', 'Real signal, awaiting verified control.')}</h1></div><span id="live-status" class="status"><i class="status-dot"></i>${statusText()}</span></div><div class="live-grid"><div class="signal-panel"><div class="panel-heading"><h3>${t('八通道原始波形', 'Eight-channel raw waveform')}</h3><span>RAW µV</span></div><canvas id="signal-chart" class="signal-chart" role="img" aria-label="Eight-channel EMG waveform, centered and independently auto-scaled"></canvas><div class="chart-footer"><span>${t('各通道居中、独立自动缩放；保存值不变', 'Each channel is centered and independently auto-scaled; saved values are unchanged')}</span><button class="text-button" data-action="live-check">${t('启用预测', 'Enable prediction')}</button></div></div><div class="live-hand">${stage(t('虚拟手', 'VIRTUAL HAND'))}</div></div><div class="notice">${t('当前没有可用的真实预测，虚拟手不会用模板或模拟轨迹冒充控制输出。', 'No real prediction is available; the virtual hand will not use templates or simulated traces as control output.')}</div></section>`; }
function render() {
  // Keep the authored hand, GL resources and dragged camera across screen updates.
  handCanvas?.remove();
  main.dataset.screen = state.screen;
  main.innerHTML = ({ intro, preferences, guide, adapt, live })[state.screen]();
  const labels = [t('偏好', 'Preferences'), t('动作', 'Capture'), t('适配', 'Adaptation'), t('实时', 'Live')];
  $('#steps').innerHTML = ['preferences', 'guide', 'adapt', 'live'].map((screen, i) =>
    '<button class="step ' + (screen === state.screen ? 'current' : '') + '" data-nav="' + screen + '"' +
    (screen === state.screen ? ' aria-current="step"' : '') + '><span>' + (i + 1) + '</span>' + labels[i] + '</button>').join('');
  $('#demo-label').textContent = t('设备采集', 'DEVICE CAPTURE');
  $('#language-toggle').textContent = t('EN', '中文');
  $('#language-toggle').setAttribute('aria-label', t('Switch to English', '切换为中文'));
  document.documentElement.lang = t('zh-CN', 'en');
  $('#footer-note').textContent = t('你的节奏，由你掌握。', 'Move at your own pace.');
  $('#reset-title').textContent = t('结束本次并回到起点？', 'End this session and start again?');
  $('#reset-copy').textContent = t('已保存的信号保留，偏好设置保留。当前未完成的一次需要重新录制。', 'Saved signals and preferences remain. The unfinished repetition needs recording again.');
  $('#reset-cancel').textContent = t('继续当前会话', 'Keep this session');
  $('#reset-confirm').textContent = t('结束并重新开始', 'End and restart');
  const mount = $('#hand-mount');
  if (mount) {
    if (!handCanvas) {
      handCanvas = document.createElement('canvas');
      handCanvas.id = 'hand-canvas';
      handCanvas.setAttribute('role', 'img');
      handCanvas.addEventListener('antikythera-webgl-unavailable', () => {
        handFailure = 'webgl'; void pauseCapture('hand_unavailable'); render();
      });
    }
    mount.append(handCanvas);
    handCanvas.setAttribute('aria-label', t('可旋转的三维动作引导手，非实时预测', 'Rotatable 3D guide hand; not live prediction'));
    if (!hand && !handFailure) {
      try {
        hand = createHand(handCanvas);
        const renderer = hand;
        renderer.ready.then(() => renderer.resize()).catch(() => {
          handFailure = 'model'; renderer.dispose(); hand = null;
          void pauseCapture('hand_unavailable'); render();
        });
      } catch { handFailure = 'webgl'; }
    }
    if (handFailure) {
      const message = document.createElement('p');
      message.className = 'hand-error';
      message.textContent = handFailure === 'model' ? t('模型未能加载，请刷新后重试。', 'Hand model could not load. Reload to retry.') :
        t('无法显示三维手，请使用支持 WebGL 的浏览器。', 'The 3D hand requires a browser with WebGL.');
      mount.append(message);
    }
    hand?.setHandedness(state.preferences.hand);
    hand?.setMotionEnabled(false);
    hand?.resize();
    if (state.screen !== 'guide' || state.phase !== 'running') hand?.setJ10(Array(10).fill(0));
  }
  const captureButton = $('[data-action="connect"], [data-action="resume"]');
  if (captureButton) captureButton.disabled = !['idle', 'paused'].includes(state.phase) || !state.templates || !!handFailure;
  const pauseButton = $('[data-action="pause"]');
  if (pauseButton) pauseButton.disabled = !['running', 'saving'].includes(state.phase);
  if (state.session) document.querySelectorAll('input[name="hand"]').forEach(input => { input.disabled = true; });
  updateCaptureView(); drawTrace();
}
function drawTrace() {
  const canvas = $('#signal-chart');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  const rect = canvas.getBoundingClientRect(), dpr = Math.min(devicePixelRatio || 1, 2);
  const w = Math.max(1, rect.width), h = Math.max(1, rect.height);
  canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  ctx.font = '9px sans-serif'; ctx.textBaseline = 'middle';
  for (let c = 0; c < 8; c++) {
    const row = state.trace[c], baseline = (c + .5) * h / 8;
    ctx.strokeStyle = '#d8e0d7'; ctx.beginPath(); ctx.moveTo(0, baseline); ctx.lineTo(w, baseline); ctx.stroke();
    ctx.fillStyle = '#66746b'; ctx.textAlign = 'left'; ctx.fillText(`CH${c + 1}`, 2, baseline);
    if (!row.length) continue;
    const min = Math.min(...row), max = Math.max(...row);
    const center = (min + max) / 2, amplitude = Math.max(1, (max - min) / 2);
    ctx.textAlign = 'right';
    ctx.fillText(`±${amplitude.toLocaleString(state.preferences.language, { maximumFractionDigits: 1 })} µV`, w - 2, baseline);
    ctx.strokeStyle = '#276b5e'; ctx.beginPath();
    row.forEach((value, i) => {
      const x = 32 + i / Math.max(1, row.length - 1) * Math.max(1, w - 107);
      const y = baseline - (value - center) / amplitude * h / 18;
      if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
    });
    ctx.stroke();
  }
}
function updateCaptureView() {
  if ($('#received-count')) $('#received-count').textContent = state.received.toLocaleString();
  if ($('#saved-count')) $('#saved-count').textContent = state.saved.toLocaleString();
  if ($('#completed-count')) $('#completed-count').textContent = state.completed.length;
  if ($('#guide-progress')) $('#guide-progress').value = state.completed.length;
  if ($('#capture-message')) $('#capture-message').textContent = state.error;
  if ($('#phase-badge')) {
    const activePhase = state.phase === 'running' ? ((performance.now() - state.startedAt) < 2000 ? t('准备', 'PREPARE') :
      (performance.now() - state.startedAt) < 7000 ? t('跟随', 'FOLLOW') : t('休息', 'REST')) :
      state.phase === 'paused' ? t('已暂停', 'PAUSED') : state.phase === 'saving' ? t('正在保存', 'SAVING') :
      state.phase === 'connecting' ? t('正在连接', 'CONNECTING') : t('待开始', 'READY');
    $('#phase-badge').textContent = activePhase;
  }
}
async function loadTemplates() {
  if (!state.templates) state.templates = await api('/api/templates', null, 'GET');
  if (!state.session) chooseNext();
}
function flushSamples() {
  while (state.pending.length) {
    const rows = state.pending.splice(0, 256), start = state.pendingStart;
    state.pendingStart += rows.length;
    const body = { session_id: state.session.session_id, segment_id: state.segment,
      batch_id: state.batch++, received_sample_index: start, received_at_ms: state.pendingAt, samples: rows };
    enqueue('/api/samples', body).then(result => {
      if (result) state.saved = result.stored_row_end;
      updateCaptureView();
    }).catch(() => {});
  }
  state.pendingStart = null;
}
function cueBody(event) {
  return { session_id: state.session.session_id, event, action: state.action, repetition: state.repetition,
    segment_id: state.segment, received_sample_index: state.received, browser_time_ms: performance.now(),
    browser_time_origin_ms: performance.timeOrigin, ack_at_ms: state.session.parameters?.ack_at_ms };
}
function sendCue(event) {
  const body = cueBody(event); // Capture the displayed time/index now, not when HTTP is sent.
  flushSamples();
  return enqueue('/api/cue', body);
}
function onSamples(samples) {
  const receivedAt = performance.now();
  state.lastReceivedAt = receivedAt;
  const start = state.received;
  state.received += samples.length; // All host-received rows, including explicit gaps.
  samples.forEach(row => row.forEach((value, c) => state.trace[c].push(value)));
  state.trace.forEach(row => { if (row.length > 600) row.splice(0, row.length - 600); });
  if (state.phase === 'running') {
    if (state.pendingStart === null) { state.pendingStart = start; state.pendingAt = receivedAt; }
    state.pending.push(...samples);
    if (state.queued >= 12 || state.pending.length > 2000) {
      void pauseCapture('backpressure', t('保存速度跟不上采集，当前一次需重做。', 'Saving fell behind capture; repeat this trial.'));
    } else if (state.pending.length >= 128) flushSamples();
  }
}
function chooseNext() {
  const plan = capturePlan();
  for (let group = 0; group < plan.groups; group++) for (const action of plan.actions) {
    for (let within = 1; within <= plan.repetitions_per_group; within++) {
      const repetition = group * plan.repetitions_per_group + within;
      if (!state.completed.some(row => row.action === action && row.repetition === repetition)) {
        state.action = action; state.repetition = repetition; return true;
      }
    }
  }
  return false;
}
function beginRepetition(gapReason = 'repetition_transition') {
  if (!state.serial?.ready || document.hidden) throw new Error(t('请回到页面并连接设备。', 'Return to the page and connect the device.'));
  state.segment++;
  state.batch = 0; state.due = []; state.pending = []; state.pendingStart = null;
  state.phase = 'running'; state.startedAt = performance.now();
  const generation = state.generation;
  render();
  // Start is enqueued synchronously before any subsequent samples.
  enqueue('/api/cue', { ...cueBody('start'), gap_reason: gapReason }).catch(() => {});
  const tick = () => {
    if (state.phase !== 'running' || generation !== state.generation) return;
    const elapsed = (performance.now() - state.startedAt) / 1000;
    if (performance.now() - state.lastReceivedAt > 500) {
      void pauseCapture('stale', t('信号接收中断，当前一次需重做。', 'Signal reception stopped; repeat this trial.')); return;
    }
    if (state.pending.length) flushSamples();
    if ($('#clock')) $('#clock').innerHTML = Math.max(0, 9 - elapsed).toFixed(1) + '<span>S</span>';
    hand?.setJ10(state.templates.poses[state.action - 1][Math.min(180, Math.floor(elapsed * 20))]);
    updateCaptureView();
    for (const [event, time] of [['cue_on', 2], ['cue_off', 7]]) {
      if (elapsed >= time && !state.due.includes(event)) {
        state.due.push(event); sendCue(event).catch(() => {});
      }
    }
    if (elapsed >= 9) {
      state.phase = 'saving';
      const completed = { action: state.action, repetition: state.repetition };
      sendCue('complete').then(async response => {
        if (!response) return;
        state.completed.push(completed);
        if (state.phase !== 'saving' || generation !== state.generation) return;
        if (chooseNext()) beginRepetition();
        else {
          await state.serial.close(); state.serial = null;
          const done = await enqueue('/api/finish', { session_id: state.session.session_id, received_sample_index: state.received });
          if (!done) return;
          state.phase = 'finished'; state.session.status = done.status;
          state.screen = 'adapt'; render();
        }
      }).catch(() => {});
      return;
    }
    state.timer = setTimeout(tick, 25);
  };
  tick();
}
async function startCapture() {
  if (!['idle', 'paused'].includes(state.phase)) return;
  state.phase = 'connecting'; state.error = ''; render();
  const serial = new Ads1298Serial({ onSamples, onError: event => {
    if (['running', 'saving'].includes(state.phase)) void pauseCapture(event.reason, event.error?.message);
  }});
  state.serial = serial;
  try {
    const params = await serial.connect(); // Directly reached from the user click.
    if (state.session && state.session.parameters?.pga_index !== params.pgaIndex) {
      throw new Error(t('设备增益已变化，请结束本次会话后重新开始。', 'Device gain changed. End this session before starting a new one.'));
    }
    if (!state.session) {
      const created = await api('/api/session', { rate_index: 2, pga_index: params.pgaIndex, ack_at_ms: params.ackAtMs,
        browser_time_origin_ms: performance.timeOrigin, preferences: { hand: state.preferences.hand, language: state.preferences.language } });
      state.session = { ...created, parameters: { pga_index: params.pgaIndex, ack_at_ms: params.ackAtMs } };
    } else state.session.parameters.ack_at_ms = params.ackAtMs;
    if (document.hidden) throw new Error(t('请保持采集页面可见。', 'Keep the capture page visible.'));
    state.lastReceivedAt = performance.now();
    beginRepetition(state.segment ? 'reconnected' : 'startup_preview');
  } catch (error) {
    await serial.close(); state.serial = null; state.phase = state.session ? 'paused' : 'idle';
    state.error = error.message; render();
  }
}
function pauseCapture(reason = 'pause', message = '') {
  if (pauseTask) return pauseTask;
  if (!['running', 'saving', 'connecting'].includes(state.phase)) return Promise.resolve();
  state.phase = 'pausing'; clearTimeout(state.timer);
  // The prefix may still be stored for audit, but cannot count as a full trial.
  if (state.session) flushSamples();
  const body = state.session ? { session_id: state.session.session_id, reason,
    browser_time_ms: performance.now(), received_sample_index: state.received } : null;
  state.error = message || t('已暂停，当前未完成的一次将从头重做。', 'Paused. The unfinished repetition will restart from the beginning.');
  render();
  pauseTask = (async () => {
    let failed = false;
    await state.serial?.close(); state.serial = null;
    if (body) body.received_sample_index = state.received;
    try {
      if (body) await enqueue('/api/pause', body, true);
    } catch (error) { failed = true; state.error = t('尚未确认保存状态：', 'Save state is not confirmed: ') + error.message; }
    state.phase = 'paused';
    if (!failed && state.session) {
      try {
        const status = await api('/api/status', null, 'GET');
        applySummary(status.session);
      } catch { /* Existing saved count remains an acknowledgment, never a guess. */ }
    }
    render();
  })().finally(() => { pauseTask = null; });
  return pauseTask;
}
function applySummary(summary) {
  if (!summary) return;
  state.session = summary;
  state.completed = summary.completed || state.completed;
  state.saved = summary.stored_rows ?? state.saved;
  state.received = Math.max(state.received, summary.received_sample_end || 0);
  state.segment = (summary.next_segment_id ?? (state.segment + 1)) - 1;
  if (summary.preferences?.hand) state.preferences.hand = summary.preferences.hand;
  chooseNext();
}
async function navigate(screen) {
  if (['running', 'saving', 'connecting'].includes(state.phase)) await pauseCapture('navigation');
  if (screen === 'guide') await loadTemplates();
  state.screen = screen; render(); main.focus({ preventScroll: true });
}
async function reset() {
  if (['running', 'saving', 'connecting'].includes(state.phase)) await pauseCapture('restart');
  if (state.session) await enqueue('/api/stop', { session_id: state.session.session_id, received_sample_index: state.received }, true);
  state.generation++; state.session = null; state.completed = []; state.received = 0; state.saved = 0;
  state.segment = 0; state.pending = []; state.pendingStart = null; state.phase = 'idle'; state.error = '';
  chooseNext(); state.trace.forEach(row => { row.length = 0; });
  state.screen = 'intro'; render();
}
async function action(value) {
  if (value === 'begin') return navigate('preferences');
  if (value === 'guide') return navigate('guide');
  if (value === 'connect' || value === 'resume') return startCapture();
  if (value === 'pause') return pauseCapture();
  if (value === 'live') return navigate('live');
  if (value === 'train' || value === 'live-check') {
    try { await api(value === 'train' ? '/api/train' : '/api/live', { session_id: state.session?.session_id }); }
    catch (error) {
      state.error = error.code === 'blocked_unit_evidence' ?
        t('肌电单位换算尚未确认，暂时不能训练或启用预测。已保存的采集数据会保留。', 'EMG unit conversion is not confirmed. Training and prediction are unavailable; saved recordings remain.') : error.message;
      render();
    }
  }
}
document.addEventListener('click', event => {
  const node = event.target.closest('[data-action], [data-nav]');
  if (!node || node.disabled) return;
  const task = node.dataset.nav ? navigate(node.dataset.nav) : action(node.dataset.action);
  Promise.resolve(task).catch(error => { state.error = error.message; render(); });
});
document.addEventListener('change', event => {
  const allowed = { hand: ['left', 'right'], language: ['zh', 'en'], gender: ['', 'male', 'female'] };
  if (allowed[event.target.name]?.includes(event.target.value)) {
    state.preferences[event.target.name] = event.target.value; save(); render();
  }
});
$('#language-toggle').addEventListener('click', () => { state.preferences.language = t('en', 'zh'); save(); render(); });
$('#brand').addEventListener('click', event => { event.preventDefault(); $('#reset-dialog').returnValue = ''; $('#reset-dialog').showModal(); });
$('#reset-dialog').addEventListener('close', () => {
  if ($('#reset-dialog').returnValue === 'reset') reset().catch(error => { state.error = error.message; render(); });
});
document.addEventListener('keydown', event => {
  if (event.code === 'Space' && !event.target.closest('button, input, select, a, dialog') && state.screen === 'guide') {
    event.preventDefault();
    if (state.phase === 'running') void pauseCapture();
    else if (state.phase === 'paused') void startCapture();
  }
});
document.addEventListener('visibilitychange', () => { if (document.hidden) void pauseCapture('visibility_loss'); });
window.addEventListener('pagehide', () => {
  if (state.session && ['running', 'saving'].includes(state.phase)) {
    fetch('/api/pause', { method: 'POST', headers: { 'Content-Type': 'application/json' }, keepalive: true,
      body: JSON.stringify({ session_id: state.session.session_id, reason: 'pagehide',
        browser_time_ms: performance.now(), received_sample_index: state.received }) }).catch(() => {});
  }
  void state.serial?.close();
});
window.addEventListener('resize', () => { hand?.resize(); drawTrace(); });
setInterval(() => { drawTrace(); updateCaptureView(); }, 50);
render();
Promise.all([api('/api/status', null, 'GET'), loadTemplates()]).then(([status]) => {
  state.status = status;
  if (status.session && ['recording', 'paused'].includes(status.session.status)) {
    applySummary(status.session); state.phase = 'paused';
    state.error = t('检测到未结束的录制，返回采集页可重连并重做当前一次。', 'An unfinished session was found. Reconnect in Capture to repeat the current trial.');
    return enqueue('/api/pause', { session_id: state.session.session_id, reason: 'page_reloaded',
      browser_time_ms: performance.now(), received_sample_index: state.received }, true);
  }
}).catch(error => { state.error = error.message; }).finally(render);

