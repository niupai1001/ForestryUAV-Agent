"""A symbol index built from Python's own parser, plus a lexical backend.

Three layers, because one search cannot answer every question, and pretending it can
is how "where is this defined" gets answered with a comment that mentions the name:

1. **lexical** -- ripgrep when it exists, a bounded file walk when it does not. Right
   for strings, error codes, config keys. Reports ``heuristic``.
2. **structural** -- a symbol index over ``ast``. Right for definition / references /
   callers within Python. Reports ``structural``.
3. **semantic** -- reserved for an LSP or SCIP backend (P3, not implemented). Until
   then, answers that would need it say so instead of guessing.

``ast`` is used rather than tree-sitter deliberately: this runtime is Python, the
standard library parses it exactly, and adding a native dependency to get a
less precise parse of the same files is not a trade worth making. Multi-language
support is the reason to add tree-sitter later, not a reason to start with it.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess

from .models import CodeLocation, Symbol

SCHEMA = """
CREATE TABLE IF NOT EXISTS code_files (
    path TEXT PRIMARY KEY, version TEXT, indexed_at REAL
);
CREATE TABLE IF NOT EXISTS code_symbols (
    id TEXT PRIMARY KEY, path TEXT, qualified_name TEXT, short_name TEXT,
    kind TEXT, start_line INTEGER, end_line INTEGER, version TEXT
);
CREATE TABLE IF NOT EXISTS code_relations (
    source_id TEXT, relation_type TEXT, target_text TEXT, confidence REAL
);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON code_symbols(short_name);
CREATE INDEX IF NOT EXISTS idx_symbols_path ON code_symbols(path);
CREATE INDEX IF NOT EXISTS idx_relations_target ON code_relations(target_text);
"""

SKIP_DIRS = {".git", ".runtime", "__pycache__", "node_modules", ".venv", "venv",
             ".pytest_cache", "dist", "build", ".mypy_cache"}


def file_version(path: Path) -> str:
    """A version for one file, from what the filesystem already tells us.

    Reused rather than invented: ``fs_edit`` already checks ``expected_version``, so a
    symbol invalidated by an edit is judged against the same notion of "changed".
    """
    try:
        stat = path.stat()
    except OSError:
        return "0"
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def python_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in sorted(Path(root).rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        found.append(path)
    return found


def _snippet(path: Path, start: int, end: int, limit: int = 6) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    end = min(end, start + limit)
    return "\n".join(lines[start - 1:end]).strip()[:600]


class SymbolIndex:
    """Definitions and call relations for one workspace, in SQLite."""

    def __init__(self, root: Path | str, database: Path | str | None = None):
        self.root = Path(root)
        self.database = Path(database) if database else self.root / ".runtime" / "code-index.sqlite3"
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def _connect(self):
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    # ------------------------------------------------------------------ indexing

    def index_paths(self, paths: list[Path]) -> int:
        """(Re)index the given files. Returns how many files were indexed."""
        indexed = 0
        with self._connect() as connection:
            for path in paths:
                try:
                    source = path.read_text(encoding="utf-8", errors="replace")
                    tree = ast.parse(source)
                except (OSError, SyntaxError, ValueError):
                    continue
                relative = self._relative(path)
                version = file_version(path)
                connection.execute(
                    "DELETE FROM code_symbols WHERE path=?", (relative,),
                )
                connection.execute(
                    "DELETE FROM code_relations WHERE source_id LIKE ?", (relative + ":%",),
                )
                rows = []
                relations = []
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        kind = "method" if self._is_method(tree, node) else "function"
                    elif isinstance(node, ast.ClassDef):
                        kind = "class"
                    else:
                        continue
                    qualified = self._qualified(tree, node)
                    identifier = f"{relative}:{qualified}"
                    rows.append((
                        identifier, relative, qualified, node.name, kind,
                        getattr(node, "lineno", 0), getattr(node, "end_lineno", 0), version,
                    ))
                    for call in self._calls_in(node):
                        relations.append((identifier, "calls", call, 0.8))
                connection.executemany(
                    "INSERT OR REPLACE INTO code_symbols"
                    "(id, path, qualified_name, short_name, kind, start_line, end_line,"
                    " version) VALUES (?,?,?,?,?,?,?,?)", rows,
                )
                connection.executemany(
                    "INSERT INTO code_relations(source_id, relation_type, target_text,"
                    " confidence) VALUES (?,?,?,?)", relations,
                )
                connection.execute(
                    "INSERT OR REPLACE INTO code_files(path, version, indexed_at)"
                    " VALUES (?,?,?)", (relative, version, 0.0),
                )
                indexed += 1
        return indexed

    def index_workspace(self, limit: int = 2000) -> int:
        return self.index_paths(python_files(self.root)[:limit])

    def invalidate(self, path: str) -> None:
        """Drop what is known about one file; it is re-indexed on next use."""
        relative = self._relative(Path(path))
        with self._connect() as connection:
            connection.execute("DELETE FROM code_symbols WHERE path=?", (relative,))
            connection.execute("DELETE FROM code_relations WHERE source_id LIKE ?",
                               (relative + ":%",))
            connection.execute("DELETE FROM code_files WHERE path=?", (relative,))

    # -------------------------------------------------------------------- lookup

    def find_symbols(self, name: str, limit: int = 10) -> list[Symbol]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM code_symbols WHERE short_name=? "
                "ORDER BY path LIMIT ?", (name, limit),
            ).fetchall()
            exact = [self._symbol(row) for row in rows]
            if exact:
                return exact
            rows = connection.execute(
                "SELECT * FROM code_symbols WHERE short_name LIKE ? "
                "ORDER BY path LIMIT ?", (f"%{name}%", limit),
            ).fetchall()
        return [self._symbol(row) for row in rows]

    def symbol_by_id(self, identifier: str) -> Symbol | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM code_symbols WHERE id=?", (identifier,),
            ).fetchone()
        return self._symbol(row) if row else None

    def definitions(self, name: str, limit: int = 10) -> list[CodeLocation]:
        return [self._location(symbol) for symbol in self.find_symbols(name, limit)]

    def references(self, name: str, limit: int = 20) -> list[CodeLocation]:
        """Places a name is mentioned, from the index where possible."""
        locations = self.definitions(name, limit)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT source_id FROM code_relations WHERE target_text=? "
                "LIMIT ?", (name, limit),
            ).fetchall()
            for row in rows:
                source = self.symbol_by_id(row["source_id"])
                if source is not None:
                    locations.append(self._location(source))
        return locations

    def callers(self, name: str, limit: int = 20) -> list[CodeLocation]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT source_id FROM code_relations WHERE target_text=? "
                "AND relation_type='calls' LIMIT ?", (name, limit),
            ).fetchall()
        out = []
        for row in rows:
            source = self.symbol_by_id(row["source_id"])
            if source is not None:
                out.append(self._location(source))
        return out

    # ------------------------------------------------------------------- helpers

    def _relative(self, path: Path) -> str:
        try:
            return str(Path(path).resolve().relative_to(self.root.resolve()))
        except (ValueError, OSError):
            return str(path)

    @staticmethod
    def _is_method(tree: ast.AST, node: ast.AST) -> bool:
        for parent in ast.walk(tree):
            if isinstance(parent, ast.ClassDef) and node in ast.iter_child_nodes(parent):
                return True
        return False

    @staticmethod
    def _qualified(tree: ast.AST, node: ast.AST) -> str:
        parts = [node.name]
        for parent in ast.walk(tree):
            if isinstance(parent, ast.ClassDef) and node in ast.iter_child_nodes(parent):
                parts.insert(0, parent.name)
                break
        return ".".join(parts)

    @staticmethod
    def _calls_in(node: ast.AST) -> list[str]:
        names = []
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            target = child.func
            if isinstance(target, ast.Name):
                names.append(target.id)
            elif isinstance(target, ast.Attribute):
                names.append(target.attr)
        return names

    def _symbol(self, row) -> Symbol:
        return Symbol(
            id=row["id"], path=row["path"], qualified_name=row["qualified_name"],
            short_name=row["short_name"], kind=row["kind"],
            start_line=row["start_line"], end_line=row["end_line"],
            version=row["version"],
        )

    def _location(self, symbol: Symbol, precision: str = "structural") -> CodeLocation:
        return CodeLocation(
            path=symbol.path, start_line=symbol.start_line, end_line=symbol.end_line,
            snippet=_snippet(self.root / symbol.path, symbol.start_line, symbol.end_line),
            version=symbol.version, precision=precision,  # type: ignore[arg-type]
        )


class LexicalBackend:
    """ripgrep when available, a bounded walk otherwise.

    ripgrep is preferred for speed, not for correctness: both answer the same
    question, and a Run must not lose the ability to search because a binary is
    missing from an image.
    """

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.executable = shutil.which("rg")

    def search(self, pattern: str, limit: int = 50, glob: str = "*.py") -> list[CodeLocation]:
        if not pattern:
            return []
        if self.executable:
            return self._with_ripgrep(pattern, limit, glob)
        return self._with_walk(pattern, limit)

    def _with_ripgrep(self, pattern: str, limit: int, glob: str) -> list[CodeLocation]:
        try:
            completed = subprocess.run(
                [self.executable, "--line-number", "--no-heading", "--color", "never",
                 "--glob", glob, "--fixed-strings", pattern, "."],
                cwd=str(self.root), capture_output=True, text=True, timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            return self._with_walk(pattern, limit)
        locations = []
        for line in completed.stdout.splitlines()[:limit]:
            match = re.match(r"^(.*?):(\d+):(.*)$", line)
            if not match:
                continue
            path, number, text = match.groups()
            locations.append(CodeLocation(
                path=path, start_line=int(number), end_line=int(number),
                snippet=text.strip()[:400], version="", precision="heuristic",
            ))
        return locations

    def _with_walk(self, pattern: str, limit: int) -> list[CodeLocation]:
        locations = []
        for path in python_files(self.root):
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for number, text in enumerate(lines, start=1):
                if pattern in text:
                    locations.append(CodeLocation(
                        path=self._relative(path), start_line=number, end_line=number,
                        snippet=text.strip()[:400], version=file_version(path),
                        precision="heuristic",
                    ))
                    if len(locations) >= limit:
                        return locations
        return locations

    def _relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.root.resolve()))
        except (ValueError, OSError):
            return str(path)


__all__ = ["LexicalBackend", "SymbolIndex", "file_version", "python_files"]
