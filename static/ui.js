/**
 * Intervue — script.js
 *
 * FIX LOG:
 * 1. Role check now redirects to /login (Flask route) not login.html
 * 2. toggleMute / toggleVideo guard against missing tracks gracefully (no alert spam)
 * 3. ML_CONFIG.processFPS lowered to 5 to reduce backend load
 * 4. sendFrameForGazeAnalysis: base64 padding fix (mirrors backend fix)
 * 5. addAuditAlert: querySelector('.timeline') → getElementById('timeline') — reliable
 * 6. updateRiskScore: querySelector fixed to use IDs where possible
 * 7. Timer: session elapsed timer added
 * 8. End-interview button wired to modal
 * 9. Audio bars animation paused when muted
 * 10. Role-aware UI labels set on load
 * 11. Camera/mic system check status updated after stream acquired
 * 12. Gaze direction label + pupil position animation added
 * 13. localStorage usage removed where not needed (no more window.location to login.html)
 */

"use strict";

// ─── Theme Management (Matte Light / Glassmorphism Dark) ──────────────────────
function initTheme() {
    const savedTheme = localStorage.getItem("theme") || "light";
    setTheme(savedTheme);
}

function setTheme(theme) {
    const btn = document.getElementById("btn-theme-toggle");
    const icon = document.getElementById("theme-toggle-icon");
    const text = document.getElementById("theme-toggle-text");
    
    if (theme === "dark") {
        document.body.classList.add("theme-dark");
        document.body.classList.remove("theme-light");
        if (icon) icon.className = "ph ph-sun";
        if (text) text.textContent = "Light Mode";
        localStorage.setItem("theme", "dark");
    } else {
        document.body.classList.add("theme-light");
        document.body.classList.remove("theme-dark");
        if (icon) icon.className = "ph ph-moon";
        if (text) text.textContent = "Dark Mode";
        localStorage.setItem("theme", "light");
    }
}

function toggleTheme() {
    const currentTheme = localStorage.getItem("theme") || "light";
    setTheme(currentTheme === "dark" ? "light" : "dark");
}

window.toggleTheme = toggleTheme;

// ─── App Origin / Role Check ──────────────────────────────────────────────────
const IS_STATIC_PREVIEW = window.location.port === "5500";
const API_ORIGIN = IS_STATIC_PREVIEW
    ? "https://127.0.0.1:5000"
    : window.location.origin;
const apiUrl = (path) => `${API_ORIGIN}${path}`;
const pageUrl = (path) => `${window.location.origin}${path}`;
const LOGIN_PATH = IS_STATIC_PREVIEW ? "/login.html" : "/login";

let rawRole = window.userRole || localStorage.getItem("role");
if (rawRole === "interviewer") rawRole = "host";
const userRole = rawRole;
if (!userRole) {
    window.location.replace(pageUrl(LOGIN_PATH));
}

if (userRole === "candidate") {
    document.body.classList.add("role-candidate");
}

// ─── DOM References ───────────────────────────────────────────────────────────
const videoElement = document.getElementById("main-video");
const btnMute = document.getElementById("btn-mute");
const btnVideo = document.getElementById("btn-video");
const iconMute = document.getElementById("icon-mute");
const iconVideo = document.getElementById("icon-video");
const textMute = document.getElementById("text-mute");
const textVideo = document.getElementById("text-video");
const gazeAlert = document.getElementById("alert");
const aiOutput = document.getElementById("aiOutput");
const audioBars = document.getElementById("audio-bars");
const timerDisplay = document.getElementById("timer-display");
const startTimeEl = document.getElementById("start-time");
const sessionStatusTitle = document.getElementById("session-status-title");
const sessionStatusMessage = document.getElementById("session-status-message");
const userProfileBtn = document.getElementById("user-profile-btn");
const btnShield = document.getElementById("btn-shield");
const btnChat = document.getElementById("btn-chat");
const btnNotes = document.getElementById("btn-notes");
const btnMore = document.getElementById("btn-more");
const btnViewAudit = document.getElementById("btn-view-audit");
const btnReviewAlert = document.getElementById("btn-review-alert");
const topAlertBanner = document.getElementById("top-alert-banner");
const alertBannerTitle = document.getElementById("alert-banner-title");
const alertBannerMsg = document.getElementById("alert-banner-msg");
const alertBannerConf = document.getElementById("alert-banner-conf");
const videoNameEl = document.getElementById("candidate-display-name");
const videoRoleEl = document.getElementById("candidate-display-role");
const interviewerRoleEl = document.getElementById("interviewer-role-label");
const interviewerNameEl = document.getElementById("interviewer-name");
const interviewerTitleEl = document.getElementById("interviewer-title");
const interviewerAvatarEl = document.getElementById("interviewer-avatar");
const candidateStatusNameEl = document.getElementById("candidate-status-name");
const candidateStatusTitleEl = document.getElementById(
    "candidate-status-title",
);
const candidateAvatarEl = document.getElementById("candidate-avatar");
const meetingListEl = document.getElementById("meetingList");
const senderInput = document.getElementById("sender");
const timelineEl = document.getElementById("timeline");
const eyeAnalysisPopup = document.getElementById("eye-analysis-popup");
const logoutModal = document.getElementById("logout-modal");
const chatPanel = document.getElementById("chat-panel");
const btnCloseChat = document.getElementById("btn-close-chat");
const chatMessages = document.getElementById("chat-messages");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const notesModal = document.getElementById("notes-modal");
const btnCloseNotes = document.getElementById("btn-close-notes");
const btnSaveNotes = document.getElementById("btn-save-notes");
const btnCopyNotes = document.getElementById("btn-copy-notes");
const notesEditor = document.getElementById("notes-editor");

// ─── State ────────────────────────────────────────────────────────────────────
let localStream = null;
let isMuted = false;
let isVideoStopped = false;

// Make these globally accessible for webrtc.js
window.isMuted = isMuted;
window.isVideoStopped = isVideoStopped;

// Functions to update global state
function updateGlobalMediaState() {
    window.isMuted = isMuted;
    window.isVideoStopped = isVideoStopped;
}
let suspiciousGazeEvents = 0;
let totalGazeFrames = 0;
let lookAwayFrames = 0;
let recentSuspiciousEvents = [];
let sessionStartTime = null;
let sessionTimerId = null;
let sessionHeartbeatId = null;
let latestSessionStatus = null;
let backendAvailable = false;
const currentDisplayName =
    localStorage.getItem("displayName") ||
    (userRole === "candidate" ? "Candidate" : "Interviewer");
const CHAT_STORAGE_KEY = "sessionChatMessages";

window.proctoringSettings = {
    gazeSensitivity: "medium",
    allowedTabSwitches: 3,
    gazeCheck: true,
    audioCheck: true,
    vmCheck: true,
    dualMonitorCheck: true,
    devToolsCheck: true,
    clipboardCheck: true
};

window.candidateMicMuted = false;
window.candidateVideoStopped = false;


// ─── Session Timer ────────────────────────────────────────────────────────────
function renderWaitingSessionState() {
    if (timerDisplay) timerDisplay.textContent = "WAITING";
    if (startTimeEl) startTimeEl.textContent = "--:--";
    if (sessionStatusTitle) sessionStatusTitle.textContent = "Session Pending";
    if (sessionStatusMessage)
        sessionStatusMessage.textContent =
            "Waiting for interviewer and candidate to join";
}

function initialOf(name, fallback) {
    return (name || fallback || "?").trim().charAt(0).toUpperCase() || fallback;
}

let bannerTimeout = null;

// Escape untrusted strings before interpolating into innerHTML (audit titles,
// YOLO object names, third-party ISP/location data, etc.).
function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function showBanner(title, message, confidence = "", tone = "info") {
    // Suppress cheating/proctoring warnings on Candidate screen
    if (userRole === "candidate" && (tone === "warning" || tone === "danger" || title.toLowerCase().includes("cheat") || title.toLowerCase().includes("gaze") || title.toLowerCase().includes("switch") || title.toLowerCase().includes("copy") || title.toLowerCase().includes("paste"))) {
        console.log(`Suppressing warning banner on Candidate screen: [${title}] ${message}`);
        return;
    }
    if (topAlertBanner) {
        topAlertBanner.classList.remove("info", "success", "warning", "danger");
        topAlertBanner.classList.add(tone);
        
        topAlertBanner.style.display = "flex";
        // Force reflow to ensure transition registers
        topAlertBanner.offsetHeight;
        topAlertBanner.classList.add("show");
        
        clearTimeout(bannerTimeout);
        bannerTimeout = setTimeout(() => {
            topAlertBanner.classList.remove("show");
            setTimeout(() => {
                if (!topAlertBanner.classList.contains("show")) {
                    topAlertBanner.style.display = "none";
                }
            }, 500);
        }, 4000);
    }
    if (alertBannerTitle) alertBannerTitle.textContent = title;
    if (alertBannerMsg) alertBannerMsg.textContent = message;
    if (alertBannerConf) alertBannerConf.textContent = confidence;
}

async function checkBackendAvailability() {
    try {
        const response = await fetch(apiUrl("/health"));
        backendAvailable = response.ok;
    } catch {
        backendAvailable = false;
    }

    if (!backendAvailable) {
        const envFace = document.getElementById("env-face");
        if (envFace) envFace.textContent = "Backend offline";
        setGazeAlert(
            "Gaze backend offline - start python app.py on port 5000",
            true,
        );
    }
}

