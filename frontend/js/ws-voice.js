class WebSocketVoiceManager {
    constructor() {
        this.ws = null;
        this.mediaRecorder = null;
        this.audioContext = null;
        this.isConnected = false;
        this.isAISpeaking = false;
        this.isUserSpeaking = false;

        // Latency tracking
        this.lastSpeechEndTime = null;
        this.responseStartTime = null;

        // Audio buffer management - accumulate per sentence
        this.currentSentenceBuffer = [];
        this.sentenceQueue = [];
        this.isPlayingAudio = false;
        this.currentAudioSource = null;
        this.audioStarted = false;
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
                    console.log(`⏱️ Latency (speech_end → audio_start): ${latency} ms`);
                }
                console.log('🔊 AI starting to speak');
                this.isAISpeaking = true;
                this.audioStarted = false;
                this.currentSentenceBuffer = [];
                this.sentenceQueue = [];
                break;

            case 'audio_complete':
                console.log('🔊 Audio stream complete');
                
                // Finalize current sentence buffer if exists
                if (this.currentSentenceBuffer.length > 0) {
                    await this.finalizeSentence();
                }
                
                const endTime = Date.now();
                if (this.responseStartTime) {
                    const streamDuration = endTime - this.responseStartTime;
                    console.log(`⏱️ AI streaming duration: ${streamDuration} ms`);
                }
                break;

            case 'interrupted':
                console.log('🛑 AI speech interrupted by user');
                this.stopAudioPlayback();
                this.hideThinking();
                this.showStatus('Interrupted - listening...');
                break;

            case 'ready':
                this.showStatus('Ready');
                this.isAISpeaking = false;
                if (this.lastSpeechEndTime) {
                    const totalLatency = Date.now() - this.lastSpeechEndTime;
                    console.log(`✅ End-to-End Latency: ${totalLatency} ms`);
                }
                break;

            case 'error':
                this.showError(message.message);
                this.isAISpeaking = false;
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
        // Accumulate chunks for current sentence
        this.currentSentenceBuffer.push(audioData);
        
        // Heuristic: If we've accumulated enough data (64KB ~= 1 sentence from ElevenLabs)
        // OR if we haven't started playing yet and have some data, finalize this sentence
        const bufferSize = this.currentSentenceBuffer.reduce((sum, chunk) => {
            return sum + (chunk.byteLength || chunk.length);
        }, 0);
        
        // Start playback quickly with first ~20KB, then every ~64KB after
        const threshold = this.audioStarted ? 64000 : 20000;
        
        if (bufferSize >= threshold) {
            await this.finalizeSentence();
        }
    }

    async finalizeSentence() {
        if (this.currentSentenceBuffer.length === 0) return;
        
        // Combine all chunks into one complete MP3
        const totalLength = this.currentSentenceBuffer.reduce((sum, chunk) => {
            const buffer = chunk instanceof ArrayBuffer ? chunk : chunk.buffer;
            return sum + buffer.byteLength;
        }, 0);
        
        const combinedBuffer = new Uint8Array(totalLength);
        let offset = 0;
        
        for (const chunk of this.currentSentenceBuffer) {
            const buffer = chunk instanceof ArrayBuffer ? new Uint8Array(chunk) : new Uint8Array(chunk);
            combinedBuffer.set(buffer, offset);
            offset += buffer.byteLength;
        }
        
        // Queue this complete sentence for playback
        this.sentenceQueue.push(combinedBuffer.buffer);
        
        // Clear buffer for next sentence
        this.currentSentenceBuffer = [];
        
        // Start playing if not already
        if (!this.isPlayingAudio) {
            this.startStreamingAudioPlayback();
        }
    }

    async startStreamingAudioPlayback() {
        if (this.isPlayingAudio) return;
        this.isPlayingAudio = true;
        
        console.log('🔊 Starting streaming playback');
        
        try {
            while (this.isAISpeaking || this.sentenceQueue.length > 0) {
                // Wait for sentences if queue is empty
                if (this.sentenceQueue.length === 0) {
                    await new Promise(resolve => setTimeout(resolve, 100));
                    continue;
                }
                
                // Check if interrupted
                if (!this.isAISpeaking && this.sentenceQueue.length === 0) {
                    break;
                }
                
                // Get next complete sentence audio
                const sentenceAudio = this.sentenceQueue.shift();
                
                try {
                    // Decode complete MP3 sentence
                    const audioBuffer = await this.audioContext.decodeAudioData(sentenceAudio);
                    
                    // Mark that audio has started
                    if (!this.audioStarted) {
                        this.audioStarted = true;
                        console.log('🎵 First audio playing');
                    }
                    
                    // Check again before playing
                    if (!this.isAISpeaking && this.sentenceQueue.length === 0) {
                        break;
                    }
                    
                    await this.playAudioBuffer(audioBuffer);
                    
                } catch (err) {
                    console.error('Failed to decode sentence audio:', err);
                    // Continue to next sentence
                }
            }
            
            console.log('✓ Streaming playback complete');
            
        } catch (err) {
            console.error('Playback error:', err);
        } finally {
            this.isPlayingAudio = false;
        }
    }

    playAudioBuffer(audioBuffer) {
        return new Promise((resolve) => {
            // Don't play if interrupted
            if (!this.isAISpeaking) {
                resolve();
                return;
            }
            
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
        console.log('🛑 Stopping audio playback');
        
        // Stop current audio source
        if (this.currentAudioSource) {
            try {
                this.currentAudioSource.stop();
                this.currentAudioSource = null;
            } catch { }
        }
        
        // Clear all buffers
        this.currentSentenceBuffer = [];
        this.sentenceQueue = [];
        this.isAISpeaking = false;
        this.isPlayingAudio = false;
        this.audioStarted = false;
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