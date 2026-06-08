"""
Job Auto-Apply Agent — Naukri + LinkedIn
Platforms : Naukri.com, LinkedIn
Level     : Junior (1–3 years experience)
Features  : Auto-apply, keyword/role filter, skip already-applied jobs

Setup:
  pip install playwright python-dotenv
  playwright install chromium

Usage:
  python job_agent.py --platform both      # default
  python job_agent.py --platform naukri
  python job_agent.py --platform linkedin
"""

import asyncio
import csv
import os
import random
import argparse
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

load_dotenv()

# ─── CONFIG — edit this section to customise ─────────────────────────────────

CONFIG = {
    # Job titles / roles to search for
    "keywords": [
        "Site Reliability Engineer",
        "SRE",
        "DevOps Engineer",
        "Platform Engineer",
        "Cloud Engineer",
        "Infrastructure Engineer",
    ],

    # Only apply if the job title contains at least one of these (case-insensitive)
    # Leave empty [] to apply to all results
    "title_filter": ["SRE", "Site Reliability", "DevOps", "Platform", "Cloud", "Infrastructure"],

    # Only apply if company name does NOT contain these (blocklist)
    "company_blocklist": [],  # e.g. ["Some Company I Don't Want"]

    "location":         "Bengaluru",
    "experience_min":   1,   # years
    "experience_max":   3,   # years (Junior band)

    "max_applications_per_run": 25,   # safety cap per session
    "delay_between_apps":       (8, 18),  # random seconds between applications
    "headless":                 os.getenv("CI") == "true",  # auto True on GitHub Actions, False locally
    "applied_log":              "applied_jobs.csv",  # tracks applied job IDs
}

# ─── CREDENTIALS (from .env) ─────────────────────────────────────────────────

NAUKRI = {
    "email":    os.getenv("NAUKRI_EMAIL", ""),
    "password": os.getenv("NAUKRI_PASSWORD", ""),
}

LINKEDIN = {
    "email":    os.getenv("LINKEDIN_EMAIL", ""),
    "password": os.getenv("LINKEDIN_PASSWORD", ""),
}

# ─── APPLIED-JOB TRACKER (in-memory + CSV) ───────────────────────────────────

LOG_FILE = Path(CONFIG["applied_log"])

def load_applied() -> set:
    """Load job IDs already applied to from the CSV log."""
    if not LOG_FILE.exists():
        return set()
    with open(LOG_FILE, newline="", encoding="utf-8") as f:
        return {row["job_id"] for row in csv.DictReader(f) if row.get("job_id")}

def save_applied(platform, job_id, title, company, url, status):
    """Append one row to the applied jobs log."""
    is_new = not LOG_FILE.exists()
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f, fieldnames=["timestamp", "platform", "job_id", "title", "company", "url", "status"]
        )
        if is_new:
            w.writeheader()
        w.writerow({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "platform":  platform,
            "job_id":    job_id,
            "title":     title,
            "company":   company,
            "url":       url,
            "status":    status,
        })
    icon = "✓" if status == "applied" else "–"
    print(f"    {icon} [{status}] {title} @ {company}")

# ─── KEYWORD FILTER ──────────────────────────────────────────────────────────

def title_matches_filter(title: str) -> bool:
    """Return True if the job title passes the configured filter."""
    if not CONFIG["title_filter"]:
        return True
    title_lower = title.lower()
    return any(kw.lower() in title_lower for kw in CONFIG["title_filter"])

def company_is_blocked(company: str) -> bool:
    company_lower = company.lower()
    return any(b.lower() in company_lower for b in CONFIG["company_blocklist"])

# ─── HELPERS ─────────────────────────────────────────────────────────────────

async def delay(min_s=None, max_s=None):
    lo = min_s if min_s is not None else CONFIG["delay_between_apps"][0]
    hi = max_s if max_s is not None else CONFIG["delay_between_apps"][1]
    await asyncio.sleep(random.uniform(lo, hi))

async def human_type(locator, text: str):
    """Fill a field with human-like per-character delays."""
    await locator.click()
    await locator.fill("")
    for char in text:
        await locator.type(char, delay=random.randint(40, 110))

# ─── NAUKRI ──────────────────────────────────────────────────────────────────

