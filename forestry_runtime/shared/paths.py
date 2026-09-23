"""Single definition of the path-containment invariant.

Ten call sites across the Runtime and the Host Bridge previously re-implemented
this check with three different idioms (``x != root and root not in x.parents``,
``try: x.relative_to(root)`` and ad-hoc ``PurePath`` comparisons). The gate that
verifies "unauthorized access is rejected" can only cover every call path when the
invariant has one definition, so it lives here and nothing re-implements it.

It sits in ``shared/`` rather than under either process because both need it and
neither may import the other: ``runtime`` is containerised while the Host Bridge
runs on the host, and the dependency-free rule for this package keeps that true.
"""

from __future__ import annotations

from pathlib import PurePath


def is_within(child: PurePath, root: PurePath) -> bool:
    """True when *child* is *root* itself or sits lexically inside *root*.

    Works for ``Path``, ``PurePosixPath`` and ``PureWindowsPath``. Includes the
    equality case because every call site treats "the root itself" as inside.

    **Callers must resolve first.** ``..`` is *not* resolved here: a path that
    still contains an unresolved ``..`` returns ``False`` even when textual
    prefixing would place it under *root*. That is deliberate — a lexical
    containment answer for an unresolved path is meaningless, and silently
    accepting ``root/../secrets`` is exactly the mistake this helper exists to
    prevent. Every real call site passes an already-resolved path, so the guard
    only ever rejects misuse.
    """
    if not isinstance(child, PurePath) or not isinstance(root, PurePath):
        raise TypeError("is_within expects PurePath instances")
    if ".." in child.parts or ".." in root.parts:
        return False
    return child == root or root in child.parents


__all__ = ["is_within"]
