// ─── WebRTC – Camera & SocketIO ────────────────────────────────────────────────
let socket = null;
let peerConnection = null;
let remoteSid = null;
let participantCount = 1;
const MEETING_ID = window.MEETING_ID;

const ADVANCED_ICE_SERVERS = {
    iceServers: [
        { urls: "stun:stun.l.google.com:19302" },
        { urls: "stun:stun1.l.google.com:19302" },
        { urls: "stun:stun2.l.google.com:19302" },
        { urls: "stun:global.stun.twilio.com:3478" }
    ],
    iceTransportPolicy: "all",
    bundlePolicy: "max-bundle"
};

let makingOffer = false;
let ignoreOffer = false;

function createPeerConnection() {
    const pc = new RTCPeerConnection(ADVANCED_ICE_SERVERS);

    pc.onicecandidate = (evt) => {
        if (evt.candidate && socket && socket.connected) {
            socket.emit("ice_candidate", {
                meetingId: MEETING_ID,
                candidate: evt.candidate,
                to: remoteSid,
            });
        }
    };

    pc.onconnectionstatechange = () => {
        console.log("WebRTC Connection State:", pc.connectionState);
        if (pc.connectionState === 'disconnected' || pc.connectionState === 'failed') {
            if (typeof UI_UPDATER !== 'undefined') {
                UI_UPDATER.addAuditAlert("Network Issue", "Video connection dropped. Attempting recovery.", "", true);
            }
            // Auto-recovery
            if (remoteSid && pc.connectionState === 'failed') {
                createAndSendOffer();
            }
        } else if (pc.connectionState === 'connected') {
            if (typeof UI_UPDATER !== 'undefined') {
                UI_UPDATER.addAuditAlert("Connection Restored", "Video connection stabilized.");
            }
        }
    };

    pc.ontrack = (evt) => {
        console.log("Received remote track:", evt.track.kind, "Role:", userRole);
        
        if (evt.track.kind === 'video') {
            // Only show remote video in main element for host, not for candidate
            if (userRole === "host" || userRole === "interviewer") {
                videoElement.srcObject = evt.streams[0];
                videoElement.muted = false; // Unmute remote
                videoElement.style.background = ""; // Clear any placeholder background
                
                console.log("Setting remote video for host from candidate");
                
                // Aggressive autoplay handler for mobile/strict browsers
                const playPromise = videoElement.play();
                if (playPromise !== undefined) {
                    playPromise.catch(error => {
                        console.warn("Autoplay blocked. Injecting manual play button overlay.", error);
                        let playBtn = document.getElementById("force-play-btn");
                        if (!playBtn) {
                            playBtn = document.createElement("button");
                            playBtn.id = "force-play-btn";
                            playBtn.textContent = "Tap to Play Video";
                            playBtn.style.cssText = "position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);z-index:9999;padding:15px 30px;font-size:1.2rem;border-radius:30px;background:var(--gradient-purple);color:white;border:none;cursor:pointer;box-shadow:0 10px 25px rgba(0,0,0,0.5);";
                            
                            playBtn.onclick = () => {
                                videoElement.play();
                                playBtn.remove();
                            };
                            
                            if (videoElement.parentElement) {
                                videoElement.parentElement.appendChild(playBtn);
                            }
                        }
                    });
                }
            } else {
                console.log("Candidate should not receive remote video, keeping local video");
            }
        } else if (evt.track.kind === 'audio') {
            if (userRole === "host" || userRole === "interviewer") {
                if (!videoElement.srcObject) {
                    videoElement.srcObject = evt.streams[0];
                }
                videoElement.muted = false;
                videoElement.play().catch(e => console.log("Audio autoplay prevented", e));
            }
        }
    };

    if (localStream) {
        localStream.getTracks().forEach((track) =>
            pc.addTrack(track, localStream)
        );
    }

    return pc;
}

async function createAndSendOffer() {
    if (!peerConnection) return;
    try {
        makingOffer = true;
        const offer = await peerConnection.createOffer();
        await peerConnection.setLocalDescription(offer);
        socket.emit("offer", {
            meetingId: MEETING_ID,
            sdp: peerConnection.localDescription,
            to: remoteSid,
        });
    } catch (err) {
        console.error("createOffer failed:", err);
    } finally {
        makingOffer = false;
    }
}

