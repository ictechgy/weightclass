"""Small shared exception definitions for process ownership boundaries."""


class ChildStatusLostError(OSError):
    """Raised when an owned direct child's real wait status is unavailable."""
