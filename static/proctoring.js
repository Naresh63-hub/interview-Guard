// ─── Multi-Signal AI (Audio Features) ───
let currentVoiceVolume = 0; // 0-100, updated each AFP run from adjusted RMS

function isCandidateProctoringRole() {
    return typeof userRole !== "undefined" && userRole === "candidate";
}

function setupAudioAnalysis(stream) {
    console.log("[Audio] Setting up audio analysis for role:", userRole);
    // Audio analysis runs on candidate side and broadcasts to host via socket
    if (!isCandidateProctoringRole()) {
        console.log("[Audio] Audio analysis runs on candidate side only. Host receives data via socket.");
        return;
    }
    
    // Initialize Audio Fingerprinting for candidate
    initAudioFingerprinting();
}

// ─── Multi-Signal AI (Confidence Scoring) ───
// Combined confidence = attention (gaze) + voice + multi-signal agreement from
// the fusion layer (see ui.js). The detection-confidence term drops as
// independent cheating signals — each with its own per-detector confidence —
// agree inside the fusion window, so a lone weak signal can't tank the gauge.
setInterval(() => {
    // For host, run this even if ML processing is not active locally (host receives data via socket)
    if (userRole !== "candidate" && userRole !== "host" && userRole !== "interviewer") return;
    if (userRole === "candidate" && (!ML_CONFIG.isProcessing || !totalGazeFrames)) return;
    
    // Gaze Score (100% minus the percentage of time looking away)
    const gazeScore = Math.max(0, 100 - ((lookAwayFrames / totalGazeFrames) * 100));
    
    // Voice Score (normalized volume — currentVoiceVolume is already 0-100)
    const voiceScore = Math.min(100, Math.max(0, currentVoiceVolume));
    
    // Detection confidence from the fusion window: each distinct active signal
    // pulls it down proportionally to (100 - its confidence).
    const nowTs = Date.now();
    const active = (window.signalLog || []).filter(
        (e) => nowTs - e.t <= 60000 && e.s && e.s !== "fusion",
    );
    const seen = new Set();
    let penalty = 0;
    for (const e of active) {
        if (seen.has(e.s)) continue;
        seen.add(e.s);
        penalty += (100 - e.c) * 0.4;
    }
    const detectionConfidence = Math.max(0, Math.min(100, 100 - penalty));
    
    // Fused score: attention 50%, agreement 30%, voice 20%
    const confidenceScore = Math.round(
        gazeScore * 0.5 + detectionConfidence * 0.3 + voiceScore * 0.2,
    );
    
    const confidenceEl = document.getElementById("insight-confidence");
    if (confidenceEl) {
        confidenceEl.textContent = `${Math.round(confidenceScore)}%`;
        if (confidenceScore >= 80) confidenceEl.className = "status green";
        else if (confidenceScore >= 50) confidenceEl.className = "status yellow";
        else confidenceEl.className = "status red";
    }
}, 2000);

// ─── Security Monitors ──────────────────────────────────────────────────
// -- Tab visibility / focus tracking --
let tabSwitchCount = 0;
const tabSwitchTimes = [];
let windowBlurCount = 0;
let copyPasteCount = 0;
let lastAudioMetricsEmitAt = 0;

const badgeFocus = document.getElementById("badge-focus");
const badgeFocusLabel = document.getElementById("badge-focus-label");
const badgeFocusIcon = document.getElementById("badge-focus-icon");
const badgeScreen = document.getElementById("badge-screen");
const badgeScreenLabel = document.getElementById("badge-screen-label");
const badgeScreenIcon = document.getElementById("badge-screen-icon");
const badgeExt = document.getElementById("badge-ext");
const badgeExtLabel = document.getElementById("badge-ext-label");
const badgeShield = document.getElementById("badge-shield");
const badgeShieldLabel = document.getElementById("badge-shield-label");

function setBadge(el, labelEl, iconEl, text, tone, iconClass, broadcast = true) {
    if (!el) return;
    el.className = `status-badge ${tone || ""}`;
    if (labelEl) labelEl.textContent = text;
    if (iconEl && iconClass) iconEl.className = iconClass;

    if (broadcast && typeof socket !== 'undefined' && socket && socket.connected) {
        socket.emit("badge_update", {
            meetingId: MEETING_ID,
            badgeId: el.id,
            text: text,
            tone: tone,
            iconClass: iconClass,
            role: userRole
        });
    }
}

if (isCandidateProctoringRole()) {
    document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
            tabSwitchCount++;
            tabSwitchTimes.push(new Date().toLocaleTimeString());
            setBadge(
                badgeFocus,
                badgeFocusLabel,
                badgeFocusIcon,
                `Tab switch #${tabSwitchCount}`,
                "danger",
                "ph ph-eye-slash",
            );
            UI_UPDATER.addAuditAlert(
                "Tab switch detected",
                `Candidate switched away from the tab. Total: ${tabSwitchCount}`,
                "Focus monitor",
                tabSwitchCount >= window.proctoringSettings.allowedTabSwitches,
            );
            showBanner(
                "Tab switch detected",
                `The interview tab lost focus. (${tabSwitchCount} time${tabSwitchCount > 1 ? "s" : ""})`,
                "Security alert",
                "warning",
            );
            updateBrowserStatsUI();
        } else {
            setBadge(
                badgeFocus,
                badgeFocusLabel,
                badgeFocusIcon,
                "Focus: Active",
                "",
                "ph ph-eye",
            );
        }
    });

    window.addEventListener("blur", () => {
        windowBlurCount++;
        setBadge(
            badgeScreen,
            badgeScreenLabel,
            badgeScreenIcon,
            `Window blur #${windowBlurCount}`,
            "danger",
            "ph ph-screencast",
        );
        if (windowBlurCount === 1) {
            UI_UPDATER.addAuditAlert(
                "Window focus lost",
                `Interview window lost focus. Total: ${windowBlurCount}`,
                "Screen monitor",
            );
        }
    });

    window.addEventListener("focus", () => {
        setBadge(
            badgeScreen,
            badgeScreenLabel,
            badgeScreenIcon,
            "Screen: Monitored",
            "green",
            "ph ph-screencast",
        );
    });

    // -- Copy paste tracking --
    document.addEventListener("copy", () => {
        if (!window.proctoringSettings.clipboardCheck) return;
        copyPasteCount++;
        UI_UPDATER.addAuditAlert(
            "Copy detected",
            `Text copied to clipboard. (${copyPasteCount} time${copyPasteCount > 1 ? "s" : ""})`,
            "Clipboard monitor",
            copyPasteCount >= 3,
        );
        showBanner(
            "Copy operation detected",
            `Clipboard copy captured during the interview.`,
            `Copy #${copyPasteCount}`,
            "warning",
        );
        updateBrowserStatsUI();
    });

    document.addEventListener("paste", () => {
        if (!window.proctoringSettings.clipboardCheck) return;
        UI_UPDATER.addAuditAlert(
            "Paste detected",
            "Content was pasted into the interview window.",
            "Clipboard monitor",
            true,
        );
        showBanner(
            "Paste detected",
            "Content pasted into the session window.",
            "Clipboard monitor",
            "warning",
        );
    });
}

