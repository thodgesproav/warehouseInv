(function () {
  'use strict';

  // Final production endpoint. The panel deliberately has no local override so
  // it cannot drift onto a retired or test deployment.
  var INVENTORY_URL = 'http://192.168.68.7:8000';
  // Replaced only in the private production archive by the build script.
  var PANEL_TOKEN = '__WAREHOUSE_PANEL_TOKEN__';
  var launchDelay = 5000;
  var launchTimer = null;
  var launchView = document.getElementById('launch-view');
  var savedUrl = document.getElementById('saved-url');

  function enableCameraStream() {
    if (!window.CrComLib || !window.CrComLib.publishEvent) { return; }
    try {
      // Camera reserved joins: full-HD resolution and stream enable on TSW-x60.
      window.CrComLib.publishEvent('n', '22900', 17);
      window.CrComLib.publishEvent('b', 'Csig.EnableStream', true);
      window.setTimeout(function () {
        window.CrComLib.publishEvent('b', 'Csig.EnableStream', false);
      }, 250);
    } catch (ignore) { /* The web-camera fallback remains available. */ }
  }

  function openInventory() {
    window.location.replace(INVENTORY_URL + '#warehouse-panel=' + PANEL_TOKEN);
  }

  document.getElementById('open-now').onclick = openInventory;

  (function initialise() {
    enableCameraStream();
    window.setTimeout(enableCameraStream, 1000);
    savedUrl.appendChild(document.createTextNode(INVENTORY_URL));
    launchView.className = '';
    launchTimer = window.setTimeout(openInventory, launchDelay);
  }());
}());
