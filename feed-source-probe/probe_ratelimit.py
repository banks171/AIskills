#!/usr/bin/env python3
"""
Tier 1 (robots.txt) + Tier 3 (HTTP HEAD) rate_limit probe tool.

Usage:
  # Tier 1: robots.txt Crawl-delay detection
  echo "reuters.com\napnews.com" | python3 probe_ratelimit.py --tier1

  # Tier 3: HTTP HEAD X-RateLimit-* header detection
  echo "10003,reuters,https://www.reuters.com" | python3 probe_ratelimit.py --tier3

  # Read proxy from .env (default: look in current dir or $PROJECT_ROOT)
  python3 probe_ratelimit.py --tier1 --env /path/to/.env < domains.txt

Output: CSV to stdout (domain,crawl_delay,rate_per_min,request_rate,status,note)
"""

import argparse
import os
import re
import sys
import time

import requests

# ---------------------------------------------------------------------------
# Proxy config
# ---------------------------------------------------------------------------

def load_proxy_from_env(env_path=None):
    """Load proxy credentials from .env file."""
    paths = [env_path] if env_path else [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.environ.get("PROJECT_ROOT", ""), ".env"),
    ]
    cfg = {}
    for p in paths:
        if p and os.path.isfile(p):
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        cfg[k.strip()] = v.strip()
            break

    host = cfg.get("PROXY_HOST", "gw.dataimpulse.com")
    port = cfg.get("PROXY_PORT", "823")
    user = cfg.get("PROXY_USER", "")
    pwd = cfg.get("PROXY_PASS", "")
    if not user or not pwd:
        print("WARNING: PROXY_USER/PROXY_PASS not found in .env, proxy may fail", file=sys.stderr)
    proxy_url = f"http://{user}:{pwd}@{host}:{port}"
    return {"http": proxy_url, "https": proxy_url}


HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# Domains where proxy is blocked (DataImpulse compliance)
BLOCKED_SUFFIXES = (".gov", ".gov.cn", ".gov.uk", ".gov.sa", ".gov.ph", ".edu", ".mil")


def is_proxy_blocked(domain):
    return any(domain.endswith(s) or f"{s}." in domain for s in BLOCKED_SUFFIXES)


# ---------------------------------------------------------------------------
# Tier 1: robots.txt
# ---------------------------------------------------------------------------

def parse_crawl_delay(robots_text):
    """Extract Crawl-delay and Request-rate from robots.txt content."""
    crawl_delay = None
    request_rate = None
    for line in robots_text.split("\n"):
        line = line.strip()
        m = re.match(r"(?i)crawl-delay\s*:\s*(\d+\.?\d*)", line)
        if m:
            crawl_delay = float(m.group(1))
        m = re.match(r"(?i)request-rate\s*:\s*(\d+)\s*/\s*(\d+)", line)
        if m:
            request_rate = f"{m.group(1)}/{m.group(2)}s"
    return crawl_delay, request_rate


def fetch_robots(domain, proxies):
    """Fetch robots.txt for a domain via proxy. Returns (text, status, error)."""
    url = f"https://{domain}/robots.txt"
    try:
        resp = requests.get(url, proxies=proxies, headers=HEADERS, timeout=15, allow_redirects=True)
        if resp.status_code == 200:
            ct = resp.headers.get("content-type", "")
            if "text/html" in ct and "<html" in resp.text[:500].lower():
                return None, None, "HTML_NOT_ROBOTS"
            return resp.text, resp.status_code, None
        return None, resp.status_code, f"HTTP_{resp.status_code}"
    except requests.exceptions.Timeout:
        return None, None, "TIMEOUT"
    except requests.exceptions.ConnectionError as e:
        err = str(e)
        if "SITE_PERMANENTLY_BLOCKED" in err or "407" in err:
            return None, None, "PROXY_BLOCKED"
        return None, None, f"CONN_ERROR:{err[:60]}"
    except Exception as e:
        return None, None, f"ERROR:{str(e)[:60]}"


