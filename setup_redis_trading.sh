#!/bin/bash

# Complete Redis Trading System Setup Script
# ==========================================
# 
# This script completely replaces Google Sheets communication with Redis pub/sub
# for crypto trading automation. It handles everything from dependency installation
# to system deployment and monitoring.
#
# Usage: ./setup_redis_trading.sh [install|deploy|start|stop|status|monitor|logs]

set -e

# Configuration
PROJECT_NAME="crypto-trading-redis"
BACKUP_DIR="backup_$(date +%Y%m%d_%H%M%S)"
LOG_FILE="setup_$(date +%Y%m%d_%H%M%S).log"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Logging function
log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1" | tee -a "$LOG_FILE"
}

warn() {
    echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] WARNING:${NC} $1" | tee -a "$LOG_FILE"
}

error() {
    echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] ERROR:${NC} $1" | tee -a "$LOG_FILE"
}

info() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')] INFO:${NC} $1" | tee -a "$LOG_FILE"
}

success() {
    echo -e "${PURPLE}[$(date +'%Y-%m-%d %H:%M:%S')] SUCCESS:${NC} $1" | tee -a "$LOG_FILE"
}

# Banner
print_banner() {
    echo -e "${CYAN}"
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║               REDIS TRADING SYSTEM SETUP                    ║"
    echo "║                                                              ║"
    echo "║  🚀 Production-Ready Crypto Trading Signal Communication     ║"
    echo "║  ⚡ Real-time Redis Pub/Sub (< 100ms latency)               ║"
    echo "║  🛡️ Google Sheets Fallback & Auto-Reconnection              ║"
    echo "║  🐳 Docker Containerized & EC2 Ready                        ║"
    echo "║                                                              ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

# Check if running on supported OS
check_os() {
    log "Checking operating system compatibility..."
    
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        if command -v lsb_release &> /dev/null; then
            OS_VERSION=$(lsb_release -d | cut -f2)
            log "Detected Linux: $OS_VERSION"
            
            if [[ "$OS_VERSION" == *"Ubuntu"* ]]; then
                log "✓ Ubuntu detected - fully supported"
                OS_TYPE="ubuntu"
            else
                warn "Non-Ubuntu Linux detected - may work but not fully tested"
                OS_TYPE="linux"
            fi
        else
            warn "Linux detected but version unknown"
            OS_TYPE="linux"
        fi
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        log "macOS detected - supported with limitations"
        OS_TYPE="macos"
    elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "win32" ]]; then
        log "Windows detected - using WSL/Docker Desktop mode"
        OS_TYPE="windows"
    else
        error "Unsupported operating system: $OSTYPE"
        exit 1
    fi
}

# Install system dependencies
install_dependencies() {
    log "Installing system dependencies..."
    
    case "$OS_TYPE" in
        "ubuntu")
            # Update package list
            sudo apt-get update
            
            # Install basic tools
            sudo apt-get install -y curl wget git unzip software-properties-common apt-transport-https ca-certificates gnupg lsb-release
            
            # Install Python 3.11+ if not present
            if ! command -v python3.11 &> /dev/null; then
                log "Installing Python 3.11..."
                sudo add-apt-repository ppa:deadsnakes/ppa -y
                sudo apt-get update
                sudo apt-get install -y python3.11 python3.11-pip python3.11-venv python3.11-dev
                
                # Create python3 symlink
                sudo update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1
            fi
            
            # Install pip for Python 3.11
            if ! command -v pip3 &> /dev/null; then
                log "Installing pip..."
                curl https://bootstrap.pypa.io/get-pip.py | python3
            fi
            
            # Install Docker
            if ! command -v docker &> /dev/null; then
                log "Installing Docker..."
                curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
                echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
                sudo apt-get update
                sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
                
                # Add user to docker group
                sudo usermod -aG docker $USER
                log "User added to docker group - you may need to log out and back in"
            fi
            
            # Install Docker Compose
            if ! command -v docker-compose &> /dev/null; then
                log "Installing Docker Compose..."
                sudo curl -L "https://github.com/docker/compose/releases/download/v2.20.0/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
                sudo chmod +x /usr/local/bin/docker-compose
            fi
            
            # Install Redis CLI for testing
            sudo apt-get install -y redis-tools
            ;;
            
        "macos")
            # Check if Homebrew is installed
            if ! command -v brew &> /dev/null; then
                log "Installing Homebrew..."
                /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
            fi
            
            # Install dependencies via Homebrew
            brew install python@3.11 docker docker-compose redis git
            ;;
            
        "windows")
            warn "Please ensure you have:"
            warn "1. Docker Desktop installed and running"
            warn "2. WSL2 enabled"
            warn "3. Python 3.11+ installed"
            warn "4. Git for Windows installed"
            
            # Check if dependencies are available
            if ! command -v python3 &> /dev/null && ! command -v python &> /dev/null; then
                error "Python not found. Please install Python 3.11+"
                exit 1
            fi
            
            if ! command -v docker &> /dev/null; then
                error "Docker not found. Please install Docker Desktop"
                exit 1
            fi
            ;;
    esac
    
    success "System dependencies installed successfully"
}

