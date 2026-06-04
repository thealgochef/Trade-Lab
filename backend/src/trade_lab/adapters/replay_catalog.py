"""Allowlisted replay source discovery for synthetic and local historical data.

The public API exposes opaque source ids only.  Request-provided filesystem paths
are intentionally not accepted: historical ids are resolved back to paths solely
through this in-process catalog so browsers cannot probe arbitrary local files.
"""

from __future__ import annotations

import logging
import re
import stat
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import pyarrow.parquet as pq

from trade_lab.adapters.historical_parquet import HistoricalParquetSource
from trade_lab.adapters.synthetic_replay import ReplaySourceDefinition, default_synthetic_sources

logger = logging.getLogger(__name__)

SUPPORTED_SCHEMAS = ("trades", "mbp-1", "mbp-10", "bbo")
_DATE_RE = re.compile(r"(?P<date>20\d{2})[-_]?([01]\d)[-_]?([0-3]\d)")
_UNSUPPORTED_DEEPER_BOOK_SCHEMA_RE = re.compile(r"\b(?:mbo|c?mbp[-_. ]?(?:[2-9]|[1-9]\d+))\b")
_SUPPORTED_MBP10_SCHEMA_RE = re.compile(r"\bc?mbp[-_. ]?10\b")
_SAFE_ID_PART_RE = re.compile(r"[^A-Za-z0-9_.:-]+")
_MAX_DIRECTORIES_VISITED = 512
_MAX_DIRECTORY_DEPTH = 8
_MAX_DIRECTORY_ENTRIES_INSPECTED = 4096
_MAX_METADATA_READS = 1024
# Replay decodes parquet on a worker thread; smaller batches keep each decode's GIL hold
# short so the event loop / WebSocket fan-out stays responsive (no replay "stop-and-go").
_REPLAY_SCAN_BATCH_SIZE = 4096


@dataclass(slots=True)
class _TraversalStats:
    truncated: bool = False
    parquet_candidates_seen: int = 0
    directories_visited: int = 0
    entries_inspected: int = 0
    truncation_reason: str = ""


@dataclass(frozen=True, slots=True)
class ReplayCatalog:
    sources: dict[str, tuple[ReplaySourceDefinition, object]]
    historical_available: bool
    historical_status: str
    historical_diagnostics: dict[str, int | bool | str]


def _diagnostics(
    *,
    data_path_configured: bool,
    root_available: bool = False,
    root_exists: bool = False,
    root_traversable: bool = False,
    parquet_candidates_seen: int = 0,
    parquet_files_inspected: int = 0,
    metadata_reads_attempted: int = 0,
    skipped_unsupported_names: int = 0,
    outside_root_or_unresolvable: int = 0,
    unreadable_metadata: int = 0,
    unsupported_schema_or_required_columns: int = 0,
    duplicates: int = 0,
    discovered: int = 0,
    traversal_truncated: bool = False,
    truncation_reason: str = "",
    directories_visited: int = 0,
    entries_inspected: int = 0,
) -> dict[str, int | bool | str]:
    return {
        "data_path_configured": data_path_configured,
        "root_available": root_available,
        "root_exists": root_exists,
        "root_traversable": root_traversable,
        "parquet_candidates_seen": parquet_candidates_seen,
        "parquet_files_inspected": parquet_files_inspected,
        "metadata_reads_attempted": metadata_reads_attempted,
        "skipped_unsupported_names": skipped_unsupported_names,
        "outside_root_or_unresolvable": outside_root_or_unresolvable,
        "unreadable_metadata": unreadable_metadata,
        "unsupported_schema_or_required_columns": unsupported_schema_or_required_columns,
        "duplicates": duplicates,
        "discovered": discovered,
        "traversal_truncated": traversal_truncated,
        "truncation_reason": truncation_reason,
        "directories_visited": directories_visited,
        "entries_inspected": entries_inspected,
        "directory_limit": _MAX_DIRECTORIES_VISITED,
        "depth_limit": _MAX_DIRECTORY_DEPTH,
        "entry_limit": _MAX_DIRECTORY_ENTRIES_INSPECTED,
        "metadata_read_limit": _MAX_METADATA_READS,
    }


