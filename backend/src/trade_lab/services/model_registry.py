"""Discovery of local model bundles (Stage 0: no CatBoost loading).

A model bundle is a subdirectory of ``TRADE_LAB_MODELS_PATH`` containing
``model.cbm`` + ``metadata.json`` + ``strategy.json`` (and an optional
``model.cbm.sha256``). This module scans that directory and returns opaque
:class:`ModelBundle` descriptors built from the strategy contract and bundle
metadata. It deliberately never opens the ``.cbm`` binary and never exposes
absolute filesystem paths, so a descriptor is safe to serialize toward the UI.

Path handling reuses the hardening style of ``replay_catalog`` /
``api/app._reject_path_like_source_id``: model ids are opaque allowlisted names
derived from directory names, never caller-supplied paths.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from trade_lab.domain.contracts import ContractError, StrategyContract, load_strategy_contract

if TYPE_CHECKING:
    from catboost import CatBoostClassifier

logger = logging.getLogger(__name__)

MODEL_FILE = "model.cbm"
METADATA_FILE = "metadata.json"
STRATEGY_FILE = "strategy.json"
CHECKSUM_FILE = "model.cbm.sha256"

_SAFE_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_MAX_BUNDLES_SCANNED = 256


@dataclass(frozen=True, slots=True)
class ModelBundle:
    """Opaque, path-free descriptor for one discovered model bundle."""

    model_id: str
    strategy_id: str
    training_mode: str
    instrument: str
    feature_count: int
    class_map: dict[int, str]
    has_checksum: bool
    validation_ok: bool
    validation_detail: str


def is_safe_model_id(model_id: str) -> bool:
    """Return True for opaque allowlisted model ids (never paths)."""

    return bool(
        model_id
        and _SAFE_MODEL_ID_RE.fullmatch(model_id)
        and not _WINDOWS_DRIVE_RE.match(model_id)
        and "/" not in model_id
        and "\\" not in model_id
        and ".." not in model_id
    )


def discover_model_bundles(models_path: Path | None) -> list[ModelBundle]:
    """Scan ``models_path`` for bundles, newest directory name first.

    Returns an empty list when no path is configured or the root is missing.
    Individual unreadable/invalid bundles are skipped (with a path-free warning)
    rather than failing the whole scan, so one bad bundle can't hide the rest.
    """

    if models_path is None:
        return []
    try:
        root = models_path.expanduser().resolve(strict=True)
    except OSError:
        logger.warning("configured models path is unavailable")
        return []
    if not root.is_dir():
        logger.warning("configured models path is not a directory")
        return []

    bundles: list[ModelBundle] = []
    scanned = 0
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        logger.warning("configured models path is not traversable")
        return []

    for entry in entries:
        if scanned >= _MAX_BUNDLES_SCANNED:
            logger.warning("model discovery stopped at bundle safety limit")
            break
        try:
            if entry.is_symlink() or not entry.is_dir():
                continue
        except OSError:
            logger.warning("ignored unreadable model bundle entry")
            continue
        scanned += 1
        model_id = entry.name
        if not is_safe_model_id(model_id):
            logger.warning("ignored model bundle with unsafe directory name")
            continue
        bundle = _describe_bundle(entry, model_id)
        if bundle is not None:
            bundles.append(bundle)

    return bundles


def _describe_bundle(directory: Path, model_id: str) -> ModelBundle | None:
    model_file = directory / MODEL_FILE
    metadata_file = directory / METADATA_FILE
    strategy_file = directory / STRATEGY_FILE
    checksum_file = directory / CHECKSUM_FILE

    try:
        if not model_file.is_file() or not metadata_file.is_file() or not strategy_file.is_file():
            return None
        has_checksum = checksum_file.is_file()
    except OSError:
        logger.warning("ignored unreadable model bundle")
        return None

    try:
        contract = load_strategy_contract(strategy_file)
    except ContractError as exc:
        logger.warning("ignored model bundle with invalid strategy contract: %s", exc)
        return None

    validation_ok, validation_detail = _validate_against_metadata(metadata_file, contract)

    return ModelBundle(
        model_id=model_id,
        strategy_id=contract.strategy_id,
        training_mode=contract.training_mode,
        instrument=contract.instrument,
        feature_count=contract.feature_count,
        class_map=dict(contract.class_map.mapping),
        has_checksum=has_checksum,
        validation_ok=validation_ok,
        validation_detail=validation_detail,
    )


def _validate_against_metadata(metadata_file: Path, contract) -> tuple[bool, str]:
    """Cross-check the contract's feature set against ``metadata.json``.

    The ``.cbm`` is never opened in this stage; ``metadata.json:selected_features``
    is the bundle's declared feature expectation. A mismatch means the shipped
    contract and the trained model disagree, so the bundle is flagged not-ok (but
    still listed, so the UI can surface the problem).
    """

    try:
        payload = json.loads(metadata_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "metadata is unreadable or not valid JSON"
    if not isinstance(payload, dict):
        return False, "metadata is not a JSON object"

    selected = payload.get("selected_features")
    if not isinstance(selected, list) or not all(isinstance(name, str) for name in selected):
        return False, "metadata is missing a valid selected_features list"

    if tuple(selected) != tuple(contract.feature_set.names):
        return False, "contract feature_set does not match metadata selected_features"

    return True, "contract matches metadata feature set"


class ModelRegistryError(RuntimeError):
    """Base error for model loading / activation failures (path-free messages)."""


class ModelValidationError(ModelRegistryError):
    """Raised when a loaded model disagrees with its shipped strategy contract.

    The runtime fails CLOSED on any mismatch: a model whose feature names, class
    count, tick size, or checksum do not match the contract is never activated, so
    a stale or wrong binary cannot silently serve predictions.
    """


class ModelNotFoundError(ModelRegistryError):
    """Raised when an activation targets an unknown or unsafe model id."""


@dataclass(frozen=True, slots=True)
class ActiveModel:
    """The currently loaded model + the contract it was validated against."""

    model_id: str
    model: CatBoostClassifier
    contract: StrategyContract


class ModelRegistry:
    """Discover, validate, and atomically hot-swap the active CatBoost model.

    Discovery reuses the module-level :func:`discover_model_bundles` scan and never
    opens the ``.cbm`` binary. Activation loads exactly one bundle's model, validates
    it fail-closed against the bundle's ``strategy.json``, and swaps it in under a
    lock so concurrent readers always see either the old or new model, never a torn
    state. No absolute path or secret is ever stored on or returned from an active
    model descriptor.
    """

    def __init__(self, models_path: Path | None) -> None:
        self._models_path = models_path
        self._lock = threading.RLock()
        self._active: ActiveModel | None = None

    def discover(self) -> list[ModelBundle]:
        """List discoverable bundles (path-free, never loads the binary)."""

        return discover_model_bundles(self._models_path)

    def active(self) -> ActiveModel | None:
        """Return the loaded ``(model, contract, model_id)`` or ``None``."""

        with self._lock:
            return self._active

    def active_model_id(self) -> str | None:
        with self._lock:
            return None if self._active is None else self._active.model_id

    def activate(self, model_id: str) -> ActiveModel:
        """Validate + load + atomically swap in ``model_id`` as the active model.

        Raises :class:`ModelNotFoundError` for an unknown/unsafe id and
        :class:`ModelValidationError` (fail closed) if the loaded model disagrees
        with its contract. The prior active model is only replaced after the new
        one fully validates, so a failed activation leaves the registry unchanged.
        """

        if not is_safe_model_id(model_id):
            raise ModelNotFoundError("unknown model id")
        directory = self._resolve_bundle_dir(model_id)
        contract = self._load_contract(directory)
        model = self._load_and_validate_model(directory, contract)
        active = ActiveModel(model_id=model_id, model=model, contract=contract)
        with self._lock:
            self._active = active
        return active

    def deactivate(self) -> None:
        """Unload the active model. Market data continues to be served."""

        with self._lock:
            self._active = None

    def _resolve_bundle_dir(self, model_id: str) -> Path:
        if self._models_path is None:
            raise ModelNotFoundError("no models path is configured")
        try:
            root = self._models_path.expanduser().resolve(strict=True)
        except OSError as exc:
            raise ModelNotFoundError("configured models path is unavailable") from exc
        directory = root / model_id
        try:
            # Guard against id/path tricks: the bundle must be a real subdirectory of
            # root (never a symlink escaping it) carrying the required files.
            resolved = directory.resolve(strict=True)
            if resolved.parent != root or not resolved.is_dir() or directory.is_symlink():
                raise ModelNotFoundError("unknown model id")
            if not (resolved / MODEL_FILE).is_file() or not (resolved / STRATEGY_FILE).is_file():
                raise ModelNotFoundError("model bundle is incomplete")
        except OSError as exc:
            raise ModelNotFoundError("unknown model id") from exc
        return resolved

    @staticmethod
    def _load_contract(directory: Path) -> StrategyContract:
        try:
            return load_strategy_contract(directory / STRATEGY_FILE)
        except ContractError as exc:
            raise ModelValidationError(f"invalid strategy contract: {exc}") from exc

    def _load_and_validate_model(
        self, directory: Path, contract: StrategyContract
    ) -> CatBoostClassifier:
        model_file = directory / MODEL_FILE
        self._verify_checksum(directory, model_file)
        model = self._load_catboost(model_file)
        self._validate_model_against_contract(model, contract)
        return model

    @staticmethod
    def _verify_checksum(directory: Path, model_file: Path) -> None:
        checksum_file = directory / CHECKSUM_FILE
        if not checksum_file.is_file():
            return
        try:
            declared = checksum_file.read_text(encoding="utf-8").split()[0].strip().lower()
        except (OSError, IndexError) as exc:
            raise ModelValidationError("model checksum file is unreadable") from exc
        digest = hashlib.sha256()
        try:
            with model_file.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise ModelValidationError("model file is unreadable") from exc
        if digest.hexdigest().lower() != declared:
            raise ModelValidationError("model file checksum does not match the sidecar .sha256")

    @staticmethod
    def _load_catboost(model_file: Path) -> CatBoostClassifier:
        # Imported lazily so discovery-only callers never pay the CatBoost import cost
        # and the dependency is only required when a model is actually activated.
        from catboost import CatBoostClassifier

        model = CatBoostClassifier()
        try:
            model.load_model(str(model_file))
        except Exception as exc:
            # Any load failure (corrupt/binary/non-model file) must fail closed.
            raise ModelValidationError("model file is not a loadable CatBoost model") from exc
        return model

    @staticmethod
    def _validate_model_against_contract(
        model: CatBoostClassifier, contract: StrategyContract
    ) -> None:
        feature_names = tuple(model.feature_names_ or ())
        if feature_names != tuple(contract.feature_set.names):
            raise ModelValidationError(
                "model feature_names_ do not match the contract feature_set (name or order)"
            )
        model_class_count = _model_class_count(model)
        if model_class_count != len(contract.class_map):
            raise ModelValidationError(
                "model class count does not match the contract class_map size"
            )
        model_tick = _model_tick_size(model)
        if model_tick is not None and not _floats_equal(model_tick, contract.tick_size):
            raise ModelValidationError("model tick_size metadata does not match the contract")


def _model_class_count(model: CatBoostClassifier) -> int:
    classes = getattr(model, "classes_", None)
    if classes is not None and len(classes) > 0:
        return len(classes)
    # Fall back to the trained class count when classes_ is unavailable.
    return int(model.get_param("classes_count") or 0) or len(classes or ())


def _model_tick_size(model: CatBoostClassifier) -> float | None:
    """Best-effort tick_size from any tick metadata embedded in the model."""

    metadata: dict[str, Any]
    try:
        metadata = dict(model.get_metadata())
    except Exception:
        # Tick metadata is optional; absence is not a validation failure.
        return None
    for key in ("tick_size", "instrument_tick_size"):
        raw = metadata.get(key)
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    return None


def _floats_equal(left: float, right: float) -> bool:
    return abs(left - right) <= 1e-9
