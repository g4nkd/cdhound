# cdhound

This tool is designed to test for web cache deception vulnerabilities by exploiting discrepancies in how web servers and caches handle URL paths, delimiters, and static resources. It automates the process of identifying and exploiting these vulnerabilities, making it easier for security researchers and penetration testers to assess the security of web applications.

Additionally, cdhound employs **discreet web poisoning** by randomizing parameters during tests to avoid detection and minimize the impact on the site's traffic.

## Features

- **Path Delimiter Testing (PD)**: Identifies discrepancies in how delimiters are interpreted by the origin server and the cache.
- **Origin Server Normalization (OSN)**: Tests for vulnerabilities where the origin server normalizes URL paths differently than the cache.
- **Cache Server Normalization (CSN)**: Exploits discrepancies in how the cache server normalizes URL paths compared to the origin server.
- **File Name Cache Rules (FNCR)**: Tests for vulnerabilities related to how common files (e.g., `robots.txt`, `index.html`) are cached.
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

- `-H, --header`: Add a custom header to the requests (e.g., `Authorization: Bearer token`).
- `-w, --wordlist`: Path to a custom wordlist of delimiters.
- `-e, --extensions`: Comma-separated list of file extensions to test (default: `.js,.css,.png`).
- `-T, --technique`: Specify the technique to use (`pd`, `osn`, `csn`, `fncr`).
- `-r`: Recursion depth for OSN/CSN testing (default: 1).
- `-v, --verbose`: Enable verbose output.
- `-t, --threads`: Number of threads to use (default: 10).
- `-p, --proxy`: Use a proxy for requests (e.g., `http://127.0.0.1:8080`).

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

4. **Test with Proxy and Verbose Output**:
   ```bash
   python cdhound.py https://example.com/account -H "Cookie: XXX" -p http://127.0.0.1:8080 -v
   ```

## Techniques Explained

### Path Delimiter Testing (`pd`)

This technique tests for discrepancies in how delimiters (e.g., `;`, `,`, `#`) are interpreted by the origin server and the cache. If the origin server treats a character as a delimiter but the cache does not, it may be possible to craft a URL that is interpreted differently by each, leading to cache deception.

### Origin Server Normalization (OSN)

OSN exploits discrepancies in how the origin server normalizes URL paths compared to the cache. For example, if the origin server resolves path traversal sequences (e.g., `/static/..%2fprofile`) but the cache does not, an attacker can craft a URL that returns sensitive information from the origin server, which is then cached and served to other users.

### Cache Server Normalization (CSN)

CSN is the inverse of OSN. It exploits cases where the cache normalizes URL paths but the origin server does not. By crafting a URL that the cache interprets differently than the origin server, an attacker can cause the cache to store and serve sensitive information.

### File Name Cache Rules (FNCR)

This technique targets cache rules that are based on specific file names (e.g., `robots.txt`, `index.html`). By appending these file names to dynamic URLs, an attacker can cause the cache to store and serve dynamic content as if it were a static file.

---

## Labs

You can test my tool in these labs:
- [Port Swigger Labs](https://portswigger.net/web-security/all-labs#web-cache-deception)
- [HackTheBox Web Challange - CDNio](https://app.hackthebox.com/challenges/CDNio?tab=play_challenge)

---
For more information on web cache poisoning and deception, refer to the [PortSwigger Web Security Academy](https://portswigger.net/web-security/web-cache-poisoning) and [Gotta Cache ‘em all bending the rules of web cache exploitation](https://www.youtube.com/watch?v=70yyOMFylUA).
