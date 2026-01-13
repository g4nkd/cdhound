import requests
import random
import string
import sys
import urllib.parse
from typing import List, Tuple, Dict, Set
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import logging
from threading import Semaphore
from tqdm import tqdm

# Logging configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Constants
DEFAULT_DELIMITERS = ['/', '!', ';', ',', ':', '|', '#', '?']
DEFAULT_EXTENSIONS = ['.js', '.css', '.png', '.jpg', '.html']
MAX_THREADS = 10
REQUEST_TIMEOUT = 15

# Semaphore to limit concurrent requests
request_semaphore = Semaphore(MAX_THREADS)

def print_logo():
    """Display the script logo."""
    green = '\033[32m'
    bright_green = '\033[92m'
    reset = '\033[0m'

    logo = rf"""
{green}           .___.__                             .___
{green}  ____   __| _/|  |__   ____  __ __  ____    __| _/
{green}_/ ___\ / __ | |  |  \ /  _ \|  |  \/    \  / __ |
{green}\  \___/ /_/ | |   Y  (  <_> )  |  /   |  \/ /_/ |
{green} \___  >____ | |___|  /\____/|____/|___|  /\____ |
{green}     \/     \/      \/                  \/      \/
                                                            {bright_green}by gankd{reset}
"""
    print(logo)

def read_delimiters(wordlist_path: str) -> List[str]:
    """Read delimiters from a wordlist file."""
    try:
        with open(wordlist_path, 'r') as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        logging.error(f"Wordlist file not found: {wordlist_path}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Error reading wordlist: {str(e)}")
        sys.exit(1)

def generate_random_chars(length: int = 3) -> str:
    """Generate a random string of the specified length."""
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

def parse_header(header_str: str) -> Dict[str, str]:
    """Parse a header string in the format 'Name: Value'."""
    try:
        name, value = header_str.split(':', 1)
        return {name.strip(): value.strip()}
    except ValueError:
        logging.error("Invalid header format. Use 'Name: Value' format.")
        sys.exit(1)

def extract_static_directories(response_text: str, url: str, headers: Dict[str, str], proxies: Dict[str, str]) -> Set[str]:
    """Extract static resource directories from the response body."""
    static_dirs = set()
    pattern = r'(?:href|src|action|link\s+href|script\s+src|img\s+src)\s*=\s*["]?(/[\w\-/]+(?:\.\w+)?)'
    paths = re.findall(pattern, response_text)

    for path in paths:
        components = [p for p in path.split('/') if p]
        if not components:
            continue

        if '.' in components[-1]:
            components = components[:-1]

        current_path = ''
        for component in components:
            current_path = f"{current_path}/{component}"
            if any(keyword in current_path.lower() for keyword in ['static', 'css', 'js', 'images', 'img', 'assets', 'settings']):
                static_dirs.add(current_path)

    try:
        base_url = urllib.parse.urljoin(url, '/')
        root_response = requests.get(base_url, headers=headers, proxies=proxies, timeout=REQUEST_TIMEOUT)
        root_paths = re.findall(pattern, root_response.text)

        for path in root_paths:
            components = [p for p in path.split('/') if p]
            if not components:
                continue

            if '.' in components[-1]:
                components = components[:-1]

            current_path = ''
            for component in components:
                current_path = f"{current_path}/{component}"
                if any(keyword in current_path.lower() for keyword in ['static', 'css', 'js', 'images', 'img', 'assets']):
                    static_dirs.add(current_path)

    except requests.exceptions.RequestException as e:
        logging.warning(f"Could not fetch root path (/): {str(e)}")

    return static_dirs