function setupSocket() {
    if (typeof io === "undefined" || !MEETING_ID) return;

    socket = io(API_ORIGIN, {
        transports: ["websocket", "polling"],
        reconnectionAttempts: 10,
        reconnectionDelay: 1000,
        timeout: 20000
    });

    socket.on("connect", () => {
        socket.emit("join_meeting", {
            meetingId: MEETING_ID,
            userName: currentDisplayName,
            userRole: userRole,
        });
        socket.emit("media_state_change", {
            meetingId: MEETING_ID,
            role: userRole,
            isMuted: isMuted,
            isVideoStopped: isVideoStopped
        });
    });

    socket.on("room_info", (data) => {
        const peers = data.participants || [];
        participantCount = peers.length;
        if (participantCount >= 2 && !sessionStartTime) {
            sessionStartTime = Date.now();
        }
        if (data.proctoringSettings) {
            window.proctoringSettings = data.proctoringSettings;
            console.log("[Socket] Received proctoring settings from room info:", window.proctoringSettings);
        }
        if (data.networkStats && (userRole === "host" || userRole === "interviewer")) {
            const ispEl = document.getElementById("stat-network-isp");
            const vpnEl = document.getElementById("stat-network-vpn");
            if (ispEl) {
                ispEl.textContent = `${data.networkStats.isp} (${data.networkStats.location})`;
                ispEl.className = "white-text";
            }
            if (vpnEl) {
                vpnEl.textContent = data.networkStats.isVpnOrProxy ? "VPN/Proxy Detected" : "Secure (Direct)";
                vpnEl.className = data.networkStats.isVpnOrProxy ? "danger-text" : "green-text";
            }
        }
        console.log("[Socket] Room info received:", data);
    });

    socket.on("session_status", (data) => {
        console.log("[Socket] Session status update received:", data);
        if (data.bothPresent && (userRole === "host" || userRole === "interviewer")) {
            console.log("[Host] Session started - both participants connected");
            if (typeof UI_UPDATER !== 'undefined') {
                UI_UPDATER.addAuditAlert("Session Started", "Both interviewer and candidate are now connected.");
            }
        }
        
        // Enable kiosk mode for candidates when session starts
        if (data.bothPresent && userRole === "candidate") {
            console.log("[Candidate] Enabling kiosk mode for secure proctoring");
            // Dispatch event to extension
            const kioskEvent = new CustomEvent('proctoring_kiosk_enable');
            window.dispatchEvent(kioskEvent);
            
            if (typeof UI_UPDATER !== 'undefined') {
                UI_UPDATER.addAuditAlert("Kiosk Mode Enabled", "Full-screen lock activated for secure proctoring");
            }
        }
    });

    socket.on("user_joined", async (data) => {
        console.log("[Socket] User joined:", data);
        remoteSid = data.sid || data.socketId || data.id;
        participantCount = Math.max(participantCount, 2);
        if (!sessionStartTime) sessionStartTime = Date.now();
        
        UI_UPDATER.addAuditAlert("Participant Joined", `${data.userName || 'Candidate'} joined the meeting.`);
        
        peerConnection = createPeerConnection();
        await createAndSendOffer();
    });

    socket.on("offer", async (data) => {
        remoteSid = data.from;
        
        if (!peerConnection) {
            peerConnection = createPeerConnection();
        }
        
        const offerCollision = makingOffer || peerConnection.signalingState !== "stable";
        ignoreOffer = !peerConnection && offerCollision;
        
        if (ignoreOffer) return;

        try {
            await peerConnection.setRemoteDescription(new RTCSessionDescription(data.sdp));
            const answer = await peerConnection.createAnswer();
            await peerConnection.setLocalDescription(answer);
            socket.emit("answer", {
                meetingId: MEETING_ID,
                sdp: peerConnection.localDescription,
                to: remoteSid,
            });
            if (!sessionStartTime) sessionStartTime = Date.now();
        } catch (err) {
            console.error("offer handler failed:", err);
        }
    });

    socket.on("answer", async (data) => {
        if (!peerConnection) return;
        try {
            await peerConnection.setRemoteDescription(new RTCSessionDescription(data.sdp));
        } catch (err) { 
            console.error("answer handling failed:", err);
        }
    });

    socket.on("ice_candidate", async (data) => {
        if (!peerConnection || !data.candidate) return;
        try {
            await peerConnection.addIceCandidate(new RTCIceCandidate(data.candidate));
        } catch (err) { 
            if (!ignoreOffer) {
                console.error("ice candidate failed:", err);
            }
        }
    });
    
    socket.on("user_left", (data) => {
        UI_UPDATER.addAuditAlert("Participant Left", `${data.userName || 'Candidate'} left the meeting.`, "", true);
        if (peerConnection) {
            peerConnection.close();
            peerConnection = null;
        }
        // Return to local stream view only for candidates
        if (userRole === "candidate" && localStream) {
            videoElement.srcObject = localStream;
            videoElement.muted = true;
            videoElement.style.background = "";
            videoElement.play().catch(e => console.log(e));
        } else if (userRole === "host" || userRole === "interviewer") {
            // Host shows placeholder when participant leaves
            videoElement.srcObject = null;
            videoElement.style.background = "linear-gradient(135deg, #1a1a2e 0%, #16213e 100%)";
            console.log("Host showing placeholder after participant left");
        }
    });

    socket.on("audit_event", (data) => {
        if (typeof UI_UPDATER !== "undefined" && UI_UPDATER.addAuditAlert) {
            // pass fromRemote = true to prevent infinite loop
            UI_UPDATER.addAuditAlert(data.title, data.message, data.confidence, data.isCritical, true);
        }
    });

    socket.on("badge_update", (data) => {
        if (data.role !== userRole) {
            const el = document.getElementById(data.badgeId);
            const labelEl = document.getElementById(data.badgeId + "-label");
            const iconEl = document.getElementById(data.badgeId + "-icon");
            // Set broadcast = false to prevent infinite loops
            setBadge(el, labelEl, iconEl, data.text, data.tone, data.iconClass, false);
        }
    });

    socket.on("settings_update", (data) => {
        console.log("[Socket] Settings update received:", data);
        if (data && data.settings) {
            window.proctoringSettings = data.settings;
            console.log("[Socket] Updated proctoring settings:", window.proctoringSettings);
            
            // Dispatch event for candidate UI
            window.dispatchEvent(new CustomEvent('settings_update', {
                detail: { settings: data.settings }
            }));
            
            // For candidate, show notification about settings change
            if (userRole === "candidate") {
                const notification = document.createElement('div');
                notification.className = 'settings-notification';
                notification.style.cssText = "position:fixed;top:20px;right:20px;background:rgba(108,78,177,0.9);color:white;padding:12px 20px;border-radius:8px;z-index:1000;animation:slideIn 0.3s ease-out;";
                notification.textContent = "Host updated proctoring settings";
                document.body.appendChild(notification);
                
                setTimeout(() => {
                    notification.style.animation = "slideOut 0.3s ease-out";
                    setTimeout(() => notification.remove(), 300);
                }, 3000);
            }
        }
    });

    socket.on("remote_control", (data) => {
        console.log("[Socket] Remote control received:", data);
        if (userRole === "candidate") {
            if (data.action === "mute") {
                const targetMute = !!data.value;
                console.log("[Remote Control] Mute command - current:", isMuted, "target:", targetMute);
                if (isMuted !== targetMute) {
                    toggleMute();
                    console.log("[Remote Control] Mute toggled to:", isMuted);
                    
                    // Dispatch event for UI feedback
                    window.dispatchEvent(new CustomEvent('remote_control_applied', {
                        detail: { action: 'mute', value: isMuted }
                    }));
                }
            } else if (data.action === "camera") {
                const targetVideoStopped = !!data.value;
                console.log("[Remote Control] Camera command - current:", isVideoStopped, "target:", targetVideoStopped);
                if (isVideoStopped !== targetVideoStopped) {
                    toggleVideo();
                    console.log("[Remote Control] Camera toggled to:", isVideoStopped);
                    
                    // Dispatch event for UI feedback
                    window.dispatchEvent(new CustomEvent('remote_control_applied', {
                        detail: { action: 'camera', value: isVideoStopped }
                    }));
                }
            }
        }
    });

    socket.on("media_state_change", (data) => {
        console.log("[Socket] Media state change received:", data);
        if (data.role === "candidate") {
            window.candidateMicMuted = data.isMuted;
            window.candidateVideoStopped = data.isVideoStopped;
            console.log("[Socket] Updated candidate state - muted:", window.candidateMicMuted, "video stopped:", window.candidateVideoStopped);
            if (userRole === "host" || userRole === "interviewer") {
                updateModeratorButtonsUI();
                
                // If candidate video is stopped, show placeholder on host side
                if (window.candidateVideoStopped && videoElement && videoElement.srcObject) {
                    // Keep the video element but show a placeholder overlay
                    console.log("[Socket] Candidate video stopped, showing placeholder on host");
                }
            }
        }
    });

    socket.on("browser_stats_update", (data) => {
        console.log("[Socket] Browser stats update received:", data);
        if (userRole === "host" || userRole === "interviewer") {
            const tabEl = document.getElementById("stat-tab-switches");
            const copyEl = document.getElementById("stat-copy-paste");
            if (tabEl) {
                tabEl.textContent = (data.tabSwitchCount || 0).toString();
                tabEl.className = data.tabSwitchCount > 0 ? "danger-text" : "gray-text";
            }
            if (copyEl) {
                copyEl.textContent = (data.copyPasteCount || 0).toString();
                copyEl.className = data.copyPasteCount > 0 ? "danger-text" : "gray-text";
            }
        }
    });

    socket.on("audio_metrics_update", (data) => {
        if (userRole === "host" || userRole === "interviewer") {
            const voiceBar = document.getElementById('voice-level-bar');
            const voiceVal = document.getElementById('voice-level-value');
            const voiceStatus = document.getElementById('voice-status');
            const pitchEl = document.getElementById('voice-pitch');
            const stressEl = document.getElementById('voice-stress');

            if (voiceBar) voiceBar.style.width = `${data.level}%`;
            if (voiceVal) voiceVal.textContent = `${data.level}%`;
            if (voiceStatus) {
                voiceStatus.textContent = data.voiceStatusText;
                voiceStatus.className = data.voiceStatusClass;
            }
            if (pitchEl) pitchEl.textContent = data.pitchText;
            if (stressEl) {
                stressEl.textContent = data.stressText;
                stressEl.className = data.stressClass;
            }

            if (audioBars) {
                const height = Math.min(100, Math.max(10, data.level * 2));
                audioBars.style.height = `${height}%`;
                audioBars.style.visibility = window.candidateMicMuted ? "hidden" : "visible";
            }
        }
    });

    socket.on("network_stats_update", (data) => {
        console.log("[Socket] Network stats update received:", data);
        if (userRole === "host" || userRole === "interviewer") {
            const ispEl = document.getElementById("stat-network-isp");
            const vpnEl = document.getElementById("stat-network-vpn");
            if (ispEl) {
                ispEl.textContent = `${data.isp || "Unknown"} (${data.location || "Unknown"})`;
                ispEl.className = "white-text";
            }
            if (vpnEl) {
                vpnEl.textContent = data.isVpnOrProxy ? "VPN/Proxy Detected" : "Secure (Direct)";
                vpnEl.className = data.isVpnOrProxy ? "danger-text" : "green-text";
            }
        }
    });

    socket.on("gaze_update", (data) => {
        console.log("[Socket] Gaze update received:", data);
        if (userRole === "host" || userRole === "interviewer") {
            // Mark proctoring as active when host receives first gaze data
            window.proctoringActive = true;
            updateGazeUI(data.gazeData);
            if (data.gazeData && data.gazeData.annotatedFrame) {
                const img = document.getElementById("annotated-gaze-feed");
                if (img) img.src = data.gazeData.annotatedFrame;
            }
        }
    });
}

