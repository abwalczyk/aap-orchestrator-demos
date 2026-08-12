# Gemini API VPN Proxy Setup Guide

## Overview

**Problem**: Ansible Orchestrator (AO) running on AWS needs to access an internal Gemini API that's only reachable through Red Hat VPN, but AO doesn't have VPN access.

**Solution**: Create a proxy on a VPN-connected laptop that forwards requests from AO → Laptop (via SSH tunnel) → VPN → Gemini API.

## Architecture

```
AO Instance (AWS)          Your Laptop (VPN)              Internal Gemini API
┌─────────────┐           ┌──────────────┐              ┌─────────────────┐
│             │           │              │              │                 │
│  AO Node    │──────────>│  Gemini      │─────────────>│  gemini--       │
│             │  SSH      │  Proxy       │  VPN         │  apicast-       │
│  port 9999  │  Tunnel   │  port 8888   │  Connected   │  production...  │
└─────────────┘           └──────────────┘              └─────────────────┘
     ^                           ^
     │                           │
  Container uses          Listens on localhost
  host.containers.        and forwards to
  internal:9999          10.31.83.111 with
                         correct Host header
```

## Components

1. **Gemini Proxy** (`gemini_proxy.py`) - Flask app that forwards HTTP requests
2. **SSH Reverse Tunnel** - Makes laptop's proxy accessible from AO instance
3. **AO Credential** - Configured to use the tunneled proxy endpoint

---

## Step-by-Step Setup Instructions

### Prerequisites

- **VPN access** to Red Hat internal network
- **SSH access** to the AO instance (EC2)
- **Python 3** installed on your laptop
- **SSH key** for accessing the AO instance

### Step 1: Install Dependencies

```bash
pip install flask requests
```

### Step 2: Connect to VPN

Connect to the Red Hat VPN. Verify you're connected:

```bash
ifconfig | grep "inet 10\."
# Should show something like: inet 10.22.88.79
```

Verify DNS resolution works for internal Gemini API:

```bash
nslookup gemini--apicast-production.apps.int.stc.ai.prod.us-east-1.aws.paas.redhat.com 10.11.5.19
# Should resolve to 10.31.83.111 or similar
```

### Step 3: Start the Proxy

The proxy script is located at `gemini_proxy.py` in this repository.

```bash
python3 gemini_proxy.py
```

You should see:
```
Starting Gemini API Proxy
Listening on: http://0.0.0.0:8888
Forwarding to: https://10.31.83.111 (Host: gemini--apicast-production...)
```

Test the proxy locally:
```bash
curl http://localhost:8888/health
# Should return: {"status": "ok", "proxy": "gemini", ...}
```

### Step 4: Create SSH Reverse Tunnel

In a **new terminal** (keep the proxy running), create the SSH tunnel to your AO instance:

```bash
ssh -i ~/.ssh/id_ed25519 -R 9999:localhost:8888 -N -f ec2-user@YOUR_AO_INSTANCE_IP
```

**Explanation:**
- `-R 9999:localhost:8888` - Reverse tunnel: AO's port 9999 → your laptop's port 8888
- `-N` - Don't execute a remote command
- `-f` - Run in background
- Replace `YOUR_AO_INSTANCE_IP` with your AO instance IP address

**Example:**
```bash
ssh -i ~/.ssh/id_ed25519 -R 9999:localhost:8888 -N -f ec2-user@100.25.10.220
```

Verify the tunnel is running:
```bash
ps aux | grep "ssh.*9999" | grep -v grep
```

### Step 5: Verify Tunnel from AO Instance

SSH into the AO instance and test:

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@YOUR_AO_INSTANCE_IP

# On the AO instance - check your container name with: podman ps
podman exec CONTAINER_NAME curl -s http://host.containers.internal:9999/health
```

**Example:**
```bash
podman exec nexus2_nexus_1 curl -s http://host.containers.internal:9999/health
```

Should return: `{"status": "ok", "proxy": "gemini", ...}`

### Step 6: Configure AO Credential

1. Log into AO UI at `http://YOUR_AO_INSTANCE_IP:8080`
2. Navigate to **Settings** → **Credentials** (or wherever LLM providers are configured)
3. Create a new **Custom** (OpenAI-compatible) credential:
   - **Provider Type**: Custom / OpenAI-compatible
   - **Base URL**: `http://host.containers.internal:9999/v1beta/openai`
   - **API Key**: Your Gemini API key
4. Click **Test Connection**

You should see: ✅ **Successfully connected. Discovered 53 models.**

---

## Important Notes

### Why Each Component is Necessary

1. **IP Address instead of Hostname**: The internal DNS name only resolves when using internal DNS servers (10.11.5.19), but the proxy uses your system's default DNS which doesn't have access. Using the IP address (10.31.83.111) bypasses DNS.

