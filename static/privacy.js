/**
 * Privacy Controls and Data Minimization
 * Allows users to control what data is collected and stored
 */

class PrivacyController {
    constructor() {
        this.privacySettings = {
            // Data collection settings
            collectAudio: true,
            collectVideo: true,
            collectGazeData: true,
            collectScreenActivity: true,
            collectBrowserMetrics: true,
            
            // Data retention settings
            retainAudio: false,          // Don't store audio by default
            retainVideo: false,          // Don't store video by default
            retainGazeData: true,        // Store gaze data for analysis
            retainAuditLogs: true,       // Store audit logs
            retentionPeriod: 30,         // Days to retain data
            
            // Data minimization settings
            anonymizeIP: true,           // Anonymize IP addresses
            blurBackground: false,       // Blur background in video
            minimizeAudio: true,         // Only process audio features, not raw audio
            compressVideo: true,         // Compress video before transmission
            
            // User consent
            consentGiven: false,
            consentVersion: '1.0',
            consentDate: null
        };
        
        this.loadPrivacySettings();
        this.init();
    }
    
    init() {
        this.showConsentDialog();
        this.setupDataMinimization();
        this.setupPeriodicCleanup();
    }
    
    // ============================================================
    // CONSENT MANAGEMENT
    // ============================================================
    