def build_replay_catalog(
    *,
    data_path: Path | None,
    requested_symbol: str | None,
    instrument_root: str,
) -> ReplayCatalog:
    sources: dict[str, tuple[ReplaySourceDefinition, object]] = dict(default_synthetic_sources())
    if data_path is None:
        return ReplayCatalog(
            sources,
            False,
            "TRADE_LAB_DATA_PATH is not configured",
            _diagnostics(data_path_configured=False),
        )
    try:
        root = data_path.expanduser().absolute()
    except OSError:
        return ReplayCatalog(
            sources,
            False,
            "configured data path is unavailable",
            _diagnostics(data_path_configured=True),
        )
    if not root.exists() and not root.is_symlink():
        return ReplayCatalog(
            sources,
            False,
            "configured data path does not exist",
            _diagnostics(data_path_configured=True, root_available=True),
        )
    if _is_non_traversable_reparse_entry(root):
        logger.warning(
            "ignored configured historical replay data path because it is non-traversable"
        )
        return ReplayCatalog(
            sources,
            False,
            "configured data path is unavailable",
            _diagnostics(data_path_configured=True, root_available=True, root_exists=True),
        )
    if not root.exists() or not root.is_dir():
        return ReplayCatalog(
            sources,
            False,
            "configured data path does not exist",
            _diagnostics(data_path_configured=True, root_available=True),
        )
    try:
        canonical_root = root.resolve(strict=True)
    except OSError:
        return ReplayCatalog(
            sources,
            False,
            "configured data path is unavailable",
            _diagnostics(data_path_configured=True, root_available=True, root_exists=True),
        )

    discovered = 0
    parquet_files_inspected = 0
    metadata_reads_attempted = 0
    skipped_unsupported_names = 0
    outside_root_or_unresolvable = 0
    unreadable_metadata = 0
    unsupported_schema_or_required_columns = 0
    duplicates = 0
    historical_entries: dict[str, tuple[ReplaySourceDefinition, HistoricalParquetSource]] = {}
    traversal = _TraversalStats()
    for file_path in _iter_candidate_files(canonical_root, traversal):
        parquet_files_inspected += 1
        try:
            resolved = file_path.resolve(strict=True)
            relative = resolved.relative_to(canonical_root)
        except (OSError, ValueError):
            logger.warning("ignored historical replay file outside configured data root")
            outside_root_or_unresolvable += 1
            continue
        schema = _schema_from_path(relative)
        if schema is None:
            skipped_unsupported_names += 1
            continue
        if metadata_reads_attempted >= _MAX_METADATA_READS:
            _mark_truncated(traversal, "metadata_read_limit")
            logger.warning("historical replay discovery stopped at metadata read safety limit")
            break
        metadata_reads_attempted += 1
        entry = _definition_for_file(
            resolved,
            relative_path=relative,
            schema=schema,
            requested_symbol=requested_symbol or f"{instrument_root}.c.0",
            instrument_root=instrument_root,
        )
        if entry is _SKIPPED_UNREADABLE_METADATA:
            unreadable_metadata += 1
            continue
        if entry is _SKIPPED_UNSUPPORTED_SCHEMA:
            unsupported_schema_or_required_columns += 1
            continue
        if entry is None:
            continue
        definition, source = entry
        existing = historical_entries.get(definition.source_id)
        if existing is not None:
            logger.info("combined partitioned historical replay source parts")
            duplicates += 1
            existing_definition, existing_source = existing
            combined_paths = tuple(sorted((*existing_definition.paths, *definition.paths)))
            historical_entries[definition.source_id] = (
                ReplaySourceDefinition(
                    source_id=existing_definition.source_id,
                    label=existing_definition.label,
                    requested_symbol=existing_definition.requested_symbol,
                    schema=existing_definition.schema,
                    kind=existing_definition.kind,
                    session_label=existing_definition.session_label,
                    availability=existing_definition.availability,
                    paths=combined_paths,
                ),
                existing_source,
            )
            continue
        if definition.source_id in sources:
            logger.warning("ignored historical replay source id colliding with built-in source")
            duplicates += 1
            continue
        historical_entries[definition.source_id] = (definition, source)
        discovered += 1

    sources.update(historical_entries)

    if traversal.truncated:
        status = (
            "historical source discovery truncated by safety limits; "
            f"{discovered} supported historical source(s) discovered"
        )
    else:
        status = (
            "historical sources discovered"
            if discovered
            else "no supported historical parquet sources found"
        )
    return ReplayCatalog(
        sources,
        discovered > 0,
        status,
        _diagnostics(
            data_path_configured=True,
            root_available=True,
            root_exists=True,
            root_traversable=True,
            parquet_candidates_seen=traversal.parquet_candidates_seen,
            parquet_files_inspected=parquet_files_inspected,
            metadata_reads_attempted=metadata_reads_attempted,
            skipped_unsupported_names=skipped_unsupported_names,
            outside_root_or_unresolvable=outside_root_or_unresolvable,
            unreadable_metadata=unreadable_metadata,
            unsupported_schema_or_required_columns=unsupported_schema_or_required_columns,
            duplicates=duplicates,
            discovered=discovered,
            traversal_truncated=traversal.truncated,
            truncation_reason=traversal.truncation_reason,
            directories_visited=traversal.directories_visited,
            entries_inspected=traversal.entries_inspected,
        ),
    )


