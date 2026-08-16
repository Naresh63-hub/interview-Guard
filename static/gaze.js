// ─── ML Processing Loop ───────────────────────────────────────────────────────
const processingCanvas = document.createElement("canvas");
const ctx = processingCanvas.getContext("2d", { willReadFrequently: true });

const ML_CONFIG = {
    // Frames are only SENT at 1 fps (gazeFrameIntervalMs), so drawing at 5fps
    // just wasted canvas cycles. The loop now ticks at 1 fps; each tick draws
    // the newest frame and sends it if the previous request has completed.
    processFPS: 1,
    isProcessing: false,
    intervalId: null,
    lastGazeFrameSentAt: 0,
    gazeFrameIntervalMs: 1000, // At most 1 frame-analysis request per second
    frameInFlight: false,      // never stack requests — one at a time
    
    // False positive reduction settings
    confidenceThreshold: 0.5,      // Minimum confidence for detection
    confidenceThresholdHigh: 0.7,  // High confidence threshold
    confidenceThresholdLow: 0.3,   // Low confidence threshold
    minFaceSize: 0.1,              // Minimum face size relative to frame
    maxFaceSize: 0.8,              // Maximum face size relative to frame
    falsePositiveReduction: true,  // Enable FP reduction algorithms
    consecutiveViolations: 3,       // Number of consecutive violations before alert
    cooldownPeriod: 5000,           // Cooldown period between same violation type (ms)
    lastViolationType: null,
    lastViolationTime: 0,
    violationHistory: [],           // Track recent violations for pattern analysis
    
    // Adaptive processing settings
    adaptiveProcessing: true,      // Enable adaptive processing based on CPU
    cpuUsageThreshold: 80,        // CPU usage threshold to reduce processing
    currentCPUUsage: 0,
    adaptiveFrameRate: 1,         // Dynamically adjusted frame rate
};

function isCandidateProctoringRole() {
    return typeof userRole !== "undefined" && userRole === "candidate";
}

function startMLProcessing() {
    // ML processing only runs on candidate side - host receives data via socket
    if (!isCandidateProctoringRole()) {
        console.log("ML processing runs on candidate side only. Host receives data via socket.");
        return;
    }
    if (ML_CONFIG.isProcessing || !backendAvailable) return;

    // Performance: cap analysis resolution at 640x480 (MediaPipe + YOLO run
    // fine at this size). This cuts per-frame payload size ~4x vs 1280x720.
    const MAX_ANALYSIS_WIDTH = 640;
    const MAX_ANALYSIS_HEIGHT = 480;
    const vw = videoElement.videoWidth || 1280;
    const vh = videoElement.videoHeight || 720;
    const scale = Math.min(
        1,
        MAX_ANALYSIS_WIDTH / vw,
        MAX_ANALYSIS_HEIGHT / vh,
    );
    processingCanvas.width = Math.max(1, Math.round(vw * scale));
    processingCanvas.height = Math.max(1, Math.round(vh * scale));

    ML_CONFIG.isProcessing = true;
    
    // Use adaptive frame rate if enabled
    const frameRate = ML_CONFIG.adaptiveProcessing ? ML_CONFIG.adaptiveFrameRate : ML_CONFIG.processFPS;
    
    ML_CONFIG.intervalId = setInterval(
        () => {
            checkCPUUsage(); // Check CPU usage for adaptive processing
            processFrame();
        },
        1000 / frameRate,
    );
    console.log(
        `[ML] Processing started at ${frameRate} fps (adaptive: ${ML_CONFIG.adaptiveProcessing})`,
    );
        `ML processing loop started at ${processingCanvas.width}x${processingCanvas.height}.`,
    );
}