function getStoredChatMessages() {
    try {
        const parsed = JSON.parse(
            localStorage.getItem(CHAT_STORAGE_KEY) || "[]",
        );
        return Array.isArray(parsed) ? parsed : [];
    } catch {
        return [];
    }
}

function saveStoredChatMessages(messages) {
    localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(messages));
}

function appendChatMessage(author, message, type = "self", persist = true) {
    if (!chatMessages) return;

    const item = document.createElement("div");
    const title = document.createElement("strong");
    const body = document.createElement("p");

    item.className = `chat-message ${type}`;
    title.textContent = author;
    body.textContent = message;

    item.appendChild(title);
    item.appendChild(body);
    chatMessages.appendChild(item);
    chatMessages.scrollTop = chatMessages.scrollHeight;

    if (!persist) return;

    if (chatPanel && !chatPanel.classList.contains("open") && type !== "self" && type !== "system") {
        unreadMessageCount++;
        updateChatBadge();
    }

    const storedMessages = getStoredChatMessages();
    storedMessages.push({ author, message, type });
    saveStoredChatMessages(storedMessages);
}

function loadChatHistory() {
    if (!chatMessages) return;

    const storedMessages = getStoredChatMessages();
    const messagesToRender =
        storedMessages.length > 0
            ? storedMessages
            : [
                  {
                      author: "System",
                      message: "Session chat is ready.",
                      type: "system",
                  },
              ];

    if (storedMessages.length === 0) {
        saveStoredChatMessages(messagesToRender);
    }

    chatMessages.innerHTML = "";
    messagesToRender.forEach(({ author, message, type }) => {
        appendChatMessage(author, message, type, false);
    });
}

let unreadMessageCount = 0;
const chatBadge = document.getElementById("chat-badge");

function updateChatBadge() {
    if (chatBadge) {
        if (unreadMessageCount > 0) {
            chatBadge.style.display = "flex";
            chatBadge.textContent = unreadMessageCount > 9 ? "9+" : unreadMessageCount.toString();
        } else {
            chatBadge.style.display = "none";
        }
    }
}

function openChatPanel() {
    if (!chatPanel) return;
    chatPanel.classList.add("open");
    chatPanel.setAttribute("aria-hidden", "false");
    unreadMessageCount = 0;
    updateChatBadge();
    if (chatInput) chatInput.focus();
}

function closeChatPanel() {
    if (!chatPanel) return;
    chatPanel.classList.remove("open");
    chatPanel.setAttribute("aria-hidden", "true");
}

function openNotesModal() {
    if (!notesModal) return;
    if (notesEditor && !notesEditor.value.trim()) {
        notesEditor.value =
            localStorage.getItem("sessionNotes") || buildNotesSummary();
    }
    notesModal.classList.add("open");
    notesModal.setAttribute("aria-hidden", "false");
}

function closeNotesModal() {
    if (!notesModal) return;
    notesModal.classList.remove("open");
    notesModal.setAttribute("aria-hidden", "true");
}

function syncParticipantUI(status = latestSessionStatus) {
    const participants = status?.participants || {};
    const interviewer = participants.interviewer;
    const candidate = participants.candidate;

    if (senderInput && !senderInput.value.trim())
        senderInput.value = currentDisplayName;

    if (userRole === "interviewer" || userRole === "host") {
        if (interviewerRoleEl) interviewerRoleEl.textContent = "INTERVIEWER";
        if (interviewerNameEl)
            interviewerNameEl.textContent = currentDisplayName;
        if (interviewerTitleEl) {
            interviewerTitleEl.textContent = status?.bothPresent
                ? "Interview host connected"
                : "Waiting for candidate";
        }
        if (interviewerAvatarEl) {
            interviewerAvatarEl.textContent = initialOf(
                currentDisplayName,
                "I",
            );
        }

        if (candidate) {
            if (videoNameEl)
                videoNameEl.textContent = candidate.displayName || "Candidate";
            if (videoRoleEl) {
                videoRoleEl.textContent = status?.bothPresent
                    ? "Candidate connected"
                    : "Candidate joined the dashboard";
            }
            if (candidateStatusNameEl) {
                candidateStatusNameEl.textContent =
                    candidate.displayName || "Candidate";
            }
            if (candidateStatusTitleEl) {
                candidateStatusTitleEl.textContent = status?.bothPresent
                    ? "Connected to session"
                    : "Joined and waiting for session start";
            }
            if (candidateAvatarEl) {
                candidateAvatarEl.textContent = initialOf(
                    candidate.displayName,
                    "C",
                );
            }
        } else {
            if (videoNameEl) videoNameEl.textContent = "Candidate";
            if (videoRoleEl) videoRoleEl.textContent = "Waiting for candidate";
            if (candidateStatusNameEl)
                candidateStatusNameEl.textContent = "Candidate";
            if (candidateStatusTitleEl) {
                candidateStatusTitleEl.textContent =
                    "Awaiting candidate details";
            }
            if (candidateAvatarEl) candidateAvatarEl.textContent = "C";
        }
        return;
    }

    const interviewerName = interviewer?.displayName || "Interviewer";
    if (videoNameEl) videoNameEl.textContent = currentDisplayName;
    if (videoRoleEl) {
        videoRoleEl.textContent = status?.bothPresent
            ? "Interview in progress"
            : "Candidate view";
    }
    if (interviewerRoleEl) interviewerRoleEl.textContent = "INTERVIEWER";
    if (interviewerNameEl) interviewerNameEl.textContent = interviewerName;
    if (interviewerTitleEl) {
        interviewerTitleEl.textContent = interviewer
            ? "Interview host connected"
            : "Waiting for interviewer";
    }
    if (interviewerAvatarEl) {
        interviewerAvatarEl.textContent = initialOf(interviewerName, "I");
    }
    if (candidateStatusNameEl)
        candidateStatusNameEl.textContent = currentDisplayName;
    if (candidateStatusTitleEl) {
        candidateStatusTitleEl.textContent = status?.bothPresent
            ? "You are connected"
            : "Current participant";
    }
    if (candidateAvatarEl) {
        candidateAvatarEl.textContent = initialOf(currentDisplayName, "C");
    }
}

function buildNotesSummary() {
    const participants = latestSessionStatus?.participants || {};
    const candidateName = participants.candidate?.displayName || "Candidate";
    const interviewerName =
        participants.interviewer?.displayName || "Interviewer";
    const sessionLabel = latestSessionStatus?.bothPresent
        ? "Session is active"
        : "Session is pending";

    return [
        `Session notes`,
        `- ${sessionLabel}`,
        `- Interviewer: ${interviewerName}`,
        `- Candidate: ${candidateName}`,
        `- Security events: ${suspiciousGazeEvents}`,
        `- Attention frames analyzed: ${totalGazeFrames}`,
    ].join("\n");
}

function scrollAuditIntoView() {
    if (!timelineEl) return;
    timelineEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
    timelineEl.scrollTop = 0;
}

function updateTimer() {
    if (!sessionStartTime) {
        renderWaitingSessionState();
        return;
    }

    const elapsed = Math.max(
        0,
        Math.floor((Date.now() - sessionStartTime) / 1000),
    );
    const mm = String(Math.floor(elapsed / 60)).padStart(2, "0");
    const ss = String(elapsed % 60).padStart(2, "0");
    if (timerDisplay) timerDisplay.textContent = `${mm}:${ss}`;
}

function applySessionStatus(status) {
    latestSessionStatus = status || null;
    syncParticipantUI(latestSessionStatus);

    const startedAt = Number(status?.startedAt);
    const bothPresent = Boolean(
        status?.bothPresent && Number.isFinite(startedAt) && startedAt > 0,
    );

    // Proctoring monitors become meaningful once the session actually starts;
    // enabling here lets browser-side alerts (tab switch, clipboard, VPN, …)
    // pass the addAuditAlert guard even before the first gaze frame arrives.
    window.sessionActive = bothPresent;
    if (bothPresent) window.proctoringActive = true;

    if (!bothPresent) {
        sessionStartTime = null;
        renderWaitingSessionState();
        return;
    }

    sessionStartTime = startedAt * 1000;
    
    // Clear timeline and session storage once session officially starts
    if (!window._sessionStartedFlag) {
        window._sessionStartedFlag = true;
        // Fresh session: clear any per-session proctoring state (liveness
        // challenge streak/status) so one candidate never inherits another's.
        if (typeof window.resetLivenessChallengeState === "function") {
            window.resetLivenessChallengeState();
        }
        const timeline = document.getElementById("timeline");
        if (timeline) timeline.innerHTML = "";
        sessionStorage.removeItem("fullAuditLog_" + MEETING_ID);
        if (typeof UI_UPDATER !== "undefined" && UI_UPDATER.addAuditAlert) {
            UI_UPDATER.addAuditAlert("Session Started", "Both participants are connected.");
        }
    }
    
    if (startTimeEl) {
        startTimeEl.textContent = new Date(sessionStartTime).toLocaleTimeString(
            [],
            { hour: "2-digit", minute: "2-digit" },
        );
    }
    if (sessionStatusTitle) sessionStatusTitle.textContent = "Session Started";
    if (sessionStatusMessage) {
        sessionStatusMessage.textContent =
            "Both interviewer and candidate are connected";
    }
    updateTimer();
}

async function postSessionState(endpoint) {
    const meetingId = typeof MEETING_ID !== 'undefined' ? MEETING_ID : "";
    const response = await fetch(apiUrl(endpoint), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            role: userRole,
            displayName: localStorage.getItem("displayName") || "",
            meetingId: meetingId,
        }),
        keepalive: true,
    });

    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

