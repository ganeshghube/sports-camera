"""
Cricket ball tracker: PHONE -> RTSP -> PC -> YOLO/OpenVINO -> (ESP32 pan/tilt)
                                                         and -> WebSocket -> web app

Detection / tracking / pan-tilt logic is carried over from the original script.
New: env configuration, AUTO/GPU/CPU device, independent detection/display
resolution, SHOW_DEBUG_WINDOW, camera auto-reconnect, and a failure-tolerant
WebSocket publisher (ball_detection / status) with remote commands.

The tracker NEVER changes the official score; it only publishes detections.
"""

import json
import os
import queue
import threading
import time
from pathlib import Path

import cv2
import numpy as np

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _env(name, default, cast=str):
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    if cast is bool:
        return v.strip().lower() in ("1", "true", "yes", "on")
    return cast(v)


RUNTIME_CONFIG = Path(os.environ.get("TRACKER_CONFIG_FILE", "data/tracker_config.json"))


def runtime_config():
    cfg = {}
    try:
        if RUNTIME_CONFIG.exists():
            cfg = json.loads(RUNTIME_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    cfg.setdefault("camera_type", "network")
    cfg.setdefault("camera_device", 0)
    cfg.setdefault("camera_name", "")
    cfg.setdefault("rtsp_url", RTSP_URL)
    cfg.setdefault("resolution", "1920x1080")
    cfg.setdefault("fps", 60)
    cfg.setdefault("ai_device", DEVICE)
    cfg.setdefault("model_path", MODEL_PATH)
    cfg.setdefault("auto_camera_track", True)
    cfg.setdefault("min_track_confidence", 0.35)
    cfg.setdefault("ai_max_fps", 20)
    cfg.setdefault("motor_driver", MOTOR_DRIVER)
    cfg.setdefault("pan_step_pin", PAN_STEP_PIN)
    cfg.setdefault("pan_dir_pin", PAN_DIR_PIN)
    cfg.setdefault("tilt_step_pin", TILT_STEP_PIN)
    cfg.setdefault("tilt_dir_pin", TILT_DIR_PIN)
    return cfg


# ------------------------------------------------------------ CONFIGURATION
RTSP_URL = _env("RTSP_URL", "rtsp://192.168.1.219:8080/h264.sdp")
MODEL_PATH = _env("MODEL_PATH", "models/best.pt")
DEVICE = _env("DEVICE", "AUTO").upper()            # AUTO | GPU | CPU
CONFIDENCE = _env("CONFIDENCE", 0.25, float)
INPUT_SIZE = _env("INPUT_SIZE", 640, int)          # detection resolution
BALL_CLASS_ID = _env("BALL_CLASS_ID", 0, int)
INFERENCE_EVERY_N_FRAMES = _env("INFERENCE_EVERY_N_FRAMES", 1, int)
AI_MAX_FPS = _env("AI_MAX_FPS", 20, int)
MIN_TRACK_CONFIDENCE = _env("MIN_TRACK_CONFIDENCE", 0.35, float)
AUTO_CAMERA_TRACK = _env("AUTO_CAMERA_TRACK", True, bool)

SHOW_DEBUG_WINDOW = _env("SHOW_DEBUG_WINDOW", False, bool)   # local window on/off
SHOW_DEBUG_OVERLAY = _env("SHOW_DEBUG_OVERLAY", False, bool)  # extra graphics in window
DISPLAY_WIDTH = _env("DISPLAY_WIDTH", 0, int)      # 0 = native; display only
FULLSCREEN = _env("FULLSCREEN", True, bool)
WINDOW_NAME = "Cricket Phone Ball Tracker"

MAX_LOST_FRAMES = _env("MAX_LOST_FRAMES", 8, int)
TARGET_X = _env("TARGET_X", 0.50, float)
TARGET_Y = _env("TARGET_Y", 0.50, float)
DEAD_ZONE_X = _env("DEAD_ZONE_X", 0.10, float)
DEAD_ZONE_Y = _env("DEAD_ZONE_Y", 0.10, float)
MAX_PAN_SPEED = _env("MAX_PAN_SPEED", 100, int)
MAX_TILT_SPEED = _env("MAX_TILT_SPEED", 80, int)
PAN_GAIN = _env("PAN_GAIN", 140, int)
TILT_GAIN = _env("TILT_GAIN", 110, int)
CONTROL_INTERVAL = _env("CONTROL_INTERVAL", 0.03, float)
PAN_REVERSE = _env("PAN_REVERSE", False, bool)
TILT_REVERSE = _env("TILT_REVERSE", True, bool)
TRACK_SMOOTHING = _env("TRACK_SMOOTHING", 0.35, float)
PREDICTION_SECONDS = _env("PREDICTION_SECONDS", 0.06, float)
MAX_PREDICTION_PX = _env("MAX_PREDICTION_PX", 220, float)

SERIAL_PORT = _env("SERIAL_PORT", "COM5")
SERIAL_BAUDRATE = _env("SERIAL_BAUDRATE", 115200, int)
USE_SERIAL = _env("USE_SERIAL", False, bool)        # legacy ESP32 mode
MOTOR_DRIVER = _env("MOTOR_DRIVER", "none").lower()  # none | serial | gpio
PAN_STEP_PIN = _env("PAN_STEP_PIN", 17, int)
PAN_DIR_PIN = _env("PAN_DIR_PIN", 27, int)
TILT_STEP_PIN = _env("TILT_STEP_PIN", 22, int)
TILT_DIR_PIN = _env("TILT_DIR_PIN", 23, int)
GPIO_MAX_PAN_SPS = _env("GPIO_MAX_PAN_SPS", 700, int)
GPIO_MAX_TILT_SPS = _env("GPIO_MAX_TILT_SPS", 550, int)

BACKEND_WS_URL = _env("BACKEND_WS_URL", "ws://127.0.0.1:8000/ws/ball-tracking")
PUBLISH_INTERVAL = _env("PUBLISH_INTERVAL", 0.05, float)  # up to 20 ball_detection updates/sec

# Live preview frames for the web UI (reuses the already-decoded frame)
PREVIEW_STREAM = _env("PREVIEW_STREAM", True, bool)
PREVIEW_WIDTH = _env("PREVIEW_WIDTH", 1280, int)
PREVIEW_FPS = _env("PREVIEW_FPS", 20, int)
PREVIEW_QUALITY = _env("PREVIEW_QUALITY", 72, int)
RPI_CAMERA_BACKEND = _env("RPI_CAMERA_BACKEND", "auto").lower()  # auto | picamera2 | opencv


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------ SERIAL / ESP32
class PanTiltController:
    def __init__(self, port, baudrate, enabled=True):
        self.serial = None
        self.last_command_time = 0
        self.last_pan = 0
        self.last_tilt = 0
        if not enabled:
            log("Serial control disabled")
            return
        try:
            import serial
            log(f"Connecting to ESP32 {port} @ {baudrate}")
            self.serial = serial.Serial(port, baudrate, timeout=0.05)
            time.sleep(2)
            log("ESP32 connected")
        except Exception as e:
            log(f"WARNING: ESP32 unavailable ({e}); continuing without motors")
            self.serial = None

    def send(self, pan, tilt):
        now = time.perf_counter()
        if now - self.last_command_time < CONTROL_INTERVAL:
            return
        pan = int(max(-MAX_PAN_SPEED, min(MAX_PAN_SPEED, pan)))
        tilt = int(max(-MAX_TILT_SPEED, min(MAX_TILT_SPEED, tilt)))
        if pan == self.last_pan and tilt == self.last_tilt:
            return
        self.last_pan, self.last_tilt, self.last_command_time = pan, tilt, now
        if self.serial is not None:
            try:
                self.serial.write(f"PAN:{pan},TILT:{tilt}\n".encode("ascii"))
            except Exception as e:
                log(f"Serial error: {e}")

    def stop(self):
        self.send(0, 0)

    def close(self):
        try:
            self.stop()
            if self.serial is not None:
                self.serial.close()
        except Exception:
            pass


# ------------------------------------------------------------ MODEL
class Detector:
    """OpenVINO YOLO detector with AUTO/GPU/CPU device selection."""

    def __init__(self):
        self.compiled = None
        self.device_used = None
        self.error = None
        self.ov_version = None

    @property
    def ready(self):
        return self.compiled is not None

    def load(self, model_path=None):
        self.compiled, self.error = None, None
        model_path = model_path or MODEL_PATH
        try:
            import openvino as ov
        except Exception as e:
            self.error = f"OpenVINO unavailable: {e}"
            return False
        self.ov_version = ov.__version__
        core = ov.Core()
        available = core.available_devices
        log(f"OpenVINO {ov.__version__}, devices: {available}")

        if DEVICE == "GPU":
            if "GPU" not in available:
                self.error = "DEVICE=GPU but no Intel GPU available"
                return False
            device = "GPU"
        elif DEVICE == "CPU":
            device = "CPU"
        else:
            device = "GPU" if "GPU" in available else "CPU"

        if not os.path.exists(model_path):
            self.error = f"AI model missing: {model_path}"
            return False

        stem = os.path.splitext(model_path)[0]
        xml_file = os.path.join(stem + "_openvino_model",
                                os.path.basename(stem) + ".xml")
        try:
            if not os.path.exists(xml_file):
                log("OpenVINO model not found; exporting from .pt ...")
                from ultralytics import YOLO
                YOLO(model_path).export(format="openvino", imgsz=INPUT_SIZE)
            self.compiled = core.compile_model(core.read_model(xml_file), device)
            self.device_used = device
            log(f"Model loaded on {device}")
            return True
        except Exception as e:
            self.error = f"Model load failed: {e}"
            self.compiled = None
            return False

    def detect(self, frame):
        h, w = frame.shape[:2]
        img = cv2.resize(frame, (INPUT_SIZE, INPUT_SIZE))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = np.expand_dims(
            np.transpose(img.astype(np.float32) / 255.0, (2, 0, 1)), 0)
        out = np.squeeze(self.compiled([img])[self.compiled.outputs[0]])
        if out.ndim != 2:
            return []
        if out.shape[0] < out.shape[1]:
            out = out.T
        sx, sy = w / INPUT_SIZE, h / INPUT_SIZE
        dets = []
        for d in out:
            if len(d) < 5:
                continue
            conf = float(d[4])
            if conf < CONFIDENCE:
                continue
            xc, yc, bw, bh = (float(v) for v in d[:4])
            x1 = max(0, min(w - 1, int((xc - bw / 2) * sx)))
            y1 = max(0, min(h - 1, int((yc - bh / 2) * sy)))
            x2 = max(0, min(w - 1, int((xc + bw / 2) * sx)))
            y2 = max(0, min(h - 1, int((yc + bh / 2) * sy)))
            if x2 <= x1 or y2 <= y1:
                continue
            dets.append({"box": (x1, y1, x2, y2), "confidence": conf})
        return dets


def select_best_ball(detections):
    if not detections:
        return None
    best = max(detections, key=lambda x: x["confidence"])
    x1, y1, x2, y2 = best["box"]
    return {"x": (x1 + x2) / 2.0, "y": (y1 + y2) / 2.0,
            "confidence": best["confidence"], "box": best["box"]}


# ------------------------------------------------------------ TRACKER
class BallTracker:
    """Low-latency ball state with smoothing and short-horizon prediction.

    Detection remains the source of truth. Smoothing only reduces camera/model
    jitter; prediction is intentionally capped so a missed ball cannot make the
    pan/tilt system jump across the frame.
    """
    def __init__(self):
        self.reset()

    def reset(self):
        self.previous_x = self.previous_y = self.previous_time = None
        self.velocity_x = self.velocity_y = 0.0
        self.filtered_x = self.filtered_y = None
        self.last_seen = 0

    def update(self, ball):
        now = time.perf_counter()
        if ball is None:
            self.last_seen += 1
            return None
        x, y = float(ball["x"]), float(ball["y"])
        if self.filtered_x is None:
            fx, fy = x, y
        else:
            a = max(0.05, min(0.95, TRACK_SMOOTHING))
            fx = self.filtered_x + a * (x - self.filtered_x)
            fy = self.filtered_y + a * (y - self.filtered_y)
        if self.previous_x is not None and self.previous_time is not None:
            dt = now - self.previous_time
            if dt > 0.001:
                vx = (fx - self.filtered_x if self.filtered_x is not None else 0.0) / dt
                vy = (fy - self.filtered_y if self.filtered_y is not None else 0.0) / dt
                self.velocity_x = 0.55 * self.velocity_x + 0.45 * vx
                self.velocity_y = 0.55 * self.velocity_y + 0.45 * vy
        self.filtered_x, self.filtered_y = fx, fy
        self.previous_x, self.previous_y, self.previous_time = fx, fy, now
        self.last_seen = 0
        return {**ball, "x": fx, "y": fy}

    def predict(self, t=None):
        if self.filtered_x is None:
            return None
        horizon = PREDICTION_SECONDS if t is None else float(t)
        dx = max(-MAX_PREDICTION_PX, min(MAX_PREDICTION_PX, self.velocity_x * horizon))
        dy = max(-MAX_PREDICTION_PX, min(MAX_PREDICTION_PX, self.velocity_y * horizon))
        return (self.filtered_x + dx, self.filtered_y + dy)


def calculate_pan_tilt(bx, by, fw, fh):
    ex = bx / fw - TARGET_X
    ey = by / fh - TARGET_Y
    pan = 0 if abs(ex) < DEAD_ZONE_X else int(ex * PAN_GAIN)
    tilt = 0 if abs(ey) < DEAD_ZONE_Y else int(ey * TILT_GAIN)
    pan = max(-MAX_PAN_SPEED, min(MAX_PAN_SPEED, pan))
    tilt = max(-MAX_TILT_SPEED, min(MAX_TILT_SPEED, tilt))
    if PAN_REVERSE:
        pan = -pan
    if TILT_REVERSE:
        tilt = -tilt
    return pan, tilt


# ------------------------------------------------------------ CAMERA
class CameraReader:
    """Latest-frame reader with automatic reconnect for network/USB/Pi cameras."""

    def __init__(self, config):
        self.config = dict(config or {})
        self.cap = None
        self.frame = None
        self.frame_time = 0.0
        self.frame_id = 0
        self.lock = threading.Lock()
        self.running = False
        self.thread = None
        self.connected = False
        self.error = None
        self._restart = threading.Event()

    def update_config(self, config):
        new_config = dict(config or self.config)
        old = dict(self.config)
        self.config = new_config
        keys = ("camera_type", "camera_device", "camera_name", "rtsp_url", "resolution", "fps")
        if self.running and any(old.get(k) != new_config.get(k) for k in keys):
            self._restart.set()

    def start(self):
        if self.running:
            return self
        self.running = True
        self.thread = threading.Thread(target=self._reader, daemon=True, name="ai-camera-reader")
        self.thread.start()
        return self

    def restart(self):
        self._restart.set()

    @staticmethod
    def _parse_resolution(value, default=(1280, 720)):
        try:
            raw = str(value or "").lower().strip()
            w, h = [int(x) for x in raw.split("x", 1)]
            if w > 0 and h > 0:
                return w, h
        except Exception:
            pass
        return default

    @staticmethod
    def _release_capture(cap):
        if cap is None:
            return
        try:
            if hasattr(cap, "stop"):
                cap.stop()
            if hasattr(cap, "close"):
                cap.close()
            elif hasattr(cap, "release"):
                cap.release()
        except Exception:
            pass

    def _read_test_frames(self, cap, count=4, timeout=2.0):
        """Require several genuine frames before accepting a capture device."""
        good = 0
        deadline = time.monotonic() + timeout
        while good < count and time.monotonic() < deadline:
            try:
                ok, frame = cap.read()
            except Exception:
                ok, frame = False, None
            if ok and frame is not None and getattr(frame, "size", 0):
                if getattr(frame, "ndim", 0) >= 2 and frame.shape[0] > 0 and frame.shape[1] > 0:
                    good += 1
                    continue
            time.sleep(0.03)
        return good >= count

    def _open_windows_usb(self, target, cfg):
        """Open a real Windows camera using a negotiated, actually working mode.

        Some webcams advertise a 1080p60 mode but fail when OpenCV selects it.
        We therefore test the requested mode plus safe fallbacks and only return
        a capture after multiple real frames have been read successfully.
        """
        preferred = target if isinstance(target, int) else 0
        indices = [preferred] + [i for i in range(6) if i != preferred]
        req_w, req_h = self._parse_resolution(cfg.get("resolution"), (1280, 720))
        req_fps = max(1, int(cfg.get("fps") or 30))
        modes = []
        for mode in ((req_w, req_h, req_fps),
                     (1920, 1080, 30),
                     (1280, 720, 30),
                     (1280, 720, 60),
                     (640, 480, 30)):
            if mode not in modes:
                modes.append(mode)
        backends = [
            ("DirectShow", getattr(cv2, "CAP_DSHOW", cv2.CAP_ANY)),
            ("Media Foundation", getattr(cv2, "CAP_MSMF", cv2.CAP_ANY)),
            ("OpenCV default", cv2.CAP_ANY),
        ]

        for idx in indices:
            for backend_name, backend in backends:
                cap = None
                try:
                    cap = cv2.VideoCapture(idx, backend)
                    if not cap.isOpened():
                        self._release_capture(cap)
                        continue
                    try:
                        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    except Exception:
                        pass
                    for w, h, fps in modes:
                        try:
                            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
                            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
                            # Do not force 60fps unless the camera proves it can stream.
                            cap.set(cv2.CAP_PROP_FPS, fps)
                        except Exception:
                            pass
                        if self._read_test_frames(cap, count=4, timeout=2.0):
                            actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                            actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                            actual_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
                            log(f"USB camera selected: device {idx} via {backend_name} "
                                f"({actual_w}x{actual_h}@{actual_fps:.1f}fps)")
                            return cap
                    self._release_capture(cap)
                except Exception:
                    self._release_capture(cap)
        log("USB camera auto-detect failed: no Windows camera returned stable frames")
        return None

    def _open(self):
        cfg = self.config
        ctype = str(cfg.get("camera_type") or "network").lower()
        if ctype in ("usb", "rpi", "raspberry_pi"):
            device = cfg.get("camera_device", 0)
            res = str(cfg.get("resolution") or "1280x720")
            w, h = self._parse_resolution(res)
            if ctype in ("rpi", "raspberry_pi") and os.name != "nt" and RPI_CAMERA_BACKEND in ("auto", "picamera2"):
                try:
                    from picamera2 import Picamera2
                    picam = Picamera2()
                    config = picam.create_video_configuration(
                        main={"size": (w, h), "format": "RGB888"},
                        controls={"FrameRate": int(cfg.get("fps") or 30)}
                    )
                    picam.configure(config)
                    picam.start()
                    time.sleep(0.25)
                    log(f"Opening Raspberry Pi camera with Picamera2: {w}x{h}")
                    return picam
                except Exception as exc:
                    if RPI_CAMERA_BACKEND == "picamera2":
                        log(f"Picamera2 failed: {exc}")
                        return None
                    log(f"Picamera2 unavailable; falling back to V4L2: {exc}")
            if ctype == "rpi" and isinstance(device, str) and str(device).startswith("/dev/video"):
                target = device
            elif ctype == "rpi" and os.name != "nt":
                target = f"/dev/video{int(device or 0)}"
            else:
                raw = str(device).strip() if device is not None else ""
                try:
                    target = int(raw) if raw.lower() not in ("", "auto", "detect", "automatic") else None
                except (ValueError, TypeError):
                    target = None

            if os.name == "nt" and ctype == "usb":
                return self._open_windows_usb(target, cfg)

            backend = cv2.CAP_V4L2 if os.name != "nt" else cv2.CAP_DSHOW
            log(f"Opening {ctype} camera: {target if target is not None else 'auto'}")
            cap = cv2.VideoCapture(target if target is not None else 0, backend)
            if not cap.isOpened() and os.name == "nt" and isinstance(target, int):
                for fallback in (getattr(cv2, "CAP_MSMF", cv2.CAP_ANY), cv2.CAP_ANY):
                    self._release_capture(cap)
                    cap = cv2.VideoCapture(target, fallback)
                    if cap.isOpened():
                        break

        else:
            url = str(cfg.get("rtsp_url") or RTSP_URL).strip()
            log(f"Opening network camera: {url}")
            # OpenCV/FFmpeg has two timeout mechanisms depending on the build:
            # capture properties and OPENCV_FFMPEG_CAPTURE_OPTIONS.  Configure
            # both so an unavailable phone cannot sit in FFmpeg's default
            # ~30-second socket timeout.  TCP is preferred because it is much
            # more reliable than UDP for a Wi-Fi phone camera.
            ffmpeg_opts = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS", "")
            wanted = "rtsp_transport;tcp|stimeout;5000000|rw_timeout;5000000"
            if "rtsp_transport;" not in ffmpeg_opts:
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = wanted
            params = []
            for name, value in (("CAP_PROP_OPEN_TIMEOUT_MSEC", 5000),
                                ("CAP_PROP_READ_TIMEOUT_MSEC", 5000)):
                prop = getattr(cv2, name, None)
                if prop is not None:
                    params.extend([prop, value])
            cap = None
            try:
                cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG, params) if params else cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            except Exception as exc:
                log(f"RTSP FFmpeg open failed: {exc}")
            if (cap is None or not cap.isOpened()) and url:
                # Some OpenCV builds ignore explicit FFmpeg parameters but can
                # still open the stream through CAP_ANY.
                self._release_capture(cap)
                try:
                    cap = cv2.VideoCapture(url, cv2.CAP_ANY)
                except Exception as exc:
                    log(f"RTSP fallback open failed: {exc}")
                    cap = None

        if cap is None or hasattr(cap, "capture_array"):
            return cap
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            # For network streams, do not force width/height/fps: the phone's
            # RTSP encoder owns those parameters. For non-Windows USB/V4L2,
            # request the configured mode but accept whatever the driver gives.
            if ctype != "network":
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
                cap.set(cv2.CAP_PROP_FPS, int(cfg.get("fps") or 30))
        except Exception:
            pass
        if not cap.isOpened():
            self._release_capture(cap)
            return None
        # For RTSP, opening the URL is not enough: FFmpeg can report an open
        # capture before the first decoded packet arrives. Validate real frames
        # here so the status cannot say CONNECTED for a dead stream.
        if ctype == "network" and not self._read_test_frames(cap, count=2, timeout=5.5):
            log("RTSP camera opened but produced no frames within 5.5s")
            self._release_capture(cap)
            return None
        return cap

    def _reader(self):
        fails = 0
        while self.running:
            if self.cap is None or self._restart.is_set():
                self._restart.clear()
                if self.cap is not None:
                    try:
                        if hasattr(self.cap, "stop"):
                            self.cap.stop()
                        if hasattr(self.cap, "close"):
                            self.cap.close()
                        elif hasattr(self.cap, "release"):
                            self.cap.release()
                    except Exception:
                        pass
                self.cap = self._open()
                if self.cap is None:
                    self.connected = False
                    self.error = "Camera connection failed"
                    time.sleep(1.5)
                    continue
                # Do not claim the camera is online until the read loop gets a real frame.
                self.connected = False
                self.error = "Waiting for first camera frame"
                fails = 0

            try:
                if hasattr(self.cap, "capture_array"):
                    frame = self.cap.capture_array()
                    ok = frame is not None
                    if ok and frame.ndim == 3 and frame.shape[2] == 3:
                        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                else:
                    ok, frame = self.cap.read()
            except Exception as exc:
                ok, frame = False, None
                self.error = f"Camera read error: {exc}"
            if not ok or frame is None:
                fails += 1
                if fails >= 30:
                    self.connected = False
                    self.error = "Camera disconnected"
                    try:
                        self.cap.release()
                    except Exception:
                        pass
                    self.cap = None
                    fails = 0
                time.sleep(0.01)
                continue

            was_connected = self.connected
            fails = 0
            self.connected = True
            self.error = None
            if not was_connected:
                log("AI camera connected — receiving real frames")
            with self.lock:
                self.frame = frame
                self.frame_time = time.time()
                self.frame_id += 1

    def read(self):
        with self.lock:
            if self.frame is None or time.time() - self.frame_time > 2.0:
                return False, None, self.frame_id
            return True, self.frame.copy(), self.frame_id

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.5)
        self.thread = None
        if self.cap is not None:
            try:
                if hasattr(self.cap, "stop"):
                    self.cap.stop()
                if hasattr(self.cap, "close"):
                    self.cap.close()
                elif hasattr(self.cap, "release"):
                    self.cap.release()
            except Exception:
                pass
        self.cap = None
        self.connected = False
        self.frame = None
        self.frame_time = 0.0
        self.frame_id = 0