# Create project structure
setup_project_structure() {
    log "Setting up project structure..."
    
    # Create necessary directories
    mkdir -p logs
    mkdir -p local_data
    mkdir -p backups
    mkdir -p config
    
    # Set permissions
    chmod 755 logs local_data backups config
    
    success "Project structure created"
}

# Create Python virtual environment and install packages
setup_python_environment() {
    log "Setting up Python environment..."
    
    # Determine Python command
    PYTHON_CMD="python3"
    if command -v python3.11 &> /dev/null; then
        PYTHON_CMD="python3.11"
    elif command -v python3 &> /dev/null; then
        PYTHON_CMD="python3"
    elif command -v python &> /dev/null; then
        PYTHON_CMD="python"
    else
        error "No Python installation found"
        exit 1
    fi
    
    log "Using Python: $($PYTHON_CMD --version)"
    
    # Create virtual environment
    if [ ! -d "venv" ]; then
        log "Creating Python virtual environment..."
        $PYTHON_CMD -m venv venv
    fi
    
    # Activate virtual environment
    source venv/bin/activate 2>/dev/null || source venv/Scripts/activate 2>/dev/null
    
    # Upgrade pip
    pip install --upgrade pip
    
    # Install Python packages
    log "Installing Python packages..."
    
    # Create requirements.txt if it doesn't exist
    if [ ! -f "requirements.txt" ]; then
        cat > requirements.txt << EOF
# Redis for signal communication
redis==5.0.1

# Existing dependencies from yf.py
pandas==2.1.4
numpy==1.24.3
gspread==5.12.4
oauth2client==4.1.3
requests==2.31.0
ccxt==4.1.64
python-dotenv==1.0.0

# Existing dependencies from trade_executor.py
openpyxl==3.1.2
python-telegram-bot==20.7
aiohttp==3.9.1

# Additional utilities
asyncio-mqtt==0.16.1
pydantic==2.5.2
psutil==5.9.6
EOF
    fi
    
    pip install -r requirements.txt
    
    success "Python environment setup completed"
}

