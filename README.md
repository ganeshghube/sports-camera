# Cricket Live — Scoring + Streaming + AI Ball Tracking

This is the existing Cricket Live application updated for a clean Windows/Git Bash and Raspberry Pi workflow.

## Architecture

```text
Camera Source
   ├── Wi-Fi / RTSP / phone camera
   ├── USB camera
   └── Raspberry Pi camera
          │
          ├── Live Preview
          ├── Recording
          ├── Streaming
          └── AI Ball Tracking (separate supervised process)
                    │
                    └── ESP32 pan/tilt motor (optional)
```

The scorer remains in control of the official score. AI ball detection is informational and can drive the optional pan/tilt motor when tracking is enabled.

## One-command start

Use Python 3.11.

```bash
cd /c/00_Ganesh/camera
source .venv/Scripts/activate
python run.py
```

`run.py` starts:
1. FastAPI on `0.0.0.0:8000`
2. the AI tracker as a separate child process

The AI process starts **idle**. It does not capture the camera until **Ball Tracking → Start Tracking** is pressed.

Open locally:

```text
http://127.0.0.1:8000
```

Open from another LAN device with the laptop/Raspberry Pi's actual IP, for example:

```text
http://192.168.1.105:8000
```

Do not type `http://0.0.0.0:8000` into the browser; `0.0.0.0` is the server bind address.

## Git Bash installation

```bash
cd /c/00_Ganesh/camera
rm -rf .venv
py -3.14 -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
python -m pip install -r ai/requirements.txt
[ -f .env ] || cp .env.example .env
python -c "from backend.system import SystemManager; print('SystemManager import OK')"
ffmpeg -version
ffprobe -version
python -c "from backend.system import SystemManager; print('SystemManager OK')" && \
python -c "import fastapi, uvicorn, cv2, numpy; print('Python dependencies OK')" && \
python run.py
```

If FFmpeg is not installed, install it and restart Git Bash so it is on PATH.

## Pages

- Dashboard
- Setup Match
- Scoring
- Overlay
- Final Output
- Ball Tracking
- Recorded Matches
- Setup Camera / Streaming
- Settings

### Scoring

The Scoring page does not display a camera preview. It embeds the **same `index.html` score bar** used by the public overlay, so the scorer sees the live authoritative score graphics.

### Overlay

The Overlay page contains overlay controls/configuration. It does not duplicate a camera preview.

### Live Preview

The Final Output page is the camera/program-output workspace:

- Start/Stop Camera
- responsive camera preview
- live score overlay
- recording Start/Stop
- recording Pause/Resume
- YouTube/Facebook/Custom RTMP Start/Stop

Camera preview is independent from AI ball tracking.

### Ball Tracking

The AI tracker has one stateful button:

```text
START TRACKING → STOP TRACKING
```

The AI process can stay running while tracking is stopped. The camera used by the AI tracker is opened only after Start Tracking.

When tracking is active:

```text
Camera → Frame Capture → Ball Detection → Position Error → Pan/Tilt Motor
```

The default target is the center of the frame. Dead-zone, gain, speed limits and reversal are configurable in `.env`. `USE_SERIAL=false` remains the safe default.

## Camera setup

Use **Setup Camera / Streaming** to:

- detect attached USB cameras
- select a USB camera
- select a Raspberry Pi `/dev/video*` camera
- configure a Wi-Fi/RTSP URL
- save camera selection
- configure resolution/FPS
- configure AI device
- configure streaming destinations
- connect YouTube/Facebook OAuth where credentials are configured

The saved camera configuration is written to the local application database and to `data/tracker_config.json` for the separate AI process.

## Images

Player/team images are stored through the existing local data model. The frontend also has a global broken-image fallback so a missing image does not break the layout.

## Recording states

The UI uses the real backend state:

```text
IDLE
STARTING
RECORDING
PAUSED
STOPPING
ERROR
```

The Start button becomes the red Stop button while recording.

## Settings

The Settings page contains:

- Current version and version available at the remote server
- Automatic update discovery and a version-specific **Install and Restart** action
- Restart Application (application process only; no device reboot)
- Factory Reset (configuration and stored application data; recordings and application files are preserved)
- Current hostname/application URL

Factory Reset requires a confirmation phrase.

## Software updates

Set:

```text
UPDATE_MANIFEST_URL=
```

when you have an update service. The manifest should return JSON like:

```json
{
  "version": "2.2.0",
  "release_notes": "Bug fixes and camera improvements",
  "package_url": "https://example.com/releases/cricket-live-2.2.0.zip",
  "sha256": "..."
}
```