# ------------------------------------------------------------ PUBLISHER
class Publisher:
    """Reliable one-connection publisher. The backend never blocks AI inference."""
    def __init__(self, url, on_command):
        self.url = url
        self.on_command = on_command
        self.q = queue.Queue(maxsize=60)
        self.connected = False
        self.running = True
        self.ws = None
        threading.Thread(target=self._run, daemon=True, name="tracker-publisher").start()


    def publish(self, msg):
        # Keep only the newest preview frame; preserve status/command messages.
        if isinstance(msg, (bytes, bytearray)):
            try:
                with self.q.mutex:
                    items = self.q.queue
                    kept = [
                        item for item in items
                        if not isinstance(item, (bytes, bytearray))
                    ]
                    removed = len(items) - len(kept)
                    items.clear()
                    items.extend(kept)
                    self.q.unfinished_tasks = max(
                        0, self.q.unfinished_tasks - removed
                    )
                    self.q.not_full.notify_all()
            except Exception as exc:
                log(f"Preview queue cleanup failed: {exc}")
            except Exception:
                pass

        try:
            self.q.put_nowait(msg)
        except queue.Full:
            try:
                self.q.get_nowait()
            except queue.Empty:
                pass
            try:
                self.q.put_nowait(msg)
            except queue.Full:
                pass


    def _run(self):
        try:
            import websocket
        except ImportError:
            log("websocket-client not installed; backend publishing disabled")
            return
        retry = 1.0
        while self.running:
            ws = None
            try:
                ws = websocket.create_connection(self.url, timeout=5, enable_multithread=True)
                ws.settimeout(0.002)
                try:
                    ws.sock.settimeout(0.002)
                except Exception:
                    pass
                self.ws = ws
                self.connected = True
                retry = 1.0
                log(f"Backend connected: {self.url}")
                while self.running:
                    # Drain at most a few messages each turn so receives keep flowing.
                    for _ in range(4):
                        try:
                            raw = self.q.get_nowait()
                        except queue.Empty:
                            break
                        if isinstance(raw, (bytes, bytearray)):
                            ws.send_binary(raw)
                        else:
                            ws.send(json.dumps(raw, separators=(",", ":")))
                    try:
                        data = ws.recv()
                        if data == "":
                            raise RuntimeError("backend closed websocket")
                        if data:
                            self.on_command(json.loads(data))
                    except websocket.WebSocketTimeoutException:
                        pass
            except Exception as exc:
                if self.connected:
                    log(f"Backend disconnected: {exc}")
                self.connected = False
            finally:
                self.ws = None
                try:
                    if ws:
                        ws.close()
                except Exception:
                    pass
            if self.running:
                time.sleep(retry)
                retry = min(retry * 2, 5.0)

    def close(self):
        self.running = False
        self.connected = False
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass


