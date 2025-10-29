#!/usr/bin/env python3
"""
YouTube Stream Updater
Fetches YouTube stream URLs and updates m3u8 playlists
"""

import json
import os
import sys
import argparse
import time
import re
try:
    import cloudscraper
    CLOUDSCRAPER_AVAILABLE = True
except ImportError:
    import requests
    CLOUDSCRAPER_AVAILABLE = False
    print("⚠ Warning: cloudscraper not installed. Install with: pip install cloudscraper")
    print("⚠ Falling back to basic requests (JS challenges may not work)")
from pathlib import Path
from urllib.parse import urlencode, urlparse

# Configuration
ENDPOINT = os.environ.get('ENDPOINT', 'https://your-endpoint.com')
FOLDER_NAME = os.environ.get('FOLDER_NAME', 'streams')
TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds

# Create a session for connection pooling
if CLOUDSCRAPER_AVAILABLE:
    # Use cloudscraper to handle JavaScript challenges
    scraper = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'mobile': False
        },
        delay=10
    )
    session = scraper
    print("✓ Using cloudscraper for JavaScript challenge bypass")
else:
    # Fallback to regular requests
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=10,
        pool_maxsize=20,
        max_retries=0
    )
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    print("⚠ Using basic requests (limited challenge support)")


def load_config(config_path):
    """Load configuration from JSON file"""
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        print(f"✓ Loaded {len(config)} stream(s) from config")
        return config
    except FileNotFoundError:
        print(f"✗ Config file not found: {config_path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"✗ Invalid JSON in config file: {e}")
        sys.exit(1)


def fetch_stream_url_with_retry(stream_config):
    """Fetch stream URL with retry logic"""
    slug = stream_config['slug']
    last_error_type = None
    
    for attempt in range(1, MAX_RETRIES + 1):
        if attempt > 1:
            delay = RETRY_DELAY * (2 ** (attempt - 2))  # Exponential backoff
            print(f"  → Retry {attempt}/{MAX_RETRIES} after {delay}s delay...")
            time.sleep(delay)
        
        result, error_type = fetch_stream_url(stream_config)
        if result is not None:
            return result, None
        
        last_error_type = error_type
        if attempt < MAX_RETRIES:
            print(f"  → Attempt {attempt} failed, will retry...")
    
    print(f"  ✗ All {MAX_RETRIES} attempts failed for {slug}")
    return None, last_error_type


def fetch_stream_url(stream_config):
    """Fetch the YouTube stream m3u8 URL"""
    stream_type = stream_config.get('type', 'channel')
    stream_id = stream_config['id']
    slug = stream_config['slug']
    
    # Build query string based on type
    if stream_type == 'video':
        query_param = 'v'
    elif stream_type == 'channel':
        query_param = 'c'
    else:
        print(f"✗ Unknown type '{stream_type}' for {slug}")
        return None
    
    # Build request URL
    url = f"{ENDPOINT}/yt.php?{query_param}={stream_id}"
    
    print(f"  Fetching: {url}")
    
    try:
        # Follow redirects and get final m3u8 URL
        print(f"  → Sending GET request (timeout={TIMEOUT}s)...")
        
        # Cloudscraper handles JS challenges automatically
        response = session.get(
            url, 
            timeout=TIMEOUT, 
            allow_redirects=True,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': '*/*',
                'Connection': 'keep-alive'
            }
        )
        
        # Log response details
        print(f"  → Status Code: {response.status_code}")
        print(f"  → Content Type: {response.headers.get('Content-Type', 'N/A')}")
        print(f"  → Content Length: {len(response.content)} bytes")
        
        # Log redirect chain if any
        if response.history:
            print(f"  → Redirects: {len(response.history)} redirect(s)")
            for i, hist_resp in enumerate(response.history, 1):
                print(f"    {i}. {hist_resp.status_code} → {hist_resp.url}")
            print(f"  → Final URL: {response.url}")
        
        response.raise_for_status()
        
        # Check if we still got a challenge page (shouldn't happen with cloudscraper)
        if not CLOUDSCRAPER_AVAILABLE:
            redirect_url = solve_js_challenge(response, slug)
            if redirect_url:
                print(f"  → Manually following extracted redirect URL...")
                
                # Make second request to the actual m3u8 URL
                response2 = session.get(
                    redirect_url,
                    timeout=TIMEOUT,
                    allow_redirects=True,
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                        'Accept': '*/*',
                        'Connection': 'keep-alive',
                        'Referer': url
                    }
                )
                
                print(f"  → Second request status: {response2.status_code}")
                print(f"  → Content Length: {len(response2.content)} bytes")
                
                response2.raise_for_status()
                response = response2  # Use the second response
        
        # Check if content looks like m3u8
        content_preview = response.text[:200] if len(response.text) > 200 else response.text
        if '#EXTM3U' in content_preview:
            print(f"  ✓ Valid m3u8 content detected")
        elif '<html' in content_preview.lower():
            print(f"  ✗ Error: Received HTML instead of m3u8")
            print(f"  → Content preview: {content_preview[:150]}...")
            return None, 'HTMLResponse'
        else:
            print(f"  ⚠ Warning: Content doesn't start with #EXTM3U")
            print(f"  → Content preview: {content_preview[:100]}...")
        
        # The response should be the m3u8 content
        return response.text, None
        
    except Exception as e:
        # Handle both cloudscraper and requests exceptions
        error_module = type(e).__module__
        
        if 'timeout' in str(e).lower() or 'timed out' in str(e).lower():
            error_type = 'Timeout'
            print(f"✗ Timeout error for {slug}: Request exceeded {TIMEOUT}s")
            print(f"  → Error details: {e}")
            return None, error_type
        elif 'connection' in str(e).lower() or 'remote' in str(e).lower():
            error_type = 'ConnectionError'
            print(f"✗ Connection error for {slug}: {type(e).__name__}")
            print(f"  → Error details: {e}")
            print(f"  → URL attempted: {url}")
            return None, error_type
        elif hasattr(e, 'response') and e.response is not None:
            error_type = f'HTTPError-{e.response.status_code}'
            print(f"✗ HTTP error for {slug}: {e.response.status_code}")
            print(f"  → Response: {e.response.text[:200] if e.response.text else 'No content'}")
            return None, error_type
        else:
            error_type = type(e).__name__
            print(f"✗ Request error for {slug}: {type(e).__name__}")
            print(f"  → Error details: {e}")
            import traceback
            print(f"  → Traceback: {traceback.format_exc()}")
            return None, error_type


