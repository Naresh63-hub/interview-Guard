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
        
        // Method 1: Debugger statement timing (safe check)
        const detectDebuggerTiming = () => {
            // Disabled unconditional debugger breakpoint to prevent wedging during inspection
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
        // Window dimension heuristics (outerHeight - innerHeight) produce false positives
        // on Windows OS DPI scaling (125%/150%), browser toolbars/bookmarks, and side-by-side window snapping.
        // We log devtools events safely without terminating the session or destroying the DOM.
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
        
        // Surface the violation to the host and the audit trail. The previous
        // code emitted a dedicated `security_violation` socket event that had
        // NO server handler, so every debugger / console / VPN / VM / integrity
        // hit was silently dropped. Route through addAuditAlert instead: on the
        // candidate it relays a real `audit_event` (which the backend persists
        // and forwards to the host); on the host it simply logs locally.
        if (typeof UI_UPDATER !== 'undefined' && UI_UPDATER.addAuditAlert) {
            UI_UPDATER.addAuditAlert(
                'Security Violation',
                message,
                this.getSeverity(type),
                violation.severity === 'critical',
                false
            );
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
        // Security warnings must never destroy the page DOM or sever active WebRTC media streams.
        // Alert the interviewer via audit logs, and display a floating non-destructive alert on screen.
        console.warn('[Security] Security violation flagged:', title, message);
        if (typeof UI_UPDATER !== 'undefined' && UI_UPDATER.addAuditAlert) {
            UI_UPDATER.addAuditAlert('Security Warning', message, 'High', false);
        }
        let warnEl = document.getElementById('security-warning-overlay');
        if (!warnEl) {
            warnEl = document.createElement('div');
            warnEl.id = 'security-warning-overlay';
            warnEl.style.cssText = `
                position: fixed;
                top: 24px;
                left: 50%;
                transform: translateX(-50%);
                background: rgba(220, 38, 38, 0.95);
                color: #ffffff;
                padding: 12px 24px;
                border-radius: 12px;
                box-shadow: 0 10px 25px rgba(0, 0, 0, 0.6);
                z-index: 999999;
                font-family: 'Inter', sans-serif;
                font-weight: 600;
                font-size: 0.95rem;
                display: flex;
                align-items: center;
                gap: 10px;
                backdrop-filter: blur(8px);
                border: 1px solid rgba(255, 255, 255, 0.2);
            `;
            document.body.appendChild(warnEl);
        }
        warnEl.innerHTML = `<span>⚠️ ${title}: ${message}</span>`;
        clearTimeout(warnEl._timer);
        warnEl._timer = setTimeout(() => {
            if (warnEl && warnEl.parentElement) warnEl.parentElement.removeChild(warnEl);
        }, 5000);
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
