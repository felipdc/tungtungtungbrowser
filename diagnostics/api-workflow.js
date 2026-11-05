/* This script belongs to the served test page. It is never injected into a page. */
"use strict";
const state = document.getElementById("state");
const events = [];
for (const type of ["pointerdown", "pointerup", "touchstart", "touchend", "click"]) {
  document.addEventListener(type, event => {
    events.push({type, target: event.target.id, trusted: event.isTrusted,
      pointerType: event.pointerType || null});
    document.getElementById("events").textContent = JSON.stringify(events.slice(-20));
  }, {passive: true});
}
document.getElementById("load-data").addEventListener("click", () => {
  const request = new XMLHttpRequest();
  request.open("GET", "api-fixture-data.json");
  request.onload = () => {
    document.getElementById("result").textContent = request.responseText;
    state.textContent = "JSON response received";
    state.dataset.response = String(request.status);
  };
  request.onerror = () => { state.textContent = "JSON request failed"; };
  request.send();
});
document.getElementById("record-click").addEventListener("click", () => {
  state.textContent = "Second click received";
  state.dataset.clicked = "true";
});
state.textContent = new URLSearchParams(location.search).get("page") === "2"
  ? "Second document ready" : "Rendered document ready";
state.dataset.ready = "true";
setTimeout(() => {
  const marker = document.createElement("p");
  marker.id = "delayed-marker";
  marker.textContent = "Delayed DOM condition reached";
  state.after(marker);
}, 250);
