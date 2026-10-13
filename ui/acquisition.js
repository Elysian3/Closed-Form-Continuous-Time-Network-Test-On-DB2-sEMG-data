// ADS1298 protocol: only the length/type header has XOR protection, not payload CRC.
const HEADER = 0xA5, TAIL = 0x5A, CHANNELS = 8;
const MAX_READ_BYTES = 65536;

export function command(address, data = []) {
  if (![address, ...data].every(n => Number.isInteger(n) && n >= 0 && n <= 255) || data.length > 252) throw new TypeError('Invalid command bytes.');
  const body = [0x80, address, ...data], length = body.length + 1;
  return new Uint8Array([0xAA, length, ...body, body.reduce((xor, n) => xor ^ n, length), 0xBB]);
}

export class Ads1298Parser {
  constructor(onError = () => {}) { this.onError = onError; this.bytes = []; }
  fail(reason) { this.onError({ type: 'error', reason }); }
  push(chunk) {
    if (!(chunk instanceof Uint8Array)) throw new TypeError('Serial chunks must be bytes.');
    if (chunk.length > MAX_READ_BYTES) { this.bytes = []; this.fail('chunk_overflow'); return []; }
    const events = [];
    // Bound retained framing memory even for a large, coalesced browser read.
    for (let offset = 0; offset < chunk.length; offset += 4096) {
      this.bytes.push(...chunk.subarray(offset, offset + 4096));
      while (this.bytes.length) {
        const start = this.bytes.indexOf(HEADER);
        if (start < 0) { this.fail('garbage'); this.bytes = []; break; }
        if (start) { this.fail('garbage'); this.bytes.splice(0, start); }
        if (this.bytes.length < 4) break;
        const length = this.bytes[1], type = this.bytes[2];
        const validLength = type === 0x10 ? length === 4 : type === 0x11 && length >= 34 && length <= 226 && (length - 2) % 32 === 0;
        if (!validLength || this.bytes[3] !== (length ^ type)) {
          this.fail('header'); this.bytes.shift(); continue;
        }
        const total = length + 3;
        if (this.bytes.length < total) break;
        if (this.bytes[total - 1] !== TAIL) { this.fail('tail'); this.bytes.shift(); continue; }
        const frame = Uint8Array.from(this.bytes.splice(0, total));
        if (type === 0x10) {
          const rateIndex = frame[4], pgaIndex = frame[5];
          if (rateIndex > 2 || pgaIndex > 6) { this.fail('parameters'); continue; }
          events.push({ type: 'params', rateIndex, pgaIndex }); continue;
        }
        const view = new DataView(frame.buffer, 4, length - 2), samples = [];
        let finite = true;
        for (let scan = 0; scan < view.byteLength / 32; scan++) {
          const row = Array.from({ length: CHANNELS }, (_, c) => view.getFloat32(scan * 32 + c * 4, true));
          finite &&= row.every(Number.isFinite); samples.push(row);
        }
        if (finite) events.push({ type: 'samples', samples });
        else this.fail('nonfinite');
      }
    }
    return events;
  }
}

function within(promise, milliseconds, message) {
  let timer;
  return Promise.race([promise, new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(message)), milliseconds);
  })]).finally(() => clearTimeout(timer));
}

