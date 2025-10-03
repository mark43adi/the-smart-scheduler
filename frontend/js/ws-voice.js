class WebSocketVoiceManager {
    constructor() {
        this.ws = null;
        this.mediaRecorder = null;
        this.audioContext = null;
        this.sourceNode = null;
        this.isConnected = false;
        this.isSpeaking = false;

        // Audio accumulation for complete playback
        this.audioChunks = [];
        this.currentAudioSource = null;
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
                if (event.data.size > 0 && this.isConnected && !this.isSpeaking) {
                    this.ws.send(event.data);
                    this.chunkCount = (this.chunkCount || 0) + 1;
                    if (this.chunkCount % 20 === 0) {
                        console.log(`📤 Sent ${this.chunkCount} chunks so far...`);
                    }
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

            const wsUrl = `${API_URL.replace('http', 'ws')}/ws/voice?token=${token}`;
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
                break;

            case 'thinking':
                this.showThinking();
                break;

            case 'response_text':
                this.addMessage(message.text, 'assistant', message.tools_used);
                this.hideThinking();
                break;

            case 'audio_complete':
                console.log('🔊 Audio streaming complete, playing accumulated audio');
                await this.playAccumulatedAudio();
                break;

            case 'ready':
                this.showStatus('Ready');
                this.isSpeaking = false;
                break;

            case 'error':
                this.showError(message.message);
                break;

            default:
                console.debug('Message:', message);
        }
    }

    // 🔊 Handle MP3 audio from ElevenLabs
    async handleAudioChunk(audioData) {
        // Don't queue individual chunks - they're incomplete MP3 fragments
        // Instead, accumulate them
        if (!this.audioChunks) {
            this.audioChunks = [];
        }
        this.audioChunks.push(audioData);
        this.isSpeaking = true;
    }

    async playAccumulatedAudio() {
        if (!this.audioChunks || this.audioChunks.length === 0) {
            console.log('No audio chunks to play');
            return;
        }

        try {
            console.log(`🔊 Playing accumulated audio (${this.audioChunks.length} chunks)`);
            
            // Combine all chunks into one complete MP3
            const totalLength = this.audioChunks.reduce((sum, chunk) => {
                const buffer = chunk instanceof ArrayBuffer ? chunk : chunk.buffer;
                return sum + buffer.byteLength;
            }, 0);

            const combinedBuffer = new Uint8Array(totalLength);
            let offset = 0;

            for (const chunk of this.audioChunks) {
                const buffer = chunk instanceof ArrayBuffer ? new Uint8Array(chunk) : new Uint8Array(chunk);
                combinedBuffer.set(buffer, offset);
                offset += buffer.byteLength;
            }

            // Now decode the complete MP3
            const audioBuffer = await this.audioContext.decodeAudioData(combinedBuffer.buffer);
            await this.playAudioBuffer(audioBuffer);

            console.log('✓ Audio playback complete');

        } catch (err) {
            console.error('Audio playback error:', err);
        } finally {
            this.audioChunks = [];
            this.isSpeaking = false;
        }
    }

    playAudioBuffer(audioBuffer) {
        return new Promise((resolve) => {
            const source = this.audioContext.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(this.audioContext.destination);
            source.onended = () => {
                this.currentAudioSource = null;
                resolve();
            };
            this.currentAudioSource = source;
            source.start();
        });
    }

    stopAudioPlayback() {
        if (this.currentAudioSource) {
            try {
                this.currentAudioSource.stop();
                this.currentAudioSource = null;
            } catch { }
        }
        this.audioChunks = [];
        this.isSpeaking = false;
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