"""Explicit project memory and source-grounded local knowledge retrieval."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import uuid

from .exec.paths import is_within
from .storage import AssetError


TEXT_SUFFIXES = {".txt", ".md", ".rst", ".csv", ".tsv", ".json", ".yaml", ".yml", ".py", ".r"}
HTML_SUFFIXES = {".html", ".htm"}
PDF_SUFFIXES = {".pdf"}
KNOWLEDGE_INDEX_VERSION = "stable-citations-v3"


class _HTMLText(HTMLParser):
    """Small dependency-free extractor for maintainer-approved HTML sources."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.hidden = 0
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden += 1
        elif tag == "title":
            self.in_title = True
        elif tag in {"p", "div", "section", "article", "li", "br", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self.hidden:
            self.hidden -= 1
        elif tag == "title":
            self.in_title = False
        elif tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.hidden:
            return
        value = " ".join(data.split())
        if not value:
            return
        self.parts.append(value + " ")
        if self.in_title:
            self.title_parts.append(value)

    def result(self) -> tuple[str, str | None]:
        text = re.sub(r"\n\s*\n+", "\n\n", "".join(self.parts)).strip()
        title = " ".join(self.title_parts).strip() or None
        return text, title


def _tokens(text: str) -> list[str]:
    latin = re.findall(r"[A-Za-z0-9_]{2,}", text.casefold())
    chinese = re.findall(r"[\u3400-\u9fff]", text)
    bigrams = ["".join(chinese[index:index + 2]) for index in range(max(0, len(chinese) - 1))]
    return list(dict.fromkeys([*latin, *bigrams, *chinese]))[:80]


def _chunks(text: str, size: int = 1800, overlap: int = 200):
    start = 0
    ordinal = 1
    while start < len(text):
        end = min(len(text), start + size)
        yield ordinal, text[start:end]
        if end == len(text):
            break
        start = max(start + 1, end - overlap)
        ordinal += 1


def _section_for(text: str, start: int) -> str | None:
    headings = list(re.finditer(r"(?m)^\s{0,3}#{1,6}\s+(.+?)\s*$", text[:start]))
    return headings[-1].group(1).strip() if headings else None


class MemoryManager:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "projects.sqlite3"
        self.embedding_model = os.getenv("KNOWLEDGE_EMBEDDING_MODEL", "qwen3-embedding:0.6b")
        self.embedding_dim = int(os.getenv("KNOWLEDGE_EMBEDDING_DIM", "1024"))
        self._vec_available = False
        with self.db() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS projects(
                id TEXT PRIMARY KEY,owner TEXT NOT NULL,name TEXT NOT NULL,
                created_at REAL NOT NULL,updated_at REAL NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS session_projects(
                owner TEXT NOT NULL,chat_id TEXT NOT NULL,project_id TEXT,
                updated_at REAL NOT NULL,PRIMARY KEY(owner,chat_id))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS memories(
                id TEXT PRIMARY KEY,project_id TEXT NOT NULL,content TEXT NOT NULL,
                version INTEGER NOT NULL,state TEXT NOT NULL,
                created_at REAL NOT NULL,updated_at REAL NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS memory_versions(
                memory_id TEXT NOT NULL,project_id TEXT NOT NULL,version INTEGER NOT NULL,
                content TEXT NOT NULL,state TEXT NOT NULL,created_at REAL NOT NULL,
                PRIMARY KEY(memory_id,version))""")
            conn.execute("""INSERT OR IGNORE INTO memory_versions(
                memory_id,project_id,version,content,state,created_at)
                SELECT id,project_id,version,content,state,updated_at FROM memories""")
            conn.execute("""CREATE TABLE IF NOT EXISTS knowledge_sources(
                id TEXT PRIMARY KEY,project_id TEXT NOT NULL,kind TEXT NOT NULL,
                locator TEXT NOT NULL,state TEXT NOT NULL,version TEXT,error TEXT,
                created_at REAL NOT NULL,updated_at REAL NOT NULL,
                UNIQUE(project_id,locator))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS knowledge_chunks(
                id TEXT PRIMARY KEY,project_id TEXT NOT NULL,source_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,content TEXT NOT NULL,citation TEXT NOT NULL,
                content_hash TEXT NOT NULL,created_at REAL NOT NULL)""")
            chunk_columns = {
                row["name"] for row in conn.execute(
                    "PRAGMA table_info(knowledge_chunks)"
                ).fetchall()
            }
            for name, definition in {
                "document": "TEXT",
                "title": "TEXT",
                "page": "INTEGER",
                "section": "TEXT",
                "source_version": "TEXT",
            }.items():
                if name not in chunk_columns:
                    conn.execute(
                        f"ALTER TABLE knowledge_chunks ADD COLUMN {name} {definition}"
                    )
            conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                chunk_id UNINDEXED,project_id UNINDEXED,search_text,content UNINDEXED,
                tokenize='unicode61')""")
            conn.execute("""UPDATE knowledge_sources SET state='failed',
                error='Indexing was interrupted by a Runtime restart',updated_at=?
                WHERE state='indexing'""", (time.time(),))
            self._load_vec(conn)

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.database, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _load_vec(self, conn) -> bool:
        try:
            import sqlite_vec
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            conn.execute(f"""CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_vec USING vec0(
                chunk_id TEXT PRIMARY KEY,embedding FLOAT[{self.embedding_dim}])""")
            self._vec_available = True
        except Exception:
            self._vec_available = False
        return self._vec_available

    def _project(self, owner: str, project_id: str) -> dict:
        with self.db() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id=? AND owner=?", (project_id, owner)).fetchone()
        if not row:
            raise AssetError("Project not found or not accessible")
        return dict(row)

    def create_project(self, owner: str, name: str) -> dict:
        project_id = "project_" + uuid.uuid4().hex
        now = time.time()
        with self.db() as conn:
            conn.execute("INSERT INTO projects VALUES(?,?,?,?,?)", (project_id, owner, name.strip(), now, now))
        return self._project(owner, project_id)

    def projects(self, owner: str) -> list[dict]:
        with self.db() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM projects WHERE owner=? ORDER BY updated_at DESC", (owner,)).fetchall()]

    def delete_project(self, owner: str, project_id: str) -> None:
        self._project(owner, project_id)
        with self.db() as conn:
            source_ids = [row[0] for row in conn.execute("SELECT id FROM knowledge_sources WHERE project_id=?", (project_id,)).fetchall()]
            chunk_ids = [row[0] for row in conn.execute("SELECT id FROM knowledge_chunks WHERE project_id=?", (project_id,)).fetchall()]
            conn.execute("UPDATE session_projects SET project_id=NULL WHERE owner=? AND project_id=?", (owner, project_id))
            conn.execute("DELETE FROM memories WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM memory_versions WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM knowledge_fts WHERE project_id=?", (project_id,))
            if self._load_vec(conn):
                for chunk_id in chunk_ids:
                    conn.execute("DELETE FROM knowledge_vec WHERE chunk_id=?", (chunk_id,))
            conn.execute("DELETE FROM knowledge_chunks WHERE project_id=?", (project_id,))
            for source_id in source_ids:
                conn.execute("DELETE FROM knowledge_sources WHERE id=?", (source_id,))
            conn.execute("DELETE FROM projects WHERE id=?", (project_id,))

    def select_project(self, owner: str, chat_id: str, project_id: str | None) -> dict:
        if project_id:
            self._project(owner, project_id)
        with self.db() as conn:
            conn.execute("""INSERT INTO session_projects(owner,chat_id,project_id,updated_at)
                VALUES(?,?,?,?) ON CONFLICT(owner,chat_id) DO UPDATE SET
                project_id=excluded.project_id,updated_at=excluded.updated_at""", (owner, chat_id, project_id, time.time()))
        return {"chat_id": chat_id, "project_id": project_id}

    def selected_project(self, owner: str, chat_id: str) -> dict | None:
        with self.db() as conn:
            row = conn.execute("""SELECT p.* FROM session_projects s JOIN projects p ON p.id=s.project_id
                WHERE s.owner=? AND s.chat_id=?""", (owner, chat_id)).fetchone()
        return dict(row) if row else None

    def forget_session(self, chat_id: str) -> None:
        """Remove only the binding; project memories and knowledge remain."""
        with self.db() as conn:
            conn.execute("DELETE FROM session_projects WHERE chat_id=?", (chat_id,))

    def memories(self, owner: str, project_id: str) -> list[dict]:
        self._project(owner, project_id)
        with self.db() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM memories WHERE project_id=? AND state='confirmed' ORDER BY updated_at DESC", (project_id,)).fetchall()]

    def add_memory(self, owner: str, project_id: str, content: str, confirmed: bool) -> dict:
        self._project(owner, project_id)
        if not confirmed:
            raise AssetError("Durable memory requires explicit confirmation")
        memory_id = "memory_" + uuid.uuid4().hex
        now = time.time()
        with self.db() as conn:
            conn.execute("INSERT INTO memories VALUES(?,?,?,?,?,?,?)", (memory_id, project_id, content.strip(), 1, "confirmed", now, now))
            conn.execute(
                "INSERT INTO memory_versions VALUES(?,?,?,?,?,?)",
                (memory_id, project_id, 1, content.strip(), "confirmed", now),
            )
            row = conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        return dict(row)

    def update_memory(self, owner: str, project_id: str, memory_id: str, content: str, confirmed: bool) -> dict:
        self._project(owner, project_id)
        if not confirmed:
            raise AssetError("Memory edits require explicit confirmation")
        with self.db() as conn:
            existing = conn.execute(
                "SELECT * FROM memories WHERE id=? AND project_id=?",
                (memory_id, project_id),
            ).fetchone()
            if existing is None:
                raise AssetError("Memory not found")
            version = int(existing["version"]) + 1
            now = time.time()
            conn.execute(
                "UPDATE memories SET content=?,version=?,updated_at=? WHERE id=?",
                (content.strip(), version, now, memory_id),
            )
            conn.execute(
                "INSERT INTO memory_versions VALUES(?,?,?,?,?,?)",
                (memory_id, project_id, version, content.strip(), "confirmed", now),
            )
            row = conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        return dict(row)

    def delete_memory(self, owner: str, project_id: str, memory_id: str) -> None:
        self._project(owner, project_id)
        with self.db() as conn:
            existing = conn.execute(
                "SELECT * FROM memories WHERE id=? AND project_id=?",
                (memory_id, project_id),
            ).fetchone()
            if existing is None:
                raise AssetError("Memory not found")
            conn.execute(
                "INSERT INTO memory_versions VALUES(?,?,?,?,?,?)",
                (memory_id, project_id, int(existing["version"]) + 1,
                 existing["content"], "deleted", time.time()),
            )
            conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))

    def memory_history(self, owner: str, project_id: str,
                       memory_id: str) -> list[dict]:
        self._project(owner, project_id)
        with self.db() as conn:
            rows = conn.execute(
                """SELECT version,content,state,created_at FROM memory_versions
                WHERE project_id=? AND memory_id=? ORDER BY version DESC""",
                (project_id, memory_id),
            ).fetchall()
        if not rows:
            raise AssetError("Memory history not found")
        return [dict(row) for row in rows]

    def _allowed_local(self, locator: str) -> Path:
        target = Path(locator).resolve()
        configured = [Path(item).resolve() for item in os.getenv("KNOWLEDGE_ROOTS", "").split(os.pathsep) if item.strip()]
        if not configured or not any(is_within(target, root) for root in configured):
            raise AssetError("Local knowledge path is outside maintainer-configured KNOWLEDGE_ROOTS")
        return target

    @staticmethod
    def _pdf_documents(raw: bytes, document: str):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        title = None
        if reader.metadata:
            title = str(reader.metadata.title or "").strip() or None
        extracted = 0
        for page_number, page in enumerate(reader.pages, 1):
            text = (page.extract_text() or "").strip()
            if not text:
                continue
            extracted += 1
            yield {
                "document": document, "text": text, "page": page_number,
                "title": title,
            }
        if not extracted:
            raise AssetError(
                f"PDF contains no extractable text and requires OCR: {document}"
            )

    @staticmethod
    def _html_document(raw: bytes, document: str, encoding: str = "utf-8"):
        parser = _HTMLText()
        parser.feed(raw.decode(encoding, errors="replace"))
        text, title = parser.result()
        if text:
            yield {
                "document": document, "text": text, "page": None,
                "title": title,
            }

    def _documents(self, kind: str, locator: str):
        if kind == "url":
            parsed = urlparse(locator)
            if parsed.scheme not in {"http", "https"}:
                raise AssetError("Knowledge URL must use http or https")
            request = Request(locator, headers={"User-Agent": "Forestry-Agent-Knowledge/1"})
            with urlopen(request, timeout=30) as response:
                raw = response.read(32 * 1024 * 1024 + 1)
                if len(raw) > 32 * 1024 * 1024:
                    raise AssetError("Knowledge URL exceeds 32 MiB")
                content_type = response.headers.get_content_type().casefold()
                if content_type == "application/pdf" or parsed.path.casefold().endswith(".pdf"):
                    yield from self._pdf_documents(raw, locator)
                elif content_type in {"text/html", "application/xhtml+xml"}:
                    yield from self._html_document(
                        raw, locator,
                        response.headers.get_content_charset() or "utf-8",
                    )
                else:
                    yield {
                        "document": locator,
                        "text": raw.decode(
                            response.headers.get_content_charset() or "utf-8"
                        ),
                        "page": None, "title": None,
                    }
            return
        target = self._allowed_local(locator)
        paths = [target] if target.is_file() else sorted(target.rglob("*"))
        for path in paths[:1000]:
            suffix = path.suffix.casefold()
            if (
                not path.is_file()
                or suffix not in TEXT_SUFFIXES | HTML_SUFFIXES | PDF_SUFFIXES
                or path.stat().st_size > 32 * 1024 * 1024
            ):
                continue
            try:
                if suffix in PDF_SUFFIXES:
                    yield from self._pdf_documents(path.read_bytes(), str(path))
                elif suffix in HTML_SUFFIXES:
                    yield from self._html_document(path.read_bytes(), str(path))
                else:
                    yield {
                        "document": str(path),
                        "text": path.read_text(encoding="utf-8"),
                        "page": None,
                        "title": path.stem,
                    }
            except UnicodeDecodeError:
                continue

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        base = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434").rstrip("/")
        vectors = []
        for start in range(0, len(texts), 32):
            batch = texts[start:start + 32]
            request_data = {"model": self.embedding_model, "input": batch}
            if start + 32 >= len(texts):
                request_data["keep_alive"] = os.getenv(
                    "KNOWLEDGE_EMBED_KEEP_ALIVE", "0s"
                )
            payload = json.dumps(request_data).encode()
            request = Request(base + "/api/embed", data=payload, headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=60) as response:
                embedded = json.loads(response.read().decode("utf-8")).get("embeddings") or []
            if len(embedded) != len(batch) or any(
                len(vector) != self.embedding_dim for vector in embedded
            ):
                raise AssetError("Embedding response dimension/count does not match configuration")
            vectors.extend(embedded)
        return vectors

    def queue_source(self, owner: str, project_id: str, kind: str,
                     locator: str) -> dict:
        self._project(owner, project_id)
        if kind not in {"local", "url"}:
            raise AssetError("Knowledge source kind must be local or url")
        source_id = "source_" + uuid.uuid4().hex
        now = time.time()
        with self.db() as conn:
            existing = conn.execute("SELECT id FROM knowledge_sources WHERE project_id=? AND locator=?", (project_id, locator)).fetchone()
            if existing:
                source_id = existing["id"]
                conn.execute("UPDATE knowledge_sources SET kind=?,state='indexing',error=NULL,updated_at=? WHERE id=?", (kind, now, source_id))
            else:
                conn.execute("INSERT INTO knowledge_sources VALUES(?,?,?,?,?,?,?,?,?)", (source_id, project_id, kind, locator, "indexing", None, None, now, now))
            row = conn.execute(
                "SELECT * FROM knowledge_sources WHERE id=?", (source_id,)
            ).fetchone()
        return dict(row)

    def index_source(self, owner: str, project_id: str, kind: str, locator: str) -> dict:
        source_id = self.queue_source(owner, project_id, kind, locator)["id"]
        now = time.time()
        try:
            records = []
            digest = hashlib.sha256()
            digest.update(
                f"{KNOWLEDGE_INDEX_VERSION}:{self.embedding_model}:{self.embedding_dim}".encode()
            )
            for document_data in self._documents(kind, locator):
                document = document_data["document"]
                text = document_data["text"]
                page = document_data.get("page")
                title = document_data.get("title")
                digest.update(document.encode())
                digest.update(text.encode())
                for ordinal, content in _chunks(text):
                    chunk_id = "chunk_" + uuid.uuid4().hex
                    anchor = (
                        f"page={page}&segment={ordinal}"
                        if page is not None else f"segment={ordinal}"
                    )
                    # Keep the citation token ASCII-only so small local models can
                    # copy it exactly even when the source path contains CJK text.
                    # The original document locator remains alongside it.
                    citation = f"knowledge://{source_id}/{chunk_id}#{anchor}"
                    start = max(0, (ordinal - 1) * (1800 - 200))
                    records.append({
                        "id": chunk_id, "ordinal": ordinal,
                        "content": content, "citation": citation,
                        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                        "document": document, "title": title, "page": page,
                        "section": _section_for(text, start),
                    })
            if not records:
                raise AssetError("Knowledge source contained no supported UTF-8 text")
            version = digest.hexdigest()
            with self.db() as conn:
                current = conn.execute(
                    "SELECT * FROM knowledge_sources WHERE id=? AND project_id=?",
                    (source_id, project_id),
                ).fetchone()
                if current is None:
                    raise AssetError("Knowledge source was deleted while indexing")
                chunk_count = conn.execute(
                    "SELECT COUNT(*) FROM knowledge_chunks WHERE source_id=?",
                    (source_id,),
                ).fetchone()[0]
                vector_count = 0
                if self._load_vec(conn):
                    vector_count = conn.execute(
                        """SELECT COUNT(*) FROM knowledge_vec v JOIN knowledge_chunks c
                        ON c.id=v.chunk_id WHERE c.source_id=?""",
                        (source_id,),
                    ).fetchone()[0]
                unchanged = current["version"] == version and chunk_count == len(records)
                if unchanged and (not self._vec_available or vector_count == chunk_count):
                    state = "ready" if vector_count else "keyword_only"
                    conn.execute(
                        "UPDATE knowledge_sources SET state=?,error=NULL,updated_at=? WHERE id=?",
                        (state, time.time(), source_id),
                    )
                    row = conn.execute(
                        "SELECT * FROM knowledge_sources WHERE id=?", (source_id,)
                    ).fetchone()
                    return dict(row) | {"chunk_count": chunk_count, "unchanged": True}
            vectors = []
            vector_error = None
            if self._vec_available:
                try:
                    vectors = self._embed([record["content"] for record in records])
                except Exception as exc:
                    vector_error = str(exc)
            with self.db() as conn:
                if conn.execute(
                    "SELECT 1 FROM knowledge_sources WHERE id=? AND project_id=?",
                    (source_id, project_id),
                ).fetchone() is None:
                    raise AssetError("Knowledge source was deleted while indexing")
                old_ids = [row[0] for row in conn.execute("SELECT id FROM knowledge_chunks WHERE source_id=?", (source_id,)).fetchall()]
                conn.execute("DELETE FROM knowledge_fts WHERE chunk_id IN (SELECT id FROM knowledge_chunks WHERE source_id=?)", (source_id,))
                vec_ready = self._load_vec(conn)
                if vec_ready:
                    for chunk_id in old_ids:
                        conn.execute("DELETE FROM knowledge_vec WHERE chunk_id=?", (chunk_id,))
                conn.execute("DELETE FROM knowledge_chunks WHERE source_id=?", (source_id,))
                for index, record in enumerate(records):
                    conn.execute("""INSERT INTO knowledge_chunks(
                        id,project_id,source_id,ordinal,content,citation,
                        content_hash,created_at,document,title,page,section,source_version
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                        record["id"], project_id, source_id, record["ordinal"],
                        record["content"], record["citation"],
                        record["content_hash"], now, record["document"],
                        record["title"], record["page"], record["section"], version,
                    ))
                    conn.execute("INSERT INTO knowledge_fts(chunk_id,project_id,search_text,content) VALUES(?,?,?,?)", (record["id"], project_id, " ".join(_tokens(record["content"])), record["content"]))
                    if vectors and vec_ready:
                        import sqlite_vec
                        conn.execute("INSERT INTO knowledge_vec(chunk_id,embedding) VALUES(?,?)", (record["id"], sqlite_vec.serialize_float32(vectors[index])))
                if vectors and not vec_ready:
                    vector_error = "sqlite-vec was unavailable while storing embeddings"
                state = "ready" if vectors and vec_ready else "keyword_only"
                conn.execute("UPDATE knowledge_sources SET state=?,version=?,error=?,updated_at=? WHERE id=?", (state, version, vector_error, time.time(), source_id))
                row = conn.execute("SELECT * FROM knowledge_sources WHERE id=?", (source_id,)).fetchone()
            return dict(row) | {"chunk_count": len(records)}
        except Exception as exc:
            with self.db() as conn:
                conn.execute("UPDATE knowledge_sources SET state='failed',error=?,updated_at=? WHERE id=?", (str(exc), time.time(), source_id))
            raise

    def sources(self, owner: str, project_id: str) -> list[dict]:
        self._project(owner, project_id)
        with self.db() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM knowledge_sources WHERE project_id=? ORDER BY updated_at DESC", (project_id,)).fetchall()]

    def delete_source(self, owner: str, project_id: str, source_id: str) -> None:
        self._project(owner, project_id)
        with self.db() as conn:
            chunk_ids = [row[0] for row in conn.execute("SELECT id FROM knowledge_chunks WHERE source_id=? AND project_id=?", (source_id, project_id)).fetchall()]
            conn.execute("DELETE FROM knowledge_fts WHERE chunk_id IN (SELECT id FROM knowledge_chunks WHERE source_id=?)", (source_id,))
            if self._load_vec(conn):
                for chunk_id in chunk_ids:
                    conn.execute("DELETE FROM knowledge_vec WHERE chunk_id=?", (chunk_id,))
            conn.execute("DELETE FROM knowledge_chunks WHERE source_id=? AND project_id=?", (source_id, project_id))
            cursor = conn.execute("DELETE FROM knowledge_sources WHERE id=? AND project_id=?", (source_id, project_id))
        if not cursor.rowcount:
            raise AssetError("Knowledge source not found")

    def search(self, owner: str, project_id: str, query: str, limit: int = 6) -> dict:
        self._project(owner, project_id)
        terms = _tokens(query)
        keyword = []
        with self.db() as conn:
            if terms:
                expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
                keyword = [dict(row) for row in conn.execute("""SELECT c.id,c.source_id,c.ordinal,c.content,c.citation,
                    c.document,c.title,c.page,c.section,c.source_version,
                    bm25(knowledge_fts) AS score
                    FROM knowledge_fts JOIN knowledge_chunks c ON c.id=knowledge_fts.chunk_id
                    WHERE knowledge_fts MATCH ? AND knowledge_fts.project_id=? ORDER BY score LIMIT ?""", (expression, project_id, limit * 3)).fetchall()]
            semantic = []
            mode = "keyword"
            if self._load_vec(conn):
                try:
                    vector_count = conn.execute("""SELECT COUNT(*) FROM knowledge_vec v
                        JOIN knowledge_chunks c ON c.id=v.chunk_id
                        WHERE c.project_id=?""", (project_id,)).fetchone()[0]
                    if vector_count:
                        vector = self._embed([query])[0]
                        import sqlite_vec
                        total_vectors = conn.execute(
                            "SELECT COUNT(*) FROM knowledge_vec"
                        ).fetchone()[0]
                        rows = conn.execute("""SELECT chunk_id,distance FROM knowledge_vec
                            WHERE embedding MATCH ? AND k=?""", (
                                sqlite_vec.serialize_float32(vector), total_vectors,
                            )).fetchall()
                        for row in rows:
                            chunk = conn.execute("""SELECT id,source_id,ordinal,content,citation,
                                document,title,page,section,source_version,project_id
                                FROM knowledge_chunks WHERE id=?""", (row["chunk_id"],)).fetchone()
                            if chunk and chunk["project_id"] == project_id:
                                semantic.append(dict(chunk) | {"distance": row["distance"]})
                                if len(semantic) >= limit * 3:
                                    break
                        mode = "hybrid"
                except Exception:
                    semantic = []
        ranks: dict[str, float] = {}
        rows_by_id = {}
        for rank, row in enumerate(keyword, 1):
            ranks[row["id"]] = ranks.get(row["id"], 0) + 1 / (60 + rank)
            rows_by_id[row["id"]] = row
        for rank, row in enumerate(semantic, 1):
            ranks[row["id"]] = ranks.get(row["id"], 0) + 1 / (60 + rank)
            rows_by_id[row["id"]] = row
        results = [rows_by_id[key] | {"rank": score} for key, score in sorted(ranks.items(), key=lambda item: item[1], reverse=True)[:limit]]
        return {"mode": mode, "results": results}

    def read_chunks(
        self, owner: str, project_id: str, chunk_id: str,
        before: int = 1, after: int = 1,
    ) -> dict:
        self._project(owner, project_id)
        with self.db() as conn:
            target = conn.execute(
                "SELECT * FROM knowledge_chunks WHERE id=? AND project_id=?",
                (chunk_id, project_id),
            ).fetchone()
            if target is None:
                raise AssetError("Knowledge chunk not found in the selected project")
            rows = conn.execute("""SELECT id,source_id,ordinal,content,citation,
                document,title,page,section,source_version
                FROM knowledge_chunks
                WHERE project_id=? AND source_id=?
                  AND document IS ? AND page IS ?
                  AND ordinal BETWEEN ? AND ?
                ORDER BY ordinal""", (
                    project_id, target["source_id"],
                    target["document"], target["page"],
                    max(1, int(target["ordinal"]) - before),
                    int(target["ordinal"]) + after,
                )).fetchall()
        return {
            "chunk_id": chunk_id,
            "source_id": target["source_id"],
            "chunks": [dict(row) for row in rows],
        }

    def context(self, owner: str, chat_id: str) -> dict:
        """Return stable project context without performing retrieval.

        Knowledge text is exposed only through knowledge_search/knowledge_read so
        the model, rather than the request compiler, decides when it is needed.
        """
        project = self.selected_project(owner, chat_id)
        if not project:
            return {}
        memory = self.memories(owner, project["id"])
        sources = self.sources(owner, project["id"])
        return {
            "project_id": project["id"], "project_name": project["name"],
            "confirmed_memories": [{"id": item["id"], "content": item["content"], "version": item["version"]} for item in memory[:20]],
            "knowledge_sources": [
                {
                    key: item.get(key)
                    for key in ("id", "kind", "locator", "state", "version")
                }
                for item in sources[:20]
            ],
        }