// ─── Camera Permission Helpers ────────────────────────────────────────────
// getUserMedia only exists in a "secure context" (HTTPS, or http://localhost /
// 127.0.0.1). Opening the app via a LAN IP (http://10.x.x.x:5000) makes
// navigator.mediaDevices undefined, so the browser locks the camera no matter
// what the OS/browser permission settings say.
function cameraErrorInfo(error) {
    const name = (error && error.name) || "";
    const message = (error && error.message) || "";
    switch (name) {
        case "NotSecureContext":
            return {
                title: "Camera is locked — page is not secure",
                message: "Your browser blocks camera & microphone access on this address.",
                hint: "This looks like a network/LAN address (http://10.x.x.x:5000), which browsers treat as insecure. Open the app at http://localhost:5000, or serve it over HTTPS, to use the camera.",
                retryLabel: "Retry Camera",
            };
        case "NotAllowedError":
        case "PermissionDeniedError":
        case "SecurityError":
            return {
                title: "Camera & microphone permission blocked",
                message: "The browser is preventing this site from using your camera/microphone.",
                hint: "Click the camera icon (or padlock) in the address bar → choose “Allow” → then press Retry. If no permission prompt ever appears, the page may not be secure — use http://localhost:5000 or HTTPS.",
                retryLabel: "I granted access — Retry",
            };
        case "NotFoundError":
        case "DevicesNotFoundError":
            return {
                title: "No camera found",
                message: "No camera device was detected on this machine.",
                hint: "Plug in / enable a camera, make sure no other app is using it, then press Retry.",
                retryLabel: "Retry Camera",
            };
        case "NotReadableError":
        case "TrackStartError":
        case "AbortError":
            return {
                title: "Camera is busy or unreadable",
                message: "Your camera could not be started — it is probably in use by another application.",
                hint: "Close apps that may hold the camera (Zoom, Teams, Meet, OBS, the camera app), then press Retry.",
                retryLabel: "Retry Camera",
            };
        case "OverconstrainedError":
            return {
                title: "Camera unsupported",
                message: "Your camera did not start with the requested settings.",
                hint: "Press Retry to fall back to basic camera settings.",
                retryLabel: "Retry Camera",
            };
        default:
            return {
                title: "Camera could not start",
                message: `Unexpected error: ${message || name || "unknown"}`,
                hint: "Press Retry to try starting the camera again.",
                retryLabel: "Retry Camera",
            };
    }
}

