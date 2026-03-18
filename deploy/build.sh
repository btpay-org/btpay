#!/bin/bash
# Build script for managed platform deploys (Render, Railway, etc.)
set -e
pip install -r requirements.lock
python -m btpay.generate_build_info
