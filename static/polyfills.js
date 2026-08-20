/**
 * Browser Compatibility Polyfills
 * Ensures functionality across different browsers and versions
 */

// ============================================================
// FETCH POLYFILL
// ============================================================
if (!window.fetch) {
    window.fetch = function(url, options) {
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open(options.method || 'GET', url);
            
            if (options.headers) {
                Object.keys(options.headers).forEach(key => {
                    xhr.setRequestHeader(key, options.headers[key]);
                });
            }
            
            xhr.onload = () => {
                resolve({
                    ok: xhr.status >= 200 && xhr.status < 300,
                    status: xhr.status,
                    json: () => JSON.parse(xhr.responseText),
                    text: () => xhr.responseText
                });
            };
            
            xhr.onerror = () => reject(new Error('Network error'));
            xhr.send(options.body);
        });
    };
}

// ============================================================
// PROMISE POLYFILL
// ============================================================
if (!window.Promise) {
    window.Promise = function(executor) {
        const self = this;
        self.state = 'pending';
        self.value = undefined;
        self.handlers = [];
        
        function resolve(value) {
            if (self.state === 'pending') {
                self.state = 'fulfilled';
                self.value = value;
                self.handlers.forEach(handler => handler.onFulfilled(value));
            }
        }
        
        function reject(reason) {
            if (self.state === 'pending') {
                self.state = 'rejected';
                self.value = reason;
                self.handlers.forEach(handler => handler.onRejected(reason));
            }
        }
        
        this.then = function(onFulfilled, onRejected) {
            return new Promise((resolve, reject) => {
                self.handlers.push({
                    onFulfilled: (value) => {
                        try {
                            const result = onFulfilled ? onFulfilled(value) : value;
                            resolve(result);
                        } catch (e) {
                            reject(e);
                        }
                    },
                    onRejected: (reason) => {
                        try {
                            const result = onRejected ? onRejected(reason) : reason;
                            resolve(result);
                        } catch (e) {
                            reject(e);
                        }
                    }
                });
            });
        };
        
        this.catch = function(onRejected) {
            return this.then(null, onRejected);
        };
        
        try {
            executor(resolve, reject);
        } catch (e) {
            reject(e);
        }
    };
}

// ============================================================
// CUSTOM EVENT POLYFILL
// ============================================================
if (!window.CustomEvent || typeof window.CustomEvent !== 'function') {
    window.CustomEvent = function(event, params) {
        params = params || { bubbles: false, cancelable: false, detail: undefined };
        const evt = document.createEvent('CustomEvent');
        evt.initCustomEvent(event, params.bubbles, params.cancelable, params.detail);
        return evt;
    };
    
    window.CustomEvent.prototype = window.Event.prototype;
}

// ============================================================
// MEDIA DEVICES POLYFILL
// ============================================================
if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    const legacyGetUserMedia =
        navigator.getUserMedia ||
        navigator.webkitGetUserMedia ||
        navigator.mozGetUserMedia;
    const legacyGetDisplayMedia =
        navigator.getDisplayMedia ||
        navigator.webkitGetDisplayMedia ||
        navigator.mozGetDisplayMedia;

    // On insecure origins (http://LAN-IP, http://10.x.x.x) `navigator.mediaDevices`
    // is undefined AND cannot be assigned (it is a getter-only accessor on
    // Navigator.prototype). If there is nothing legacy to delegate to, leave it
    // undefined rather than crashing the rest of this polyfill file: webrtc.js
    // detects that condition and shows the "not a secure context" guidance.
    if (legacyGetUserMedia || legacyGetDisplayMedia) {
        try {
            const md = navigator.mediaDevices || {};

            if (!md.getUserMedia && legacyGetUserMedia) {
                md.getUserMedia = function(constraints) {
                    return new Promise((resolve, reject) => {
                        legacyGetUserMedia.call(navigator, constraints, resolve, reject);
                    });
                };
            }

            if (!md.getDisplayMedia && legacyGetDisplayMedia) {
                md.getDisplayMedia = function(constraints) {
                    return new Promise((resolve, reject) => {
                        legacyGetDisplayMedia.call(navigator, constraints, resolve, reject);
                    });
                };
            }

            if (!navigator.mediaDevices) {
                navigator.mediaDevices = md;
            }
        } catch (e) {
            console.warn("MediaDevices polyfill skipped:", e);
        }
    }
}

// ============================================================
// WEBRTC POLYFILL
// ============================================================
if (!window.RTCPeerConnection) {
    window.RTCPeerConnection = window.webkitRTCPeerConnection || 
                               window.mozRTCPeerConnection;
}

if (!window.RTCSessionDescription) {
    window.RTCSessionDescription = window.webkitRTCSessionDescription || 
                                    window.mozRTCSessionDescription;
}

if (!window.RTCIceCandidate) {
    window.RTCIceCandidate = window.webkitRTCIceCandidate || 
                              window.mozRTCIceCandidate;
}

// ============================================================
// PERFORMANCE POLYFILL
// ============================================================
if (!window.performance) {
    window.performance = {};
}

if (!window.performance.now) {
    window.performance.now = function() {
        return Date.now();
    };
}

// ============================================================
// ARRAY POLYFILLS
// ============================================================
if (!Array.prototype.includes) {
    Array.prototype.includes = function(searchElement, fromIndex) {
        if (this == null) {
            throw new TypeError('Array.prototype.includes called on null or undefined');
        }
        
        const O = Object(this);
        const len = parseInt(O.length) || 0;
        
        if (len === 0) return false;
        
        const n = parseInt(fromIndex) || 0;
        let k = n >= 0 ? n : Math.max(len + n, 0);
        
        while (k < len) {
            if (O[k] === searchElement) return true;
            k++;
        }
        
        return false;
    };
}

