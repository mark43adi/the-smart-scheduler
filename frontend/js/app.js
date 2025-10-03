class SmartSchedulerApp {
    constructor() {
        this.sessionId = null;
        this.isProcessing = false;
        this.voiceMode = 'websocket'; // 'websocket' or 'traditional'
    }

    async initialize() {
        // Initialize auth
        const authenticated = await authManager.initialize();
        if (!authenticated) return;

        // Initialize WebSocket voice manager
        const voiceInitialized = await wsVoiceManager.initialize();
        if (!voiceInitialized) {
            console.warn('Voice features unavailable');
        }

        // Setup event listeners
        this.setupEventListeners();

        // Check if user prefers voice mode
        this.setupVoiceMode();
    }

    setupEventListeners() {
        // Connect button for WebSocket voice
        const connectBtn = document.getElementById('connectBtn');
        if (connectBtn) {
            connectBtn.onclick = () => {
                if (wsVoiceManager.isConnected) {
                    wsVoiceManager.disconnect();
                } else {
                    wsVoiceManager.connect();
                }
            };
        }

        // Optional: Text input fallback (if you want to keep it)
        const messageInput = document.getElementById('messageInput');
        const sendBtn = document.getElementById('sendBtn');
        
        if (messageInput && sendBtn) {
            sendBtn.addEventListener('click', () => {
                this.sendTextMessage();
            });

            messageInput.addEventListener('keypress', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    this.sendTextMessage();
                }
            });
        }

        // Logout button
        const logoutBtn = document.getElementById('logoutBtn');
        if (logoutBtn) {
            logoutBtn.onclick = () => {
                wsVoiceManager.disconnect();
                authManager.logout();
            };
        }
    }

    setupVoiceMode() {
        // Auto-connect to voice on load (optional)
        const autoConnect = localStorage.getItem('autoConnectVoice');
        if (autoConnect === 'true') {
            setTimeout(() => {
                wsVoiceManager.connect();
            }, 1000);
        }
    }

    async sendTextMessage() {
        const input = document.getElementById('messageInput');
        if (!input) return;

        const message = input.value.trim();
        if (!message || this.isProcessing) return;

        input.value = '';
        this.addMessage(message, 'user');

        this.isProcessing = true;
        this.showLoading(true);

        try {
            const requestBody = {
                message: message
            };

            if (this.sessionId) {
                requestBody.session_id = this.sessionId;
            }

            const response = await fetch(`${API_URL}/chat`, {
                method: 'POST',
                headers: authManager.getAuthHeaders(),
                body: JSON.stringify(requestBody)
            });

            if (!response.ok) {
                throw new Error('Failed to send message');
            }

            const data = await response.json();

            if (data.session_id) {
                this.sessionId = data.session_id;
            }

            this.addMessage(data.reply, 'assistant', data.tools_used);

        } catch (error) {
            console.error('Send message error:', error);
            this.addMessage(
                'Sorry, I encountered an error. Please try again.',
                'assistant'
            );
        } finally {
            this.showLoading(false);
            this.isProcessing = false;
        }
    }

    addMessage(text, sender, tools = []) {
        const messagesContainer = document.getElementById('chatMessages');
        
        // Remove welcome message if exists
        const welcomeMsg = messagesContainer.querySelector('.welcome-message');
        if (welcomeMsg) {
            welcomeMsg.remove();
        }

        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${sender}`;

        const avatar = document.createElement('div');
        avatar.className = 'message-avatar';
        avatar.textContent = sender === 'user' ? 
            (authManager.user?.name?.charAt(0) || 'U') : 
            '🤖';

        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';
        contentDiv.textContent = text;

        messageDiv.appendChild(avatar);
        messageDiv.appendChild(contentDiv);

        // Add tools info if any
        if (tools && tools.length > 0) {
            const toolsDiv = document.createElement('div');
            toolsDiv.className = 'message-tools';
            
            tools.forEach(tool => {
                const toolItem = document.createElement('div');
                toolItem.className = 'tool-item';
                toolItem.innerHTML = `
                    <span class="tool-icon">✓</span>
                    <span>Used: ${tool.tool}</span>
                `;
                toolsDiv.appendChild(toolItem);
            });
            
            contentDiv.appendChild(toolsDiv);
        }

        messagesContainer.appendChild(messageDiv);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    showLoading(isLoading) {
        const overlay = document.getElementById('loadingOverlay');
        if (overlay) {
            overlay.style.display = isLoading ? 'flex' : 'none';
        }
    }

    cleanup() {
        wsVoiceManager.disconnect();
    }
}

// Initialize app when DOM is ready
let app;
document.addEventListener('DOMContentLoaded', async () => {
    app = new SmartSchedulerApp();
    await app.initialize();
});

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    if (app) {
        app.cleanup();
    }
});