async def naukri_login(page):
    print("\n[Naukri] Logging in …")
    await page.goto("https://www.naukri.com/nlogin/login", wait_until="domcontentloaded")
    await delay(2, 4)
    await human_type(
        page.locator('input[placeholder="Enter your active Email ID / Username"]'),
        NAUKRI["email"]
    )
    await delay(0.5, 1.5)
    await human_type(
        page.locator('input[placeholder="Enter your password"]'),
        NAUKRI["password"]
    )
    await delay(0.5, 1.5)
    await page.locator('button[type="submit"]').click()
    await page.wait_for_load_state("networkidle")
    print("[Naukri] Login complete.")

async def naukri_apply(page, applied: set) -> int:
    count = 0
    exp = CONFIG["experience_min"]  # use min of junior band for Naukri filter

    for keyword in CONFIG["keywords"]:
        if count >= CONFIG["max_applications_per_run"]:
            break

        print(f"\n[Naukri] Keyword: \"{keyword}\" | Location: {CONFIG['location']} | Exp: {exp}+ yrs")
        slug = keyword.lower().replace(" ", "-")
        city = CONFIG["location"].lower()
        url  = f"https://www.naukri.com/{slug}-jobs-in-{city}?experience={exp}"

        await page.goto(url, wait_until="domcontentloaded")
        await delay(2, 4)

        cards = await page.locator("article.jobTuple").all()
        print(f"  {len(cards)} listings found")

        for card in cards:
            if count >= CONFIG["max_applications_per_run"]:
                break
            try:
                job_id   = await card.get_attribute("data-job-id") or ""
                title_el = card.locator("a.title")
                title    = (await title_el.inner_text()).strip()
                company  = (await card.locator("a.subTitle").inner_text()).strip()
                job_url  = await title_el.get_attribute("href") or ""

                # ── filters ──
                if job_id in applied:
                    print(f"  [skip] Already applied — {title}")
                    continue
                if not title_matches_filter(title):
                    print(f"  [skip] Title filter — {title}")
                    continue
                if company_is_blocked(company):
                    print(f"  [skip] Blocked company — {company}")
                    continue

                # ── open job tab ──
                job_page = await page.context.new_page()
                await job_page.goto(job_url, wait_until="domcontentloaded")
                await delay(2, 4)

                apply_btn = job_page.locator(
                    'button:has-text("Apply"), a:has-text("Apply now"), button:has-text("Apply now")'
                )
                if await apply_btn.count() == 0:
                    save_applied("naukri", job_id, title, company, job_url, "no_apply_btn")
                    await job_page.close()
                    continue

                await apply_btn.first.click()
                await delay(2, 4)

                # step through any multi-page apply modal
                for _ in range(6):
                    nxt = job_page.locator(
                        'button:has-text("Next"), button:has-text("Submit application"), button:has-text("Apply")'
                    )
                    if await nxt.count() > 0:
                        await nxt.first.click()
                        await delay(1, 2)
                    else:
                        break

                applied.add(job_id)
                save_applied("naukri", job_id, title, company, job_url, "applied")
                count += 1

                await job_page.close()
                await delay()

            except PWTimeout:
                print("  [timeout] Skipping job")
            except Exception as e:
                print(f"  [error] {e}")

    return count

# ─── LINKEDIN ─────────────────────────────────────────────────────────────────

async def linkedin_login(page):
    print("\n[LinkedIn] Logging in …")
    await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
    await delay(2, 3)
    await human_type(page.locator("input#username"), LINKEDIN["email"])
    await delay(0.5, 1.5)
    await human_type(page.locator("input#password"), LINKEDIN["password"])
    await delay(0.5, 1.5)
    await page.locator('button[type="submit"]').click()
    await page.wait_for_load_state("networkidle")
    print("[LinkedIn] Login complete.")

