"""Native control of a CPU-rendered iOS Safari guest."""
from .browser import Browser, Screenshot
from .display import DisplayScreenshot, capture_display
from .guest import GuestError, GuestTransportError

__version__ = "0.1.0"
__all__ = ['Browser', 'Screenshot', 'DisplayScreenshot', 'capture_display', 'GuestError', 'GuestTransportError']
