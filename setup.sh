#!/bin/bash

# ==============================================================================
# 🚀 SYNTHETA HUB SETUP SCRIPT
# ==============================================================================

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=========================================="
echo -e "   SYNTHETA HUB AUTOMATED SETUP"
echo -e "==========================================${NC}"

# 1. Check Prerequisites
echo -e "\n${YELLOW}[1/5] Checking Prerequisites...${NC}"

if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python 3 is not installed. Please install Python 3.10+${NC}"
    exit 1
fi

if ! command -v go &> /dev/null; then
    echo -e "${RED}❌ Go is not installed. Please install Go 1.20+${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Python and Go are installed.${NC}"

# 2. Setup Virtual Environments
echo -e "\n${YELLOW}[2/5] Setting up Virtual Environments...${NC}"

if [ ! -d "venv" ]; then
    echo "Creating primary venv..."
    python3 -m venv venv
fi

if [ ! -d "venv-audio" ]; then
    echo "Creating audio-specialized venv..."
    python3 -m venv venv-audio
fi

echo -e "${GREEN}✅ Virtual environments created.${NC}"

# 3. Install Dependencies
echo -e "\n${YELLOW}[3/5] Installing Dependencies...${NC}"

echo "Installing Brain dependencies (venv)..."
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

echo "Installing Audio dependencies (venv-audio)..."
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements-audio.txt

echo -e "${GREEN}✅ Dependencies installed.${NC}"

# 4. Build Go Bridge
echo -e "\n${YELLOW}[4/5] Compiling Audio Bridge (Go)...${NC}"

cd go
if [ -f "go.mod" ]; then
    go build -o syntheta-hub ./cmd
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ Go Bridge compiled successfully.${NC}"
    else
        echo -e "${RED}❌ Go compilation failed.${NC}"
        exit 1
    fi
else
    echo -e "${RED}❌ go.mod not found in go/ directory.${NC}"
    exit 1
fi
cd ..

# 5. Configuration
echo -e "\n${YELLOW}[5/5] Finalizing Configuration...${NC}"

if [ ! -f ".env" ]; then
    echo "Creating .env from template..."
    cp .env.example .env
    echo -e "${YELLOW}⚠️  Please edit .env with your Home Assistant details.${NC}"
else
    echo ".env already exists, skipping."
fi

# Set executable permissions for the launcher
chmod +x run_syntheta.sh

echo -e "\n${GREEN}=========================================="
echo -e "   🎉 SETUP COMPLETE!"
echo -e "=========================================="
echo -e "To start Syntheta Hub, run:"
echo -e "   ./run_syntheta.sh"
echo -e "==========================================${NC}"
