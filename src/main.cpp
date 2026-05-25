#include <Arduino.h>
#include <WiFi.h>
#include <WiFiManager.h>
#include <ArduinoOTA.h>
#include <ESPmDNS.h>
#include "esp_camera.h"
#include "esp_http_server.h"
#include "index_html.h"

// After boot the cam is reachable at  http://esp32cam.local/  on most networks.
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

// /reset_wifi → erase saved creds and reboot into setup portal
static esp_err_t reset_wifi_handler(httpd_req_t *req){
  Serial.println("reset_wifi requested via web UI");
  httpd_resp_set_type(req, "text/plain");
  httpd_resp_send(req, "rebooting into setup mode", HTTPD_RESP_USE_STRLEN);
  delay(500);
  WiFi.disconnect(true, true);   // disconnect + erase stored creds from NVS
  delay(200);
  ESP.restart();
  return ESP_OK;
}

void startCameraServer(){
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 80;

  httpd_uri_t index_uri      = { .uri="/",           .method=HTTP_GET, .handler=index_handler,      .user_ctx=NULL };
  httpd_uri_t capture_uri    = { .uri="/capture",    .method=HTTP_GET, .handler=capture_handler,    .user_ctx=NULL };
  httpd_uri_t control_uri    = { .uri="/control",    .method=HTTP_GET, .handler=control_handler,    .user_ctx=NULL };
  httpd_uri_t reset_wifi_uri = { .uri="/reset_wifi", .method=HTTP_GET, .handler=reset_wifi_handler, .user_ctx=NULL };

  if (httpd_start(&camera_httpd, &config) == ESP_OK){
    httpd_register_uri_handler(camera_httpd, &index_uri);
    httpd_register_uri_handler(camera_httpd, &capture_uri);
    httpd_register_uri_handler(camera_httpd, &control_uri);
    httpd_register_uri_handler(camera_httpd, &reset_wifi_uri);
  }

  // Stream gets its own port so a long-lived stream doesn't block control requests
  config.server_port = 81;
  config.ctrl_port   = 32769;
  httpd_uri_t stream_uri  = { .uri="/stream",  .method=HTTP_GET, .handler=stream_handler,  .user_ctx=NULL };
  if (httpd_start(&stream_httpd, &config) == ESP_OK){
    httpd_register_uri_handler(stream_httpd, &stream_uri);
  }
}

void setupOTA(){
  ArduinoOTA.setHostname(mdns_hostname);
  ArduinoOTA
    .onStart([](){
      esp_camera_deinit();   // free PSRAM frame buffers before flash write
      Serial.println("OTA: update starting...");
    })
    .onEnd([](){
      Serial.println("\nOTA: update complete.");
    })
    .onProgress([](unsigned int p, unsigned int t){
      Serial.printf("OTA: %u%%\r", (p * 100) / t);
    })
    .onError([](ota_error_t e){
      Serial.printf("OTA error[%u]\n", e);
    });
  ArduinoOTA.begin();
  Serial.println("OTA listener ready");
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
  if (psramFound()){
    config.frame_size   = FRAMESIZE_VGA;
    config.jpeg_quality = 12;
    config.fb_count     = 2;
    config.fb_location  = CAMERA_FB_IN_PSRAM;
    config.grab_mode    = CAMERA_GRAB_LATEST;
  } else {
    config.frame_size   = FRAMESIZE_QVGA;
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
  Serial.printf("PSRAM: %s | free PSRAM %u | free heap %u\n",
                psramFound() ? "YES" : "NO",
                (unsigned)ESP.getFreePsram(),
                (unsigned)ESP.getFreeHeap());

  // WiFiManager: tries saved creds first. If none / expired, opens AP
  // "ESP32CAM_Setup" (password "cam12345"). User joins it from phone or
  // laptop, picks their WiFi from the dropdown, types its password.
  // The new creds are saved to NVS and the ESP reconnects.
  WiFiManager wm;
  wm.setHostname(mdns_hostname);
  wm.setTitle("ESP32-CAM Setup");
  wm.setConfigPortalTimeout(180);   // 3 min idle → reboot and retry

  Serial.println("Trying saved WiFi...");
  Serial.println("If none, join AP 'ESP32CAM_Setup' (password 'cam12345') to configure.");

  if (!wm.autoConnect("ESP32CAM_Setup", "cam12345")){
    Serial.println("Portal timed out, restarting.");
    delay(1000);
    ESP.restart();
  }
  WiFi.setSleep(false);

  if (MDNS.begin(mdns_hostname)){
    MDNS.addService("http", "tcp", 80);
    Serial.printf("mDNS: http://%s.local/\n", mdns_hostname);
  }

  setupOTA();

  Serial.printf("Connected to: %s  (RSSI %d dBm)\n", WiFi.SSID().c_str(), WiFi.RSSI());
  Serial.print("Stream:       http://");
  Serial.print(WiFi.localIP());
  Serial.println("/");

  startCameraServer();
}

void loop(){
  ArduinoOTA.handle();
  delay(50);
}