async def linkedin_apply(page, applied: set) -> int:
    count = 0
    # LinkedIn experience level codes: 2=Entry, 3=Associate (covers 1-3 yrs junior band)
    exp_levels = "f_E=2%2C3"

    for keyword in CONFIG["keywords"]:
        if count >= CONFIG["max_applications_per_run"]:
            break

        print(f"\n[LinkedIn] Keyword: \"{keyword}\" | Location: {CONFIG['location']} | Level: Junior")
        kw_enc  = keyword.replace(" ", "%20")
        loc_enc = CONFIG["location"].replace(" ", "%20")
        url = (
            f"https://www.linkedin.com/jobs/search/"
            f"?keywords={kw_enc}&location={loc_enc}"
            f"&f_AL=true"        # Easy Apply only
            f"&{exp_levels}"
        )

        await page.goto(url, wait_until="domcontentloaded")
        await delay(2, 4)

        cards = await page.locator("li.jobs-search-results__list-item").all()
        print(f"  {len(cards)} listings found")

        for card in cards:
            if count >= CONFIG["max_applications_per_run"]:
                break
            try:
                await card.click()
                await delay(1, 2)

                job_id  = await card.get_attribute("data-occludable-job-id") or ""
                title   = (await page.locator(
                    "h1.job-details-jobs-unified-top-card__job-title"
                ).inner_text()).strip()
                company = (await page.locator(
                    "div.job-details-jobs-unified-top-card__company-name"
                ).inner_text()).strip()
                job_url = f"https://www.linkedin.com/jobs/view/{job_id}/"

                # ── filters ──
                if job_id in applied:
                    print(f"  [skip] Already applied — {title}")
                    continue
                if not title_matches_filter(title):
                    print(f"  [skip] Title filter — {title}")
                    continue
                if company_is_blocked(company):
                    print(f"  [skip] Blocked company — {company}")
                    continue

                # Easy Apply button
                apply_btn = page.locator('button.jobs-apply-button:has-text("Easy Apply")')
                if await apply_btn.count() == 0:
                    save_applied("linkedin", job_id, title, company, job_url, "no_easy_apply")
                    continue

                await apply_btn.click()
                await delay(1, 2)

                submitted = False
                for _ in range(8):
                    # fill phone number if empty
                    phone = page.locator('input[id*="phoneNumber"], input[name*="phone"]')
                    if await phone.count() > 0 and not (await phone.first.input_value()):
                        await human_type(phone.first, "9059941099")

                    # answer required yes/no radio questions (pick first option)
                    radios = page.locator('fieldset input[type="radio"]')
                    if await radios.count() > 0:
                        if not await radios.first.is_checked():
                            await radios.first.check()

                    # submit
                    submit = page.locator('button:has-text("Submit application")')
                    if await submit.count() > 0:
                        await submit.click()
                        submitted = True
                        await delay(1, 2)
                        break

                    # next / review
                    nxt = page.locator('button:has-text("Next"), button:has-text("Review")')
                    if await nxt.count() > 0:
                        await nxt.click()
                        await delay(1, 2)
                    else:
                        break

                status = "applied" if submitted else "incomplete"
                applied.add(job_id)
                save_applied("linkedin", job_id, title, company, job_url, status)
                if submitted:
                    count += 1

                # dismiss modal
                dismiss = page.locator('button[aria-label="Dismiss"], button[aria-label="Discard"]')
                if await dismiss.count() > 0:
                    await dismiss.first.click()

                await delay()

            except PWTimeout:
                print("  [timeout] Skipping job")
            except Exception as e:
                print(f"  [error] {e}")

    return count

# ─── MAIN ─────────────────────────────────────────────────────────────────────

async def run(platform: str):
    applied = load_applied()
    print(f"Tracking {len(applied)} previously applied jobs (will skip these).")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=CONFIG["headless"],
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page  = await context.new_page()
        total = 0

        if platform in ("naukri", "both"):
            if not NAUKRI["email"]:
                print("[Naukri] Skipped — add NAUKRI_EMAIL / NAUKRI_PASSWORD to .env")
            else:
                await naukri_login(page)
                n = await naukri_apply(page, applied)
                total += n
                print(f"\n[Naukri] Session total: {n} applications submitted.")

        if platform in ("linkedin", "both"):
            if not LINKEDIN["email"]:
                print("[LinkedIn] Skipped — add LINKEDIN_EMAIL / LINKEDIN_PASSWORD to .env")
            else:
                await linkedin_login(page)
                n = await linkedin_apply(page, applied)
                total += n
                print(f"\n[LinkedIn] Session total: {n} applications submitted.")

        await browser.close()

    print(f"\n{'─'*50}")
    print(f"  Run complete — {total} total applications this session.")
    print(f"  Log: {LOG_FILE.resolve()}")
    print(f"{'─'*50}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job Auto-Apply Agent — Naukri + LinkedIn")
    parser.add_argument(
        "--platform",
        choices=["naukri", "linkedin", "both"],
        default="both",
        help="Which platform(s) to run on (default: both)"
    )
    args = parser.parse_args()
    asyncio.run(run(args.platform))