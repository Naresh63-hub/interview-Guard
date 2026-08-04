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
        `- Gaze alerts captured: ${suspiciousGazeEvents}`,
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

    if (!bothPresent) {
        sessionStartTime = null;
        renderWaitingSessionState();
        return;
    }

    sessionStartTime = startedAt * 1000;
    
    // Clear timeline and session storage once session officially starts
    if (!window._sessionStartedFlag) {
        window._sessionStartedFlag = true;
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
    const response = await fetch(apiUrl(endpoint), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            role: userRole,
            displayName: localStorage.getItem("displayName") || "",
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
    });
}

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
                        <h5 style="margin: 0 0 0.25rem; font-size: 1rem; color: var(--text-primary);">${log.title} ${log.confidence ? `<span class="confidence" style="margin-left: 0.5rem; font-size: 0.8rem; padding: 0.1rem 0.4rem; border-radius: 4px; background: rgba(255,255,255,0.1);">${log.confidence}</span>` : ''}</h5>
                        <p style="margin: 0; color: var(--text-secondary); font-size: 0.9rem;">${log.message}</p>
                    </div>
                    <span class="time" style="font-size: 0.8rem; color: var(--text-secondary); margin-top: 0.25rem; display: block;">${log.timeStr}</span>
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
    });
}

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
                        <h5 style="margin: 0 0 0.25rem; font-size: 1rem; color: var(--text-primary);">${log.title} ${log.confidence ? `<span class="confidence" style="margin-left: 0.5rem; font-size: 0.8rem; padding: 0.1rem 0.4rem; border-radius: 4px; background: rgba(255,255,255,0.1);">${log.confidence}</span>` : ''}</h5>
                        <p style="margin: 0; color: var(--text-secondary); font-size: 0.9rem;">${log.message}</p>
                    </div>
                    <span class="time" style="font-size: 0.8rem; color: var(--text-secondary); margin-top: 0.25rem; display: block;">${log.timeStr}</span>
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
        // Guard: Prevent any proctoring/cheating alerts from registering/sending until candidate's face is visible at least once
        const isConnectionEvent = title === "Participant Joined" || title === "Participant Left" || title === "Session Started";
        if (!window.proctoringActive && !isConnectionEvent) {
            console.log(`Proctoring not active yet. Ignoring alert: [${title}] ${message}`);
            return;
        }

        const isCandidate = typeof userRole !== "undefined" && userRole === "candidate";
        if (!fromRemote && isCandidate && typeof socket !== "undefined" && socket) {
            socket.emit("audit_event", {
                meetingId: MEETING_ID,
        }
        // Save to sessionStorage for Full Audit Log view
                  "YOLO Vision",
                  true
              );
              window.lastYoloAuditTime = now;
              if (typeof suspiciousGazeEvents !== 'undefined') {
                  suspiciousGazeEvents += 15;
              }
          }
      }


    // Recalculate scores only if session has started
    if (typeof sessionStartTime !== "undefined" && sessionStartTime) {
        const recentPenalty = recentSuspiciousEvents.length;
        const attentionScore = Math.max(
            0,
            Math.min(100, 100 - recentPenalty * 10),
        );
        const riskScore = Math.max(0, Math.min(100, recentPenalty * 10));

        UI_UPDATER.updateGazeAttention(attentionScore);
        UI_UPDATER.updateRiskScore(riskScore);
    }
}

// ─── Init ─────────────────────────────────────────────────────────────────────
window.addEventListener("load", async () => {
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

const btnSettings = document.getElementById("btn-settings");
const settingsModal = document.getElementById("settings-modal");
if (btnSettings && settingsModal) {
    btnSettings.addEventListener("click", () => {
        // Populate fields from current settings
        document.getElementById("settings-gaze-sensitivity").value = window.proctoringSettings.gazeSensitivity;
        document.getElementById("settings-tab-switches").value = window.proctoringSettings.allowedTabSwitches;
        document.getElementById("settings-check-gaze").checked = window.proctoringSettings.gazeCheck;
        document.getElementById("settings-check-audio").checked = window.proctoringSettings.audioCheck;
        document.getElementById("settings-check-vm").checked = window.proctoringSettings.vmCheck;
        document.getElementById("settings-check-monitor").checked = window.proctoringSettings.dualMonitorCheck;
        document.getElementById("settings-check-devtools").checked = window.proctoringSettings.devToolsCheck;
        document.getElementById("settings-check-clipboard").checked = window.proctoringSettings.clipboardCheck;

        // Update moderator remote buttons based on candidate state
        updateModeratorButtonsUI();

        settingsModal.classList.add("open");
    });
}

function updateModeratorButtonsUI() {
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
    }
}

const btnMuteCand = document.getElementById("settings-btn-mute-candidate");
if (btnMuteCand) {
    btnMuteCand.addEventListener("click", () => {
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

const btnSaveSettings = document.getElementById("settings-btn-save");
if (btnSaveSettings) {
    btnSaveSettings.addEventListener("click", () => {
        const gazeSensitivity = document.getElementById("settings-gaze-sensitivity").value;
        const allowedTabSwitches = parseInt(document.getElementById("settings-tab-switches").value) || 3;
        const gazeCheck = document.getElementById("settings-check-gaze").checked;
        const audioCheck = document.getElementById("settings-check-audio").checked;
        const vmCheck = document.getElementById("settings-check-vm").checked;
        const dualMonitorCheck = document.getElementById("settings-check-monitor").checked;
        const devToolsCheck = document.getElementById("settings-check-devtools").checked;
        const clipboardCheck = document.getElementById("settings-check-clipboard").checked;

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

        // Emit over socket
        if (socket && socket.connected) {
            socket.emit("settings_update", {
                meetingId: MEETING_ID,
                settings: window.proctoringSettings
            });
        }

        UI_UPDATER.addAuditAlert(
            "Settings Updated",
            "Host updated proctoring configuration settings.",
            "Moderator Control"
        );

        if (settingsModal) {
            settingsModal.classList.remove("open");
        }
    });
}

function copyMeetingInvite() {
    const meetingId = typeof MEETING_ID !== 'undefined' ? MEETING_ID : "ROD-8821-X-V6";
    const hostName = "Interviewer";
    const timeString = new Date().toLocaleString(undefined, { 
        weekday: 'long', year: 'numeric', month: 'long', 
        day: 'numeric', hour: '2-digit', minute:'2-digit' 
    });
    const joinUrl = `${window.location.origin}/login/candidate?room=${meetingId}`;
    
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
