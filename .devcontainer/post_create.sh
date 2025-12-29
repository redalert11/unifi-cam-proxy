#!/usr/bin/env bash
set -euo pipefail

echo "[devcontainer] apt-get update..."
sudo apt-get update

echo "[devcontainer] installing packages..."
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ffmpeg \
  iputils-ping \
  curl \
  ca-certificates \
  tar \
  tshark \
  openssh-client

echo "[devcontainer] installing go2rtc binary..."
sudo curl -fsSL https://github.com/AlexxIT/go2rtc/releases/latest/download/go2rtc_linux_amd64 \
  -o /usr/local/bin/go2rtc
sudo chmod +x /usr/local/bin/go2rtc

echo "[devcontainer] installing Python requirements..."
pip install -r requirements.txt

echo "[devcontainer] post-create complete."
