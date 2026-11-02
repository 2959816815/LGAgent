"""Background indexing for the local knowledge base."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import jieba
import numpy as np

from knowledge_embed import EmbeddingError
from knowledge_extract import chunk_units, discover_files, extract_units
from knowledge_store import ChunkInsert, KnowledgeError, KnowledgeStore

MAX_CHUNKS = 50_000


class _Cancelled(Exception):
    pass


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class IndexerProgress:
    state: str = "idle"
    total_files: int = 0
    processed_files: int = 0
    indexed_documents: int = 0
    failed_documents: int = 0
    current_file: str = ""
    started_at: str = ""
    finished_at: str = ""
    message: str = ""


class KnowledgeIndexer:
    def __init__(self, store: KnowledgeStore, embedder) -> None:
        self.store = store
        self.embedder = embedder
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._progress = IndexerProgress()

    def snapshot(self) -> dict:
        with self._lock:
            return asdict(self._progress)

    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self, rebuild: bool = False) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._cancel.clear()
            self._progress = IndexerProgress(
                state="scanning", started_at=_now()
            )
            self._thread = threading.Thread(
                target=self._run,
                args=(rebuild,),
                daemon=True,
                name="knowledge-indexer",
            )
            self._thread.start()
            return True

    def cancel(self) -> None:
        self._cancel.set()

    def _update(self, **fields) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(self._progress, key, value)

    def _run(self, rebuild: bool) -> None:
        indexed = 0
        failed = 0
        processed = 0
        try:
            status = self.embedder.status()
            if not status.available:
                raise EmbeddingError(status.reason)
            if rebuild:
                self.store.clear_documents()
            sources = self.store.list_sources()
            if not sources:
                self._finish("done", indexed, failed, processed)
                return

            files: list[tuple[int, Path, Path]] = []
            for source in sources:
                root = Path(source.path)
                for path in discover_files(root):
                    files.append((source.id, root, path))
            self._update(total_files=len(files))

            seen: dict[int, set[str]] = {source.id: set() for source in sources}
            for source_id, root, path in files:
                if self._cancel.is_set():
                    raise _Cancelled()
                absolute = str(path.resolve())
                seen[source_id].add(absolute)
                relative = path.relative_to(root).as_posix()
                try:
                    stat = path.stat()
                except OSError:
                    processed += 1
                    continue
                existing = self.store.get_document(source_id, absolute)
                if (
                    existing is not None
                    and existing.status != "error"
                    and existing.size == stat.st_size
                    and abs(existing.mtime - stat.st_mtime) < 1e-6
                ):
                    processed += 1
                    self._update(processed_files=processed)
                    continue
                digest = _file_hash(path)
                if (
                    existing is not None
                    and existing.status == "indexed"
                    and existing.content_hash == digest
                ):
                    self.store.touch_document(existing.id, stat.st_size, stat.st_mtime)
                    processed += 1
                    self._update(processed_files=processed)
                    continue

                self._update(state="extracting", current_file=relative)
                try:
                    drafts = chunk_units(extract_units(path))
                    if not drafts:
                        self.store.replace_document(
                            source_id,
                            absolute,
                            relative,
                            stat.st_size,
                            stat.st_mtime,
                            digest,
                            path.stem,
                            [],
                            status="empty",
                        )
                        processed += 1
                        self._update(processed_files=processed)
                        continue
                    existing_chunks = (
                        self.store.count_document_chunks(existing.id)
                        if existing is not None
                        else 0
                    )
                    if (
                        self.store.count_chunks()
                        - existing_chunks
                        + len(drafts)
                        > MAX_CHUNKS
                    ):
                        raise KnowledgeError("知识库已达 5 万段上限")
                    self._update(state="embedding", current_file=relative)
                    vectors: list[list[float]] = []
                    for start in range(0, len(drafts), 16):
                        if self._cancel.is_set():
                            raise _Cancelled()
                        batch = drafts[start : start + 16]
                        vectors.extend(
                            self.embedder.embed([draft.embed_text for draft in batch])
                        )
                    if len(vectors) != len(drafts):
                        raise EmbeddingError("嵌入数量与分块数量不一致")
                    dim = len(vectors[0])
                    self.store.set_embedding_dim(dim)
                    records = [
                        ChunkInsert(
                            text=draft.text,
                            tokens_text=" ".join(jieba.cut_for_search(draft.text)),
                            heading=draft.heading,
                            page=draft.page,
                            sheet=draft.sheet,
                            slide=draft.slide,
                            tokens=draft.tokens,
                            embedding=np.asarray(vector, dtype=np.float32).tobytes(),
                            dim=dim,
                            embedding_model=self.embedder.model,
                        )
                        for draft, vector in zip(drafts, vectors)
                    ]
                    self.store.replace_document(
                        source_id,
                        absolute,
                        relative,
                        stat.st_size,
                        stat.st_mtime,
                        digest,
                        path.stem,
                        records,
                        status="indexed",
                    )
                    indexed += 1
                except KnowledgeError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    self.store.mark_document_error(
                        source_id,
                        absolute,
                        relative,
                        stat.st_size,
                        stat.st_mtime,
                        digest,
                        path.stem,
                        str(exc),
                    )
                processed += 1
                self._update(
                    processed_files=processed,
                    indexed_documents=indexed,
                    failed_documents=failed,
                )

            for source in sources:
                self.store.delete_missing_documents(
                    source.id, seen.get(source.id, set())
                )
                self.store.touch_source(source.id)
            self._finish("done", indexed, failed, processed)
        except _Cancelled:
            self._finish("cancelled", indexed, failed, processed)
        except Exception as exc:  # noqa: BLE001
            self.store.bump_index_version()
            self._update(
                state="error",
                message=str(exc)[:500],
                finished_at=_now(),
                indexed_documents=indexed,
                failed_documents=failed,
                processed_files=processed,
            )

    def _finish(self, state: str, indexed: int, failed: int, processed: int) -> None:
        self.store.mark_indexed_at()
        self.store.bump_index_version()
        self._update(
            state=state,
            finished_at=_now(),
            indexed_documents=indexed,
            failed_documents=failed,
            processed_files=processed,
            current_file="",
        )
