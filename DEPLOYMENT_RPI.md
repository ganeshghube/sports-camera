# Raspberry Pi deployment

## Recommended hardware path

- Raspberry Pi OS 64-bit
- CSI camera using Picamera2/libcamera
- Two STEP/DIR stepper drivers (A4988/DRV8825/TMC-style) for pan and tilt
- Adequate 5V/12V motor power supply; do not power motors from GPIO
- Common ground between the Pi and motor-driver logic

## Install

```bash
sudo apt update
sudo apt install -y ffmpeg python3-venv python3-dev python3-picamera2
./tools/setup_raspberry_pi.sh
```

Then configure `.env`:

```text
camera_type=rpi
RPI_CAMERA_BACKEND=auto
MOTOR_DRIVER=gpio
PAN_STEP_PIN=17
PAN_DIR_PIN=27
TILT_STEP_PIN=22
TILT_DIR_PIN=23
GPIO_MAX_PAN_SPS=700
GPIO_MAX_TILT_SPS=550
YOUTUBE_LATENCY=ultraLow
PREVIEW_FPS=15
PREVIEW_WIDTH=960
PREVIEW_QUALITY=68
PUBLISH_INTERVAL=0.05
```

The Pi camera reader prefers Picamera2 and falls back to V4L2. OpenVINO supports ARM64 CPU inference; the actual ball-tracking FPS depends on the Pi model, camera resolution and model size.

## Motor tuning

Start with conservative `GPIO_MAX_*_SPS`, `PAN_GAIN`, `TILT_GAIN`, and a generous dead zone. Verify the camera mount direction with the motor driver unloaded before mounting the camera.

Never let the software become the only safety mechanism for the physical mount. Add mechanical end stops or a safe travel limit in the motor controller.

## GitHub updates (versioned ZIP in `updates/`)

Set these values in `.env`:

```text
GITHUB_REPO=YOUR_ORG/YOUR_REPO
GITHUB_BRANCH=main
```

From a command prompt in the project folder, create the versioned ZIP and remote manifest:

```bash
python tools/create_update.py --version 2.8.8 --repo YOUR_ORG/YOUR_REPO --release-notes "Your update summary"
```

Commit and push the updated `VERSION` and generated `updates/` files. The script creates `updates/cricket-live-v2.8.8.zip`, a SHA-256 checksum, and `updates/latest.json`; the app checks that manifest and installs a newer version from Settings. The ZIP excludes local `.env`, runtime database/data, recordings, virtual environments, caches, and the `updates/` folder itself. During installation, user data and recordings are preserved.

`UPDATE_MANIFEST_URL` and GitHub Releases remain supported for backward compatibility. Private repositories require a device-side `GITHUB_TOKEN`; never commit tokens. Factory Reset resets app configuration and stored app data but preserves recordings and application files. Restart Application restarts the app process only; it does not reboot the Raspberry Pi.

## v2.7.1 realtime cricket setup

Recommended first match profile:
- Camera: 1920x1080 at 60 FPS if the phone/camera and Wi-Fi path can sustain it.
- YouTube: 1080p60, Low Latency / Performance profile, 12 Mbps video, 128 kbps audio.
- Keep the camera and AI worker running locally; FFmpeg/YouTube is a separate path.
- Enable automatic camera tracking only after testing the pan/tilt mount with the phone removed or safely secured.

For a GPIO pan/tilt mount, select `Raspberry Pi GPIO STEP/DIR` in Setup Camera / Streaming and verify STEP/DIR pins against the actual motor driver wiring. The controller stops when ball confidence is too low or the ball is lost.

The application updater and AI model updater are independent. A normal GitHub application release does not require a new ball model. Configure `GITHUB_REPO` for application releases. Configure `MODEL_UPDATE_URL` + `MODEL_SHA256` (or `MODEL_GITHUB_REPO` + `MODEL_GITHUB_ASSET`) only when a new model is intentionally released.
