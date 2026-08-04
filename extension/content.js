// content.js - Injected script to enforce proctoring restrictions

console.log("[InterviewOS Guard] Injecting restrictions...");

// 1. Disable Right-Click Context Menu
document.addEventListener('contextmenu', function (e) {
    e.preventDefault();
    showWarning("Right-click context menu is disabled during the interview.");
});

// 2. Disable Copy, Cut, and Paste
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

// 3. Disable Developer Tools and Inspection Shortcuts
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
});

// 4. Focus/Blur Monitoring (Tab Switches / Application switches)
window.addEventListener('blur', function () {
    console.warn("[InterviewOS Guard] Focus lost! User navigated away from the window.");
    reportEvent("Focus Lost", "User switched tab, window, or opened another application.");
});

window.addEventListener('focus', function () {
    console.log("[InterviewOS Guard] Focus regained.");
});

// 5. Helper function to show floating warnings on-screen
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
    
    // Auto-fade after 3 seconds
    if (window.warningTimeout) {
        clearTimeout(window.warningTimeout);
    }
    window.warningTimeout = setTimeout(function () {
        warningDiv.style.opacity = '0';
    }, 3000);
}

// 6. Connect to SocketIO or window custom events to report telemetry back to server
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
