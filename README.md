# Intervue - AI-Powered Interview Proctoring System

Advanced AI-based interview proctoring platform with real-time monitoring, anti-cheating detection, and enterprise-grade security.

## 🚀 Features

### **AI-Powered Monitoring**
- **Gaze Tracking**: Real-time eye movement analysis using MediaPipe FaceLandmarker
- **Head Pose Detection**: Advanced pose estimation with anomaly detection using autoencoders
- **Object Detection**: YOLO-based detection of prohibited objects (phones, books, etc.)
- **Audio Analysis**: Anomaly detection in audio patterns for suspicious behavior
- **Liveness Detection**: Anti-spoofing using facial analysis and blink detection

### **Security & Compliance**
- **Kiosk Mode**: Full-screen lock to prevent tab switching during interviews
- **Browser Extension**: Chrome extension for comprehensive candidate monitoring
- **Secure Architecture**: Signed invitation links, session validation, rate limiting
- **Audit Logging**: Complete security event tracking and compliance reporting

### **Real-Time Dashboard**
- **Host Dashboard**: Live monitoring with gaze tracking, alerts, and analytics
- **Candidate Dashboard**: Clean interface with calibration and status updates
- **Live Eye Analysis**: Picture-in-picture video monitoring with annotations

### **Enterprise Features**
- **MongoDB Atlas Integration**: Persistent storage and analytics
- **Scalable Architecture**: Multi-server support with shared state
- **White-Label Solution**: Embeddable proctoring for partner platforms
- **Analytics Dashboard**: Meeting analytics, risk scoring, and historical data

## 📋 Installation

### **Prerequisites**
- Python 3.11+
- MongoDB Atlas account (for persistent storage)
- Chrome browser (for extension)

### **Setup Steps**

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd interviewproject
   ```

2. **Create virtual environment**
   ```bash
   python -m venv .venv
   .venv\Scripts\activate  # Windows
   source .venv/bin/activate  # Linux/Mac
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your MongoDB Atlas credentials
   ```

5. **Run the application**
   ```bash
   python app.py
   ```

## 🎯 Usage

### **Start an Interview**
1. Navigate to `http://localhost:5000/host`
2. Create a meeting room with host password
3. Share the generated invite link with the candidate
4. Monitor the interview in real-time

### **Candidate Join**
1. Access the invite link
2. Complete calibration process
3. Enable browser extension for full proctoring
4. Participate in the interview with automatic monitoring

### **Monitor Session**
- Real-time gaze tracking
- Security alerts for violations
- Live video monitoring
- Risk scoring and analytics

## 🏗️ Architecture

### **Core Components**
- **Flask-SocketIO**: Real-time WebSocket communication
- **MediaPipe**: 468-point facial landmark tracking
- **YOLO**: Object detection ensemble (custom + standard models)
- **PyTorch**: Autoencoder for head pose anomaly detection
- **MongoDB Atlas**: Data persistence and analytics

### **Project Structure**
```
interviewproject/
├── app.py                    # Main Flask application
├── routes.py                 # API routes and endpoints
├── sockets.py                # Socket.IO event handlers
├── state.py                  # Application state management
├── database.py               # MongoDB Atlas integration
├── advanced_gaze_detector.py # Gaze and pose detection engine
├── audio_analyzer.py         # Audio anomaly detection
├── liveness_detector.py      # Anti-spoofing detection
├── extension/                # Chrome extension files
├── static/                   # Frontend JavaScript and CSS
├── templates/                # HTML templates
└── trained_models/           # AI model files
```

## 🔒 Security Features

- **Cryptographic Security**: HMAC-signed invitation links
- **Session Management**: Timeout, validation, and secure storage
- **Browser Monitoring**: Tab switching, dev tools, clipboard detection
- **Rate Limiting**: API endpoint protection
- **CORS Configuration**: Controlled cross-origin access
- **MongoDB Security**: Atlas enterprise-grade database security

## 📊 Analytics & Reporting

- **Meeting Analytics**: Duration, event counts, risk scoring
- **Partner Analytics**: Multi-tenant usage statistics
- **Audit Logs**: Complete security event history
- **Compliance Reports**: Exportable session data

## 🚀 Deployment

### **Development**
```bash
python app.py
```

### **Production**
- MongoDB Atlas for database
- Gunicorn with eventlet for WSGI server
- Nginx as reverse proxy
- SSL/TLS for secure connections

## 📝 Configuration

### **Environment Variables**
```env
MONGODB_URI=mongodb+srv://username:password@cluster.mongodb.net/
MONGODB_DB_NAME=intervue_proctoring
SECRET_KEY=your-secret-key
HOST_PASSWORD=admin123
CORS_ORIGINS=http://localhost:5000
```

## 🎯 Project Level

**Classification**: Advanced

This project represents an advanced AI/ML application with:
- Multi-modal AI integration (vision, audio, behavioral)
- Real-time video processing and WebSocket streaming
- Browser extension development
- Enterprise database integration
- Microservices architecture
- Production deployment infrastructure

## 📄 License

MIT License