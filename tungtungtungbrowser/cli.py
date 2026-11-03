import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

from .guest import Guest, GuestError, log_options, web_url
from .browser import Browser


def emit(value):
    print(json.dumps(value, indent=2, ensure_ascii=False))


def capture_logs(guest, destination, **options):
    options = log_options(**options)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    started = time.monotonic()
    report = {"status": "capturing", "started_at": datetime.now(timezone.utc).isoformat(),
              "options": options}
    with os.fdopen(descriptor, "w") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.flush()
        try:
            report["result"] = guest.logs(**options)
            report["status"] = "complete"
        except Exception as exc:
            report.update(status="failed", error=str(exc))
            raise
        finally:
            report["elapsed_seconds"] = time.monotonic() - started
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            output.seek(0)
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.truncate()
    return {"output": str(destination), "count": report["result"]["count"],
            "truncated": report["result"]["truncated"],
            "elapsed_seconds": report["elapsed_seconds"]}


def capture(guest, url, destination, settle, *, accessibility=False, ocr=False,
            qmp_socket=None, display_timeout=15):
    web_url(url)
    # Create an exclusive evidence directory so a run never overwrites older evidence.
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {"started_at": datetime.now(timezone.utc).isoformat(), "url": url,
                "browser": "com.apple.mobilesafari", "navigation_verified": False,
                "page_javascript_injected": False, "artifacts": [],
                "optional_diagnostics": {"accessibility": accessibility, "ocr": ocr}}
    try:
        manifest["health"] = guest.health()
        manifest["unlock"] = guest.call("screen.unlock", timeout=60)
        if manifest["unlock"].get("locked") is not False:
            raise GuestError("Screen remains locked")
        manifest["navigation_request"] = guest.open(url)
        time.sleep(settle)
        # Preserve the required screenshot before potentially slow native queries.
        shot = Browser(guest=guest, qmp_socket=qmp_socket,
                       display_timeout=display_timeout).screenshot()
        image_name = "screenshot.png" if qmp_socket is not None else "screenshot.jpg"
        shot.save(destination / image_name)
        (destination / "screenshot.json").write_text(json.dumps(shot.metadata(), indent=2))
        manifest["artifacts"].extend([image_name, "screenshot.json"])
        queries = [("device.info", "device.json", {}),
                   ("apps.foreground", "foreground.json", {})]
        if accessibility:
            queries.append(("ui.tree", "accessibility.json",
                            {"max_elements": 30, "limit": 30, "visible_only": True}))
        if ocr:
            queries.append(("ui.ocr", "ocr.json", {}))
        for method, name, params in queries:
            try:
                data = guest.call(method, **params)
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


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tungtungtungbrowser")
    parser.add_argument("--cid", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--qmp-socket", type=Path, help="Use local QMP display capture (PNG) instead of guest JPEG")
    parser.add_argument("--display-timeout", type=float, default=15, help="QMP capture deadline in seconds (default: 15)")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("health")
    sub.add_parser("sync-clock", help="Offer the host's time once; preserve manual guest time settings")
    sub.add_parser("device")
    sub.add_parser("screen-state", help="Read native startup, lock and display notifications without input")
    sub.add_parser("home", help="Send one native Home-button event; inspect the display separately")
    sub.add_parser("launch", help="Unlock and request native Safari launch once, without navigating")
    logs = sub.add_parser("logs", help="Save a bounded capture of native unified logs")
    logs.add_argument("--output", type=Path, required=True)
    logs.add_argument("--seconds", type=float, default=2)
    logs.add_argument("--process", help="Case-insensitive process-name substring")
    logs.add_argument("--level", choices=["all", "debug", "info", "default", "notice", "error", "fault"], default="error")
    logs.add_argument("--max-lines", type=int, default=500)
    opening = sub.add_parser("open", help="Open HTTP(S) URL through iOS LaunchServices in Safari")
    opening.add_argument("url")
    going = sub.add_parser("goto", help="Navigate through native Safari URL opening")
    going.add_argument("url")
    going.add_argument("--reuse-tab", action="store_true", help="Experimental: use native address-field input")
    shot = sub.add_parser("screenshot", help="Save a native JPEG, or PNG with --qmp-socket, without navigating")
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
    capturing.add_argument("--accessibility", action="store_true", help="Also collect the native accessibility tree; may be slow")
    capturing.add_argument("--ocr", action="store_true", help="Also run native OCR; may be slow")
    trimming = sub.add_parser("services", help="Inspect or apply the upstream reversible service profile")
    trimming.add_argument("profile", choices=["status", "trimmed", "none"], default="status", nargs="?")
    args = parser.parse_args(argv)
    if not 0 < args.timeout <= 300:
        parser.error("timeout must be between 0 and 300 seconds")
    if not 0 < args.display_timeout <= 300:
        parser.error("display-timeout must be between 0 and 300 seconds")
    if args.action == "capture" and not 0 <= args.settle_seconds <= 60:
        parser.error("settle-seconds must be between 0 and 60")
    guest = Guest(args.cid, args.timeout)
    browser = Browser(guest=guest, qmp_socket=args.qmp_socket,
                      display_timeout=args.display_timeout)
    try:
        match args.action:
            case "health": emit(guest.health())
            case "sync-clock": emit(browser.sync_clock())
            case "device": emit(guest.call("device.info"))
            case "screen-state": emit(browser.screen_state())
            case "home": emit(browser.home())
            case "launch": emit(browser.launch())
            case "logs": emit(capture_logs(guest, args.output, seconds=args.seconds,
                                           process=args.process, level=args.level,
                                           max_lines=args.max_lines))
            case "open": emit(guest.open(args.url))
            case "goto": emit(browser.goto(args.url, reuse_tab=args.reuse_tab))
            case "screenshot": emit(browser.screenshot(args.output, overwrite=args.overwrite).metadata())
            case "type": emit(browser.type_text(args.text))
            case "key": emit(browser.press(args.name))
            case "swipe": emit(browser.swipe(args.x1, args.y1, args.x2, args.y2, seconds=args.seconds))
            case "tap": emit(browser.tap(args.x, args.y))
            case "capture": emit(capture(guest, args.url, args.output, args.settle_seconds,
                                         accessibility=args.accessibility, ocr=args.ocr,
                                         qmp_socket=args.qmp_socket, display_timeout=args.display_timeout))
            case "services":
                emit(guest.call("services.profile") if args.profile == "status" else
                     guest.call("services.profile.apply", profile=args.profile, force=True))
    except (GuestError, ValueError, OSError) as exc:
        print(f"tungtungtungbrowser: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
