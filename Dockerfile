FROM mcr.microsoft.com/devcontainers/python:3.11

# Set working directory
WORKDIR /workspace

# Set environment variable
ENV CAMERA_MODEL=UVC_G4_DOME

# Install system dependencies
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ffmpeg \
    netcat-openbsd \
    iputils-ping \
    curl \
    ca-certificates \
    tar \
    tshark \
    openssh-client && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Default command
CMD ["bash"]