def reverse_hls_quality(m3u8_content):
    """
    Reverse the quality order in m3u8 playlist
    High quality streams will appear first
    """
    lines = m3u8_content.split('\n')
    
    # Find all stream definitions (lines starting with #EXT-X-STREAM-INF)
    stream_blocks = []
    current_block = []
    
    for line in lines:
        if line.startswith('#EXTM3U'):
            # Keep header
            continue
        elif line.startswith('#EXT-X-STREAM-INF'):
            if current_block:
                stream_blocks.append(current_block)
            current_block = [line]
        elif current_block:
            current_block.append(line)
            if line and not line.startswith('#'):
                # End of this stream block
                stream_blocks.append(current_block)
                current_block = []
    
    # Add any remaining block
    if current_block:
        stream_blocks.append(current_block)
    
    # Reverse the order (high quality first)
    stream_blocks.reverse()
    
    # Reconstruct m3u8
    result = ['#EXTM3U']
    for block in stream_blocks:
        result.extend(block)
    
    return '\n'.join(result)


def get_output_path(stream_config):
    """Get the output file path for a stream"""
    slug = stream_config['slug']
    subfolder = stream_config.get('subfolder', '')
    
    # Build output path
    if subfolder:
        output_dir = Path(FOLDER_NAME) / subfolder
    else:
        output_dir = Path(FOLDER_NAME)
    
    return output_dir / f"{slug}.m3u8"


def delete_old_file(stream_config):
    """Delete the old m3u8 file if it exists"""
    output_file = get_output_path(stream_config)
    
    try:
        if output_file.exists():
            output_file.unlink()
            print(f"  ⚠ Deleted old file: {output_file}")
            return True
    except Exception as e:
        print(f"  ⚠ Could not delete old file {output_file}: {e}")
        return False
    
    return False


def extract_redirect_url(html_content):
    """Extract redirect URL from JavaScript challenge page"""
    # Look for location.href pattern
    redirect_pattern = r'location\.href\s*=\s*["\']([^"\']+)["\']'
    match = re.search(redirect_pattern, html_content)
    
    if match:
        return match.group(1)
    
    return None


def solve_js_challenge(response, slug):
    """Detect and solve JavaScript challenge"""
    content = response.text
    
    # Check if this is a JS challenge page
    if '<script type="text/javascript" src="/aes.js"' in content or 'slowAES.decrypt' in content:
        print(f"  ⚠ JavaScript challenge detected")
        
        # Extract the redirect URL from the challenge
        redirect_url = extract_redirect_url(content)
        
        if redirect_url:
            print(f"  → Extracted redirect URL: {redirect_url}")
            return redirect_url
        else:
            print(f"  ✗ Could not extract redirect URL from challenge")
            # Try to extract cookie value for debugging
            cookie_pattern = r'document\.cookie\s*=\s*"([^"]+)"'
            cookie_match = re.search(cookie_pattern, content)
            if cookie_match:
                print(f"  → Cookie pattern found: {cookie_match.group(1)[:50]}...")
            return None
    
    return None


