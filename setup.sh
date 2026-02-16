#!/bin/bash
# Setup script for BAR Unit Sync

echo "=================================="
echo "BAR Unit Sync - Setup"
echo "=================================="
echo ""

# Check Python version
echo "Checking Python version..."
python_version=$(python3 --version 2>&1 | grep -oP '\d+\.\d+' | head -1)
required_version="3.7"

if [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" != "$required_version" ]; then
    echo "❌ Error: Python 3.7 or higher required. Found: $python_version"
    exit 1
fi
echo "✅ Python version: $python_version"
echo ""

# Create virtual environment
echo "Creating virtual environment..."
if [ -d "venv" ]; then
    echo "⚠️  Virtual environment already exists"
else
    python3 -m venv venv
    echo "✅ Virtual environment created"
fi
echo ""

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate
echo "✅ Virtual environment activated"
echo ""

# Install dependencies
echo "Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt
echo "✅ Dependencies installed"
echo ""

# Create .env file if it doesn't exist
if [ ! -f ".env" ]; then
    echo "Creating .env file from template..."
    cp .env.example .env
    echo "✅ .env file created"
    echo ""
    echo "⚠️  IMPORTANT: Edit .env and add your WEBFLOW_API_TOKEN"
    echo "   Get your token from: https://webflow.com/dashboard/account/apps"
else
    echo "✅ .env file already exists"
fi
echo ""

# Run test
echo "Running tests..."
python test_sync.py
echo ""

echo "=================================="
echo "Setup Complete! 🎉"
echo "=================================="
echo ""
echo "Next steps:"
echo "1. Edit .env and add your WEBFLOW_API_TOKEN"
echo "2. Run a dry-run: python sync_units_github_to_webflow.py --dry-run"
echo "3. Run the sync: python sync_units_github_to_webflow.py"
echo ""
echo "For more information, see README.md"
echo ""