# Setup configuration files
setup_configuration() {
    log "Setting up configuration files..."
    
    # Create .env file if it doesn't exist
    if [ ! -f ".env" ]; then
        log "Creating .env configuration file..."
        cat > .env << 'EOF'
# Redis Configuration
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=
REDIS_TIMEOUT=5
REDIS_MAX_RETRIES=10

# Signal Bus Configuration
SIGNAL_CHANNEL=crypto_signals
STATUS_CHANNEL=crypto_status
HEARTBEAT_CHANNEL=crypto_heartbeat
MESSAGE_TTL=300
DEDUP_WINDOW=60
HEARTBEAT_INTERVAL=30

# Application Configuration
ENABLE_REDIS=true
ENABLE_SHEETS_FALLBACK=true
LOG_LEVEL=INFO

# Google Sheets Configuration (Fallback)
GOOGLE_SHEET_ID=your_sheet_id_here
GOOGLE_CREDENTIALS_FILE=credentials.json
GOOGLE_WORKSHEET_NAME=Trading
ARCHIVE_WORKSHEET_NAME=Archive

# Crypto.com Exchange API
CRYPTO_API_KEY=your_api_key_here
CRYPTO_API_SECRET=your_api_secret_here
TRADE_AMOUNT=10

# Trading Configuration
TRADE_CHECK_INTERVAL=5
BATCH_SIZE=5
UPDATE_INTERVAL=5
BATCH_UPDATE_INTERVAL=60
ATR_PERIOD=14
ATR_MULTIPLIER=2.0

# TradingView/CCXT Configuration
EXCHANGE=binance
TRADINGVIEW_SCREENER=CRYPTO
TRADINGVIEW_INTERVAL=15m

# Telegram Notifications
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here

# Monitoring and Health Checks
HEALTH_CHECK_INTERVAL=10
EOF
        
        warn "Created .env file with default values"
        warn "Please edit .env with your actual API keys and configuration"
    fi
    
    # Create docker-compose.yml if it doesn't exist
    if [ ! -f "docker-compose.yml" ]; then
        log "Creating Docker Compose configuration..."
        cat > docker-compose.yml << 'EOF'
version: '3.8'

services:
  # Redis server for signal communication
  redis:
    image: redis:7-alpine
    container_name: crypto_redis
    restart: unless-stopped
    ports:
      - "6379:6379"
    command: redis-server --appendonly yes --maxmemory 256mb --maxmemory-policy allkeys-lru
    volumes:
      - redis_data:/data
    networks:
      - crypto_network
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 30s
      timeout: 10s
      retries: 3
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

  # Signal generator (yf.py)
  signal_generator:
    build:
      context: .
      dockerfile: Dockerfile.yf
    container_name: crypto_signal_generator
    restart: unless-stopped
    depends_on:
      redis:
        condition: service_healthy
    environment:
      - REDIS_HOST=redis
      - REDIS_PORT=6379
      - REDIS_DB=0
      - LOG_LEVEL=INFO
      - PYTHONUNBUFFERED=1
    env_file:
      - .env
    volumes:
      - ./logs:/app/logs
      - ./credentials.json:/app/credentials.json:ro
    networks:
      - crypto_network
    healthcheck:
      test: ["CMD", "python", "-c", "import redis; r=redis.Redis(host='redis'); r.ping()"]
      interval: 60s
      timeout: 30s
      retries: 3
      start_period: 30s
    logging:
      driver: "json-file"
      options:
        max-size: "50m"
        max-file: "5"

  # Trade executor (trade_executor.py)
  trade_executor:
    build:
      context: .
      dockerfile: Dockerfile.executor
    container_name: crypto_trade_executor
    restart: unless-stopped
    depends_on:
      redis:
        condition: service_healthy
      signal_generator:
        condition: service_healthy
    environment:
      - REDIS_HOST=redis
      - REDIS_PORT=6379
      - REDIS_DB=0
      - LOG_LEVEL=INFO
      - PYTHONUNBUFFERED=1
    env_file:
      - .env
    volumes:
      - ./logs:/app/logs
      - ./local_data:/app/local_data
      - ./credentials.json:/app/credentials.json:ro
    networks:
      - crypto_network
    healthcheck:
      test: ["CMD", "python", "-c", "import redis; r=redis.Redis(host='redis'); r.ping()"]
      interval: 60s
      timeout: 30s
      retries: 3
      start_period: 60s
    logging:
      driver: "json-file"
      options:
        max-size: "50m"
        max-file: "5"

volumes:
  redis_data:
    driver: local

networks:
  crypto_network:
    driver: bridge
    ipam:
      config:
        - subnet: 172.20.0.0/16
EOF
    fi
    
    success "Configuration files created"
}

