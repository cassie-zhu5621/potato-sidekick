/*
 * unitcams3_stream.ino — M5Stack Unit CamS3-5MP (PY260) MJPEG stream
 * =====================================================================
 * This is M5's official web_cam example (the one that actually drives the
 * PY260 sensor). It streams motion-JPEG over Wi-Fi at  http://<camera-ip>/
 *
 * ONLY thing to edit: your Wi-Fi name + password just below.
 *
 * Arduino IDE settings:
 *   Board:  esp32 -> M5UnitCAMS3
 *   Tools -> USB CDC On Boot -> Enabled
 *   Tools -> PSRAM -> OPI PSRAM
 *   (If upload fails: short G0-GND with a jumper before power-on = download mode.)
 *
 * After flashing, the Serial Monitor (115200) prints the camera's IP.
 *   - Open  http://<that-ip>/  in a browser  -> you should see live video.
 *   - Then run  sidekick_collector.py --camera http://<that-ip>  on the laptop
 *     to do stillness detection + save the dataset + the WebUI.
 */

#include <WiFi.h>
#include <ESPmDNS.h>
#include "esp_camera.h"

// ======================= EDIT THESE =======================
const char* ssid     = "cyberneticbeinglab";        // <-- set per network, then RE-FLASH
const char* password = "c1bernet1cs";    // <-- set per network, then RE-FLASH
const char* HOSTNAME = "sidekick-loop";    // loop cam = "sidekick-loop"; collector cam = "sidekick-cam"
// ==========================================================

WiFiServer server(80);
static void jpegStream(WiFiClient* client);

// Unit CamS3-5MP (PY260) pin map
#define PWDN_GPIO_NUM  -1
#define RESET_GPIO_NUM 21
#define XCLK_GPIO_NUM  11
#define SIOD_GPIO_NUM  17
#define SIOC_GPIO_NUM  41
#define Y9_GPIO_NUM    13
#define Y8_GPIO_NUM    4
#define Y7_GPIO_NUM    10
#define Y6_GPIO_NUM    5
#define Y5_GPIO_NUM    7
#define Y4_GPIO_NUM    16
#define Y3_GPIO_NUM    15
#define Y2_GPIO_NUM    6
#define VSYNC_GPIO_NUM 42
#define HREF_GPIO_NUM  18
#define PCLK_GPIO_NUM  12
#define LED_GPIO_NUM   14

void setup()
{
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
    config.frame_size   = FRAMESIZE_UXGA;    // 1600x1200 (~1.9MP) for VLM-quality frames
                                             // if it won't stream, drop to FRAMESIZE_SXGA or FRAMESIZE_HD
    config.pixel_format = PIXFORMAT_JPEG;
    config.grab_mode    = CAMERA_GRAB_WHEN_EMPTY;
    config.fb_location  = CAMERA_FB_IN_PSRAM;
    config.jpeg_quality = 10;    // lower = better quality (0..63)
    config.fb_count     = 1;

    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK) {
        Serial.printf("Camera init failed with error 0x%x", err);
        return;
    }

    sensor_t* s = esp_camera_sensor_get();
    if (s->id.PID == OV3660_PID) {
        s->set_vflip(s, 1);
        s->set_brightness(s, 1);
        s->set_saturation(s, -2);
    }

    WiFi.begin(ssid, password);
    WiFi.setSleep(false);
    Serial.print("WiFi connecting");
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }
    Serial.println("");
    Serial.println("WiFi connected");
    Serial.print("IP address: ");
    Serial.println(WiFi.localIP());   // <-- open http://<this>/ in a browser
    server.begin();

    // mDNS: reachable as http://sidekick-loop.local/ regardless of IP
    if (MDNS.begin(HOSTNAME)) {
        MDNS.addService("http", "tcp", 80);
        Serial.print("mDNS ready: http://"); Serial.print(HOSTNAME); Serial.println(".local/");
    }
}

void loop()
{
    WiFiClient client = server.available();
    if (client) {
        while (client.connected()) {
            if (client.available()) {
                jpegStream(&client);
            }
        }
        client.stop();
        Serial.println("Client Disconnected.");
    }
}

#define PART_BOUNDARY "123456789000000000000987654321"
static const char* _STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
static const char* _STREAM_BOUNDARY     = "--" PART_BOUNDARY "\r\n";
static const char* _STREAM_PART         = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

static void jpegStream(WiFiClient* client)
{
    Serial.println("Image stream start");
    client->println("HTTP/1.1 200 OK");
    client->printf("Content-Type: %s\r\n", _STREAM_CONTENT_TYPE);
    client->println("Content-Disposition: inline; filename=capture.jpg");
    client->println("Access-Control-Allow-Origin: *");
    client->println();

    static int64_t last_frame = 0;
    if (!last_frame) {
        last_frame = esp_timer_get_time();
    }

    camera_fb_t* fb;
    for (;;) {
        fb = esp_camera_fb_get();
        if (!fb) {
            delay(10);
            continue;
        }
        client->print(_STREAM_BOUNDARY);
        client->printf(_STREAM_PART, fb->len);

        int32_t to_sends    = fb->len;
        int32_t now_sends   = 0;
        uint8_t* out_buf    = fb->buf;
        uint32_t packet_len = 8 * 1024;
        while (to_sends > 0) {
            now_sends = to_sends > packet_len ? packet_len : to_sends;
            if (client->write(out_buf, now_sends) == 0) {
                goto client_exit;
            }
            out_buf  += now_sends;
            to_sends -= now_sends;
        }

        int64_t fr_end     = esp_timer_get_time();
        int64_t frame_time = fr_end - last_frame;
        last_frame         = fr_end;
        frame_time /= 1000;
        Serial.printf("MJPG: %luKB %lums (%.1ffps)\r\n",
                      (long unsigned int)(fb->len / 1024),
                      (long unsigned int)frame_time,
                      1000.0 / (long unsigned int)frame_time);
        esp_camera_fb_return(fb);
    }

client_exit:
    if (fb) {
        esp_camera_fb_return(fb);
    }
    client->stop();
    Serial.printf("Image stream end\r\n");
}
