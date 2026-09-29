"""GUI entry point plus offline packaging smoke test; never contact the live site in CI."""
import argparse
import json
import sys
from pathlib import Path


def smoke_test(output):
    import tkinter as tk
    from playwright.sync_api import sync_playwright
    from course_auto_assistant import __version__
    from course_auto_assistant.core import SiteConfig, starter_config
    result = {"version": __version__, "live_site_tested": False}
    try:
        root = tk.Tk()
        root.withdraw()
        root.update()
        root.destroy()
        result["tk"] = True
        assert not SiteConfig.parse(starter_config()).verified
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page()
            page.set_content('<h1>本地离线自检</h1>')
            assert page.locator("h1").inner_text() == "本地离线自检"
            browser.close()
        result["edge_driver"] = True
        result["ok"] = True
    except Exception as exc:
        result["ok"] = False
        result["error_type"] = type(exc).__name__
    Path(output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", metavar="REPORT_JSON")
    args = parser.parse_args()
    if args.self_test:
        sys.exit(smoke_test(args.self_test))
    from course_auto_assistant.app import run_gui
    run_gui()