function stopMLProcessing() {
    if (ML_CONFIG.intervalId) clearInterval(ML_CONFIG.intervalId);
    ML_CONFIG.isProcessing = false;
    ML_CONFIG.frameInFlight = false;
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
    if (ML_CONFIG.frameInFlight) return; // previous request still running
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
    if (ML_CONFIG.frameInFlight) return;
    ML_CONFIG.frameInFlight = true;

    try {
        // Combined /analyze endpoint: the backend runs MediaPipe ONCE and
        // returns gaze + liveness together — one HTTP request, one inference
        // (previously /gaze-frame and /liveness-frame each ran MediaPipe on
        // ~the same frame, ~1.3 extra inferences/sec).
        //
        // Legacy wire format (old browsers, encode failure): base64 data-URL
        // in a JSON body. Kept in one place so the two paths can't drift.
        const sendBase64 = () =>
            fetch(apiUrl("/analyze"), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    image: processingCanvas.toDataURL("image/jpeg", 0.82),
                    role: "candidate",
                    meetingId: MEETING_ID,
                }),
            });

        // Binary upload: canvas.toBlob yields a JPEG Blob — ~25% fewer bytes
        // than the base64 data-URL and no client-side base64 encode. The
        // backend decodes raw bytes (Content-Type: image/jpeg); meeting id
        // travels in a header since the body is no longer JSON. Feature-detect
        // toBlob BEFORE calling: on old browsers it's undefined and calling it
        // would throw (and wrongly take the backend offline via the catch).
        let response;
        if (typeof processingCanvas.toBlob === "function") {
            const blob = await new Promise((resolve) => {
                processingCanvas.toBlob(resolve, "image/jpeg", 0.82);
            });
            if (blob && blob.size > 0) {
                response = await fetch(apiUrl("/analyze"), {
                    method: "POST",
                    headers: {
                        "Content-Type": "image/jpeg",
                        "X-Meeting-Id": MEETING_ID,
                    },
                    body: blob,
                });
            } else {
                response = await sendBase64();
            }
        } else {
            response = await sendBase64();
        }

        if (!response.ok) {
            // Verdict-level rejection (bad frame, decode error) — skip this
            // frame but KEEP the loop alive. Only network-level failures
            // below take the backend offline.
            const errData = await response.json().catch(() => null);
            if (Math.random() < 0.1) {
                console.warn(
                    "Frame analysis rejected:",
                    errData && errData.error ? errData.error : `HTTP ${response.status}`,
                );
            }
            return;
        }

        const data = await response.json();
        totalGazeFrames++;
        
        // Apply false positive reduction
        const processedData = applyFalsePositiveReduction(data);
        updateGazeUI(processedData);

        // Liveness rides the same response (see proctoring.js handleLivenessResult)
        if (processedData.liveness && typeof window.handleLivenessResult === "function") {
            window.handleLivenessResult(processedData.liveness);
        }

        // Update Live Eye Analysis PiP locally (backend throttles re-encode to 2s)
        if (processedData.annotatedFrame) {
            const img = document.getElementById("annotated-gaze-feed");
            if (img) img.src = processedData.annotatedFrame;
        }

        // Broadcast gaze data to other participants (interviewer) via socket
        if (typeof socket !== "undefined" && socket && socket.connected) {
            socket.emit("gaze_update", {
                meetingId: MEETING_ID,
                gazeData: processedData
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
    } finally {
        ML_CONFIG.frameInFlight = false;
    }
}

// ─── False Positive Reduction ───────────────────────────────────────────────────
function applyFalsePositiveReduction(data) {
    if (!ML_CONFIG.falsePositiveReduction) return data;
    
    const processed = { ...data };
    const now = Date.now();
    
    // Confidence threshold filtering
    if (processed.gaze && processed.gaze.confidence) {
        if (processed.gaze.confidence < ML_CONFIG.confidenceThreshold) {
            processed.gaze.direction = 'uncertain';
            processed.gaze.confidence = ML_CONFIG.confidenceThreshold;
        }
    }
    
    // Face size validation
    if (processed.face && processed.face.size) {
        if (processed.face.size < ML_CONFIG.minFaceSize || processed.face.size > ML_CONFIG.maxFaceSize) {
            processed.face.detected = false;
            processed.face.reliable = false;
        }
    }
    
    // Consecutive violation check
    if (processed.violation && processed.violation.type) {
        const violationType = processed.violation.type;
        
        // Check cooldown period
        if (ML_CONFIG.lastViolationType === violationType && 
            now - ML_CONFIG.lastViolationTime < ML_CONFIG.cooldownPeriod) {
            processed.violation.suppressed = true;
            processed.violation.reason = 'cooldown_period';
            return processed;
        }
        
        // Check consecutive violations
        ML_CONFIG.violationHistory.push({
            type: violationType,
            time: now
        });
        
        // Keep only recent violations
        ML_CONFIG.violationHistory = ML_CONFIG.violationHistory.filter(
            v => now - v.time < 30000 // 30 second window
        );
        
        const recentViolations = ML_CONFIG.violationHistory.filter(
            v => v.type === violationType
        ).length;
        
        if (recentViolations < ML_CONFIG.consecutiveViolations) {
            processed.violation.suppressed = true;
            processed.violation.reason = 'insufficient_consecutive';
            return processed;
        }
        
        // Valid violation - update tracking
        ML_CONFIG.lastViolationType = violationType;
        ML_CONFIG.lastViolationTime = now;
    }
    
    return processed;
}

// ─── Adaptive Processing ───────────────────────────────────────────────────────
function checkCPUUsage() {
    if (!ML_CONFIG.adaptiveProcessing) return;
    
    // Estimate CPU usage by measuring frame processing time
    const start = performance.now();
    
    // Simulate processing by running a quick calculation
    for (let i = 0; i < 1000; i++) {
        Math.sqrt(i);
    }
    
    const end = performance.now();
    const processingTime = end - start;
    
    // Estimate CPU usage based on processing time
    ML_CONFIG.currentCPUUsage = Math.min(100, (processingTime / 10) * 100);
    
    // Adjust frame rate based on CPU usage
    if (ML_CONFIG.currentCPUUsage > ML_CONFIG.cpuUsageThreshold) {
        // Reduce frame rate if CPU is high
        ML_CONFIG.adaptiveFrameRate = Math.max(0.5, ML_CONFIG.processFPS * 0.5);
        console.log('[Adaptive] Reduced frame rate due to high CPU usage');
    } else {
        // Restore normal frame rate
        ML_CONFIG.adaptiveFrameRate = ML_CONFIG.processFPS;
    }
}