def _mark_truncated(stats: _TraversalStats, reason: str) -> None:
    stats.truncated = True
    if not stats.truncation_reason:
        stats.truncation_reason = reason


def _iter_candidate_files(root: Path, stats: _TraversalStats):
    """Yield bounded, deterministic parquet candidates without unbounded rglob.

    Discovery is intentionally capped by directory count, depth, inspected entry
    count, and metadata reads (enforced by the caller).  Candidates are streamed
    so early unsupported parquet noise cannot starve supported files later in the
    same bounded traversal.  If a cap is reached, callers still receive sources
    discovered so far plus safe count-only diagnostics; local paths are never
    logged or returned.
    """

    pending: deque[tuple[Path, int]] = deque([(root, 0)])

    while pending:
        if stats.entries_inspected >= _MAX_DIRECTORY_ENTRIES_INSPECTED:
            _mark_truncated(stats, "entry_limit")
            logger.warning("historical replay discovery stopped at entry safety limit")
            break

        directory, depth = pending.popleft()
        stats.directories_visited += 1
        if stats.directories_visited > _MAX_DIRECTORIES_VISITED:
            _mark_truncated(stats, "directory_limit")
            logger.warning("historical replay discovery stopped at directory safety limit")
            break

        entries: list[Path] = []
        try:
            for entry in directory.iterdir():
                entries.append(entry)
                stats.entries_inspected += 1
                if stats.entries_inspected >= _MAX_DIRECTORY_ENTRIES_INSPECTED:
                    _mark_truncated(stats, "entry_limit")
                    logger.warning("historical replay discovery stopped at entry safety limit")
                    break
        except OSError:
            logger.warning("ignored unreadable historical replay directory")
            continue

        entries.sort(key=lambda p: p.name.lower())

        for entry in entries:
            try:
                if _is_non_traversable_reparse_entry(entry):
                    continue
                is_dir = entry.is_dir()
                is_file = entry.is_file()
            except OSError:
                logger.warning("ignored unreadable historical replay directory entry")
                continue
            if is_dir:
                if depth < _MAX_DIRECTORY_DEPTH:
                    pending.append((entry, depth + 1))
                else:
                    _mark_truncated(stats, "depth_limit")
                continue
            if is_file and entry.suffix.lower() == ".parquet":
                stats.parquet_candidates_seen += 1
                yield entry

        if stats.truncated:
            break


