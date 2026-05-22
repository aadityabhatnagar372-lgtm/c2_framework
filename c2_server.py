#!/usr/bin/env python3
import sys
import os

# Add relevant directories to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), 'server')))

from server.app import app

def main():
    print("--- Red Team C2 Server Starting ---")
    print("[*] Dashboard available via admin API endpoints")
    print("[*] Default port: 5001")
    
    # Run the Flask app on Port 5001 to avoid macOS AirPlay conflicts
    app.run(host="0.0.0.0", port=5001)

if __name__ == "__main__":
    main()