function showCameraErrorOverlay(info) {
    // Surface the cause + fix in a visible overlay instead of the tiny status
    // pill, and give the user a one-click retry after they fix permissions.
    let overlay = document.getElementById("camera-error-overlay");
    if (!overlay) {
        overlay = document.createElement("div");
        overlay.id = "camera-error-overlay";
        overlay.style.cssText = "position:fixed;top:0;left:0;right:0;bottom:0;z-index:2147483000;background:rgba(10,10,20,.72);display:flex;align-items:center;justify-content:center;padding:16px;font-family:Inter,system-ui,sans-serif;";
        document.body.appendChild(overlay);
    }
    const box = document.createElement("div");
    box.style.cssText = "max-width:460px;width:100%;background:#fff;color:#111827;border-radius:14px;padding:24px;box-shadow:0 20px 60px rgba(0,0,0,.4);";
    box.innerHTML = `
        <div style="font-size:1.05rem;font-weight:700;margin-bottom:8px;">⚠️ ${info.title}</div>
        <div style="font-size:.92rem;line-height:1.5;color:#374151;margin-bottom:8px;">${info.message}</div>
        ${info.hint ? `<div style="font-size:.85rem;line-height:1.45;color:#6b7280;background:#f3f4f6;border-radius:8px;padding:10px 12px;margin-bottom:14px;">${info.hint}</div>` : ""}
        <button type="button" style="width:100%;padding:11px 14px;border:none;border-radius:10px;background:#7c3aed;color:#fff;font-size:.95rem;font-weight:600;cursor:pointer;">${info.retryLabel}</button>
    `;
    overlay.innerHTML = "";
    overlay.appendChild(box);
    box.querySelector("button").onclick = () => {
        overlay.remove();
        startWebcam();
    };
}

