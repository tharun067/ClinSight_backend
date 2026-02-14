#!/bin/bash

# ClinSight Backend - Setup and Run Script

echo "================================"
echo "ClinSight Backend Setup"
echo "================================"

# Check Python version
python_version=$(python3 --version 2>&1 | awk '{print $2}')
echo "Python version: $python_version"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate || . venv/Scripts/activate

# Install dependencies
echo "Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Create .env if it doesn't exist
if [ ! -f ".env" ]; then
    echo "Creating .env file from template..."
    cp .env.example .env
    echo "Please edit .env file with your credentials before running!"
    echo "At minimum, set:"
    echo "  - POSTGRES_PASSWORD"
    echo "  - SECRET_KEY (generate with: openssl rand -hex 32)"
    echo "  - GOOGLE_API_KEY (if using Gemini)"
fi

# Create required directories
echo "Creating required directories..."
mkdir -p data/faiss_index
mkdir -p uploads

echo "================================"
echo "Setup Complete!"
echo "================================"
echo ""
echo "Next steps:"
echo "1. Edit .env file with your credentials"
echo "2. Start PostgreSQL database"
echo "3. Run: python -m src.main"
echo ""
echo "Or use: uvicorn src.main:app --reload --host 0.0.0.0 --port 8000"
echo ""
echo "API Docs will be available at: http://localhost:8000/api/docs"
echo "================================"