def _is_non_traversable_reparse_entry(entry: Path) -> bool:
    """Return True for symlinks and Windows reparse/junction entries.

    On non-Windows platforms ``st_file_attributes`` is not present, making the
    reparse-point check a safe no-op while preserving symlink blocking.
    """

    try:
        if entry.is_symlink():
            return True
        attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        logger.warning("ignored unreadable historical replay directory entry")
        return True
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _definition_for_file(
    path: Path,
    *,
    relative_path: Path,
    schema: str,
    requested_symbol: str,
    instrument_root: str,
) -> tuple[ReplaySourceDefinition, HistoricalParquetSource] | object | None:
    try:
        names = set(pq.ParquetFile(path).schema_arrow.names)
    except Exception:
        logger.warning("ignored unreadable historical parquet metadata")
        return _SKIPPED_UNREADABLE_METADATA
    if not _has_required_live_columns(schema, names):
        logger.warning("ignored historical parquet with unsupported live-compatible schema")
        return _SKIPPED_UNSUPPORTED_SCHEMA

    date_label = _date_label(relative_path)
    symbol = _safe_part(_symbol_from_path(relative_path, instrument_root))
    source_id = f"historical:{symbol.lower()}:{date_label}:{schema}"
    label = f"Historical {symbol} {date_label} {schema}"
    definition = ReplaySourceDefinition(
        source_id=source_id,
        label=label,
        requested_symbol=requested_symbol,
        schema=schema,
        kind="historical",
        session_label=date_label,
        availability="metadata_only",
        paths=(path,),
    )
    # Use the opaque source id as the adapter source label so warnings/status never
    # include full local paths. Historical-only depth fields are ignored by adapter.
    # Real local dumps mix the front-month outright with back-month/calendar-spread
    # trades, so restrict replay tick bars to the dominant front-month outright.
    return definition, HistoricalParquetSource(
        dataset_label=source_id,
        front_month_only=True,
        batch_size=_REPLAY_SCAN_BATCH_SIZE,
    )


_SKIPPED_UNSUPPORTED_NAME = object()
_SKIPPED_UNREADABLE_METADATA = object()
_SKIPPED_UNSUPPORTED_SCHEMA = object()


def _schema_from_path(relative_path: Path) -> str | None:
    safe_parts = [part for part in relative_path.parts if part not in {"", ".", ".."}]
    lower = " ".join(safe_parts).lower()
    return _schema_from_name(lower)


def _schema_from_name(name: str) -> str | None:
    lower = name.lower()
    if "depth-only" in lower or "depth_only" in lower:
        return None
    if _SUPPORTED_MBP10_SCHEMA_RE.search(lower):
        return "mbp-10"
    if _is_deeper_book_name(lower):
        # Do not advertise unsupported deeper books as runtime features.  MBP-10 is
        # handled above because it has an explicit live-compatible projection.
        return None
    if "trades" in lower or "trade" in lower:
        return "trades"
    if "mbp-1" in lower or "mbp1" in lower or "cmbp-1" in lower:
        return "mbp-1"
    if "bbo" in lower or "cbbo" in lower:
        return "bbo"
    return None


def _is_deeper_book_name(lower_name: str) -> bool:
    return bool(_UNSUPPORTED_DEEPER_BOOK_SCHEMA_RE.search(lower_name))


def _has_required_live_columns(schema: str, names: set[str]) -> bool:
    if schema == "trades":
        return {"ts_event", "price", "size"} <= names
    if schema == "mbp-10":
        has_trade_projection = {"ts_event", "action", "price", "size"} <= names
        has_bid = any(n in names for n in ("bid_price", "bid_px", "bid", "bid_px_00"))
        has_ask = any(n in names for n in ("ask_price", "ask_px", "ask", "ask_px_00"))
        return has_trade_projection or ("ts_event" in names and has_bid and has_ask)
    if schema in {"mbp-1", "bbo"}:
        has_bid = any(n in names for n in ("bid_price", "bid_px", "bid"))
        has_ask = any(n in names for n in ("ask_price", "ask_px", "ask"))
        return "ts_event" in names and has_bid and has_ask
    return False


def _date_label(path: Path) -> str:
    match = _DATE_RE.search(str(path))
    if match is None:
        return "undated"
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def _symbol_from_path(path: Path, instrument_root: str) -> str:
    upper = str(path).upper()
    root = instrument_root.upper()
    return root if root and root in upper else instrument_root


def _safe_part(value: str) -> str:
    sanitized = _SAFE_ID_PART_RE.sub("-", value.strip())
    return sanitized.strip("-._:") or "source"
