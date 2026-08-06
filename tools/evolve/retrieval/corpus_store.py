"""Content-addressed corpus store for the real-log eval set.

The real-log eval rows are line-addressed against version-pinned corpora
(a scaffolded agent project, the installed ``google-adk`` tree). Scores
drift — and rows silently break — if evaluation resolves those against
whatever the host happens to have installed. This module pins them:

* :func:`snapshot` packs a corpus tree into a **deterministic** tar.gz
  (sorted entries, normalized metadata, zeroed gzip mtime) so the same
  tree always produces the same archive sha256, records per-file
  sha256s, and registers everything in ``<store>/index.json``.
* :func:`materialize` extracts a corpus by name, verifying the archive
  sha256 **and** every file hash, into a cache keyed by content hash —
  tampered or drifted stores fail loudly instead of skewing scores.

Caches (``__pycache__``, ``*.pyc``, ``.venv`` …) are excluded: they are
non-deterministic and agents' retrieval targets are source files.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

_EXCLUDE_DIRS = {"__pycache__", ".venv", ".git", "node_modules", ".ruff_cache"}
_EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in _EXCLUDE_DIRS for part in rel.parts):
            continue
        if path.suffix in _EXCLUDE_SUFFIXES:
            continue
        yield rel.as_posix(), path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_index(store_dir: Path | str) -> dict:
    """The store's corpus registry: name -> entry (archive, hashes, provenance)."""
    index_path = Path(store_dir) / "index.json"
    if index_path.exists():
        return json.loads(index_path.read_text())
    return {}


def snapshot(root: Path | str, name: str, store_dir: Path | str,
             provenance: str = "", force: bool = False) -> dict:
    """Pack ``root`` into the store as corpus ``name``; return its entry.

    Raises ``ValueError`` if *name* already exists in the index with a
    different archive hash — this prevents accidental wrong-generation
    scoring.  Pass ``force=True`` to allow an explicit overwrite.
    Re-snapshotting identical content (same hash) always succeeds.
    """
    root, store_dir = Path(root), Path(store_dir)
    store_dir.mkdir(parents=True, exist_ok=True)

    files: dict[str, str] = {}
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w") as tar:
        for rel, path in _iter_files(root):
            data = path.read_bytes()
            files[rel] = _sha256(data)
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))

    gz_buf = io.BytesIO()
    with gzip.GzipFile(fileobj=gz_buf, mode="wb", mtime=0) as gz:
        gz.write(tar_buf.getvalue())
    payload = gz_buf.getvalue()
    digest = _sha256(payload)

    index = load_index(store_dir)
    if name in index and index[name]["sha256"] != digest and not force:
        raise ValueError(
            f"corpus {name!r} already exists in the store with a different "
            f"archive hash (existing={index[name]['sha256'][:12]}..., "
            f"new={digest[:12]}...). Use force=True to overwrite."
        )

    archive = f"{name}-{digest[:12]}.tar.gz"
    (store_dir / archive).write_bytes(payload)
    entry = {
        "archive": archive,
        "sha256": digest,
        "num_files": len(files),
        "provenance": provenance,
        "files": files,
    }
    index[name] = entry
    (store_dir / "index.json").write_text(json.dumps(index, indent=1, sort_keys=True))
    return entry


def materialize(name: str, store_dir: Path | str, cache_dir: Path | str) -> Path:
    """Extract corpus ``name`` (verified) into the cache; return its root."""
    store_dir, cache_dir = Path(store_dir), Path(cache_dir)
    entry = load_index(store_dir)[name]

    dest = cache_dir / f"{name}-{entry['sha256'][:12]}"
    marker = dest / ".corpus_verified"
    if marker.exists():
        return dest

    payload = (store_dir / entry["archive"]).read_bytes()
    if _sha256(payload) != entry["sha256"]:
        raise ValueError(
            f"corpus {name!r}: archive sha256 mismatch — store is corrupt or tampered"
        )
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(payload))) as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extraction_filter = tarfile.data_filter
            tar.extractall(dest)
        else:
            # Python < 3.12: manual path-traversal guard (C17 fix).
            safe = []
            for member in tar.getmembers():
                if ".." in member.name or member.name.startswith("/"):
                    raise ValueError(
                        f"corpus {name!r}: refusing to extract unsafe "
                        f"path {member.name!r}"
                    )
                safe.append(member)
            tar.extractall(dest, members=safe)
    for rel, expected in entry["files"].items():
        actual = _sha256((dest / rel).read_bytes())
        if actual != expected:
            raise ValueError(
                f"corpus {name!r}: file {rel} sha256 mismatch after extraction"
            )
    marker.write_text(entry["sha256"])
    return dest