    showConsentDialog() {
        if (this.privacySettings.consentGiven) return;
        
        const consentDialog = document.createElement('div');
        consentDialog.id = 'privacy-consent-dialog';
        consentDialog.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.8);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 10000;
        `;
        
        consentDialog.innerHTML = `
            <div style="
                background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
                padding: 40px;
                border-radius: 20px;
                max-width: 600px;
                color: white;
                border: 1px solid rgba(255, 255, 255, 0.1);
            ">
                <h2 style="margin-top: 0; color: #6C4EB1;">🔒 Privacy Consent</h2>
                <p style="line-height: 1.6; opacity: 0.9;">
                    This interview proctoring system collects the following data for security purposes:
                </p>
                <ul style="line-height: 1.8; opacity: 0.9;">
                    <li>Camera video (for gaze tracking and liveness detection)</li>
                    <li>Microphone audio (for voice activity monitoring)</li>
                    <li>Screen activity (tab switching, window focus)</li>
                    <li>Browser metrics (hardware info, timezone)</li>
                </ul>
                <p style="line-height: 1.6; opacity: 0.9;">
                    <strong>Data Privacy:</strong>
                    <br>• Audio and video are not stored by default
                    <br>• IP addresses are anonymized
                    <br>• Data is retained for ${this.privacySettings.retentionPeriod} days
                    <br>• You can adjust these settings in the Privacy panel
                </p>
                <div style="margin-top: 20px; display: flex; gap: 10px;">
                    <button id="accept-consent" style="
                        flex: 1;
                        padding: 12px;
                        background: linear-gradient(135deg, #6C4EB1, #8B6FB5);
                        border: none;
                        border-radius: 8px;
                        color: white;
                        cursor: pointer;
                        font-weight: 600;
                    ">Accept & Continue</button>
                    <button id="customize-consent" style="
                        flex: 1;
                        padding: 12px;
                        background: rgba(255, 255, 255, 0.1);
                        border: 1px solid rgba(255, 255, 255, 0.2);
                        border-radius: 8px;
                        color: white;
                        cursor: pointer;
                    ">Customize</button>
                </div>
            </div>
        `;
        
        document.body.appendChild(consentDialog);
        
        document.getElementById('accept-consent').addEventListener('click', () => {
            this.privacySettings.consentGiven = true;
            this.privacySettings.consentDate = new Date().toISOString();
            this.savePrivacySettings();
            consentDialog.remove();
        });
        
        document.getElementById('customize-consent').addEventListener('click', () => {
            this.showCustomizeDialog(consentDialog);
        });
    }
    
    showCustomizeDialog(parentDialog) {
        const customizeDialog = document.createElement('div');
        customizeDialog.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.9);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 10001;
        `;
        
        customizeDialog.innerHTML = `
            <div style="
                background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
                padding: 40px;
                border-radius: 20px;
                max-width: 500px;
                color: white;
                border: 1px solid rgba(255, 255, 255, 0.1);
                max-height: 80vh;
                overflow-y: auto;
            ">
                <h2 style="margin-top: 0; color: #6C4EB1;">⚙️ Privacy Settings</h2>
                
                <div style="margin: 20px 0;">
                    <label style="display: flex; align-items: center; margin-bottom: 15px;">
                        <input type="checkbox" id="privacy-audio" ${this.privacySettings.collectAudio ? 'checked' : ''} style="margin-right: 10px;">
                        <span>Collect Audio Data</span>
                    </label>
                    
                    <label style="display: flex; align-items: center; margin-bottom: 15px;">
                        <input type="checkbox" id="privacy-video" ${this.privacySettings.collectVideo ? 'checked' : ''} style="margin-right: 10px;">
                        <span>Collect Video Data</span>
                    </label>
                    
                    <label style="display: flex; align-items: center; margin-bottom: 15px;">
                        <input type="checkbox" id="privacy-gaze" ${this.privacySettings.collectGazeData ? 'checked' : ''} style="margin-right: 10px;">
                        <span>Collect Gaze Data</span>
                    </label>
                    
                    <label style="display: flex; align-items: center; margin-bottom: 15px;">
                        <input type="checkbox" id="privacy-screen" ${this.privacySettings.collectScreenActivity ? 'checked' : ''} style="margin-right: 10px;">
                        <span>Collect Screen Activity</span>
                    </label>
                    
                    <label style="display: flex; align-items: center; margin-bottom: 15px;">
                        <input type="checkbox" id="privacy-anonymize" ${this.privacySettings.anonymizeIP ? 'checked' : ''} style="margin-right: 10px;">
                        <span>Anonymize IP Address</span>
                    </label>
                    
                    <label style="display: flex; align-items: center; margin-bottom: 15px;">
                        <input type="checkbox" id="privacy-blur" ${this.privacySettings.blurBackground ? 'checked' : ''} style="margin-right: 10px;">
                        <span>Blur Background (if supported)</span>
                    </label>
                    
                    <label style="display: flex; align-items: center; margin-bottom: 15px;">
                        <input type="checkbox" id="privacy-compress" ${this.privacySettings.compressVideo ? 'checked' : ''} style="margin-right: 10px;">
                        <span>Compress Video Data</span>
                    </label>
                    
                    <div style="margin-top: 20px;">
                        <label>Data Retention Period (days):</label>
                        <input type="number" id="privacy-retention" value="${this.privacySettings.retentionPeriod}" min="1" max="365" style="
                            width: 100%;
                            padding: 8px;
                            margin-top: 5px;
                            background: rgba(255, 255, 255, 0.1);
                            border: 1px solid rgba(255, 255, 255, 0.2);
                            border-radius: 5px;
                            color: white;
                        ">
                    </div>
                </div>
                
                <div style="display: flex; gap: 10px; margin-top: 20px;">
                    <button id="save-privacy" style="
                        flex: 1;
                        padding: 12px;
                        background: linear-gradient(135deg, #6C4EB1, #8B6FB5);
                        border: none;
                        border-radius: 8px;
                        color: white;
                        cursor: pointer;
                        font-weight: 600;
                    ">Save Settings</button>
                    <button id="cancel-privacy" style="
                        flex: 1;
                        padding: 12px;
                        background: rgba(255, 255, 255, 0.1);
                        border: 1px solid rgba(255, 255, 255, 0.2);
                        border-radius: 8px;
                        color: white;
                        cursor: pointer;
                    ">Cancel</button>
                </div>
            </div>
        `;
        
        document.body.appendChild(customizeDialog);
        
        document.getElementById('save-privacy').addEventListener('click', () => {
            this.privacySettings.collectAudio = document.getElementById('privacy-audio').checked;
            this.privacySettings.collectVideo = document.getElementById('privacy-video').checked;
            this.privacySettings.collectGazeData = document.getElementById('privacy-gaze').checked;
            this.privacySettings.collectScreenActivity = document.getElementById('privacy-screen').checked;
            this.privacySettings.anonymizeIP = document.getElementById('privacy-anonymize').checked;
            this.privacySettings.blurBackground = document.getElementById('privacy-blur').checked;
            this.privacySettings.compressVideo = document.getElementById('privacy-compress').checked;
            this.privacySettings.retentionPeriod = parseInt(document.getElementById('privacy-retention').value);
            
            this.privacySettings.consentGiven = true;
            this.privacySettings.consentDate = new Date().toISOString();
            
            this.savePrivacySettings();
            customizeDialog.remove();
            parentDialog.remove();
        });
        
        document.getElementById('cancel-privacy').addEventListener('click', () => {
            customizeDialog.remove();
        });
    }
    