function updateBrowserStatsUI() {
    const tabEl = document.getElementById("stat-tab-switches");
    const copyEl = document.getElementById("stat-copy-paste");
    if (tabEl) {
        tabEl.textContent = tabSwitchCount.toString();
        tabEl.className = tabSwitchCount > 0 ? "danger-text" : "gray-text";
        if (tabSwitchCount > 0) {
            tabEl.style.cursor = "pointer";
            tabEl.title = "Click to view timestamps";
            tabEl.onclick = () => {
                showBanner(
                    "Tab Switch History",
                    "Candidate switched tabs at: " + tabSwitchTimes.join(", "),
                    "Browser Activity",
                    "info"
                );
            };
        }
    }
    if (copyEl) {
        copyEl.textContent = copyPasteCount.toString();
        copyEl.className = copyPasteCount > 0 ? "danger-text" : "gray-text";
    }

    if (socket && socket.connected) {
        socket.emit("browser_stats_update", {
            meetingId: MEETING_ID,
            tabSwitchCount: tabSwitchCount,
            copyPasteCount: copyPasteCount
        });
    }
}

// -- Extension check (enhanced heuristic) --
function checkExtensions() {
    if (!isCandidateProctoringRole()) return;
    console.log("[Extension] Checking for suspicious browser extensions");
    
    const suspiciousGlobals = [
        "__REACT_DEVTOOLS_GLOBAL_HOOK__",
        "__VUE_DEVTOOLS_GLOBAL_HOOK__",
        "openAIExtension",
        "chatGPTExtension",
        "ngDevtools",
        "__ANGULAR_DEVTOOLS_GLOBAL_HOOK__",
        "EMMET_DEVTOOLS_GLOBAL_HOOK__"
    ];
    
    const suspiciousAPIs = [
        "chrome.extension",
        "browser.extension",
        "chrome.runtime",
        "browser.runtime"
    ];
    
    const foundGlobals = suspiciousGlobals.filter((g) => g in window);
    const foundAPIs = suspiciousAPIs.filter(api => {
        try {
            const parts = api.split('.');
            let obj = window;
            for (const part of parts) {
                if (obj[part]) {
                    obj = obj[part];
                } else {
                    return false;
                }
            }
            return true;
        } catch (e) {
            return false;
        }
    });
    
    const allFound = [...foundGlobals, ...foundAPIs];
    
    if (allFound.length > 0) {
        console.warn("[Extension] Suspicious extensions detected:", allFound);
        setBadge(
            badgeExt,
            badgeExtLabel,
            null,
            "Extension: Detected",
            "danger",
            null,
        );
        UI_UPDATER.addAuditAlert(
            "Browser extension detected",
            `Suspicious extensions/APIs found: ${allFound.join(", ")}.`,
            "Extension monitor",
            true,
        );
        
        // Implement page locking for candidates
        lockCandidatePage();
    } else {
        console.log("[Extension] No suspicious extensions detected");
        setBadge(
            badgeExt,
            badgeExtLabel,
            null,
            "Extensions: OK",
            "green",
            null,
        );
    }
}

// -- Candidate Page Locking --
function lockCandidatePage() {
    if (!isCandidateProctoringRole()) return;
    console.log("[Security] Locking candidate page due to security violation");
    
    // Disable common keyboard shortcuts
    document.addEventListener('keydown', function(e) {
        // Prevent F12, Ctrl+Shift+I, Ctrl+Shift+J, Ctrl+U
        if (e.key === 'F12' || 
            (e.ctrlKey && e.shiftKey && (e.key === 'I' || e.key === 'J')) ||
            (e.ctrlKey && e.key === 'U')) {
            e.preventDefault();
            e.stopPropagation();
            UI_UPDATER.addAuditAlert(
                "Developer tools blocked",
                "Candidate attempted to open developer tools",
                "Security lock",
                true
            );
        }
    }, true);
    
    // Disable right-click
    document.addEventListener('contextmenu', function(e) {
        e.preventDefault();
        e.stopPropagation();
        UI_UPDATER.addAuditAlert(
            "Context menu blocked",
            "Candidate attempted to open context menu",
            "Security lock",
            false
        );
    }, true);
    
    // Disable drag and drop
    document.addEventListener('dragstart', function(e) {
        e.preventDefault();
    }, true);
    
    // Detect devtools opening
    const devtoolsDetector = setInterval(() => {
        const threshold = 160;
        const widthThreshold = window.outerWidth - window.innerWidth > threshold;
        const heightThreshold = window.outerHeight - window.innerHeight > threshold;
        
        if (widthThreshold || heightThreshold) {
            console.warn("[Security] DevTools detected!");
            UI_UPDATER.addAuditAlert(
                "Developer tools detected",
                "Candidate opened browser developer tools",
                "Security violation",
                true
            );
            
            // Optionally redirect or lock further
            if (window.proctoringSettings && window.proctoringSettings.devToolsCheck) {
                document.body.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;background:#1a1a2e;color:#fff;font-family:sans-serif;text-align:center;"><div><h1>🔒 Security Violation</h1><p>Developer tools were detected. This session has been terminated.</p><p>Please contact your interviewer.</p></div></div>';
                clearInterval(devtoolsDetector);
            }
        }
    }, 1000);
    
    // Store detector ID for cleanup
    window._devtoolsDetector = devtoolsDetector;
}

// -- Shield status: updates when gaze + camera are both active --
function updateShieldStatus(active) {
    if (active) {
        setBadge(
            badgeShield,
            badgeShieldLabel,
            null,
            "Shield: ON",
            "green",
            null,
        );
    } else {
        setBadge(
            badgeShield,
            badgeShieldLabel,
            null,
            "Shield: Limited",
            "warning",
            null,
        );
    }
}

// ═══════════════════════════════════════════════════════
//  PHASE 3 — Audio Fingerprinting Engine
//  Runs 100% in browser — no backend needed
//  No STT — pure signal analysis
// ═══════════════════════════════════════════════════════

const AFP = {
    // ── Config ──────────────────────────────────────────
    FFT_SIZE:           2048,
    SAMPLE_RATE:        16000,
    ANALYSIS_INTERVAL:  500,       // ms between analysis runs
    HISTORY_SIZE:       120,       // 60 seconds at 500ms intervals

    // ── Thresholds ───────────────────────────────────────
    SILENCE_THRESHOLD:      0.005,  // below = silence
    WHISPER_THRESHOLD:      0.015,  // below = whisper
    VOICE_THRESHOLD:        0.030,  // above = clear speech
    MULTI_VOICE_THRESHOLD:  0.40,   // spectral flatness above = multiple voices
    MONOTONE_THRESHOLD:     15,     // Hz variation below = reading aloud
    TIMBRE_SHIFT_THRESHOLD: 0.35,   // cosine distance above = voice changed

    // ── State ────────────────────────────────────────────
    isRunning:          false,
    intervalId:         null,
    audioContext:       null,
    analyser:           null,
    source:             null,

    // ── History buffers ───────────────────────────────────
    rmsHistory:         [],
    pitchHistory:       [],
    timbreHistory:      [],
    noiseFloor:         0.003,      // calibrated in first 2 seconds
    noiseCalibrated:    false,
    calibrationFrames:  0,

    // ── Suspicious event counters ─────────────────────────
    whisperCount:       0,
    multiVoiceCount:    0,
    monotoneCount:      0,
    timbreShiftCount:   0,
    backgroundCount:    0,
    silenceCount:       0,
};

