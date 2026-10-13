const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../acquisition.js'));
const moduleUrl = `data:text/javascript;base64,${source.toString('base64')}`;
const pause = () => new Promise(resolve => setTimeout(resolve, 0));

function packet(scans) {
  const out = new Uint8Array(scans.length * 32 + 5), length = out.length - 3;
  out.set([0xA5, length, 0x11, length ^ 0x11]);
  const view = new DataView(out.buffer);
  scans.forEach((row, i) => row.forEach((v, c) => view.setFloat32(4 + i * 32 + c * 4, v, true)));
  out[out.length - 1] = 0x5A; return out;
}
const params = (rate = 2, gain = 4) => new Uint8Array([0xA5, 4, 0x10, 0x14, rate, gain, 0x5A]);
function fakePort({ wrongRate = false, failOpen = false, failWrite = false, startupText = '', textOnly = false, badHeader = false, missingReplyAddress = null } = {}) {
  let controller;
  const commands = [];
  const port = {
    readable: new ReadableStream({ start(c) { controller = c; } }),
    writable: new WritableStream({ write(bytes) {
      commands.push([...bytes]);
      if (failWrite) throw new Error('write failure');
      if (bytes[3] === 0x09 || bytes[3] === 0x10) {
        // Older waveform data can arrive before a command's parameter reply.
        controller.enqueue(packet([Array(8).fill(-123)]));
        if (bytes[3] === missingReplyAddress) return;
        const reply = params(bytes[3] === 0x10 && wrongRate ? 1 : 2);
        if (badHeader) reply[3] ^= 1;
        const boot = new TextEncoder().encode('Synthetic self-test passed: max_error=6.10948e-07 max_step_us=127520\r\n');
        if (textOnly) controller.enqueue(boot);
        else if (startupText === 'coalesced') controller.enqueue(new Uint8Array([...boot, ...reply]));
        else if (startupText === 'fragmented') {
          controller.enqueue(boot.subarray(0, 19));
          controller.enqueue(new Uint8Array([...boot.subarray(19), ...reply]));
        } else controller.enqueue(reply);
      }
    }}),
    opened: false, closed: false,
    async open(options) {
      assert.equal(options.baudRate, 1000000);
      if (failOpen) throw new Error('open failure');
      this.opened = true;
    },
    async close() {
      assert.equal(this.readable.locked, false, 'read lock released before close');
      assert.equal(this.writable.locked, false, 'write lock released before close');
      this.closed = true;
    },
    emit(bytes) { controller.enqueue(bytes); },
    eof() { controller.close(); },
    commands,
  };
  return port;
}
(async () => {
  const { Ads1298Parser, Ads1298Serial, command } = await import(moduleUrl);
  assert.deepEqual([...command(0x10, [2,4])], [0xAA,5,0x80,0x10,2,4,0x93,0xBB]);
  assert.deepEqual([...command(0x09, [1])], [0xAA,4,0x80,9,1,0x8C,0xBB]);
  for (let scans = 1; scans <= 7; scans++) {
    const rows = Array.from({ length: scans }, (_, i) => Array.from({ length: 8 }, (_, c) => (i * 8 + c - 4) / 2));
    const bytes = packet(rows);
    for (let split = 1; split < bytes.length; split++) {
      const parser = new Ads1298Parser(() => assert.fail('valid split reported an error'));
      assert.deepEqual(parser.push(bytes.subarray(0, split)), []);
      assert.deepEqual(parser.push(bytes.subarray(split)), [{ type: 'samples', samples: rows }]);
    }
    const parser = new Ads1298Parser();
    const events = [...bytes].flatMap(n => parser.push(new Uint8Array([n])));
    assert.deepEqual(events[0].samples, rows);
  }
  const marker = new DataView(new ArrayBuffer(4));
  marker.setUint32(0, 0x3F5AA5A5, true);
  const marked = packet([Array(8).fill(marker.getFloat32(0, true))]);
  const parser = new Ads1298Parser();
  assert.equal(parser.push(new Uint8Array([...params(), ...marked, ...marked])).length, 3);
  console.log('PASS commands, all 1–7 scan fragment boundaries, coalescing and header bytes in floats');

  const reasons = [], recover = new Ads1298Parser(e => reasons.push(e.reason));
  const good = packet([Array(8).fill(1)]);
  for (const corrupt of [
    new Uint8Array([1, 2, 3]),
    new Uint8Array([0xA5, 2, 0x11, 0x13, 0x5A]),
    new Uint8Array([0xA5, 255, 0x11, 0xEE]),
    new Uint8Array([...good.slice(0, -1), 0]),
    packet([Array(8).fill(NaN)]),
    packet([Array(8).fill(Infinity)]),
    params(3, 4), params(2, 7),
  ]) {
    recover.push(corrupt);
    assert.deepEqual(recover.push(good).at(-1).samples, [Array(8).fill(1)]);
    assert(recover.bytes.length < 229);
  }
  assert.deepEqual(recover.push(new Uint8Array(65537)), []);
  for (const reason of ['garbage', 'header', 'tail', 'nonfinite', 'parameters', 'chunk_overflow']) assert(reasons.includes(reason), reason);
  console.log('PASS malformed recovery, finite payloads, valid parameters and bounded framing buffer');

  const port = fakePort(), seen = [], errors = [];
  let chooserCalls = 0;
  const serial = new Ads1298Serial({ serial: { requestPort() { chooserCalls++; return Promise.resolve(port); } },
    onSamples: rows => seen.push(rows), onError: error => errors.push(error) });
  const connecting = serial.connect();
  assert.equal(chooserCalls, 1, 'chooser called synchronously before first await');
  const ack = await connecting;
  assert.equal(ack.rateIndex, 2); assert.equal(ack.pgaIndex, 4); assert(Number.isFinite(ack.ackAtMs));
  assert.equal(serial.ready, true);
  assert.deepEqual(seen, [], 'pre-handshake waveform ignored');
  assert.deepEqual(port.commands.map(bytes => bytes[3]), [0x11, 0x09, 0x10, 0x11]);
  assert.deepEqual(port.commands[2].slice(4, 6), [2, 4], 'actual PGA retained');
  port.emit(good); await pause();
  assert.deepEqual(seen, [[Array(8).fill(1)]]);
  await serial.close(); await serial.close();
  assert(port.closed); assert.equal(serial.ready, false); assert.equal(serial.port, null);
  assert.deepEqual(errors, []);
  console.log('PASS actual Web Streams handshake, stale suppression, sample delivery and lock cleanup');

  for (const startupText of ['coalesced', 'fragmented']) {
    const bootPort = fakePort({ startupText }), bootErrors = [], bootSamples = [];
    const bootSerial = new Ads1298Serial({ serial: { requestPort: async () => bootPort },
      onSamples: rows => bootSamples.push(rows), onError: e => bootErrors.push(e.reason) });
    const bootAck = await bootSerial.connect();
    assert.equal(bootAck.rateIndex, 2); assert.equal(bootAck.pgaIndex, 4);
    assert(bootSerial.ready); assert.deepEqual(bootErrors, []); assert.deepEqual(bootSamples, []);
    bootPort.emit(good); await pause();
    assert.deepEqual(bootSamples, [[Array(8).fill(1)]]);
    bootPort.emit(new TextEncoder().encode('unexpected text during capture\r\n')); await pause();
    assert.deepEqual(bootErrors, ['garbage'], 'streaming text must still report a capture error');
    await bootSerial.close(); assert(bootPort.closed);
  }
  console.log('PASS coalesced/fragmented boot text allows verified handshake; streaming garbage stays strict');

  for (const [settings, message] of [
    [{ textOnly: true }, /No ADS1298 parameter reply/],
    [{ startupText: 'coalesced', badHeader: true }, /Invalid ADS1298 reply: header/],
  ]) {
    const bootPort = fakePort(settings);
    const bootSerial = new Ads1298Serial({ serial: { requestPort: async () => bootPort } });
    await assert.rejects(bootSerial.connect(), message);
    assert(bootPort.closed); assert.equal(bootSerial.ready, false);
  }
  console.log('PASS boot text alone cannot connect; malformed parameter replies still fail');

  for (const [address, message] of [
    [0x09, /No ADS1298 parameter reply \(query 0x09\)/],
    [0x10, /No ADS1298 parameter reply \(set 0x10\)/],
  ]) {
    const silentPort = fakePort({ missingReplyAddress: address });
    const silent = new Ads1298Serial({ serial: { requestPort: async () => silentPort } });
    await assert.rejects(silent.connect(), message);
    assert(silentPort.closed); assert.equal(silent.ready, false); assert.equal(silent.port, null);
    assert(!silentPort.commands.some(bytes => bytes[3] === 0x11 && bytes[4] === 1),
      'missing parameters must not start streaming');
    assert.equal(silentPort.commands.some(bytes => bytes[3] === 0x10), address === 0x10,
      'parameter setup must not run without a query reply');
  }
  console.log('PASS query/setup timeouts identify the failed stage and close without starting');

  for (const settings of [{ wrongRate: true }, { failOpen: true }, { failWrite: true }]) {
    const failedPort = fakePort(settings);
    const failed = new Ads1298Serial({ serial: { requestPort: async () => failedPort } });
    await assert.rejects(failed.connect());
    assert(failedPort.closed); assert.equal(failed.ready, false);
  }
  const cancelled = new Ads1298Serial({ serial: { requestPort: async () => { throw new Error('chooser cancelled'); } } });
  await assert.rejects(cancelled.connect(), /cancelled/);
  await assert.rejects(new Ads1298Serial({ serial: null }).connect(), /unavailable/);
  const droppedPort = fakePort(), drops = [];
  const dropped = new Ads1298Serial({ serial: { requestPort: async () => droppedPort }, onError: e => drops.push(e.reason) });
  await dropped.connect(); droppedPort.eof();
  for (let i = 0; i < 10 && !droppedPort.closed; i++) await pause();
  assert(drops.includes('disconnect')); assert(droppedPort.closed); assert.equal(dropped.ready, false);
  console.log('PASS cancelled/failed startup, mismatched rate and unexpected EOF close cleanly');
})().catch(error => { console.error(error); process.exitCode = 1; });

