// content.js - Injected script to enforce proctoring restrictions

console.log("[InterviewOS Guard] Injecting restrictions...");

// Kiosk mode state
let kioskModeActive = false;
let fullScreenElement = null;

// 1. Enhanced Kiosk Mode - Full screen lock when meeting starts
function enterKioskMode() {
    if (kioskModeActive) return;
    
    console.log("[InterviewOS Guard] Entering Kiosk Mode");
    kioskModeActive = true;
    
    // Request full screen
    const elem = document.documentElement;
    if (elem.requestFullscreen) {
        elem.requestFullscreen().catch(err => {
            console.warn("[InterviewOS Guard] Fullscreen request failed:", err);
            showWarning("Please allow fullscreen for secure proctoring");
        });
    } else if (elem.webkitRequestFullscreen) {
        elem.webkitRequestFullscreen();
    } else if (elem.msRequestFullscreen) {
        elem.msRequestFullscreen();
    }
    
    // Create lock overlay
    createLockOverlay();
    
    // Disable navigation
    disableNavigation();
    
    // Report kiosk mode activation
    reportEvent("Kiosk Mode Activated", "Full-screen lock enabled for secure proctoring");
}

function exitKioskMode() {
    if (!kioskModeActive) return;
    
    console.log("[InterviewOS Guard] Exiting Kiosk Mode");
    kioskModeActive = false;
    
    // Exit full screen
    if (document.exitFullscreen) {
        document.exitFullscreen().catch(err => {
            console.warn("[InterviewOS Guard] Exit fullscreen failed:", err);
        });
    } else if (document.webkitExitFullscreen) {
        document.webkitExitFullscreen();
    } else if (document.msExitFullscreen) {
        document.msExitFullscreen();
    }
    
    // Remove lock overlay
    removeLockOverlay();
    
    // Re-enable navigation
    enableNavigation();
    
    reportEvent("Kiosk Mode Deactivated", "Full-screen lock disabled");
}

