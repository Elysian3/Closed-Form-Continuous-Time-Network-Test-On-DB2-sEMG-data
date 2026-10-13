// Isolated fake-serial regression for DC-offset clipping in the raw trace.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/14148/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const url = process.env.UI_URL;
const root = process.env.SESSION_ROOT;
if (!url || !root) throw new Error('Use ui/tests/run_browser.py to provide an isolated UI_URL and SESSION_ROOT.');

function fakeHardware(mode) {
  window.waveformMode = mode === 'dc' ? 'dc' : 'ac';
  window.fakePorts = [];
  const frame = rows => {
    const bytes = new Uint8Array(rows.length * 32 + 5), n = bytes.length - 3;
    const view = new DataView(bytes.buffer);
    bytes.set([0xA5, n, 0x11, n ^ 0x11]);
    rows.forEach((row, i) => row.forEach((v, c) => view.setFloat32(4 + i * 32 + c * 4, v, true)));
    bytes[bytes.length - 1] = 0x5A;
    return bytes;
  };
  Object.defineProperty(navigator, 'serial', { configurable: true, value: {
    async requestPort() {
      let controller, timer, received = 0, started = 0;
      const stop = () => clearInterval(timer);
      const port = {
        readable: new ReadableStream({ start(c) { controller = c; }, cancel() { stop(); } }),
        writable: new WritableStream({ write(bytes) {
          if (bytes[3] === 0x09 || bytes[3] === 0x10) {
            stop(); controller.enqueue(new Uint8Array([0xA5, 4, 0x10, 0x14, 2, 4, 0x5A]));
          }
          if (bytes[3] === 0x11) {
            stop();
            if (bytes[4] === 1) {
              received = 0; started = performance.now();
              timer = setInterval(() => {
                const target = Math.floor((performance.now() - started) * 2);
                while (received < target) {
                  const rows = [];
                  for (let i = 0, count = Math.min(7, target - received); i < count; i++, received++) {
                    rows.push(Array.from({ length: 8 }, (_, c) => -14000 + (window.waveformMode === 'dc' ? 0 : (c % 2 ? 3000 : 1000) * Math.sin(received * .08 + c * .3))));
                  }
                  controller.enqueue(frame(rows));
                }
              }, 10);
            }
          }
        }, abort() { stop(); } }),
        async open(options) { if (options.baudRate !== 1000000) throw new Error('wrong baud'); },
        async close() { stop(); },
      };
      window.fakePorts.push(port);
      return port;
    },
  } });
}

(async () => {
  const browser = await chromium.launch({ headless: true,
    executablePath: process.env.CHROME_PATH || 'C:/Users/14148/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe',
    args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const mode = process.env.WAVEFORM_MODE === 'dc' ? 'dc' : 'ac';
    await context.addInitScript(fakeHardware, mode);
    const page = await context.newPage();
    page.on('pageerror', error => { throw error; });
    await page.goto(url);
    await page.waitForSelector('#hand-canvas[data-model-state="ready"]');
    await page.locator('[data-action="begin"]').click();
    await page.locator('[data-action="guide"]').click();
    await page.locator('[data-action="connect"]').click();
    await page.waitForFunction(() => Number(document.querySelector('#received-count')?.textContent.replaceAll(',', '')) >= 600, null, { timeout: 12000 });
    await page.waitForTimeout(100);

    const trace = await page.locator('#signal-chart').evaluate(canvas => {
      const { width, height } = canvas;
      const data = canvas.getContext('2d').getImageData(0, 0, width, height).data;
      const ranges = [];
      for (let channel = 0; channel < 8; channel++) {
        let min = height, max = -1;
        const bandCenter = (channel + .5) * height / 8;
        for (let y = Math.max(0, Math.floor(bandCenter - height / 18)); y < Math.min(height, Math.ceil(bandCenter + height / 18)); y++) for (let x = Math.floor(width * .2); x < Math.ceil(width * .8); x++) {
          const p = (y * width + x) * 4;
          if (data[p] < 65 && data[p + 1] > 70 && data[p + 1] < 145 && data[p + 2] > 50 && data[p + 2] < 115) {
            min = Math.min(min, y); max = Math.max(max, y);
          }
        }
        ranges.push(max < 0 ? 0 : (max - min) / Math.min(devicePixelRatio || 1, 2));
      }
      return { ranges, width, height, dpr: Math.min(devicePixelRatio || 1, 2) };
    });
    console.log('trace pixel ranges CSS px:', JSON.stringify(trace));

    const status = await (await context.request.get(url + '/api/status')).json();
    assert(status.session?.session_id, 'temporary capture session was created');
    const manifest = JSON.parse(fs.readFileSync(path.join(root, status.session.session_id, 'manifest.json'), 'utf8'));
    const raw = fs.readFileSync(path.join(root, status.session.session_id, 'emg.f32'));
    assert.equal(manifest.wire_units, 'uV');
    assert.equal(manifest.stored_units, 'V');
    assert(raw.length >= 600 * 8 * 4, 'raw capture includes received rows');
    const firstChannel = [];
    for (let offset = 0; offset + 4 <= raw.length; offset += 32) firstChannel.push(raw.readFloatLE(offset));
    const dc = firstChannel.reduce((sum, value) => sum + value, 0) / firstChannel.length;
    assert(Math.abs(dc - (-14e-3)) < 1e-3, `saved first-channel DC remains near -14 mV, got ${dc}`);
    const firstMin = Math.min(...firstChannel), firstMax = Math.max(...firstChannel);
    if (mode === 'ac') {
      assert(firstMin < -14.9e-3 && firstMin > -15.1e-3, `saved minimum retains the -15 mV input range, got ${firstMin}`);
      assert(firstMax < -12.9e-3 && firstMax > -13.1e-3, `saved maximum retains the -13 mV input range, got ${firstMax}`);
    } else {
      assert(Math.abs(firstMin - (-14e-3)) < 1e-5 && Math.abs(firstMax - (-14e-3)) < 1e-5, 'constant input remains unchanged in raw storage');
    }
    console.log('PASS raw storage retains -14 mV DC and V units');
    if (mode === 'ac') assert(trace.ranges.every(range => range > 5), 'each of eight channels must show >5 CSS px of AC movement');
    else assert(trace.ranges.every(range => range <= 2), `constant DC input must remain a horizontal trace, got ${trace.ranges}`);
    console.log(`PASS ${mode} waveform display; channel ranges CSS px: ${trace.ranges.join(', ')}`);
    await context.close();
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
