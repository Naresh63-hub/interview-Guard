// ─── ML Processing Loop ───────────────────────────────────────────────────────
const processingCanvas = document.createElement("canvas");
const ctx = processingCanvas.getContext("2d", { willReadFrequently: true });

const ML_CONFIG = {
    // FIX: Reduced to 5 FPS to avoid overwhelming the Flask backend
    processFPS: 5,
    isProcessing: false,
    intervalId: null,
    lastGazeFrameSentAt: 0,
    gazeFrameIntervalMs: 1000, // At most 1 gaze request per second
};

function isCandidateProctoringRole() {
    return typeof userRole !== "undefined" && userRole === "candidate";
}

function startMLProcessing() {
    if (!isCandidateProctoringRole()) {
        console.log("ML processing suppressed for non-candidate role.");
        return;
    }
    if (ML_CONFIG.isProcessing || !backendAvailable) return;

    processingCanvas.width = videoElement.videoWidth || 640;
    processingCanvas.height = videoElement.videoHeight || 480;

    ML_CONFIG.isProcessing = true;
    ML_CONFIG.intervalId = setInterval(
        processFrame,
        1000 / ML_CONFIG.processFPS,
    );
    console.log("ML processing loop started.");
}

function stopMLProcessing() {
    if (ML_CONFIG.intervalId) clearInterval(ML_CONFIG.intervalId);
    ML_CONFIG.isProcessing = false;
    console.log("ML processing loop stopped.");
}

async function processFrame() {
    if (!isCandidateProctoringRole()) return;
    if (!videoElement || videoElement.paused || videoElement.ended) return;
    if (videoElement.readyState < 2) return; // FIX: Wait until video has data

    ctx.drawImage(
        videoElement,
        0,
        0,
        processingCanvas.width,
        processingCanvas.height,
    );

    const now = Date.now();
    if (now - ML_CONFIG.lastGazeFrameSentAt >= ML_CONFIG.gazeFrameIntervalMs) {
        ML_CONFIG.lastGazeFrameSentAt = now;
        sendFrameForGazeAnalysis();
    }
}

// Start ML once video is actually playing
// Phase 3 Audio logic handles ML initialization in its own playing listener.

// ─── Gaze Analysis ────────────────────────────────────────────────────────────
async function sendFrameForGazeAnalysis() {
    if (!isCandidateProctoringRole()) return;
    if (!backendAvailable) return;

    try {
        // IMPROVED: Use higher JPEG quality (0.85) to yield better Face Mesh & object detection accuracy
        const image = processingCanvas.toDataURL("image/jpeg", 0.85);

        const response = await fetch(apiUrl("/gaze-frame"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ image, role: "candidate" }),
        });

        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const data = await response.json();
        totalGazeFrames++;
        updateGazeUI(data);
        
        // Update Live Eye Analysis PiP locally
        if (data.annotatedFrame) {
            const img = document.getElementById("annotated-gaze-feed");
            if (img) img.src = data.annotatedFrame;
        }

        // Broadcast gaze data to other participants (interviewer) via socket
        if (typeof socket !== "undefined" && socket && socket.connected) {
            socket.emit("gaze_update", {
                meetingId: MEETING_ID,
                gazeData: data
            });
        }
    } catch (error) {
        backendAvailable = false;
        stopMLProcessing();
        if (Math.random() < 0.1) {
            console.warn("Gaze frame error (sampled):", error.message);
        }
        setGazeAlert(
            "Gaze backend offline - start python app.py on port 5000",
            true,
        );
    }
}