function createLockOverlay() {
    // Create full-screen overlay to prevent clicks outside the app
    let overlay = document.getElementById('kiosk-lock-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'kiosk-lock-overlay';
        overlay.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            z-index: 999998;
            pointer-events: none;
            display: none;
        `;
        document.body.appendChild(overlay);
    }
    overlay.style.display = 'block';
}

function removeLockOverlay() {
    const overlay = document.getElementById('kiosk-lock-overlay');
    if (overlay) {
        overlay.style.display = 'none';
    }
}

function disableNavigation() {
    // Prevent URL changes
    const originalPushState = history.pushState;
    const originalReplaceState = history.replaceState;
    
    history.pushState = function() {
        if (kioskModeActive) {
            showWarning("Navigation is disabled during secure proctoring");
            return;
        }
        return originalPushState.apply(this, arguments);
    };
    
    history.replaceState = function() {
        if (kioskModeActive) {
            showWarning("Navigation is disabled during secure proctoring");
            return;
        }
        return originalReplaceState.apply(this, arguments);
    };
    
    // Prevent back button
    window.addEventListener('popstate', function(e) {
        if (kioskModeActive) {
            e.preventDefault();
            e.stopImmediatePropagation();
            history.pushState(null, '', window.location.href);
            showWarning("Back navigation is disabled during secure proctoring");
        }
    });
}

function enableNavigation() {
    // Navigation restrictions are lifted when kiosk mode exits
    // The browser will restore normal navigation behavior
}

// Monitor for fullscreen changes to enforce kiosk mode
document.addEventListener('fullscreenchange', handleFullscreenChange);
document.addEventListener('webkitfullscreenchange', handleFullscreenChange);
document.addEventListener('mozfullscreenchange', handleFullscreenChange);
document.addEventListener('MSFullscreenChange', handleFullscreenChange);

function handleFullscreenChange() {
    const isFullscreen = document.fullscreenElement || 
                         document.webkitFullscreenElement || 
                         document.mozFullScreenElement || 
                         document.msFullscreenElement;
    
    if (kioskModeActive && !isFullscreen) {
        console.warn("[InterviewOS Guard] User exited fullscreen during kiosk mode!");
        showWarning("⚠️ SECURITY ALERT: Fullscreen was exited during secure proctoring");
        reportEvent("Fullscreen Exited", "User exited fullscreen during kiosk mode - security violation");
        
        // Attempt to re-enter fullscreen
        setTimeout(() => {
            if (kioskModeActive) {
                enterKioskMode();
            }
        }, 100);
    }
}

// Listen for kiosk mode commands from the app
window.addEventListener('proctoring_kiosk_enable', enterKioskMode);
window.addEventListener('proctoring_kiosk_disable', exitKioskMode);

// 2. Disable Right-Click Context Menu
document.addEventListener('contextmenu', function (e) {
    e.preventDefault();
    showWarning("Right-click context menu is disabled during the interview.");
});

// 3. Disable Copy, Cut, and Paste
document.addEventListener('copy', function (e) {
    e.preventDefault();
    showWarning("Copying text is prohibited during this session.");
});

document.addEventListener('cut', function (e) {
    e.preventDefault();
    showWarning("Cutting text is prohibited during this session.");
});

document.addEventListener('paste', function (e) {
    e.preventDefault();
    showWarning("Pasting text is prohibited during this session.");
});

// 4. Disable Developer Tools and Inspection Shortcuts
document.addEventListener('keydown', function (e) {
    // F12 key
    if (e.key === 'F12' || e.keyCode === 123) {
        e.preventDefault();
        showWarning("Developer Tools (F12) are disabled.");
        return false;
    }

    // Ctrl+Shift+I / Cmd+Opt+I (DevTools)
    // Ctrl+Shift+J / Cmd+Opt+J (Console)
    // Ctrl+Shift+C / Cmd+Opt+C (Inspector)
    // Ctrl+U / Cmd+Opt+U (View Source)
    const isCmdOrCtrl = e.ctrlKey || e.metaKey;
    const isShift = e.shiftKey;
    const isAlt = e.altKey;

    if (isCmdOrCtrl) {
        // View Source
        if (e.key === 'u' || e.key === 'U' || e.keyCode === 85) {
            e.preventDefault();
            showWarning("View Page Source is disabled.");
            return false;
        }

        if (isShift) {
            if (e.key === 'I' || e.key === 'i' || e.keyCode === 73 ||
                e.key === 'J' || e.key === 'j' || e.keyCode === 74 ||
                e.key === 'C' || e.key === 'c' || e.keyCode === 67) {
                e.preventDefault();
                showWarning("Developer Tools are disabled.");
                return false;
            }
        }
        
        // macOS alternative shortcuts using Alt/Option instead of Shift
        if (isAlt) {
            if (e.key === 'I' || e.key === 'i' || e.keyCode === 73 ||
                e.key === 'J' || e.key === 'j' || e.keyCode === 74 ||
                e.key === 'C' || e.key === 'c' || e.keyCode === 67) {
                e.preventDefault();
                showWarning("Developer Tools are disabled.");
                return false;
            }
        }
    }
    
    // Prevent Alt+Tab (doesn't work due to OS restrictions, but prevents Alt key usage)
    if (e.altKey && !isCmdOrCtrl) {
        if (e.key === 'Tab' || e.keyCode === 9) {
            if (kioskModeActive) {
                e.preventDefault();
                showWarning("Tab switching is disabled in kiosk mode");
            }
        }
    }
});

// 5. Enhanced Focus/Blur Monitoring (Tab Switches / Application switches)
let focusLostCount = 0;
let focusLostStartTime = null;

window.addEventListener('blur', function () {
    focusLostCount++;
    if (!focusLostStartTime) {
        focusLostStartTime = Date.now();
    }
    
    const duration = focusLostStartTime ? Date.now() - focusLostStartTime : 0;
    
    console.warn(`[InterviewOS Guard] Focus lost! Count: ${focusLostCount}, Duration: ${duration}ms`);
    
    if (kioskModeActive) {
        showWarning("⚠️ SECURITY ALERT: Window focus lost in kiosk mode!");
        reportEvent("Focus Lost (Kiosk)", `Window focus lost for ${duration}ms during kiosk mode`);
    } else {
        reportEvent("Focus Lost", `User switched tab, window, or opened another application (Duration: ${duration}ms)`);
    }
});

window.addEventListener('focus', function () {
    const duration = focusLostStartTime ? Date.now() - focusLostStartTime : 0;
    console.log("[InterviewOS Guard] Focus regained after " + duration + "ms");
    
    if (duration > 3000) { // More than 3 seconds
        showWarning(`⚠️ You were away for ${(duration/1000).toFixed(1)} seconds`);
        reportEvent("Extended Focus Loss", `User was away for ${(duration/1000).toFixed(1)} seconds`);
    }
    
    focusLostStartTime = null;
});

// 6. Helper function to show floating warnings on-screen
function showWarning(message) {
    console.warn("[InterviewOS Guard] Blocked action: " + message);
    
    // Check if warning container exists
    let warningDiv = document.getElementById('proctoring-warning');
    if (!warningDiv) {
        warningDiv = document.createElement('div');
        warningDiv.id = 'proctoring-warning';
        warningDiv.style.position = 'fixed';
        warningDiv.style.bottom = '20px';
        warningDiv.style.right = '20px';
        warningDiv.style.backgroundColor = '#ff3b30';
        warningDiv.style.color = '#ffffff';
        warningDiv.style.padding = '12px 20px';
        warningDiv.style.borderRadius = '8px';
        warningDiv.style.fontFamily = 'system-ui, -apple-system, sans-serif';
        warningDiv.style.fontSize = '14px';
        warningDiv.style.fontWeight = 'bold';
        warningDiv.style.zIndex = '999999';
        warningDiv.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
        warningDiv.style.transition = 'opacity 0.3s ease';
        document.body.appendChild(warningDiv);
    }
    
    warningDiv.innerText = message;
    warningDiv.style.opacity = '1';
    
    // Auto-fade after 5 seconds for kiosk mode warnings
    const fadeTime = kioskModeActive ? 5000 : 3000;
    
    if (window.warningTimeout) {
        clearTimeout(window.warningTimeout);
    }
    window.warningTimeout = setTimeout(function () {
        warningDiv.style.opacity = '0';
    }, fadeTime);
}

// 7. Connect to SocketIO or window custom events to report telemetry back to server
function reportEvent(title, message) {
    // Dispatch a custom event that our web app socket layers can listen to
    const event = new CustomEvent('proctoring_violation', {
        detail: {
            title: title,
            message: message,
            timestamp: Date.now()
        }
    });
    window.dispatchEvent(event);
}

// 8. Initialize kiosk mode if the page has the lock-mode flag
function checkKioskModeFlag() {
    const urlParams = new URLSearchParams(window.location.search);
    const lockMode = urlParams.get('lock_mode');
    
    if (lockMode === 'true') {
        console.log("[InterviewOS Guard] Lock mode requested via URL parameter");
        // Wait for page to load before entering kiosk mode
        if (document.readyState === 'complete') {
            enterKioskMode();
        } else {
            window.addEventListener('load', enterKioskMode);
        }
    }
}

// Check for kiosk mode on script load
checkKioskModeFlag();
