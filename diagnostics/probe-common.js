/* Observations only: no browser properties or API implementations are changed. */
function observeNavigator() {
  const result = {};
  for (const key of ['userAgent', 'platform', 'vendor', 'language', 'languages',
                     'hardwareConcurrency', 'deviceMemory', 'maxTouchPoints', 'webdriver']) {
    try { result[key] = navigator[key] === undefined ? null : navigator[key]; }
    catch (error) { result[key] = {error: String(error)}; }
  }
  result.timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  result.timezoneOffsetMinutes = new Date().getTimezoneOffset();
  result.secureContext = self.isSecureContext;
  return result;
}
