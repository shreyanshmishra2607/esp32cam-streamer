#include <Arduino.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include "esp_camera.h"
#include "esp_http_server.h"

// ===== WiFi credentials — FILL THESE IN before flashing =====
const char* ssid     = "Redmi Note 14 5G";
const char* password = "12345678";

// ===== Static IP config — pins the ESP to one address so it never changes =====
// Adjust if your router's subnet is different. Your DHCP-assigned IPs were 192.168.1.x,
// so gateway is almost certainly 192.168.1.1. Pick a host number outside your router's
// DHCP pool (most home routers hand out .100–.200, so .220 is usually safe).
IPAddress local_IP(192, 168, 1, 220);
IPAddress gateway (192, 168, 1, 1);
IPAddress subnet  (255, 255, 255, 0);
IPAddress dns1    (8, 8, 8, 8);

// mDNS name — after boot, you can also reach the cam at  http://esp32cam.local/
const char* mdns_hostname = "esp32cam";

// ===== AI-Thinker ESP32-CAM pin map (OV2640) =====
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

#define PART_BOUNDARY "123456789000000000000987654321"
static const char* _STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
static const char* _STREAM_BOUNDARY     = "\r\n--" PART_BOUNDARY "\r\n";
static const char* _STREAM_PART         = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

httpd_handle_t camera_httpd = NULL;
httpd_handle_t stream_httpd = NULL;

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
    .row { margin: 10px 0; display:flex; align-items:center; gap:10px; }
    .row label { width: 110px; }
    .row input[type=range] { flex:1; }
    img { max-width: 100%; border:1px solid #444; }
    select, button { padding:6px 10px; background:#222; color:#eee; border:1px solid #444; }
    button:hover { background:#333; cursor:pointer; }
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
  </div>

  <h2>Live stream</h2>
  <img id="stream" src="" />
  <h2>Snapshot</h2>
  <img id="still" />

  <script>
    function setVal(v, val){ fetch('/control?var=' + v + '&val=' + val); }
    // Point the stream at port 81 (separate httpd instance) using the same host
    document.getElementById('stream').src = 'http://' + location.hostname + ':81/stream';
  </script>
</body>
</html>
)rawliteral";

static esp_err_t index_handler(httpd_req_t *req){
  httpd_resp_set_type(req, "text/html");
  return httpd_resp_send(req, (const char*)INDEX_HTML, strlen(INDEX_HTML));
}

static esp_err_t capture_handler(httpd_req_t *req){
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb){
    httpd_resp_send_500(req);
    return ESP_FAIL;
  }
  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Content-Disposition", "inline; filename=capture.jpg");
  esp_err_t res = httpd_resp_send(req, (const char *)fb->buf, fb->len);
  esp_camera_fb_return(fb);
  return res;
}

static esp_err_t stream_handler(httpd_req_t *req){
  camera_fb_t *fb = NULL;
  esp_err_t res = ESP_OK;
  char part_buf[64];

  res = httpd_resp_set_type(req, _STREAM_CONTENT_TYPE);
  if (res != ESP_OK) return res;

  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

  while (true){
    fb = esp_camera_fb_get();
    if (!fb){ res = ESP_FAIL; break; }

    size_t hlen = snprintf(part_buf, sizeof(part_buf), _STREAM_PART, fb->len);
    res = httpd_resp_send_chunk(req, _STREAM_BOUNDARY, strlen(_STREAM_BOUNDARY));
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, part_buf, hlen);
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, (const char *)fb->buf, fb->len);

    esp_camera_fb_return(fb);
    if (res != ESP_OK) break;
  }
  return res;
}

static esp_err_t control_handler(httpd_req_t *req){
  char query[64];
  char var[16] = {0};
  char val[16] = {0};

  if (httpd_req_get_url_query_str(req, query, sizeof(query)) != ESP_OK){
    httpd_resp_send_404(req);
    return ESP_FAIL;
  }
  httpd_query_key_value(query, "var", var, sizeof(var));
  httpd_query_key_value(query, "val", val, sizeof(val));

  int v = atoi(val);
  sensor_t *s = esp_camera_sensor_get();
  if (!s){ httpd_resp_send_500(req); return ESP_FAIL; }

  int res = -1;
  if      (!strcmp(var, "framesize"))  res = s->set_framesize(s, (framesize_t)v);
  else if (!strcmp(var, "quality"))    res = s->set_quality(s, v);
  else if (!strcmp(var, "brightness")) res = s->set_brightness(s, v);
  else if (!strcmp(var, "contrast"))   res = s->set_contrast(s, v);
  else if (!strcmp(var, "saturation")) res = s->set_saturation(s, v);
  else if (!strcmp(var, "hmirror"))    res = s->set_hmirror(s, v);
  else if (!strcmp(var, "vflip"))      res = s->set_vflip(s, v);

  if (res < 0){ httpd_resp_send_404(req); return ESP_FAIL; }

  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  return httpd_resp_send(req, NULL, 0);
}

