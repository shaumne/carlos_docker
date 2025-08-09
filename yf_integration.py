#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
YF Integration Script
====================

This script integrates the existing yf.py with the new Redis-based signal system.
It serves as a drop-in replacement that maintains backward compatibility while
adding real-time signal delivery capabilities.

Usage:
    python yf_integration.py

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
    from yf import TradingBot, GoogleSheetIntegration, TelegramNotifier
except ImportError as e:
    print(f"Error importing yf.py modules: {e}")
    print("Make sure yf.py is in the same directory")
    sys.exit(1)

# Import new signal modules
try:
    from signal_producer import YfIntegrationAdapter, SignalProducer
    from signal_bus import SignalBusConfig
except ImportError as e:
    print(f"Error importing signal modules: {e}")
    print("Make sure signal_bus.py and signal_producer.py are available")
    sys.exit(1)

# Configure logging
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logs/yf_integration.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("yf_integration")


class EnhancedTradingBot:
    """Composition-based wrapper that preserves TradingBot internals"""

    def __init__(self):
        logger.info("Initializing Enhanced Trading Bot with Redis integration (composition)")

        # Create original bot first so its internals are fully initialized
        self.bot = TradingBot()

        enable_redis = os.getenv("ENABLE_REDIS", "true").lower() == "true"
        enable_sheets_fallback = os.getenv("ENABLE_SHEETS_FALLBACK", "true").lower() == "true"

        if enable_redis:
            try:
                _ = SignalBusConfig()  # validate env
                original_sheets = None
                if enable_sheets_fallback:
                    try:
                        original_sheets = self.bot.sheets if hasattr(self.bot, 'sheets') else GoogleSheetIntegration()
                        logger.info("Google Sheets fallback initialized")
                    except Exception as e:
                        logger.warning(f"Google Sheets fallback failed to initialize: {e}")

                # Swap sheets with adapter
                self.bot.sheets = YfIntegrationAdapter(original_sheets)
                logger.info("Redis integration initialized successfully")
            except Exception as e:
                logger.error(f"Redis integration failed: {e}")
                if enable_sheets_fallback:
                    logger.info("Falling back to Google Sheets only")
                else:
                    raise RuntimeError(f"Redis required but failed to initialize: {e}")
        else:
            logger.info("Using Google Sheets only (Redis disabled)")

    def run(self):
        # Startup info
        try:
            if hasattr(self.bot, 'telegram'):
                self.bot.telegram.send_startup_message()
        except Exception:
            pass

        # Heartbeat if Redis
        try:
            sheets = getattr(self.bot, 'sheets', None)
            if sheets and hasattr(sheets, 'producer'):
                sheets.producer.send_heartbeat()
        except Exception:
            pass

        # Delegate to original bot
        self.bot.run()


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
    if not crypto_api_key:
        logger.warning("CRYPTO_API_KEY not set - trading features may not work")
    
    # Check Telegram configuration
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat = os.getenv("TELEGRAM_CHAT_ID")
    
    if not telegram_token or not telegram_chat:
        logger.warning("Telegram configuration incomplete - notifications may not work")
    
    logger.info("Environment check complete")


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
    logger.info("=== YF Integration Script Starting ===")
    logger.info(f"Python version: {sys.version}")
    logger.info(f"Working directory: {os.getcwd()}")
    logger.info(f"Script started at: {datetime.now().isoformat()}")
    
    try:
        # Check environment
        check_environment()
        
        # Test Redis connection if enabled
        if os.getenv("ENABLE_REDIS", "true").lower() == "true":
            if not test_redis_connection():
                if os.getenv("ENABLE_SHEETS_FALLBACK", "true").lower() != "true":
                    logger.error("Redis connection failed and fallback disabled")
                    sys.exit(1)
                logger.warning("Redis connection failed, will use fallback")
        
        # Create and run the enhanced trading bot
        bot = EnhancedTradingBot()
        
        # Print startup information
        logger.info("=== Configuration Summary ===")
        logger.info(f"Redis enabled: {os.getenv('ENABLE_REDIS', 'true')}")
        logger.info(f"Sheets fallback: {os.getenv('ENABLE_SHEETS_FALLBACK', 'true')}")
        logger.info(f"Update interval: {bot.update_interval}s")
        logger.info(f"Batch size: {bot.batch_size}")
        logger.info("==============================")
        
        # Start the bot
        bot.run()
        
    except KeyboardInterrupt:
        logger.info("Application interrupted by user")
    except Exception as e:
        logger.critical(f"Application failed: {e}")
        import traceback
        logger.critical(traceback.format_exc())
        sys.exit(1)
    
    logger.info("=== YF Integration Script Finished ===")


if __name__ == "__main__":
    main()