2. **Host Header**: The Gemini API uses virtual hosting, so we must send the correct `Host` header even when connecting via IP address.

3. **Content-Encoding Exclusion**: The `requests` library auto-decompresses gzip responses, but was forwarding `Content-Encoding: gzip` headers. This caused AO's `httpx` client to try decompressing already-decompressed data, resulting in `zlib.error`.

4. **SSL Verification Disabled**: The internal API uses self-signed certificates. In production, you might want to add the CA certificate instead.

5. **Port 9999**: Using `host.containers.internal:9999` allows the containerized AO app to reach the SSH tunnel endpoint on the host machine.

### Configuration Values to Update

Different users/instances will need to update these values:

| Value | Where to Find | Example |
|-------|--------------|---------|
| **AO Instance IP** | Your AWS EC2 instance | `100.25.10.220` |
| **SSH Key Path** | Your SSH key location | `~/.ssh/id_ed25519` |
| **Container Name** | `ssh AO_IP "podman ps"` | `nexus2_nexus_1` |
| **API Key** | Your Gemini API credentials | Contact your administrator |
| **Gemini API IP** | `nslookup` via VPN (see Step 2) | `10.31.83.111` |

---

## Troubleshooting

### VPN disconnected

Symptoms: Proxy logs show connection timeouts

**Solution:**
1. Reconnect to VPN
2. Restart the SSH tunnel (it may die when VPN disconnects):
```bash
# Kill old tunnel
pkill -f "ssh.*9999.*YOUR_AO_IP"
# Restart
ssh -i ~/.ssh/id_ed25519 -R 9999:localhost:8888 -N -f ec2-user@YOUR_AO_IP
```

### SSH tunnel died

Symptoms: AO can't reach the proxy, no logs in proxy

**Check if tunnel is running:**
```bash
ps aux | grep "ssh.*9999" | grep -v grep
```

**Restart tunnel:**
```bash
ssh -i ~/.ssh/id_ed25519 -R 9999:localhost:8888 -N -f ec2-user@YOUR_AO_IP
```

### Proxy not responding

**Check if running:**
```bash
ps aux | grep gemini_proxy | grep -v grep
```

**Restart if needed:**
```bash
# Kill old process
pkill -f gemini_proxy
# Start new one
python3 gemini_proxy.py
```

### Connection test fails with "Request failed unexpectedly"

This was the original issue we solved. Common causes:

1. **Gzip decompression error**: Make sure you're using the updated `gemini_proxy.py` that excludes `content-encoding` header
2. **VPN not connected**: Verify with `ifconfig | grep "inet 10\."`
3. **Wrong API endpoint**: Ensure Base URL is `http://host.containers.internal:9999/v1beta/openai`
4. **DNS resolution**: The proxy uses IP address to avoid DNS issues

### Checking Logs

**Proxy logs** (if running in foreground):
Shows all HTTP requests and responses

**Proxy logs** (if running in background):
```bash
tail -f /tmp/gemini_proxy.log
```

**AO backend logs:**
```bash
ssh ec2-user@YOUR_AO_IP "podman logs --tail 100 CONTAINER_NAME"
```

---

## Production Considerations

For production use, consider:

1. **Auto-restart**: Use systemd or supervisor to keep the proxy running
2. **Monitoring**: Add health checks and alerting
3. **Logging**: Send logs to a centralized system
4. **SSH Tunnel Keepalive**: Add to `~/.ssh/config`:
   ```
   Host your-ao-instance
       ServerAliveInterval 60
       ServerAliveCountMax 3
   ```
5. **SSL Certificates**: Use proper CA certificates instead of `verify=False`
6. **Dedicated proxy server**: Instead of running on a laptop, deploy the proxy on a stable server with VPN access

---

## Quick Reference

**Start everything:**
```bash
# Terminal 1: Start proxy
python3 gemini_proxy.py

# Terminal 2: Create SSH tunnel
ssh -i ~/.ssh/id_ed25519 -R 9999:localhost:8888 -N -f ec2-user@YOUR_AO_IP
```

**Stop everything:**
```bash
# Stop proxy
pkill -f gemini_proxy

# Stop SSH tunnel
pkill -f "ssh.*9999.*YOUR_AO_IP"
```

**Verify everything is running:**
```bash
# Check proxy
ps aux | grep gemini_proxy | grep -v grep
curl http://localhost:8888/health

# Check tunnel
ps aux | grep "ssh.*9999" | grep -v grep

# Check from AO (SSH into instance first)
podman exec CONTAINER_NAME curl -s http://host.containers.internal:9999/health
```
