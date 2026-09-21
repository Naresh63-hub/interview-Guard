/**
 * Advanced Security and Anti-Bypass System
 * Protects against tampering, debugging, and security bypasses
 */

class SecurityGuard {
    constructor() {
        // Baseline is computed lazily on the first integrity tick (see
        // monitorIntegrity): this script loads BEFORE webrtc.js/proctoring.js/
        // gaze.js, so at construction time none of the critical functions
        // exist yet. Hashing then would produce a baseline of "nothing" that
        // differs from the loaded code 10s later — a guaranteed false
        // "Code integrity hash changed" lockout on every page load.
        // The script list has the same problem: capturing it here would miss
        // every script that loads after this file, and the first tick would
        // flag all of them as "injected". Both baselines are captured on the
        // first tick instead, when the page is fully loaded.
        this.integrityHash = null;
        this.originalScripts = null;
        this.debuggerDetectionEnabled = true;
        this.tamperDetected = false;
        this.securityViolations = [];
        
        this.init();
    }
    
    init() {
        this.detectDebugger();
        this.monitorIntegrity();
        this.detectConsoleOpening();
        this.monitorNetwork();
        this.preventScriptInjection();
        this.detectProxyVPNs();
        this.enforceSandbox();
    }
    
    // ============================================================
    // ANTI-DEBUGGING MEASURES
    // ============================================================
    
    detectDebugger() {
        if (!this.debuggerDetectionEnabled) return;
        
        // Method 1: Debugger statement timing
        const detectDebuggerTiming = () => {
            const start = performance.now();
            debugger; // This line will only trigger if devtools is open
            const end = performance.now();
            
            if (end - start > 100) {
                this.logViolation('debugger_timing', 'Debugger detected via timing attack');
            }
        };
        
        // Method 2: Function decompilation detection
        const detectDecompilation = () => {
            const testFunc = function() { return true; };
            const decompiled = testFunc.toString();
            
            if (decompiled.includes('debugger') || decompiled.includes('debugger;')) {
                this.logViolation('decompilation', 'Function decompilation detected');
            }
        };
        
        // Method 3: Object property tampering
        const detectPropertyTampering = () => {
            const originalConsole = console.log;
            const test = { get value() { debugger; } };
            
            try {
                console.log(test);
                if (console.log !== originalConsole) {
                    this.logViolation('console_tamper', 'Console object tampered');
                }
            } catch (e) {
                this.logViolation('property_tamper', 'Object property tampering detected');
            }
        };
        
        // Run checks periodically
        setInterval(() => {
            detectDebuggerTiming();
            detectDecompilation();
            detectPropertyTampering();
        }, 5000);
    }
    
    // ============================================================
    // INTEGRITY MONITORING
    // ============================================================
    
    calculateIntegrityHash() {
        // Create hash of critical functions and variables.
        // NOTE: only entries that are actually defined on `window` contribute.
        // Scripts load AFTER this file, so on the first tick every function
        // listed here exists and the hash is a meaningful baseline.
        const criticalElements = [
            'toggleVideo',
            'toggleMute',
            'startMLProcessing',
            'checkExtensions'
        ];
        
        let hash = '';
        criticalElements.forEach(element => {
            const func = window[element];
            if (typeof func === 'function') {
                hash += func.toString().slice(0, 100);
            }
        });
        
        return this.simpleHash(hash);
    }
    
    captureOriginalScripts() {
        const scripts = {};
        document.querySelectorAll('script[src]').forEach(script => {
            scripts[script.src] = script.textContent || '';
        });
        return scripts;
    }
    
    monitorIntegrity() {
        setInterval(() => {
            const currentHash = this.calculateIntegrityHash();
            
            if (this.integrityHash === null) {
                // First tick: every page script has finished loading by now
                // (the monitor runs 10s after load), so this is the true
                // baseline. Delaying it here — instead of in the constructor —
                // prevents the false-positive lockout described above.
                this.integrityHash = currentHash;
                this.originalScripts = this.captureOriginalScripts();
                return;
            }
            
            if (currentHash !== this.integrityHash) {
                this.logViolation('integrity_breach', 'Code integrity hash changed - possible tampering');
                this.integrityHash = currentHash; // Update to prevent spam
            }
            
            // Check for new script injections
            const currentScripts = document.querySelectorAll('script[src]');
            const newScripts = Array.from(currentScripts).filter(
                script => !this.originalScripts[script.src] && 
                          !script.src.includes('phosphor-icons') &&
                          !script.src.includes('chrome-extension') &&
                          !script.src.includes('socket.io') &&
                          !script.src.includes('/static/')
            );
            
            if (newScripts.length > 0) {
                this.logViolation('script_injection', 'New script detected: ' + newScripts.map(s => s.src).join(', '));
            }
        }, 10000);
    }
    
