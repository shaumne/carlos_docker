#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Signal Bus - Production-Ready Redis Communication Layer
==================================================================

This module provides a robust, low-latency communication layer between
yf.py (signal generator) and trade_executor.py (signal consumer) using Redis pub/sub.

Features:
- Redis pub/sub for real-time signal delivery
- Automatic reconnection with exponential backoff
- Message deduplication and ordering
- Comprehensive logging and monitoring
- Ubuntu/EC2 compatible
- Health checks and failover mechanisms
"""

import os
import json
import time
import uuid
import logging
import threading
from typing import Dict, Any, Optional, Callable, List
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from enum import Enum
import hashlib

# Redis imports with fallback
try:
    import redis
    import redis.sentinel
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    print("WARNING: Redis not installed. Install with: pip install redis")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("signal_bus.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("signal_bus")


class SignalType(Enum):
    """Enumeration for signal types"""
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"
    HEARTBEAT = "HEARTBEAT"
    STATUS_UPDATE = "STATUS_UPDATE"


@dataclass
class TradingSignal:
    """Structure for trading signals"""
    signal_id: str
    signal_type: SignalType
    symbol: str
    timestamp: str
    data: Dict[str, Any]
    source: str = "yf.py"
    version: str = "1.0"
    
    def to_json(self) -> str:
        """Convert signal to JSON string"""
        signal_dict = asdict(self)
        signal_dict['signal_type'] = self.signal_type.value
        return json.dumps(signal_dict, ensure_ascii=False)
    
    @classmethod
    def from_json(cls, json_str: str) -> 'TradingSignal':
        """Create signal from JSON string"""
        data = json.loads(json_str)
        data['signal_type'] = SignalType(data['signal_type'])
        return cls(**data)
    
    def get_hash(self) -> str:
        """Generate hash for deduplication"""
        content = f"{self.signal_type.value}:{self.symbol}:{self.data.get('action', '')}:{self.data.get('last_price', 0)}"
        return hashlib.md5(content.encode()).hexdigest()


class ConnectionState(Enum):
    """Redis connection states"""
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING" 
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    FAILED = "FAILED"


class SignalBusConfig:
    """Configuration for Signal Bus"""
    
    def __init__(self):
        # Redis configuration
        self.redis_host = os.getenv("REDIS_HOST", "localhost")
        self.redis_port = int(os.getenv("REDIS_PORT", "6379"))
        self.redis_password = os.getenv("REDIS_PASSWORD", None)
        self.redis_db = int(os.getenv("REDIS_DB", "0"))
        
        # Channel configuration
        self.signal_channel = os.getenv("SIGNAL_CHANNEL", "crypto_signals")
        self.status_channel = os.getenv("STATUS_CHANNEL", "crypto_status")
        self.heartbeat_channel = os.getenv("HEARTBEAT_CHANNEL", "crypto_heartbeat")
        
        # Connection settings
        self.connection_timeout = int(os.getenv("REDIS_TIMEOUT", "5"))
        self.max_retries = int(os.getenv("REDIS_MAX_RETRIES", "10"))
        self.retry_delay = float(os.getenv("REDIS_RETRY_DELAY", "1.0"))
        self.max_retry_delay = float(os.getenv("REDIS_MAX_RETRY_DELAY", "60.0"))
        
        # Message settings
        self.message_ttl = int(os.getenv("MESSAGE_TTL", "300"))  # 5 minutes
        self.dedup_window = int(os.getenv("DEDUP_WINDOW", "60"))  # 1 minute
        self.heartbeat_interval = int(os.getenv("HEARTBEAT_INTERVAL", "30"))  # 30 seconds
        
        # Health check settings
        self.health_check_interval = int(os.getenv("HEALTH_CHECK_INTERVAL", "10"))  # 10 seconds
        
        logger.info(f"SignalBus configured: {self.redis_host}:{self.redis_port}, DB: {self.redis_db}")


class SignalBus:
    """Main Signal Bus class for Redis communication"""
    
    def __init__(self, config: Optional[SignalBusConfig] = None):
        if not REDIS_AVAILABLE:
            raise RuntimeError("Redis is not available. Install with: pip install redis")
            
        self.config = config or SignalBusConfig()
        self.redis_client = None
        self.pubsub = None
        self.connection_state = ConnectionState.DISCONNECTED
        
        # Threading
        self.subscriber_thread = None
        self.heartbeat_thread = None
        self.health_check_thread = None
        self.running = False
        
        # Message handling
        self.message_handlers: Dict[SignalType, List[Callable]] = {
            signal_type: [] for signal_type in SignalType
        }
        self.sent_messages_cache = {}  # For deduplication
        self.received_messages_cache = {}  # For deduplication
        
        # Statistics
        self.stats = {
            'messages_sent': 0,
            'messages_received': 0,
            'messages_dropped': 0,
            'reconnections': 0,
            'last_heartbeat': None,
            'connection_uptime': 0
        }
        
        # Connection tracking
        self.connection_start_time = None
        self.last_successful_operation = None
        
        logger.info("SignalBus initialized")
    
    def connect(self) -> bool:
        """Establish Redis connection with retries"""
        if self.connection_state == ConnectionState.CONNECTED:
            return True
            
        self.connection_state = ConnectionState.CONNECTING
        retry_count = 0
        retry_delay = self.config.retry_delay
        
        while retry_count < self.config.max_retries:
            try:
                # Create Redis client
                self.redis_client = redis.Redis(
                    host=self.config.redis_host,
                    port=self.config.redis_port,
                    password=self.config.redis_password,
                    db=self.config.redis_db,
                    socket_timeout=self.config.connection_timeout,
                    socket_connect_timeout=self.config.connection_timeout,
                    retry_on_timeout=True,
                    decode_responses=True
                )
                
                # Test connection
                self.redis_client.ping()
                
                # Create pubsub
                self.pubsub = self.redis_client.pubsub()
                
                self.connection_state = ConnectionState.CONNECTED
                self.connection_start_time = time.time()
                self.last_successful_operation = time.time()
                
                if retry_count > 0:
                    self.stats['reconnections'] += 1
                    
                logger.info(f"Connected to Redis at {self.config.redis_host}:{self.config.redis_port}")
                return True
                
            except Exception as e:
                retry_count += 1
                logger.error(f"Redis connection attempt {retry_count} failed: {str(e)}")
                
                if retry_count < self.config.max_retries:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, self.config.max_retry_delay)
                else:
                    self.connection_state = ConnectionState.FAILED
                    logger.error("Max retries exceeded. Redis connection failed.")
                    
        return False
    
    def disconnect(self):
        """Disconnect from Redis"""
        logger.info("Disconnecting from Redis...")
        self.running = False
        
        if self.pubsub:
            try:
                self.pubsub.close()
            except:
                pass
                
        if self.redis_client:
            try:
                self.redis_client.close()
            except:
                pass
                
        self.connection_state = ConnectionState.DISCONNECTED
        logger.info("Disconnected from Redis")
    
    def _ensure_connection(self) -> bool:
        """Ensure Redis connection is active"""
        if self.connection_state != ConnectionState.CONNECTED:
            return self.connect()
            
        try:
            self.redis_client.ping()
            self.last_successful_operation = time.time()
            return True
        except:
            logger.warning("Redis connection lost, attempting to reconnect...")
            self.connection_state = ConnectionState.RECONNECTING
            return self.connect()
    
    def publish_signal(self, signal: TradingSignal) -> bool:
        """Publish a trading signal"""
        if not self._ensure_connection():
            logger.error("Cannot publish signal: Redis not connected")
            return False
            
        try:
            # Check for duplicate signals
            signal_hash = signal.get_hash()
            current_time = time.time()
            
            # Clean old cache entries
            self._clean_cache(self.sent_messages_cache, current_time)
            
            # Check if we already sent this signal recently
            if signal_hash in self.sent_messages_cache:
                last_sent_time = self.sent_messages_cache[signal_hash]
                if current_time - last_sent_time < self.config.dedup_window:
                    logger.debug(f"Skipping duplicate signal for {signal.symbol}")
                    return True
            
            # Publish signal
            message = signal.to_json()
            result = self.redis_client.publish(self.config.signal_channel, message)
            
            if result > 0:
                self.sent_messages_cache[signal_hash] = current_time
                self.stats['messages_sent'] += 1
                self.last_successful_operation = time.time()
                logger.info(f"Published {signal.signal_type.value} signal for {signal.symbol}")
                return True
            else:
                logger.warning(f"No subscribers for signal channel")
                return False
                
        except Exception as e:
            logger.error(f"Error publishing signal: {str(e)}")
            self.connection_state = ConnectionState.RECONNECTING
            return False
    
    def subscribe_to_signals(self, signal_types: Optional[List[SignalType]] = None):
        """Subscribe to trading signals"""
        if not signal_types:
            signal_types = [SignalType.BUY, SignalType.SELL, SignalType.STATUS_UPDATE]
            
        if not self._ensure_connection():
            logger.error("Cannot subscribe: Redis not connected")
            return False
            
        try:
            self.pubsub.subscribe(self.config.signal_channel)
            self.pubsub.subscribe(self.config.heartbeat_channel)
            
            logger.info(f"Subscribed to signals: {[st.value for st in signal_types]}")
            return True
            
        except Exception as e:
            logger.error(f"Error subscribing to signals: {str(e)}")
            return False
    
    def add_signal_handler(self, signal_type: SignalType, handler: Callable[[TradingSignal], None]):
        """Add handler for specific signal type"""
        self.message_handlers[signal_type].append(handler)
        logger.info(f"Added handler for {signal_type.value} signals")
    
    def _clean_cache(self, cache: Dict[str, float], current_time: float):
        """Clean old cache entries"""
        expired_keys = [
            key for key, timestamp in cache.items()
            if current_time - timestamp > self.config.dedup_window
        ]
        for key in expired_keys:
            del cache[key]
    
    def _handle_message(self, message):
        """Handle incoming Redis message"""
        try:
            if message['type'] != 'message':
                return
                
            signal = TradingSignal.from_json(message['data'])
            current_time = time.time()
            
            # Check for duplicate messages
            signal_hash = signal.get_hash()
            self._clean_cache(self.received_messages_cache, current_time)
            
            if signal_hash in self.received_messages_cache:
                last_received_time = self.received_messages_cache[signal_hash]
                if current_time - last_received_time < self.config.dedup_window:
                    logger.debug(f"Skipping duplicate received signal for {signal.symbol}")
                    return
            
            self.received_messages_cache[signal_hash] = current_time
            self.stats['messages_received'] += 1
            self.last_successful_operation = time.time()
            
            # Call handlers
            handlers = self.message_handlers.get(signal.signal_type, [])
            for handler in handlers:
                try:
                    handler(signal)
                except Exception as e:
                    logger.error(f"Error in signal handler: {str(e)}")
            
            logger.debug(f"Processed {signal.signal_type.value} signal for {signal.symbol}")
            
        except Exception as e:
            logger.error(f"Error handling message: {str(e)}")
            self.stats['messages_dropped'] += 1
    
    def _subscriber_worker(self):
        """Background worker for message subscription"""
        logger.info("Signal subscriber started")
        
        while self.running:
            try:
                if not self._ensure_connection():
                    time.sleep(1)
                    continue
                    
                # Listen for messages with timeout
                message = self.pubsub.get_message(timeout=1.0)
                if message:
                    self._handle_message(message)
                    
            except Exception as e:
                logger.error(f"Error in subscriber worker: {str(e)}")
                time.sleep(1)
        
        logger.info("Signal subscriber stopped")
    
    def _heartbeat_worker(self):
        """Background worker for heartbeat messages"""
        logger.info("Heartbeat worker started")
        
        while self.running:
            try:
                if self._ensure_connection():
                    heartbeat_signal = TradingSignal(
                        signal_id=str(uuid.uuid4()),
                        signal_type=SignalType.HEARTBEAT,
                        symbol="SYSTEM",
                        timestamp=datetime.now().isoformat(),
                        data={"status": "alive", "stats": self.stats.copy()},
                        source=f"signal_bus_{os.getpid()}"
                    )
                    
                    self.redis_client.publish(self.config.heartbeat_channel, heartbeat_signal.to_json())
                    self.stats['last_heartbeat'] = time.time()
                    
                time.sleep(self.config.heartbeat_interval)
                
            except Exception as e:
                logger.error(f"Error in heartbeat worker: {str(e)}")
                time.sleep(self.config.heartbeat_interval)
        
        logger.info("Heartbeat worker stopped")
    
    def _health_check_worker(self):
        """Background worker for health checks"""
        logger.info("Health check worker started")
        
        while self.running:
            try:
                current_time = time.time()
                
                # Update connection uptime
                if self.connection_start_time:
                    self.stats['connection_uptime'] = current_time - self.connection_start_time
                
                # Check if we should reconnect
                if (self.last_successful_operation and 
                    current_time - self.last_successful_operation > self.config.health_check_interval * 3):
                    logger.warning("No successful operations for a while, checking connection...")
                    self._ensure_connection()
                
                time.sleep(self.config.health_check_interval)
                
            except Exception as e:
                logger.error(f"Error in health check worker: {str(e)}")
                time.sleep(self.config.health_check_interval)
        
        logger.info("Health check worker stopped")
    
    def start(self):
        """Start the Signal Bus"""
        if self.running:
            logger.warning("SignalBus is already running")
            return
            
        logger.info("Starting SignalBus...")
        
        if not self.connect():
            raise RuntimeError("Failed to connect to Redis")
        
        self.running = True
        
        # Start background threads
        self.subscriber_thread = threading.Thread(target=self._subscriber_worker, daemon=True)
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_worker, daemon=True)
        self.health_check_thread = threading.Thread(target=self._health_check_worker, daemon=True)
        
        self.subscriber_thread.start()
        self.heartbeat_thread.start()
        self.health_check_thread.start()
        
        logger.info("SignalBus started successfully")
    
    def stop(self):
        """Stop the Signal Bus"""
        logger.info("Stopping SignalBus...")
        self.running = False
        
        # Wait for threads to finish
        for thread in [self.subscriber_thread, self.heartbeat_thread, self.health_check_thread]:
            if thread and thread.is_alive():
                thread.join(timeout=5)
        
        self.disconnect()
        logger.info("SignalBus stopped")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get Signal Bus statistics"""
        current_time = time.time()
        stats = self.stats.copy()
        stats['connection_state'] = self.connection_state.value
        stats['current_time'] = current_time
        
        if self.connection_start_time:
            stats['connection_uptime'] = current_time - self.connection_start_time
            
        return stats
    
    def is_healthy(self) -> bool:
        """Check if Signal Bus is healthy"""
        return (
            self.connection_state == ConnectionState.CONNECTED and
            self.running and
            (not self.last_successful_operation or 
             time.time() - self.last_successful_operation < self.config.health_check_interval * 2)
        )


