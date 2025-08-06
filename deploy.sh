#!/bin/bash

# Deploy Script for Redis Signal Bus System
# ==========================================
# 
# This script deploys the crypto trading system with Redis communication
# on an Ubuntu 22.04+ EC2 instance.
#
# Usage: ./deploy.sh [start|stop|restart|logs|status]

set -e

# Configuration
PROJECT_NAME="crypto-trading-redis"
COMPOSE_FILE="docker-compose.yml"
ENV_FILE=".env"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging function
log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] WARNING:${NC} $1"
}

error() {
    echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] ERROR:${NC} $1"
}

# Check if Docker is installed
check_docker() {
    if ! command -v docker &> /dev/null; then
        error "Docker is not installed. Please install Docker first."
        exit 1
    fi
    
    if ! command -v docker-compose &> /dev/null; then
        error "Docker Compose is not installed. Please install Docker Compose first."
        exit 1
    fi
    
    log "Docker and Docker Compose are available"
}

# Check if environment file exists
check_environment() {
    if [ ! -f "$ENV_FILE" ]; then
        warn "Environment file $ENV_FILE not found"
        if [ -f ".env.example" ]; then
            log "Creating $ENV_FILE from .env.example"
            cp .env.example "$ENV_FILE"
            warn "Please edit $ENV_FILE with your actual configuration before running"
            return 1
        else
            error "No environment configuration found"
            return 1
        fi
    fi
    
    log "Environment file $ENV_FILE found"
    
    # Check critical environment variables
    local missing_vars=()
    
    if ! grep -q "CRYPTO_API_KEY=" "$ENV_FILE" || grep -q "CRYPTO_API_KEY=your_api_key_here" "$ENV_FILE"; then
        missing_vars+=("CRYPTO_API_KEY")
    fi
    
    if ! grep -q "CRYPTO_API_SECRET=" "$ENV_FILE" || grep -q "CRYPTO_API_SECRET=your_api_secret_here" "$ENV_FILE"; then
        missing_vars+=("CRYPTO_API_SECRET")
    fi
    
    if ! grep -q "GOOGLE_SHEET_ID=" "$ENV_FILE" || grep -q "GOOGLE_SHEET_ID=your_sheet_id_here" "$ENV_FILE"; then
        missing_vars+=("GOOGLE_SHEET_ID")
    fi
    
    if [ ${#missing_vars[@]} -gt 0 ]; then
        error "Missing required environment variables: ${missing_vars[*]}"
        error "Please update $ENV_FILE with your actual configuration"
        return 1
    fi
    
    return 0
}

# Check if credentials file exists
check_credentials() {
    if [ ! -f "credentials.json" ]; then
        warn "Google Sheets credentials file 'credentials.json' not found"
        warn "Google Sheets fallback will not work without this file"
        return 1
    fi
    
    log "Google Sheets credentials file found"
    return 0
}

# Create necessary directories
setup_directories() {
    log "Creating necessary directories..."
    mkdir -p logs
    mkdir -p local_data
    chmod 755 logs local_data
    log "Directories created successfully"
}

# Start the services
start_services() {
    log "Starting crypto trading system..."
    
    # Pull latest images
    docker-compose -f "$COMPOSE_FILE" pull
    
    # Start services
    docker-compose -f "$COMPOSE_FILE" up -d
    
    log "Services started. Waiting for health checks..."
    sleep 10
    
    # Check service health
    check_service_health
}

# Stop the services
stop_services() {
    log "Stopping crypto trading system..."
    docker-compose -f "$COMPOSE_FILE" down
    log "Services stopped"
}

# Restart the services
restart_services() {
    log "Restarting crypto trading system..."
    stop_services
    sleep 5
    start_services
}

# Check service health
check_service_health() {
    log "Checking service health..."
    
    # Check Redis
    if docker-compose -f "$COMPOSE_FILE" exec -T redis redis-cli ping > /dev/null 2>&1; then
        log "✓ Redis is healthy"
    else
        error "✗ Redis is not responding"
    fi
    
    # Check signal generator
    if docker-compose -f "$COMPOSE_FILE" ps signal_generator | grep -q "Up"; then
        log "✓ Signal Generator is running"
    else
        warn "✗ Signal Generator is not running"
    fi
    
    # Check trade executor
    if docker-compose -f "$COMPOSE_FILE" ps trade_executor | grep -q "Up"; then
        log "✓ Trade Executor is running"
    else
        warn "✗ Trade Executor is not running"
    fi
}

# Show service status
show_status() {
    log "Service Status:"
    docker-compose -f "$COMPOSE_FILE" ps
    
    echo
    log "Resource Usage:"
    docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}"
}

# Show logs
show_logs() {
    local service="$1"
    if [ -n "$service" ]; then
        log "Showing logs for $service..."
        docker-compose -f "$COMPOSE_FILE" logs -f "$service"
    else
        log "Showing logs for all services..."
        docker-compose -f "$COMPOSE_FILE" logs -f
    fi
}

# Monitor the system
monitor_system() {
    log "Starting system monitor (Press Ctrl+C to stop)..."
    
    while true; do
        clear
        echo "=== Crypto Trading System Monitor ==="
        echo "Time: $(date)"
        echo
        
        show_status
        
        echo
        log "Recent log entries:"
        docker-compose -f "$COMPOSE_FILE" logs --tail=10 signal_generator trade_executor
        
        sleep 30
    done
}

# Update the system
update_system() {
    log "Updating crypto trading system..."
    
    # Pull latest changes (if using git)
    if [ -d ".git" ]; then
        log "Pulling latest code changes..."
        git pull
    fi
    
    # Rebuild containers
    log "Rebuilding containers..."
    docker-compose -f "$COMPOSE_FILE" build --no-cache
    
    # Restart services
    restart_services
}

# Backup configuration and data
backup_system() {
    local backup_dir="backup_$(date +%Y%m%d_%H%M%S)"
    log "Creating backup in $backup_dir..."
    
    mkdir -p "$backup_dir"
    
    # Backup configuration
    cp "$ENV_FILE" "$backup_dir/"
    cp "credentials.json" "$backup_dir/" 2>/dev/null || warn "credentials.json not found"
    
    # Backup logs
    cp -r logs "$backup_dir/" 2>/dev/null || warn "logs directory not found"
    
    # Backup local data
    cp -r local_data "$backup_dir/" 2>/dev/null || warn "local_data directory not found"
    
    # Create archive
    tar -czf "${backup_dir}.tar.gz" "$backup_dir"
    rm -rf "$backup_dir"
    
    log "Backup created: ${backup_dir}.tar.gz"
}

# Install system dependencies
install_dependencies() {
    log "Installing system dependencies..."
    
    # Update package list
    sudo apt-get update
    
    # Install Docker if not present
    if ! command -v docker &> /dev/null; then
        log "Installing Docker..."
        sudo apt-get install -y apt-transport-https ca-certificates curl gnupg lsb-release
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
        echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
        sudo apt-get update
        sudo apt-get install -y docker-ce docker-ce-cli containerd.io
        sudo usermod -aG docker $USER
        log "Docker installed. Please log out and log back in for group changes to take effect."
    fi
    
    # Install Docker Compose if not present
    if ! command -v docker-compose &> /dev/null; then
        log "Installing Docker Compose..."
        sudo curl -L "https://github.com/docker/compose/releases/download/v2.20.0/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
        sudo chmod +x /usr/local/bin/docker-compose
    fi
    
    log "Dependencies installed successfully"
}

# Main function
main() {
    local command="$1"
    local service="$2"
    
    case "$command" in
        "install")
            install_dependencies
            ;;
        "start")
            check_docker
            if check_environment && check_credentials; then
                setup_directories
                start_services
            else
                error "Pre-flight checks failed. Please fix the issues above."
                exit 1
            fi
            ;;
        "stop")
            check_docker
            stop_services
            ;;
        "restart")
            check_docker
            restart_services
            ;;
        "status")
            check_docker
            show_status
            ;;
        "health")
            check_docker
            check_service_health
            ;;
        "logs")
            check_docker
            show_logs "$service"
            ;;
        "monitor")
            check_docker
            monitor_system
            ;;
        "update")
            check_docker
            update_system
            ;;
        "backup")
            backup_system
            ;;
        *)
            echo "Usage: $0 {install|start|stop|restart|status|health|logs|monitor|update|backup} [service]"
            echo
            echo "Commands:"
            echo "  install  - Install system dependencies (Docker, Docker Compose)"
            echo "  start    - Start the crypto trading system"
            echo "  stop     - Stop the crypto trading system"
            echo "  restart  - Restart the crypto trading system"
            echo "  status   - Show service status and resource usage"
            echo "  health   - Check service health"
            echo "  logs     - Show logs (optionally for specific service)"
            echo "  monitor  - Start real-time system monitor"
            echo "  update   - Update and rebuild the system"
            echo "  backup   - Create backup of configuration and data"
            echo
            echo "Services: redis, signal_generator, trade_executor"
            exit 1
            ;;
    esac
}

# Run main function with all arguments
main "$@"