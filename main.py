#!/usr/bin/env python3
"""
YouTube Stream Updater - Improved Version
Fetches YouTube stream URLs and updates m3u8 playlists
"""

import json
import os
import sys
import argparse
import time
import re
import subprocess
from pathlib import Path
from urllib.parse import urlencode, urlparse, quote

# Try to import cloudscraper, fallback to requests
try:
    import cloudscraper
    CLOUDSCRAPER_AVAILABLE = True
except ImportError:
    try:
        import requests
        CLOUDSCRAPER_AVAILABLE = False
        print("⚠ cloudscraper not available, using requests")
    except ImportError:
        print("✗ Neither cloudscraper nor requests available")
        sys.exit(1)

# Configuration
DEFAULT_ENDPOINT = 'https://your-endpoint.com'  # Replace with actual endpoint
FOLDER_NAME = 'streams'
TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY = 2

class YouTubeStreamUpdater:
    def __init__(self, endpoint, output_folder, timeout=30, max_retries=3):
        self.endpoint = endpoint.rstrip('/')
        self.output_folder = Path(output_folder)
        self.timeout = timeout
        self.max_retries = max_retries
        
        # Initialize session
        self.session = self._create_session()
        
    def _create_session(self):
        """Create HTTP session with proper headers"""
        if CLOUDSCRAPER_AVAILABLE:
            try:
                scraper = cloudscraper.create_scraper(
                    browser={
                        'browser': 'chrome',
                        'platform': 'windows',
                        'mobile': False
                    },
                    delay=10
                )
                print("✓ Using cloudscraper for JavaScript challenges")
                return scraper
            except Exception as e:
                print(f"⚠ Cloudscraper init failed: {e}, falling back to requests")
                import requests
                session = requests.Session()
        else:
            import requests
            session = requests.Session()
            print("⚠ Using basic requests session")
        
        # Common headers
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })
        
        return session

    def load_config(self, config_path):
        """Load configuration from JSON file"""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            if isinstance(config, list):
                print(f"✓ Loaded {len(config)} stream(s) from {config_path}")
                return config
            elif isinstance(config, dict) and 'streams' in config:
                print(f"✓ Loaded {len(config['streams'])} stream(s) from {config_path}")
                return config['streams']
            else:
                print(f"✗ Invalid config structure in {config_path}")
                return []
                
        except FileNotFoundError:
            print(f"✗ Config file not found: {config_path}")
            return []
        except json.JSONDecodeError as e:
            print(f"✗ Invalid JSON in {config_path}: {e}")
            return []
        except Exception as e:
            print(f"✗ Error loading {config_path}: {e}")
            return []

    def build_request_url(self, stream_config):
        """Build the request URL for a stream"""
        stream_type = stream_config.get('type', 'channel').lower()
        stream_id = stream_config['id']
        slug = stream_config.get('slug', stream_id)
        
        # Different endpoints might use different parameters
        if stream_type == 'video':
            # For individual videos
            param = 'v'
        elif stream_type == 'channel':
            # For channel live streams
            param = 'c'
        elif stream_type == 'playlist':
            # For playlists
            param = 'p'
        else:
            print(f"⚠ Unknown type '{stream_type}' for {slug}, using 'c'")
            param = 'c'
        
        # Build URL - adjust this based on your endpoint's expected format
        url = f"{self.endpoint}/yt.php?{param}={stream_id}"
        return url

    def fetch_stream_url(self, stream_config):
        """Fetch the YouTube stream URL with retry logic"""
        slug = stream_config.get('slug', stream_config['id'])
        url = self.build_request_url(stream_config)
        
        print(f"  Fetching: {slug}")
        print(f"  URL: {url}")
        
        for attempt in range(1, self.max_retries + 1):
            if attempt > 1:
                delay = self.retry_delay * (2 ** (attempt - 2))
                print(f"  → Retry {attempt}/{self.max_retries} after {delay}s...")
                time.sleep(delay)
            
            try:
                response = self.session.get(
                    url,
                    timeout=self.timeout,
                    allow_redirects=True,
                    headers={'Referer': 'https://www.youtube.com/'}
                )
                
                print(f"  → Status: {response.status_code}")
                
                if response.status_code == 200:
                    content = response.text
                    
                    # Check if we got m3u8 content
                    if content and '#EXTM3U' in content:
                        print(f"  ✓ Successfully fetched m3u8 content")
                        return content
                    else:
                        # Might be HTML error page or redirect needed
                        print(f"  ⚠ Response doesn't contain m3u8 data")
                        
                        # Try to extract m3u8 URL from response
                        m3u8_url = self.extract_m3u8_url(content, response.url)
                        if m3u8_url:
                            print(f"  → Found m3u8 URL: {m3u8_url}")
                            return self.fetch_final_url(m3u8_url)
                        
                        # Log first few lines for debugging
                        preview = content[:200] if len(content) > 200 else content
                        print(f"  → Content preview: {preview}")
                        
                elif response.status_code in [403, 429]:
                    print(f"  ✗ Access denied (rate limit or block)")
                    if attempt < self.max_retries:
                        print(f"  → Waiting longer before retry...")
                        time.sleep(10)
                    continue
                    
                elif response.status_code >= 500:
                    print(f"  ✗ Server error {response.status_code}")
                    continue
                    
            except Exception as e:
                error_type = type(e).__name__
                print(f"  ✗ Attempt {attempt} failed: {error_type} - {str(e)}")
                
                if 'timeout' in str(e).lower():
                    print(f"  → Request timeout after {self.timeout}s")
                elif 'connection' in str(e).lower():
                    print(f"  → Connection error")
        
        print(f"  ✗ All {self.max_retries} attempts failed for {slug}")
        return None

    def extract_m3u8_url(self, content, base_url):
        """Extract m3u8 URL from HTML content or response"""
        # Look for m3u8 URLs in content
        m3u8_patterns = [
            r'https?://[^\s"\']+\.m3u8[^\s"\']*',
            r'#EXT-X-STREAM-INF[^\n]+\n([^\n]+)',
        ]
        
        for pattern in m3u8_patterns:
            matches = re.findall(pattern, content)
            for match in matches:
                if isinstance(match, tuple):
                    match = match[0] if match else ''
                if match and '.m3u8' in match:
                    # Convert relative URL to absolute
                    if match.startswith('//'):
                        return 'https:' + match
                    elif match.startswith('/'):
                        parsed_base = urlparse(base_url)
                        return f"{parsed_base.scheme}://{parsed_base.netloc}{match}"
                    elif match.startswith('http'):
                        return match
                    else:
                        return base_url + '/' + match.lstrip('/')
        
        return None

    def fetch_final_url(self, url):
        """Fetch final m3u8 content from URL"""
        try:
            response = self.session.get(url, timeout=self.timeout)
            if response.status_code == 200 and '#EXTM3U' in response.text:
                return response.text
        except Exception as e:
            print(f"  ✗ Error fetching final URL: {e}")
        
        return None

    def reverse_quality_order(self, m3u8_content):
        """Reverse quality order to put highest quality first"""
        if not m3u8_content or '#EXT-X-STREAM-INF' not in m3u8_content:
            return m3u8_content
        
        lines = m3u8_content.strip().split('\n')
        streams = []
        current_stream = []
        
        for line in lines:
            if line.startswith('#EXT-X-STREAM-INF'):
                if current_stream:
                    streams.append(current_stream)
                current_stream = [line]
            elif current_stream:
                current_stream.append(line)
                if line and not line.startswith('#'):
                    # This should be the URL line
                    streams.append(current_stream)
                    current_stream = []
        
        # Add any remaining stream
        if current_stream:
            streams.append(current_stream)
        
        # Reverse order (highest quality first)
        streams.reverse()
        
        # Rebuild content
        result = []
        for stream in streams:
            result.extend(stream)
        
        return '\n'.join(result)

    def save_stream(self, stream_config, m3u8_content):
        """Save m3u8 content to file"""
        slug = stream_config.get('slug', stream_config['id'])
        subfolder = stream_config.get('subfolder', '')
        
        # Determine output directory
        if subfolder:
            output_dir = self.output_folder / subfolder
        else:
            output_dir = self.output_folder
        
        # Create directory if needed
        output_dir.mkdir(parents=True, exist_ok=True)
        
        output_file = output_dir / f"{slug}.m3u8"
        
        try:
            # Reverse quality order
            processed_content = self.reverse_quality_order(m3u8_content)
            
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(processed_content)
            
            print(f"✓ Saved: {output_file}")
            return True
            
        except Exception as e:
            print(f"✗ Error saving {output_file}: {e}")
            return False

    def delete_old_file(self, stream_config):
        """Delete old m3u8 file if it exists"""
        slug = stream_config.get('slug', stream_config['id'])
        subfolder = stream_config.get('subfolder', '')
        
        if subfolder:
            output_dir = self.output_folder / subfolder
        else:
            output_dir = self.output_folder
        
        output_file = output_dir / f"{slug}.m3u8"
        
        try:
            if output_file.exists():
                output_file.unlink()
                print(f"  ⚠ Deleted old file: {output_file}")
                return True
        except Exception as e:
            print(f"  ⚠ Could not delete {output_file}: {e}")
        
        return False

    def process_config(self, config_file, fail_on_error=False):
        """Process a single configuration file"""
        print(f"\n📄 Processing: {config_file}")
        print("-" * 50)
        
        streams = self.load_config(config_file)
        if not streams:
            print("✗ No streams found in config")
            return 0, 0
        
        success_count = 0
        fail_count = 0
        
        for i, stream in enumerate(streams, 1):
            slug = stream.get('slug', stream['id'])
            print(f"\n[{i}/{len(streams)}] {slug}")
            
            m3u8_content = self.fetch_stream_url(stream)
            
            if m3u8_content:
                if self.save_stream(stream, m3u8_content):
                    success_count += 1
                else:
                    fail_count += 1
                    self.delete_old_file(stream)
            else:
                fail_count += 1
                self.delete_old_file(stream)
        
        return success_count, fail_count