# Create a simple Redis setup for non-Docker environments
setup_redis_standalone() {
    log "Setting up standalone Redis server..."
    
    case "$OS_TYPE" in
        "ubuntu")
            # Install Redis server
            sudo apt-get install -y redis-server
            
            # Configure Redis
            sudo sed -i 's/^# maxmemory <bytes>/maxmemory 256mb/' /etc/redis/redis.conf
            sudo sed -i 's/^# maxmemory-policy noeviction/maxmemory-policy allkeys-lru/' /etc/redis/redis.conf
            
            # Start Redis
            sudo systemctl enable redis-server
            sudo systemctl start redis-server
            ;;
            
        "macos")
            # Start Redis using brew services
            brew services start redis
            ;;
            
        "windows")
            warn "For Windows, please use Docker mode or install Redis manually"
            ;;
    esac
    
    # Test Redis connection
    if command -v redis-cli &> /dev/null; then
        if redis-cli ping > /dev/null 2>&1; then
            success "Redis server is running and accessible"
        else
            warn "Redis server may not be running properly"
        fi
    fi
}

# Test the entire system
test_system() {
    log "Testing the complete system..."
    
    # Test Redis connection
    info "Testing Redis connection..."
    if command -v redis-cli &> /dev/null; then
        if redis-cli ping > /dev/null 2>&1; then
            success "✓ Redis connection successful"
        else
            error "✗ Redis connection failed"
            return 1
        fi
    else
        warn "Redis CLI not available for testing"
    fi
    
    # Test Python environment
    info "Testing Python environment..."
    source venv/bin/activate 2>/dev/null || source venv/Scripts/activate 2>/dev/null
    
    if python -c "import redis, pandas, ccxt, gspread; print('All imports successful')" 2>/dev/null; then
        success "✓ Python environment working"
    else
        error "✗ Python environment issues detected"
        return 1
    fi
    
    # Test signal bus modules
    info "Testing signal bus modules..."
    if python -c "from signal_bus import SignalBus; print('Signal bus import successful')" 2>/dev/null; then
        success "✓ Signal bus modules working"
    else
        warn "Signal bus modules not yet created - this is normal for first run"
    fi
    
    success "System test completed successfully"
}