    // ============================================================
    // CONSOLE OPENING DETECTION
    // ============================================================
    
    detectConsoleOpening() {
        const detectConsole = () => {
            const threshold = 160;
            const widthThreshold = window.outerWidth - window.innerWidth > threshold;
            const heightThreshold = window.outerHeight - window.innerHeight > threshold;
            
            if (widthThreshold || heightThreshold) {
                this.logViolation('console_open', 'Developer console detected');
                
                // Optionally terminate session
                if (window.proctoringSettings && window.proctoringSettings.devToolsCheck) {
                    this.lockPage('Security Violation', 'Developer tools were detected. This session has been terminated.');
                }
            }
        };
        
        setInterval(detectConsole, 1000);
    }
    
    // ============================================================
    // NETWORK MONITORING
    // ============================================================
    
    monitorNetwork() {
        // Monitor for unauthorized network requests
        // NOTE: the wrappers must close over `this` — a plain function
        // assigned to window.fetch loses the guard as `this` when app code
        // calls bare `fetch(...)`, which used to throw
        // "Cannot read properties of undefined (reading 'isSuspiciousURL')"
        // on EVERY request and silently killed the session heartbeat.
        const guard = this;
        const originalFetch = window.fetch;
        const originalXHR = window.XMLHttpRequest;

        window.fetch = function(...args) {
            try {
                const url = args[0];
                if (typeof url === 'string' && guard.isSuspiciousURL(url)) {
                    console.log('[Security] Suspicious fetch request:', url);
                }
            } catch (e) {
                // Monitoring must never break the request itself.
            }
            return originalFetch.apply(this, args);
        };
        
        window.XMLHttpRequest = function() {
            const xhr = new originalXHR();
            const originalOpen = xhr.open;

            xhr.open = function(method, url) {
                try {
                    if (guard.isSuspiciousURL(url)) {
                        console.log('[Security] Suspicious XHR request:', url);
                    }
                } catch (e) {
                    // Monitoring must never break the request itself.
                }
                return originalOpen.apply(this, arguments);
            };

            return xhr;
        };
    }
    
    isSuspiciousURL(url) {
        const suspiciousPatterns = [
            /api\.openai\.com/,
            /chatgpt\.com/,
            /claude\.ai/,
            /cheat/,
            /hack/,
            /exam-questions/,
            /interview-answers/
        ];
        
        return suspiciousPatterns.some(pattern => pattern.test(url));
    }
    
    // ============================================================
    // SCRIPT INJECTION PREVENTION
    // ============================================================
    