def create_osn_test_urls(base_url: str, static_dirs: Set[str], recursion_depth: int) -> Set[str]:
    """Create test URLs for origin server normalization testing."""
    parsed_url = urllib.parse.urlparse(base_url)
    original_path = parsed_url.path.strip('/')
    test_urls = set()

    random_string = generate_random_chars()
    root_url = f"{parsed_url.scheme}://{parsed_url.netloc}/..%2f{original_path}?{random_string}"
    test_urls.add(root_url)

    if recursion_depth == 1:
        base_dirs = set()
        for static_dir in static_dirs:
            parts = [p for p in static_dir.split('/') if p]
            if parts:
                base_dirs.add(parts[0])

        for base_dir in base_dirs:
            random_string = generate_random_chars()
            test_url = f"{parsed_url.scheme}://{parsed_url.netloc}/{base_dir}/..%2f{original_path}?{random_string}"
            test_urls.add(test_url)
    else:
        for static_dir in static_dirs:
            parts = [p for p in static_dir.split('/') if p]
            current_path = ""

            for i in range(min(recursion_depth, len(parts))):
                current_path = '/'.join(parts[:i + 1])
                random_string = generate_random_chars()
                test_url = f"{parsed_url.scheme}://{parsed_url.netloc}/{current_path}/..%2f{original_path}?{random_string}"
                test_urls.add(test_url)

    return test_urls

def create_csn_test_urls(base_url: str, static_dirs: Set[str], delimiters: List[str], recursion_depth: int) -> Set[str]:
    """Create test URLs for client-side normalization testing."""
    parsed_url = urllib.parse.urlparse(base_url)
    original_path = parsed_url.path.strip('/')
    test_urls = set()

    base_dirs = set()
    for static_dir in static_dirs:
        parts = [p for p in static_dir.split('/') if p]
        if parts:
            for i in range(min(recursion_depth, len(parts))):
                base_dirs.add('/'.join(parts[:i + 1]))

    for base_dir in base_dirs:
        for delimiter in delimiters:
            random_chars = generate_random_chars()
            test_url = f"{parsed_url.scheme}://{parsed_url.netloc}/{original_path}{delimiter}%2f%2e%2e%2f{base_dir}?{random_chars}"
            test_urls.add(test_url)

    return test_urls

def create_file_cache_test_urls(base_url: str, delimiters: List[str], static_files: List[str] = None) -> Set[str]:
    """Create test URLs for file name cache rule exploitation with delimiters and random query strings."""
    parsed_url = urllib.parse.urlparse(base_url)
    test_urls = set()

    # Get the actual path from the URL
    original_path = parsed_url.path.strip('/')
    if not original_path:
        original_path = ""

    # Common files to test (base list + user-provided)
    common_files = ['robots.txt', 'index.html', 'index.php', 'sitemap.xml', 'favicon.ico', '404.html']
    if static_files:
        common_files.extend(static_files)

    for file in common_files:
        for delimiter in delimiters:
            # Generate a random query string
            random_query = generate_random_chars()
            # Create the test URL with delimiter and path traversal
            if original_path:
                test_url = f"{parsed_url.scheme}://{parsed_url.netloc}/{original_path}{delimiter}%2f%2e%2e%2f{file}?{random_query}"
            else:
                test_url = f"{parsed_url.scheme}://{parsed_url.netloc}/{delimiter}%2f%2e%2e%2f{file}?{random_query}"
            test_urls.add(test_url)

    return test_urls

def create_test_urls(base_url: str, delimiters: List[str], extensions: List[str]) -> Set[str]:
    """Create test URLs combining delimiters and extensions."""
    urls = set()
    parsed_url = urllib.parse.urlparse(base_url)
    path = parsed_url.path.rstrip('/')

    # Use default delimiters if none provided
    if not delimiters:
        delimiters = DEFAULT_DELIMITERS

    # Test each combination of delimiter and extension
    for delimiter in delimiters:
        for ext in extensions:
            random_chars = generate_random_chars()

            # Create path with delimiter
            if delimiter == '/':
                new_path = f"{path}/{random_chars}{ext}"
            else:
                new_path = f"{path}{delimiter}{random_chars}{ext}"

            new_url = urllib.parse.urlunparse((
                parsed_url.scheme,
                parsed_url.netloc,
                new_path,
                parsed_url.params,
                parsed_url.query,
                parsed_url.fragment
            ))
            urls.add(new_url)

    return urls

