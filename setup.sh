#!/bin/bash
# Setup script for YouTube Summarizer skill

set -e

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SKILL_DIR/venv"

echo "🔧 Setting up YouTube Summarizer skill..."

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found. Please install Python 3.9+"
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
echo "✅ Python version: $PYTHON_VERSION"

# Check/install yt-dlp
if ! command -v yt-dlp &> /dev/null; then
    echo "📦 Installing yt-dlp..."
    if command -v brew &> /dev/null; then
        brew install yt-dlp
    else
        echo "❌ Homebrew not found. Please install yt-dlp manually:"
        echo "   brew install yt-dlp"
        exit 1
    fi
else
    echo "✅ yt-dlp found"
fi

# Create virtual environment
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

# Activate and install dependencies
echo "📦 Installing Python dependencies..."
source "$VENV_DIR/bin/activate"
pip install --quiet youtube-transcript-api requests innertube

echo ""
echo "✅ Setup complete!"
echo ""
echo "Usage:"
echo "  youtube-summarizer --url 'https://youtube.com/watch?v=VIDEO_ID'"
echo "  youtube-summarizer --channel 'UC_x5XG1OV2P6uZZ5FSM9Ttw' --hours 24"
echo "  youtube-summarizer --config channels.json --daily --output /tmp/youtube_summary.json"
echo ""
echo "Add to PATH:"
echo "  export PATH=\"\$PATH:$HOME/.openclaw/skills/youtube-summarizer\""
