"""User-facing errors. The CLI prints the message and exits 1."""


class SSHGetError(Exception):
    """Recoverable failure to show to the user."""
