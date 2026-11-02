"""Native control of a CPU-rendered iOS Safari guest."""
from .browser import Browser, Screenshot
from .guest import GuestError

__version__ = "0.1.0"
__all__ = ['Browser', 'Screenshot', 'GuestError']
