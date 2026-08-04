# Intervue — Advanced AI Proctoring Dashboard

Intervue is a real-time, AI-driven interview proctoring and video session application. It integrates multiple signal processing models (Vision gaze tracking, Audio anomaly detection, Typing rhythm biometrics, Hardware diagnostics, and Browser environment checks) to evaluate candidate integrity during virtual coding interviews.

---

## Key Features

1. **Host Authentication:** Host login is password-protected using standard password checks (default password: `admin123`, configurable via `HOST_PASSWORD` environment variable) and Flask session cookies.
2. **Signed Candidate Join Links:** Candidate access is protected by cryptographically signed invitation links with a 24-hour expiration token. Direct access without valid signatures is blocked (HTTP 403).
3. **Server-Side Socket Validation:** Web Socket connection requests (`join_meeting` event) are validated server-side to ensure the candidate or host session matches the target meeting room.
4. **Deduplicated Dashboard Templates:** The host and candidate dashboards share a modular Jinja base layout (`templates/base_dashboard.html`), making the frontend DRY and maintainable.
5. **Frontend Modularity:** The frontend code is cleanly split into cohesive modules:
   - `ui.js`: DOM references, UI state managers, toolbar auto-hide, and timer.
   - `webrtc.js`: Camera/mic stream access, screen sharing, socket bindings, and WebRTC peer signaling.
   - `proctoring.js`: Proctoring logic, event checks, VPN/Proxy scans, typing biometrics, and the browser-based Audio Fingerprinting (AFP) engine.
   - `gaze.js`: MediaPipe model execution and drawing the pupil tracker coordinates.
6. **Robust Testing Suite:** Unit tests for Flask endpoints, signature generation/validation, and room joining controls.

---

## Setup & Local Development

### 1. Prerequisites
- Python 3.8 to 3.11.
- A functional webcam and microphone.
- [mkcert](https://github.com/FiloSottile/mkcert) for locally trusted SSL development certificates (included in the workspace as `mkcert.exe` for Windows).

### 2. Installation
1. Initialize the virtual environment and install dependencies:
   ```powershell
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. SSL Certificate Generation:
   Web browsers require an HTTPS connection to access the webcam and microphone APIs. Standard local HTTPS certificates (`ssl.crt` and `ssl.key`) are pre-generated. If you need to regenerate them:
   ```powershell
   .\mkcert.exe -install
   .\mkcert.exe localhost 127.0.0.1 ::1
   # Rename the output cert and key files to ssl.crt and ssl.key in the root directory
   ```

### 3. Running the Server
Start the Flask-SocketIO application server:
```powershell
.venv\Scripts\python app.py
```
Upon start, the server outputs the URLs and developer host credentials in the console log:
```
============================================================
  Intervue Backend     ->  https://127.0.0.1:5000
  Dashboard            ->  https://127.0.0.1:5000/host_dashboard
  Host Credentials     ->  admin123 (HOST_PASSWORD)
============================================================
```

### 4. Running the Tests
Execute the Python unit testing suite:
```powershell
.venv\Scripts\python -m unittest discover -s tests
```

---

## Usage Workflow

1. Open `https://127.0.0.1:5000/login/host` in your web browser.
2. Enter your name and password (`admin123` by default) to create the meeting room.
3. Copy the **Candidate Invitation Link**. This is a signed invitation link containing a cryptographic verification signature (`sig`) and expiry timestamp (`expires`).
4. Click **Join as Host** to open the Host Dashboard.
5. In an Incognito window (or a separate browser), open the copied **Candidate Invitation Link**.
6. The candidate enters their name and joins the meeting room. The candidate will see their video feed, while the host sees the candidate's video, live gaze tracking popup, and proctoring telemetry logs.
