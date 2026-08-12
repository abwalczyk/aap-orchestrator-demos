#!/usr/bin/env python3
"""
Gemini API Proxy Server
Forwards requests from AO to internal Gemini API through VPN
"""
import logging
from flask import Flask, request, Response
import requests

app = Flask(__name__)

# Configuration
# Using IP address instead of hostname to avoid DNS resolution issues
# gemini--apicast-production.apps.int.stc.ai.prod.us-east-1.aws.paas.redhat.com resolves to 10.31.83.111
TARGET_BASE_URL = "https://10.31.83.111"
TARGET_HOST_HEADER = "gemini--apicast-production.apps.int.stc.ai.prod.us-east-1.aws.paas.redhat.com"
PROXY_PORT = 8888
PROXY_HOST = "0.0.0.0"  # Listen on all interfaces

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@app.route('/', defaults={'path': ''}, methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'])
@app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'])
def proxy(path):
    """Forward all requests to the target Gemini API"""

    # Build the target URL
    target_url = f"{TARGET_BASE_URL}/{path}"
    if request.query_string:
        target_url += f"?{request.query_string.decode('utf-8')}"

    # Log the request
    logger.info(f"{request.method} {path} -> {target_url}")

    # Forward headers (replace Host header with target hostname)
    headers = {key: value for key, value in request.headers if key.lower() != 'host'}
    headers['Host'] = TARGET_HOST_HEADER

    # Log API key if present (first 8 chars only)
    auth_header = headers.get('Authorization', '')
    if auth_header:
        logger.info(f"Authorization: {auth_header[:20]}...")

    try:
        # Make the request to the target API
        resp = requests.request(
            method=request.method,
            url=target_url,
            headers=headers,
            data=request.get_data(),
            cookies=request.cookies,
            allow_redirects=False,
            timeout=120,
            verify=False,  # Disable SSL verification for internal API with self-signed cert
        )

        # Log the response
        logger.info(f"Response: {resp.status_code}")
        if resp.status_code >= 400:
            logger.error(f"Response body: {resp.text[:500]}")

        # Build response headers (exclude hop-by-hop headers and content-encoding)
        # We exclude content-encoding because requests library auto-decompresses,
        # so we're returning decompressed content but headers might still say gzip
        excluded_headers = ['connection', 'keep-alive', 'proxy-authenticate',
                           'proxy-authorization', 'te', 'trailers',
                           'transfer-encoding', 'upgrade', 'content-encoding']
        response_headers = [(name, value) for (name, value) in resp.raw.headers.items()
                           if name.lower() not in excluded_headers]

        # Return the response
        return Response(resp.content, resp.status_code, response_headers)

    except requests.exceptions.RequestException as e:
        logger.error(f"Error forwarding request: {e}")
        return Response(
            f"Proxy error: {str(e)}",
            status=502,
            content_type='text/plain'
        )


@app.route('/health')
def health():
    """Health check endpoint"""
    return {'status': 'ok', 'proxy': 'gemini', 'target': f"{TARGET_BASE_URL} (Host: {TARGET_HOST_HEADER})"}


if __name__ == '__main__':
    logger.info(f"Starting Gemini API Proxy")
    logger.info(f"Listening on: http://{PROXY_HOST}:{PROXY_PORT}")
    logger.info(f"Forwarding to: {TARGET_BASE_URL} (Host: {TARGET_HOST_HEADER})")
    logger.info(f"Press Ctrl+C to stop")

    app.run(
        host=PROXY_HOST,
        port=PROXY_PORT,
        debug=False,
        threaded=True
    )
