#!/bin/bash
# Setup macvlan shim to allow communication between host and macvlan containers
# This allows the unifi-cam-proxy container (192.168.1.40) to reach the Docker host (192.168.1.3)

INTERFACE="enp0s31f6"
SHIM_NAME="mac-shim"
HOST_IP="192.168.1.3"
CONTAINER_IP="192.168.1.40"

# Check if shim already exists
if ip link show "$SHIM_NAME" &> /dev/null; then
    echo "Macvlan shim '$SHIM_NAME' already exists, removing..."
    ip link delete "$SHIM_NAME" 2>/dev/null || true
fi

echo "Creating macvlan shim interface..."
ip link add "$SHIM_NAME" link "$INTERFACE" type macvlan mode bridge

# Assign the host's IP to the shim (creates alias for host on macvlan network)
ip addr add "$HOST_IP/32" dev "$SHIM_NAME"
ip link set "$SHIM_NAME" up

# Add route to container via shim
ip route add "$CONTAINER_IP/32" dev "$SHIM_NAME" 2>/dev/null || true

echo "Macvlan shim created successfully!"
echo "Routes configured:"
ip route | grep "$SHIM_NAME"