def check_cache_behavior(url: str, headers: Dict[str, str], proxies: Dict[str, str], verbose: bool = False) -> Tuple[str, bool, Dict, bool]:
    """Check if a URL is vulnerable to cache poisoning."""
    try:
        request_headers = {
            'User-Agent': 'r4nd0m'
        }
        request_headers.update(headers)

        debug_info = {}

        # First request
        first_response = requests.get(url, headers=request_headers, proxies=proxies, timeout=REQUEST_TIMEOUT)
        debug_info['first_status'] = first_response.status_code
        debug_info['first_cache'] = first_response.headers.get('X-Cache', '')
        debug_info['first_body'] = first_response.text

        # Second request (without cookies)
        headers_without_cookies = request_headers.copy()
        headers_without_cookies.pop('Cookie', None)

        second_response = requests.get(url, headers=headers_without_cookies, proxies=proxies, timeout=REQUEST_TIMEOUT)
        debug_info['second_status'] = second_response.status_code
        debug_info['second_cache'] = second_response.headers.get('X-Cache', '')
        debug_info['second_body'] = second_response.text

        # Check for vulnerability
        is_vulnerable = (
            first_response.status_code == 200 and
            second_response.status_code == 200 and
            first_response.text == second_response.text and
            'miss' in debug_info['first_cache'].lower() and
            'hit' in debug_info['second_cache'].lower()
        )

        # Check additional headers
        additional_headers = ['Vary', 'Pragma', 'Expires', 'Age', 'Cache-Control']
        for header in additional_headers:
            debug_info[f'first_{header.lower()}'] = first_response.headers.get(header, '')
            debug_info[f'second_{header.lower()}'] = second_response.headers.get(header, '')

        return url, is_vulnerable, debug_info, False

    except requests.exceptions.Timeout:
        if verbose:
            logging.warning(f"Timeout while testing: {url}")
        return url, False, {}, True

    except requests.exceptions.RequestException as e:
        if verbose:
            logging.warning(f"Error testing {url}: {str(e)}")
        return url, False, {}, False

