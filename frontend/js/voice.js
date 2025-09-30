class VoiceManager {
    constructor() {
        this.mediaRecorder = null;
        this.audioChunks = [];
        this.isRecording = false;
        this.audioPlayer = new Audio();
    }

    async initialize() {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            this.mediaRecorder = new MediaRecorder(stream);
            
            this.mediaRecorder.ondataavailable = (event) => {
                this.audioChunks.push(event.data);
            };
            
            return true;
        } catch (error) {
            console.error('Microphone access denied:', error);
            return false;
        }
    }

    startRecording() {
        if (!this.mediaRecorder) {
            alert('Please allow microphone access');
            return false;
        }

        this.audioChunks = [];
        this.mediaRecorder.start();
        this.isRecording = true;
        
        // Update UI
        const voiceBtn = document.getElementById('voiceBtn');
        const voiceIndicator = document.getElementById('voiceIndicator');
        voiceBtn.classList.add('recording');
        voiceIndicator.style.display = 'flex';
        
        return true;
    }

    stopRecording() {
        return new Promise((resolve) => {
            if (!this.isRecording) {
                resolve(null);
                return;
            }

            this.mediaRecorder.onstop = () => {
                const audioBlob = new Blob(this.audioChunks, { type: 'audio/webm' });
                this.isRecording = false;
                
                // Update UI
                const voiceBtn = document.getElementById('voiceBtn');
                const voiceIndicator = document.getElementById('voiceIndicator');
                voiceBtn.classList.remove('recording');
                voiceIndicator.style.display = 'none';
                
                resolve(audioBlob);
            };

            this.mediaRecorder.stop();
        });
    }

    async sendAudio(audioBlob, sessionId) {
        const formData = new FormData();
        formData.append('audio', audioBlob, 'recording.webm');
        
        // Only add session_id if we have one
        if (sessionId) {
            formData.append('session_id', sessionId);
        }

        try {
            const response = await fetch(`${API_URL}/voice/transcribe`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${authManager.token}`
                },
                body: formData
            });

            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`Transcription failed: ${errorText}`);
            }

            return await response.json();
        } catch (error) {
            console.error('Voice send error:', error);
            throw error;
        }
    }

    async playAudio(audioUrl) {
        try {
            // Ensure full URL
            const fullUrl = audioUrl.startsWith('http') ? audioUrl : `${API_URL}${audioUrl}`;
            this.audioPlayer.src = fullUrl;
            await this.audioPlayer.play();
        } catch (error) {
            console.error('Audio playback error:', error);
        }
    }
}

const voiceManager = new VoiceManager();