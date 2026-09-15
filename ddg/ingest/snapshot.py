"""Immutable source snapshots.

Sources are copied, hashed and then only ever read. The hash is re-verified on
every run, so a source that changed under us is a visible event rather than a
silent one (F01).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import mimetypes
import shutil
from pathlib import Path

from ddg import PARSER_VERSION
from ddg.models import Node, SourceSnapshot

MEDIA_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".pdf": "application/pdf",
}


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_file(path: Path, package_id: str, blob_dir: Path) -> SourceSnapshot:
    """Copy a source into the blob store and record its snapshot."""
    path = Path(path)
    digest = file_hash(path)
    blob_dir.mkdir(parents=True, exist_ok=True)
    blob = blob_dir / f"{digest}{path.suffix}"
    if not blob.exists():
        shutil.copy2(path, blob)
        blob.chmod(0o444)  # read-only: the graph never writes back to a source

    media = MEDIA_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0]
    return SourceSnapshot(
        package_id=package_id,
        document_id=path.name,
        version_hash=digest,
        media_type=media or "application/octet-stream",
        import_time=dt.datetime.now(dt.UTC),
        parser_version=PARSER_VERSION,
        blob_path=str(blob),
    )


def ingest_file(
    path: Path, package_id: str, blob_dir: Path
) -> tuple[SourceSnapshot, list[Node]]:
    """Snapshot a file and parse it into nodes.

    An unsupported file is snapshotted and reported, never skipped in silence.
    """
    from ddg.ingest.docx_parser import parse_docx
    from ddg.ingest.pdf_parser import parse_pdf
    from ddg.ingest.xlsx_parser import parse_xlsx

    snap = snapshot_file(Path(path), package_id, blob_dir)
    suffix = Path(path).suffix.lower()
    blob = Path(snap.blob_path)

    if suffix == ".docx":
        return parse_docx(blob, snap)
    if suffix in (".xlsx", ".xlsm"):
        return parse_xlsx(blob, snap)
    if suffix == ".pdf":
        return parse_pdf(blob, snap)

    return snap.model_copy(update={
        "unsupported_objects": [f"unsupported file type {suffix!r}; no nodes extracted"]
    }), []


def verify(snap: SourceSnapshot) -> bool:
    """Re-verify a snapshot's hash. False means the blob store was disturbed."""
    return file_hash(Path(snap.blob_path)) == snap.version_hash
