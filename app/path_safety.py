"""Shared operator-configured-path safety checks; no filesystem writes."""


def symlinked(path):
    return any(part.is_symlink() for part in (path, *path.parents))
