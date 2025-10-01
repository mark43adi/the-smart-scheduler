class SmartSchedulerApp {
    constructor() {
        this.sessionId = null;
        this.isProcessing = false;
        this.contextUpdateInterval = null;
    }

    async initialize() {
        // Initialize auth
        const authenticated = await authManager.initialize();
        if (!authenticated) return;

        // Initialize voice
        await voiceManager.initialize();

        // Setup event listeners
        this.setupEventListeners();

        // Load session from backend response (not localStorage)
        // We'll get it from the first message response
        this.sessionId = null;

        // Update context once on load, then every minute
        this.updateContext();
        this.contextUpdateInterval = setInterval(() => this.updateContext(), 60000);
    }

    setupEventListeners() {
        // Send button
        document.getElementById('sendBtn').addEventListener('click', () => {
            this.sendMessage();
        });

        // Enter key in input
        document.getElementById('messageInput').addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });

        // Voice button (hold to record)
        const voiceBtn = document.getElementById('voiceBtn');
        
        voiceBtn.addEventListener('mousedown', () => {
            voiceManager.startRecording();
        });

        voiceBtn.addEventListener('mouseup', async () => {
            const audioBlob = await voiceManager.stopRecording();
            if (audioBlob) {
                this.sendVoiceMessage(audioBlob);
            }
        });

        // Touch events for mobile
        voiceBtn.addEventListener('touchstart', (e) => {
            e.preventDefault();
            voiceManager.startRecording();
        });

        voiceBtn.addEventListener('touchend', async (e) => {
            e.preventDefault();
            const audioBlob = await voiceManager.stopRecording();
            if (audioBlob) {
                this.sendVoiceMessage(audioBlob);
            }
        });
    }

    async updateContext() {
        try {
            const response = await fetch(`${API_URL}/api/context`, {
                headers: authManager.getAuthHeaders()
            });

            if (response.ok) {
                const context = await response.json();
                document.getElementById('currentTime').textContent = context.time;
                document.getElementById('currentDate').textContent = 
                    `${context.day}, ${context.date}`;
            }
        } catch (error) {
            console.error('Context update error:', error);
        }
    }

    async sendMessage() {
        const input = document.getElementById('messageInput');
        const message = input.value.trim();

        if (!message || this.isProcessing) return;

        // Clear input
        input.value = '';

        // Add user message to chat
        this.addMessage(message, 'user');

        // Show loading
        this.setLoading(true);
        this.isProcessing = true;

        try {
            const requestBody = {
                message: message
            };

            // Only include session_id if we have one
            if (this.sessionId) {
                requestBody.session_id = this.sessionId;
            }

            const response = await fetch(`${API_URL}/api/chat`, {
                method: 'POST',
                headers: authManager.getAuthHeaders(),
                body: JSON.stringify(requestBody)
            });

            if (!response.ok) {
                throw new Error('Failed to send message');
            }

            const data = await response.json();

            // CRITICAL: Store session_id from response
            if (data.session_id) {
                this.sessionId = data.session_id;
                console.log('Session ID:', this.sessionId, 'Turn:', data.turn_count);
            }

            // Add assistant response
            this.addMessage(data.reply, 'assistant', data.tools_used);

            // Play audio if available
            if (data.audio_url) {
                await voiceManager.playAudio(data.audio_url);
            }

        } catch (error) {
            console.error('Send message error:', error);
            this.addMessage(
                'Sorry, I encountered an error. Please try again.',
                'assistant'
            );
        } finally {
            this.setLoading(false);
            this.isProcessing = false;
        }
    }

    async sendVoiceMessage(audioBlob) {
        // Show loading
        this.setLoading(true);
        this.isProcessing = true;

        try {
            const result = await voiceManager.sendAudio(audioBlob, this.sessionId);

            // CRITICAL: Store session_id from response
            if (result.session_id) {
                this.sessionId = result.session_id;
                console.log('Session ID:', this.sessionId, 'Turn:', result.turn_count);
            }

            // Add transcribed message as user message
            this.addMessage(result.transcript, 'user');

            // Add assistant response
            this.addMessage(result.reply, 'assistant', result.tools_used);

            // Play audio response
            if (result.audio_url) {
                await voiceManager.playAudio(result.audio_url);
            }

        } catch (error) {
            console.error('Voice message error:', error);
            this.addMessage(
                'Sorry, I couldn\'t process your voice message. Please try again.',
                'assistant'
            );
        } finally {
            this.setLoading(false);
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

    setLoading(isLoading) {
        const overlay = document.getElementById('loadingOverlay');
        const sendBtn = document.getElementById('sendBtn');
        
        overlay.style.display = isLoading ? 'flex' : 'none';
        sendBtn.disabled = isLoading;
    }

    // Add cleanup method
    cleanup() {
        if (this.contextUpdateInterval) {
            clearInterval(this.contextUpdateInterval);
        }
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