if (!Array.prototype.findIndex) {
    Array.prototype.findIndex = function(predicate) {
        if (this == null) {
            throw new TypeError('Array.prototype.findIndex called on null or undefined');
        }
        
        const O = Object(this);
        const len = parseInt(O.length) || 0;
        
        if (len === 0) return -1;
        
        let k = 0;
        while (k < len) {
            if (predicate.call(this, O[k], k, O)) return k;
            k++;
        }
        
        return -1;
    };
}

// ============================================================
// OBJECT POLYFILLS
// ============================================================
if (!Object.assign) {
    Object.assign = function(target) {
        if (target == null) {
            throw new TypeError('Cannot convert undefined or null to object');
        }
        
        const to = Object(target);
        
        for (let index = 1; index < arguments.length; index++) {
            const nextSource = arguments[index];
            
            if (nextSource != null) {
                for (const nextKey in nextSource) {
                    if (Object.prototype.hasOwnProperty.call(nextSource, nextKey)) {
                        to[nextKey] = nextSource[nextKey];
                    }
                }
            }
        }
        
        return to;
    };
}

if (!Object.values) {
    Object.values = function(obj) {
        if (obj !== Object(obj)) {
            throw new TypeError('Object.values called on non-object');
        }
        
        return Object.keys(obj).map(key => obj[key]);
    };
}

// ============================================================
// STRING POLYFILLS
// ============================================================
if (!String.prototype.includes) {
    String.prototype.includes = function(search, start) {
        if (typeof start !== 'number') {
            start = 0;
        }
        
        if (start + search.length > this.length) {
            return false;
        }
        
        return this.indexOf(search, start) !== -1;
    };
}

if (!String.prototype.startsWith) {
    String.prototype.startsWith = function(searchString, position) {
        position = position || 0;
        return this.indexOf(searchString, position) === position;
    };
}

if (!String.prototype.endsWith) {
    String.prototype.endsWith = function(searchString, position) {
        const subjectString = this.toString();
        if (typeof position !== 'number' || !isFinite(position) || 
            Math.floor(position) !== position || position > subjectString.length) {
            position = subjectString.length;
        }
        position -= searchString.length;
        const lastIndex = subjectString.indexOf(searchString, position);
        return lastIndex !== -1 && lastIndex === position;
    };
}

// ============================================================
// BROWSER DETECTION
// ============================================================
const BrowserCompat = {
    isChrome: /Chrome/.test(navigator.userAgent) && /Google Inc/.test(navigator.vendor),
    isFirefox: /Firefox/.test(navigator.userAgent),
    isSafari: /Safari/.test(navigator.userAgent) && /Apple Computer/.test(navigator.vendor),
    isEdge: /Edge/.test(navigator.userAgent),
    isIE: /MSIE/.test(navigator.userAgent) || /Trident/.test(navigator.userAgent),
    
    supportsWebRTC: function() {
        return !!(window.RTCPeerConnection || 
                   window.webkitRTCPeerConnection || 
                   window.mozRTCPeerConnection);
    },
    
    supportsGetUserMedia: function() {
        return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
    },
    
    supportsGetDisplayMedia: function() {
        return !!(navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia);
    },
    
    supportsSecureContext: function() {
        return window.isSecureContext || 
               window.location.protocol === 'https:' || 
               window.location.hostname === 'localhost' ||
               window.location.hostname === '127.0.0.1';
    },
    
    getBrowserInfo: function() {
        return {
            name: this.getBrowserName(),
            version: this.getBrowserVersion(),
            supportsWebRTC: this.supportsWebRTC(),
            supportsGetUserMedia: this.supportsGetUserMedia(),
            supportsGetDisplayMedia: this.supportsGetDisplayMedia(),
            supportsSecureContext: this.supportsSecureContext()
        };
    },
    
    getBrowserName: function() {
        if (this.isChrome) return 'Chrome';
        if (this.isFirefox) return 'Firefox';
        if (this.isSafari) return 'Safari';
        if (this.isEdge) return 'Edge';
        if (this.isIE) return 'Internet Explorer';
        return 'Unknown';
    },
    
    getBrowserVersion: function() {
        const match = navigator.userAgent.match(/(Chrome|Firefox|Safari|Edge|MSIE)\/([\d.]+)/);
        return match ? match[2] : 'Unknown';
    },
    
    showCompatibilityWarning: function() {
        const info = this.getBrowserInfo();
        const warnings = [];
        
        if (!info.supportsWebRTC) {
            warnings.push('WebRTC is not supported in this browser');
        }
        
        if (!info.supportsGetUserMedia) {
            warnings.push('Camera/microphone access is not supported');
        }
        
        if (!info.supportsGetDisplayMedia) {
            warnings.push('Screen sharing is not supported');
        }
        
        if (!info.supportsSecureContext && window.location.protocol !== 'https:') {
            warnings.push('Secure context required for camera access (use HTTPS or localhost)');
        }
        
        if (warnings.length > 0) {
            console.warn('[BrowserCompat] Compatibility warnings:', warnings);
            return warnings;
        }
        
        return [];
    }
};

// Log browser compatibility on load
console.log('[BrowserCompat] Browser info:', BrowserCompat.getBrowserInfo());
BrowserCompat.showCompatibilityWarning();