# ------------------------------------------------------------ DEBUG DRAWING
def draw_ball(frame, ball):
    if ball is None:
        return
    x1, y1, x2, y2 = ball["box"]
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 3)
    cv2.putText(frame, f"BALL {ball['confidence']:.2f}", (x1, max(30, y1 - 10)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)


def draw_debug_overlay(frame, ball, w, h, tracking, fps, device):
    tx, ty = int(w * TARGET_X), int(h * TARGET_Y)
    dx, dy = int(w * DEAD_ZONE_X), int(h * DEAD_ZONE_Y)
    cv2.rectangle(frame, (tx - dx, ty - dy), (tx + dx, ty + dy), (255, 255, 0), 2)
    cv2.drawMarker(frame, (tx, ty), (255, 255, 0), cv2.MARKER_CROSS, 40, 2)
    if ball is not None:
        cx, cy = int(ball["x"]), int(ball["y"])
        cv2.circle(frame, (cx, cy), 8, (0, 0, 255), -1)
        cv2.line(frame, (tx, ty), (cx, cy), (0, 255, 255), 2)
    cv2.rectangle(frame, (15, 15), (560, 155), (0, 0, 0), -1)
    cv2.putText(frame, "CRICKET BALL AI TRACKER", (30, 45),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    cv2.putText(frame, f"{device} / OPENVINO", (30, 75),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    cv2.putText(frame, f"Inference: {fps:.1f} FPS", (30, 105),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
    cv2.putText(frame, "TRACKING: ON" if tracking else "TRACKING: OFF", (30, 135),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                (0, 255, 0) if tracking else (0, 0, 255), 2)


# ------------------------------------------------------------ SYSTEM METRICS
def system_metrics():
    out = {"cpu_load_1m": None, "cpu_temp_c": None, "memory_percent": None}
    try:
        load = os.getloadavg()[0]
        out["cpu_load_1m"] = round(float(load), 2)
    except Exception:
        pass
    try:
        import psutil
        out["memory_percent"] = round(float(psutil.virtual_memory().percent), 1)
        temps = psutil.sensors_temperatures()
        vals = [x.current for group in temps.values() for x in group if getattr(x, "current", None) is not None]
        if vals: out["cpu_temp_c"] = round(float(max(vals)), 1)
    except Exception:
        pass
    if out["cpu_temp_c"] is None:
        try:
            raw = Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()
            out["cpu_temp_c"] = round(int(raw) / 1000.0, 1)
        except Exception:
            pass
    return out

# ------------------------------------------------------------ MAIN
def main():
    global DEVICE, AUTO_CAMERA_TRACK, MIN_TRACK_CONFIDENCE, AI_MAX_FPS
    log("Cricket ball tracker starting")
    cfg = runtime_config()
    DEVICE = str(cfg.get("ai_device") or DEVICE).upper()
    AUTO_CAMERA_TRACK = bool(cfg.get("auto_camera_track", AUTO_CAMERA_TRACK))
    MIN_TRACK_CONFIDENCE = float(cfg.get("min_track_confidence", MIN_TRACK_CONFIDENCE))
    AI_MAX_FPS = max(5, int(cfg.get("ai_max_fps", AI_MAX_FPS)))
    motor_driver = str(cfg.get("motor_driver") or MOTOR_DRIVER).lower()
    pan_step_pin = int(cfg.get("pan_step_pin", PAN_STEP_PIN)); pan_dir_pin = int(cfg.get("pan_dir_pin", PAN_DIR_PIN))
    tilt_step_pin = int(cfg.get("tilt_step_pin", TILT_STEP_PIN)); tilt_dir_pin = int(cfg.get("tilt_dir_pin", TILT_DIR_PIN))
    detector = Detector()
    detector.load(cfg.get("model_path") or MODEL_PATH)
    if not detector.ready:
        log(f"AI OFFLINE: {detector.error}  (will keep running; use reload_model)")

    if motor_driver in ("gpio", "uln2003"):
        try:
            if motor_driver == "uln2003":
                from hardware.stepper_gpio import ULN2003PanTilt
                # The four existing pin fields map to ULN2003 IN1, IN2, IN3, IN4.
                controller = ULN2003PanTilt(pan_step_pin, pan_dir_pin, tilt_step_pin, tilt_dir_pin,
                                            max_pan_sps=250, log=log)
            else:
                from hardware.stepper_gpio import GpioStepperPanTilt
                controller = GpioStepperPanTilt(pan_step_pin, pan_dir_pin, tilt_step_pin, tilt_dir_pin,
                                                GPIO_MAX_PAN_SPS, GPIO_MAX_TILT_SPS, log)
        except Exception as exc:
            log(f"GPIO motor setup failed; motors disabled: {exc}")
            controller = PanTiltController(SERIAL_PORT, SERIAL_BAUDRATE, False)
    else:
        controller = PanTiltController(SERIAL_PORT, SERIAL_BAUDRATE,
                                       USE_SERIAL if motor_driver == "serial" else False)
    camera = None
    tracker = BallTracker()
    state = {"tracking": False, "camera": False, "reload": False}

    def ensure_camera():
        nonlocal camera
        if camera is None:
            camera = CameraReader(runtime_config()).start()
        else:
            camera.update_config(runtime_config())
            if not camera.running:
                camera.start()
        return camera

    def stop_camera():
        nonlocal camera
        if camera is not None:
            camera.stop()
            camera = None

    def on_command(msg):
        global DEVICE
        nonlocal camera, cfg
        cmd = msg.get("command") or msg.get("type")
        log(f"Command: {cmd}")
        if cmd == "start_camera":
            state["camera"] = True
            ensure_camera()
        elif cmd == "stop_camera":
            state["tracking"] = False
            state["camera"] = False
            tracker.reset()
            controller.stop()
            stop_camera()
        elif cmd == "start_tracking":
            cfg = runtime_config()
            desired_device = str(cfg.get("ai_device") or DEVICE).upper()
            if desired_device != DEVICE:
                DEVICE = desired_device
                detector.load(cfg.get("model_path") or MODEL_PATH)
            if not detector.ready:
                log(f"Cannot start tracking: {detector.error or 'AI model not ready'}")
                state["tracking"] = False
                return
            state["camera"] = True
            state["tracking"] = True
            ensure_camera()
            tracker.reset()
        elif cmd == "stop_tracking":
            state["tracking"] = False
            tracker.reset()
            controller.stop()
            # Keep camera alive for Live Preview.
        elif cmd == "restart_camera":
            state["camera"] = True
            ensure_camera()
            camera.restart()
        elif cmd in ("reload_model", "reload_config"):
            state["reload"] = True

    pub = Publisher(BACKEND_WS_URL, on_command)

    if SHOW_DEBUG_WINDOW:
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
        if FULLSCREEN:
            cv2.setWindowProperty(WINDOW_NAME, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    frame_number = 0
    last_detections = []
    inference_times = []
    latencies = []
    fps_counter = 0
    last_print = last_status = last_pub = last_preview = time.perf_counter()
    cam_fps = 0.0
    last_detection_ts = None
    last_inference_at = 0.0
    ball = None
    inference_fps = 0.0
    last_cam_state = None
    last_processed_frame_id = -1
    camera_fps_ema = 0.0
    last_camera_frame_time = None

    log("System ready — AI process is idle until Start Tracking is pressed")
    try:
        while True:
            if state["reload"]:
                state["reload"] = False
                cfg = runtime_config()
                AUTO_CAMERA_TRACK = bool(cfg.get("auto_camera_track", AUTO_CAMERA_TRACK))
                MIN_TRACK_CONFIDENCE = float(cfg.get("min_track_confidence", MIN_TRACK_CONFIDENCE))
                AI_MAX_FPS = max(5, int(cfg.get("ai_max_fps", AI_MAX_FPS)))
                detector.load(cfg.get("model_path") or MODEL_PATH)
                log("Model reloaded" if detector.ready else f"AI OFFLINE: {detector.error}")

            # Camera owns the preview lifecycle. Tracking is an independent mode
            # running on the same decoded frames.
            if not state["camera"]:
                ball = None
                controller.stop()
                now = time.perf_counter()
                if now - last_status >= 1.0:
                    last_status = now
                    pub.publish(status_msg(None, detector, state, 0, 0, 0, pub))
                time.sleep(0.08)
                continue

            camera = ensure_camera()
            ok, frame, frame_id = camera.read()
            if not ok:
                if last_cam_state is not False:
                    log(f"CAMERA OFFLINE: {camera.error or 'no frames'}")
                    last_cam_state = False
                ball = None
                tracker.update(None)
                controller.stop()
                now = time.perf_counter()
                if now - last_status >= 1.0:
                    last_status = now
                    pub.publish(status_msg(camera, detector, state, 0, 0, 0, pub))
                time.sleep(0.03)
                continue

            if last_cam_state is not True:
                log("Camera online")
                last_cam_state = True

            # CameraReader exposes a monotonically increasing frame id. Never
            # process the same decoded frame repeatedly; doing so inflated the
            # displayed FPS into impossible values and wasted CPU.
            if frame_id == last_processed_frame_id:
                time.sleep(0.001)
                continue
            last_processed_frame_id = frame_id

            h, w = frame.shape[:2]
            frame_number += 1
            fps_counter += 1
            now = time.perf_counter()
            t_frame = now
            if last_camera_frame_time is not None:
                dt_frame = now - last_camera_frame_time
                if dt_frame > 0:
                    inst_fps = min(120.0, 1.0 / dt_frame)
                    camera_fps_ema = inst_fps if camera_fps_ema <= 0 else (0.85 * camera_fps_ema + 0.15 * inst_fps)
            last_camera_frame_time = now

            if detector.ready and state["tracking"]:
                inference_due = (now - last_inference_at) >= (1.0 / max(1, AI_MAX_FPS))
                if inference_due and frame_number % max(1, INFERENCE_EVERY_N_FRAMES) == 0:
                    last_inference_at = now
                    t0 = time.perf_counter()
                    try:
                        last_detections = detector.detect(frame)
                    except Exception as e:
                        log(f"Inference error: {e}")
                        last_detections = []
                    el = time.perf_counter() - t0
                    inference_times.append(el)
                    latencies.append(time.time() - camera.frame_time)
                    del inference_times[:-100]
                    del latencies[:-100]
                detections = last_detections
            else:
                detections = []

            ball = select_best_ball(detections)
            tracker.update(ball)
            if ball is not None:
                last_detection_ts = time.time()

            predicted = tracker.predict()
            cx, cy = predicted if predicted is not None else ((ball["x"], ball["y"]) if ball is not None else (None, None))

            if (state["tracking"] and AUTO_CAMERA_TRACK and cx is not None
                    and tracker.last_seen <= MAX_LOST_FRAMES
                    and (ball is None or float(ball.get("confidence", 0)) >= MIN_TRACK_CONFIDENCE)):
                controller.send(*calculate_pan_tilt(cx, cy, w, h))
            else:
                controller.stop()

            recent = inference_times[-30:]
            avg = sum(recent) / len(recent) if recent else 0
            inference_fps = 1.0 / avg if avg > 0 else 0

            if ball is not None and now - last_pub >= PUBLISH_INTERVAL:
                last_pub = now
                pub.publish({
                    "type": "ball_detection", "timestamp": time.time(),
                    "detected": True, "confidence": round(ball["confidence"], 3),
                    "x": int(ball["x"]), "y": int(ball["y"]), "box": list(ball["box"]),
                    "frame_width": w, "frame_height": h, "fps": round(inference_fps, 1),
                    "motor_target_x": round(w * TARGET_X, 1), "motor_target_y": round(h * TARGET_Y, 1),
                })

            if (PREVIEW_STREAM and pub.connected
                    and now - last_preview >= 1.0 / max(1, PREVIEW_FPS)):
                last_preview = now
                try:
                    small = frame
                    if PREVIEW_WIDTH and w > PREVIEW_WIDTH:
                        small = cv2.resize(frame, (PREVIEW_WIDTH, int(h * PREVIEW_WIDTH / w)), interpolation=cv2.INTER_AREA)
                    okj, jpg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, PREVIEW_QUALITY])
                    if okj:
                        pub.publish(jpg.tobytes())
                except Exception:
                    pass

            if now - last_status >= 1.0:
                cam_fps = camera_fps_ema if camera_fps_ema > 0 else (fps_counter / (now - last_print) if now > last_print else 0)
                last_status = now
                lat = (sum(latencies[-30:]) / len(latencies[-30:]) * 1000 if latencies else 0)
                pub.publish(status_msg(camera, detector, state, inference_fps, cam_fps, lat, pub,
                                       ball, last_detection_ts, w, h, avg * 1000))

            if SHOW_DEBUG_WINDOW:
                draw_ball(frame, ball)
                if SHOW_DEBUG_OVERLAY:
                    draw_debug_overlay(frame, ball, w, h, state["tracking"], inference_fps, detector.device_used or "NO AI")
                disp = frame
                if DISPLAY_WIDTH and DISPLAY_WIDTH != w:
                    disp = cv2.resize(frame, (DISPLAY_WIDTH, int(h * DISPLAY_WIDTH / w)))
                cv2.imshow(WINDOW_NAME, disp)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                if key == 32:
                    on_command({"command": "stop_tracking"})

            if now - last_print >= 1.0:
                display_fps = camera_fps_ema if camera_fps_ema > 0 else (fps_counter / (now - last_print) if now > last_print else 0)
                print(f"\rCamera FPS: {display_fps:.1f} | Inference: {inference_fps:.1f} | Ball: {'YES' if ball else 'NO'}", end="", flush=True)
                fps_counter = 0
                last_print = now
    except KeyboardInterrupt:
        pass
    finally:
        print()
        log("Stopping...")
        controller.close()
        stop_camera()
        pub.close()
        if SHOW_DEBUG_WINDOW:
            cv2.destroyAllWindows()
        log("Stopped")


def status_msg(camera, detector, state, fps, cam_fps, latency_ms, pub,
               ball=None, last_ts=None, w=0, h=0, inference_ms=0):
    cam_connected = bool(camera and camera.connected)
    cam_error = camera.error if camera else ("Tracking stopped" if not state["tracking"] else "Camera not started")
    return {
        "type": "status", "timestamp": time.time(),
        "camera": "CONNECTED" if cam_connected else "OFFLINE",
        "camera_error": cam_error,
        "model": "READY" if detector.ready else "OFFLINE",
        "model_error": detector.error,
        "device": detector.device_used,
        "openvino": detector.ov_version,
        "tracking": bool(state["tracking"]),
        "camera_running": bool(camera and camera.running),
        "motor": "FOLLOWING" if state["tracking"] and ball is not None else "STOPPED",
        "fps": round(fps, 1), "camera_fps": round(cam_fps, 1),
        "latency_ms": round(latency_ms, 1),
        "inference_ms": round(inference_ms, 1),
        "detected": ball is not None,
        "confidence": round(ball["confidence"], 3) if ball else None,
        "x": int(ball["x"]) if ball else None,
        "y": int(ball["y"]) if ball else None,
        "last_detection": last_ts,
        "frame_width": w, "frame_height": h,
        "auto_camera_track": bool(AUTO_CAMERA_TRACK),
        "min_track_confidence": round(float(MIN_TRACK_CONFIDENCE), 3),
        "system": system_metrics(),
    }



if __name__ == "__main__":
    main()