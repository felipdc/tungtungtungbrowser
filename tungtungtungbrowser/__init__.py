"""Native control of a CPU-rendered iOS Safari guest."""
from .browser import Browser, Screenshot
from .display import DisplayScreenshot, capture_display
from .guest import GuestError, GuestTransportError
from .inspector import InspectorSession
from .inspector_transport import InspectorError, InspectorTransport
from .native_focus import NativeFocusReader

__version__ = "0.1.0"
__all__ = ['Browser', 'Screenshot', 'DisplayScreenshot', 'capture_display', 'GuestError', 'GuestTransportError',
           'InspectorSession', 'InspectorTransport', 'InspectorError', 'NativeFocusReader']
