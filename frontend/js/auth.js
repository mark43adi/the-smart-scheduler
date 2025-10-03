// js/auth.js - Updated to use CONFIG

class AuthManager {
    constructor() {
        this.token = null;
        this.user = null;
    }

    async initialize() {
        // Check if we have a token in URL (OAuth callback)
        const urlParams = new URLSearchParams(window.location.search);
        const token = urlParams.get('token');

        if (token) {
            // Store token
            this.token = token;
            localStorage.setItem('auth_token', token);
            
            // Remove token from URL
            window.history.replaceState({}, document.title, window.location.pathname);
            
            // Fetch user info
            await this.fetchUserInfo();
            return true;
        }

        // Check localStorage for existing token
        const storedToken = localStorage.getItem('auth_token');
        if (storedToken) {
            this.token = storedToken;
            
            // Validate token by fetching user info
            const valid = await this.fetchUserInfo();
            if (valid) {
                return true;
            }
        }

        // No valid auth - show login page
        this.showLoginPage();
        return false;
    }

    async fetchUserInfo() {
        try {
            const response = await fetch(`${window.CONFIG.AUTH_URL}/me`, {
                headers: this.getAuthHeaders()
            });

            if (response.ok) {
                this.user = await response.json();
                this.updateUI();
                return true;
            } else {
                // Token invalid
                this.clearAuth();
                return false;
            }
        } catch (error) {
            console.error('Failed to fetch user info:', error);
            this.clearAuth();
            return false;
        }
    }

    showLoginPage() {
        // Hide main app, show login page
        const mainApp = document.querySelector('.container');
        if (mainApp) {
            mainApp.style.display = 'none';
        }

        // Create login page
        const loginPage = document.createElement('div');
        loginPage.className = 'login-container';
        loginPage.innerHTML = `
            <div class="login-card">
                <div class="login-header">
                    <svg width="64" height="64" viewBox="0 0 64 64" fill="none">
                        <rect width="64" height="64" rx="16" fill="#6366f1"/>
                        <path d="M32 16v16l11.32 6.74" stroke="white" stroke-width="4" stroke-linecap="round"/>
                    </svg>
                    <h1>Smart Scheduler AI</h1>
                    <p>Your AI-powered voice assistant</p>
                </div>
                <div class="login-body">
                    <h2>Welcome!</h2>
                    <p>Sign in with your Google account to start scheduling meetings with AI.</p>
                    <button id="googleLoginBtn" class="btn-google">
                        <svg width="24" height="24" viewBox="0 0 24 24">
                            <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
                            <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                            <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
                            <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
                        </svg>
                        <span>Continue with Google</span>
                    </button>
                    <div class="features">
                        <div class="feature">
                            <div class="feature-icon">🎤</div>
                            <div class="feature-text">
                                <h3>Voice Enabled</h3>
                                <p>Speak naturally to schedule</p>
                            </div>
                        </div>
                        <div class="feature">
                            <div class="feature-icon">🤖</div>
                            <div class="feature-text">
                                <h3>AI Powered</h3>
                                <p>Smart scheduling suggestions</p>
                            </div>
                        </div>
                        <div class="feature">
                            <div class="feature-icon">📧</div>
                            <div class="feature-text">
                                <h3>Guest Management</h3>
                                <p>Easily invite attendees</p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `;

        // Add login page styles
        const style = document.createElement('style');
        style.textContent = `
            .login-container {
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
                padding: 20px;
            }
            .login-card {
                background: white;
                border-radius: 20px;
                max-width: 500px;
                width: 100%;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                overflow: hidden;
            }
            .login-header {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding: 40px;
                text-align: center;
                color: white;
            }
            .login-header h1 {
                margin: 20px 0 10px;
                font-size: 28px;
            }
            .login-header p {
                opacity: 0.9;
                font-size: 16px;
            }
            .login-body {
                padding: 40px;
            }
            .login-body h2 {
                margin-bottom: 10px;
                color: #333;
            }
            .login-body > p {
                color: #666;
                margin-bottom: 30px;
            }
            .btn-google {
                width: 100%;
                padding: 15px;
                background: white;
                border: 2px solid #e0e0e0;
                border-radius: 8px;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 12px;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.2s;
            }
            .btn-google:hover {
                border-color: #4285F4;
                box-shadow: 0 2px 8px rgba(66,133,244,0.2);
            }
            .features {
                margin-top: 40px;
                display: flex;
                flex-direction: column;
                gap: 20px;
            }
            .feature {
                display: flex;
                align-items: center;
                gap: 15px;
            }
            .feature-icon {
                font-size: 32px;
                width: 50px;
                text-align: center;
            }
            .feature-text h3 {
                font-size: 16px;
                color: #333;
                margin-bottom: 5px;
            }
            .feature-text p {
                font-size: 14px;
                color: #666;
            }
        `;
        document.head.appendChild(style);
        document.body.appendChild(loginPage);

        // Add click handler
        document.getElementById('googleLoginBtn').addEventListener('click', () => {
            this.initiateGoogleLogin();
        });
    }

    async initiateGoogleLogin() {
        try {
            const response = await fetch(`${window.CONFIG.AUTH_URL}/login`);
            const data = await response.json();
            
            if (data.auth_url) {
                // Redirect to Google OAuth
                window.location.href = data.auth_url;
            }
        } catch (error) {
            console.error('Login error:', error);
            alert('Failed to initiate login. Please try again.');
        }
    }

    clearAuth() {
        this.token = null;
        this.user = null;
        localStorage.removeItem('auth_token');
    }

    logout() {
        this.clearAuth();
        window.location.reload();
    }

    getAuthHeaders() {
        return {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${this.token}`
        };
    }

    updateUI() {
        if (this.user) {
            // Show main app
            const mainApp = document.querySelector('.container');
            if (mainApp) {
                mainApp.style.display = 'block';
            }

            // Remove login page if exists
            const loginPage = document.querySelector('.login-container');
            if (loginPage) {
                loginPage.remove();
            }

            // Update user info
            const userNameEl = document.getElementById('userName');
            const logoutBtn = document.getElementById('logoutBtn');
            
            if (userNameEl) {
                userNameEl.textContent = this.user.name || this.user.email;
            }
            
            if (logoutBtn) {
                logoutBtn.style.display = 'inline-block';
            }
        }
    }
}

// Global instance
const authManager = new AuthManager();