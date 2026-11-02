import argparse
import base64
import binascii
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

from .guest import Guest, GuestError, web_url
from .browser import Browser


def emit(value):
    print(json.dumps(value, indent=2, ensure_ascii=False))


def capture(guest, url, destination, settle):
    web_url(url)
    # Create an exclusive evidence directory so a run never overwrites older evidence.
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {"started_at": datetime.now(timezone.utc).isoformat(), "url": url,
                "browser": "com.apple.mobilesafari", "navigation_verified": False,
                "page_javascript_injected": False, "artifacts": []}
    try:
        manifest["health"] = guest.health()
        manifest["unlock"] = guest.call("screen.unlock", timeout=60)
        if manifest["unlock"].get("locked") is not False:
            raise GuestError("Screen remains locked")
        manifest["navigation_request"] = guest.open(url)
        time.sleep(settle)
        for method, name in [("screen.screenshot", "screenshot.json"),
                             ("device.info", "device.json"), ("apps.foreground", "foreground.json"),
                             ("ui.tree", "accessibility.json"),
                             ("ui.ocr", "ocr.json")]:
            try:
                data = guest.call(method)
                if method == "screen.screenshot":
                    if not isinstance(data, dict) or data.get("mime_type") != "image/jpeg":
                        raise GuestError("Native screenshot did not report image/jpeg")
                    try:
                        jpeg = base64.b64decode(data["data"], validate=True)
                    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
                        raise GuestError("Native screenshot has invalid base64 data") from exc
                    if not jpeg.startswith(b'\xff\xd8') or not jpeg.endswith(b'\xff\xd9'):
                        raise GuestError("Native screenshot has invalid JPEG framing")
                    (destination / "screenshot.jpg").write_bytes(jpeg)
                    data = {key: value for key, value in data.items() if key != "data"}
                    manifest["artifacts"].append("screenshot.jpg")
                (destination / name).write_text(json.dumps(data, ensure_ascii=False, indent=2))
                manifest["artifacts"].append(name)
            except GuestError as exc:
                manifest.setdefault("capture_errors", {})[method] = str(exc)
        manifest["review_required"] = "Inspect the screenshot and available text for the expected page content; request success alone is not page success."
        return manifest
    except Exception as exc:
        manifest["error"] = str(exc)
        raise
    finally:
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(prog="tungtungtungbrowser")
    parser.add_argument("--cid", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=120)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("health")
    sub.add_parser("device")
    opening = sub.add_parser("open", help="Open HTTP(S) URL through iOS LaunchServices in Safari")
    opening.add_argument("url")
    going = sub.add_parser("goto", help="Navigate through native Safari URL opening")
    going.add_argument("url")
    going.add_argument("--reuse-tab", action="store_true", help="Experimental: use native address-field input")
    shot = sub.add_parser("screenshot", help="Save a native JPEG without navigating")
    shot.add_argument("output", type=Path)
    shot.add_argument("--overwrite", action="store_true")
    typing = sub.add_parser("type", help="Enter text through native Unicode HID")
    typing.add_argument("text")
    key = sub.add_parser("key", help="Press a native key, such as return or cmd+l")
    key.add_argument("name")
    swipe = sub.add_parser("swipe")
    for coordinate in ("x1", "y1", "x2", "y2"):
        swipe.add_argument(coordinate, type=float)
    swipe.add_argument("--seconds", type=float, default=0.5)
    tap = sub.add_parser("tap")
    tap.add_argument("x", type=float)
    tap.add_argument("y", type=float)
    capturing = sub.add_parser("capture", help="Collect native evidence; does not assert rendered-page success")
    capturing.add_argument("url")
    capturing.add_argument("--output", type=Path, required=True)
    capturing.add_argument("--settle-seconds", type=float, default=10)
    trimming = sub.add_parser("services", help="Inspect or apply the upstream reversible service profile")
    trimming.add_argument("profile", choices=["status", "trimmed", "none"], default="status", nargs="?")
    args = parser.parse_args()
    if not 0 < args.timeout <= 300:
        parser.error("timeout must be between 0 and 300 seconds")
    if args.action == "capture" and not 0 <= args.settle_seconds <= 60:
        parser.error("settle-seconds must be between 0 and 60")
    guest = Guest(args.cid, args.timeout)
    browser = Browser(guest=guest)
    try:
        match args.action:
            case "health": emit(guest.health())
            case "device": emit(guest.call("device.info"))
            case "open": emit(guest.open(args.url))
            case "goto": emit(browser.goto(args.url, reuse_tab=args.reuse_tab))
            case "screenshot": emit(browser.screenshot(args.output, overwrite=args.overwrite).metadata())
            case "type": emit(browser.type_text(args.text))
            case "key": emit(browser.press(args.name))
            case "swipe": emit(browser.swipe(args.x1, args.y1, args.x2, args.y2, seconds=args.seconds))
            case "tap": emit(browser.tap(args.x, args.y))
            case "capture": emit(capture(guest, args.url, args.output, args.settle_seconds))
            case "services":
                emit(guest.call("services.profile") if args.profile == "status" else
                     guest.call("services.profile.apply", profile=args.profile, force=True))
    except (GuestError, ValueError, OSError) as exc:
        print(f"tungtungtungbrowser: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