    // ============================================================
    // DATA MINIMIZATION
    // ============================================================
    
    setupDataMinimization() {
        // Anonymize IP address
        if (this.privacySettings.anonymizeIP) {
            this.anonymizeIPAddress();
        }
        
        // Apply video compression
        if (this.privacySettings.compressVideo) {
            this.enableVideoCompression();
        }
        
        // Apply audio minimization
        if (this.privacySettings.minimizeAudio) {
            this.enableAudioMinimization();
        }
    }
    
    anonymizeIPAddress() {
        // Hook into socket connection to anonymize IP
        if (typeof socket !== 'undefined' && socket) {
            const originalEmit = socket.emit;
            socket.emit = function(event, data) {
                if (data && data.ipAddress) {
                    data.ipAddress = this.hashIP(data.ipAddress);
                }
                return originalEmit.call(this, event, data);
            };
        }
    }
    
    hashIP(ip) {
        // Simple hash to anonymize IP
        const parts = ip.split('.');
        if (parts.length === 4) {
            return `${parts[0]}.${parts[1]}.xxx.xxx`;
        }
        return 'xxx.xxx.xxx.xxx';
    }
    
    enableVideoCompression() {
        // Compress video frames before sending
        // NOTE: processingCanvas is a top-level const in gaze.js, which loads
        // AFTER this file. Touching the binding during init (even via typeof)
        // throws a TDZ ReferenceError that used to abort PrivacyController
        // init entirely. Compression is optional — degrade, never crash.
        try {
            if (processingCanvas && processingCanvas.getContext) {
                // Compression is applied inside the ML processing loop (gaze.js)
                console.log('[Privacy] Video compression enabled');
            }
        } catch (e) {
            // gaze.js not evaluated yet (TDZ) — skip silently.
        }
    }
    
    enableAudioMinimization() {
        // Only process audio features, not raw audio
        console.log('[Privacy] Audio minimization enabled - only features extracted');
    }
    
    // ============================================================
    // DATA RETENTION
    // ============================================================
    
    setupPeriodicCleanup() {
        // Clean up old data periodically
        setInterval(() => {
            this.cleanupOldData();
        }, 24 * 60 * 60 * 1000); // Daily
    }
    
    cleanupOldData() {
        const cutoffDate = new Date();
        cutoffDate.setDate(cutoffDate.getDate() - this.privacySettings.retentionPeriod);
        
        console.log('[Privacy] Cleaning up data older than', cutoffDate);
        
        // Emit cleanup request to server
        if (typeof socket !== 'undefined' && socket && socket.connected) {
            socket.emit('cleanup_old_data', {
                cutoffDate: cutoffDate.toISOString()
            });
        }
    }
    
    // ============================================================
    // SETTINGS MANAGEMENT
    // ============================================================
    
    savePrivacySettings() {
        localStorage.setItem('privacySettings', JSON.stringify(this.privacySettings));
        console.log('[Privacy] Settings saved:', this.privacySettings);
    }
    
    loadPrivacySettings() {
        const saved = localStorage.getItem('privacySettings');
        if (saved) {
            try {
                const parsed = JSON.parse(saved);
                this.privacySettings = { ...this.privacySettings, ...parsed };
                console.log('[Privacy] Settings loaded:', this.privacySettings);
            } catch (e) {
                console.error('[Privacy] Failed to load settings:', e);
            }
        }
    }
    
    getPrivacySettings() {
        return { ...this.privacySettings };
    }
    
    updatePrivacySettings(newSettings) {
        this.privacySettings = { ...this.privacySettings, ...newSettings };
        this.savePrivacySettings();
    }
    
    // ============================================================
    // DATA COLLECTION CONTROL
    // ============================================================
    
    shouldCollectAudio() {
        return this.privacySettings.collectAudio && this.privacySettings.consentGiven;
    }
    
    shouldCollectVideo() {
        return this.privacySettings.collectVideo && this.privacySettings.consentGiven;
    }
    
    shouldCollectGazeData() {
        return this.privacySettings.collectGazeData && this.privacySettings.consentGiven;
    }
    
    shouldCollectScreenActivity() {
        return this.privacySettings.collectScreenActivity && this.privacySettings.consentGiven;
    }
}

// Initialize privacy controller
if (typeof window !== 'undefined') {
    window.privacyController = new PrivacyController();
    console.log('[Privacy] Privacy Controller initialized');
}
