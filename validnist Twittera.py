import asyncio
import re
from playwright.async_api import async_playwright

TOKENS_FILE = "tokens.txt"
PROXY_FILE = "proxies.txt"

MAX_CONCURRENCY = 10  # одночасних браузерів


def load_auth_tokens(file_path):
    """Завантажує токени у форматах:
       - auth_token
       - auth_token:ct0
       - auth_token=...;ct0=...
    """
    tokens = []
    with open(file_path, "r", encoding="utf-8") as f:
        for raw in f:
            s = raw.strip()
            if not s:
                continue
            auth_token = None
            ct0 = None
            if ";" in s and "=" in s:
                parts = [p.strip() for p in s.split(";")]
                for p in parts:
                    if p.startswith("auth_token="):
                        auth_token = p.split("=", 1)[1].strip()
                    elif p.startswith("ct0="):
                        ct0 = p.split("=", 1)[1].strip()
            elif ":" in s and "=" not in s:
                auth_token, ct0 = s.split(":", 1)
            else:
                if s.startswith("auth_token="):
                    s = s.split("=", 1)[1].strip()
                auth_token = s
            if auth_token:
                tokens.append({"auth_token": auth_token.strip(), "ct0": (ct0.strip() if ct0 else None)})
    return tokens


def load_proxies(file_path):
    """Завантажує проксі у форматі username:password@host:port"""
    proxies = []
    with open(file_path, "r", encoding="utf-8") as pf:
        for line in pf:
            line = line.strip()
            if not line:
                continue
            if "@" in line:
                creds, hostport = line.split("@", 1)
                if ":" in creds:
                    username, password = creds.split(":", 1)
                else:
                    username, password = creds, ""
                if ":" in hostport:
                    host, port = hostport.split(":", 1)
                else:
                    host, port = hostport, ""
                proxies.append({
                    "server": f"http://{host.strip()}:{port.strip()}",
                    "username": username.strip(),
                    "password": password.strip()
                })
            else:
                print("❌ Невірний формат проксі:", line)
    return proxies


async def check_token(pw, token_dict, proxy=None):
    """Перевіряє один токен через Playwright + проксі"""
    browser = await pw.chromium.launch(headless=True, proxy=proxy)
    context = await browser.new_context()

    # підставляємо куки
    cookies = [
        {"name": "auth_token", "value": token_dict["auth_token"], "domain": ".x.com", "path": "/", "httpOnly": True, "secure": True},
        {"name": "auth_token", "value": token_dict["auth_token"], "domain": ".twitter.com", "path": "/", "httpOnly": True, "secure": True}
    ]
    if token_dict.get("ct0"):
        cookies += [
            {"name": "ct0", "value": token_dict["ct0"], "domain": ".x.com", "path": "/", "httpOnly": True, "secure": True},
            {"name": "ct0", "value": token_dict["ct0"], "domain": ".twitter.com", "path": "/", "httpOnly": True, "secure": True}
        ]
    await context.add_cookies(cookies)

    page = await context.new_page()
    status = "invalid"
    try:
        await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)

        url = page.url
        if "suspended" in url:
            status = "suspended"
        elif re.search(r"login|flow/login|verify|challenge", url):
            status = "invalid"
        elif await page.locator('[data-testid="AppTabBar_Home_Link"]').first.is_visible():
            status = "valid"
        else:
            status = "invalid"
    except Exception as e:
        print("❌ Error:", e)
        status = "invalid"

    await context.close()
    await browser.close()
    return status


async def worker(pw, token, proxy, idx):
    status = await check_token(pw, token, proxy)
    print(f"[{idx}] {status.upper()} (proxy: {proxy['server'] if proxy else 'none'})")
    return token, status


async def main():
    tokens = load_auth_tokens(TOKENS_FILE)
    proxies = load_proxies(PROXY_FILE)

    if not tokens:
        print("❌ Не знайдено жодного токена")
        return
    if not proxies:
        print("❌ Не знайдено жодного проксі")
        return

    results = []
    async with async_playwright() as pw:
        sem = asyncio.Semaphore(MAX_CONCURRENCY)

        async def run_with_sem(idx, token, proxy):
            async with sem:
                return await worker(pw, token, proxy, idx)

        tasks = []
        for i, token in enumerate(tokens, 1):
            proxy = proxies[(i - 1) % len(proxies)]  # кругове використання проксі
            tasks.append(run_with_sem(i, token, proxy))

        results = await asyncio.gather(*tasks)

    # розділяємо результати
    valid = [t for t, s in results if s == "valid"]
    invalid = [t for t, s in results if s == "invalid"]
    suspended = [t for t, s in results if s == "suspended"]

    def save_tokens(file, lst):
        with open(file, "w", encoding="utf-8") as f:
            for t in lst:
                line = t["auth_token"]
                if t.get("ct0"):
                    line += f":{t['ct0']}"
                f.write(line + "\n")

    save_tokens("valid.txt", valid)
    save_tokens("invalid.txt", invalid)
    save_tokens("suspended.txt", suspended)

    print(f"\n=== Результат ===\n✅ Валідні: {len(valid)}\n❌ Невалідні: {len(invalid)}\n⛔ Suspended: {len(suspended)}")


if __name__ == "__main__":
    asyncio.run(main())