async function joinSession() {
    try {
        const status = await postSessionState("/session/join");
        applySessionStatus(status);

        if (!sessionHeartbeatId) {
            sessionHeartbeatId = setInterval(async () => {
                try {
                    const heartbeatStatus =
                        await postSessionState("/session/heartbeat");
                    applySessionStatus(heartbeatStatus);
                } catch (error) {
                    console.warn("session heartbeat failed:", error.message);
                }
            }, 5000);
        }
    } catch (error) {
        console.warn("joinSession failed:", error.message);
        renderWaitingSessionState();
    }
}

renderWaitingSessionState();
if (!sessionTimerId) sessionTimerId = setInterval(updateTimer, 1000);

// ─── Role-aware UI labels ─────────────────────────────────────────────────────
syncParticipantUI();

// ─── Meetings API ─────────────────────────────────────────────────────────────
function loadMeetings() {
    fetch(apiUrl("/get_meetings"))
        .then((res) => res.json())
        .then((data) => {
            if (!meetingListEl) return;
            meetingListEl.innerHTML = "";

            if (!Array.isArray(data) || data.length === 0) {
                const li = document.createElement("li");
                li.className = "meeting-list-empty";
                li.textContent = "No saved meetings yet.";
                meetingListEl.appendChild(li);
                return;
            }

            data.slice()
                .reverse()
                .forEach((m) => {
                    const li = document.createElement("li");
                    const title = document.createElement("strong");
                    const meta = document.createElement("span");

                    li.className = "meeting-list-item";
                    title.textContent = m.title || "Untitled meeting";
                    meta.textContent = `${m.sender || "Unknown sender"} • ${m.time || "Time not set"}`;

                    li.appendChild(title);
                    li.appendChild(meta);
                    meetingListEl.appendChild(li);
                });
        })
        .catch((err) => console.warn("loadMeetings:", err));
}

async function createMeeting() {
    const payload = {
        sender:
            document.getElementById("sender")?.value.trim() ||
            currentDisplayName,
        title: document.getElementById("title")?.value.trim() || "",
        description: document.getElementById("description")?.value.trim() || "",
        time: document.getElementById("time")?.value.trim() || "",
    };

    if (!payload.title) {
        if (aiOutput) aiOutput.textContent = "Please enter a meeting title.";
        return;
    }

    try {
        const response = await fetch(apiUrl("/create_meeting"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        // FIX: Check ok before parsing
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const data = await response.json();
        if (aiOutput) aiOutput.textContent = data.message || "Meeting saved.";
        loadMeetings();
        showBanner(
            "Meeting saved",
            `${payload.title} has been added to the meeting list.`,
            "Saved locally",
            "success",
        );
    } catch (error) {
        console.error("createMeeting:", error);
        if (aiOutput)
            aiOutput.textContent =
                "Could not save meeting. Make sure the backend is running.";
    }
}

async function getAIInsights() {
    const description = document.getElementById("description")?.value.trim();
    if (!aiOutput) return;

    if (!description) {
        aiOutput.textContent = "Please paste a meeting description first.";
        return;
    }

    aiOutput.textContent = "Analyzing meeting…";

    try {
        const response = await fetch(apiUrl("/ai_insights"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ description }),
        });

        const data = await response.json();
        if (!response.ok)
            throw new Error(data.error || `HTTP ${response.status}`);

        aiOutput.textContent = data.insights || "No insights returned.";
    } catch (error) {
        console.error("getAIInsights:", error);
        aiOutput.textContent =
            "Could not get AI insights. Check that the backend is running (python app.py).";
    }
}

// Expose to HTML onclick handlers
window.createMeeting = createMeeting;
window.getAIInsights = getAIInsights;

// ─── End Interview ────────────────────────────────────────────────────────────
const btnEnd = document.getElementById("btn-end-interview");
if (btnEnd) {
    btnEnd.addEventListener("click", () => {
        const modal = document.getElementById("logout-modal");
        if (modal) modal.style.display = "flex";
    });
}

async function endInterview() {
    // Stop all media tracks
    if (localStream) {
        localStream.getTracks().forEach((t) => t.stop());
    }
    stopMLProcessing();
    if (sessionHeartbeatId) {
        clearInterval(sessionHeartbeatId);
        sessionHeartbeatId = null;
    }

    try {
        await postSessionState("/session/leave");
    } catch (error) {
        console.warn("session leave failed:", error.message);
    }

    localStorage.removeItem("role");
    localStorage.removeItem("displayName");
    window.location.href = pageUrl(LOGIN_PATH);
}
window.endInterview = endInterview;

// ─── Secondary Controls ───────────────────────────────────────────────────────
if (userProfileBtn) {
    userProfileBtn.addEventListener("click", () => {
        if (logoutModal) logoutModal.style.display = "flex";
    });
}

if (btnShield) {
    btnShield.addEventListener("click", () => {
        showBanner(
            "Session protection enabled",
            "Camera monitoring, gaze analysis, and audit tracking are currently active.",
            "Security status: active",
            "success",
        );
    });
}

if (btnChat) {
    btnChat.addEventListener("click", () => {
        if (chatPanel.classList.contains("open")) {
            closeChatPanel();
        } else {
            openChatPanel();
            showBanner(
                "Chat panel opened",
                "Session chat is ready for local messages and interviewer notes.",
                "Status: active",
                "info",
            );
        }
    });
}

if (btnCloseChat) {
    btnCloseChat.addEventListener("click", closeChatPanel);
}

if (chatForm) {
    chatForm.addEventListener("submit", (event) => {
        event.preventDefault();
        const message = chatInput?.value.trim();
        if (!message) return;

        appendChatMessage(currentDisplayName, message, "self");
        if (chatInput) chatInput.value = "";
        
        // Send message via socket if connected
        if (typeof socket !== 'undefined' && socket && socket.connected) {
            socket.emit("chat_message", {
                meetingId: MEETING_ID,
                sender: currentDisplayName,
                message: message
            });
        }
    });
}

// Listen for incoming chat messages via socket
function setupChatSocketListeners() {
    if (typeof socket !== 'undefined' && socket) {
        console.log("[Chat] Setting up chat socket listeners");
        socket.on("chat_message", (data) => {
            console.log("[Chat] Received chat message:", data);
            const sender = data.sender || "Unknown";
            const message = data.message || "";
            const type = sender === currentDisplayName ? "self" : "other";
            appendChatMessage(sender, message, type);
        });
    } else {
        console.warn("[Chat] Socket not available for chat listeners");
    }
}

// Setup chat listeners when socket is available
function waitForSocketAndSetupChat() {
    if (typeof socket !== 'undefined' && socket) {
        setupChatSocketListeners();
    } else {
        console.log("[Chat] Waiting for socket to be initialized...");
        // Wait for socket to be initialized
        const checkSocketInterval = setInterval(() => {
            if (typeof socket !== 'undefined' && socket) {
                setupChatSocketListeners();
                clearInterval(checkSocketInterval);
            }
        }, 100);
        
        // Clear interval after 10 seconds to prevent memory leak
        setTimeout(() => {
            clearInterval(checkSocketInterval);
            console.warn("[Chat] Socket initialization timeout");
        }, 10000);
    }
}

// Initialize chat socket listeners
waitForSocketAndSetupChat();

if (btnNotes) {
    btnNotes.addEventListener("click", () => {
        if (notesModal.classList.contains("open")) {
            closeNotesModal();
        } else {
            if (notesEditor) {
                notesEditor.value =
                    localStorage.getItem("sessionNotes") || buildNotesSummary();
            }
            openNotesModal();
            showBanner(
                "Notes panel opened",
                "Session notes are ready to review and edit.",
                "Status: editable",
                "info",
            );
        }
    });
}

if (btnCloseNotes) {
    btnCloseNotes.addEventListener("click", closeNotesModal);
}

if (btnSaveNotes) {
    btnSaveNotes.addEventListener("click", () => {
        const noteText = notesEditor?.value.trim() || buildNotesSummary();
        localStorage.setItem("sessionNotes", noteText);
        if (aiOutput) aiOutput.textContent = noteText;
        showBanner(
            "Notes saved",
            "Session notes were saved successfully.",
            "Status: saved",
            "success",
        );
        closeNotesModal();
    });
}

if (btnCopyNotes) {
    btnCopyNotes.addEventListener("click", async () => {
        const noteText = notesEditor?.value.trim() || buildNotesSummary();
        try {
            await navigator.clipboard.writeText(noteText);
            showBanner(
                "Notes copied",
                "Session notes were copied to the clipboard.",
                "Status: copied",
                "success",
            );
        } catch (error) {
            console.warn("copy notes failed:", error.message);
            showBanner(
                "Copy unavailable",
                "Clipboard access is blocked. You can still select and copy the notes manually.",
                "Status: blocked",
                "warning",
            );
        }
    });
}

if (btnMore) {
    btnMore.addEventListener("click", () => {
        if (!eyeAnalysisPopup) return;
        const currentlyHidden =
            window.getComputedStyle(eyeAnalysisPopup).display === "none";
        eyeAnalysisPopup.style.display = currentlyHidden ? "block" : "none";
        showBanner(
            currentlyHidden
                ? "Eye analysis panel opened"
                : "Eye analysis panel hidden",
            currentlyHidden
                ? "The live eye analysis panel is now visible."
                : "The live eye analysis panel has been hidden.",
            currentlyHidden ? "Status: visible" : "Status: hidden",
            "info",
        );
    });
}