// ─── Initialize ───────────────────────────────────────────────────────────────
function initAudioFingerprinting() {
    if (!isCandidateProctoringRole()) {
        console.log("[AFP] Suppressed for non-candidate role");
        return;
    }
    if (!localStream) {
        console.warn("[AFP] No stream yet");
        return;
    }

    const audioTracks = localStream.getAudioTracks();
    if (audioTracks.length === 0) {
        console.warn("[AFP] No audio track");
        return;
    }

    try {
        // Resume AudioContext if suspended (browser autoplay policy)
        AFP.audioContext = new (window.AudioContext || window.webkitAudioContext)();
        if (AFP.audioContext.state === 'suspended') {
            AFP.audioContext.resume();
        }
        
        AFP.analyser     = AFP.audioContext.createAnalyser();

        AFP.analyser.fftSize               = AFP.FFT_SIZE;
        AFP.analyser.smoothingTimeConstant = 0.3;  // Low smoothing for responsiveness

        AFP.source = AFP.audioContext.createMediaStreamSource(localStream);
        AFP.source.connect(AFP.analyser);
        // NOTE: Not connecting to destination — analysis only, no echo

        AFP.isRunning = true;
        AFP.intervalId = setInterval(runAFPAnalysis, AFP.ANALYSIS_INTERVAL);

        console.log("[AFP] Audio fingerprinting started with sample rate:", AFP.audioContext.sampleRate);
    } catch(err) {
        console.error("[AFP] Init failed:", err);
    }
}

// ─── Stop ─────────────────────────────────────────────────────────────────────
function stopAudioFingerprinting() {
    if (AFP.intervalId)   clearInterval(AFP.intervalId);
    if (AFP.source)       AFP.source.disconnect();
    if (AFP.audioContext) AFP.audioContext.close();
    AFP.isRunning = false;
    console.log("[AFP] Stopped");
}

// ─── Main Analysis Loop ───────────────────────────────────────────────────────
function runAFPAnalysis() {
    if (!AFP.analyser || !AFP.isRunning) {
        console.warn("[AFP] Analysis skipped - analyser or running state invalid");
        return;
    }

    const bufferLength  = AFP.analyser.frequencyBinCount;  // FFT_SIZE / 2
    const freqData      = new Float32Array(bufferLength);  // dB values
    const timeData      = new Float32Array(bufferLength);  // waveform

    try {
        AFP.analyser.getFloatFrequencyData(freqData);
        AFP.analyser.getFloatTimeDomainData(timeData);
    } catch (e) {
        console.error("[AFP] Error getting audio data:", e);
        return;
    }

    // ── 1. RMS Energy ─────────────────────────────────────────────────────────
    const rms = computeRMS(timeData);

    // ── 2. Calibrate noise floor (first 4 seconds) ───────────────────────────
    if (!AFP.noiseCalibrated) {
        AFP.calibrationFrames++;
        AFP.noiseFloor = AFP.noiseFloor * 0.9 + rms * 0.1;  // Rolling average
        if (AFP.calibrationFrames >= 8) {
            AFP.noiseCalibrated = true;
            AFP.noiseFloor      = Math.max(AFP.noiseFloor, 0.002);
            console.log(`[AFP] Noise floor calibrated: ${AFP.noiseFloor.toFixed(4)}`);
        }
        return;
    }

    // ── 3. Voice Activity ─────────────────────────────────────────────────────
    const adjustedRMS    = Math.max(0, rms - AFP.noiseFloor);
    const isVoiceActive  = adjustedRMS > AFP.VOICE_THRESHOLD;

    // Shared volume signal (was computed by the removed second analyser loop)
    currentVoiceVolume = Math.min(100, Math.max(0, adjustedRMS * 2000));
    const isWhispering   = adjustedRMS > AFP.WHISPER_THRESHOLD &&
                           adjustedRMS < AFP.VOICE_THRESHOLD;
    const isSilent       = adjustedRMS < AFP.SILENCE_THRESHOLD;

    // ── 4. Pitch Estimation (YIN-lite) ────────────────────────────────────────
    const pitch          = estimatePitch(timeData, AFP.audioContext.sampleRate);

    // ── 5. Spectral Features ──────────────────────────────────────────────────
    const {
        spectralFlatness,
        spectralCentroid,
        bandEnergies,
    } = computeSpectralFeatures(freqData, AFP.audioContext.sampleRate);

    // ── 6. Timbre Vector (for voice change detection) ─────────────────────────
    const timbreVector   = computeTimbreVector(freqData);

    // ── Push to history ───────────────────────────────────────────────────────
    AFP.rmsHistory.push(adjustedRMS);
    AFP.pitchHistory.push(pitch);
    AFP.timbreHistory.push(timbreVector);

    // Keep history bounded
    if (AFP.rmsHistory.length   > AFP.HISTORY_SIZE) AFP.rmsHistory.shift();
    if (AFP.pitchHistory.length > AFP.HISTORY_SIZE) AFP.pitchHistory.shift();
    if (AFP.timbreHistory.length> AFP.HISTORY_SIZE) AFP.timbreHistory.shift();

    // ── 7. Run all detectors ──────────────────────────────────────────────────
    const results = {
        rms:            adjustedRMS,
        pitch,
        isVoiceActive,
        isWhispering,
        isSilent,
        spectralFlatness,
        spectralCentroid,
        bandEnergies,
        whisper:        detectWhisper(isWhispering, adjustedRMS),
        multiVoice:     detectMultipleVoices(spectralFlatness, bandEnergies),
        monotone:       detectMonotone(AFP.pitchHistory),
        timbreShift:    detectTimbreShift(AFP.timbreHistory),
        background:     detectBackgroundVoice(bandEnergies, adjustedRMS, isSilent),
        stress:         detectVoiceStress(AFP.pitchHistory, AFP.rmsHistory),
    };

    // ── 8. Update UI ──────────────────────────────────────────────────────────
    updateAFPUI(results);

    // ── 9. Fire audit alerts ──────────────────────────────────────────────────
    handleAFPAlerts(results);

    // ── 10. VAD Answer Segmentation ───────────────────────────────────────────
    if (isVoiceActive) {
        if (!isCurrentlySpeaking) {
            isCurrentlySpeaking = true;
            speechStartTime = Date.now();
        }
        silenceFrames = 0;
    } else {
        if (isCurrentlySpeaking) {
            silenceFrames++;
            // 3 consecutive silent frames (1.5 seconds) marks end of speaking burst
            if (silenceFrames >= 3) {
                isCurrentlySpeaking = false;
                evaluateAnswerAudioProfile();
            }
        }
    }
}

// ═══════════════════════════════════════════════════════
//  SIGNAL PROCESSORS
// ═══════════════════════════════════════════════════════

// ─── RMS Energy ──────────────────────────────────────────────────────────────
function computeRMS(timeData) {
    let sum = 0;
    for (let i = 0; i < timeData.length; i++) {
        sum += timeData[i] * timeData[i];
    }
    return Math.sqrt(sum / timeData.length);
}

