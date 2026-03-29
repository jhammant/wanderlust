#!/usr/bin/env python3
"""Take screenshots of the Wanderlust web UI for LinkedIn post - 1400x900 viewport."""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

OUTPUT_DIR = Path("/Users/jhammant/dev/wanderlust/docs/screenshots")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
BASE_URL = "http://127.0.0.1:5555"

async def take_screenshot(page, name: str, wait: float = 1.0):
    await page.wait_for_timeout(int(wait * 1000))
    path = OUTPUT_DIR / f"{name}.png"
    await page.screenshot(path=str(path), full_page=True)
    print(f"Saved: {path}")
    await asyncio.sleep(0.5)

async def scroll_to_section(page, section_index: int):
    await page.evaluate(f"window.scrollTo(0, {800 * section_index})")
    await asyncio.sleep(0.5)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1400, "height": 900},
            device_scale_factor=1,
        )
        page = await context.new_page()
        
        print("📸 Taking Wanderlust screenshots with 1400x900 viewport...")
        
        # Screenshot 1: Hero section
        print("1. Hero & Travel DNA")
        await page.goto(BASE_URL)
        await scroll_to_section(page, 0)
        await take_screenshot(page, "linkedin-hero", wait=2.0)
        
        # Screenshot 2: Map section
        print("2. Interactive map")
        await scroll_to_section(page, 1)
        await take_screenshot(page, "linkedin-map", wait=1.5)
        
        # Screenshot 3: Timeline section
        print("3. Travel timeline (2019-2025)")
        await scroll_to_section(page, 2)
        await take_screenshot(page, "linkedin-timeline", wait=1.5)
        
        # Screenshot 4: AI Recommendations
        print("4. AI Recommendations")
        await scroll_to_section(page, 3)
        await take_screenshot(page, "linkedin-ai-recommendations", wait=1.5)
        
        # Screenshot 5: Travel Insights
        print("5. Travel insights & quote wall")
        await scroll_to_section(page, 4)
        await take_screenshot(page, "linkedin-insights", wait=1.5)
        
        # Screenshot 6: Full page
        print("6. Full application view")
        await page.goto(BASE_URL)
        await take_screenshot(page, "linkedin-full", wait=2.0)
        
        await browser.close()
    
    print(f"\n✅ All screenshots saved to {OUTPUT_DIR}")
    for f in sorted(OUTPUT_DIR.glob("*.png"), key=lambda x: x.stat().st_size):
        print(f"   - {f.name} ({f.stat().st_size / 1024:.1f} KB)")

if __name__ == "__main__":
    asyncio.run(main())