The updater validates the ZIP and SHA-256 before copying application files. Runtime data (`data`, `recordings`, `updates`) and `.venv` are preserved.

## YouTube / Facebook

Manual stream keys and the existing OAuth flow are preserved. Live streaming is blocked while TEST MODE is ON.

FFmpeg recording and streaming include a lightweight live score text overlay when Broadcast Overlay is enabled. Live Preview independently shows the responsive camera + rich `index.html` score overlay as the operator-facing Final Output.

## Tests

```bash
python -m pytest tests -q
```

## Troubleshooting

### `bash: .venvScriptsactivate: command not found`

Use:

```bash
source .venv/Scripts/activate
```

### `pip` points to old `C:\Python`

Use:

```bash
python -m pip ...
```

after activating `.venv`.

### `SystemManager` not found

Run from the project root:

```bash
cd /c/00_Ganesh/Sports-Camera
python -c "from backend.system import SystemManager; print('OK')"
```

### Camera is stopped

Go to **Live Preview → Start Camera**.

### Ball Tracking hangs

Use:

```bash
python run.py
```

The launcher keeps the AI tracker separate from FastAPI. The AI tracker is idle until the tracking button is pressed.

### LAN access

Find the machine's IPv4 address with Windows:

```bash
ipconfig.exe
```

Then use:

```text
http://<IPv4-address>:8000
```

## v2.4.0 YouTube broadcast fix

- The YouTube/RTMP encoder receives the server-composited cricket score bar and scorecard directly in the video frames.
- The broadcast no longer depends on `data/stream_score.txt` or FFmpeg `drawtext` file reloads.
- YouTube uses the RTMPS ingestion address when the API provides one.
- Unexpected FFmpeg/ingest disconnects automatically reconnect with bounded exponential backoff while the destination remains enabled. Manual Stop disables reconnect.
- YouTube broadcasts are created with automatic start enabled and automatic stop disabled so a transient encoder reconnect does not unnecessarily terminate the event.
- Google OAuth redirect URI is configurable with `GOOGLE_OAUTH_REDIRECT_URI` and defaults to `http://127.0.0.1:8000/api/oauth/youtube/callback`.

## v2.5.0 — exact Live Preview broadcast overlay

YouTube/RTMP now receives the same HTML/CSS overlay page used by **Live Preview**. A headless Chromium renderer composes the raw camera MJPEG with `frontend/index.html?overlay=1` and hands the resulting frames to FFmpeg. This is intentionally different from the previous OpenCV approximation: score bar, scorecard, team colours, typography, spacing, badges, player photos, animations/state and visibility follow the actual Live Preview DOM/CSS.

The browser renderer is non-blocking. If Chromium is unavailable or temporarily fails, the existing OpenCV compositor/raw camera frame is used immediately, so the YouTube encoder is not stopped by an overlay-renderer failure.

### Browser renderer

`requirements.txt` includes Playwright. On Windows the application automatically looks for Chrome/Edge in the normal installation locations. You can override the executable with `CRICKET_CHROME_PATH`. If neither is installed, install the Playwright browser once with `python -m playwright install chromium`.

`BROADCAST_BROWSER_FPS=30` controls exact browser compositor capture rate (10–30 FPS). The YouTube output can remain 1080p60; the compositor avoids forcing the Pi to render a full Chromium screenshot 60 times per second.

## v2.6 realtime deployment

### YouTube realtime mode
Set `YOUTUBE_LATENCY=ultraLow` (the UI also exposes this setting). The broadcast encoder uses
low-latency x264 settings, short GOP/keyframes, and the exact Chromium-rendered Live Preview
overlay. The browser renderer receives the live score state, so the exact score bar and scorecard are encoded into the YouTube video rather than being a browser-only layer. Ball-tracking graphics are intentionally not shown in the broadcast.

### One recording per match
Pause/resume recording creates temporary parts, but stopping the recording automatically merges
all valid parts into one MP4. Recorded Matches groups legacy part files by match so the UI shows
one logical recording per match.

### Raspberry Pi
For Raspberry Pi OS 64-bit, use the optional `requirements-rpi.txt`. Picamera2 is recommended
for CSI camera modules; install it from Raspberry Pi OS (`sudo apt install -y python3-picamera2`).
The included `hardware/stepper_gpio.py` supports STEP/DIR drivers such as A4988/DRV8825 through
`MOTOR_DRIVER=gpio`. Existing ESP32 serial control remains available with `MOTOR_DRIVER=serial`.