if (btnViewAudit) {
    btnViewAudit.addEventListener("click", () => {
        const modal = document.getElementById("audit-log-modal");
        const timeline = document.getElementById("modal-full-timeline");
        if (!modal || !timeline) return;
        
        const logs = JSON.parse(sessionStorage.getItem('fullAuditLog_' + MEETING_ID) || '[]');
        if (logs.length === 0) {
            timeline.innerHTML = '<p style="color: var(--text-secondary);">No audit events recorded yet.</p>';
        } else {
            timeline.innerHTML = '';
            logs.reverse().forEach(log => {
                const item = document.createElement('div');
                item.className = `timeline-item${log.isCritical ? ' critical' : ''}`;
                item.innerHTML = `
                    <div class="marker"></div>
                    <div class="content">
                        <h5 style="margin: 0 0 0.25rem; font-size: 1rem; color: var(--text-primary);">${escapeHtml(log.title)} ${log.confidence ? `<span class="confidence" style="margin-left: 0.5rem; font-size: 0.8rem; padding: 0.1rem 0.4rem; border-radius: 4px; background: rgba(255,255,255,0.1);">${escapeHtml(log.confidence)}</span>` : ''}</h5>
                        <p style="margin: 0; color: var(--text-secondary); font-size: 0.9rem;">${escapeHtml(log.message)}</p>
                    </div>
                    <span class="time" style="font-size: 0.8rem; color: var(--text-secondary); margin-top: 0.25rem; display: block;">${escapeHtml(log.timeStr)}</span>
                `;
                timeline.appendChild(item);
            });
        }
        modal.classList.add("open");
    });
}

if (btnReviewAlert) {
    btnReviewAlert.addEventListener("click", scrollAuditIntoView);
}

if (notesModal) {
    notesModal.addEventListener("click", (event) => {
        if (event.target === notesModal) closeNotesModal();
    });
}

window.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    closeChatPanel();
    closeNotesModal();
});

// ─── UI Updaters ──────────────────────────────────────────────────────────────
const UI_UPDATER = {
    updateRiskScore(scorePercent) {
        // FIX: Use IDs instead of fragile deep querySelector chains
        const pct = document.getElementById("risk-percentage");
        const circle = document.getElementById("risk-circle");
        const btn = document.getElementById("risk-level-btn");

        if (pct) pct.textContent = `${scorePercent}%`;
        if (circle)
            circle.setAttribute("stroke-dasharray", `${scorePercent}, 100`);

        if (btn) {
            if (scorePercent < 30) {
                btn.textContent = "LOW";
                btn.className = "btn primary";
            } else if (scorePercent < 65) {
                btn.textContent = "MODERATE";
                btn.className = "btn primary";
                btn.style.background =
                    "linear-gradient(135deg,#f59e0b,#d97706)";
            } else {
                btn.textContent = "HIGH";
                btn.className = "btn primary";
                btn.style.background =
                    "linear-gradient(135deg,#ef4444,#b91c1c)";
            }
        }
    },

    updateGazeAttention(scorePercent) {
        const val = document.getElementById("attention-value");
        const bar = document.getElementById("attention-bar");
        // FIX: Use IDs for reliable lookup
        if (val) val.textContent = `${scorePercent}%`;
        if (bar) bar.style.width = `${scorePercent}%`;

        // Update trust score circle
        const trustCircle = document.getElementById("trust-circle");
        const trustValue = document.getElementById("trust-value");
        if (trustCircle)
            trustCircle.setAttribute(
                "stroke-dasharray",
                `${scorePercent}, 100`,
            );
        if (trustValue) trustValue.textContent = `${scorePercent}%`;

        // Update focus / looking away
        const focusEl = document.getElementById("focus-level");
        const awayPctEl = document.getElementById("looking-away-pct");
        const insightEl = document.getElementById("insight-eye");

        if (focusEl)
            focusEl.textContent =
                scorePercent >= 80
                    ? "High"
                    : scorePercent >= 50
                      ? "Medium"
                      : "Low";
        if (awayPctEl) awayPctEl.textContent = `${100 - scorePercent}%`;
        if (insightEl) {
            insightEl.textContent = scorePercent >= 75 ? "Good" : "Poor";
            insightEl.className = `status ${scorePercent >= 75 ? "green" : "red"}`;
        }
    },

    addAuditAlert(title, message, confidence, isCritical = false, fromRemote = false) {
        // Guard: Prevent any proctoring/cheating alerts from registering/sending
        // until the session is active (both participants connected) or the
        // candidate's face has been seen at least once.
        const isConnectionEvent = title === "Participant Joined" || title === "Participant Left" || title === "Session Started";
        if (!window.proctoringActive && !window.sessionActive && !isConnectionEvent) {
            console.log(`Proctoring not active yet. Ignoring alert: [${title}] ${message}`);
            return;
        }

        const isCandidate = typeof userRole !== "undefined" && userRole === "candidate";
        if (!fromRemote && isCandidate && typeof socket !== "undefined" && socket) {
            socket.emit("audit_event", {
                meetingId: MEETING_ID,
                title: title,
                message: message,
                confidence: confidence,
                isCritical: isCritical,
            });
        }

        // Save to sessionStorage for Full Audit Log view
        const timeStr = new Date().toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
        });
        const event = { title, message, confidence, isCritical, timeStr };
        try {
            const logs = JSON.parse(sessionStorage.getItem('fullAuditLog_' + MEETING_ID) || '[]');
            logs.push(event);
            sessionStorage.setItem('fullAuditLog_' + MEETING_ID, JSON.stringify(logs));
        } catch (e) { /* storage unavailable */ }

        // Append to the live audit timeline
        const timeline = document.getElementById("timeline");
        if (timeline) {
            const item = document.createElement("div");
            item.className = `timeline-item${isCritical ? " critical" : ""}`;
            item.innerHTML = `
                <div class="marker"></div>
                <div class="content">
                    <h5>${escapeHtml(title)} ${confidence ? `<span class="confidence">${escapeHtml(confidence)}</span>` : ""}</h5>
                    <p>${escapeHtml(message)}</p>
                </div>
                <span class="time">${escapeHtml(timeStr)}</span>
            `;
            timeline.appendChild(item);
            timeline.scrollTop = timeline.scrollHeight;
        }

        // Surface critical events via the top banner
        if (isCritical && typeof showBanner !== "undefined") {
            showBanner(title, message, confidence, "danger");
        }
    },
};

// ─── Unified Risk Score Engine + Signal Fusion ──────────────────────────────
// Every cheating signal (gaze, head pose, YOLO objects, liveness, audio,
// forensics, network, …) routes through bumpRisk(weight, source, confidence).
//
// 1. CONFIDENCE: each detection reports how sure it is (0-100). The gauge is
//    precision-first — a low-confidence signal contributes less weight:
//    effective = weight * (0.35 + 0.65 * confidence/100).
// 2. FUSION: signals are also logged into a sliding window. When >= 2
//    INDEPENDENT sources agree within the window with sufficient average
//    confidence, a single fused critical alert fires — a lone weak signal
//    (one glance away, one whisper) can never escalate alone.
// 3. COOLDOWN: fused alerts are globally rate-limited so the trail can't be
//    spammed even when several detectors are legitimately busy.
const RISK_EVENT_DECAY_MS = 45000;  // each weight halves every ~31s
const RISK_MAX_AGE_MS = 300000;     // drop events older than 5 minutes
const RISK_MAX_EVENTS = 100;        // cap memory in a long session
const FUSION_WINDOW_MS = 60000;     // signals agree within this window
const FUSION_MIN_SOURCES = 2;       // distinct sources required to fuse
const FUSION_MIN_SOURCE_CONFIDENCE = 40; // a source below this is "uncertain"
const FUSION_MIN_AVG_CONFIDENCE = 50; // …with at least this avg confidence
const FUSION_COOLDOWN_MS = 60000;   // global cooldown between fused alerts
window.riskEvents = [];
window.signalLog = [];
window.lastFusedAlertAt = 0;

function bumpRisk(weight, source = "", confidence = 100) {
    const now = Date.now();
    const conf = Math.max(1, Math.min(100, Number(confidence) || 100));
    // Precision-first weighting: low-confidence signals barely move the gauge.
    const effectiveWeight = Math.max(
        1,
        Math.round(weight * (0.35 + 0.65 * (conf / 100))),
    );
    window.riskEvents.push({ t: now, w: effectiveWeight, s: source, c: conf });
    if (window.riskEvents.length > RISK_MAX_EVENTS) {
        window.riskEvents = window.riskEvents.slice(-RISK_MAX_EVENTS);
    }
    // Keep the historical counter (notes summary / audit display) — this is
    // an event count now, not the score.
    if (typeof suspiciousGazeEvents !== "undefined") {
        suspiciousGazeEvents = Math.min(999, suspiciousGazeEvents + 1);
    }
    // Feed the fusion window (skip self-triggered fusion bumps).
    if (source !== "fusion") {
        window.signalLog.push({ t: now, s: source, w: weight, c: conf });
        evaluateFusion();
    }
    return recomputeRiskScore();
}