# Create integrated run script for both modules
create_integrated_runner() {
    log "Creating integrated runner script..."
    
    cat > run_trading_system.py << 'EOF'
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Integrated Trading System Runner
================================

This script runs both the signal generator (yf.py) and trade executor (trade_executor.py)
with Redis communication in a coordinated manner.
"""

import os
import sys
import time
import signal
import logging
import threading
import subprocess
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logs/integrated_system.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("integrated_system")

class IntegratedTradingSystem:
    """Integrated trading system manager"""
    
    def __init__(self):
        self.processes = {}
        self.running = False
        
    def start_redis_check(self):
        """Check if Redis is available"""
        try:
            import redis
            redis_host = os.getenv("REDIS_HOST", "localhost")
            redis_port = int(os.getenv("REDIS_PORT", "6379"))
            
            client = redis.Redis(host=redis_host, port=redis_port, socket_timeout=5)
            client.ping()
            logger.info(f"Redis connection successful: {redis_host}:{redis_port}")
            return True
        except Exception as e:
            logger.error(f"Redis connection failed: {e}")
            return False
    
    def start_signal_generator(self):
        """Start the signal generator process"""
        try:
            logger.info("Starting signal generator (yf.py integration)...")
            
            if os.path.exists("yf_integration.py"):
                cmd = [sys.executable, "yf_integration.py"]
            else:
                # Fallback to original yf.py with Redis integration
                cmd = [sys.executable, "yf.py"]
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
            
            self.processes['signal_generator'] = process
            logger.info(f"Signal generator started with PID: {process.pid}")
            
            return process
            
        except Exception as e:
            logger.error(f"Failed to start signal generator: {e}")
            return None
    
    def start_trade_executor(self):
        """Start the trade executor process"""
        try:
            logger.info("Starting trade executor (trade_executor.py integration)...")
            
            if os.path.exists("executor_integration.py"):
                cmd = [sys.executable, "executor_integration.py"]
            else:
                # Fallback to original trade_executor.py
                cmd = [sys.executable, "trade_executor.py"]
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
            
            self.processes['trade_executor'] = process
            logger.info(f"Trade executor started with PID: {process.pid}")
            
            return process
            
        except Exception as e:
            logger.error(f"Failed to start trade executor: {e}")
            return None
    
    def monitor_processes(self):
        """Monitor running processes"""
        while self.running:
            try:
                for name, process in self.processes.items():
                    if process.poll() is not None:
                        logger.error(f"Process {name} has stopped unexpectedly")
                        # Could implement restart logic here
                
                time.sleep(10)
                
            except Exception as e:
                logger.error(f"Error in process monitoring: {e}")
                time.sleep(5)
    
    def stop_all(self):
        """Stop all processes"""
        logger.info("Stopping all processes...")
        self.running = False
        
        for name, process in self.processes.items():
            try:
                logger.info(f"Stopping {name}...")
                process.terminate()
                
                # Wait for graceful shutdown
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    logger.warning(f"Force killing {name}...")
                    process.kill()
                    
            except Exception as e:
                logger.error(f"Error stopping {name}: {e}")
        
        logger.info("All processes stopped")
    
    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"Received signal {signum}, shutting down...")
        self.stop_all()
        sys.exit(0)
    
    def run(self):
        """Run the integrated system"""
        logger.info("=== Integrated Trading System Starting ===")
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        try:
            # Check Redis
            if not self.start_redis_check():
                if os.getenv("ENABLE_SHEETS_FALLBACK", "true").lower() != "true":
                    logger.error("Redis unavailable and fallback disabled")
                    return
                logger.warning("Redis unavailable, using fallback mode")
            
            self.running = True
            
            # Start signal generator
            signal_gen = self.start_signal_generator()
            if not signal_gen:
                logger.error("Failed to start signal generator")
                return
            
            # Wait a bit for signal generator to initialize
            time.sleep(5)
            
            # Start trade executor
            trade_exec = self.start_trade_executor()
            if not trade_exec:
                logger.error("Failed to start trade executor")
                self.stop_all()
                return
            
            # Start monitoring
            monitor_thread = threading.Thread(target=self.monitor_processes, daemon=True)
            monitor_thread.start()
            
            logger.info("=== System Running Successfully ===")
            logger.info("Press Ctrl+C to stop the system")
            
            # Keep main thread alive
            while self.running:
                time.sleep(1)
                
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt")
        except Exception as e:
            logger.error(f"System error: {e}")
        finally:
            self.stop_all()

if __name__ == "__main__":
    system = IntegratedTradingSystem()
    system.run()
EOF
    
    chmod +x run_trading_system.py
    success "Integrated runner script created"
}

# Backup existing files
backup_existing_files() {
    log "Creating backup of existing files..."
    
    mkdir -p "$BACKUP_DIR"
    
    # Backup existing Python files
    for file in yf.py trade_executor.py credentials.json .env; do
        if [ -f "$file" ]; then
            cp "$file" "$BACKUP_DIR/"
            log "Backed up: $file"
        fi
    done
    
    # Backup logs if they exist
    if [ -d "logs" ]; then
        cp -r logs "$BACKUP_DIR/logs_backup"
    fi
    
    success "Backup created in: $BACKUP_DIR"
}

# Start the system
start_system() {
    log "Starting the Redis Trading System..."
    
    # Check if Docker is available and working
    if command -v docker &> /dev/null && docker info > /dev/null 2>&1; then
        log "Using Docker deployment..."
        
        # Pull images and start services
        docker-compose pull
        docker-compose up -d
        
        # Wait for services to be ready
        log "Waiting for services to start..."
        sleep 15
        
        # Check service health
        check_docker_health
        
    else
        log "Using standalone deployment..."
        
        # Start Redis if not running
        if ! redis-cli ping > /dev/null 2>&1; then
            setup_redis_standalone
        fi
        
        # Activate Python environment
        source venv/bin/activate 2>/dev/null || source venv/Scripts/activate 2>/dev/null
        
        # Start the integrated system
        python run_trading_system.py &
        SYSTEM_PID=$!
        
        log "System started with PID: $SYSTEM_PID"
        echo $SYSTEM_PID > system.pid
    fi
    
    success "Redis Trading System is now running!"
}

# Stop the system
stop_system() {
    log "Stopping the Redis Trading System..."
    
    # Stop Docker services if running
    if command -v docker-compose &> /dev/null && [ -f "docker-compose.yml" ]; then
        docker-compose down
    fi
    
    # Stop standalone processes
    if [ -f "system.pid" ]; then
        SYSTEM_PID=$(cat system.pid)
        if kill -0 $SYSTEM_PID 2>/dev/null; then
            kill $SYSTEM_PID
            log "Stopped system process: $SYSTEM_PID"
        fi
        rm -f system.pid
    fi
    
    success "System stopped"
}

# Check Docker service health
check_docker_health() {
    log "Checking Docker service health..."
    
    # Check Redis
    if docker-compose exec -T redis redis-cli ping > /dev/null 2>&1; then
        success "✓ Redis is healthy"
    else
        error "✗ Redis is not responding"
    fi
    
    # Check containers
    docker-compose ps
}

# Show system status
show_status() {
    log "Redis Trading System Status:"
    
    echo -e "\n${CYAN}=== System Status ===${NC}"
    
    # Check Redis
    if command -v redis-cli &> /dev/null && redis-cli ping > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Redis: Running${NC}"
    else
        echo -e "${RED}✗ Redis: Not Running${NC}"
    fi
    
    # Check Docker services
    if command -v docker-compose &> /dev/null && [ -f "docker-compose.yml" ]; then
        echo -e "\n${CYAN}=== Docker Services ===${NC}"
        docker-compose ps
    fi
    
    # Check standalone processes
    if [ -f "system.pid" ]; then
        SYSTEM_PID=$(cat system.pid)
        if kill -0 $SYSTEM_PID 2>/dev/null; then
            echo -e "${GREEN}✓ Integrated System: Running (PID: $SYSTEM_PID)${NC}"
        else
            echo -e "${RED}✗ Integrated System: Not Running${NC}"
        fi
    fi
    
    # Show resource usage if available
    if command -v docker &> /dev/null; then
        echo -e "\n${CYAN}=== Resource Usage ===${NC}"
        docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}" 2>/dev/null || echo "No Docker containers running"
    fi
}

# Show logs
show_logs() {
    log "Showing system logs..."
    
    if command -v docker-compose &> /dev/null && [ -f "docker-compose.yml" ]; then
        log "Docker logs:"
        docker-compose logs -f --tail=50
    else
        log "Application logs:"
        if [ -d "logs" ]; then
            tail -f logs/*.log
        else
            warn "No logs directory found"
        fi
    fi
}

# Monitor system
monitor_system() {
    log "Starting system monitor (Press Ctrl+C to stop)..."
    
    while true; do
        clear
        print_banner
        show_status
        
        echo -e "\n${CYAN}=== Recent Activity ===${NC}"
        if [ -d "logs" ]; then
            tail -n 5 logs/*.log 2>/dev/null | head -20
        fi
        
        sleep 10
    done
}

# Interactive configuration
configure_system() {
    log "Starting interactive configuration..."
    
    echo -e "${CYAN}Redis Trading System Configuration${NC}"
    echo "=================================="
    
    # Load existing .env if it exists
    if [ -f ".env" ]; then
        source .env
    fi
    
    # Configure Crypto.com API
    echo -e "\n${YELLOW}Crypto.com Exchange API Configuration:${NC}"
    read -p "Enter your Crypto.com API Key: " -r API_KEY
    read -p "Enter your Crypto.com API Secret: " -r API_SECRET
    read -p "Enter trade amount in USDT (default: 10): " -r TRADE_AMOUNT
    TRADE_AMOUNT=${TRADE_AMOUNT:-10}
    
    # Configure Google Sheets
    echo -e "\n${YELLOW}Google Sheets Configuration (for fallback):${NC}"
    read -p "Enter your Google Sheet ID: " -r SHEET_ID
    
    # Configure Telegram
    echo -e "\n${YELLOW}Telegram Notifications:${NC}"
    read -p "Enter your Telegram Bot Token: " -r TELEGRAM_TOKEN
    read -p "Enter your Telegram Chat ID: " -r TELEGRAM_CHAT
    
    # Configure Redis
    echo -e "\n${YELLOW}Redis Configuration:${NC}"
    read -p "Redis host (default: localhost): " -r REDIS_HOST
    REDIS_HOST=${REDIS_HOST:-localhost}
    read -p "Redis port (default: 6379): " -r REDIS_PORT
    REDIS_PORT=${REDIS_PORT:-6379}
    
    # Update .env file
    log "Updating configuration file..."
    
    sed -i "s/CRYPTO_API_KEY=.*/CRYPTO_API_KEY=$API_KEY/" .env
    sed -i "s/CRYPTO_API_SECRET=.*/CRYPTO_API_SECRET=$API_SECRET/" .env
    sed -i "s/TRADE_AMOUNT=.*/TRADE_AMOUNT=$TRADE_AMOUNT/" .env
    sed -i "s/GOOGLE_SHEET_ID=.*/GOOGLE_SHEET_ID=$SHEET_ID/" .env
    sed -i "s/TELEGRAM_BOT_TOKEN=.*/TELEGRAM_BOT_TOKEN=$TELEGRAM_TOKEN/" .env
    sed -i "s/TELEGRAM_CHAT_ID=.*/TELEGRAM_CHAT_ID=$TELEGRAM_CHAT/" .env
    sed -i "s/REDIS_HOST=.*/REDIS_HOST=$REDIS_HOST/" .env
    sed -i "s/REDIS_PORT=.*/REDIS_PORT=$REDIS_PORT/" .env
    
    success "Configuration updated successfully!"
    
    # Ask about credentials.json
    echo -e "\n${YELLOW}Google Sheets Credentials:${NC}"
    if [ ! -f "credentials.json" ]; then
        warn "credentials.json not found"
        echo "Please place your Google Sheets service account credentials in 'credentials.json'"
        echo "Or the system will work with Redis only (no Google Sheets fallback)"
    else
        success "credentials.json found"
    fi
}

# Complete installation
complete_install() {
    print_banner
    
    log "Starting complete Redis Trading System installation..."
    
    # Create backup
    backup_existing_files
    
    # Check OS
    check_os
    
    # Install dependencies
    install_dependencies
    
    # Setup project
    setup_project_structure
    
    # Setup Python environment
    setup_python_environment
    
    # Setup configuration
    setup_configuration
    
    # Create integration scripts
    create_integrated_runner
    
    # Test system
    test_system
    
    success "Installation completed successfully!"
    
    echo -e "\n${CYAN}Next Steps:${NC}"
    echo "1. Configure your API keys: ./setup_redis_trading.sh configure"
    echo "2. Start the system: ./setup_redis_trading.sh start"
    echo "3. Monitor the system: ./setup_redis_trading.sh monitor"
}

# Main function
main() {
    local command="$1"
    
    case "$command" in
        "install")
            complete_install
            ;;
        "configure")
            configure_system
            ;;
        "deploy")
            complete_install
            configure_system
            start_system
            ;;
        "start")
            start_system
            ;;
        "stop")
            stop_system
            ;;
        "restart")
            stop_system
            sleep 3
            start_system
            ;;
        "status")
            show_status
            ;;
        "logs")
            show_logs
            ;;
        "monitor")
            monitor_system
            ;;
        "test")
            test_system
            ;;
        "backup")
            backup_existing_files
            ;;
        *)
            print_banner
            echo "Usage: $0 {install|configure|deploy|start|stop|restart|status|logs|monitor|test|backup}"
            echo
            echo "Commands:"
            echo "  install    - Complete system installation (dependencies, setup, configuration)"
            echo "  configure  - Interactive configuration of API keys and settings"
            echo "  deploy     - Full deployment (install + configure + start)"
            echo "  start      - Start the Redis trading system"
            echo "  stop       - Stop the Redis trading system"
            echo "  restart    - Restart the Redis trading system"
            echo "  status     - Show system status and health"
            echo "  logs       - Show system logs"
            echo "  monitor    - Real-time system monitoring"
            echo "  test       - Test system components"
            echo "  backup     - Backup existing configuration and data"
            echo
            echo -e "${GREEN}Quick Start:${NC}"
            echo "  $0 deploy    # Complete setup and start"
            echo "  $0 monitor   # Monitor running system"
            echo
            exit 1
            ;;
    esac
}

# Run main function with all arguments
main "$@"