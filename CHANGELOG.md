# v2.8.7

- Hardened YouTube/FFmpeg supervision: startup stalls are detected before FFmpeg reaches LIVE, and unexpected clean exits are automatically reconnected while streaming remains requested.
- Added clearer FFmpeg failure state when YouTube/RTMP closes the connection unexpectedly.
- Increased tolerance of the local MJPEG browser-renderer input during startup and 15 FPS camera operation.
- Preserved the exact browser-rendered score overlay, raw-camera fallback, ball tracking, and RTSP camera path.

# v2.8.6

- Fixed Windows YouTube/broadcast renderer startup on Python 3.14 by using a Proactor asyncio loop inside the Playwright renderer thread.
- Removed the launcher override that incorrectly forced Windows SelectorEventLoop and caused Playwright `NotImplementedError` subprocess failures.
- Kept the exact Live Preview score overlay authenticated and usable by the YouTube compositor, with the existing raw-camera fallback so a browser failure cannot stop the stream pipeline.
- Changed camera discovery to be explicitly requested (`/api/cameras?probe=1`) so RTSP/network streaming does not repeatedly probe nonexistent USB indexes or generate DSHOW/MSMF/obsensor warnings.
- Preserved realtime/low-latency YouTube profiles, automatic ball tracking/pan-tilt, and the broadcast rule that no ball-tracking marker is shown.

## 2.8.5
- Fixed fresh/incomplete Windows `.venv` startup: `python run.py` now detects missing project packages before importing FastAPI, bootstraps pip with `ensurepip` when needed, and installs the appropriate requirements automatically.
- Prevents `ModuleNotFoundError: fastapi` from stopping the launcher when the virtual environment exists but dependencies were not installed.
- Preserved the v2.8.4 authenticated YouTube score overlay, RTSP/Windows camera reliability, automatic ball tracking/pan-tilt, monitoring, low-latency YouTube streaming, and GitHub application updates.

# Changelog

## 2.8.4
- Fixed YouTube/broadcast score overlay authentication: the headless Chromium renderer now receives a server-created session so the exact same Live Preview `index.html?overlay=1` score overlay can read live scoring data.
- YouTube broadcast output now uses the same authoritative score overlay page as Live Preview without exposing the operator password to Chromium.
- Added renderer authentication logging and regression coverage.

# v2.8.3 — RTSP timeout and Windows discovery fix

- Fixed RTSP/FFmpeg connections using the default long socket timeout; phone-camera connections now request TCP transport with 5-second open/read socket timeouts where supported.
- RTSP capture is not reported online until real decoded frames arrive.
- Added a bounded first-frame validation so a dead phone stream returns to reconnect logic instead of blocking the tracker for ~30 seconds.
- Reduced Windows USB discovery noise by suppressing transient OpenCV backend/index warnings during device probing.
- Preserved Windows USB backend negotiation, exact score-only broadcast overlay, automatic ball-following pan/tilt, OpenVINO tracking, YouTube streaming, monitoring, recordings, and GitHub application updates.

# v2.8.2 — Robust Windows camera mode negotiation

- Fixed a false “AI camera connected” state when Windows Media Foundation opened a device but could not deliver stable frames.
- Windows USB cameras now prefer DirectShow, fall back to Media Foundation/OpenCV, and require multiple real frames before accepting a device.
- USB mode negotiation now tests safe fallback modes (1080p30, 720p30/60, 480p30) instead of blindly forcing 1080p60 on cameras that do not support it.
- The tracker no longer reports the camera online until the live read loop receives a real frame.
- RTSP startup now uses OpenCV open/read timeouts when the installed build supports them, avoiding long dead waits for an offline phone stream.
- Camera discovery uses the same stable-frame validation and conservative 720p30 probe.
- Preserved exact score-only broadcast overlay, no ball marker, automatic ball-following pan/tilt, OpenVINO tracking, YouTube streaming, monitoring, recordings, and GitHub application updates.

# v2.8.1 — Windows camera reliability

- Added automatic Windows USB camera backend detection using Media Foundation, DirectShow and OpenCV default capture.
- USB Camera settings now support simple Auto-detect instead of requiring a device index.
- Camera discovery validates that a real frame can be read before reporting a camera.
- Added explicit websocket-client dependency.
- Reduced Windows Proactor connection-reset noise by preferring the Selector event loop when available.
- Preserved 1080p60 low-latency streaming, exact score overlay, ball tracking and automatic pan/tilt control.

