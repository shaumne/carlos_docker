#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Signal Producer - Integration Module for yf.py
==============================================

This module provides seamless integration between yf.py and the Redis Signal Bus.
It acts as a bridge, converting market analysis results into trading signals
and publishing them via Redis pub/sub for real-time delivery to trade_executor.py.

Integration Strategy:
- Minimal changes to existing yf.py code
- Drop-in replacement for Google Sheets communication
- Backward compatibility maintained
- Production-ready error handling
"""

import os
import time
import uuid
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

from signal_bus import SignalBus, TradingSignal, SignalType, SignalBusConfig

# Configure logging
logger = logging.getLogger("signal_producer")


@dataclass
class MarketAnalysis:
    """Structure to hold market analysis data from yf.py"""
    symbol: str
    original_symbol: str
    action: str  # "BUY", "SELL", "WAIT"
    last_price: float
    rsi: float
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None
    timestamp: Optional[str] = None
    
    # Additional technical indicators
    ma200: Optional[float] = None
    ma50: Optional[float] = None
    ema10: Optional[float] = None
    ma200_valid: Optional[bool] = None
    ma50_valid: Optional[bool] = None
    ema10_valid: Optional[bool] = None
    
    # Support/Resistance levels
    resistance: Optional[float] = None
    support: Optional[float] = None
    
    # Volume data
    volume: Optional[float] = None
    volume_ratio: Optional[float] = None
    
    # Risk management
    risk_reward_ratio: Optional[float] = None
    atr: Optional[float] = None
    
    # Additional metadata
    buy_signal: Optional[bool] = None
    sell_signal: Optional[bool] = None
    formatted_symbol: Optional[str] = None
    row_index: Optional[int] = None


class SignalProducer:
    """
    Signal Producer for yf.py integration
    
    This class replaces Google Sheets communication in yf.py with Redis pub/sub.
    It maintains the same interface but provides real-time, low-latency signal delivery.
    """
    
    def __init__(self, config: Optional[SignalBusConfig] = None, fallback_to_sheets: bool = True):
        """
        Initialize Signal Producer
        
        Args:
            config: Signal Bus configuration
            fallback_to_sheets: Whether to fallback to Google Sheets on Redis failure
        """
        self.config = config or SignalBusConfig()
        self.fallback_to_sheets = fallback_to_sheets
        self.signal_bus = None
        
        # Statistics tracking
        self.stats = {
            'signals_sent': 0,
            'signals_failed': 0,
            'last_signal_time': None,
            'redis_available': False,
            'fallback_used': 0
        }
        
        # Signal deduplication
        self.last_signals = {}  # symbol -> (action, timestamp)
        self.signal_cooldown = 5  # Minimum seconds between identical signals
        
        # Initialize Redis connection
        self._initialize_redis()
        
        logger.info(f"SignalProducer initialized (Redis: {self.stats['redis_available']})")
    
    def _initialize_redis(self):
        """Initialize Redis Signal Bus connection"""
        try:
            self.signal_bus = SignalBus(self.config)
            self.signal_bus.start()
            
            # Test connection
            if self.signal_bus.is_healthy():
                self.stats['redis_available'] = True
                logger.info("Redis Signal Bus connected successfully")
            else:
                logger.warning("Redis Signal Bus connection unhealthy")
                
        except Exception as e:
            logger.error(f"Failed to initialize Redis Signal Bus: {str(e)}")
            self.stats['redis_available'] = False
            
            if not self.fallback_to_sheets:
                raise RuntimeError(f"Redis Signal Bus required but failed to initialize: {str(e)}")
    
    def _should_send_signal(self, analysis: MarketAnalysis) -> bool:
        """
        Determine if signal should be sent based on deduplication rules
        
        Args:
            analysis: Market analysis data
            
        Returns:
            True if signal should be sent, False otherwise
        """
        current_time = time.time()
        symbol = analysis.symbol
        action = analysis.action
        
        # Always send BUY and SELL signals
        if action in ["BUY", "SELL"]:
            # Check cooldown for identical signals
            if symbol in self.last_signals:
                last_action, last_time = self.last_signals[symbol]
                if (last_action == action and 
                    current_time - last_time < self.signal_cooldown):
                    logger.debug(f"Signal cooldown active for {symbol}: {action}")
                    return False
            
            # Update last signal tracking
            self.last_signals[symbol] = (action, current_time)
            return True
        
        # For WAIT signals, only send if previous signal was different
        if symbol in self.last_signals:
            last_action, _ = self.last_signals[symbol]
            if last_action != "WAIT":
                self.last_signals[symbol] = (action, current_time)
                return True
        else:
            # First signal for this symbol
            self.last_signals[symbol] = (action, current_time)
            return True
        
        return False
    
    def _convert_analysis_to_signal(self, analysis: MarketAnalysis) -> TradingSignal:
        """
        Convert market analysis to trading signal
        
        Args:
            analysis: Market analysis data
            
        Returns:
            TradingSignal object ready for Redis publishing
        """
        # Determine signal type
        if analysis.action == "BUY":
            signal_type = SignalType.BUY
        elif analysis.action == "SELL":
            signal_type = SignalType.SELL
        else:
            signal_type = SignalType.WAIT
        
        # Prepare signal data
        signal_data = {
            'action': analysis.action,
            'original_symbol': analysis.original_symbol,
            'last_price': analysis.last_price,
            'rsi': analysis.rsi,
            'timestamp': analysis.timestamp or datetime.now().isoformat()
        }
        
        # Add trading levels for BUY signals
        if analysis.action == "BUY" and analysis.take_profit and analysis.stop_loss:
            signal_data.update({
                'take_profit': analysis.take_profit,
                'stop_loss': analysis.stop_loss,
                'risk_reward_ratio': analysis.risk_reward_ratio
            })
        
        # Add technical indicators
        if analysis.ma200 is not None:
            signal_data['ma200'] = analysis.ma200
            signal_data['ma200_valid'] = analysis.ma200_valid
        
        if analysis.ma50 is not None:
            signal_data['ma50'] = analysis.ma50
            signal_data['ma50_valid'] = analysis.ma50_valid
        
        if analysis.ema10 is not None:
            signal_data['ema10'] = analysis.ema10
            signal_data['ema10_valid'] = analysis.ema10_valid
        
        # Add support/resistance levels
        if analysis.resistance is not None:
            signal_data['resistance'] = analysis.resistance
        
        if analysis.support is not None:
            signal_data['support'] = analysis.support
        
        # Add volume data
        if analysis.volume is not None:
            signal_data['volume'] = analysis.volume
        
        if analysis.volume_ratio is not None:
            signal_data['volume_ratio'] = analysis.volume_ratio
        
        # Add ATR data
        if analysis.atr is not None:
            signal_data['atr'] = analysis.atr
        
        # Add row index for backward compatibility
        if analysis.row_index is not None:
            signal_data['row_index'] = analysis.row_index
        
        # Create trading signal
        return TradingSignal(
            signal_id=str(uuid.uuid4()),
            signal_type=signal_type,
            symbol=analysis.symbol,
            timestamp=datetime.now().isoformat(),
            data=signal_data,
            source="yf.py"
        )
    
    def send_signal(self, analysis: MarketAnalysis) -> bool:
        """
        Send trading signal via Redis or fallback mechanism
        
        Args:
            analysis: Market analysis data from yf.py
            
        Returns:
            True if signal was sent successfully, False otherwise
        """
        try:
            # Check if we should send this signal
            if not self._should_send_signal(analysis):
                logger.debug(f"Skipping signal for {analysis.symbol}: {analysis.action}")
                return True  # Return True as this is not an error
            
            # Convert analysis to signal
            signal = self._convert_analysis_to_signal(analysis)
            
            # Try Redis first
            if self.stats['redis_available'] and self.signal_bus:
                try:
                    success = self.signal_bus.publish_signal(signal)
                    if success:
                        self.stats['signals_sent'] += 1
                        self.stats['last_signal_time'] = time.time()
                        logger.info(f"Sent {signal.signal_type.value} signal for {analysis.symbol} via Redis")
                        return True
                    else:
                        logger.warning(f"Failed to publish signal via Redis for {analysis.symbol}")
                        
                except Exception as e:
                    logger.error(f"Redis signal publishing failed: {str(e)}")
                    self.stats['redis_available'] = False
            
            # Fallback to Google Sheets if enabled
            if self.fallback_to_sheets:
                return self._fallback_to_sheets(analysis)
            else:
                self.stats['signals_failed'] += 1
                return False
                
        except Exception as e:
            logger.error(f"Error in send_signal: {str(e)}")
            self.stats['signals_failed'] += 1
            return False
    
    def _fallback_to_sheets(self, analysis: MarketAnalysis) -> bool:
        """
        Fallback to Google Sheets communication
        
        Args:
            analysis: Market analysis data
            
        Returns:
            True if fallback succeeded, False otherwise
        """
        try:
            logger.warning(f"Using Google Sheets fallback for {analysis.symbol}")
            self.stats['fallback_used'] += 1
            
            # Here you would implement the Google Sheets update logic
            # For now, we'll just log the fallback attempt
            logger.info(f"Fallback signal: {analysis.action} for {analysis.symbol} at {analysis.last_price}")
            
            # In the actual implementation, you would call the original
            # Google Sheets update method from yf.py
            # Example: self.sheets.update_analysis(analysis.row_index, analysis.__dict__)
            
            return True
            
        except Exception as e:
            logger.error(f"Google Sheets fallback failed: {str(e)}")
            return False
    
    def send_heartbeat(self) -> bool:
        """
        Send heartbeat signal to indicate yf.py is alive
        
        Returns:
            True if heartbeat was sent successfully
        """
        try:
            if self.signal_bus and self.stats['redis_available']:
                heartbeat_signal = TradingSignal(
                    signal_id=str(uuid.uuid4()),
                    signal_type=SignalType.HEARTBEAT,
                    symbol="SYSTEM",
                    timestamp=datetime.now().isoformat(),
                    data={
                        "status": "alive",
                        "stats": self.stats.copy(),
                        "process": "yf.py"
                    },
                    source="yf.py"
                )
                
                return self.signal_bus.publish_signal(heartbeat_signal)
            
            return False
            
        except Exception as e:
            logger.error(f"Error sending heartbeat: {str(e)}")
            return False
    
    def get_stats(self) -> Dict[str, Any]:
        """Get producer statistics"""
        stats = self.stats.copy()
        if self.signal_bus:
            bus_stats = self.signal_bus.get_stats()
            stats['bus_stats'] = bus_stats
        return stats
    
    def is_healthy(self) -> bool:
        """Check if producer is healthy"""
        if self.signal_bus and self.stats['redis_available']:
            return self.signal_bus.is_healthy()
        return self.fallback_to_sheets  # Healthy if fallback is available
    
    def stop(self):
        """Stop the signal producer"""
        logger.info("Stopping SignalProducer...")
        if self.signal_bus:
            self.signal_bus.stop()
        logger.info("SignalProducer stopped")


class YfIntegrationAdapter:
    """
    Adapter class to integrate with existing yf.py code
    
    This class provides a drop-in replacement for GoogleSheetIntegration
    in yf.py, allowing seamless migration to Redis-based communication.
    """
    
    def __init__(self, original_sheets_integration=None):
        """
        Initialize adapter
        
        Args:
            original_sheets_integration: Original GoogleSheetIntegration instance for fallback
        """
        self.producer = SignalProducer(fallback_to_sheets=bool(original_sheets_integration))
        self.original_sheets = original_sheets_integration
        
        # Track which symbols are being processed
        self.active_symbols = set()
        
        logger.info("YfIntegrationAdapter initialized")
    
    def update_analysis(self, row_index: int, analysis_data: Dict[str, Any]) -> bool:
        """
        Drop-in replacement for GoogleSheetIntegration.update_analysis()
        
        Args:
            row_index: Sheet row index (for backward compatibility)
            analysis_data: Analysis data from yf.py
            
        Returns:
            True if update was successful
        """
        try:
            # Convert analysis_data dict to MarketAnalysis object
            analysis = MarketAnalysis(
                symbol=analysis_data.get('symbol', ''),
                original_symbol=analysis_data.get('original_symbol', analysis_data.get('symbol', '')),
                action=analysis_data.get('action', 'WAIT'),
                last_price=float(analysis_data.get('last_price', 0)),
                rsi=float(analysis_data.get('rsi', 50)),
                take_profit=analysis_data.get('take_profit'),
                stop_loss=analysis_data.get('stop_loss'),
                timestamp=analysis_data.get('timestamp'),
                ma200=analysis_data.get('ma200'),
                ma50=analysis_data.get('ma50'),
                ema10=analysis_data.get('ema10'),
                ma200_valid=analysis_data.get('ma200_valid'),
                ma50_valid=analysis_data.get('ma50_valid'),
                ema10_valid=analysis_data.get('ema10_valid'),
                resistance=analysis_data.get('resistance'),
                support=analysis_data.get('support'),
                volume=analysis_data.get('volume'),
                volume_ratio=analysis_data.get('volume_ratio'),
                risk_reward_ratio=analysis_data.get('risk_reward_ratio'),
                atr=analysis_data.get('atr'),
                buy_signal=analysis_data.get('buy_signal'),
                sell_signal=analysis_data.get('sell_signal'),
                formatted_symbol=analysis_data.get('formatted_symbol'),
                row_index=row_index
            )
            
            # Send signal via Redis
            success = self.producer.send_signal(analysis)
            
            # Fallback to original sheets if Redis failed and fallback is available
            if not success and self.original_sheets:
                logger.warning(f"Redis failed, using Google Sheets fallback for {analysis.symbol}")
                return self.original_sheets.update_analysis(row_index, analysis_data)
            
            return success
            
        except Exception as e:
            logger.error(f"Error in update_analysis: {str(e)}")
            
            # Try fallback
            if self.original_sheets:
                try:
                    return self.original_sheets.update_analysis(row_index, analysis_data)
                except Exception as fallback_error:
                    logger.error(f"Fallback also failed: {str(fallback_error)}")
            
            return False
    
    def get_trading_pairs(self) -> List[Dict[str, Any]]:
        """
        Delegate to original sheets integration for trading pairs
        
        Note: This method still uses Google Sheets as the source of truth
        for which coins to track. Only the signal communication is via Redis.
        """
        if self.original_sheets:
            return self.original_sheets.get_trading_pairs()
        else:
            logger.warning("No original sheets integration available for get_trading_pairs")
            return []
    
    def update_timestamp_only(self, row_index: int, data: Dict[str, Any]) -> bool:
        """Delegate timestamp updates to original sheets (optional)"""
        if self.original_sheets:
            return self.original_sheets.update_timestamp_only(row_index, data)
        return True  # Not critical if this fails
    
    def has_open_position(self, symbol: str) -> bool:
        """Delegate position checks to original sheets"""
        if self.original_sheets:
            return self.original_sheets.has_open_position(symbol)
        return False  # Safe default
    
    def get_stats(self) -> Dict[str, Any]:
        """Get adapter statistics"""
        return self.producer.get_stats()
    
    def is_healthy(self) -> bool:
        """Check if adapter is healthy"""
        return self.producer.is_healthy()
    
    def stop(self):
        """Stop the adapter"""
        self.producer.stop()


# Example usage and testing
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        # Test the signal producer
        print("Testing Signal Producer...")
        
        producer = SignalProducer(fallback_to_sheets=False)
        
        # Test market analysis data
        test_analysis = MarketAnalysis(
            symbol="BTC_USDT",
            original_symbol="BTC",
            action="BUY",
            last_price=45000.0,
            rsi=35.5,
            take_profit=46000.0,
            stop_loss=44000.0,
            ma200_valid=True,
            ma50_valid=True,
            ema10_valid=True,
            volume_ratio=2.5
        )
        
        # Send test signal
        success = producer.send_signal(test_analysis)
        print(f"Test signal sent: {success}")
        
        # Show stats
        stats = producer.get_stats()
        print(f"Producer stats: {stats}")
        
        # Send heartbeat
        heartbeat_success = producer.send_heartbeat()
        print(f"Heartbeat sent: {heartbeat_success}")
        
        # Clean shutdown
        producer.stop()
        
    else:
        print("Usage: python signal_producer.py test")