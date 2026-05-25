#pragma once

// Served at  GET /  by the camera_httpd instance.
// Stream <img> points at port 81 so a long-lived stream doesn't block /control.
// "Change WiFi" button hits /reset_wifi → ESP forgets saved creds and reboots into portal.

static const char PROGMEM INDEX_HTML[] = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>ESP32-CAM</title>
  <style>
    body { font-family: sans-serif; background:#111; color:#eee; margin:0; padding:20px; }
    h1, h2 { font-weight: 500; }
    .row { margin: 10px 0; display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
    .row label { width: 110px; }
    .row input[type=range] { flex:1; min-width:120px; }
    img { max-width: 100%; border:1px solid #444; }
    select, button { padding:6px 10px; background:#222; color:#eee; border:1px solid #444; border-radius:4px; }
    button:hover { background:#333; cursor:pointer; }
    button.danger { background:#3a1a1a; border-color:#7a3030; }
    button.danger:hover { background:#5a2424; }
  </style>
</head>
<body>
  <h1>ESP32-CAM Stream</h1>

  <div class="row">
    <label>Resolution</label>
    <select onchange="setVal('framesize', this.value)">
      <option value="13">UXGA (1600x1200)</option>
      <option value="12">SXGA (1280x1024)</option>
      <option value="11">HD (1280x720)</option>
      <option value="10">XGA (1024x768)</option>
      <option value="9">SVGA (800x600)</option>
      <option value="8" selected>VGA (640x480)</option>
      <option value="5">QVGA (320x240)</option>
      <option value="3">HQVGA (240x176)</option>
      <option value="1">QQVGA (160x120)</option>
    </select>
  </div>

  <div class="row"><label>Quality</label><input type="range" min="10" max="63" value="12" onchange="setVal('quality', this.value)"></div>
  <div class="row"><label>Brightness</label><input type="range" min="-2" max="2" value="0" onchange="setVal('brightness', this.value)"></div>
  <div class="row"><label>Contrast</label><input type="range" min="-2" max="2" value="0" onchange="setVal('contrast', this.value)"></div>
  <div class="row"><label>Saturation</label><input type="range" min="-2" max="2" value="0" onchange="setVal('saturation', this.value)"></div>

  <div class="row">
    <label>H-Mirror</label><input type="checkbox" onchange="setVal('hmirror', this.checked?1:0)">
    <label style="margin-left:20px;">V-Flip</label><input type="checkbox" onchange="setVal('vflip', this.checked?1:0)">
  </div>

  <div class="row">
    <button onclick="document.getElementById('still').src='/capture?_='+Date.now()">Snapshot</button>
    <button class="danger" onclick="resetWifi()">Change WiFi</button>
  </div>

  <h2>Live stream</h2>
  <img id="stream" src="" />
  <h2>Snapshot</h2>
  <img id="still" />

  <script>
    function setVal(v, val){ fetch('/control?var=' + v + '&val=' + val); }
    function resetWifi(){
      if (!confirm('Forget saved WiFi and reboot into setup mode?')) return;
      fetch('/reset_wifi').then(function(){
        document.body.innerHTML = '<h2>Rebooting.</h2><p>Reconnect your device to the WiFi network "ESP32CAM_Setup" to pick a new network.</p>';
      });
    }
    // Stream comes from port 81 (separate httpd instance) on the same host
    document.getElementById('stream').src = 'http://' + location.hostname + ':81/stream';
  </script>
</body>
</html>
)rawliteral";