export class Ads1298Serial {
  constructor({ serial = globalThis.navigator?.serial, onSamples = () => {}, onError = () => {}, onParams = () => {} } = {}) {
    Object.assign(this, { serial, onSamples, onError, onParams });
    this.port = null; this.reader = null; this.writer = null;
    this.ready = false; this.reading = false; this.connecting = false;
    this.waiter = null; this.readTask = null; this.closeTask = null;
    this.parser = new Ads1298Parser(event => {
      // Boot text can precede binary replies; valid parameters are still required.
      if (!this.ready && event.reason === 'garbage') return;
      if (this.ready) this.onError(event);
      else this.waiter?.reject(new Error('Invalid ADS1298 reply: ' + event.reason));
    });
  }
  async connect() {
    if (!this.serial?.requestPort) throw new Error('Web Serial is unavailable in this browser.');
    if (this.connecting || this.port) throw new Error('A device connection is already active.');
    this.connecting = true;
    try {
      // First asynchronous operation: called directly from the user's click.
      this.port = await this.serial.requestPort();
      await this.port.open({ baudRate: 1000000 });
      this.writer = this.port.writable.getWriter();
      this.reader = this.port.readable.getReader();
      this.reading = true; this.readTask = this.readLoop();
      await this.write(command(0x11, [0]));
      const queried = await this.exchange(0x09, [1]);
      const confirmed = await this.exchange(0x10, [2, queried.pgaIndex]);
      if (confirmed.rateIndex !== 2 || confirmed.pgaIndex !== queried.pgaIndex) throw new Error('ADS1298 did not confirm 2000 Hz and the retained gain.');
      await this.write(command(0x11, [1]));
      if (!this.reading) throw new Error('Device disconnected during startup.');
      this.ready = true;
      this.onParams(confirmed);
      return confirmed;
    } catch (error) {
      await this.close();
      throw error;
    } finally { this.connecting = false; }
  }
  async write(bytes) {
    if (!this.writer) throw new Error('Serial writer is closed.');
    await within(this.writer.write(bytes), 3000, 'ADS1298 write timed out.');
  }
  async exchange(address, data) {
    let resolve, reject;
    const reply = new Promise((yes, no) => { resolve = yes; reject = no; });
    // A disconnect can reject while the write promise is still pending.
    reply.catch(() => {});
    this.waiter = { resolve, reject };
    try {
      await this.write(command(address, data));
      const stage = address === 0x09 ? 'query 0x09' : 'set 0x10';
      return await within(reply, 3000, `No ADS1298 parameter reply (${stage}).`);
    } finally { this.waiter = null; }
  }
  async readLoop() {
    try {
      while (this.reading) {
        const { value, done } = await this.reader.read();
        if (!this.reading) break;
        if (done) throw new Error('Device disconnected.');
        for (const event of this.parser.push(value || new Uint8Array())) {
          if (event.type === 'params') {
            const params = { rateIndex: event.rateIndex, pgaIndex: event.pgaIndex, ackAtMs: performance.now() };
            if (this.waiter) this.waiter.resolve(params);
            else if (this.ready) throw new Error('Device parameters changed while streaming.');
          } else if (this.ready) this.onSamples(event.samples);
          // Samples from before the startup handshake are deliberately ignored.
        }
      }
    } catch (error) {
      if (this.reading) {
        this.ready = false; this.reading = false;
        this.waiter?.reject(error);
        this.onError({ type: 'error', reason: 'disconnect', error });
        void this.close();
      }
    } finally {
      try { this.reader?.releaseLock(); } catch { /* close() also retries after cancellation. */ }
    }
  }
  close() {
    if (this.closeTask) return this.closeTask;
    this.closeTask = this.closePort().finally(() => { this.closeTask = null; });
    return this.closeTask;
  }
  async closePort() {
    this.ready = false; this.reading = false;
    this.waiter?.reject(new Error('Serial connection closed.'));
    if (this.writer) {
      try { await within(this.writer.write(command(0x11, [0])), 1000, 'Stop timed out.'); }
      catch { try { await within(this.writer.abort(), 1000, 'Abort timed out.'); } catch { /* Unplugged device. */ } }
    }
    try { await within(this.reader?.cancel(), 1000, 'Reader cancellation timed out.'); } catch { /* Unplugged device. */ }
    try { await within(this.readTask, 1000, 'Reader shutdown timed out.'); } catch { /* Unplugged device. */ }
    try { this.reader?.releaseLock(); } catch { /* Already released. */ }
    try { this.writer?.releaseLock(); } catch { /* Already released. */ }
    const port = this.port;
    this.reader = null; this.writer = null; this.port = null; this.readTask = null;
    this.parser.bytes = [];
    if (port) {
      try { await within(port.close(), 1000, 'Port close timed out.'); }
      catch (error) { this.onError({ type: 'error', reason: 'close', error }); }
    }
  }
}