function evaluateFusion() {
    if (typeof UI_UPDATER === "undefined" || !UI_UPDATER.addAuditAlert) return;
    const now = Date.now();
    // Prune the window; ignore fusion's own events. A signal below
    // FUSION_MIN_SOURCE_CONFIDENCE is an UNCERTAIN state (e.g. "no face seen"
    // at confidence 30), not a positive detection — it must neither count as
    // an agreeing source nor drag the average down. This keeps a lone camera-
    // cover from ever fusing with an unrelated detector.
    window.signalLog = window.signalLog.filter(
        (e) =>
            now - e.t <= FUSION_WINDOW_MS &&
            e.s &&
            e.s !== "fusion" &&
            e.c >= FUSION_MIN_SOURCE_CONFIDENCE,
    );
    const sources = [...new Set(window.signalLog.map((e) => e.s))];
    if (sources.length < FUSION_MIN_SOURCES) return;
    const avgConf =
        window.signalLog.reduce((a, e) => a + e.c, 0) / window.signalLog.length;
    if (avgConf < FUSION_MIN_AVG_CONFIDENCE) return;
    if (now - window.lastFusedAlertAt < FUSION_COOLDOWN_MS) return;
    window.lastFusedAlertAt = now;

    UI_UPDATER.addAuditAlert(
        "Multiple Signals — Cheating Suspected",
        `${sources.length} independent signals agreed within the last minute: ` +
            `${sources.join(", ")} (avg confidence ${Math.round(avgConf)}%).`,
        "FUSED",
        true,
    );
    // Bump the gauge directly (bypass bumpRisk to avoid recursion).
    window.riskEvents.push({ t: now, w: 25, s: "fusion", c: avgConf });
    if (window.riskEvents.length > RISK_MAX_EVENTS) {
        window.riskEvents = window.riskEvents.slice(-RISK_MAX_EVENTS);
    }
    recomputeRiskScore();
}

function recomputeRiskScore() {
    const now = Date.now();
    window.riskEvents = window.riskEvents.filter(
        (e) => now - e.t <= RISK_MAX_AGE_MS,
    );
    let sum = 0;
    for (const e of window.riskEvents) {
        sum += e.w * Math.exp(-(now - e.t) / RISK_EVENT_DECAY_MS);
    }
    const risk = Math.max(0, Math.min(100, Math.round(sum)));
    if (typeof UI_UPDATER !== "undefined" && UI_UPDATER.updateRiskScore) {
        UI_UPDATER.updateRiskScore(risk);
    }
    return risk;
}
window.bumpRisk = bumpRisk;
window.recomputeRiskScore = recomputeRiskScore;
window.evaluateFusion = evaluateFusion;

// ─── Gaze Alert Overlay ────────────────────────────────────────────────────
function setGazeAlert(message, critical) {
    // Suppress gaze alerts on candidate screen - only show on host page
    if (userRole === "candidate") {
        console.log(`Suppressing gaze alert on Candidate screen: ${message}`);
        return;
    }
    
    const alertEl = document.getElementById("alert");
    if (!alertEl) return;
    alertEl.textContent = message;
    alertEl.classList.toggle("active", Boolean(critical));
}

// ─── Pupil Animation ───────────────────────────────────────────────────────
function animatePupil(direction) {
    const pupil = document.getElementById("pupil");
    if (!pupil) return;
    const offsets = {
        LEFT: { x: -30, y: 0 },
        RIGHT: { x: 30, y: 0 },
        LOOKING_UP: { x: 0, y: -14 },
        LOOKING_DOWN: { x: 0, y: 14 },
        CENTER: { x: 0, y: 0 },
        NO_FACE: { x: 0, y: 0 },
    };
    const off = offsets[direction] || offsets.CENTER;
    pupil.style.transform = `translate(${off.x}px, ${off.y}px)`;
}