def save_stream(stream_config, m3u8_content):
    """Save m3u8 content to file"""
    slug = stream_config['slug']
    
    # Get output file path
    output_file = get_output_path(stream_config)
    output_dir = output_file.parent
    
    # Create directory if it doesn't exist
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Reverse quality order
    reversed_content = reverse_hls_quality(m3u8_content)
    
    # Write to file
    try:
        with open(output_file, 'w') as f:
            f.write(reversed_content)
        print(f"✓ Saved: {output_file}")
        return True
    except Exception as e:
        print(f"✗ Error saving {output_file}: {e}")
        return False


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description='Update YouTube stream m3u8 playlists',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python update_streams.py config.json
  python update_streams.py streams/live.json
  python update_streams.py config1.json config2.json
  python update_streams.py config.json --retries 5 --timeout 60
        """
    )
    
    parser.add_argument(
        'config_files',
        nargs='+',
        help='Configuration file(s) to process'
    )
    
    parser.add_argument(
        '--endpoint',
        default=ENDPOINT,
        help=f'API endpoint URL (default: {ENDPOINT})'
    )
    
    parser.add_argument(
        '--folder',
        default=FOLDER_NAME,
        help=f'Output folder name (default: {FOLDER_NAME})'
    )
    
    parser.add_argument(
        '--timeout',
        type=int,
        default=TIMEOUT,
        help=f'Request timeout in seconds (default: {TIMEOUT})'
    )
    
    parser.add_argument(
        '--retries',
        type=int,
        default=MAX_RETRIES,
        help=f'Maximum retry attempts (default: {MAX_RETRIES})'
    )
    
    parser.add_argument(
        '--retry-delay',
        type=int,
        default=RETRY_DELAY,
        help=f'Initial retry delay in seconds (default: {RETRY_DELAY})'
    )
    
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose debug output'
    )
    
    return parser.parse_args()


def main():
    """Main execution function"""
    args = parse_arguments()
    
    # Update globals with command line arguments
    global ENDPOINT, FOLDER_NAME, TIMEOUT, MAX_RETRIES, RETRY_DELAY
    ENDPOINT = args.endpoint
    FOLDER_NAME = args.folder
    TIMEOUT = args.timeout
    MAX_RETRIES = args.retries
    RETRY_DELAY = args.retry_delay
    
    print("=" * 50)
    print("YouTube Stream Updater")
    print("=" * 50)
    print(f"Endpoint: {ENDPOINT}")
    print(f"Output folder: {FOLDER_NAME}")
    print(f"Config files: {', '.join(args.config_files)}")
    print(f"Timeout: {TIMEOUT}s")
    print(f"Max retries: {MAX_RETRIES}")
    print(f"Retry delay: {RETRY_DELAY}s")
    print(f"Verbose: {args.verbose}")
    print("=" * 50)
    
    total_success = 0
    total_fail = 0
    error_summary = {}  # Track error types
    
    # Process each config file
    for config_file in args.config_files:
        print(f"\n📄 Processing config: {config_file}")
        print("-" * 50)
        
        # Load configuration
        streams = load_config(config_file)
        
        # Process each stream
        for i, stream in enumerate(streams, 1):
            slug = stream.get('slug', 'unknown')
            print(f"\n[{i}/{len(streams)}] Processing: {slug}")
            
            # Fetch stream URL with retry
            m3u8_content, error_type = fetch_stream_url_with_retry(stream)
            
            if m3u8_content:
                # Save to file
                if save_stream(stream, m3u8_content):
                    total_success += 1
                else:
                    total_fail += 1
                    # Delete old file on save error
                    delete_old_file(stream)
                    error_summary['SaveError'] = error_summary.get('SaveError', 0) + 1
            else:
                total_fail += 1
                # Delete old file on fetch error
                delete_old_file(stream)
                # Track error type
                if error_type:
                    error_summary[error_type] = error_summary.get(error_type, 0) + 1
    
    # Summary
    print("\n" + "=" * 50)
    print(f"Complete: {total_success} successful, {total_fail} failed")
    
    # Error breakdown
    if error_summary:
        print("\nError Breakdown:")
        for error_type, count in sorted(error_summary.items(), key=lambda x: x[1], reverse=True):
            print(f"  • {error_type}: {count}")
    
    print("=" * 50)
    
    # Exit with error if any failed
    if total_fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
