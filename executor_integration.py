#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Executor Integration Script
===========================

This script integrates the existing trade_executor.py with the new Redis-based signal system.
It replaces Google Sheets polling with real-time Redis signal consumption while maintaining
backward compatibility and all existing trading logic.

Usage:
    python executor_integration.py

Environment Variables:
    REDIS_HOST - Redis server hostname (default: localhost)
    REDIS_PORT - Redis server port (default: 6379)
    REDIS_PASSWORD - Redis password (optional)
    ENABLE_SHEETS_FALLBACK - Enable Google Sheets fallback (default: true)
    LOG_LEVEL - Logging level (default: INFO)
"""

import os
import sys
import time
import logging
from datetime import datetime

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import existing modules
try:
    from trade_executor import GoogleSheetTradeManager, CryptoExchangeAPI, TelegramNotifier
except ImportError as e:
    print(f"Error importing trade_executor.py modules: {e}")
    print("Make sure trade_executor.py is in the same directory")
    sys.exit(1)

# Import new signal modules
try:
    from signal_consumer import TradeExecutorAdapter, SignalConsumer
    from signal_bus import SignalBusConfig
except ImportError as e:
    print(f"Error importing signal modules: {e}")
    print("Make sure signal_bus.py and signal_consumer.py are available")
    sys.exit(1)

# Configure logging
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logs/executor_integration.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("executor_integration")


class EnhancedTradeManager(GoogleSheetTradeManager):
    """
    Enhanced Trade Manager with Redis integration
    
    This class extends the original GoogleSheetTradeManager to use Redis for signal consumption
    while maintaining backward compatibility with Google Sheets and all existing trading logic.
    """
    
    def __init__(self):
        """Initialize the enhanced trade manager"""
        logger.info("Initializing Enhanced Trade Manager with Redis integration")
        
        # Check if Redis should be used
        enable_redis = os.getenv("ENABLE_REDIS", "true").lower() == "true"
        enable_sheets_fallback = os.getenv("ENABLE_SHEETS_FALLBACK", "true").lower() == "true"
        
        if enable_redis:
            try:
                # Initialize original trade manager for fallback
                original_manager = None
                if enable_sheets_fallback:
                    try:
                        # Call parent constructor for Google Sheets functionality
                        super().__init__()
                        original_manager = self
                        logger.info("Google Sheets fallback initialized")
                    except Exception as e:
                        logger.warning(f"Google Sheets fallback failed to initialize: {e}")
                
                # Create Redis-based adapter
                self.signal_adapter = TradeExecutorAdapter(original_manager)
                self.redis_enabled = True
                logger.info("Redis integration initialized successfully")
                
            except Exception as e:
                logger.error(f"Redis integration failed: {e}")
                if enable_sheets_fallback:
                    logger.info("Falling back to Google Sheets only")
                    super().__init__()
                    self.signal_adapter = None
                    self.redis_enabled = False
                else:
                    raise RuntimeError(f"Redis required but failed to initialize: {e}")
        else:
            # Use Google Sheets only
            logger.info("Using Google Sheets only (Redis disabled)")
            super().__init__()
            self.signal_adapter = None
            self.redis_enabled = False
        
        # Additional initialization
        self.stats = {
            'signals_processed': 0,
            'trades_executed': 0,
            'redis_signals': 0,
            'fallback_signals': 0,
            'start_time': time.time()
        }
        
        logger.info("Enhanced Trade Manager initialized successfully")
    
    def get_trade_signals(self):
        """
        Enhanced get_trade_signals that uses Redis or falls back to Google Sheets
        
        Returns:
            List of trade signals in the original format
        """
        try:
            # Try Redis first if available
            if self.redis_enabled and self.signal_adapter:
                signals = self.signal_adapter.get_trade_signals()
                if signals:
                    self.stats['redis_signals'] += len(signals)
                    logger.debug(f"Retrieved {len(signals)} signals from Redis")
                    return signals
            
            # Fallback to Google Sheets
            if hasattr(super(), 'get_trade_signals'):
                signals = super().get_trade_signals()
                if signals:
                    self.stats['fallback_signals'] += len(signals)
                    logger.debug(f"Retrieved {len(signals)} signals from Google Sheets")
                return signals
            
            return []
            
        except Exception as e:
            logger.error(f"Error in get_trade_signals: {e}")
            return []
    
    def wait_for_signals(self, timeout=5.0):
        """
        Wait for new signals to arrive (Redis only feature)
        
        Args:
            timeout: Maximum time to wait for signals
            
        Returns:
            List of new signals
        """
        if self.redis_enabled and self.signal_adapter:
            return self.signal_adapter.wait_for_new_signals(timeout)
        else:
            # For Google Sheets, just return current signals
            return self.get_trade_signals()
    
    def run(self):
        """Enhanced run method with Redis signal consumption"""
        logger.info("Starting Enhanced Trade Manager")
        logger.info(f"Redis integration: {'Yes' if self.redis_enabled else 'No'}")
        logger.info(f"Check interval: {self.check_interval} seconds")
        
        # Send startup notification
        startup_message = "🚀 *Enhanced Trade Executor Started*\n\n"
        startup_message += f"• Redis integration: {'Enabled' if self.redis_enabled else 'Disabled'}\n"
        startup_message += f"• Signal source: {'Redis + Sheets fallback' if self.redis_enabled else 'Google Sheets only'}\n"
        startup_message += f"• Check interval: {self.check_interval}s\n"
        startup_message += f"• Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        self.telegram.send_message(startup_message)
        
        last_order_check_time = 0
        order_check_interval = 30
        last_stats_time = time.time()
        
        try:
            while True:
                start_time = time.time()
                
                # Get trade signals (Redis or Google Sheets)
                if self.redis_enabled:
                    # Use Redis with timeout for real-time processing
                    signals = self.wait_for_signals(timeout=float(self.check_interval))
                else:
                    # Use traditional Google Sheets polling
                    signals = self.get_trade_signals()
                
                # Process all signals
                for signal in signals:
                    try:
                        symbol = signal['symbol']
                        action = signal['action']
                        
                        logger.info(f"Processing {action} signal for {symbol}")
                        
                        # For BUY signals
                        if action == "BUY":
                            # Skip if already have an active position
                            if symbol in self.active_positions:
                                logger.debug(f"Skipping BUY for {symbol} - already have an active position")
                                continue
                            
                            # Execute the buy trade
                            success = self.execute_trade(signal)
                            if success:
                                self.stats['trades_executed'] += 1
                        
                        # For SELL signals
                        elif action == "SELL":
                            # Execute the sell trade
                            success = self.execute_trade(signal)
                            if success:
                                self.stats['trades_executed'] += 1
                        
                        self.stats['signals_processed'] += 1
                        
                        # Small delay between trades
                        time.sleep(0.5)
                        
                    except Exception as e:
                        logger.error(f"Error processing signal for {signal.get('symbol', 'unknown')}: {e}")
                
                # Check for take profit/stop loss in active positions
                current_time = time.time()
                if current_time - last_order_check_time >= order_check_interval:
                    try:
                        self.check_completed_orders()
                        self.check_recent_trades()
                        last_order_check_time = current_time
                    except Exception as e:
                        logger.error(f"Error checking orders: {e}")
                
                # Process batch updates to Google Sheets
                try:
                    # Get pending operations from local manager
                    pending_counts = self.local_manager.get_pending_count()
                    total_pending = sum(pending_counts.values())
                    
                    if total_pending > 0:
                        logger.info(f"Processing {total_pending} pending operations")
                        
                        # Get batch for processing
                        batch = self.local_manager.get_batch_for_processing()
                        
                        # Process cell updates
                        if batch['updates']:
                            self._process_cell_updates_batch(batch['updates'])
                        
                        # Process archive operations
                        if batch['archives']:
                            self._process_archive_batch(batch['archives'])
                        
                        # Process clear operations
                        if batch['clears']:
                            self._process_clear_batch(batch['clears'])
                
                except Exception as e:
                    logger.error(f"Error processing batch updates: {e}")
                
                # Log statistics periodically
                if current_time - last_stats_time >= 300:  # Every 5 minutes
                    self._log_statistics()
                    last_stats_time = current_time
                
                # Calculate sleep time for consistent intervals
                elapsed = time.time() - start_time
                sleep_time = max(0, self.check_interval - elapsed)
                
                if self.redis_enabled:
                    # For Redis, use shorter sleep since we get real-time signals
                    sleep_time = min(sleep_time, 1.0)
                
                if sleep_time > 0:
                    logger.debug(f"Cycle completed in {elapsed:.2f}s, sleeping {sleep_time:.2f}s")
                    time.sleep(sleep_time)
                
        except KeyboardInterrupt:
            logger.info("Trade manager stopped by user")
            self.telegram.send_message("⚠️ *Trade Executor Stopped*\n\nTrade executor was manually stopped.")
        except Exception as e:
            logger.critical(f"Fatal error in trade manager: {e}")
            self.telegram.send_message(f"🚨 *Trade Executor Error*\n\nFatal error: {str(e)}")
            raise
        finally:
            # Clean shutdown
            if self.signal_adapter:
                self.signal_adapter.stop()
            logger.info("Enhanced Trade Manager shutdown complete")
    
    def _log_statistics(self):
        """Log operational statistics"""
        uptime = time.time() - self.stats['start_time']
        uptime_hours = uptime / 3600
        
        stats_message = f"📊 *Trade Executor Statistics*\n\n"
        stats_message += f"• Uptime: {uptime_hours:.1f} hours\n"
        stats_message += f"• Signals processed: {self.stats['signals_processed']}\n"
        stats_message += f"• Trades executed: {self.stats['trades_executed']}\n"
        stats_message += f"• Redis signals: {self.stats['redis_signals']}\n"
        stats_message += f"• Fallback signals: {self.stats['fallback_signals']}\n"
        stats_message += f"• Active positions: {len(self.active_positions)}\n"
        
        # Add Redis health if available
        if self.signal_adapter:
            adapter_stats = self.signal_adapter.get_stats()
            stats_message += f"• Redis healthy: {'Yes' if adapter_stats.get('redis_available') else 'No'}\n"
            stats_message += f"• Queue size: {adapter_stats.get('queue_size', 0)}\n"
        
        logger.info("Operational statistics logged")
        self.telegram.send_message(stats_message)
    
    def get_stats(self):
        """Get comprehensive statistics"""
        stats = self.stats.copy()
        stats['uptime'] = time.time() - self.stats['start_time']
        stats['active_positions'] = len(self.active_positions)
        
        # Add Redis adapter stats if available
        if self.signal_adapter:
            stats['adapter_stats'] = self.signal_adapter.get_stats()
        
        return stats


def check_environment():
    """Check environment variables and configuration"""
    logger.info("Checking environment configuration...")
    
    # Check Redis configuration
    redis_host = os.getenv("REDIS_HOST", "localhost")
    redis_port = os.getenv("REDIS_PORT", "6379")
    logger.info(f"Redis configuration: {redis_host}:{redis_port}")
    
    # Check Google Sheets configuration
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    credentials_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
    
    if not sheet_id:
        logger.warning("GOOGLE_SHEET_ID not set - Google Sheets features may not work")
    
    if not os.path.exists(credentials_file):
        logger.warning(f"Credentials file not found: {credentials_file}")
    
    # Check API credentials
    crypto_api_key = os.getenv("CRYPTO_API_KEY")
    crypto_api_secret = os.getenv("CRYPTO_API_SECRET")
    
    if not crypto_api_key or not crypto_api_secret:
        logger.error("Crypto.com API credentials not set - trading will not work")
        return False
    
    # Check Telegram configuration
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat = os.getenv("TELEGRAM_CHAT_ID")
    
    if not telegram_token or not telegram_chat:
        logger.warning("Telegram configuration incomplete - notifications may not work")
    
    logger.info("Environment check complete")
    return True


def test_redis_connection():
    """Test Redis connection"""
    try:
        import redis
        redis_host = os.getenv("REDIS_HOST", "localhost")
        redis_port = int(os.getenv("REDIS_PORT", "6379"))
        redis_password = os.getenv("REDIS_PASSWORD")
        
        client = redis.Redis(
            host=redis_host,
            port=redis_port,
            password=redis_password,
            socket_timeout=5
        )
        
        client.ping()
        logger.info(f"Redis connection successful: {redis_host}:{redis_port}")
        return True
        
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")
        return False


def main():
    """Main entry point"""
    logger.info("=== Executor Integration Script Starting ===")
    logger.info(f"Python version: {sys.version}")
    logger.info(f"Working directory: {os.getcwd()}")
    logger.info(f"Script started at: {datetime.now().isoformat()}")
    
    try:
        # Check environment
        if not check_environment():
            logger.error("Environment check failed")
            sys.exit(1)
        
        # Test Redis connection if enabled
        if os.getenv("ENABLE_REDIS", "true").lower() == "true":
            if not test_redis_connection():
                if os.getenv("ENABLE_SHEETS_FALLBACK", "true").lower() != "true":
                    logger.error("Redis connection failed and fallback disabled")
                    sys.exit(1)
                logger.warning("Redis connection failed, will use fallback")
        
        # Create and run the enhanced trade manager
        manager = EnhancedTradeManager()
        
        # Print startup information
        logger.info("=== Configuration Summary ===")
        logger.info(f"Redis enabled: {os.getenv('ENABLE_REDIS', 'true')}")
        logger.info(f"Sheets fallback: {os.getenv('ENABLE_SHEETS_FALLBACK', 'true')}")
        logger.info(f"Check interval: {manager.check_interval}s")
        logger.info(f"Trade amount: ${manager.exchange_api.trade_amount}")
        logger.info("==============================")
        
        # Start the manager
        manager.run()
        
    except KeyboardInterrupt:
        logger.info("Application interrupted by user")
    except Exception as e:
        logger.critical(f"Application failed: {e}")
        import traceback
        logger.critical(traceback.format_exc())
        sys.exit(1)
    
    logger.info("=== Executor Integration Script Finished ===")


if __name__ == "__main__":
    main()