// ─── Gaze Data UI Update ───────────────────────────────────────────────────
function updateGazeUI(data) {
    if (!data) return;
    console.log("[UI] Updating gaze UI with data:", data);
    const now = Date.now();
    let direction = data.direction || "NO_FACE";
    let lookingAway = data.lookingAway === true;

    // Gaze + head-pose thresholds (client-side refinement of backend verdict)
    if (window.proctoringActive && window.proctoringSettings.gazeCheck) {
        let pitchThresholdDown = -10;
        let pitchThresholdUp = 15;
        let yawThreshold = 15;

        if (window.proctoringSettings.gazeSensitivity === "low") {
            pitchThresholdDown = -15;
            pitchThresholdUp = 20;
            yawThreshold = 22;
        } else if (window.proctoringSettings.gazeSensitivity === "high") {
            pitchThresholdDown = -8;
            pitchThresholdUp = 12;
            yawThreshold = 10;
        }

        const pitch = data.pose?.pitch || 0;
        const yaw = data.pose?.yaw || 0;

        if (direction === "NO_FACE") {
            lookingAway = true;
        } else {
            if (pitch < pitchThresholdDown) {
                direction = "LOOKING_DOWN";
                lookingAway = true;
            } else if (pitch > pitchThresholdUp) {
                direction = "LOOKING_UP";
                lookingAway = true;
            } else if (yaw < -yawThreshold) {
                direction = "RIGHT";
                lookingAway = true;
            } else if (yaw > yawThreshold) {
                direction = "LEFT";
                lookingAway = true;
            } else {
                if (direction === "LEFT" || direction === "RIGHT") {
                    if (window.proctoringSettings.gazeSensitivity !== "low") {
                        lookingAway = true;
                    } else {
                        direction = "CENTER";
                    }
                }
            }
        }
    }

    // Dwell-based smoothing: momentary glances must persist for ~2-5s before
    // they count as real "looking away" events (cuts single-frame false alarms
    // from brief head movement or a glance at the interviewer).
    const dwellMs =
        window.proctoringSettings.gazeSensitivity === "low" ? 5000
        : window.proctoringSettings.gazeSensitivity === "high" ? 2000
        : 3000;
    let sustainedAway = false;
    if (lookingAway) {
        if (window._lastAwayDir !== direction) {
            window._awaySince = now;
            window._lastAwayDir = direction;
        }
        sustainedAway = now - window._awaySince >= dwellMs;
    } else {
        window._awaySince = null;
        window._lastAwayDir = null;
    }

    // Proctoring becomes active once a face has been seen at least once
    // For host, activate immediately when receiving gaze data
    if (data.faceDetected || (userRole === "host" || userRole === "interviewer")) {
        window.proctoringActive = true;
    }

    // Vision Tracking summary (host dashboard)
    const statFace = document.getElementById("stat-face-detected");
    if (statFace) {
        statFace.textContent = data.faceDetected ? "Detected" : "Not Detected";
        statFace.className = data.faceDetected ? "green-text" : "gray-text";
    }
    const statCamera = document.getElementById("stat-looking-camera");
    if (statCamera) {
        const ok = data.faceDetected && !data.lookingAway;
        statCamera.textContent = ok ? "Yes" : "No";
        statCamera.className = ok ? "green-text" : "gray-text";
    }
    // statCamera reflects the instant signal (camera contact is immediate);
    // statAway uses sustainedAway so the summary doesn't flicker on brief
    // glances — deliberately different, not a bug.
    const statAway = document.getElementById("stat-looking-away");
    if (statAway) {
        const away = sustainedAway;
        statAway.textContent = away ? "Yes" : "No";
        statAway.className = away ? "danger-text" : "gray-text";
    }
    const statPose = document.getElementById("stat-head-pose-abnormal");
    if (statPose) {
        const abnormal = !!(data.pose && data.pose.abnormal);
        statPose.textContent = abnormal ? "Yes" : "No";
        statPose.className = abnormal ? "danger-text" : "gray-text";
    }
    const statRefl = document.getElementById("stat-reflections-detected");
    if (statRefl) {
        statRefl.textContent = data.reflectionDetected ? "Yes" : "No";
        statRefl.className = data.reflectionDetected ? "danger-text" : "gray-text";
    }

    // Update direction label
    const dirLabel = document.getElementById("gaze-direction-label");
    if (dirLabel) dirLabel.textContent = direction;

    // Update Head Pose label
    const poseLabel = document.getElementById("head-pose-label");
    if (poseLabel) {
        if (direction === "LOOKING_DOWN" || direction === "LOOKING_UP") {
            poseLabel.textContent = direction;
        } else {
            poseLabel.textContent = "FORWARD";
        }
    }

    // Update Faces Detected label
    const faceCountEl = document.getElementById("face-count-label");
    if (faceCountEl) {
        const numFaces = data.multipleFaces ? "2+" : (data.faceDetected ? "1" : "0");
        faceCountEl.textContent = numFaces;
        faceCountEl.className = data.multipleFaces ? "value red" : "value white";
    }

    // Update Face Confidence label
    const faceConfidenceEl = document.getElementById("face-confidence-label");
    if (faceConfidenceEl) {
        const confidence = data.confidence || 0;
        faceConfidenceEl.textContent = `${Math.round(confidence)}%`;
        faceConfidenceEl.className = confidence > 70 ? "value green" : (confidence > 40 ? "value yellow" : "value red");
    }

    // Update environment face visibility
    const envFace = document.getElementById("env-face");
    if (envFace) envFace.textContent = data.faceDetected ? "Clear" : "Not Detected";

    // Animate pupil
    animatePupil(window.proctoringActive ? direction : "CENTER");

    // Prune events older than 30s (decay window for the risk score)
    recentSuspiciousEvents = recentSuspiciousEvents.filter(
        (t) => now - t <= 30000,
    );

    if (sustainedAway) {
        lookAwayFrames++;
        // Push at most one event per 4s so a long look-away elevates the risk
        // score without spiking it on the first dwell threshold crossing.
        if (!window._lastAwayEventAt || now - window._lastAwayEventAt >= 4000) {
            window._lastAwayEventAt = now;
            recentSuspiciousEvents.push(now);
            // Confidence comes from the backend verdict (MediaPipe 90, Haar 55,
            // no-face 30) — a camera cover alone won't reach fusion thresholds.
            bumpRisk(16, "gaze", data.confidence || 75);
        }

        let message = `Candidate looking ${direction.toLowerCase()}!`;
        if (direction === "NO_FACE") message = "Candidate face not detected!";
        if (direction === "LOOKING_DOWN") message = "Candidate is looking down (reading notes?)";
        if (direction === "LOOKING_UP") message = "Candidate is looking up significantly.";

        if (recentSuspiciousEvents.length > 5) {
            setGazeAlert("⚠ Cheating behavior detected!", true);
            if (now - (window.lastGazeAuditTime || 0) > 15000) {
                UI_UPDATER.addAuditAlert(
                    "Cheating Suspected",
                    "Repeated gaze anomalies detected",
                    "Live",
                    true,
                );
                window.lastGazeAuditTime = now;
            }
        } else {
            setGazeAlert(message, true);
            if (now - (window.lastGazeAuditTime || 0) > 10000) {
                UI_UPDATER.addAuditAlert(
                    "Gaze Anomaly",
                    message,
                    "Live",
                    direction === "NO_FACE" || direction === "LOOKING_DOWN",
                );
                window.lastGazeAuditTime = now;
            }
        }
    } else if (lookingAway) {
        // Brief glance — show it live but don't count it as an event yet
        setGazeAlert("Gaze: brief glance away", false);
    } else {
        window._lastAwayEventAt = null;
        setGazeAlert(window.proctoringActive ? "Gaze centered ✓" : "Waiting for candidate...", false);
    }

    // Head-pose anomaly signal (independent of gaze direction — feeds fusion).
    // The backend only flags after 2s of persistence, so this is already
    // high-precision; gate the risk bump on the same audit cooldown.
    if (data.pose && data.pose.abnormal && window.proctoringSettings.gazeCheck) {
        if (now - (window.lastHeadAuditTime || 0) > 15000) {
            window.lastHeadAuditTime = now;
            bumpRisk(10, "head", data.pose.confidence || 80);
        }
    }

    // Check for Multiple Faces
    if (data.multipleFaces && window.proctoringSettings.gazeCheck) {
        setGazeAlert("⚠ MULTIPLE FACES DETECTED!", true);
        if (now - (window.lastMultiFaceAuditTime || 0) > 20000) {
            UI_UPDATER.addAuditAlert(
                "Critical Security Breach",
                "Multiple faces detected in the camera frame!",
                "100%",
                true,
            );
            window.lastMultiFaceAuditTime = now;
            bumpRisk(15, "multiple_faces", 95);
        }
    }

    // Check for Corneal Screen Reflections
    if (data.reflectionDetected && window.proctoringSettings.gazeCheck) {
        setGazeAlert("⚠ SCREEN REFLECTION DETECTED IN EYE!", true);
        if (now - (window.lastReflectionAuditTime || 0) > 20000) {
            UI_UPDATER.addAuditAlert(
                "Hidden Device Detected",
                "Unnatural rectangular reflection detected in candidate's iris (possible phone or second monitor).",
                "95%",
                true,
            );
            window.lastReflectionAuditTime = now;
            bumpRisk(12, "reflection", 90);
        }
    }

    // Check for Contraband Objects (YOLO)
    if (data.objectsDetected && data.objectsDetected.length > 0 && window.proctoringSettings.gazeCheck) {
        const objectsStr = data.objectsDetected.join(", ");
        setGazeAlert(`⚠ ILLEGAL OBJECT DETECTED: ${objectsStr.toUpperCase()}`, true);
        if (now - (window.lastYoloAuditTime || 0) > 15000) {
            UI_UPDATER.addAuditAlert(
                "Contraband Object Detected",
                `YOLOv8 detected unauthorized items in the frame: ${objectsStr}`,
                "YOLO Vision",
                true,
            );
            window.lastYoloAuditTime = now;
            // YOLO reports its box confidence (2-run confirmed, so 85+ typical).
            bumpRisk(15, "yolo", data.objectsConfidence || 85);
        }
    }

    // Recalculate scores - for host, calculate as soon as we receive data
    // For candidate, only calculate if session has started
    const shouldCalculateScores = (userRole === "host" || userRole === "interviewer") || 
                                  (typeof sessionStartTime !== "undefined" && sessionStartTime);
    
    if (shouldCalculateScores) {
        const riskScore = recomputeRiskScore();
        const attentionScore = Math.max(0, Math.min(100, 100 - riskScore));

        UI_UPDATER.updateGazeAttention(attentionScore);
    }
}
// ─── Draggable Picture-in-Picture ─────────────────────────────────────────────
function makeElementDraggable(elmnt) {
    if (!elmnt) return;
    let pos1 = 0, pos2 = 0, pos3 = 0, pos4 = 0;
    
    elmnt.style.cursor = 'grab';
    
    elmnt.onmousedown = dragMouseDown;
    elmnt.ontouchstart = dragTouchStart;

    function dragMouseDown(e) {
        e = e || window.event;
        if (e.target.tagName === 'BUTTON' || e.target.tagName === 'INPUT') return;
        e.preventDefault();
        pos3 = e.clientX;
        pos4 = e.clientY;
        document.addEventListener('mouseup', closeDragElement);
        document.addEventListener('mousemove', elementDrag);
        elmnt.style.cursor = 'grabbing';
    }

    function elementDrag(e) {
        e = e || window.event;
        e.preventDefault();
        pos1 = pos3 - e.clientX;
        pos2 = pos4 - e.clientY;
        pos3 = e.clientX;
        pos4 = e.clientY;
        
        const newTop = elmnt.offsetTop - pos2;
        const newLeft = elmnt.offsetLeft - pos1;
        
        const parent = elmnt.parentElement;
        const parentWidth = parent ? parent.clientWidth : window.innerWidth;
        const parentHeight = parent ? parent.clientHeight : window.innerHeight;
        
        const topBound = Math.max(0, Math.min(newTop, parentHeight - elmnt.clientHeight));
        const leftBound = Math.max(0, Math.min(newLeft, parentWidth - elmnt.clientWidth));

        elmnt.style.top = topBound + "px";
        elmnt.style.left = leftBound + "px";
        elmnt.style.right = "auto";
        elmnt.style.bottom = "auto";
    }

    function closeDragElement() {
        document.removeEventListener('mouseup', closeDragElement);
        document.removeEventListener('mousemove', elementDrag);
        elmnt.style.cursor = 'grab';
    }
    
    function dragTouchStart(e) {
        if (e.target.tagName === 'BUTTON' || e.target.tagName === 'INPUT') return;
        const touch = e.touches[0];
        pos3 = touch.clientX;
        pos4 = touch.clientY;
        document.addEventListener('touchend', closeDragTouch, {passive: false});
        document.addEventListener('touchmove', elementTouchDrag, {passive: false});
        elmnt.style.cursor = 'grabbing';
    }
    
    function elementTouchDrag(e) {
        const touch = e.touches[0];
        pos1 = pos3 - touch.clientX;
        pos2 = pos4 - touch.clientY;
        pos3 = touch.clientX;
        pos4 = touch.clientY;
        
        const newTop = elmnt.offsetTop - pos2;
        const newLeft = elmnt.offsetLeft - touch.clientX; // Wait, let's make sure it is touch.clientX, pos1 calculation takes care of it, offsetting by pos1:
        
        // Actually, calculate newLeft similar to mouse elementDrag:
        // const newLeft = elmnt.offsetLeft - pos1;
        // Let's keep it uniform:
        const nLeft = elmnt.offsetLeft - pos1;
        
        const parent = elmnt.parentElement;
        const parentWidth = parent ? parent.clientWidth : window.innerWidth;
        const parentHeight = parent ? parent.clientHeight : window.innerHeight;
        
        const topBound = Math.max(0, Math.min(newTop, parentHeight - elmnt.clientHeight));
        const leftBound = Math.max(0, Math.min(nLeft, parentWidth - elmnt.clientWidth));

        elmnt.style.top = topBound + "px";
        elmnt.style.left = leftBound + "px";
        elmnt.style.right = "auto";
        elmnt.style.bottom = "auto";
    }
    
    function closeDragTouch() {
        document.removeEventListener('touchend', closeDragTouch);
        document.removeEventListener('touchmove', elementTouchDrag);
        elmnt.style.cursor = 'grab';
    }
}