// ─── Pitch Estimation (Autocorrelation) ──────────────────────────────────────
function estimatePitch(timeData, sampleRate) {
    const SIZE    = timeData.length;
    const MAX_LAG = Math.floor(sampleRate / 80);   // Min 80Hz
    const MIN_LAG = Math.floor(sampleRate / 400);  // Max 400Hz (human voice range)

    let   bestLag      = -1;
    let   bestCorr     = -Infinity;

    for (let lag = MIN_LAG; lag <= MAX_LAG; lag++) {
        let corr = 0;
        for (let i = 0; i < SIZE - lag; i++) {
            corr += timeData[i] * timeData[i + lag];
        }
        if (corr > bestCorr) {
            bestCorr = corr;
            bestLag  = lag;
        }
    }

    if (bestLag <= 0) return 0;
    return Math.round(sampleRate / bestLag);  // Hz
}

// ─── Spectral Features ───────────────────────────────────────────────────────
function computeSpectralFeatures(freqData, sampleRate) {
    const binCount   = freqData.length;
    const binHz      = sampleRate / (AFP.FFT_SIZE);

    // Convert dB to linear magnitude
    const magnitudes = freqData.map(db => Math.pow(10, db / 20));

    // Spectral Flatness — high = noise/multiple voices, low = tonal/single voice
    const geomMean   = Math.exp(
        magnitudes.reduce((s, m) => s + Math.log(m + 1e-10), 0) / binCount
    );
    const arithMean  = magnitudes.reduce((s, m) => s + m, 0) / binCount;
    const spectralFlatness = geomMean / (arithMean + 1e-10);

    // Spectral Centroid — "brightness" of the sound
    let weightedSum  = 0;
    let totalMag     = 0;
    magnitudes.forEach((m, i) => {
        weightedSum += m * i * binHz;
        totalMag    += m;
    });
    const spectralCentroid = totalMag > 0 ? weightedSum / totalMag : 0;

    // Band Energies — split into 4 bands
    // Sub-bass: 0-250Hz | Voice: 250-3000Hz | Presence: 3-8kHz | Air: 8kHz+
    const bands        = [0, 0, 0, 0];
    const bandLimits   = [250, 3000, 8000, sampleRate / 2];

    magnitudes.forEach((m, i) => {
        const hz = i * binHz;
        if      (hz < bandLimits[0]) bands[0] += m;
        else if (hz < bandLimits[1]) bands[1] += m;
        else if (hz < bandLimits[2]) bands[2] += m;
        else                         bands[3] += m;
    });

    // Normalize bands
    const totalBand = bands.reduce((s, b) => s + b, 0) || 1;
    const bandEnergies = bands.map(b => b / totalBand);

    return { spectralFlatness, spectralCentroid, bandEnergies };
}

// ─── Timbre Vector (MFCC-lite) ────────────────────────────────────────────────
function computeTimbreVector(freqData) {
    // Simplified 8-band timbre fingerprint
    const bandSize = Math.floor(freqData.length / 8);
    const vector   = [];

    for (let b = 0; b < 8; b++) {
        let energy = 0;
        const start = b * bandSize;
        const end   = start + bandSize;
        for (let i = start; i < end; i++) {
            energy += Math.pow(10, freqData[i] / 20);
        }
        vector.push(energy / bandSize);
    }

    // Normalize
    const max = Math.max(...vector) || 1;
    return vector.map(v => v / max);
}

// ═══════════════════════════════════════════════════════
//  DETECTORS
// ═══════════════════════════════════════════════════════

// ─── 1. Whisper Detection ─────────────────────────────────────────────────────
function detectWhisper(isWhispering, rms) {
    if (!isWhispering) return { detected: false };

    AFP.whisperCount++;
    return {
        detected:    true,
        rms:         rms.toFixed(4),
        message:     "Whispering detected — possible coaching",
        riskDelta:   15,
    };
}

// ─── 2. Multiple Voice Detection ──────────────────────────────────────────────
function detectMultipleVoices(spectralFlatness, bandEnergies) {
    // Multiple voices create broader, flatter frequency spectrum
    const isFlat         = spectralFlatness > AFP.MULTI_VOICE_THRESHOLD;

    // Also check if both sub-bass AND voice bands are simultaneously active
    const subBassActive  = bandEnergies[0] > 0.15;
    const voiceActive    = bandEnergies[1] > 0.40;
    const multiSignal    = subBassActive && voiceActive && isFlat;

    if (!multiSignal) return { detected: false };

    AFP.multiVoiceCount++;
    return {
        detected:    true,
        flatness:    spectralFlatness.toFixed(3),
        message:     "Multiple voice frequencies detected",
        riskDelta:   25,
    };
}

// ─── 3. Monotone Detection (Reading Aloud) ────────────────────────────────────
function detectMonotone(pitchHistory) {
    if (pitchHistory.length < 20) return { detected: false };

    // Get last 10 seconds of pitch (20 frames × 500ms)
    const recent     = pitchHistory.slice(-20).filter(p => p > 0);
    if (recent.length < 10) return { detected: false };

    const mean       = recent.reduce((s, p) => s + p, 0) / recent.length;
    const variance   = recent.reduce((s, p) => s + Math.pow(p - mean, 2), 0)
                       / recent.length;
    const stdDev     = Math.sqrt(variance);

    // Natural speech has stdDev > 20-30Hz
    // Reading aloud is very flat (stdDev < 15Hz)
    if (stdDev >= AFP.MONOTONE_THRESHOLD) return { detected: false };

    AFP.monotoneCount++;
    return {
        detected: true,
        stdDev:   stdDev.toFixed(2),
        message:  `Monotone speech detected (±${stdDev.toFixed(0)}Hz) — possibly reading`,
        riskDelta: 12,
    };
}

// ─── 4. Timbre Shift (Voice Change) ──────────────────────────────────────────
function detectTimbreShift(timbreHistory) {
    if (timbreHistory.length < 10) return { detected: false };

    // Compare average of last 5 frames vs average of 5 frames before that
    const recent   = timbreHistory.slice(-5);
    const previous = timbreHistory.slice(-10, -5);

    const recentAvg   = averageVectors(recent);
    const previousAvg = averageVectors(previous);

    const distance    = cosineSimilarity(recentAvg, previousAvg);
    // cosine similarity: 1 = identical, 0 = completely different
    const shift       = 1 - distance;

    if (shift < AFP.TIMBRE_SHIFT_THRESHOLD) return { detected: false };

    AFP.timbreShiftCount++;
    return {
        detected:  true,
        shift:     shift.toFixed(3),
        message:   "Voice characteristics changed — different speaker possible",
        riskDelta: 30,
    };
}

// ─── 5. Background Voice Detection ───────────────────────────────────────────
function detectBackgroundVoice(bandEnergies, rms, isSilent) {
    if (!isSilent) return { detected: false };

    // During silence — if voice band (250-3000Hz) still has energy
    // it suggests a background voice
    const voiceBandEnergy = bandEnergies[1];
    const hasBackground   = voiceBandEnergy > 0.35 && rms > AFP.WHISPER_THRESHOLD;

    if (!hasBackground) return { detected: false };

    AFP.backgroundCount++;
    return {
        detected:  true,
        energy:    voiceBandEnergy.toFixed(3),
        message:   "Background voice during silence — possible coaching",
        riskDelta: 20,
    };
}