Example Pi motor configuration:
```
MOTOR_DRIVER=gpio
PAN_STEP_PIN=17
PAN_DIR_PIN=27
TILT_STEP_PIN=22
TILT_DIR_PIN=23
GPIO_MAX_PAN_SPS=700
GPIO_MAX_TILT_SPS=550
```

### GitHub versioned ZIP updates

The Settings page displays **Current version** and **Available at remote server**. It checks `updates/latest.json` in the configured GitHub repository automatically; when a newer version is found, the button offers **Version X available — Install and Restart**. No separate “Check for Updates” or “Update & Restart” controls are needed.

Configure `.env`:

```text
GITHUB_REPO=YOUR_ORG/YOUR_REPO
GITHUB_BRANCH=main
```

Create a package from a command prompt in the project folder:

```bash
python tools/create_update.py --version 2.8.8 --repo YOUR_ORG/YOUR_REPO --release-notes "Describe the changes"
```

The command creates `updates/cricket-live-v2.8.8.zip`, its `.sha256` checksum, and `updates/latest.json`. Commit and push `VERSION` and the generated `updates/` files to the configured branch. The app downloads the ZIP directly from the repository, validates its SHA-256, installs the application files, and preserves runtime data, recordings, `.env`, virtual environments and the `updates/` directory. Do not commit `.env` or access tokens. For private repositories, configure `GITHUB_TOKEN` on the device.

`UPDATE_MANIFEST_URL` remains supported for deployments that already use a custom manifest. GitHub Releases are retained as a compatibility fallback when `updates/latest.json` is absent.

Factory Reset clears application settings/data and authentication sessions/secrets, but preserves recordings, update packages, application files and environment configuration. Restart Application restarts only the application process, not the operating system.

## YouTube output quality (v2.6.1)

Setup Camera / Streaming now includes YouTube resolution, FPS, video bitrate, audio bitrate, and latency controls. The selected values are used when creating the YouTube Live stream and by FFmpeg when encoding it.

Recommended H.264 defaults follow YouTube's current guidance: 720p 8 Mbps, 1080p 14/17 Mbps (30/60 fps), 1440p 21/34 Mbps, and 2160p 42/50 Mbps. 4K is forced to Normal latency because YouTube does not support low or ultra-low latency at 2160p.

Changing YouTube resolution/FPS requires creating a new YouTube Live stream; YouTube does not make those inbound stream properties mutable after stream creation.

Broadcast renderer ball-tracking markers are removed in v2.6.1. Ball tracking remains available in Live Preview.

## v2.7.1 realtime tracking/performance

For Raspberry Pi cricket use, start with **1080p / 60 FPS / Low Latency / Performance**. The profile defaults to 12 Mbps to reduce encoder load while keeping 1080p60. YouTube currently lists 17 Mbps as the recommended H.264 ingestion bitrate for 1080p60, so the Quality profile remains available when the Pi and uplink can sustain it.

The application now reports encoder FPS, speed, dropped/duplicated frames, CPU load/temperature, AI inference timing, and ingest health. These are Pi-side measurements; exact viewer playback delay inside YouTube cannot be read directly from the encoder.

Automatic pan/tilt tracking is enabled by default. It uses ball detection + smoothing + short-horizon prediction and stops motors when confidence is too low or the ball is lost.

AI model files ship with normal application releases. There is no separate model-update workflow for the operator; installing a new application release updates the bundled model when the release contains one.

## v2.8 GitHub updates and AI model policy

- Settings automatically reads `updates/latest.json` from the configured GitHub repository and displays the current and remote versions.
- Set `GITHUB_REPO=owner/repository` and optionally `GITHUB_BRANCH=main` in `.env`. For a public repository no GitHub token is required.
- Run `python tools/create_update.py --version 2.8.8 --repo OWNER/REPOSITORY --release-notes "Summary"` to create a versioned ZIP, SHA-256 checksum and `latest.json` under `updates/`.
- Commit and push the generated files to GitHub. The app validates the SHA-256, installs the ZIP and restarts the application process.
- The AI model is part of the application package. Runtime data, recordings, `.env`, virtual environments and update packages are preserved during updates.

YouTube realtime profile:

- Real-time cricket: 1080p60, 12 Mbps, Ultra Low latency.
- Balanced: 1080p60, 14 Mbps, Low latency.
- High quality: 1080p60, 17 Mbps, Low latency.

YouTube documents Ultra Low latency as normally under 5 seconds for most viewers, while network congestion and player buffering can add delay. 4K cannot use Ultra Low or Low latency. Test with the same movement/audio conditions as the real match.