    preventScriptInjection() {
        // Monitor DOM for script injection
        const observer = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                mutation.addedNodes.forEach((node) => {
                    if (node.nodeName === 'SCRIPT') {
                        // Check if script is from suspicious source
                        if (node.src && this.isSuspiciousURL(node.src)) {
                            node.remove();
                            this.logViolation('script_blocked', 'Blocked suspicious script: ' + node.src);
                        }
                    }
                });
            });
        });
        
        observer.observe(document, {
            childList: true,
            subtree: true
        });
    }
    
    // ============================================================
    // PROXY/VPN DETECTION
    // ============================================================
    
    detectProxyVPNs() {
        // Check for proxy indicators
        const indicators = {
            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
            language: navigator.language,
            hardwareConcurrency: navigator.hardwareConcurrency,
            deviceMemory: navigator.deviceMemory
        };
        
        // Check for timezone anomalies
        const systemTime = new Date().getTimezoneOffset();
        const localTime = new Date().getTimezoneOffset();
        
        if (Math.abs(systemTime - localTime) > 60) {
            this.logViolation('timezone_anomaly', 'Timezone mismatch detected - possible VPN');
        }
        
        // Check for low hardware specs (common in VMs)
        if (indicators.hardwareConcurrency < 2) {
            this.logViolation('low_hardware', 'Low hardware specs detected - possible VM');
        }
    }
    
    // ============================================================
    // SANDBOX ENFORCEMENT
    // ============================================================
    
    enforceSandbox() {
        // Prevent iframe breakout
        if (window.top !== window.self) {
            window.top.location = window.self.location;
        }
        
        // Prevent window navigation
        const originalOpen = window.open;
        window.open = function() {
            console.log('[Security] Blocked window.open call');
            return null;
        };
        
        // Prevent location changes
        // NOTE: `location` is [Unforgeable] per the HTML spec — it has no
        // configurable accessor on window in modern browsers, so this
        // defineProperty ALWAYS throws TypeError. That used to abort the
        // guard's constructor midway, leaving window.securityGuard unset
        // while the earlier setInterval detectors kept running unstoppably.
        // Sandbox hardening is best-effort: degrade, never crash.
        try {
            Object.defineProperty(window, 'location', {
                get: function() {
                    return window.location;
                },
                set: function(value) {
                    console.log('[Security] Blocked location change attempt');
                }
            });
        } catch (e) {
            console.log('[Security] location lockdown unavailable in this browser:', e.message);
        }
    }
    
    // ============================================================
    // UTILITY FUNCTIONS
    // ============================================================
    
    simpleHash(str) {
        let hash = 0;
        for (let i = 0; i < str.length; i++) {
            const char = str.charCodeAt(i);
            hash = ((hash << 5) - hash) + char;
            hash = hash & hash;
        }
        return hash.toString(36);
    }
    
    logViolation(type, message) {
        const violation = {
            type,
            message,
            timestamp: new Date().toISOString(),
            severity: this.getSeverity(type)
        };
        
        this.securityViolations.push(violation);
        
        console.log('[Security Violation]', violation);
        
        // Emit to host if socket is available
        if (typeof socket !== 'undefined' && socket && socket.connected) {
            socket.emit('security_violation', violation);
        }
        
        // Check if should lock page
        if (violation.severity === 'critical') {
            this.lockPage('Security Violation', message);
        }
    }
    
    getSeverity(type) {
        const criticalTypes = ['integrity_breach', 'console_open'];
        const highTypes = ['debugger_timing', 'decompilation', 'console_tamper', 'script_injection'];
        
        if (criticalTypes.includes(type)) return 'critical';
        if (highTypes.includes(type)) return 'high';
        return 'medium';
    }
    
    lockPage(title, message) {
        // The proctor dashboard must never destroy itself: a false-positive
        // console heuristic on the HOST side used to brick the interview
        // screen with "Please contact your interviewer". Locks are for the
        // candidate experience only; host-side violations are logged and
        // surfaced as audit alerts instead.
        if (window.userRole === 'interviewer' || window.userRole === 'host') {
            console.warn('[Security] Lock suppressed for host role:', title, message);
            if (typeof UI_UPDATER !== 'undefined' && UI_UPDATER.addAuditAlert) {
                UI_UPDATER.addAuditAlert('Security Warning', message, 'Local', false);
            }
            return;
        }
        document.body.innerHTML = `
            <div style="
                display: flex;
                align-items: center;
                justify-content: center;
                height: 100vh;
                background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
                color: white;
                font-family: sans-serif;
                text-align: center;
            ">
                <div>
                    <h1 style="font-size: 3rem; margin-bottom: 20px;">🔒 ${title}</h1>
                    <p style="font-size: 1.2rem; opacity: 0.8;">${message}</p>
                    <p style="margin-top: 20px; opacity: 0.6;">Please contact your interviewer.</p>
                </div>
            </div>
        `;
        
        // Disable all interactions
        document.addEventListener('click', e => e.preventDefault(), true);
        document.addEventListener('keydown', e => e.preventDefault(), true);
    }
    
    getSecurityReport() {
        return {
            violations: this.securityViolations,
            integrityVerified: this.integrityHash === null || this.calculateIntegrityHash() === this.integrityHash,
            debuggerDetectionEnabled: this.debuggerDetectionEnabled,
            tamperDetected: this.tamperDetected
        };
    }
}

// Initialize security guard
if (typeof window !== 'undefined') {
    window.securityGuard = new SecurityGuard();
    console.log('[Security] Security Guard initialized');
}
