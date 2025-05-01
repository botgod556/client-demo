#!/usr/bin/env python3
"""
Raspberry Pi 5 GPIO Pin Monitor
This program monitors 4 GPIO pins for switch changes and sends the data to a server.
"""

import RPi.GPIO as GPIO
import socket
import time
import json
import configparser
import os
import sys
import signal
import logging
from logging.handlers import RotatingFileHandler

# Setup logging
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_file = '/var/log/gpio_monitor.log'
log_handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3)
log_handler.setFormatter(log_formatter)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)

logger = logging.getLogger('gpio_monitor')
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)
logger.addHandler(console_handler)

# Default configuration
DEFAULT_CONFIG = {
    'device': {
        'name': 'RaspberryPi_1',
    },
    'server': {
        'ip': '192.168.1.128',
        'port': 5000
    },
    'gpio': {
        'pins': '23,24,25,12',
        'debounce_time': 50  # milliseconds
    }
}

CONFIG_FILE = '/etc/gpio_monitor.conf'

class GPIOMonitor:
    def __init__(self):
        self.config = self.load_config()
        self.device_name = self.config['device']['name']
        self.server_ip = self.config['server']['ip']
        self.server_port = int(self.config['server']['port'])
        self.pins = [int(pin) for pin in self.config['gpio']['pins'].split(',')]
        self.debounce_time = int(self.config['gpio']['debounce_time'])
        
        self.pin_states = {}
        self.pin_timestamps = {}
        self.running = True
        
        # Setup signal handling for graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        # Initialize GPIO
        self.setup_gpio()
        
    def load_config(self):
        """Load configuration from file or create default config if not exists"""
        config = configparser.ConfigParser()
        
        # Set default configuration
        for section, items in DEFAULT_CONFIG.items():
            if not config.has_section(section):
                config.add_section(section)
            for key, value in items.items():
                config.set(section, key, str(value))
        
        # Try to read configuration file
        if os.path.exists(CONFIG_FILE):
            try:
                config.read(CONFIG_FILE)
                logger.info(f"Configuration loaded from {CONFIG_FILE}")
            except Exception as e:
                logger.error(f"Error loading configuration: {e}")
                logger.info("Using default configuration")
        else:
            logger.info(f"Config file {CONFIG_FILE} not found, using default configuration")
            
            # Create default config file
            try:
                with open(CONFIG_FILE, 'w') as configfile:
                    config.write(configfile)
                logger.info(f"Default configuration saved to {CONFIG_FILE}")
            except Exception as e:
                logger.error(f"Could not save default configuration: {e}")
        
        return config
    
    def setup_gpio(self):
        """Initialize GPIO pins"""
        GPIO.setmode(GPIO.BCM)
        
        # Setup pins with pull-up resistors
        for pin in self.pins:
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            
            # Set initial state and timestamp
            self.pin_states[pin] = GPIO.input(pin)
            self.pin_timestamps[pin] = time.time()
            
            # Add event detection with callbacks
            GPIO.add_event_detect(pin, GPIO.BOTH, 
                                 callback=self.pin_changed, 
                                 bouncetime=self.debounce_time)
            
        logger.info(f"GPIO pins {self.pins} initialized with pull-up resistors")
    
    def pin_changed(self, pin):
        """Callback function when a pin state changes"""
        current_time = time.time()
        current_state = GPIO.input(pin)
        
        # Calculate time difference if state has changed
        if current_state != self.pin_states[pin]:
            time_diff_ms = (current_time - self.pin_timestamps[pin]) * 1000  # convert to ms
            
            state_str = "HIGH" if current_state else "LOW"
            logger.info(f"Pin {pin} changed to {state_str}, was in previous state for {time_diff_ms:.2f}ms")
            
            # Send data to server
            self.send_data_to_server(pin, current_state, time_diff_ms)
            
            # Update state and timestamp
            self.pin_states[pin] = current_state
            self.pin_timestamps[pin] = current_time
    
    def send_data_to_server(self, pin, state, time_diff_ms):
        """Send pin change data to the server"""
        data = {
            'device_name': self.device_name,
            'pin': pin,
            'state': 'HIGH' if state else 'LOW',
            'time_diff_ms': round(time_diff_ms, 2),
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
        }
        
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((self.server_ip, self.server_port))
                s.sendall(json.dumps(data).encode('utf-8'))
                response = s.recv(1024).decode('utf-8')
                
                if response == 'OK':
                    logger.info(f"Data for pin {pin} sent successfully")
                else:
                    logger.warning(f"Server returned unexpected response: {response}")
                    
        except ConnectionRefusedError:
            logger.error(f"Connection refused by server {self.server_ip}:{self.server_port}")
        except Exception as e:
            logger.error(f"Error sending data to server: {e}")
    
    def signal_handler(self, sig, frame):
        """Handle termination signals gracefully"""
        logger.info("Shutdown signal received, cleaning up...")
        self.running = False
        self.cleanup()
        sys.exit(0)
    
    def cleanup(self):
        """Clean up GPIO resources"""
        GPIO.cleanup()
        logger.info("GPIO resources cleaned up")
    
    def run(self):
        """Main loop to keep the program running"""
        logger.info(f"GPIO Monitor started on {self.device_name}")
        logger.info(f"Monitoring pins: {self.pins}")
        logger.info(f"Connected to server: {self.server_ip}:{self.server_port}")
        
        try:
            # Keep the program running
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Program interrupted by user")
        finally:
            self.cleanup()

if __name__ == "__main__":
    monitor = GPIOMonitor()
    monitor.run()