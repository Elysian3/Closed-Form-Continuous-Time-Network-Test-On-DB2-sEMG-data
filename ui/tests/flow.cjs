// Fake hardware is injected by this test; every /api request uses the actual server.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/14148/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const url = process.env.UI_URL || 'http://127.0.0.1:8765';
const artifacts = path.join(__dirname, 'artifacts');
fs.mkdirSync(artifacts, { recursive: true });
let checks = 0;
const check = (condition, message) => { assert(condition, message); checks++; console.log('PASS', message); };
const tap = (page, action) => page.locator('[data-action="' + action + '"]').click();
async function until(fn, message, timeout = 12000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { if (await fn()) return; await new Promise(r => setTimeout(r, 100)); }
  throw new Error('Timed out: ' + message);
}
function fakeHardware() {
  window.fakePorts = []; window.chooserActivations = [];
  const frame = rows => {
    const bytes = new Uint8Array(rows.length * 32 + 5), n = bytes.length - 3, view = new DataView(bytes.buffer);
    bytes.set([0xA5, n, 0x11, n ^ 0x11]);
    rows.forEach((row, i) => row.forEach((v, c) => view.setFloat32(4 + i * 32 + c * 4, v, true)));
    bytes[bytes.length - 1] = 0x5A; return bytes;
  };
  Object.defineProperty(navigator, 'serial', { configurable: true, value: {
    async requestPort() {
      window.chooserActivations.push(navigator.userActivation.isActive);
      let controller, interval, received = 0, start = 0;
      const stop = () => clearInterval(interval);
      const port = {
        readable: new ReadableStream({ start(c) { controller = c; }, cancel() { stop(); } }),
        writable: new WritableStream({ write(bytes) {
          if (bytes[3] === 0x09 || bytes[3] === 0x10) {
            stop(); controller.enqueue(new Uint8Array([0xA5, 4, 0x10, 0x14, 2, 4, 0x5A]));
          }
          if (bytes[3] === 0x11) {
            stop();
            if (bytes[4] === 1) {
              received = 0; start = performance.now();
              interval = setInterval(() => {
                const target = Math.floor((performance.now() - start) * 2);
                while (received < target) {
                  const rows = [];
                  const count = Math.min(7, target - received);
                  for (let i = 0; i < count; i++, received++) rows.push(Array.from({ length: 8 }, (_, c) => 20 * Math.sin(received * .2 + c)));
                  controller.enqueue(frame(rows));
                }
              }, 10);
            }
          }
        }, abort() { stop(); } }),
        async open(options) { if (options.baudRate !== 1000000) throw new Error('wrong baud'); },
        async close() {
          stop();
          if (this.readable.locked || this.writable.locked) throw new Error('locks still held');
          this.closed = true;
        },
        disconnect() { stop(); controller.close(); },
        corrupt() { controller.enqueue(new Uint8Array([0, 1, 2])); },
      };
      window.fakePorts.push(port); return port;
    }
  } });
}
(async () => {
  const browser = await chromium.launch({ headless: true,
    executablePath: process.env.CHROME_PATH || 'C:/Users/14148/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe',
    args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  const errors = [];
  let context;
  try {
    context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await context.addInitScript(fakeHardware);
    const page = await context.newPage();
    page.on('pageerror', e => errors.push(e.message));
    const api = async () => (await context.request.get(url + '/api/status')).json();
    await page.goto(url);
    await page.waitForSelector('#hand-canvas[data-model-state="ready"]');
    check(await page.locator('.hand-error').count() === 0, 'continuous WebGL hand initializes');
    await tap(page, 'begin');
    await page.locator('input[name="gender"][value="female"]').check();
    await page.locator('input[name="hand"][value="left"]').check();
    await page.locator('input[name="language"][value="en"]').check();
    check(await page.locator('html').getAttribute('lang') === 'en', 'language preference applies');
    check((await page.locator('.stage-pill').textContent()).includes('LEFT'), 'left hand preference applies');
    await tap(page, 'guide');
    const templates = await (await context.request.get(url + '/api/templates')).json();
    check(templates.actions.length === 40 && templates.repetitions === 6 && templates.duration_s === 9, 'actual pinned templates expose all 40 × 6, 9-second repetitions');
    const plan = templates.capture_plan;
    const firstAction = plan.actions[0];
    if (plan.profile === 'two-actions') {
      check(plan.actions.length === 2 && plan.groups === 3 && plan.repetitions_per_group === 6 && plan.total === 36, 'two-action plan has three groups with six repetitions per action');
      check(await page.locator('#guide-progress').getAttribute('max') === '36', 'two-action progress has 36 trials');
      check((await page.locator('#action-title').textContent()).includes(plan.action_names[String(firstAction)].en), 'guide uses the selected action name');
      check((await page.locator('#action-title').textContent()).includes('Group 1/3'), 'guide shows group and within-group repetition');
    }
    check(templates.poses.length === 40 && templates.poses[0].length === 181 && templates.poses[0][0].length === 10, 'ten-joint guide template dimensions');
    check(await page.locator('[data-action^="skip"]').count() === 0, 'real recording has no demo fast-forward');
    await page.route('**/api/cue', async route => {
      if (route.request().postDataJSON()?.event === 'complete') await new Promise(resolve => setTimeout(resolve, 300));
      await route.continue();
    });
    await tap(page, 'connect');
    await until(async () => { const session = (await api()).session; return session?.valid_repetitions >= 1 && session.next_segment_id >= 3; }, 'first full real-API repetition and next segment');
    await tap(page, 'pause');
    await until(async () => (await api()).status === 'paused' && await page.locator('[data-action="resume"]').isEnabled(), 'pause closes serial and recording');
    let status = await api();
    await page.unroute('**/api/cue');
    check(status.session.completed.some(r => r.action === firstAction && r.repetition === 1), 'first intended repetition recorded');
    check(status.session.valid_repetitions === 1, 'partial second repetition does not count');
    check(status.session.stored_rows > 17500, 'raw rows were actually saved');
    check(await page.evaluate(() => fakePorts.every(p => p.closed) && chooserActivations.every(Boolean)), 'chooser keeps user activation and pause releases every port');

    if (process.env.SESSION_ROOT) {
      const dir = path.join(process.env.SESSION_ROOT, status.session.session_id);
      const manifest = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8'));
      const events = fs.readFileSync(path.join(dir, 'events.jsonl'), 'utf8').trim().split('\n').map(JSON.parse);
      const bytes = fs.readFileSync(path.join(dir, 'emg.f32'));
      check(bytes.length === status.session.stored_rows * 32, 'on-disk scan-major eight-float row count matches acknowledgments');
      check(manifest.wire_units === 'uV' && manifest.stored_units === 'V' && manifest.device_continuity === 'unverified_legacy_wire', 'microvolt input is saved as volts with continuity limits retained');
      check(manifest.capture_plan.profile === plan.profile, 'actual recording preserves the selected capture plan');
      if (plan.profile === 'two-actions') {
        check(manifest.model_provenance.state_size === 512 && manifest.model_provenance.input_size === 16 && manifest.model_provenance.output_size === 10, 'two-action recording binds NCP512 with 16 inputs and ten outputs');
        check(manifest.repetitions[0].group === 1 && manifest.repetitions[0].repetition_in_group === 1, 'saved repetition has group provenance');
      }
      let peak = 0;
      for (let offset = 0; offset < bytes.length; offset += 4) peak = Math.max(peak, Math.abs(bytes.readFloatLE(offset)));
      check(peak > 19.9e-6 && peak <= 20.1e-6, 'fake 20-microvolt waveform is stored with a 20-microvolt peak in volts');
      check(events.some(e => e.event === 'cue_cue_on') && events.some(e => e.event === 'pause'), 'actual cue and pause audit saved');
      check(manifest.segments.some(s => !s.valid), 'interrupted segment remains invalid in the manifest');
      check(manifest.gaps.some(g => g.reason === 'repetition_transition' && g.received_sample_end - g.received_sample_start >= 400 && !g.stored), 'delayed completion has an explicit unrecorded host-index interval');
    }

    await page.reload(); await page.waitForSelector('#hand-canvas[data-model-state="ready"]');
    await page.locator('[data-nav="guide"]').click();
    check((await page.locator('#action-title').textContent()).includes('2'), 'reload resumes at the unfinished repetition');
    await tap(page, 'resume');
    await until(async () => (await api()).session?.valid_repetitions === 2, 'replayed second complete repetition');
    await page.evaluate(() => fakePorts.at(-1).disconnect());
    await until(async () => (await api()).status === 'paused' && await page.locator('[data-action="resume"]').isEnabled(), 'disconnect recovery');
    status = await api();
    check(status.session.valid_repetitions === 2 && status.session.next_segment_id >= 4, 'disconnect retains completed trials and allocates a fresh segment');

    await tap(page, 'resume');
    await page.waitForTimeout(400);
    await page.evaluate(() => fakePorts.at(-1).corrupt());
    await until(async () => (await api()).status === 'paused' && await page.locator('[data-action="resume"]').isEnabled(), 'malformed frame pause');
    check((await api()).session.valid_repetitions === 2, 'parser errors cannot complete a repetition');
    await page.route('**/api/samples', async route => {
      await new Promise(resolve => setTimeout(resolve, 120)); await route.continue();
    });
    await tap(page, 'resume');
    await until(async () => (await api()).status === 'paused' && await page.locator('[data-action="resume"]').isEnabled(), 'bounded save backlog pauses capture');
    await page.unroute('**/api/samples');
    check((await api()).session.valid_repetitions === 2, 'slow saving pauses without counting an incomplete trial');
    await tap(page, 'resume'); await page.waitForTimeout(400);
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, value: true });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await until(async () => (await api()).status === 'paused' && await page.locator('[data-action="resume"]').isEnabled(), 'visibility loss pauses capture');
    await page.evaluate(() => { delete document.hidden; });
    check((await api()).session.valid_repetitions === 2, 'backgrounding requires a fresh repetition');
    await page.screenshot({ path: path.join(artifacts, 'capture-desktop.png'), fullPage: true, animations: 'disabled' });
    await page.setViewportSize({ width: 375, height: 812 });
    await page.screenshot({ path: path.join(artifacts, 'capture-mobile.png'), fullPage: true, animations: 'disabled' });
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'mobile capture has no horizontal overflow');

    for (const endpoint of ['/api/train', '/api/live']) {
      const response = await context.request.post(url + endpoint, { data: { verified: true, scale: .000001 } });
      check(response.status() === 409 && (await response.json()).error === 'blocked_unit_evidence', endpoint + ' cannot bypass the production unit gate');
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.locator('[data-nav="adapt"]').click();
    await tap(page, 'train');
    await until(async () => (await page.locator('#training-gate').textContent()).includes('unit conversion'), 'training response is rendered');
    check((await page.locator('#training-gate').textContent()).includes('unit conversion'), 'adaptation explains the real blocker');
    check(await page.locator('.result-table tbody tr').count() === 10, 'all ten targets remain unmeasured');
    check((await page.locator('.result-table tbody tr td:nth-child(2)').allTextContents()).every(v => v === '—'), 'no fabricated metrics');
    await tap(page, 'live');
    await tap(page, 'live-check');
    await page.locator('#brand').click();
    await page.locator('#reset-cancel').click();
    check(await page.locator('main').getAttribute('data-screen') === 'live', 'reset cancel keeps the current screen');
    await page.locator('#brand').click(); await page.locator('#reset-confirm').click();
    await page.waitForSelector('main[data-screen="intro"]');
    check((await api()).status === 'closed', 'reset closes the session without deleting data');
    await tap(page, 'begin');
    check(await page.locator('input[name="gender"][value="female"]').isChecked(), 'existing browser preferences survive reset');
    check(await page.locator('input[name="hand"][value="left"]').isChecked(), 'hand preference survives reset');
    await context.close(); context = null;

    const fallback = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 375, height: 812 } });
    await fallback.addInitScript(() => {
      const get = HTMLCanvasElement.prototype.getContext;
      HTMLCanvasElement.prototype.getContext = function(type, ...args) { return type === 'webgl' ? null : get.call(this, type, ...args); };
      Storage.prototype.getItem = () => { throw new Error('storage unavailable'); };
      Storage.prototype.setItem = () => { throw new Error('storage unavailable'); };
      Object.defineProperty(navigator, 'serial', { value: undefined });
    });
    const noGL = await fallback.newPage(); noGL.on('pageerror', e => errors.push(e.message));
    await noGL.goto(url); await noGL.waitForSelector('.hand-error');
    await tap(noGL, 'begin');
    check(await noGL.locator('main').getAttribute('data-screen') === 'preferences', 'WebGL/storage unavailable does not crash navigation');
    await tap(noGL, 'guide');
    check(await noGL.locator('[data-action="connect"]').isDisabled(), 'missing guide renderer cannot start recording');
    check(await noGL.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'fallback mobile layout fits');
    await fallback.close();
    check(errors.length === 0, 'no uncaught browser errors: ' + errors.join('; '));
    console.log('Verified ' + checks + ' real-API browser checks.');
  } finally {
    if (context) await context.close();
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