async function startWebcam() {
    try {
        // Camera APIs only exist in secure contexts (HTTPS / localhost).
        // Detect this up-front and explain it, instead of a generic "denied".
        if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
            console.error("startWebcam: navigator.mediaDevices unavailable — page is not in a secure context (HTTPS or localhost).");
            setGazeAlert("Camera locked: open http://localhost:5000 or HTTPS", true);
            if (typeof UI_UPDATER !== 'undefined') {
                UI_UPDATER.addAuditAlert("Hardware Error", "Camera locked by browser: page is not served over HTTPS/localhost.", "", true);
            }
            showCameraErrorOverlay(cameraErrorInfo({ name: "NotSecureContext" }));
            return;
        }

        try {
            localStream = await navigator.mediaDevices.getUserMedia({
                video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: "user" },
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true
                },
            });
        } catch {
            console.warn("High-res video failed, falling back to basic constraints.");
            try {
                localStream = await navigator.mediaDevices.getUserMedia({
                    video: true,
                    audio: true,
                });
            } catch {
                console.warn("Video+Audio failed, falling back to Video only.");
                localStream = await navigator.mediaDevices.getUserMedia({
                    video: true,
                    audio: false,
                });
            }
        }

        // Set local stream to the new small PiP video
        const localVideo = document.getElementById("local-video");
        const pipContainer = document.getElementById("pip-container");
        
        if (localVideo) {
            localVideo.srcObject = localStream;
            localVideo.style.background = ""; // Clear any placeholder
            
            // For candidates, hide PiP since they use main video area
            if (userRole === "candidate" && pipContainer) {
                pipContainer.style.display = "none";
                console.log("[WebRTC] Hidden PiP container for candidate (using main video area)");
            } else if (pipContainer) {
                pipContainer.style.display = "block";
                console.log("[WebRTC] PiP container visible for host");
            }
            
            try { await localVideo.play(); } catch { }
        }

        // Only show local video in main area for candidates, not for hosts
        // Hosts should wait for remote video from candidate
        if (userRole === "candidate" && videoElement && !videoElement.srcObject) {
            videoElement.srcObject = localStream;
            videoElement.muted = true; // Prevent echo
            videoElement.style.background = ""; // Clear any placeholder
            try { await videoElement.play(); } catch { }
            console.log("[WebRTC] Set local video in main area for candidate");
        } else if (userRole === "host" || userRole === "interviewer") {
            // Host shows placeholder until candidate joins
            videoElement.style.background = "linear-gradient(135deg, #1a1a2e 0%, #16213e 100%)";
            console.log("Host waiting for remote video from candidate");
        }

        if (localStream.getAudioTracks().length > 0) {
            console.log("[WebRTC] Audio tracks found:", localStream.getAudioTracks().length);
            setupAudioAnalysis(localStream);
        } else {
            console.warn("[WebRTC] No audio tracks found in local stream");
        }

        updateShieldStatus(true);
        console.log("Webcam started successfully.");
        if (typeof window.scanMediaDevices === "function") {
            window.scanMediaDevices();
        }
        
    } catch (error) {
        console.error("startWebcam:", error);
        setGazeAlert("Camera access denied or locked. Please check permissions.", true);
        if (typeof UI_UPDATER !== 'undefined') {
            UI_UPDATER.addAuditAlert("Hardware Error", "Camera could not be accessed. It may be locked by another application.", "", true);
        }
        showCameraErrorOverlay(cameraErrorInfo(error));
    }
}

