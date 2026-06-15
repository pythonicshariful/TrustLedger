#!/bin/bash

# Shopno Bilash - Mac Startup Script
# This script installs dependencies and launches the application in GUI mode.

echo "Starting Shopno Bilash for Mac..."

# Ensure we are in the correct directory
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# Check for Python 3
if ! command -v python3 &> /dev/null
then
    echo "Python 3 is not installed. Please install it from python.org."
    exit
fi

# Create a virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install requirements
echo "Checking dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Run the app
echo "Launching GUI..."
python run_gui.py