def main():
    """Main execution function"""
    parser = argparse.ArgumentParser(description='YouTube Stream Updater')
    parser.add_argument('config_files', nargs='+', help='Configuration JSON files')
    parser.add_argument('--endpoint', default=DEFAULT_ENDPOINT, help='API endpoint URL')
    parser.add_argument('--folder', default=FOLDER_NAME, help='Output folder')
    parser.add_argument('--timeout', type=int, default=TIMEOUT, help='Request timeout')
    parser.add_argument('--retries', type=int, default=MAX_RETRIES, help='Max retries')
    parser.add_argument('--retry-delay', type=int, default=RETRY_DELAY, help='Retry delay')
    parser.add_argument('--fail-on-error', action='store_true', help='Exit on errors')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("YouTube Stream Updater - Improved Version")
    print("=" * 60)
    print(f"Endpoint: {args.endpoint}")
    print(f"Output: {args.folder}")
    print(f"Configs: {', '.join(args.config_files)}")
    print(f"Timeout: {args.timeout}s, Retries: {args.retries}")
    print("=" * 60)
    
    # Create updater instance
    updater = YouTubeStreamUpdater(
        endpoint=args.endpoint,
        output_folder=args.folder,
        timeout=args.timeout,
        max_retries=args.retries
    )
    updater.retry_delay = args.retry_delay
    
    total_success = 0
    total_fail = 0
    
    # Process each config file
    for config_file in args.config_files:
        if not os.path.exists(config_file):
            print(f"\n✗ Config file not found: {config_file}")
            continue
            
        success, fail = updater.process_config(config_file, args.fail_on_error)
        total_success += success
        total_fail += fail
    
    # Print summary
    print("\n" + "=" * 60)
    print(f"SUMMARY: {total_success} successful, {total_fail} failed")
    print("=" * 60)
    
    if total_fail > 0 and args.fail_on_error:
        sys.exit(1)
    elif total_fail > 0:
        print("⚠ Some streams failed, but continuing...")
    else:
        print("✓ All streams processed successfully!")

if __name__ == "__main__":
    main()
