#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Signal Consumer - Integration Module for trade_executor.py
========================================================

This module provides seamless integration between trade_executor.py and the Redis Signal Bus.
It replaces Google Sheets polling with real-time signal consumption from Redis pub/sub,
enabling instant trade execution with minimal latency.

Integration Strategy:
- Drop-in replacement for Google Sheets polling
- Maintains existing trade execution logic
- Real-time signal processing
- Comprehensive error handling and fallback mechanisms
"""

import os
import time
import json
import logging
import threading
from datetime import datetime
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass
from queue import Queue, Empty
import uuid

from signal_bus import SignalBus, TradingSignal, SignalType, SignalBusConfig

# Configure logging
logger = logging.getLogger("signal_consumer")


@dataclass
class TradeSignalData:
    """Structure to hold trade signal data compatible with trade_executor.py"""
    symbol: str
    original_symbol: str
    row_index: int
    action: str  # "BUY", "SELL", "WAIT"
    last_price: float
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None
    order_id: Optional[str] = None
    
    # Additional data for compatibility
    timestamp: Optional[str] = None
    rsi: Optional[float] = None
    volume_ratio: Optional[float] = None
    signal_id: Optional[str] = None


class SignalQueue:
    """Thread-safe queue for managing trading signals"""
    
    def __init__(self, max_size: int = 1000):
        self.queue = Queue(maxsize=max_size)
        self.processed_signals = set()  # Track processed signal IDs
        self.max_processed_history = 10000  # Maximum processed signals to remember
        
    def put_signal(self, signal: TradeSignalData) -> bool:
        """Add signal to queue if not already processed"""
        try:
            # Check if signal was already processed
            if signal.signal_id and signal.signal_id in self.processed_signals:
                logger.debug(f"Skipping already processed signal: {signal.signal_id}")
                return False
            
            # Add to queue
            self.queue.put(signal, block=False)
            
            # Track as processed
            if signal.signal_id:
                self.processed_signals.add(signal.signal_id)
                
                # Clean old processed signals to prevent memory growth
                if len(self.processed_signals) > self.max_processed_history:
                    # Remove oldest 20% of entries
                    to_remove = list(self.processed_signals)[:int(self.max_processed_history * 0.2)]
                    for signal_id in to_remove:
                        self.processed_signals.discard(signal_id)
            
            logger.debug(f"Queued {signal.action} signal for {signal.symbol}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to queue signal: {str(e)}")
            return False
    
    def get_signal(self, timeout: Optional[float] = None) -> Optional[TradeSignalData]:
        """Get next signal from queue"""
        try:
            return self.queue.get(timeout=timeout)
        except Empty:
            return None
    
    def get_queue_size(self) -> int:
        """Get current queue size"""
        return self.queue.qsize()
    
    def clear(self):
        """Clear all pending signals"""
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except Empty:
                break


class SignalConsumer:
    """
    Signal Consumer for trade_executor.py integration
    
    This class replaces Google Sheets polling in trade_executor.py with Redis pub/sub
    consumption, providing real-time signal processing with minimal latency.
    """
    
    def __init__(self, config: Optional[SignalBusConfig] = None, fallback_to_sheets: bool = True):
        """
        Initialize Signal Consumer
        
        Args:
            config: Signal Bus configuration
            fallback_to_sheets: Whether to fallback to Google Sheets on Redis failure
        """
        self.config = config or SignalBusConfig()
        self.fallback_to_sheets = fallback_to_sheets
        self.signal_bus = None
        
        # Signal processing
        self.signal_queue = SignalQueue()
        self.signal_handlers: Dict[str, Callable] = {}  # action -> handler function
        
        # State tracking
        self.running = False
        self.processor_thread = None
        
        # Statistics
        self.stats = {
            'signals_received': 0,
            'signals_processed': 0,
            'signals_failed': 0,
            'last_signal_time': None,
            'redis_available': False,
            'fallback_used': 0,
            'queue_size': 0
        }
        
        # Heartbeat tracking
        self.last_heartbeat = {}  # source -> timestamp
        self.heartbeat_timeout = 120  # 2 minutes
        
        # Initialize Redis connection
        self._initialize_redis()
        
        logger.info(f"SignalConsumer initialized (Redis: {self.stats['redis_available']})")
    
    def _initialize_redis(self):
        """Initialize Redis Signal Bus connection"""
        try:
            self.signal_bus = SignalBus(self.config)
            
            # Add signal handlers
            self.signal_bus.add_signal_handler(SignalType.BUY, self._handle_redis_signal)
            self.signal_bus.add_signal_handler(SignalType.SELL, self._handle_redis_signal)
            self.signal_bus.add_signal_handler(SignalType.WAIT, self._handle_redis_signal)
            self.signal_bus.add_signal_handler(SignalType.HEARTBEAT, self._handle_heartbeat)
            
            self.signal_bus.start()
            self.signal_bus.subscribe_to_signals()
            
            # Test connection
            if self.signal_bus.is_healthy():
                self.stats['redis_available'] = True
                logger.info("Redis Signal Bus connected and subscribed successfully")
            else:
                logger.warning("Redis Signal Bus connection unhealthy")
                
        except Exception as e:
            logger.error(f"Failed to initialize Redis Signal Bus: {str(e)}")
            self.stats['redis_available'] = False
            
            if not self.fallback_to_sheets:
                raise RuntimeError(f"Redis Signal Bus required but failed to initialize: {str(e)}")
    
    def _handle_redis_signal(self, signal: TradingSignal):
        """Handle incoming Redis signal"""
        try:
            # Convert Redis signal to TradeSignalData
            trade_signal = self._convert_redis_signal(signal)
            
            if trade_signal:
                # Add to processing queue
                queued = self.signal_queue.put_signal(trade_signal)
                if queued:
                    self.stats['signals_received'] += 1
                    self.stats['last_signal_time'] = time.time()
                    self.stats['queue_size'] = self.signal_queue.get_queue_size()
                    
                    logger.info(f"Received {signal.signal_type.value} signal for {signal.symbol}")
                else:
                    logger.debug(f"Signal already processed or queue full: {signal.symbol}")
            
        except Exception as e:
            logger.error(f"Error handling Redis signal: {str(e)}")
            self.stats['signals_failed'] += 1
    
    def _handle_heartbeat(self, signal: TradingSignal):
        """Handle heartbeat signals from other components"""
        try:
            source = signal.source
            self.last_heartbeat[source] = time.time()
            logger.debug(f"Received heartbeat from {source}")
            
        except Exception as e:
            logger.error(f"Error handling heartbeat: {str(e)}")
    
    def _convert_redis_signal(self, signal: TradingSignal) -> Optional[TradeSignalData]:
        """Convert Redis TradingSignal to TradeSignalData"""
        try:
            data = signal.data
            
            # Skip heartbeat signals
            if signal.signal_type == SignalType.HEARTBEAT:
                return None
            
            # Create TradeSignalData object
            trade_signal = TradeSignalData(
                symbol=signal.symbol,
                original_symbol=data.get('original_symbol', signal.symbol),
                row_index=data.get('row_index', 0),  # Default for Redis signals
                action=data.get('action', signal.signal_type.value),
                last_price=float(data.get('last_price', 0)),
                take_profit=data.get('take_profit'),
                stop_loss=data.get('stop_loss'),
                order_id=data.get('order_id'),
                timestamp=data.get('timestamp', signal.timestamp),
                rsi=data.get('rsi'),
                volume_ratio=data.get('volume_ratio'),
                signal_id=signal.signal_id
            )
            
            return trade_signal
            
        except Exception as e:
            logger.error(f"Error converting Redis signal: {str(e)}")
            return None
    
    def _signal_processor_worker(self):
        """Background worker to process signals from queue"""
        logger.info("Signal processor started")
        
        while self.running:
            try:
                # Get next signal from queue
                signal = self.signal_queue.get_signal(timeout=1.0)
                
                if signal:
                    # Process the signal
                    self._process_signal(signal)
                    self.stats['queue_size'] = self.signal_queue.get_queue_size()
                
            except Exception as e:
                logger.error(f"Error in signal processor: {str(e)}")
                time.sleep(1)
        
        logger.info("Signal processor stopped")
    
    def _process_signal(self, signal: TradeSignalData):
        """Process a trading signal"""
        try:
            action = signal.action.upper()
            
            # Find appropriate handler
            handler = self.signal_handlers.get(action)
            if handler:
                logger.info(f"Processing {action} signal for {signal.symbol} at {signal.last_price}")
                handler(signal)
                self.stats['signals_processed'] += 1
            else:
                logger.warning(f"No handler registered for action: {action}")
            
        except Exception as e:
            logger.error(f"Error processing signal {signal.symbol}: {str(e)}")
            self.stats['signals_failed'] += 1
    
    def register_signal_handler(self, action: str, handler: Callable[[TradeSignalData], None]):
        """
        Register a handler function for specific signal types
        
        Args:
            action: Signal action ("BUY", "SELL", "WAIT")
            handler: Function to call when signal is received
        """
        self.signal_handlers[action.upper()] = handler
        logger.info(f"Registered handler for {action} signals")
    
    def start(self):
        """Start the signal consumer"""
        if self.running:
            logger.warning("SignalConsumer is already running")
            return
        
        logger.info("Starting SignalConsumer...")
        self.running = True
        
        # Start signal processor thread
        self.processor_thread = threading.Thread(target=self._signal_processor_worker, daemon=True)
        self.processor_thread.start()
        
        logger.info("SignalConsumer started successfully")
    
    def stop(self):
        """Stop the signal consumer"""
        logger.info("Stopping SignalConsumer...")
        self.running = False
        
        # Wait for processor thread
        if self.processor_thread and self.processor_thread.is_alive():
            self.processor_thread.join(timeout=5)
        
        # Stop signal bus
        if self.signal_bus:
            self.signal_bus.stop()
        
        # Clear pending signals
        self.signal_queue.clear()
        
        logger.info("SignalConsumer stopped")
    
    def get_pending_signals(self) -> List[TradeSignalData]:
        """
        Get all pending signals for processing
        
        Returns:
            List of pending trade signals (compatible with trade_executor.py)
        """
        signals = []
        
        # Get all signals from queue without blocking
        while True:
            signal = self.signal_queue.get_signal(timeout=0.01)
            if signal is None:
                break
            signals.append(signal)
        
        # Update stats
        self.stats['queue_size'] = self.signal_queue.get_queue_size()
        
        return signals
    
    def wait_for_signals(self, timeout: Optional[float] = None) -> List[TradeSignalData]:
        """
        Wait for signals to arrive and return them
        
        Args:
            timeout: Maximum time to wait for signals
            
        Returns:
            List of received signals
        """
        signals = []
        start_time = time.time()
        
        while True:
            # Check timeout
            if timeout and (time.time() - start_time) > timeout:
                break
            
            # Get signal with short timeout
            signal = self.signal_queue.get_signal(timeout=0.1)
            if signal:
                signals.append(signal)
            else:
                # No signal received, check if we should continue waiting
                if signals or not timeout:
                    break
        
        return signals
    
    def get_stats(self) -> Dict[str, Any]:
        """Get consumer statistics"""
        stats = self.stats.copy()
        stats['queue_size'] = self.signal_queue.get_queue_size()
        stats['running'] = self.running
        
        # Add Redis stats if available
        if self.signal_bus:
            bus_stats = self.signal_bus.get_stats()
            stats['bus_stats'] = bus_stats
        
        # Add heartbeat status
        current_time = time.time()
        active_sources = []
        for source, last_time in self.last_heartbeat.items():
            if current_time - last_time < self.heartbeat_timeout:
                active_sources.append(source)
        
        stats['active_sources'] = active_sources
        stats['heartbeat_sources'] = len(active_sources)
        
        return stats
    
    def is_healthy(self) -> bool:
        """Check if consumer is healthy"""
        if self.signal_bus and self.stats['redis_available']:
            return self.signal_bus.is_healthy() and self.running
        return self.fallback_to_sheets and self.running
    
    def check_source_health(self) -> Dict[str, bool]:
        """Check health of signal sources"""
        current_time = time.time()
        source_health = {}
        
        for source, last_time in self.last_heartbeat.items():
            is_healthy = (current_time - last_time) < self.heartbeat_timeout
            source_health[source] = is_healthy
        
        return source_health


class TradeExecutorAdapter:
    """
    Adapter class to integrate with existing trade_executor.py code
    
    This class provides a drop-in replacement for the Google Sheets polling
    mechanism in trade_executor.py, allowing seamless migration to Redis-based
    real-time signal consumption.
    """
    
    def __init__(self, original_trade_manager=None):
        """
        Initialize adapter
        
        Args:
            original_trade_manager: Original GoogleSheetTradeManager instance for fallback
        """
        self.consumer = SignalConsumer(fallback_to_sheets=bool(original_trade_manager))
        self.original_manager = original_trade_manager
        
        # Start consumer
        self.consumer.start()
        
        logger.info("TradeExecutorAdapter initialized")
    
    def get_trade_signals(self) -> List[Dict[str, Any]]:
        """
        Drop-in replacement for GoogleSheetTradeManager.get_trade_signals()
        
        Returns:
            List of trade signals in the format expected by trade_executor.py
        """
        try:
            # Get signals from Redis
            if self.consumer.stats['redis_available']:
                pending_signals = self.consumer.get_pending_signals()
                
                # Convert to format expected by trade_executor.py
                trade_signals = []
                for signal in pending_signals:
                    signal_dict = {
                        'symbol': signal.symbol,
                        'original_symbol': signal.original_symbol,
                        'row_index': signal.row_index,
                        'action': signal.action,
                        'last_price': signal.last_price
                    }
                    
                    # Add optional fields
                    if signal.take_profit:
                        signal_dict['take_profit'] = signal.take_profit
                    
                    if signal.stop_loss:
                        signal_dict['stop_loss'] = signal.stop_loss
                    
                    if signal.order_id:
                        signal_dict['order_id'] = signal.order_id
                    
                    trade_signals.append(signal_dict)
                
                if trade_signals:
                    logger.info(f"Retrieved {len(trade_signals)} signals from Redis")
                    return trade_signals
            
            # Fallback to original manager if Redis unavailable or no signals
            if self.original_manager:
                logger.debug("Using Google Sheets fallback for trade signals")
                self.consumer.stats['fallback_used'] += 1
                return self.original_manager.get_trade_signals()
            
            return []
            
        except Exception as e:
            logger.error(f"Error in get_trade_signals: {str(e)}")
            
            # Try fallback
            if self.original_manager:
                try:
                    return self.original_manager.get_trade_signals()
                except Exception as fallback_error:
                    logger.error(f"Fallback also failed: {str(fallback_error)}")
            
            return []
    
    def register_trade_handler(self, action: str, handler: Callable):
        """Register a handler for specific trade actions"""
        self.consumer.register_signal_handler(action, handler)
    
    def wait_for_new_signals(self, timeout: float = 5.0) -> List[Dict[str, Any]]:
        """
        Wait for new signals to arrive
        
        Args:
            timeout: Maximum time to wait for signals
            
        Returns:
            List of new signals
        """
        signals = self.consumer.wait_for_signals(timeout)
        
        # Convert to expected format
        return [
            {
                'symbol': s.symbol,
                'original_symbol': s.original_symbol,
                'row_index': s.row_index,
                'action': s.action,
                'last_price': s.last_price,
                'take_profit': s.take_profit,
                'stop_loss': s.stop_loss,
                'order_id': s.order_id
            }
            for s in signals
        ]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get adapter statistics"""
        return self.consumer.get_stats()
    
    def is_healthy(self) -> bool:
        """Check if adapter is healthy"""
        return self.consumer.is_healthy()
    
    def stop(self):
        """Stop the adapter"""
        self.consumer.stop()


# Example usage and testing
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        # Test the signal consumer
        print("Testing Signal Consumer...")
        
        consumer = SignalConsumer(fallback_to_sheets=False)
        
        # Register test handlers
        def handle_buy_signal(signal: TradeSignalData):
            print(f"BUY Handler: {signal.symbol} at {signal.last_price}")
        
        def handle_sell_signal(signal: TradeSignalData):
            print(f"SELL Handler: {signal.symbol} at {signal.last_price}")
        
        consumer.register_signal_handler("BUY", handle_buy_signal)
        consumer.register_signal_handler("SELL", handle_sell_signal)
        
        # Start consumer
        consumer.start()
        
        try:
            # Wait for signals
            print("Waiting for signals... (Press Ctrl+C to stop)")
            while True:
                stats = consumer.get_stats()
                print(f"Stats: Received={stats['signals_received']}, "
                      f"Processed={stats['signals_processed']}, "
                      f"Queue={stats['queue_size']}")
                time.sleep(10)
                
        except KeyboardInterrupt:
            print("Stopping...")
        finally:
            consumer.stop()
            
    else:
        print("Usage: python signal_consumer.py test")