def main():
    print_logo()

    parser = argparse.ArgumentParser(
        description='Test for web cache poisoning vulnerabilities.',
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument('url', help='Target URL')
    parser.add_argument('-H', '--header', help='Header in format "Name: Value" -> Used to authenticate requests')
    parser.add_argument('-w', '--wordlist', help='Path to custom delimiters wordlist', default=None)
    parser.add_argument('-e', '--extensions', help='Comma-separated list of extensions to test (default: ".js,.css,.png")', default='.js,.css,.png')
    parser.add_argument('-s', '--static-files', help='Comma-separated list of additional static files to test (e.g., "static.js,config.json,/path/to/file.svg")', default='')
    parser.add_argument(
        '-T', '--technique',
        choices=['pd', 'osn', 'csn', 'fncr'],
        help="""
Specific technique to run:
  pd   - Identify Path Delimiters for web cache deception
  osn  - Identify Origin Server Normalization for web cache deception
  csn  - Identify Cache Server Normalization for web cache deception
  fncr - Identify File Name Cache Rules for web cache deception
"""
    )
    parser.add_argument('-r', type=int, choices=[1, 2, 3], help='Recursion depth for OSN/CSN testing (default: 1)', default=1)
    parser.add_argument('-v', '--verbose', action='store_true', help='Show verbose output')
    parser.add_argument('-t', '--threads', type=int, default=MAX_THREADS, help=f'Number of threads to use (default: {MAX_THREADS})')
    parser.add_argument('-p', '--proxy', help='Proxy to use for requests (e.g., http://127.0.0.1:8080)')
    args = parser.parse_args()

    headers = {}
    if args.header:
        headers.update(parse_header(args.header))

    proxies = {}
    if args.proxy:
        proxies = {
            'http': args.proxy,
            'https': args.proxy
        }

    if proxies:
        print(f"[*] Using proxy: {args.proxy}")

    techniques_to_run = [args.technique] if args.technique else ['pd', 'osn', 'csn', 'fncr']
    recursion_depth = args.r
    static_dirs = None

    # Process static files argument
    static_files = []
    if args.static_files:
        static_files = [f.strip() for f in args.static_files.split(',') if f.strip()]
        print(f"[*] Added {len(static_files)} custom static files:")
        for file in static_files:
            print(f"    - {file}")

    total_techniques = len(techniques_to_run)
    current_technique = 0

    for technique in techniques_to_run:
        current_technique += 1
        print(f"\n{'=' * 60}")
        print(f"[*] Starting Technique {current_technique}/{total_techniques}: {technique.upper()}")
        print(f"[*] Description: {get_technique_description(technique)}")
        print(f"{'=' * 60}")

        test_urls = set()

        if technique == 'pd':
            extensions = [ext.strip() for ext in args.extensions.split(',') if ext.strip()]
            delimiters = read_delimiters(args.wordlist) if args.wordlist else DEFAULT_DELIMITERS
            print(f"[*] Using extensions: {extensions}")
            if delimiters:
                print(f"[*] Using {len(delimiters)} delimiters")
            test_urls = create_test_urls(args.url, delimiters, extensions)

        elif technique in ['osn', 'csn']:
            if static_dirs is None:
                try:
                    print("[*] Fetching static resource directories...")
                    initial_response = requests.get(args.url, headers=headers, proxies=proxies, timeout=REQUEST_TIMEOUT)
                    static_dirs = extract_static_directories(initial_response.text, args.url, headers, proxies)
                    if static_dirs:
                        print("[*] Found static resource directories:")
                        for dir in static_dirs:
                            print(f"   - {dir}")
                except requests.exceptions.RequestException as e:
                    print(f"[!] Error making initial request: {str(e)}")
                    continue

            if static_dirs:
                if technique == 'osn':
                    print(f"[*] Generating OSN test URLs with recursion depth {recursion_depth}...")
                    test_urls = create_osn_test_urls(args.url, static_dirs, recursion_depth)
                else:  # csn technique
                    delimiters = read_delimiters(args.wordlist) if args.wordlist else DEFAULT_DELIMITERS
                    print(f"[*] Loaded {len(delimiters)} delimiters")
                    print(f"[*] Generating CSN test URLs with recursion depth {recursion_depth}...")
                    test_urls = create_csn_test_urls(args.url, static_dirs, delimiters, recursion_depth)

        elif technique == 'fncr':
            print("[*] Generating test URLs for file name cache rule exploitation...")
            delimiters = read_delimiters(args.wordlist) if args.wordlist else DEFAULT_DELIMITERS
            print(f"[*] Using {len(delimiters)} delimiters")
            test_urls = create_file_cache_test_urls(args.url, delimiters, static_files)
        
        if test_urls:
            print(f"[*] Testing {len(test_urls)} URLs for {technique.upper()} technique")
            print("-" * 60)

            timeout_count = 0
            vulnerable_urls = []

            with ThreadPoolExecutor(max_workers=args.threads) as executor:
                futures = {executor.submit(check_cache_behavior, url, headers, proxies, args.verbose): url for url in test_urls}

                for future in tqdm(as_completed(futures), total=len(test_urls), desc="Testing URLs", unit="URL"):
                    url, is_vulnerable, debug_info, had_timeout = future.result()
                    if had_timeout:
                        timeout_count += 1
                    if is_vulnerable:
                        print(f"\n\033[32m[!] VULNERABLE URL FOUND!\033[0m")
                        print(f"\033[32m[!] URL: {url}\033[0m")
                        print(f"[!] Cache behavior: {debug_info.get('first_cache')} -> {debug_info.get('second_cache')}")
                        print("[!] Authenticated content leaked to unauthenticated user!")
                        vulnerable_urls.append(url)
                    elif args.verbose:
                        print(f"[+] Tested: {url} | Not vulnerable")

            print(f"\n[*] Completed {technique.upper()} technique scan ({current_technique}/{total_techniques})")

            if timeout_count > 0:
                print(f"\033[33m[!] Warning: {timeout_count} requests timed out during the scan (timeout={REQUEST_TIMEOUT}s)\033[0m")

            if vulnerable_urls:
                print(f"\033[32m[*] Vulnerable URLs found: {len(vulnerable_urls)}")
                for url in vulnerable_urls:
                    print(f"   - {url}")
        else:
            print(f"[!] No test URLs were generated for {technique.upper()} technique")

def get_technique_description(technique: str) -> str:
    """Return a description of each testing technique."""
    descriptions = {
        'pd': "Testing for basic cache poisoning using different file extensions",
        'osn': "Origin Server Normalization - Testing path traversal via static resource directories",
        'csn': "Client-Side Normalization - Testing path traversal with delimiters and static resources",
        'fncr': "Exploiting file name cache rules by testing common files with path traversal sequences",
    }
    return descriptions.get(technique, "Unknown technique")

if __name__ == "__main__":
    main()