// ─── Init ─────────────────────────────────────────────────────────────────────
window.addEventListener("load", async () => {
    console.log("[Init] Page loading for role:", window.userRole);
    initTheme();
    // Populate User Profile Header
    const nameStr = localStorage.getItem("displayName") || "User";
    let rawRoleStr = window.userRole || localStorage.getItem("role") || "CANDIDATE";
    if (rawRoleStr === "host") rawRoleStr = "interviewer";
    const roleStr = rawRoleStr;
    const headerRole = document.getElementById("header-user-role");
    const headerName = document.getElementById("header-user-name");
    const userAvatar = document.getElementById("user-avatar");
    if (headerRole) headerRole.textContent = roleStr;
    if (headerName) headerName.textContent = nameStr;
    
    console.log("[Init] User role set to:", roleStr, "Display name:", nameStr);
    
    // Candidate-specific initializations
    if (userRole === "candidate") {
        console.log("[Init] Candidate-specific initialization");
        
        // Ensure proctoring settings are initialized
        if (!window.proctoringSettings) {
            window.proctoringSettings = {
                gazeSensitivity: "medium",
                allowedTabSwitches: 3,
                gazeCheck: true,
                audioCheck: true,
                vmCheck: true,
                dualMonitorCheck: true,
                devToolsCheck: true,
                clipboardCheck: true
            };
            console.log("[Init] Initialized default proctoring settings for candidate");
        }
        
        // For candidates, hide the PiP container since they see their video in main area
        const pipContainer = document.getElementById("pip-container");
        if (pipContainer) {
            pipContainer.style.display = "none"; 
            console.log("[Init] Hidden PiP container for candidate (using main video area)");
        }
    }
    
    // Add CSS class for role-based UI separation
    const isCandidateDashboard = window.location.pathname.includes("/candidate_dashboard");
    if (isCandidateDashboard || userRole === "candidate" || roleStr.toUpperCase() === "CANDIDATE") {
        if (!window.location.pathname.includes("/host_dashboard")) {
            document.body.classList.add("role-candidate");
            
            // Change End Interview to Leave Meeting for candidate
            const endBtn = document.querySelector(".end-interview-btn");
            if (endBtn) {
                endBtn.innerHTML = '<i class="ph-fill ph-phone-disconnect"></i> Leave Meeting';
            }
            
            // For candidates, ensure main video shows their local stream
            console.log("[Init] Candidate view - ensuring local video in main area");
        }
    }

    if (userAvatar) userAvatar.src = `https://ui-avatars.com/api/?name=${encodeURIComponent(nameStr)}&background=6C4EB1&color=fff`;

    // Start Session Timer
    const timerDisplay = document.getElementById("timer-display");
    if (timerDisplay) {
        setInterval(() => {
            if (typeof sessionStartTime === "undefined" || !sessionStartTime) {
                timerDisplay.textContent = "WAITING";
                return;
            }
            const elapsed = Math.floor((Date.now() - sessionStartTime) / 1000);
            const m = Math.floor(elapsed / 60).toString().padStart(2, "0");
            const s = (elapsed % 60).toString().padStart(2, "0");
            timerDisplay.textContent = `${m}:${s}`;
        }, 1000);
    }

    loadMeetings();
    loadChatHistory();
    checkExtensions();
    await checkBackendAvailability();
    await startWebcam();
    setupSocket();
    await joinSession();

    // Check if TensorFlow.js is loaded
    if (typeof tf !== "undefined") {
        console.log(`[Intervue] TensorFlow.js loaded successfully. Version: ${tf.version.tfjs}`);
    } else {
        console.warn("[Intervue] TensorFlow.js library not detected.");
    }

    // Initialize auto-hiding toolbar logic
    initToolbarAutoHide();

    // Initialize draggable local picture-in-picture video
    const pipContainer = document.getElementById("pip-container");
    if (pipContainer) {
        makeElementDraggable(pipContainer);
    }

    // Initialize host-specific settings
    if (userRole === "host" || userRole === "interviewer") {
        console.log("[Init] Initializing host-specific settings");
        initHostSettings();
        initModeratorControls();
        initSaveSettings();
    }

    // re-check extensions after a short delay
    setTimeout(checkExtensions, 2000);
});

window.addEventListener("beforeunload", () => {
    if (!userRole || !navigator.sendBeacon) return;

    const payload = new Blob(
        [
            JSON.stringify({
                role: userRole,
                displayName: localStorage.getItem("displayName") || "",
            }),
        ],
        { type: "application/json" },
    );
    navigator.sendBeacon(apiUrl("/leave"), payload);
});

const btnAnalyzeChat = document.getElementById("btn-analyze-chat");
if (btnAnalyzeChat) {
    btnAnalyzeChat.addEventListener("click", async () => {
        const messages = Array.from(document.querySelectorAll(".chat-bubble")).map(m => m.textContent).join("\n");
        if (!messages) return;
        
        btnAnalyzeChat.disabled = true;
        try {
            const res = await fetch(apiUrl("/ai_insights"), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ description: messages })
            });
            const data = await res.json();
            
            appendMessage("AI Assistant", data.summary || "No insights could be generated.", "interviewer");
        } catch (e) {
            console.error(e);
        } finally {
            btnAnalyzeChat.disabled = false;
        }
    });
}

// ═══════════════════════════════════════════════════════
//  UI HELPERS & BUTTON ACTIONS
// ═══════════════════════════════════════════════════════

function initHostSettings() {
    // Wait for DOM to be fully loaded
    setTimeout(() => {
        const btnSettings = document.getElementById("btn-settings");
        const settingsModal = document.getElementById("settings-modal");
        
        console.log("[Settings] Initializing host settings button:", btnSettings);
        console.log("[Settings] Settings modal found:", settingsModal);
        
        if (btnSettings && settingsModal) {
            btnSettings.addEventListener("click", () => {
                console.log("[Settings] Settings button clicked");
                
                // Populate fields from current settings
                const gazeSensitivityEl = document.getElementById("settings-gaze-sensitivity");
                const tabSwitchesEl = document.getElementById("settings-tab-switches");
                const gazeCheckEl = document.getElementById("settings-check-gaze");
                const audioCheckEl = document.getElementById("settings-check-audio");
                const vmCheckEl = document.getElementById("settings-check-vm");
                const monitorCheckEl = document.getElementById("settings-check-monitor");
                const devToolsCheckEl = document.getElementById("settings-check-devtools");
                const clipboardCheckEl = document.getElementById("settings-check-clipboard");
                
                console.log("[Settings] Setting elements found:", {
                    gazeSensitivity: !!gazeSensitivityEl,
                    tabSwitches: !!tabSwitchesEl,
                    gazeCheck: !!gazeCheckEl,
                    audioCheck: !!audioCheckEl,
                    vmCheck: !!vmCheckEl,
                    monitorCheck: !!monitorCheckEl,
                    devToolsCheck: !!devToolsCheckEl,
                    clipboardCheck: !!clipboardCheckEl
                });
                
                if (gazeSensitivityEl) gazeSensitivityEl.value = window.proctoringSettings.gazeSensitivity || "medium";
                if (tabSwitchesEl) tabSwitchesEl.value = window.proctoringSettings.allowedTabSwitches || 3;
                if (gazeCheckEl) gazeCheckEl.checked = window.proctoringSettings.gazeCheck !== false;
                if (audioCheckEl) audioCheckEl.checked = window.proctoringSettings.audioCheck !== false;
                if (vmCheckEl) vmCheckEl.checked = window.proctoringSettings.vmCheck !== false;
                if (monitorCheckEl) monitorCheckEl.checked = window.proctoringSettings.dualMonitorCheck !== false;
                if (devToolsCheckEl) devToolsCheckEl.checked = window.proctoringSettings.devToolsCheck !== false;
                if (clipboardCheckEl) clipboardCheckEl.checked = window.proctoringSettings.clipboardCheck !== false;

                // Update moderator remote buttons based on candidate state
                updateModeratorButtonsUI();

                settingsModal.classList.add("open");
                console.log("[Settings] Settings modal opened");
            });
        } else {
            console.warn("[Settings] Could not initialize settings - button or modal not found");
        }
    }, 100); // Small delay to ensure DOM is ready
}

function updateModeratorButtonsUI() {
    console.log("[Settings] Updating moderator buttons UI - Candidate muted:", window.candidateMicMuted, "Candidate video stopped:", window.candidateVideoStopped);
    const btnMuteCand = document.getElementById("settings-btn-mute-candidate");
    const iconMuteCand = document.getElementById("settings-icon-mute-candidate");
    const textMuteCand = document.getElementById("settings-text-mute-candidate");

    const btnCamCand = document.getElementById("settings-btn-cam-candidate");
    const iconCamCand = document.getElementById("settings-icon-cam-candidate");
    const textCamCand = document.getElementById("settings-text-cam-candidate");

    if (btnMuteCand && iconMuteCand && textMuteCand) {
        if (window.candidateMicMuted) {
            textMuteCand.textContent = "Unmute Candidate";
            iconMuteCand.className = "ph-fill ph-microphone-slash";
            btnMuteCand.style.background = "linear-gradient(135deg, #ef4444, #b91c1c)";
        } else {
            textMuteCand.textContent = "Mute Candidate";
            iconMuteCand.className = "ph-fill ph-microphone";
            btnMuteCand.style.background = "";
        }
    } else {
        console.warn("[Settings] Mute button elements not found");
    }

    if (btnCamCand && iconCamCand && textCamCand) {
        if (window.candidateVideoStopped) {
            textCamCand.textContent = "Enable Candidate Cam";
            iconCamCand.className = "ph-fill ph-video-camera-slash";
            btnCamCand.style.background = "linear-gradient(135deg, #ef4444, #b91c1c)";
        } else {
            textCamCand.textContent = "Kill Candidate Cam";
            iconCamCand.className = "ph-fill ph-video-camera";
            btnCamCand.style.background = "";
        }
    } else {
        console.warn("[Settings] Camera button elements not found");
    }
}