# v2.8.0 — Realtime camera tracking & simpler setup

- Fixed camera FPS measurement and duplicate-frame processing; the AI loop now processes each decoded camera frame once.
- Added automatic Playwright bootstrap for the exact browser broadcast renderer.
- Broadcast compositor defaults to 30 FPS to reduce CPU pressure while YouTube output remains 1080p60 when selected.
- Added low-latency FFmpeg muxing/flush options and clearer FFmpeg error reporting.
- Simplified Setup Camera / Streaming with user-facing YouTube profiles: Real-time cricket, Balanced, High quality.
- Removed separate AI model download/update controls. AI model files included in an application release are updated with the normal GitHub application update.
- Kept automatic ball-following pan/tilt tracking and exact Live Preview score overlay; no broadcast ball marker.
- GitHub application updates continue to use published Releases and SHA-256 validation when configured.

# Changelog

## 2.7.1
- Fixed a startup crash in `ai/ball_tracker.py` caused by runtime configuration assignments shadowing module-level camera-tracking settings.
- Added a regression test for the tracker configuration scope bug.
- Sanitized the distributed tracker configuration so it contains no embedded RTSP credentials.
- Full release validation: Python syntax, JavaScript syntax, backend tests, tracker scope regression test, ZIP integrity.

## v2.7.0

### Realtime camera tracking + performance
- Added Low Latency / Performance, Balanced and Quality YouTube profiles.
- Default Performance profile uses 1080p60 at 12 Mbps, with veryfast x264 and zerolatency settings.
- Added FFmpeg encoder FPS, bitrate, speed, duplicate-frame and dropped-frame telemetry.
- Added Pi CPU, memory and temperature monitoring.
- Added measurable camera-to-AI/inference timing and encoder/ingest health indicators.
- Added adaptive AI inference scheduling so a heavy detector does not build a growing frame queue or starve camera control.
- Automatic pan/tilt tracking now uses predicted ball position and a minimum confidence gate; motors stop when confidence is insufficient or the ball is lost.
- Added optional GitHub/direct-download AI model update support. A new application release does not require a new model.
- Kept the exact Live Preview HTML/CSS score overlay in the broadcast renderer.
- Kept the broadcast ball-tracking marker removed.

### YouTube
- 1080p60 Performance profile is optimized for lower encoder load; YouTube's current official H.264 recommendation for 1080p60 is 17 Mbps, while 12 Mbps is a lower-load performance choice.
- 4K remains normal latency because YouTube does not provide low-latency optimization for 2160p.
# Changelog

## 2.6.1
- Removed ball-tracking markers from the broadcast renderer. Ball tracking remains available in Live Preview.
- Added configurable YouTube resolution (720p/1080p/1440p/2160p), 30/60 fps, video bitrate, audio bitrate, and latency.
- YouTube Live API stream creation now requests the selected fixed resolution and frame rate.
- FFmpeg YouTube encoding now matches the selected resolution/FPS/bitrate and uses 2-second GOPs.
- 4K automatically uses Normal latency because YouTube does not support low/ultra-low latency at 2160p.
- Browser broadcast renderer uses the selected YouTube output dimensions so the exact HTML/CSS score overlay is rendered at the target output size.

# v2.6.0

- One logical recording per match in Recorded Matches; pause/resume parts are merged on stop.
- Legacy timestamped recordings are grouped into one logical match entry.
- Exact Chromium-rendered Live Preview score bar/scorecard remains the broadcast source.
- Broadcast renderer now includes the live ball-tracking marker.
- Increased camera/preview transport to 20 FPS by default.
- Ball detection publication increased to up to 20 updates/sec with smoothing and bounded prediction.
- YouTube encoder tuned for low latency and short keyframes.
- YouTube broadcast creation supports `normal`, `low`, and `ultraLow` latency.
- Stream watchdog restarts an encoder that stops producing progress.
- RTMPS ingestion is preferred when YouTube supplies it.
- Added Raspberry Pi Picamera2 support with V4L2 fallback.
- Added optional Raspberry Pi STEP/DIR GPIO pan/tilt controller.
- Added GitHub Releases update source.
- Added GitHub release packaging workflow.
- Existing ESP32 serial motor mode remains available.
- Existing scoring, overlay, recording, camera and match functionality preserved.
