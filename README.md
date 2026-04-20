# cdhound

A tool for testing web cache deception vulnerabilities by exploiting discrepancies in how web servers and CDN caches handle URL paths, delimiters, static resources, and request headers. It automates detection and exploitation of these mismatches, making it easier for security researchers and penetration testers to assess the caching layer of web applications.

Requests use randomized parameters (`?<rand>`) so each test produces unique cache keys — avoiding pollution of the live cache and keeping test impact on legitimate traffic minimal.

## Features

- **Path Delimiter Testing (PD)**: Identifies discrepancies in how delimiters are interpreted by the origin server and the cache.
- **Origin Server Normalization (OSN)**: Tests for vulnerabilities where the origin server normalizes URL paths differently than the cache.
- **Cache Server Normalization (CSN)**: Exploits discrepancies in how the cache server normalizes URL paths compared to the origin server.
- **File Name Cache Rules (FNCR)**: Tests for vulnerabilities related to how common files (e.g., `robots.txt`, `index.html`) are cached.
- **Path Header Override (PHO)**: Injects HTTP headers (`X-Original-URL`, `X-Rewrite-URL`, `X-Forwarded-*`, etc.) to desync the path the CDN keys on from the path the origin actually serves.
- **Cross-CDN cache detection**: Recognises cache status from Cloudflare, Fastly, Akamai, Vercel, Varnish, Nginx and others — not just `X-Cache`.
- **Marker-based leak detection**: Extracts sensitive tokens (emails, UUIDs, JWTs, session IDs) from authenticated responses and confirms a leak when they reappear in anonymous responses.
- **Static Resource Detection**: Automatically detects static resource directories and uses them to craft test URLs.
- **Multi-threaded Testing**: Utilizes multiple threads to speed up the testing process.
- **Verbose Output**: Provides detailed information about each test, including response headers and cache behavior.

---

## Installation

1. Clone the repository to your local machine:
   ```bash
   git clone https://github.com/g4nkd/cdhound.git
   ```

2. Navigate to the project directory:
   ```bash
   cd cdhound
   ```

3. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

---

### Options

- `-H, --header`: Add a custom auth header to the requests. Repeatable (e.g. `-H "Cookie: ..." -H "Authorization: Bearer ..."`).
- `-fh, --filter-header`: Only test URLs whose authenticated response contains this header/value.
- `-w, --wordlist`: Path to a custom wordlist of delimiters.
- `-e, --extensions`: Comma-separated list of file extensions to test (default: `.js,.css,.png`).
- `-s, --static-files`: Extra static filenames/paths to probe in FNCR and PHO.
- `-T, --technique`: Specify the technique to use (`pd`, `osn`, `csn`, `fncr`, `pho`).
- `-r`: Recursion depth for OSN/CSN testing (default: 1).
- `-v, --verbose`: Enable verbose output.
- `-t, --threads`: Number of threads to use (default: 10).
- `-p, --proxy`: Use a proxy for requests (e.g., `http://127.0.0.1:8080`).
- `--delay`: Seconds between the authenticated request and the anonymous retry (default: 0.3).
- `--markers`: Comma-separated extra strings to watch for in responses (leaked tokens, emails, etc.).
- `--sensitive-path`: Path to inject in PHO override headers (default: target URL path).

### Example Commands

1. **Basic Test with Default Settings**:
   ```bash
   python cdhound.py https://example.com/profile -H "Cookie: XXX"
   ```

2. **Test with Custom Delimiters and Extensions**:
   ```bash
   python cdhound.py https://example.com/my-account -H "Cookie: XXX" -w delimiters.txt -e .js,.css,.html
   ```

3. **Test with Specific Technique (OSN)**:
   ```bash
   python cdhound.py https://example.com/profile -H "Cookie: XXX" -T osn -r 2
   ```

4. **Path Header Override against a sensitive endpoint**:
   ```bash
   python cdhound.py https://example.com/api/users/me -H "Cookie: XXX" -T pho --markers "user@example.com"
   ```

5. **Test with Proxy and Verbose Output**:
   ```bash
   python cdhound.py https://example.com/account -H "Cookie: XXX" -p http://127.0.0.1:8080 -v
   ```

## Techniques Explained

### Path Delimiter Testing (`pd`)

This technique tests for discrepancies in how delimiters (e.g., `;`, `,`, `#`) are interpreted by the origin server and the cache. If the origin server treats a character as a delimiter but the cache does not, it may be possible to craft a URL that is interpreted differently by each, leading to cache deception.

### Origin Server Normalization (`osn`)

OSN exploits discrepancies in how the origin server normalizes URL paths compared to the cache. For example, if the origin server resolves path traversal sequences (e.g., `/static/..%2fprofile`) but the cache does not, an attacker can craft a URL that returns sensitive information from the origin server, which is then cached and served to other users.

### Cache Server Normalization (`csn`)

CSN is the inverse of OSN. It exploits cases where the cache normalizes URL paths but the origin server does not. By crafting a URL that the cache interprets differently than the origin server, an attacker can cause the cache to store and serve sensitive information.

### File Name Cache Rules (`fncr`)

This technique targets cache rules that are based on specific file names (e.g., `robots.txt`, `index.html`). By appending these file names to dynamic URLs, an attacker can cause the cache to store and serve dynamic content as if it were a static file.

### Path Header Override (`pho`)

PHO tests middleware/frameworks that rewrite the internal request path based on headers like `X-Original-URL`, `X-Rewrite-URL` or `X-Forwarded-URI`. When the origin honours such a header, the CDN keys the cache on the visible (static-looking) path while the origin serves content from the header-specified path — allowing an attacker to cache authenticated responses under attacker-controlled URLs.

---

## Labs

You can test the tool in these labs:
- [PortSwigger Labs](https://portswigger.net/web-security/all-labs#web-cache-deception)
- [HackTheBox Web Challenge — CDNio](https://app.hackthebox.com/challenges/CDNio?tab=play_challenge)

---

For more information on web cache poisoning and deception, refer to the [PortSwigger Web Security Academy](https://portswigger.net/web-security/web-cache-poisoning) and [Gotta Cache 'em all — bending the rules of web cache exploitation](https://www.youtube.com/watch?v=70yyOMFylUA).
