class WebSocketVoiceManager {
    constructor() {
        this.ws = null;
        this.mediaRecorder = null;
        this.audioContext = null;
        this.isConnected = false;
        this.isUserSpeaking = false;

        // Latency tracking
        this.lastSpeechEndTime = null;
        this.responseStartTime = null;

        // Audio playback - SIMPLE queue-based approach
        this.audioQueue = [];
        this.isPlayingAudio = false;
        this.currentAudioSource = null;
        this.streamingComplete = false; // Backend done sending
    }

    async initialize() {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true,
                    sampleRate: 16000,
                    channelCount: 1
                }
            });

            let mimeType = 'audio/webm;codecs=opus';
            const supportedTypes = ['audio/webm;codecs=opus', 'audio/webm', 'audio/wav'];
            for (const type of supportedTypes) {
                if (MediaRecorder.isTypeSupported(type)) {
                    mimeType = type;
                    console.log(`🎙 Using audio format: ${type}`);
                    break;
                }
            }

            this.audioContext = new (window.AudioContext || window.webkitAudioContext)();

            this.mediaRecorder = new MediaRecorder(stream, {
                mimeType,
                audioBitsPerSecond: 16000
            });

            this.mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0 && this.isConnected) {
                    this.ws.send(event.data);
                }
            };

            console.log('✓ Audio initialized');
            return true;

        } catch (error) {
            console.error('❌ Mic access denied:', error);
            this.showError('Please allow microphone access');
            return false;
        }
    }

    async connect() {
        if (this.isConnected) return;

        try {
            const token = authManager.token;
            if (!token) throw new Error('Not authenticated');

            const wsUrl = `${window.CONFIG.WS_URL}?token=${authManager.token}`;
            this.ws = new WebSocket(wsUrl);
            this.ws.binaryType = 'arraybuffer';

            this.ws.onopen = () => {
                console.log('✓ WebSocket connected');
                this.isConnected = true;
                this.onConnectionChange(true);
                this.startRecording();
            };

            this.ws.onmessage = async (event) => {
                if (typeof event.data === 'string') {
                    await this.handleMessage(JSON.parse(event.data));
                } else {
                    // Binary audio chunk
                    await this.handleAudioChunk(event.data);
                }
            };

            this.ws.onerror = (err) => {
                console.error('❌ WebSocket error:', err);
                this.showError('Connection error');
            };

            this.ws.onclose = () => {
                console.log('WebSocket closed');
                this.isConnected = false;
                this.onConnectionChange(false);
                this.stopRecording();
            };

        } catch (error) {
            console.error('Connection error:', error);
            this.showError('Failed to connect');
        }
    }

    disconnect() {
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
        this.stopRecording();
        this.stopAudioPlayback();
        this.isConnected = false;
    }

    startRecording() {
        if (!this.mediaRecorder || this.mediaRecorder.state === 'recording') return;
        this.mediaRecorder.start(100);
        console.log('🎤 Recording started');
        this.updateRecordingUI(true);
    }

    stopRecording() {
        if (this.mediaRecorder && this.mediaRecorder.state === 'recording') {
            this.mediaRecorder.stop();
            console.log('🎤 Recording stopped');
        }
        this.updateRecordingUI(false);
    }

    async handleMessage(message) {
        switch (message.type) {
            case 'connected':
                this.showStatus('Connected! Start speaking...');
                break;

            case 'partial_transcript':
                this.showPartialTranscript(message.text);
                break;

            case 'transcript':
                this.addMessage(message.text, 'user');
                this.clearPartialTranscript();
                this.lastSpeechEndTime = Date.now();
                this.isUserSpeaking = false;
                break;

            case 'thinking':
                this.showThinking();
                break;

            case 'response_text':
                this.addMessage(message.text, 'assistant', message.tools_used);
                this.hideThinking();
                break;

            case 'audio_start':
                this.responseStartTime = Date.now();
                if (this.lastSpeechEndTime) {
                    const latency = this.responseStartTime - this.lastSpeechEndTime;
                    console.log(`⏱️ Latency: ${latency} ms`);
                }
                console.log('🔊 AI starting to speak');
                this.audioQueue = [];
                this.streamingComplete = false;
                break;

            case 'audio_complete':
                console.log('🔊 Backend done sending audio');
                this.streamingComplete = true;
                
                const endTime = Date.now();
                if (this.responseStartTime) {
                    const streamDuration = endTime - this.responseStartTime;
                    console.log(`⏱️ Streaming duration: ${streamDuration} ms`);
                }
                break;

            case 'interrupted':
                console.log('🛑 Interrupted');
                this.stopAudioPlayback();
                this.hideThinking();
                this.showStatus('Interrupted - listening...');
                break;

            case 'ready':
                this.showStatus('Ready');
                if (this.lastSpeechEndTime) {
                    const totalLatency = Date.now() - this.lastSpeechEndTime;
                    console.log(`✅ Total latency: ${totalLatency} ms`);
                }
                break;

            case 'error':
                this.showError(message.message);
                this.stopAudioPlayback();
                break;

            case 'silence_warning':
                this.showWarning(message.message);
                break;

            case 'timeout':
                this.showError(message.message);
                this.disconnect();
                break;

            default:
                console.debug('Message:', message);
        }
    }

    async handleAudioChunk(audioData) {
        this.audioQueue.push(audioData);
        console.log(`📦 Queued chunk, total: ${this.audioQueue.length}`);
        
        // Start playback if not already running
        if (!this.isPlayingAudio) {
            this.playAudioQueue();
        }
    }

    async playAudioQueue() {
        if (this.isPlayingAudio) {
            console.warn('⚠️ Playback already running');
            return;
        }
        
        this.isPlayingAudio = true;
        console.log('🔊 Starting playback loop');
        
        let chunkCount = 0;
        
        try {
            while (true) {
                // Check if we have chunks to play
                if (this.audioQueue.length > 0) {
                    const audioData = this.audioQueue.shift();
                    
                    try {
                        const audioBuffer = await this.audioContext.decodeAudioData(audioData);
                        chunkCount++;
                        console.log(`🎵 Playing chunk ${chunkCount}, remaining: ${this.audioQueue.length}`);
                        
                        // Play and WAIT for completion
                        await this.playAudioBuffer(audioBuffer);
                        
                    } catch (err) {
                        console.error('⚠️ Decode error:', err);
                    }
                }
                // No chunks in queue
                else {
                    // If backend is done AND queue is empty, we're finished
                    if (this.streamingComplete) {
                        console.log(`✓ Playback complete - played ${chunkCount} chunks`);
                        break;
                    }
                    // Otherwise wait for more chunks
                    else {
                        await new Promise(resolve => setTimeout(resolve, 50));
                    }
                }
            }
        } catch (err) {
            console.error('❌ Playback error:', err);
        } finally {
            this.isPlayingAudio = false;
            console.log('🔊 Playback loop ended');
        }
    }

    playAudioBuffer(audioBuffer) {
        return new Promise((resolve, reject) => {
            try {
                const source = this.audioContext.createBufferSource();
                source.buffer = audioBuffer;
                source.connect(this.audioContext.destination);
                
                source.onended = () => {
                    this.currentAudioSource = null;
                    resolve();
                };
                
                this.currentAudioSource = source;
                source.start(0);
            } catch (err) {
                reject(err);
            }
        });
    }

    stopAudioPlayback() {
        console.log('🛑 Stopping playback');
        
        // Stop current audio
        if (this.currentAudioSource) {
            try {
                this.currentAudioSource.stop();
                this.currentAudioSource = null;
            } catch (e) {
                console.warn('Stop error:', e);
            }
        }
        
        // Clear queue
        this.audioQueue = [];
        this.streamingComplete = true; // Force loop to exit
        this.isPlayingAudio = false;
    }

    // UI Methods
    onConnectionChange(connected) {
        const statusEl = document.getElementById('connectionStatus');
        const connectBtn = document.getElementById('connectBtn');

        if (connected) {
            statusEl.textContent = '🟢 Connected';
            statusEl.className = 'status connected';
            connectBtn.textContent = 'Disconnect';
        } else {
            statusEl.textContent = '🔴 Disconnected';
            statusEl.className = 'status disconnected';
            connectBtn.textContent = 'Connect Voice';
        }
    }

    updateRecordingUI(recording) {
        const indicator = document.getElementById('recordingIndicator');
        if (recording) {
            indicator.style.display = 'flex';
            indicator.innerHTML = `
                <div class="pulse-dot"></div>
                <span>Listening...</span>
            `;
        } else {
            indicator.style.display = 'none';
        }
    }

    showPartialTranscript(text) {
        let partialEl = document.getElementById('partialTranscript');

        if (!partialEl) {
            partialEl = document.createElement('div');
            partialEl.id = 'partialTranscript';
            partialEl.className = 'partial-transcript';
            document.getElementById('chatMessages').appendChild(partialEl);
        }

        partialEl.textContent = `"${text}..."`;
        this.scrollToBottom();
    }

    clearPartialTranscript() {
        const partialEl = document.getElementById('partialTranscript');
        if (partialEl) partialEl.remove();
    }

    showThinking() {
        this.hideThinking();

        const thinkingEl = document.createElement('div');
        thinkingEl.id = 'thinkingIndicator';
        thinkingEl.className = 'thinking-indicator';
        thinkingEl.innerHTML = `
            <div class="thinking-dots">
                <span></span><span></span><span></span>
            </div>
            <span>Processing...</span>
        `;
        document.getElementById('chatMessages').appendChild(thinkingEl);
        this.scrollToBottom();
    }

    hideThinking() {
        const thinkingEl = document.getElementById('thinkingIndicator');
        if (thinkingEl) thinkingEl.remove();
    }

    addMessage(text, sender, tools = []) {
        const messagesContainer = document.getElementById('chatMessages');

        const welcomeMsg = messagesContainer.querySelector('.welcome-message');
        if (welcomeMsg) welcomeMsg.remove();

        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${sender}`;

        const avatar = document.createElement('div');
        avatar.className = 'message-avatar';
        avatar.textContent = sender === 'user' ?
            (authManager.user?.name?.charAt(0) || 'U') : '🤖';

        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';
        contentDiv.textContent = text;

        messageDiv.appendChild(avatar);
        messageDiv.appendChild(contentDiv);

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
        this.scrollToBottom();
    }

    showStatus(message) {
        const statusEl = document.getElementById('statusMessage');
        if (statusEl) {
            statusEl.textContent = message;
            statusEl.className = 'status-message';
            statusEl.style.display = 'block';
            
            setTimeout(() => {
                if (statusEl.textContent === message) {
                    statusEl.style.display = 'none';
                }
            }, 3000);
        }
    }

    showWarning(message) {
        const statusEl = document.getElementById('statusMessage');
        if (statusEl) {
            statusEl.textContent = message;
            statusEl.className = 'status-message warning';
            statusEl.style.display = 'block';
        }
    }

    showError(message) {
        const statusEl = document.getElementById('statusMessage');
        if (statusEl) {
            statusEl.textContent = message;
            statusEl.className = 'status-message error';
            statusEl.style.display = 'block';
        }
    }

    scrollToBottom() {
        const container = document.getElementById('chatMessages');
        container.scrollTop = container.scrollHeight;
    }
}

// Global instance
const wsVoiceManager = new WebSocketVoiceManager();