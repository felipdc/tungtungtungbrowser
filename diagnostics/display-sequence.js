'use strict';

// This is the local page's own content. It changes no browser APIs or other pages.
(async () => {
  const status = document.getElementById('status');
  try {
    const query = new URLSearchParams(location.search);
    function integer(name, fallback, min, max) {
      const raw = query.get(name);
      const value = raw === null ? fallback : Number(raw);
      if (!Number.isInteger(value) || value < min || value > max) {
        throw new Error('Invalid ' + name);
      }
      return value;
    }
    const steps = integer('steps', 16, 1, 64);
    const dwell = integer('dwell_ms', 12000, 1000, 60000);
    const run = Array.from(crypto.getRandomValues(new Uint8Array(12)),
      value => value.toString(16).padStart(2, '0')).join('');
    const number = document.getElementById('number');
    const cells = document.getElementById('cells');
    const canvas = document.getElementById('canvas');
    const context = canvas.getContext('2d');
    if (!context) throw new Error('2D canvas unavailable');
    const start = performance.now();
    const raf = () => new Promise(resolve => requestAnimationFrame(resolve));
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const rect = node => {
      const r = node.getBoundingClientRect();
      return {x: r.x, y: r.y, width: r.width, height: r.height};
    };
    async function record(observation) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 15000);
      try {
        const response = await fetch(new URL('input', location.href), {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({diagnostic: 'display-sequence', version: 1,
            run, steps, dwellMilliseconds: dwell, url: location.href,
            performanceTimeOrigin: performance.timeOrigin,
            elapsedMilliseconds: performance.now() - start,
            visibility: document.visibilityState, ...observation}),
          signal: controller.signal
        });
        if (!response.ok) throw new Error('Callback HTTP ' + response.status);
        await response.json();
      } finally { clearTimeout(timer); }
    }
    status.textContent = 'Run ' + run + '; ' + steps + ' steps';
    for (let sequence = 1; sequence <= steps; sequence++) {
      const written = performance.now();
      number.textContent = String(sequence).padStart(3, '0');
      cells.replaceChildren();
      context.clearRect(0, 0, canvas.width, canvas.height);
      for (let column = 0; column < 10; column++) {
        const marker = column === 0 ? '#ff00ff' : column === 9 ? '#00ffff' : null;
        const bit = (sequence >> (8 - column)) & 1;
        const cell = document.createElement('div');
        for (let row = 0; row < 2; row++) {
          const color = marker || ((bit ^ row) ? '#ffffff' : '#000000');
          const half = document.createElement('div');
          half.style.backgroundColor = color;
          cell.appendChild(half);
          context.fillStyle = color;
          context.fillRect(column * 32, row * 32, 32, 32);
        }
        cells.appendChild(cell);
      }
      context.fillStyle = '#000000';
      context.font = '24px monospace';
      context.fillText(String(sequence).padStart(3, '0'), 0, 90);
      const first = await raf();
      const second = await raf();
      // rAF establishes callback ordering, not that a host-visible frame was painted.
      await record({phase: 'step', sequence, writtenMilliseconds: written,
        firstAnimationFrameMilliseconds: first, secondAnimationFrameMilliseconds: second,
        domText: number.textContent, rectangles: {dom: rect(cells), canvas: rect(canvas)}});
      if (sequence < steps) await sleep(dwell);
    }
    await record({phase: 'complete', sequence: steps});
    status.textContent = 'Complete: ' + run;
  } catch (error) {
    status.textContent = 'Stopped: ' + error;
    // No callback, page reload, timer or native input is retried.
  }
})();