// ─── 6. Voice Stress Detection ────────────────────────────────────────────────
function detectVoiceStress(pitchHistory, rmsHistory) {
    if (pitchHistory.length < 30) return { stressLevel: "unknown" };

    const recent        = pitchHistory.slice(-30).filter(p => p > 0);
    const recentRMS     = rmsHistory.slice(-30);

    if (recent.length < 10) return { stressLevel: "unknown" };

    // Stress indicators:
    // 1. Higher pitch than baseline
    // 2. More pitch variability
    // 3. Energy fluctuations

    const mean          = recent.reduce((s, p) => s + p, 0) / recent.length;
    const variance      = recent.reduce((s, p) => s + Math.pow(p - mean, 2), 0)
                          / recent.length;
    const stdDev        = Math.sqrt(variance);

    const rmsVariance   = recentRMS.reduce((s, r) => {
        const avg = recentRMS.reduce((a, b) => a + b, 0) / recentRMS.length;
        return s + Math.pow(r - avg, 2);
    }, 0) / recentRMS.length;

    const stressScore   = Math.min(100,
        (mean > 200 ? 20 : 0) +       // High pitch
        (stdDev > 50 ? 20 : 0) +      // Pitch instability
        (rmsVariance > 0.01 ? 20 : 0) // Energy instability
    );

    return {
        stressLevel: stressScore > 50 ? "High" :
                     stressScore > 25 ? "Medium" : "Low",
        stressScore,
        pitch:       Math.round(mean),
        pitchStdDev: Math.round(stdDev),
    };
}

// ═══════════════════════════════════════════════════════
//  MATH HELPERS
// ═══════════════════════════════════════════════════════

function averageVectors(vectors) {
    if (!vectors.length) return [];
    const len = vectors[0].length;
    const avg = new Array(len).fill(0);
    vectors.forEach(v => v.forEach((val, i) => avg[i] += val));
    return avg.map(v => v / vectors.length);
}

function cosineSimilarity(a, b) {
    if (!a.length || !b.length) return 1;
    const dot     = a.reduce((s, v, i) => s + v * b[i], 0);
    const magA    = Math.sqrt(a.reduce((s, v) => s + v * v, 0));
    const magB    = Math.sqrt(b.reduce((s, v) => s + v * v, 0));
    return dot / (magA * magB + 1e-10);
}

// ═══════════════════════════════════════════════════════
//  UI UPDATES
// ═══════════════════════════════════════════════════════

function updateAFPUI(results) {
    const {
        rms, pitch, isVoiceActive,
        isWhispering, isSilent, stress,
        bandEnergies,
    } = results;

    console.log("[AFP] Updating audio UI - RMS:", rms.toFixed(4), "Voice Active:", isVoiceActive);

    // ── Voice level bar ───────────────────────────────────────────────────────
    const level        = Math.min(100, Math.round(rms * 2000));
    const voiceBar     = document.getElementById('voice-level-bar');
    const voiceVal     = document.getElementById('voice-level-value');
    if (voiceBar) voiceBar.style.width   = `${level}%`;
    if (voiceVal) voiceVal.textContent   = `${level}%`;

    // ── Voice status ──────────────────────────────────────────────────────────
    const voiceStatus  = document.getElementById('voice-status');
    if (voiceStatus) {
        if      (isSilent)      { voiceStatus.textContent = 'Silent';     voiceStatus.className = 'value gray';   }
        else if (isWhispering)  { voiceStatus.textContent = 'Whisper';    voiceStatus.className = 'value red';    }
        else if (isVoiceActive) { voiceStatus.textContent = 'Speaking';   voiceStatus.className = 'value green';  }
        else                    { voiceStatus.textContent = 'Low';        voiceStatus.className = 'value purple'; }
    }

    // ── Pitch display ─────────────────────────────────────────────────────────
    const pitchEl      = document.getElementById('voice-pitch');
    if (pitchEl && pitch > 0) pitchEl.textContent = `${pitch} Hz`;

    // ── Stress level ──────────────────────────────────────────────────────────
    const stressEl     = document.getElementById('voice-stress');
    if (stressEl && stress) {
        stressEl.textContent = stress.stressLevel;
        stressEl.className   = `value ${
            stress.stressLevel === 'High'   ? 'red'    :
            stress.stressLevel === 'Medium' ? 'purple' : 'green'
        }`;
    }

    // ── Emit to host via socket (candidate only) ─────────────────────────────
    if (userRole === "candidate" && socket && socket.connected) {
        const emitNow = Date.now();
        if (emitNow - lastAudioMetricsEmitAt < 2000) return; // 2s throttle
        lastAudioMetricsEmitAt = emitNow;
        
        const voiceStatusEl = document.getElementById('voice-status');
        const pitchElUI = document.getElementById('voice-pitch');
        const stressElUI = document.getElementById('voice-stress');
        
        const audioData = {
            meetingId: MEETING_ID,
            level: level,
            voiceStatusText: voiceStatusEl ? voiceStatusEl.textContent : "Waiting",
            voiceStatusClass: voiceStatusEl ? voiceStatusEl.className : "value gray",
            pitchText: (pitchElUI && pitch > 0) ? pitchElUI.textContent : "-- Hz",
            stressText: (stressElUI && stress) ? stressElUI.textContent : "Low",
            stressClass: (stressElUI && stress) ? stressElUI.className : "value green"
        };
        
        console.log("[AFP] Emitting audio metrics to host:", audioData);
        socket.emit("audio_metrics_update", audioData);
    }
}

// ─── Alert Handler ────────────────────────────────────────────────────────────
// Throttle alerts — don't spam audit trail
const _afpAlertCooldowns = {};

function handleAFPAlerts(results) {
    if (!window.proctoringSettings.audioCheck) return;
    const checks = [
        { key: 'whisper',    data: results.whisper    },
        { key: 'multiVoice', data: results.multiVoice },
        { key: 'monotone',   data: results.monotone   },
        { key: 'timbre',     data: results.timbreShift},
        { key: 'background', data: results.background },
    ];

    const now = Date.now();

    checks.forEach(({ key, data }) => {
        if (!data?.detected) return;

        // Cooldown: don't fire same alert more than once per 15 seconds
        const lastFired = _afpAlertCooldowns[key] || 0;
        if (now - lastFired < 15000) return;

        _afpAlertCooldowns[key] = now;

        const isCritical = data.riskDelta >= 20;
        UI_UPDATER.addAuditAlert(
            'Audio Anomaly',
            data.message,
            `Confidence: ${Math.min(100, data.riskDelta * 2)}%`,
            isCritical
        );

        // Unified risk engine: weight = detector's riskDelta. AFP heuristics
        // (spectral flatness, pitch variance) are noisy — medium confidence.
        if (typeof bumpRisk !== 'undefined') bumpRisk(data.riskDelta, 'audio', 60);
    });
}

// ─── Hook into existing webcam start ─────────────────────────────────────────
let livenessMonitorTimer = null;
let lastLivenessAlertAt = 0;
let faceLostSince = 0; // timestamp when continuous face loss began
let lastLivenessFace = false; // whether the most recent liveness frame had a face
let challengeFailStreak = 0;  // consecutive unanswered liveness challenges
let lastChallengeStatus = "idle"; // previous verdict, to count transitions once
let lastChallengeAt = 0;
let livenessChallengeDelay = 90000; // first challenge ~90s in, then 90-180s

// Reset per-session challenge state (called from ui.js when a session starts)
// so one candidate's fail streak never leaks into the next interview.
window.resetLivenessChallengeState = () => {
    challengeFailStreak = 0;
    lastChallengeStatus = "idle";
    lastChallengeAt = 0;
    livenessChallengeDelay = 90000;
};