function initModeratorControls() {
    // Wait for DOM to be fully loaded
    setTimeout(() => {
        const btnMuteCand = document.getElementById("settings-btn-mute-candidate");
        if (btnMuteCand) {
            btnMuteCand.addEventListener("click", () => {
                console.log("[Settings] Mute button clicked");
                window.candidateMicMuted = !window.candidateMicMuted;
                updateModeratorButtonsUI();
                if (socket && socket.connected) {
                    socket.emit("remote_control", {
                        meetingId: MEETING_ID,
                        action: "mute",
                        value: window.candidateMicMuted
                    });
                }
                UI_UPDATER.addAuditAlert(
                    window.candidateMicMuted ? "Remote Mute Sent" : "Remote Unmute Sent",
                    `Host sent command to ${window.candidateMicMuted ? 'mute' : 'unmute'} the candidate microphone.`,
                    "Moderator Control"
                );
            });
        } else {
            console.warn("[Settings] Mute button not found");
        }

        const btnCamCand = document.getElementById("settings-btn-cam-candidate");
        if (btnCamCand) {
            btnCamCand.addEventListener("click", () => {
                console.log("[Settings] Camera button clicked");
                window.candidateVideoStopped = !window.candidateVideoStopped;
                updateModeratorButtonsUI();
                if (socket && socket.connected) {
                    socket.emit("remote_control", {
                        meetingId: MEETING_ID,
                        action: "camera",
                        value: window.candidateVideoStopped
                    });
                }
                UI_UPDATER.addAuditAlert(
                    window.candidateVideoStopped ? "Remote Camera Kill Sent" : "Remote Camera Enable Sent",
                    `Host sent command to ${window.candidateVideoStopped ? 'disable' : 'enable'} the candidate camera feed.`,
                    "Moderator Control"
                );
            });
        } else {
            console.warn("[Settings] Camera button not found");
        }
    }, 100); // Small delay to ensure DOM is ready
}

const btnCamCand = document.getElementById("settings-btn-cam-candidate");
if (btnCamCand) {
    btnCamCand.addEventListener("click", () => {
        window.candidateVideoStopped = !window.candidateVideoStopped;
        updateModeratorButtonsUI();
        if (socket && socket.connected) {
            socket.emit("remote_control", {
                meetingId: MEETING_ID,
                action: "camera",
                value: window.candidateVideoStopped
            });
        }
        UI_UPDATER.addAuditAlert(
            window.candidateVideoStopped ? "Remote Camera Kill Sent" : "Remote Camera Enable Sent",
            `Host sent command to ${window.candidateVideoStopped ? 'disable' : 'enable'} the candidate camera feed.`,
            "Moderator Control"
        );
    });
}

function initSaveSettings() {
    // Wait for DOM to be fully loaded
    setTimeout(() => {
        const btnSaveSettings = document.getElementById("settings-btn-save");
        if (btnSaveSettings) {
            btnSaveSettings.addEventListener("click", () => {
                console.log("[Settings] Save settings button clicked");
                
                const gazeSensitivityEl = document.getElementById("settings-gaze-sensitivity");
                const tabSwitchesEl = document.getElementById("settings-tab-switches");
                const gazeCheckEl = document.getElementById("settings-check-gaze");
                const audioCheckEl = document.getElementById("settings-check-audio");
                const vmCheckEl = document.getElementById("settings-check-vm");
                const monitorCheckEl = document.getElementById("settings-check-monitor");
                const devToolsCheckEl = document.getElementById("settings-check-devtools");
                const clipboardCheckEl = document.getElementById("settings-check-clipboard");
                
                const gazeSensitivity = gazeSensitivityEl ? gazeSensitivityEl.value : "medium";
                const allowedTabSwitches = tabSwitchesEl ? parseInt(tabSwitchesEl.value) || 3 : 3;
                const gazeCheck = gazeCheckEl ? gazeCheckEl.checked : true;
                const audioCheck = audioCheckEl ? audioCheckEl.checked : true;
                const vmCheck = vmCheckEl ? vmCheckEl.checked : true;
                const dualMonitorCheck = monitorCheckEl ? monitorCheckEl.checked : true;
                const devToolsCheck = devToolsCheckEl ? devToolsCheckEl.checked : true;
                const clipboardCheck = clipboardCheckEl ? clipboardCheckEl.checked : true;

                window.proctoringSettings = {
                    gazeSensitivity,
                    allowedTabSwitches,
                    gazeCheck,
                    audioCheck,
                    vmCheck,
                    dualMonitorCheck,
                    devToolsCheck,
                    clipboardCheck
                };

                console.log("[Settings] Updated proctoring settings:", window.proctoringSettings);

                // Emit over socket
                if (socket && socket.connected) {
                    socket.emit("settings_update", {
                        meetingId: MEETING_ID,
                        settings: window.proctoringSettings
                    });
                    console.log("[Settings] Settings update emitted via socket");
                }

                UI_UPDATER.addAuditAlert(
                    "Settings Updated",
                    "Host updated proctoring configuration settings.",
                    "Moderator Control"
                );

                const settingsModal = document.getElementById("settings-modal");
                if (settingsModal) {
                    settingsModal.classList.remove("open");
                    console.log("[Settings] Settings modal closed");
                }
            });
        } else {
            console.warn("[Settings] Save button not found");
        }
    }, 100); // Small delay to ensure DOM is ready
}

async function copyMeetingInvite() {
    const meetingId = typeof MEETING_ID !== 'undefined' ? MEETING_ID : "ROD-8821-X-V6";
    const hostName = "Interviewer";
    const timeString = new Date().toLocaleString(undefined, { 
        weekday: 'long', year: 'numeric', month: 'long', 
        day: 'numeric', hour: '2-digit', minute:'2-digit' 
    });

    // The candidate link MUST be the HMAC-signed /meet/<id>?sig=...&expires=...
    // URL — a plain /login/candidate link 403s because it lacks the verified
    // session cookie set by the signed-link route.
    let joinUrl = "";
    try {
        const res = await fetch(apiUrl(`/api/room/${encodeURIComponent(meetingId)}/invite`));
        if (res.ok) {
            const data = await res.json();
            if (data.link) joinUrl = `${window.location.origin}${data.link}`;
        }
    } catch (e) {
        console.warn("Failed to fetch signed invite link:", e);
    }
    if (!joinUrl) {
        // Never copy an unsigned link — it would 403 for the candidate. Surface
        // the failure instead of handing out a broken invitation.
        if (typeof showBanner !== "undefined") {
            showBanner(
                "Invite unavailable",
                "Could not generate the signed invitation link. Check that the backend is running and try again.",
                "System",
                "warning",
            );
        } else {
            alert("Could not generate the invitation link. Check that the backend is running.");
        }
        return;
    }
    
    const inviteText = `${hostName} is inviting you to a scheduled Intervue meeting.\n\nTopic: Technical Interview\nTime: ${timeString}\n\nJoin Intervue Meeting\n${joinUrl}\n\nMeeting ID: ${meetingId}`;
    
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(inviteText).then(() => {
            if (typeof showBanner !== "undefined") {
                showBanner("Invitation Copied", "The meeting invitation has been copied to your clipboard.", "System", "info");
            } else {
                alert("Invitation Copied:\n\n" + inviteText);
            }
        }).catch(err => {
            console.error("Failed to copy invite:", err);
            alert("Failed to copy invitation. Please manually copy the URL.");
        });
    } else {
        // Fallback for older browsers
        const textArea = document.createElement("textarea");
        textArea.value = inviteText;
        document.body.appendChild(textArea);
        textArea.select();
        try {
            document.execCommand('copy');
            if (typeof showBanner !== "undefined") {
                showBanner("Invitation Copied", "The meeting invitation has been copied to your clipboard.", "System", "info");
            } else {
                alert("Invitation copied!");
            }
        } catch (err) {
            console.error('Fallback: Oops, unable to copy', err);
        }
        document.body.removeChild(textArea);
    }
}

// ─── Auto-Hiding Toolbar logic ───────────────────────────────────────────────
function initToolbarAutoHide() {
    const toolbar = document.querySelector(".video-toolbar-overlay");
    const container = document.querySelector(".main-video-container");
    if (!toolbar || !container) return;

    let timeoutId = null;
    let isMouseOverToolbar = false;

    // Expand toolbar when cursor is over it
    toolbar.addEventListener("mouseenter", () => {
        isMouseOverToolbar = true;
        expandToolbar();
    });

    // Reset collapse timer when cursor leaves
    toolbar.addEventListener("mouseleave", () => {
        isMouseOverToolbar = false;
        resetTimer();
    });

    // Expand toolbar when clicked
    toolbar.addEventListener("click", () => {
        expandToolbar();
        resetTimer();
    });

    // Expand on mouse movement inside the video container
    container.addEventListener("mousemove", () => {
        expandToolbar();
        resetTimer();
    });

    // Initial timer start
    resetTimer();

    function expandToolbar() {
        toolbar.classList.remove("collapsed");
    }

    function collapseToolbar() {
        // Guard: Do not collapse if mouse is over the toolbar, or if chat/notes/settings/logout panels are open
        const isChatOpen = document.getElementById("chat-panel")?.classList.contains("open");
        const isNotesOpen = document.getElementById("notes-modal")?.classList.contains("open");
        const isSettingsOpen = document.getElementById("settings-modal")?.classList.contains("open");
        const isLogoutOpen = document.getElementById("logout-modal")?.style.display === "flex";

        if (isMouseOverToolbar || isChatOpen || isNotesOpen || isSettingsOpen || isLogoutOpen) {
            return;
        }

        toolbar.classList.add("collapsed");
    }

    function resetTimer() {
        if (timeoutId) clearTimeout(timeoutId);
        timeoutId = setTimeout(collapseToolbar, 3000); // 3 seconds of inactivity
    }
}
