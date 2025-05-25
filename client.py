#!/usr/bin/env python3
"""
Orange Pi Zero 2W GPIO Pin Monitor
This program monitors 4 GPIO pins for switch changes and sends the data to a server.
Uses OPi.GPIO for Orange Pi Zero 2W compatibility.
"""

import OPi.GPIO as GPIO
import socket
import time
import json
import configparser
import os
import sys
import signal
import logging
from logging.handlers import RotatingFileHandler
import threading

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
        'name': 'Device-1',  # This will be overridden by the hostname
    },
    'server': {
        'ip': '192.168.1.128',
        'port': 5000
    },
    'gpio': {
        'pins': 'PC12,PI11,PH4,PI1',
        'debounce_time': 100  # milliseconds
    }
}

CONFIG_FILE = '/etc/gpio_monitor.conf'

class GPIOMonitor:
    def __init__(self):
        self.config = self.load_config()
        
        # Get hostname and use it as device name
        self.device_name = self.get_hostname()
        logger.info(f"Using hostname '{self.device_name}' as device name")
        
        self.server_ip = self.config['server']['ip']
        self.server_port = int(self.config['server']['port'])
        self.pins = self.config['gpio']['pins'].split(',')
        self.debounce_time = int(self.config['gpio']['debounce_time'])
        
        self.pin_states = {}
        self.pin_timestamps = {}
        self.last_interrupt_time = {}
        self.running = True
        
        # Setup signal handling for graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        # Initialize GPIO
        self.setup_gpio()
    
    def get_hostname(self):
        """Get the hostname of the device"""
        try:
            # Use socket.gethostname() to get the hostname
            hostname = socket.gethostname()
            logger.info(f"Retrieved hostname: {hostname}")
            return hostname
        except Exception as e:
            logger.error(f"Error getting hostname: {e}")
            # Fall back to the configured device name
            return self.config['device']['name']
        
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
        """Initialize GPIO pins using OPi.GPIO"""
        # Set the GPIO mode to SOC (use pin names like PC12)
        GPIO.setmode(GPIO.SUNXI)
        GPIO.setwarnings(False)
        
        # Setup pins with pull-up resistors
        for pin in self.pins:
            try:
                # Setup pin as input with pull-up resistor
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                
                # Read initial state
                initial_state = GPIO.input(pin)
                self.pin_states[pin] = initial_state
                self.pin_timestamps[pin] = time.time()
                self.last_interrupt_time[pin] = 0
                
                # Add event detection for both edges with debounce
                GPIO.add_event_detect(pin, GPIO.BOTH, 
                                    callback=self.pin_change_callback, 
                                    bouncetime=self.debounce_time)
                
                logger.info(f"Pin {pin} initialized with pull-up, initial state: {'HIGH' if initial_state else 'LOW'}")
                
            except Exception as e:
                logger.error(f"Error setting up pin {pin}: {e}")
                
        logger.info(f"GPIO pins {self.pins} initialized with pull-up resistors")
    
    def pin_change_callback(self, channel):
        """Callback function for pin state changes"""
        current_time = time.time()
        
        # Additional software debouncing
        time_since_last = (current_time - self.last_interrupt_time.get(channel, 0)) * 1000
        if time_since_last < self.debounce_time:
            return
            
        self.last_interrupt_time[channel] = current_time
        
        # Read the new state
        new_state = GPIO.input(channel)
        old_state = self.pin_states.get(channel, new_state)
        
        # Only process if state actually changed
        if new_state != old_state:
            # Calculate time difference
            time_diff_ms = (current_time - self.pin_timestamps[channel]) * 1000
            
            # Log the change
            state_str = 'HIGH' if new_state else 'LOW'
            old_state_str = 'HIGH' if old_state else 'LOW'
            logger.info(f"Pin {channel} changed from {old_state_str} to {state_str}, was {old_state_str} for {time_diff_ms:.2f}ms")
            
            # Send data to server in a separate thread to avoid blocking
            threading.Thread(target=self.send_data_to_server, 
                           args=(channel, new_state, time_diff_ms),
                           daemon=True).start()
            
            # Update state and timestamp
            self.pin_states[channel] = new_state
            self.pin_timestamps[channel] = current_time
    
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
            # Log connection attempt with specific details
            logger.info(f"Attempting to connect to server at {self.server_ip}:{self.server_port}")
            
            # Create socket with timeout
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)  # 5 second timeout
            
            # Connect to server
            s.connect((self.server_ip, self.server_port))
            
            # Log successful connection
            logger.info(f"Connected to server at {self.server_ip}:{self.server_port}")
            
            # Send data with proper encoding
            json_data = json.dumps(data)
            logger.debug(f"Sending data: {json_data}")
            
            s.sendall(json_data.encode('utf-8'))
            
            # Wait for response with timeout
            response = s.recv(1024).decode('utf-8')
            
            if response == 'OK':
                logger.info(f"Data for pin {pin} sent successfully")
            else:
                logger.warning(f"Server returned unexpected response: {response}")
            
            # Close the connection
            s.close()
                    
        except ConnectionRefusedError:
            logger.error(f"Connection refused by server {self.server_ip}:{self.server_port}. Check if server is running and the IP/port are correct.")
        except socket.timeout:
            logger.error(f"Connection to server {self.server_ip}:{self.server_port} timed out. Check network connectivity.")
        except socket.gaierror:
            logger.error(f"Address-related error connecting to server {self.server_ip}:{self.server_port}. Check if the IP address is valid.")
        except Exception as e:
            logger.error(f"Error sending data to server: {e}", exc_info=True)
    
    def signal_handler(self, sig, frame):
        """Handle termination signals gracefully"""
        logger.info("Shutdown signal received, cleaning up...")
        self.running = False
        self.cleanup()
        sys.exit(0)
    
    def cleanup(self):
        """Clean up GPIO resources"""
        try:
            # Remove event detection for all pins
            for pin in self.pins:
                GPIO.remove_event_detect(pin)
            
            # Clean up GPIO
            GPIO.cleanup()
            logger.info("GPIO resources cleaned up")
        except Exception as e:
            logger.error(f"Error during GPIO cleanup: {e}")
    
    def run(self):
        """Main loop to keep the program running"""
        logger.info(f"GPIO Monitor started on {self.device_name}")
        logger.info(f"Monitoring pins: {self.pins}")
        logger.info(f"Will connect to server: {self.server_ip}:{self.server_port}")
        
        # Test server connection at startup
        self.test_server_connection()
        
        try:
            # Keep the program running
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Program interrupted by user")
        finally:
            self.cleanup()
            
    def test_server_connection(self):
        """Test connection to the server at startup"""
        try:
            logger.info(f"Testing connection to server at {self.server_ip}:{self.server_port}...")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)  # 5 second timeout
            s.connect((self.server_ip, self.server_port))
            s.close()
            logger.info("Server connection test successful!")
        except ConnectionRefusedError:
            logger.error(f"Connection refused by server {self.server_ip}:{self.server_port}. Check if server is running and the IP/port are correct.")
        except socket.timeout:
            logger.error(f"Connection to server {self.server_ip}:{self.server_port} timed out. Check network connectivity.")
        except socket.gaierror:
            logger.error(f"Address-related error connecting to server {self.server_ip}:{self.server_port}. Check if the IP address is valid.")
        except Exception as e:
            logger.error(f"Error testing connection to server: {e}", exc_info=True)

if __name__ == "__main__":
    monitor = GPIOMonitor()
    monitor.run()