// ─── Liveness verdict handling (combined /analyze path) ───────────────────────
// The backend computes liveness inside the /analyze response (one MediaPipe
// pass, one HTTP request — see gaze.js) and this consumes it. It carries the
// same challenge / face-loss / presentation-attack logic the old standalone
// /liveness-frame loop had, minus the duplicated inference + request.
window.handleLivenessResult = function handleLivenessResult(liveness) {
    if (!liveness) return;
    if (!isCandidateProctoringRole()) return;
    if (!window.proctoringSettings.gazeCheck) return;

    const now = Date.now();

    lastLivenessFace = liveness.faceDetected === true;

    // Liveness challenge verdict: /liveness-challenge issues a random
    // blink/head prompt; the backend reports a resolved verdict until the
    // next challenge, so only count a verdict on status TRANSITION.
    const ch = liveness.challenge;
    if (ch && ch.status !== lastChallengeStatus) {
        lastChallengeStatus = ch.status;
        if (ch.status === "passed") {
            challengeFailStreak = 0;
            if (typeof showBanner !== "undefined") {
                showBanner(
                    "Liveness check passed",
                    "The candidate responded to the liveness prompt.",
                    "Face Liveness",
                    "success",
                );
            }
        } else if (ch.status === "failed") {
            challengeFailStreak++;
            UI_UPDATER.addAuditAlert(
                "Liveness Challenge Failed",
                "Candidate did not respond to the liveness prompt (blink or head move).",
                "Face Liveness",
                challengeFailStreak >= 2,
            );
            if (challengeFailStreak >= 2 && typeof bumpRisk !== "undefined") {
                bumpRisk(10, "liveness", 85);
            }
        }
    }

    // Sustained face loss: a brief glance away / hand in front of the
    // camera is normal (and face_not_detected must NOT spam), but
    // ~25s+ with no face means the candidate left the camera entirely.
    if (liveness.reason === "face_not_detected") {
        if (!faceLostSince) faceLostSince = now;
        const lostFor = now - faceLostSince;
        if (lostFor >= 25000 && now - lastLivenessAlertAt >= 60000) {
            lastLivenessAlertAt = now;
            UI_UPDATER.addAuditAlert(
                "Candidate Face Not Visible",
                `No face detected for ${Math.round(lostFor / 1000)}s — candidate may have left the camera.`,
                "Face Liveness",
                true
            );
            if (typeof bumpRisk !== "undefined") bumpRisk(8, "liveness", 80);
        }
        return;
    }
    faceLostSince = 0;

    // Alert only on explicit high risk (photo / screen replay / dark / blur).
    // "Medium" states such as awaiting_blink (calibration window) and
    // face_not_detected (brief glance away / hand in front of camera) stay
    // quiet — face_not_detected has is_live=false but must NOT alert.
    if (liveness.risk !== "high") return;

    if (now - lastLivenessAlertAt < 20000) return;
    lastLivenessAlertAt = now;

    const confidence = Math.round((liveness.confidence || 0) * 100);
    const message = `Liveness risk: ${liveness.risk}. Reason: ${liveness.reason || "unknown"}. Confidence: ${confidence}%`;

    UI_UPDATER.addAuditAlert(
        "Presentation Attack Warning",
        message,
        "Face Liveness",
        true
    );

    if (typeof bumpRisk !== "undefined") {
        // Liveness reports its own confidence (0-1); high-risk verdicts are
        // 0.70-0.90, so a static-face / no-blink finding carries strong weight.
        const conf = liveness.confidence
            ? Math.round(liveness.confidence * 100)
            : 90;
        bumpRisk(liveness.risk === "high" ? 12 : 4, "liveness", conf);
    }
};

// Random liveness challenge scheduler: every 90-180s (when the session is
// active, the camera is on, and a face is visible) ask the candidate — subtly,
// on their own screen — to blink or turn their head, then verify the response
// via the backend. A photo or screen replay cannot react to a prompt on cue.
function maybeIssueLivenessChallenge() {
    if (!isCandidateProctoringRole()) return;
    if (!window.sessionActive) return;
    if (!window.proctoringSettings || !window.proctoringSettings.gazeCheck) return;
    if (!lastLivenessFace) return; // pointless without a visible face

    const now = Date.now();
    if (now - lastChallengeAt < livenessChallengeDelay) return;

    const type = Math.random() < 0.5 ? "blink" : "head";
    lastChallengeAt = now;
    livenessChallengeDelay = 90000 + Math.floor(Math.random() * 90000);

    fetch(apiUrl("/liveness-challenge"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ meetingId: MEETING_ID, type }),
    })
        .then((r) => r.json())
        .then((data) => {
            if (!data || data.error) return;
            if (typeof showBanner !== "undefined") {
                showBanner(
                    "Liveness check",
                    type === "blink"
                        ? "Please blink now."
                        : "Please turn your head slightly.",
                    "Face Liveness",
                    "info",
                );
            }
        })
        .catch(() => {});
}

function startLivenessMonitoring() {
    if (livenessMonitorTimer) return;
    // Frame analysis (gaze + liveness) is driven by the ML loop in gaze.js at
    // ~1 fps through the combined /analyze request — no separate liveness
    // timer. This timer only checks whether a random challenge is due.
    livenessMonitorTimer = setInterval(maybeIssueLivenessChallenge, 10000);
}

videoElement.addEventListener('playing', () => {
    if (!isCandidateProctoringRole()) return;
    startMLProcessing();
    startLivenessMonitoring();
    initAudioFingerprinting();   // ← Phase 3 starts here
});



// ═══════════════════════════════════════════════════════
//  PHASE 5 — Network Analysis (VPN / Proxy Detection)
// ═══════════════════════════════════════════════════════

async function checkNetworkSecurity() {
    if (!isCandidateProctoringRole()) return; // Only verify candidate IP

    try {
        // 1. Grab candidate public IP
        const ipRes = await fetch("https://api.ipify.org?format=json");
        const ipData = await ipRes.json();
        if (!ipData.ip) return;

        // 2. Validate via our secure backend to bypass mixed-content HTTPS issues
        const analysisRes = await fetch(apiUrl("/analyze_network"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ meetingId: MEETING_ID, ip: ipData.ip })
        });
        const analysis = await analysisRes.json();

        if (typeof socket !== "undefined" && socket) {
            socket.emit("network_stats_update", {
                meetingId: MEETING_ID,
                isp: analysis.isp || "Unknown",
                location: analysis.location || "Unknown",
                isVpnOrProxy: !!analysis.is_vpn_or_proxy
            });
        }

        if (analysis.is_vpn_or_proxy) {
            UI_UPDATER.addAuditAlert(
                "Critical Security Breach",
                `Candidate is using a VPN or Proxy to hide their identity! (ISP: ${analysis.isp}, Location: ${analysis.location})`,
                "Network Forensics",
                true
            );
            // Severe Risk Spike
            // Severe: weight 50 pins the gauge near HIGH immediately
            if (typeof bumpRisk !== 'undefined') bumpRisk(50, 'network', 95);
        } else {
            console.log(`[Network] Secure connection from ${analysis.location} via ${analysis.isp}`);
        }
    } catch (e) {
        console.warn("Network security check failed:", e);
    }
}

