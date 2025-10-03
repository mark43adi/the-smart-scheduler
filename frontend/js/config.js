const isLocalhost = window.location.hostname === 'localhost' || 
                    window.location.hostname === '127.0.0.1';

// Base URLs for different environments
const API_BASE_URL = isLocalhost 
    ? 'http://localhost:8080' 
    : `https://${window.location.hostname}`;

const WS_BASE_URL = isLocalhost 
    ? 'ws://localhost:8080' 
    : `wss://${window.location.hostname}`;

// Export configuration
const CONFIG = {
    API_URL: `${API_BASE_URL}/api`,
    WS_URL: `${WS_BASE_URL}/ws`,
    AUTH_URL: `${API_BASE_URL}/auth`,
};

console.log('Environment Config:', {
    hostname: window.location.hostname,
    isLocalhost: isLocalhost,
    API_URL: CONFIG.API_URL,
    WS_URL: CONFIG.WS_URL,
    AUTH_URL: CONFIG.AUTH_URL
});

// Make globally available
window.CONFIG = CONFIG;