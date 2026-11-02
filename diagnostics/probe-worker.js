importScripts('probe-common.js');
self.onmessage = () => self.postMessage(observeNavigator());
self.onconnect = event => {
  const port = event.ports[0];
  port.onmessage = () => port.postMessage(observeNavigator());
  port.start();
};
