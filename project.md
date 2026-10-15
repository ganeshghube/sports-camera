# Cricket Live — Current Project Architecture

This document describes the current implementation shipped in the project.

## Navigation
- Dashboard
- Setup Match
- Scoring
- Overlay
- Live Preview
- Ball Tracking
- Recorded Matches
- Setup Camera / Streaming
- Settings

There is no separate Start Match workflow. Match creation/start is handled by Setup Match.

## Scoring and overlay
Scoring shows the live score controls and the same score-bar page (`index.html?scorebar=1`) used by the public overlay. It does not decode a camera preview. Overlay configuration shows graphics only.

## Live Preview / Final Output
Final Output owns the camera preview. Final Output composes that camera image with the transparent public score overlay (`index.html?overlay=1`) and scales responsively to the available 16:9 stage.

## Camera
`CameraManager` is the central camera abstraction. It supports RTSP/Wi-Fi network cameras, USB cameras, and Raspberry Pi/V4L2 cameras where supported by the host. Camera settings are saved through the Setup Camera / Streaming page.

## Ball tracking
The AI tracker is supervised by `run.py` but remains idle until Start Tracking is pressed. Tracking and camera preview are independent UI operations. Start Tracking changes to Stop Tracking while active. The tracker reports real camera/model/detection/motor state and sends safe motor-stop commands when tracking is lost or stopped.

## Recording and streaming
Recording uses explicit IDLE/STARTING/RECORDING/PAUSED/STOPPING/ERROR states. Start becomes Stop while active and Pause/Resume is available when valid. Streaming has the same start/stop state model. The current FFmpeg streaming path uses the selected camera source; the browser Final Output is the authoritative camera+graphics preview.

## Device Administration
The Settings page provides current/remote version information, versioned ZIP update installation, application-only restart, configuration/data factory reset and network/application URL information. Factory reset preserves recordings, application files and the OS. Versioned update packages and `latest.json` are generated with `python tools/create_update.py` and published in the GitHub `updates/` folder.

## Runtime
Use `python run.py` as the single launcher. It starts the FastAPI web server on `0.0.0.0:8000` and supervises the AI tracker process. The tracker does not capture frames until tracking is started.

## Windows Git Bash setup
```bash
cd /c/00_Ganesh/Sports-Camera
py -3.11 -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
python -m pip install -r ai/requirements.txt
cp .env.example .env
python run.py
```

`0.0.0.0` is a server bind address, not a browser address. Use `http://127.0.0.1:8000` locally or `http://<device-ip>:8000` from another device on the LAN.
