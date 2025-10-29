#!/usr/bin/env python3
"""
YouTube Stream Updater
Fetches YouTube stream URLs and updates m3u8 playlists
"""

import json
import os
import sys
import argparse
import requests
from pathlib import Path
from urllib.parse import urlencode

# Configuration
ENDPOINT = os.environ.get('ENDPOINT', 'https://your-endpoint.com')
FOLDER_NAME = os.environ.get('FOLDER_NAME', 'streams')
TIMEOUT = 30


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
        response = requests.get(url, timeout=TIMEOUT, allow_redirects=True)
        response.raise_for_status()
        
        # The response should be the m3u8 content
        return response.text
        
    except requests.exceptions.RequestException as e:
        print(f"✗ Error fetching stream for {slug}: {e}")
        return None


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
    
    return parser.parse_args()


def main():
    """Main execution function"""
    args = parse_arguments()
    
    # Update globals with command line arguments
    global ENDPOINT, FOLDER_NAME
    ENDPOINT = args.endpoint
    FOLDER_NAME = args.folder
    
    print("=" * 50)
    print("YouTube Stream Updater")
    print("=" * 50)
    print(f"Endpoint: {ENDPOINT}")
    print(f"Output folder: {FOLDER_NAME}")
    print(f"Config files: {', '.join(args.config_files)}")
    print("=" * 50)
    
    total_success = 0
    total_fail = 0
    
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
            
            # Fetch stream URL
            m3u8_content = fetch_stream_url(stream)
            
            if m3u8_content:
                # Save to file
                if save_stream(stream, m3u8_content):
                    total_success += 1
                else:
                    total_fail += 1
                    # Delete old file on save error
                    delete_old_file(stream)
            else:
                total_fail += 1
                # Delete old file on fetch error
                delete_old_file(stream)
    
    # Summary
    print("\n" + "=" * 50)
    print(f"Complete: {total_success} successful, {total_fail} failed")
    print("=" * 50)
    
    # Exit with error if any failed
    if total_fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