void startCameraServer(){
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 80;

  httpd_uri_t index_uri   = { .uri="/",        .method=HTTP_GET, .handler=index_handler,   .user_ctx=NULL };
  httpd_uri_t capture_uri = { .uri="/capture", .method=HTTP_GET, .handler=capture_handler, .user_ctx=NULL };
  httpd_uri_t control_uri = { .uri="/control", .method=HTTP_GET, .handler=control_handler, .user_ctx=NULL };

  if (httpd_start(&camera_httpd, &config) == ESP_OK){
    httpd_register_uri_handler(camera_httpd, &index_uri);
    httpd_register_uri_handler(camera_httpd, &capture_uri);
    httpd_register_uri_handler(camera_httpd, &control_uri);
  }

  // Stream gets its own port so a long-lived stream doesn't block control requests
  config.server_port = 81;
  config.ctrl_port   = 32769;
  httpd_uri_t stream_uri  = { .uri="/stream",  .method=HTTP_GET, .handler=stream_handler,  .user_ctx=NULL };
  if (httpd_start(&stream_httpd, &config) == ESP_OK){
    httpd_register_uri_handler(stream_httpd, &stream_uri);
  }
}

void setup(){
  Serial.begin(115200);
  Serial.setDebugOutput(true);
  Serial.println();

  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0       = Y2_GPIO_NUM;
  config.pin_d1       = Y3_GPIO_NUM;
  config.pin_d2       = Y4_GPIO_NUM;
  config.pin_d3       = Y5_GPIO_NUM;
  config.pin_d4       = Y6_GPIO_NUM;
  config.pin_d5       = Y7_GPIO_NUM;
  config.pin_d6       = Y8_GPIO_NUM;
  config.pin_d7       = Y9_GPIO_NUM;
  config.pin_xclk     = XCLK_GPIO_NUM;
  config.pin_pclk     = PCLK_GPIO_NUM;
  config.pin_vsync    = VSYNC_GPIO_NUM;
  config.pin_href     = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn     = PWDN_GPIO_NUM;
  config.pin_reset    = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  // PSRAM → big frame + double-buffer (smooth).  No PSRAM → tiny frame, single buffer.
  if (psramFound()){
    config.frame_size   = FRAMESIZE_VGA;   // 640x480, the smooth sweet spot
    config.jpeg_quality = 12;
    config.fb_count     = 2;
    config.fb_location  = CAMERA_FB_IN_PSRAM;
    config.grab_mode    = CAMERA_GRAB_LATEST;
  } else {
    config.frame_size   = FRAMESIZE_QVGA;  // 320x240, all DRAM can fit
    config.jpeg_quality = 15;
    config.fb_count     = 1;
    config.fb_location  = CAMERA_FB_IN_DRAM;
    config.grab_mode    = CAMERA_GRAB_WHEN_EMPTY;
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK){
    Serial.printf("Camera init failed: 0x%x\n", err);
    return;
  }

  Serial.printf("PSRAM found: %s\n", psramFound() ? "YES" : "NO");
  Serial.printf("Free PSRAM:  %u bytes\n", (unsigned)ESP.getFreePsram());
  Serial.printf("Free heap:   %u bytes\n", (unsigned)ESP.getFreeHeap());

  // Static IP disabled while on phone hotspot — re-enable when back on a known router.
  // if (!WiFi.config(local_IP, gateway, subnet, dns1)){
  //   Serial.println("Static IP config failed; falling back to DHCP.");
  // }
  WiFi.begin(ssid, password);
  WiFi.setSleep(false);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED){
    delay(500);
    Serial.print(".");
  }
  Serial.println();

  if (MDNS.begin(mdns_hostname)){
    MDNS.addService("http", "tcp", 80);
    Serial.printf("mDNS started:  http://%s.local/\n", mdns_hostname);
  } else {
    Serial.println("mDNS start failed.");
  }

  Serial.print("WiFi connected. Open http://");
  Serial.print(WiFi.localIP());
  Serial.println("/  in your browser.");

  startCameraServer();
}

void loop(){
  delay(10000);
}
