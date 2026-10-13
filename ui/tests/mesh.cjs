const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/14148/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const url = process.env.UI_URL || 'http://127.0.0.1:8765';
const artifacts = path.join(__dirname, 'artifacts');
fs.mkdirSync(artifacts, { recursive: true });
const jointNames = {};
for (const side of ['left', 'right']) {
  const bytes = fs.readFileSync(path.join(__dirname, '../assets/hand', `${side}.glb`));
  assert.equal(bytes.readUInt32LE(0), 0x46546c67);
  const gltf = JSON.parse(bytes.subarray(20, 20 + bytes.readUInt32LE(12)).toString());
  jointNames[side] = gltf.skins[0].joints.map(i => gltf.nodes[i].name);
  assert.equal(gltf.skins[0].joints.length, 25);
  assert.equal(gltf.meshes.length, 1);
  assert.equal(gltf.accessors[gltf.meshes[0].primitives[0].attributes.POSITION].count, 1360);
}
console.log('PASS both assets contain one authored mesh and 25 skin joints');

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH || 'C:/Users/14148/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe', args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  try {
    const page = await browser.newPage({ viewport: { width: 720, height: 720 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/__mesh-test', route => route.fulfill({ contentType: 'text/html', body: '<html><body style="margin:0;background:#e5eee7"><canvas id="hand" style="width:720px;height:720px;touch-action:none"></canvas></body></html>' }));
    await page.goto(`${url}/__mesh-test`);
    await page.evaluate(async () => {
      const upload = WebGLRenderingContext.prototype.uniformMatrix4fv;
      WebGLRenderingContext.prototype.uniformMatrix4fv = function(location, transpose, values) {
        if (values.length === 400) window.uploadedBones = Array.from(values);
        return upload.call(this, location, transpose, values);
      };
      const { createHand } = await import('/hand.js');
      window.testHand = createHand(document.querySelector('canvas'));
      await testHand.ready;
      testHand.setMotionEnabled(false);
    });
    const pose = async (fingers, side) => {
      await page.evaluate(({ fingers, side }) => { testHand.setHandedness(side); testHand.setPose({ fingers }); }, { fingers, side });
      await page.waitForTimeout(80);
    };
    const pixels = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => {
      const canvas = document.querySelector('canvas'), gl = canvas.getContext('webgl');
      const data = new Uint8Array(canvas.width * canvas.height * 4);
      gl.readPixels(0, 0, canvas.width, canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, data);
      let count = 0, hash = 2166136261, xMin = canvas.width, xMax = 0, yMin = canvas.height, yMax = 0;
      for (let i = 0; i < data.length; i += 4) {
        if (data[i + 3] < 10) continue;
        const p = i / 4, x = p % canvas.width, y = Math.floor(p / canvas.width);
        count++; hash = Math.imul(hash ^ data[i], 16777619);
        xMin = Math.min(xMin, x); xMax = Math.max(xMax, x); yMin = Math.min(yMin, y); yMax = Math.max(yMax, y);
      }
      resolve({ count, hash, xMin, xMax, yMin, yMax, width: canvas.width, height: canvas.height, error: gl.getError() });
    })));
    const framed = p => { assert(p.count > 1000, 'visible mesh pixels'); assert.equal(p.error, 0); assert(p.xMin > 10 && p.yMin > 10 && p.xMax < p.width - 10 && p.yMax < p.height - 10, `mesh has margins: ${JSON.stringify(p)}`); };
    for (const side of ['right', 'left']) {
      await pose([0, 0, 0, 0, 0], side);
      const open = await pixels(); framed(open);
      assert(open.yMax - open.yMin > (open.xMax - open.xMin) * 1.1, `${side} neutral silhouette is an upright hand`);
      await page.screenshot({ path: path.join(artifacts, `mesh-${side}-open.png`) });
      for (let finger = 0; finger < 5; finger++) {
        const fingers = [0, 0, 0, 0, 0]; fingers[finger] = .85;
        await pose(fingers, side);
        const bent = await pixels(); framed(bent);
        assert.notEqual(open.hash, bent.hash, `${side} finger ${finger} deforms the surface`);
      }
      await pose([.55, .9, .94, 1, 1], side);
      const fist = await pixels(); framed(fist);
      assert(fist.yMax - fist.yMin < (open.yMax - open.yMin) * .9, `${side} finger curl shortens the open hand silhouette`);
      await page.screenshot({ path: path.join(artifacts, `mesh-${side}-fist.png`) });
      await page.mouse.move(100, 360); await page.mouse.down(); await page.mouse.move(362, 360, { steps: 6 }); await page.mouse.up();
      await page.waitForTimeout(80);
      await page.screenshot({ path: path.join(artifacts, `mesh-${side}-fist-palm.png`) });
      await page.mouse.down(); await page.mouse.move(100, 360, { steps: 6 }); await page.mouse.up();
      console.log(`PASS ${side}: all five fingers deform, open/fist render without clipping`);
    }
    // J10 drives only its intended flexion joint and descendants, with fixed
    // wrist/metacarpals and no frame-based easing even if legacy smoothing is on.
    for (const side of ['right', 'left']) {
      const bones = values => page.evaluate(async ({ values, side }) => {
        testHand.setHandedness(side);
        testHand.setPose({ wrist: .8 });
        testHand.setMotionEnabled(true);
        testHand.setJ10(values);
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        const first = uploadedBones.slice();
        await new Promise(resolve => requestAnimationFrame(resolve));
        return { first, next: uploadedBones.slice() };
      }, { values, side });
      const neutral = (await bones(Array(10).fill(0))).first;
      const expected = [[2,3,4], [3,4], [6,7,8,9], [7,8,9], [11,12,13,14], [12,13,14], [16,17,18,19], [17,18,19], [21,22,23,24], [22,23,24]];
      for (let target = 0; target < 10; target++) {
        const values = Array(10).fill(0); values[target] = .7;
        const result = await bones(values), changed = [];
        for (let joint = 0; joint < 25; joint++) {
          if (result.first.slice(joint * 16, joint * 16 + 16).some((v, i) => Math.abs(v - neutral[joint * 16 + i]) > 1e-5)) changed.push(joint);
        }
        assert.deepEqual(changed.map(i => jointNames[side][i]).sort(), expected[target].map(i => jointNames.right[i]).sort(), side + ' J10 target ' + target + ' joint ownership');
        assert.deepEqual(result.first, result.next, 'J10 bypasses easing on the first rendered frame');
      }
      console.log('PASS ' + side + ': ten independent J10 inputs, fixed wrist/opposition and immediate rendering');
    }
    await page.evaluate(() => { testHand.setPose({ wrist: 0 }); testHand.setMotionEnabled(false); });
    const beforeDrag = await pixels();
    await page.mouse.move(360, 360); await page.mouse.down(); await page.mouse.move(470, 380, { steps: 8 }); await page.mouse.up();
    await page.waitForTimeout(80);
    assert.notEqual(beforeDrag.hash, (await pixels()).hash);
    console.log('PASS pointer orbit changes model view');
    for (const [width, height] of [[300, 530], [500, 300]]) {
      await page.evaluate(({ width, height }) => { const c = document.querySelector('canvas'); c.style.width = `${width}px`; c.style.height = `${height}px`; testHand.resize(); }, { width, height });
      await pose([0, 0, 0, 0, 0], 'right'); framed(await pixels());
    }
    console.log('PASS narrow and wide canvases retain mesh margins');
    await page.evaluate(() => testHand.dispose());
    await page.close();

    const failed = await browser.newPage();
    failed.on('pageerror', e => errors.push(e.message));
    await failed.route('**/assets/hand/*.glb', route => route.abort());
    await failed.goto(url);
    await failed.waitForSelector('.hand-error');
    assert((await failed.locator('.hand-error').textContent()).includes('模型未能加载'));
    await failed.locator('[data-action=begin]').click();
    assert.equal(await failed.locator('main').getAttribute('data-screen'), 'preferences');
    console.log('PASS missing asset shows model error and remaining flow works');
    await failed.close();
    assert.deepEqual(errors, []);
    console.log('PASS no uncaught browser or WebGL errors');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