// ─── Audio / Video Toggles ────────────────────────────────────────────────────
function toggleMute() {
    if (!localStream) return;
    const audioTracks = localStream.getAudioTracks();

    // Remove No Mic hardblock to allow visual toggling
    if (audioTracks.length === 0) {
        console.warn("Toggling mute visually despite no audio track present.");
    }

    isMuted = !isMuted;
    audioTracks.forEach((t) => (t.enabled = !isMuted));

    if (isMuted) {
        iconMute.classList.replace("ph-microphone", "ph-microphone-slash");
        iconMute.style.color = "#ef4444";
        textMute.textContent = "Unmute";
        // Pause audio bars animation
        if (audioBars) audioBars.style.visibility = "hidden";
    } else {
        iconMute.classList.replace("ph-microphone-slash", "ph-microphone");
        iconMute.style.color = "";
        textMute.textContent = "Mute";
        if (audioBars) audioBars.style.visibility = "visible";
    }

    if (socket && socket.connected) {
        socket.emit("media_state_change", {
            meetingId: MEETING_ID,
            role: userRole,
            isMuted: isMuted,
            isVideoStopped: isVideoStopped
        });
    }
}

function toggleVideo() {
    if (!localStream) return;
    const videoTracks = localStream.getVideoTracks();

    if (videoTracks.length === 0) {
        if (textVideo) textVideo.textContent = "No Cam";
        return;
    }

    isVideoStopped = !isVideoStopped;
    videoTracks.forEach((t) => (t.enabled = !isVideoStopped));

    if (isVideoStopped) {
        iconVideo.classList.replace("ph-video-camera", "ph-video-camera-slash");
        iconVideo.style.color = "#ef4444";
        textVideo.textContent = "Start Cam";
        
        // For candidate, show placeholder when camera is off
        if (userRole === "candidate" && videoElement) {
            videoElement.style.background = "linear-gradient(135deg, #1a1a2e 0%, #16213e 100%)";
            videoElement.srcObject = null; // Clear video stream
        }
    } else {
        iconVideo.classList.replace("ph-video-camera-slash", "ph-video-camera");
        iconVideo.style.color = "";
        textVideo.textContent = "Stop Cam";
        
        // For candidate, restore video when camera is on
        if (userRole === "candidate" && videoElement) {
            videoElement.style.background = "";
            videoElement.srcObject = localStream;
            videoElement.play().catch(e => console.log("Video play error:", e));
        }
    }

    if (socket && socket.connected) {
        socket.emit("media_state_change", {
            meetingId: MEETING_ID,
            role: userRole,
            isMuted: isMuted,
            isVideoStopped: isVideoStopped
        });
    }
}

