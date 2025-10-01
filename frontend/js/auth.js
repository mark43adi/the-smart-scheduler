const API_URL = 'https://34.133.159.102:8080';

class AuthManager {
    constructor() {
        this.token = localStorage.getItem('auth_token');
        this.user = null;
    }

    async initialize() {
        // Check for token in URL (from OAuth redirect)
        const urlParams = new URLSearchParams(window.location.search);
        const token = urlParams.get('token');
        
        if (token) {
            this.token = token;
            localStorage.setItem('auth_token', token);
            // Clean URL
            window.history.replaceState({}, document.title, '/');
        }

        // If no token, redirect to login
        if (!this.token && !window.location.pathname.includes('login')) {
            window.location.href = '/login.html';
            return false;
        }

        // Verify token and get user info
        if (this.token) {
            try {
                const response = await fetch(`${API_URL}/auth/me`, {
                    headers: {
                        'Authorization': `Bearer ${this.token}`
                    }
                });

                if (response.ok) {
                    this.user = await response.json();
                    this.updateUI();
                    return true;
                } else {
                    this.logout();
                    return false;
                }
            } catch (error) {
                console.error('Auth error:', error);
                this.logout();
                return false;
            }
        }

        return false;
    }

    updateUI() {
        if (this.user) {
            document.getElementById('userName').textContent = this.user.name;
            document.getElementById('userAvatar').src = this.user.picture;
            document.getElementById('userAvatar').style.display = 'block';
            document.getElementById('logoutBtn').style.display = 'block';
        }
    }

    logout() {
        localStorage.removeItem('auth_token');
        this.token = null;
        this.user = null;
        window.location.href = '/login.html';
    }

    getAuthHeaders() {
        return {
            'Authorization': `Bearer ${this.token}`,
            'Content-Type': 'application/json'
        };
    }
}

// Initialize auth on page load
const authManager = new AuthManager();

// Logout button handler
document.addEventListener('DOMContentLoaded', () => {
    const logoutBtn = document.getElementById('logoutBtn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', () => authManager.logout());
    }
});