// Trigger network assessment automatically 5 seconds after page load
setTimeout(checkNetworkSecurity, 5000);

// ═══════════════════════════════════════════════════════
//  PHASE 5 — Typing Biometrics (Keyboard Rhythm)
// ═══════════════════════════════════════════════════════

const TypingBiometrics = {
    keyDwells: [],   // Duration keys are held down (ms)
    keyFlights: [],  // Duration between keystrokes (ms)
    lastKeyDown: 0,
    activeKeys: {},
    baselineEstablished: false,
    baselineAvgDwell: 0,
    baselineAvgFlight: 0
};

document.addEventListener("keydown", (e) => {
    if (!isCandidateProctoringRole()) return; // Only track candidate keystrokes
    
    // Ignore modifiers and specialized keys
    if (["Shift", "Control", "Alt", "Meta", "CapsLock", "Tab", "Enter"].includes(e.key)) return;

    const now = Date.now();
    if (!TypingBiometrics.activeKeys[e.key]) {
        TypingBiometrics.activeKeys[e.key] = now;
        
        if (TypingBiometrics.lastKeyDown > 0) {
            const flightTime = now - TypingBiometrics.lastKeyDown;
            if (flightTime < 1500) { // Only log continuous typing bursts
                TypingBiometrics.keyFlights.push(flightTime);
            }
        }
        TypingBiometrics.lastKeyDown = now;
    }
});

document.addEventListener("keyup", (e) => {
    if (!isCandidateProctoringRole()) return;
    
    const now = Date.now();
    if (TypingBiometrics.activeKeys[e.key]) {
        const dwellTime = now - TypingBiometrics.activeKeys[e.key];
        if (dwellTime < 1000) { // Filter out abnormally long holds
            TypingBiometrics.keyDwells.push(dwellTime);
        }
        delete TypingBiometrics.activeKeys[e.key];
        
        analyzeTypingRhythm();
    }
});

function analyzeTypingRhythm() {
    if (TypingBiometrics.keyDwells.length < 20) return; // Need a minimum sample size
    
    // Calculate current running averages
    const recentDwells = TypingBiometrics.keyDwells.slice(-15);
    const currentAvgDwell = recentDwells.reduce((a, b) => a + b, 0) / recentDwells.length;

    // Macro/Bot Detection: Check for perfect mathematical variance (0ms deviation)
    let varianceSum = 0;
    for (let d of recentDwells) varianceSum += Math.abs(d - currentAvgDwell);
    const avgVariance = varianceSum / recentDwells.length;
    
    if (avgVariance < 1.0) { // Humans cannot type 15 keys with < 1ms variance perfectly
        UI_UPDATER.addAuditAlert(
            "Bot / Macro Script Detected",
            "Keystroke timing is mathematically perfect. Candidate is using an automated script or USB rubber ducky!",
            "Biometrics",
            true
        );
        TypingBiometrics.keyDwells = []; // reset to avoid spam
    }

    // Establish Baseline during the first ~50 keystrokes
    if (!TypingBiometrics.baselineEstablished && TypingBiometrics.keyDwells.length >= 50) {
        const baselineDwells = TypingBiometrics.keyDwells.slice(0, 50);
        TypingBiometrics.baselineAvgDwell = baselineDwells.reduce((a, b) => a + b, 0) / 50;
        TypingBiometrics.baselineEstablished = true;
        console.log(`[Biometrics] Baseline typing rhythm established. Dwell: ${Math.round(TypingBiometrics.baselineAvgDwell)}ms`);
        return;
    }

    if (TypingBiometrics.baselineEstablished) {
        // If current rhythm speed shifts by > 55% from the established baseline, flag an anomaly
        const dwellShift = Math.abs(currentAvgDwell - TypingBiometrics.baselineAvgDwell) / TypingBiometrics.baselineAvgDwell;
        
        if (dwellShift > 0.55) {
            UI_UPDATER.addAuditAlert(
                "Typing Biometrics Mismatch",
                "Keyboard rhythm changed drastically — possible remote desktop or 2nd typist!",
                "Browser Forensics",
                true
            );
            
            // Reset baseline to prevent alert spam, forcing system to learn the new typist's rhythm
            TypingBiometrics.baselineEstablished = false;
            TypingBiometrics.keyDwells = [];
            TypingBiometrics.keyFlights = [];
            
            if (typeof bumpRisk !== 'undefined') bumpRisk(6, 'typing', 40);
        }
    }
}

// ═══════════════════════════════════════════════════════
//  PHASE 6 — Browser Environment & Clipboard Forensics
// ═══════════════════════════════════════════════════════

function initBrowserForensics() {
    if (!isCandidateProctoringRole()) return;

    // 1. Tab Switching & Browser Focus Detection
    document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
            if (tabSwitchCount >= window.proctoringSettings.allowedTabSwitches) {
                UI_UPDATER.addAuditAlert(
                    "Browser Focus Lost",
                    `Candidate minimized the window or switched tabs repeatedly! Total: ${tabSwitchCount}`,
                    "OS Forensics",
                    true
                );
                if (typeof bumpRisk !== 'undefined') bumpRisk(15, 'focus', 70);
            }
        }
    });

    window.addEventListener("blur", () => {
        // Less severe than visibilitychange, but captures clicking into another app
        console.warn("[Forensics] Window lost focus");
    });

    // 2. Clipboard Tracking (Copy / Paste)
    document.addEventListener("copy", (e) => {
        if (!window.proctoringSettings.clipboardCheck) return;
        UI_UPDATER.addAuditAlert(
            "Clipboard Abuse (Copy)",
            "Candidate copied content from the interview window.",
            "OS Forensics",
            true
        );
    });

    document.addEventListener("paste", (e) => {
        if (!window.proctoringSettings.clipboardCheck) return;
        const pasteData = (e.clipboardData || window.clipboardData).getData('text');
        
        // If they paste a huge block of text instantly
        if (pasteData && pasteData.length > 50) {
            UI_UPDATER.addAuditAlert(
                "Clipboard Abuse (Paste)",
                `Candidate instantly pasted a large block of text (${pasteData.length} chars). Possible ChatGPT copy/paste!`,
                "OS Forensics",
                true
            );
            if (typeof bumpRisk !== 'undefined') bumpRisk(20, 'clipboard', 80);
        }
    });

    // 3. Camera Sabotage (Pitch Black Frame Check)
    setInterval(() => {
        if (!window.proctoringSettings.gazeCheck) return;
        if (!videoElement || videoElement.videoWidth === 0) return;
        
        // Sample the middle of the video feed
        const canvas = document.createElement('canvas');
        canvas.width = 64;
        canvas.height = 64;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(videoElement, 0, 0, 64, 64);
        
        const frameData = ctx.getImageData(0, 0, 64, 64).data;
        let brightnessSum = 0;
        
        for (let i = 0; i < frameData.length; i += 4) {
            brightnessSum += (frameData[i] + frameData[i+1] + frameData[i+2]) / 3;
        }
        
        const avgBrightness = brightnessSum / (64 * 64);
        
        if (avgBrightness < 5) { // Almost completely black
            UI_UPDATER.addAuditAlert(
                "Camera Sabotage Warning",
                "Video feed is pitch black. Candidate may have covered the camera or turned off the lights.",
                "Vision Engine",
                true
            );
        }
    }, 10000); // Check every 10 seconds
}