// Wire buttons
if (btnMute) btnMute.addEventListener("click", toggleMute);
if (btnVideo) btnVideo.addEventListener("click", toggleVideo);

// ─── Screen Share ──────────────────────────────────────────────────────
let screenStream = null;
let screenVideoEl = null;

const badgeScreenShare = document.getElementById("badge-screen-share");
const badgeShareLabel = document.getElementById("badge-share-label");
const badgeShareIcon = document.getElementById("badge-share-icon");

async function toggleScreenShare() {
    if (screenStream) {
        screenStream.getTracks().forEach((t) => t.stop());
        screenStream = null;
        if (screenVideoEl) {
            screenVideoEl.remove();
            screenVideoEl = null;
        }
        setBadge(badgeScreenShare, badgeShareLabel, badgeShareIcon, "Share Screen", "", "ph ph-monitor-arrow-up");
        UI_UPDATER.addAuditAlert(
            "Screen share stopped",
            "Screen sharing ended.",
            "User action",
        );
        return;
    }

    try {
        screenStream = await navigator.mediaDevices.getDisplayMedia({
            video: { cursor: "always" },
            audio: false,
        });

        screenVideoEl = document.createElement("video");
        screenVideoEl.srcObject = screenStream;
        screenVideoEl.autoplay = true;
        screenVideoEl.muted = true;
        screenVideoEl.playsInline = true;
        screenVideoEl.className = "screen-share-tile";

        const container = document.querySelector(".main-video-container");
        if (container) container.appendChild(screenVideoEl);

        setBadge(badgeScreenShare, badgeShareLabel, badgeShareIcon, "Sharing Screen", "green", "ph ph-monitor-arrow-up");

        UI_UPDATER.addAuditAlert(
            "Screen share started",
            `${currentDisplayName} is now sharing their screen.`,
            "User action",
        );

        screenStream.getVideoTracks()[0].addEventListener("ended", () => {
            toggleScreenShare();
        });
    } catch (err) {
        console.warn("Screen share cancelled or denied:", err.message);
        setBadge(badgeScreenShare, badgeShareLabel, badgeShareIcon, "Share Denied", "danger", "ph ph-monitor-arrow-up");
        setTimeout(() => {
            setBadge(badgeScreenShare, badgeShareLabel, badgeShareIcon, "Share Screen", "", "ph ph-monitor-arrow-up");
        }, 3000);
    }
}

if (badgeScreenShare) {
    badgeScreenShare.style.cursor = "pointer";
    badgeScreenShare.addEventListener("click", toggleScreenShare);
}

const btnScreenShare = document.getElementById("btn-screen-share");
if (btnScreenShare) {
    btnScreenShare.addEventListener("click", toggleScreenShare);
}

