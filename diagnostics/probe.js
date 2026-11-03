'use strict';
const inputTrace = [];
const touchButton = document.getElementById('touch-test');
for (const type of ['pointerdown', 'pointerup', 'pointercancel', 'touchstart',
  'touchend', 'touchcancel', 'mousedown', 'mouseup', 'click']) {
  touchButton.addEventListener(type, event => {
    if (type === 'pointerdown' || (type === 'touchstart' &&
        !inputTrace.some(item => item.type === 'pointerdown'))) inputTrace.length = 0;
    inputTrace.push({
      type, isTrusted: event.isTrusted, timeStamp: event.timeStamp,
      pointerType: event.pointerType ?? null, pointerId: event.pointerId ?? null,
      width: event.width ?? null, height: event.height ?? null,
      pressure: event.pressure ?? null, clientX: event.clientX ?? null,
      clientY: event.clientY ?? null,
      changedTouches: event.changedTouches ? Array.from(event.changedTouches, touch => ({
        identifier: touch.identifier, clientX: touch.clientX, clientY: touch.clientY,
        radiusX: touch.radiusX, radiusY: touch.radiusY, force: touch.force,
        rotationAngle: touch.rotationAngle,
        webkitRadiusX: touch.webkitRadiusX ?? null,
        webkitRadiusY: touch.webkitRadiusY ?? null,
        webkitForce: touch.webkitForce ?? null
      })) : null
    });
    if (inputTrace.length > 32) inputTrace.shift();
  }, {passive: true});
}
document.getElementById('touch-test').addEventListener('click', async event => {
  const button = event.currentTarget;
  const observation = {
    capturedAt: new Date().toISOString(), type: event.type, isTrusted: event.isTrusted,
    clientX: event.clientX, clientY: event.clientY,
    screenX: event.screenX, screenY: event.screenY,
    pointerType: event.pointerType || null, events: inputTrace.slice()
  };
  button.textContent = 'Touch received';
  try {
    const response = await fetch('input', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(observation)});
    if (!response.ok) throw new Error('HTTP ' + response.status);
    button.textContent = 'Touch recorded';
  } catch (error) { button.textContent = 'Touch save failed: ' + error; }
});
function workerObservation(shared, timeoutMilliseconds) {
  return new Promise(resolve => {
    const started = performance.now();
    let worker, port, timer, settled = false;
    const finish = (value, outcome) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (shared && port) port.close();
      else if (worker) worker.terminate();
      resolve({...value, measurement: {outcome, timeoutMilliseconds,
        elapsedMilliseconds: performance.now() - started}});
    };
    timer = setTimeout(() => finish({error: 'Worker did not answer within ' +
      timeoutMilliseconds + ' milliseconds'}, 'timeout'), timeoutMilliseconds);
    try {
      worker = shared ? new SharedWorker('probe-worker.js') : new Worker('probe-worker.js');
      port = shared ? worker.port : worker;
      port.onmessage = event => finish(event.data, 'response');
      worker.onerror = event => finish({error: event.message || 'Worker error'}, 'error');
      if (shared) port.start();
      port.postMessage('observe');
    } catch (error) { finish({error: String(error)}, 'error'); }
  });
}
function observeGraphics() {
  const canvas = document.createElement('canvas');
  const gl = canvas.getContext('webgl');
  if (!gl) return {available: false};
  const debug = gl.getExtension('WEBGL_debug_renderer_info');
  const result = {
    available: true, version: gl.getParameter(gl.VERSION),
    shadingLanguage: gl.getParameter(gl.SHADING_LANGUAGE_VERSION),
    vendor: gl.getParameter(gl.VENDOR), renderer: gl.getParameter(gl.RENDERER),
    unmaskedVendor: debug ? gl.getParameter(debug.UNMASKED_VENDOR_WEBGL) : null,
    unmaskedRenderer: debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : null,
    extensions: gl.getSupportedExtensions(),
    // Read before releasing this diagnostic context. Obtaining an object does
    // not mean the native graphics context is still usable.
    contextLost: gl.isContextLost(), error: gl.getError()
  };
  const release = gl.getExtension('WEBGL_lose_context');
  if (release) release.loseContext();
  return result;
}
function observeTouchConstructor() {
  // Construct but do not dispatch: distinguish DOM field conversion from
  // native HID delivery without replacing any browser method or property.
  const expected = {identifier: 17, clientX: 23, clientY: 29,
    screenX: 31, screenY: 37, pageX: 41, pageY: 43,
    radiusX: 7, radiusY: 11, rotationAngle: 13, force: 0.25};
  try {
    const touch = new Touch({...expected, target: touchButton});
    return {dispatched: false, expected,
      observed: Object.fromEntries(Object.keys(expected).map(key => [key, touch[key]]))};
  } catch (error) { return {dispatched: false, error: String(error)}; }
}
(async () => {
  const result = {capturedAt: new Date().toISOString(), main: observeNavigator()};
  result.touchConstructor = observeTouchConstructor();
  result.display = {
    width: screen.width, height: screen.height, availWidth: screen.availWidth,
    availHeight: screen.availHeight, colorDepth: screen.colorDepth,
    pixelDepth: screen.pixelDepth, devicePixelRatio,
    innerWidth, innerHeight, outerWidth, outerHeight,
    visualViewport: self.visualViewport ? {
      width: visualViewport.width, height: visualViewport.height, scale: visualViewport.scale
    } : null
  };
  try { result.webgl = observeGraphics(); } catch (error) { result.webgl = {error: String(error)}; }
  try {
    const canvas = document.getElementById('canvas');
    const context = canvas.getContext('2d');
    context.fillStyle = '#e0e9f1'; context.fillRect(0, 0, 320, 80);
    context.fillStyle = '#173d5e'; context.font = '20px Arial';
    context.fillText('tungtungtungbrowser — Safari', 8, 30);
    context.fillStyle = 'rgba(240, 90, 45, 0.65)';
    context.beginPath(); context.arc(42, 56, 16, 0, Math.PI * 2); context.fill();
    result.canvasPNG = canvas.toDataURL('image/png');
  } catch (error) { result.canvasError = String(error); }
  const timeoutMilliseconds = Number(new URL(location.href).searchParams.get('worker_timeout_ms') ?? 15000);
  if (!Number.isInteger(timeoutMilliseconds) || timeoutMilliseconds < 1000 || timeoutMilliseconds > 120000) {
    result.dedicatedWorker = result.sharedWorker = {
      error: 'worker_timeout_ms must be an integer between 1000 and 120000',
      measurement: {outcome: 'invalid_configuration'}
    };
  } else {
    [result.dedicatedWorker, result.sharedWorker] = await Promise.all([
      workerObservation(false, timeoutMilliseconds), workerObservation(true, timeoutMilliseconds)
    ]);
  }
  result.serviceWorker = {tested: false, reason: 'Separate secure-origin test required'};
  result.networkIsolation = {tested: false};
  const output = document.getElementById('output');
  output.textContent = JSON.stringify({...result, canvasPNG: result.canvasPNG ? '(saved separately)' : null}, null, 2);
  try {
    const response = await fetch('collect', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(result)});
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const saved = await response.json();
    document.getElementById('status').textContent = 'Saved observations: ' + saved.id;
  } catch (error) { document.getElementById('status').textContent = 'Save failed: ' + error; }
})();