# Convenience functions for easy integration
def create_signal_producer() -> SignalBus:
    """Create a Signal Bus instance for producing signals (yf.py)"""
    bus = SignalBus()
    return bus


def create_signal_consumer() -> SignalBus:
    """Create a Signal Bus instance for consuming signals (trade_executor.py)"""
    bus = SignalBus()
    return bus


# Example usage
if __name__ == "__main__":
    # Test the signal bus
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "producer":
        # Producer mode
        print("Starting signal producer test...")
        producer = create_signal_producer()
        producer.start()
        
        try:
            # Send test signals
            for i in range(5):
                signal = TradingSignal(
                    signal_id=str(uuid.uuid4()),
                    signal_type=SignalType.BUY,
                    symbol="BTC_USDT",
                    timestamp=datetime.now().isoformat(),
                    data={
                        "action": "BUY",
                        "last_price": 45000.0 + i,
                        "take_profit": 46000.0,
                        "stop_loss": 44000.0
                    }
                )
                
                producer.publish_signal(signal)
                time.sleep(2)
                
        except KeyboardInterrupt:
            pass
        finally:
            producer.stop()
            
    elif len(sys.argv) > 1 and sys.argv[1] == "consumer":
        # Consumer mode
        print("Starting signal consumer test...")
        consumer = create_signal_consumer()
        
        def handle_buy_signal(signal: TradingSignal):
            print(f"Received BUY signal: {signal.symbol} at {signal.data.get('last_price')}")
        
        consumer.add_signal_handler(SignalType.BUY, handle_buy_signal)
        consumer.start()
        consumer.subscribe_to_signals()
        
        try:
            while True:
                stats = consumer.get_stats()
                print(f"Stats: {stats}")
                time.sleep(10)
        except KeyboardInterrupt:
            pass
        finally:
            consumer.stop()
    else:
        print("Usage: python signal_bus.py [producer|consumer]")