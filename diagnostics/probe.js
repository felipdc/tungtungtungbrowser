'use strict';
document.getElementById('touch-test').addEventListener('click', async event => {
  const button = event.currentTarget;
  const observation = {
    capturedAt: new Date().toISOString(), type: event.type, isTrusted: event.isTrusted,
    clientX: event.clientX, clientY: event.clientY,
    screenX: event.screenX, screenY: event.screenY,
    pointerType: event.pointerType || null
  };
  button.textContent = 'Touch received';
  try {
    const response = await fetch('input', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(observation)});
    if (!response.ok) throw new Error('HTTP ' + response.status);
    button.textContent = 'Touch recorded';
  } catch (error) { button.textContent = 'Touch save failed: ' + error; }
});
function workerObservation(shared) {
  return new Promise(resolve => {
    let worker, port, timer;
    const finish = value => {
      clearTimeout(timer);
      if (shared && port) port.close();
      else if (worker) worker.terminate();
      resolve(value);
    };
    timer = setTimeout(() => finish({error: 'Worker did not answer within 15 seconds'}), 15000);
    try {
      worker = shared ? new SharedWorker('probe-worker.js') : new Worker('probe-worker.js');
      port = shared ? worker.port : worker;
      port.onmessage = event => finish(event.data);
      worker.onerror = event => finish({error: event.message || 'Worker error'});
      if (shared) port.start();
      port.postMessage('observe');
    } catch (error) { finish({error: String(error)}); }
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
    extensions: gl.getSupportedExtensions()
  };
  const release = gl.getExtension('WEBGL_lose_context');
  if (release) release.loseContext();
  return result;
}
(async () => {
  const result = {capturedAt: new Date().toISOString(), main: observeNavigator()};
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
  [result.dedicatedWorker, result.sharedWorker] = await Promise.all([
    workerObservation(false), workerObservation(true)
  ]);
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