// Start immediately
initBrowserForensics();

// ═══════════════════════════════════════════════════════
//  PHASE 7 — Advanced Hardware & OS Forensics
// ═══════════════════════════════════════════════════════

function initAdvancedHardwareForensics() {
    if (!isCandidateProctoringRole()) return;

    // 1. Virtual Machine (VM) GPU Detection
    try {
        if (window.proctoringSettings.vmCheck) {
            const canvas = document.createElement("canvas");
            const gl = canvas.getContext("webgl") || canvas.getContext("experimental-webgl");
            if (gl) {
                const debugInfo = gl.getExtension("WEBGL_debug_renderer_info");
                if (debugInfo) {
                    const renderer = gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL).toLowerCase();
                    const vmKeywords = ["vmware", "virtualbox", "llvmpipe", "swiftshader", "parallels", "qemu"];
                    
                    for (const keyword of vmKeywords) {
                        if (renderer.includes(keyword)) {
                            UI_UPDATER.addAuditAlert(
                                "Hardware Tampering (VM Detected)",
                                `Candidate is running the interview inside a Virtual Machine to bypass security! (GPU: ${renderer})`,
                                "Hardware Forensics",
                                true
                            );
                            if (typeof bumpRisk !== 'undefined') bumpRisk(25, 'hardware', 90);
                            break;
                        }
                    }
                }
            }
        }
    } catch (e) { console.warn("VM detection failed", e); }

    // 2. Dual-Monitor Detection
    let hasAlertedMonitor = false;
    setInterval(() => {
        if (!window.proctoringSettings.dualMonitorCheck) return;
        if (hasAlertedMonitor) return;
        // Check window.screen.isExtended (supported in modern Chromium)
        const isExtended = window.screen && window.screen.isExtended;
        if (isExtended) {
            UI_UPDATER.addAuditAlert(
                "Multi-Monitor Warning",
                "Multiple monitors detected via Screen API. Candidate might have answers open on a second display.",
                "Hardware Forensics",
                true
            );
            hasAlertedMonitor = true;
        }
    }, 15000); // Check every 15s

    // 3. Virtual Camera & Deepfake Guard
    async function scanMediaDevices() {
        if (!window.proctoringSettings.vmCheck) return;
        try {
            const devices = await navigator.mediaDevices.enumerateDevices();
            for (const device of devices) {
                const label = device.label.toLowerCase();
                if (label.includes("obs") || label.includes("virtual") || label.includes("manycam") || label.includes("snap camera")) {
                    UI_UPDATER.addAuditAlert(
                        "Deepfake / Virtual Camera Detected",
                        `Candidate is routing video through software! (${device.label})`,
                        "Hardware Forensics",
                        true
                    );
                }
            }
        } catch (e) { console.warn("Device scan failed", e); }
    }
    window.scanMediaDevices = scanMediaDevices;
    
    // We must wait for permissions to be granted to read labels
    setTimeout(scanMediaDevices, 5000);

    // 4. Listen for mid-interview hardware swaps (Bluetooth earpieces)
    navigator.mediaDevices.addEventListener('devicechange', () => {
        if (!window.proctoringSettings.vmCheck) return;
        UI_UPDATER.addAuditAlert(
            "Mid-Session Hardware Change",
            "A new audio/video device (e.g. bluetooth earpiece) was just plugged in mid-interview!",
            "Hardware Forensics",
            true
        );
        scanMediaDevices();
    });
}

initAdvancedHardwareForensics();

// ═══════════════════════════════════════════════════════
//  PHASE 8 — Ultimate Edge-Case Protections
// ═══════════════════════════════════════════════════════

function initUltimateEdgeCases() {
    if (!isCandidateProctoringRole()) return;

    // 1. DevTools Hacker Guard (F12 Detection)
    let devToolsAlerted = false;
    setInterval(() => {
        if (!window.proctoringSettings.devToolsCheck) return;
        if (devToolsAlerted) return;
        const widthDiff = window.outerWidth - window.innerWidth;
        const heightDiff = window.outerHeight - window.innerHeight;
        
        // If outer window is large, but inner window is shrunk massively (DevTools pane opens)
        if ((widthDiff > 250 || heightDiff > 250) && window.outerWidth > 400) {
            UI_UPDATER.addAuditAlert(
                "System Tampering (DevTools)",
                "Candidate opened Browser Developer Tools (F12) to inspect or hack the session!",
                "Cyber Forensics",
                true
            );
            devToolsAlerted = true;
            if (typeof bumpRisk !== 'undefined') bumpRisk(30, 'devtools', 85);
        }
    }, 2000);

    // 2. Network Sabotage Guard (WiFi dropping)
    window.addEventListener('offline', () => {
        window.offlineStartTime = Date.now();
        console.warn("[Network] Connection lost");
    });

    window.addEventListener('online', () => {
        if (window.offlineStartTime) {
            const downtime = Math.round((Date.now() - window.offlineStartTime) / 1000);
            UI_UPDATER.addAuditAlert(
                "Network Sabotage",
                `Candidate internet disconnected for ${downtime} seconds. Possible intentional WiFi drop to freeze video!`,
                "Network Forensics",
                true
            );
            window.offlineStartTime = null;
        }
    });
}
initUltimateEdgeCases();


// ─── VAD Answer Evaluation ──────────────────────────────────────────────────
async function evaluateAnswerAudioProfile() {
    if (!isCandidateProctoringRole()) return;
    
    // Check speechStartTime
    if (typeof speechStartTime === "undefined" || !speechStartTime) return;
    
    const durationSeconds = Math.round((Date.now() - speechStartTime) / 1000);
    if (durationSeconds < 2) {
        // Too short to evaluate
        return;
    }
    
    // Calculate stress metrics from the last speech burst using detectVoiceStress
    const stress = detectVoiceStress(AFP.pitchHistory, AFP.rmsHistory);
    
    // We check if it is monotone
    const pitchStdDev = stress.pitchStdDev || 0;
    const isMonotone = pitchStdDev < AFP.MONOTONE_THRESHOLD;
    
    const voiceMetrics = {
        durationSeconds: durationSeconds,
        pitchStdDev: pitchStdDev,
        isMonotone: isMonotone,
        whisperCount: AFP.whisperCount,
        stressLevel: stress.stressLevel || "Low"
    };
    
    console.log("[AFP] Evaluating candidate answer audio metrics:", voiceMetrics);
    
    try {
        const response = await fetch(apiUrl("/analyze_answer"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ meetingId: MEETING_ID, voiceMetrics })
        });
        if (response.ok) {
            const data = await response.json();
            if (data.analysis) {
                console.log("[AFP] Answer evaluation verdict:", data.analysis);
                // Send audit event via socket
                if (typeof socket !== "undefined" && socket && socket.connected) {
                    socket.emit("audit_event", {
                        meetingId: MEETING_ID,
                        title: "Audio Profile Analysis",
                        message: `Verdict: ${data.analysis.verdict.toUpperCase()}. Reason: ${data.analysis.reason}`,
                        confidence: data.analysis.score,
                        isCritical: data.analysis.verdict !== "human"
                    });
                }
            }
        }
    } catch (e) {
        console.warn("Failed to evaluate answer audio profile:", e);
    }
}