def run_tier1(proxies, delay=3):
    """Read domains from stdin, probe robots.txt, output CSV."""
    print("domain,crawl_delay,rate_per_min,request_rate,status,note")
    domains = [line.strip() for line in sys.stdin if line.strip()]
    for i, domain in enumerate(domains):
        if is_proxy_blocked(domain):
            print(f"{domain},,,SKIPPED_GOV,proxy compliance block", flush=True)
            continue

        text, status, error = fetch_robots(domain, proxies)
        if error:
            print(f"{domain},,,{error},", flush=True)
        elif text:
            cd, rr = parse_crawl_delay(text)
            rate = ""
            note = "no_crawl_delay"
            if cd is not None:
                rate = str(round(60 / cd, 1)) if cd > 0 else "inf"
                note = f"Crawl-delay:{cd}"
            if rr:
                note += f"|Request-rate:{rr}"
            cd_str = str(cd) if cd is not None else ""
            rr_str = rr or ""
            print(f"{domain},{cd_str},{rate},{rr_str},OK_{status},{note}", flush=True)

        if i < len(domains) - 1 and not is_proxy_blocked(domains[i + 1]):
            time.sleep(delay)


# ---------------------------------------------------------------------------
# Tier 3: HTTP HEAD
# ---------------------------------------------------------------------------

RATE_KEYWORDS = [
    "x-ratelimit", "x-rate-limit", "ratelimit", "rate-limit",
    "retry-after", "x-retry-after", "x-app-rate-limit", "x-quota",
]


def run_tier3(proxies, delay=3):
    """Read 'id,name,url' from stdin, probe HEAD, output CSV."""
    print("id,name,url,status,header_key,header_value,note")
    lines = [line.strip() for line in sys.stdin if line.strip()]
    for i, line in enumerate(lines):
        parts = line.split(",", 2)
        if len(parts) < 3:
            continue
        fid, name, url = parts[0].strip(), parts[1].strip(), parts[2].strip()

        try:
            resp = requests.head(url, proxies=proxies, headers=HEADERS, timeout=15, allow_redirects=True)
            method = "HEAD"
            if resp.status_code == 405:
                resp = requests.get(url, proxies=proxies, headers=HEADERS, timeout=15,
                                    allow_redirects=True, stream=True)
                method = "GET_stream"
                resp.close()

            found = []
            for k, v in resp.headers.items():
                if any(rk in k.lower() for rk in RATE_KEYWORDS):
                    found.append((k, v))

            if found:
                for hk, hv in found:
                    print(f"{fid},{name},{url},{resp.status_code},{hk},{hv},{method}", flush=True)
            else:
                print(f"{fid},{name},{url},{resp.status_code},,,no_rate_headers|{method}", flush=True)

        except requests.exceptions.Timeout:
            print(f"{fid},{name},{url},,,,TIMEOUT", flush=True)
        except requests.exceptions.ConnectionError as e:
            err = str(e)[:80].replace(",", ";")
            print(f"{fid},{name},{url},,,,CONN_ERROR:{err}", flush=True)
        except Exception as e:
            print(f"{fid},{name},{url},,,,ERROR:{str(e)[:80]}", flush=True)

        if i < len(lines) - 1:
            time.sleep(delay)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Feed source rate_limit probe (Tier 1 + Tier 3)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tier1", action="store_true", help="Tier 1: robots.txt Crawl-delay (stdin: one domain per line)")
    group.add_argument("--tier3", action="store_true", help="Tier 3: HTTP HEAD X-RateLimit (stdin: id,name,url per line)")
    parser.add_argument("--env", default=None, help="Path to .env file for proxy credentials")
    parser.add_argument("--delay", type=int, default=3, help="Seconds between requests (default: 3)")
    args = parser.parse_args()

    proxies = load_proxy_from_env(args.env)

    if args.tier1:
        run_tier1(proxies, args.delay)
    elif args.tier3:
        run_tier3(proxies, args.delay)


if __name__ == "__main__":
    main()