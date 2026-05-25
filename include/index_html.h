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
    .stats { display:grid; grid-template-columns:max-content 1fr; column-gap:16px; row-gap:6px; background:#1a1a1a; padding:14px 18px; border:1px solid #333; border-radius:6px; font-family:ui-monospace,Menlo,Consolas,monospace; font-size:13px; }
    .stats .k { color:#888; }
    .stats .v { color:#eee; }
    .pill { display:inline-block; padding:1px 8px; border-radius:10px; font-size:12px; }
    .pill.good { background:#1a3a1a; color:#7adb7a; }
    .pill.ok   { background:#3a3a1a; color:#dbd97a; }
    .pill.bad  { background:#3a1a1a; color:#db7a7a; }
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

  <h2>Device stats</h2>
  <div id="stats" class="stats">Loading&hellip;</div>

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

    function fmtKB(b){ return (b/1024).toFixed(0) + ' KB'; }
    function rssiPill(r){
      var cls = r >= -60 ? 'good' : (r >= -75 ? 'ok' : 'bad');
      var label = r >= -50 ? 'excellent' : (r >= -60 ? 'good' : (r >= -70 ? 'fair' : 'weak'));
      return '<span class="pill ' + cls + '">' + r + ' dBm &middot; ' + label + '</span>';
    }
    function tempPill(t){
      var cls = t < 60 ? 'good' : (t < 75 ? 'ok' : 'bad');
      return '<span class="pill ' + cls + '">' + t.toFixed(1) + ' &deg;C</span>';
    }
    function fmtUptime(s){
      var d = Math.floor(s/86400), h = Math.floor((s%86400)/3600), m = Math.floor((s%3600)/60), x = s%60;
      if (d) return d+'d '+h+'h '+m+'m';
      if (h) return h+'h '+m+'m';
      if (m) return m+'m '+x+'s';
      return x+'s';
    }
    function rows(o){
      var html = '';
      for (var i=0; i<o.length; i++){
        html += '<div class="k">' + o[i][0] + '</div><div class="v">' + o[i][1] + '</div>';
      }
      return html;
    }
    function loadStats(){
      fetch('/stats', { cache:'no-store' })
        .then(function(r){ return r.json(); })
        .then(function(s){
          document.getElementById('stats').innerHTML = rows([
            ['WiFi',       s.wifi_ssid],
            ['Signal',     rssiPill(s.wifi_rssi)],
            ['IP',         s.ip],
            ['MAC',        s.mac],
            ['Chip',       s.chip + ' &middot; ' + s.cores + ' cores @ ' + s.cpu_mhz + ' MHz'],
            ['Temperature', tempPill(s.temp_c)],
            ['Free heap',  fmtKB(s.free_heap)],
            ['Free PSRAM', fmtKB(s.free_psram)],
            ['Uptime',     fmtUptime(s.uptime_s)]
          ]);
        })
        .catch(function(){
          document.getElementById('stats').innerHTML = '<span class="k">stats unavailable</span>';
        });
    }
    loadStats();
    setInterval(loadStats, 2000);
  </script>
</body>
</html>
)rawliteral";
