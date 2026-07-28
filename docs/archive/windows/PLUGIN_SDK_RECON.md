# PLUGIN_SDK_RECON — plugin SDK surface inventory for the second-strategy window

**Scope.** Read-only recon across the three platform-refactor repos: SC (strategy-core) @ `108d1a7`, QL (Claude-Quant-Lab) @ `0b4a2e8`, TL (Trade-Lab) @ `923b29a` — all on branch `platform-refactor`. Date: 2026-07-06. Facts with `file:line` throughout; no design opinions.

**Citation convention.** `SC/…`, `QL/…`, `TL/…` prefix repo-relative paths (SC = `C:\Users\gonza\Documents\Strategy-core`, QL = `C:\Users\gonza\Documents\Claude-Quant-Lab`, TL = `C:\Users\gonza\Documents\Trade-Lab`).

**"9.x" doc labels.** All resolve to `SC/docs/PLATFORM_REFACTOR_PLAN.md` §9 "Decisions needed from the owner before implementation" (SC/docs/PLATFORM_REFACTOR_PLAN.md:1353): 9.1 plugin interface shape (:1359), 9.2 registry mechanism (:1384), 9.3 section versioning (:1395), 9.5 package layout / id slug (:1436), 9.8 outcome-tracker/Barrier decision (:1472), 9.10 quote handling (:1496).

**Method.** Seven parallel recon agents (one per section) followed by seven independent citation-verification agents that re-opened every cited file:line; corrections were applied before assembly. Sections below are the verified versions.

---

## 1. The plugin Protocol

#### Doc mapping

The design doc the "9.x" labels refer to was located: `SC/docs/PLATFORM_REFACTOR_PLAN.md`, section "## 9. Decisions needed from the owner before implementation" (SC/docs/PLATFORM_REFACTOR_PLAN.md:1353). Item 9.1 = "Strategy-plugin interface shape: `Protocol` vs `ABC` vs callable-bundle" (SC/docs/PLATFORM_REFACTOR_PLAN.md:1359); item 9.2 = "Registry mechanism: in-package registry module vs setuptools entry-points" (SC/docs/PLATFORM_REFACTOR_PLAN.md:1384); item 9.5 fixes the `strategy_core/strategies/<id>/` package layout and the registry-keyed `strategy_id` slug (SC/docs/PLATFORM_REFACTOR_PLAN.md:1436, 1443-1444).

#### (a) The `StrategyPlugin` Protocol — every required member

`StrategyPlugin` is a `@runtime_checkable` structural `Protocol` (SC/src/strategy_core/strategies/protocols.py:240-241). Its docstring states the split of responsibilities: "``runtime_checkable`` checks method/attr *presence* only — a wrong signature fails at call time, not registration. The deeper validation (``required_bars()`` returns ``BarSpec``s; ``SectionModel`` is a pydantic ``BaseModel``) is the §9.1 registry-time assertion in ``strategy_core.strategies.registry.register`` so a malformed plugin fails at ``@register``, not in the hot path." (SC/src/strategy_core/strategies/protocols.py:242-249).

**Identity / binding attributes** (SC/src/strategy_core/strategies/protocols.py:251-254):

| Attribute | Declared type | Stated intent |
|---|---|---|
| `strategy_id` | `str` | registry key (grouped under "identity / binding", protocols.py:251-252) |
| `strategy_version` | `str` | per-strategy version axis (protocols.py:253); production plugin comment: "the QL emitter stamps this value into every contract's ``strategy_version`` … and Trade-Lab activation equality-checks it against the bundle" (SC/src/strategy_core/strategies/touch_reversal/plugin.py:200-205) |
| `SectionModel` | `type` | "the pydantic section model this strategy OWNS (PLAN §2.4)" (protocols.py:254) |

**Methods** (signatures quoted exactly; docstring intents abridged from the quoted lines):

| Member | Exact signature | Docstring intent | Lines |
|---|---|---|---|
| `configure` | `def configure(self, section: Any, ctx: PlatformContext) -> None:` | "Wire the validated ``SectionModel`` + platform context into plugin state." | protocols.py:257-258 |
| `reset` | `def reset(self) -> None:` | "Clear the plugin's own state (the platform resets candles/feed)." | protocols.py:261-262 |
| `set_static_levels` | `def set_static_levels(self, levels: tuple[Level, ...]) -> None:` | "Seed static reference levels — written through by the runtime's lifecycle method; the SOLE level-seed path since S-B3a (W4/D-B2j)." | protocols.py:265-267 |
| `load_prior_day_summary` | `def load_prior_day_summary(self, trading_day: date, *, high_ticks: int, low_ticks: int) -> None:` | "Seed the prior-day PDH/PDL summary — written through by the runtime's lifecycle method; the SOLE level-seed path since S-B3a (W4/D-B2j)." | protocols.py:270-272 |
| `required_bars` | `@staticmethod` `def required_bars() -> tuple[BarSpec, ...]:` | "The tick/time bars this strategy needs (PLAN §2.2)." | protocols.py:276-278 |
| `decision_bar_label` | `@staticmethod` `def decision_bar_label() -> str:` | "The BarSpec label that drives single-bar decisions." | protocols.py:281-283 |
| `on_event` | `def on_event(self, event: Trade | Quote, ctx: PlatformContext) -> tuple[Level, ...]:` | "Fold a trade/quote into the plugin's own state; return the level delta." — for a `Trade` the full post-fold level set (plugin owns the SOLE level fold, R1); for a `Quote` it returns `()` | protocols.py:287-294 |
| `on_bar_closed` | `def on_bar_closed(self, bar: Bar, ctx: PlatformContext) -> StrategyStep:` | "Fold a closed bar; return the sparse strategy delta (setups/decisions/features/touches/zones)." — cross-bar first-touch dedup is PLUGIN-owned since S-B3a | protocols.py:297-304 |
| `current_levels` | `def current_levels(self) -> tuple[Level, ...]:` | "The plugin's full current level set (feeds the platform snapshot's ``levels``)." | protocols.py:308-309 |
| `snapshot_zones` | `def snapshot_zones(self, trading_day: date | None) -> tuple[Zone, ...]:` | "Display zones with already-fired zones pre-marked ``touched``…"; `trading_day=None` returns zones unmarked; docstring records the S-B3a deviation that the protocol "temporarily carries touch vocabulary" because `RuntimeUpdate`/`RuntimeSnapshot` still expose typed `levels`/`zones` fields | protocols.py:312-323 |
| `feature_spec` | `@staticmethod` `def feature_spec() -> FeatureSpec:` | (no docstring — declaration only) | protocols.py:326-328 |
| `label_policy` | `@staticmethod` `def label_policy() -> LabelPolicySpec:` | (no docstring — declaration only) | protocols.py:330-332 |
| `emitted_event_types` | `@staticmethod` `def emitted_event_types() -> tuple[EventTypeSpec, ...]:` | (no docstring — declaration only) | protocols.py:334-336 |

Total surface: 3 attributes + 13 methods (5 of them `@staticmethod`).

**Nested Protocols / dataclasses referenced by the surface** (all in the same file):

- `PlatformContext` (`Protocol`, protocols.py:85-119) — the read-only platform context; per resolution R2 it is "the COMPLETE surface — there is deliberately NO levels accessor and NO scheme attribute" (protocols.py:88-90). Members: attrs `tick_size: float`, `point_value: float` (protocols.py:96-97); `def closed_bars(self, label: str) -> Sequence[Bar]` ("Recently-closed bars for the BarSpec identified by ``label``.", :99-100); `def current_bar(self, label: str) -> Bar | None` (:103-104); `def trade_price_at(self, ts_utc: datetime) -> float | None` ("Realistic front-month trade-print price at a decision instant (honest fill).", :107-108); `def session_at(self, ts_utc: datetime) -> str | None` (:111-112); `def quotes_in_window(self, start_ts_utc: datetime, end_ts_utc: datetime) -> Sequence[Quote]` — the §9.10 accessor, "[quotes] in ``[start, end)``" (:115-118).
- `BarKind` (`StrEnum`, protocols.py:61-65): `TICK = "tick"` (current engine), `TIME = "time"` ("NEW close trigger, Phase F" — not yet buildable, decision 9.4, protocols.py:23-25).
- `BarSpec` (`@dataclass(frozen=True, slots=True)`, protocols.py:68-81): fields `kind: BarKind`, `size: int`, `label: str`; "``size`` is a tick count for ``TICK`` or interval *seconds* for ``TIME``" (:72).
- `SetupState` (`Protocol`, protocols.py:123-136): fields `setup_id: str`, `phase: str`, `direction: str`, `status: str`, `evidence: Mapping[str, Any]` — the multi-stage arming→locking→invalidation lifecycle (:124).
- `Barrier` (`Protocol`, protocols.py:138-152): field `kind: str` ("``fixed_points`` or ``r_relative``", :139); methods `def stop_price(self, entry_price: float, direction: str) -> float` (:148) and `def target_price(self, entry_price: float, direction: str) -> float` (:151); "Both feed the SAME shared MAE-first kernel" (:142-143).
- `DecisionEvent` (`Protocol`, protocols.py:155-167): fields `setup_id: str`, `decision_ts_utc: datetime`, `direction: str`, `entry_reference: str`, `barrier: Barrier`.
- `StrategyStep` (`@dataclass(frozen=True, slots=True)`, protocols.py:170-189): `setups: tuple[SetupState, ...] = ()`, `decisions: tuple[DecisionEvent, ...] = ()`, `features: tuple[Mapping[str, float], ...] = ()`, `touches: tuple[Touch, ...] = ()`, `zones: tuple[Zone, ...] = ()` — "Default-constructible (``StrategyStep()`` == empty delta)" (:174); touches are "folded VERBATIM onto ``RuntimeUpdate.touches``" (:183-184).
- `FeatureSpec` (dataclass, protocols.py:193-206): `names`, `interaction_features`, `approach_features` (all `tuple[str, ...]`), `nan_policy: str` — same partition the contract's `FeatureSet` validator enforces (:197-199).
- `LabelPolicySpec` (dataclass, protocols.py:208-223): `resolution: str`, `barrier_mode: str`, `barrier: Barrier`, `decision_offset_minutes: int`, `flatten_time: time`, `forward_cutoff: str`, `no_resolution_dropped: bool`.
- `EventTypeSpec` (dataclass, protocols.py:226-236): `message_type: str`, `payload_fields: tuple[str, ...] = ()` — fanned out "on the GENERIC ``ws.v1`` envelope, so adding a strategy delta does not edit the platform's ``MessageType`` literal" (:230-231).

**Engine value types the signatures reference** (imported at protocols.py:43 from `strategy_core.types`; all stdlib-only dataclasses):

| Type | Fields | Citation |
|---|---|---|
| `Trade` | `event_ts_utc: datetime`, `price_ticks: int`, `size: int`, `side: str | None = None` | SC/src/strategy_core/types.py:58-67 |
| `Quote` | `event_ts_utc`, `bid_price_ticks: int`, `ask_price_ticks: int`, `bid_size: int = 0`, `ask_size: int = 0` (top-of-book only) | SC/src/strategy_core/types.py:73-81 |
| `Bar` | `timeframe_ticks`, `trading_day`, `bar_index`, `bar_id`, `open_ts_utc`, `close_ts_utc`, `open_ticks`, `high_ticks`, `low_ticks`, `close_ticks`, `volume`, `trade_count`, `is_complete`, `is_partial`, `close_reason: CloseReason | None` | SC/src/strategy_core/types.py:90-112 |
| `Level` | `name: str`, `price: float`, `side: Side`, `available_from: datetime | None = None` (the v3 look-ahead guard) | SC/src/strategy_core/types.py:124-139 |
| `Zone` | `representative_price: float`, `names: tuple[str, ...]`, `side: Side`, `touched: bool = False` (the only mutable field on any engine type), `available_from: datetime | None = None` | SC/src/strategy_core/types.py:142-161 |
| `Touch` | `bar_ts_utc`, `representative_price`, `direction: Direction`, `level_type: str`, `trading_day: date` | SC/src/strategy_core/types.py:164-172 |

#### (b) The registry-time assertion (design-doc item 9.1)

Per the registry module docstring, "because the plugin interface is a structural ``runtime_checkable`` Protocol (presence-only), ``register`` does the deeper checks AT REGISTRATION so a malformed plugin fails at ``@register``, not in the hot path" (SC/src/strategy_core/strategies/registry.py:17-22). `register` runs **five** checks, all raising `ContractError` (imported from `strategy_core.contract.schema`, registry.py:29 — "the engine's single fail-closed contract exception", registry.py:44):

1. **`runtime_checkable` isinstance presence check** — against the class object (SC/src/strategy_core/strategies/registry.py:48-55):
```python
    # §9.1 (presence): the class must structurally satisfy StrategyPlugin. isinstance
    # against a runtime_checkable Protocol checks that every required member name is
    # present on the class (methods + identity attrs), catching a plugin missing a hook.
    if not isinstance(plugin_cls, StrategyPlugin):
        raise ContractError(
            f"{plugin_cls!r} does not satisfy the StrategyPlugin protocol "
            f"(missing one or more required members)"
        )
```
2. **`strategy_id` shape check** (registry.py:57-61):
```python
    strategy_id = plugin_cls.strategy_id
    if not isinstance(strategy_id, str) or not strategy_id:
        raise ContractError(
            f"{plugin_cls.__name__} must declare a non-empty str strategy_id"
        )
```
3. **`required_bars()` executed at register time** — any exception is re-raised as `ContractError`, and the return value must be a non-empty tuple of `BarSpec` (registry.py:63-73):
```python
    try:
        bars = plugin_cls.required_bars()
    except Exception as exc:  # noqa: BLE001 -- surface any declaration error as ContractError
        raise ContractError(
            f"{plugin_cls.__name__}.required_bars() raised at register time: {exc!r}"
        ) from exc
    if not isinstance(bars, tuple) or not bars or not all(isinstance(b, BarSpec) for b in bars):
        raise ContractError(
            f"{plugin_cls.__name__}.required_bars() must return a non-empty tuple of BarSpec"
        )
```
4. **`SectionModel` must be a pydantic `BaseModel` subclass** (registry.py:75-80):
```python
    section_model = getattr(plugin_cls, "SectionModel", None)
    if not (isinstance(section_model, type) and issubclass(section_model, BaseModel)):
        raise ContractError(
            f"{plugin_cls.__name__}.SectionModel must be a pydantic BaseModel subclass"
        )
```
5. **Duplicate-id collision** (re-registering the same class is idempotent; a different class under the same id fails) (registry.py:82-88):
```python
    existing = _REGISTRY.get(strategy_id)
    if existing is not None and existing is not plugin_cls:
        raise ContractError(
            f"strategy_id {strategy_id!r} is already registered to {existing.__name__}"
        )
    _REGISTRY[strategy_id] = plugin_cls
```

**What is NOT checked at register time**: there is no version check — `strategy_version` is only presence-checked by the isinstance (it is a protocol member, protocols.py:253); its *value* is equality-checked later, at TL activation/discovery: `if plugin_cls.strategy_version != contract.strategy_version:` returns a binding error (TL/backend/src/trade_lab/services/model_registry.py:290-295, in `_strategy_binding_error`, :276). Signatures are also not checked ("a wrong signature fails at call time", protocols.py:244-245). The design-doc provenance of this exact assertion set is the 9.1 recommendation: "Option A (Protocol), with a one-time registry-time assertion (e.g. verify `required_bars()` returns `BarSpec`s and `SectionModel` is a `BaseModel` subclass)" (SC/docs/PLATFORM_REFACTOR_PLAN.md:1380).

#### (c) The registration path (design-doc item 9.2)

The mechanism is an **in-package registry module** (design decision 9.2 Option A, SC/docs/PLATFORM_REFACTOR_PLAN.md:1388), "Chosen over setuptools entry-points so the SHA-pinned Trade-Lab and the (to-be-pinned) Quant-Lab resolve the SAME registry by construction" (SC/src/strategy_core/strategies/registry.py:5-8). The table is a plain module-level dict: `_REGISTRY: dict[str, type[StrategyPlugin]] = {}` "Keyed by ``plugin_cls.strategy_id``" (registry.py:34-35). Step by step, using `touch_reversal` as the worked production example:

1. **Package layout** — one sub-package per strategy at `strategy_core/strategies/<id>/` (decision 9.5: "RECOMMEND `strategy_core/strategies/<strategy_id>/` co-located inside SC … each exporting its plugin class + typed `SectionModel`", SC/docs/PLATFORM_REFACTOR_PLAN.md:1443; restated in SC/src/strategy_core/strategies/__init__.py:5-6). The production package has `__init__.py`, `plugin.py`, `section.py` (SC/src/strategy_core/strategies/touch_reversal/).
2. **The plugin module self-registers via decorator side-effect** — the class is decorated `@register` (SC/src/strategy_core/strategies/touch_reversal/plugin.py:195-196), which runs the (b) assertions and inserts into `_REGISTRY` at import time, returning "the class unchanged so it can be used as a ``@register`` decorator" (registry.py:46-47, 88-89). The class declares `strategy_id = "touch_reversal"`, `strategy_version = "1"`, `SectionModel = TouchReversalSection` (plugin.py:199-206).
3. **The package `__init__` imports the plugin module**, so importing the *package* is sufficient to register: "Importing this package imports ``plugin``, whose ``@register`` decorator populates the registry" (SC/src/strategy_core/strategies/touch_reversal/__init__.py:3-4); the actual imports are `from strategy_core.strategies.touch_reversal.plugin import (FixedPointsBarrier, TouchReversalPlugin)` and `from strategy_core.strategies.touch_reversal.section import TouchReversalSection`, with `__all__ = ["TouchReversalPlugin", "TouchReversalSection", "FixedPointsBarrier"]` (touch_reversal/__init__.py:12-18).
4. **There is NO auto-discovery** — `strategy_core/strategies/__init__.py` "is intentionally SIDE-EFFECT-FREE: it imports nothing, so importing ``strategy_core`` (or this package) never registers a plugin" (SC/src/strategy_core/strategies/__init__.py:8-11); the registry "starts EMPTY … A strategy lands in the table only when its plugin module is explicitly imported (the ``@register`` side-effect)" (registry.py:10-14). No entry-points exist (registry.py:5-8; PLAN.md:1389 rejects Option B).
5. **Consumers therefore perform an explicit registration import**: TL — `import strategy_core.strategies.touch_reversal  # noqa: F401` (TL/backend/src/trade_lab/services/model_registry.py:29); QL — the same line under the comment "The explicit registration import (touch_reversal/__init__ registers the plugin)" (QL/src/alpha_lab/agents/data_infra/ml/strategy_contract.py:49-52); SC's own wiring — `from strategy_core.strategies.touch_reversal import plugin as _tr_plugin  # noqa: F401 -- registers` (SC/src/strategy_core/runtime/wiring.py:21). The contract loader deliberately imports the registry lazily and resolves "whatever plugins the CALLER registered (the explicit registration import, D-B3c)" (SC/src/strategy_core/contract/loader.py:99-106).
6. **Lookup function** — exact signature: `def get_strategy(strategy_id: str) -> type[StrategyPlugin]:` (SC/src/strategy_core/strategies/registry.py:92), docstring "Resolve a registered plugin class by ``strategy_id``; fail closed on unknown id. Raises :class:`~strategy_core.contract.schema.ContractError` (never ``KeyError``) listing the registered ids" (registry.py:93-97; the raise at :100-103). Callers: the SC loader's section-validation hook `plugin_cls = get_strategy(contract.strategy_id)` then `plugin_cls.SectionModel.model_validate(dict(contract.section))` (SC/src/strategy_core/contract/loader.py:106-108); SC production wiring `"plugin": get_strategy("touch_reversal")()` — note it **instantiates the class with zero args** (SC/src/strategy_core/runtime/wiring.py:32-35); TL's router gate `plugin_cls = get_strategy(contract.strategy_id)` (TL/backend/src/trade_lab/services/model_registry.py:287). `StrategyRuntime` auto-attaches the registered plugin via `touch_reversal_kwargs()` when constructed without one (SC/src/strategy_core/runtime/state.py:207-220), and TL's serving service spreads `**touch_reversal_kwargs()` into runtime construction (TL/backend/src/trade_lab/services/strategy_core_service.py:113-115).

#### (d) Checklist — what a new `strategies/<id>/` package must export to become resolvable

1. A sub-package at `src/strategy_core/strategies/<strategy_id>/` inside SC (decision 9.5, SC/docs/PLATFORM_REFACTOR_PLAN.md:1443; SC/src/strategy_core/strategies/__init__.py:5-6).
2. A plugin **class** structurally satisfying all 16 `StrategyPlugin` members — 3 attrs (`strategy_id`, `strategy_version`, `SectionModel`) + 13 methods, 5 of them `@staticmethod` (SC/src/strategy_core/strategies/protocols.py:240-336) — presence-enforced by `isinstance(plugin_cls, StrategyPlugin)` at registration (SC/src/strategy_core/strategies/registry.py:51-55).
3. `strategy_id`: a non-empty `str` class attribute (registry.py:57-61), unique — a second, different class under the same id raises `ContractError` (registry.py:82-88); the design fixes it as a "stable lowercase slug, registry-keyed, version-free" (PLAN.md:1444, 1446).
4. `SectionModel`: a pydantic `BaseModel` subclass class attribute (registry.py:75-80); by production convention defined in the package's `section.py` as an `extra="forbid"` `_ContractModel` subclass (SC/src/strategy_core/strategies/touch_reversal/section.py:73-94) — it is what the loader validates the bundle's `section` subtree against via `get_strategy(strategy_id).SectionModel` (SC/src/strategy_core/contract/loader.py:106-108).
5. `required_bars()` must be callable at register time without raising and return a **non-empty `tuple` of `BarSpec`** (registry.py:63-73).
6. `strategy_version`: present (protocol member, protocols.py:253); its value must equal the `strategy_version` stamped in any bundle contract it is to serve — TL fails discovery/activation closed on mismatch (TL/backend/src/trade_lab/services/model_registry.py:290-295).
7. The class must be decorated `@register` (or passed to `register()`) in a module of the package, so importing it populates `_REGISTRY` (registry.py:38-47; SC/src/strategy_core/strategies/touch_reversal/plugin.py:195).
8. The package `__init__.py` must import that plugin module (re-exporting the plugin class + section is the production convention), so that `import strategy_core.strategies.<id>` alone triggers registration (SC/src/strategy_core/strategies/touch_reversal/__init__.py:3-4, 12-18).
9. Each consumer process must add the **explicit registration import** — nothing auto-discovers it: `strategies/__init__.py` is side-effect-free (SC/src/strategy_core/strategies/__init__.py:8-14), the registry starts empty (registry.py:10-14), and today's import sites are TL model_registry (TL/backend/src/trade_lab/services/model_registry.py:29), QL's contract emitter (QL/src/alpha_lab/agents/data_infra/ml/strategy_contract.py:52), and SC's own wiring (SC/src/strategy_core/runtime/wiring.py:21).
10. The class must be **zero-arg constructible** if resolved through the production wiring pattern, which instantiates `get_strategy(<id>)()` with no arguments (SC/src/strategy_core/runtime/wiring.py:33; the production plugin's `def __init__(self) -> None:`, SC/src/strategy_core/strategies/touch_reversal/plugin.py:208) — configuration arrives afterwards via `configure(section, ctx)` (protocols.py:257-258; SC/src/strategy_core/runtime/state.py:248-251).
11. Resolution is then `get_strategy(strategy_id: str) -> type[StrategyPlugin]`, which raises `ContractError` (never `KeyError`) listing registered ids on an unknown id (SC/src/strategy_core/strategies/registry.py:92-103).

---

## 2. What Strategy A (`touch_reversal`) implements

Doc-number resolution: the "9.x" labels are §9 of SC/docs/PLATFORM_REFACTOR_PLAN.md ("Decisions needed from the owner", SC/docs/PLATFORM_REFACTOR_PLAN.md:1353) — 9.1 = plugin interface shape (Protocol) at :1359, 9.2 = registry mechanism at :1384, 9.3 = section versioning at :1395, 9.8 = TL outcome-tracker retirement at :1472, 9.10 = quote handling at :1496.

#### (a) Protocol surface → touch_reversal implementation

`StrategyPlugin` is a `runtime_checkable` structural Protocol (SC/src/strategy_core/strategies/protocols.py:240-241); deeper validation runs at `@register` time (SC/src/strategy_core/strategies/registry.py:51-79). Full member list and the implementing code:

| Protocol member (protocols.py) | Implementation (plugin.py) | What it does |
|---|---|---|
| `strategy_id: str` (SC/src/strategy_core/strategies/protocols.py:252) | SC/src/strategy_core/strategies/touch_reversal/plugin.py:199 | Registry key, literal `"touch_reversal"`. |
| `strategy_version: str` (protocols.py:253) | plugin.py:205 | Literal `"1"`; stamped into every contract by QL and equality-checked at TL activation (comment plugin.py:200-204). |
| `SectionModel: type` (protocols.py:254) | plugin.py:206 | Binds the plugin's pydantic contract section, `TouchReversalSection`. |
| `configure(section, ctx)` (protocols.py:257) | plugin.py:220-228 | Stores the section, takes `tick_size` from ctx, rebuilds the plugin-owned `StrategyLevelState` from the section's session scheme (R1/R2). |
| `reset()` (protocols.py:261) | plugin.py:230-235 | Clears the level state and the first-touch dedup set. |
| `set_static_levels(levels)` (protocols.py:265) | plugin.py:237-239 | Seeds static reference levels into the plugin's level state (sole seed path since S-B3a). |
| `load_prior_day_summary(trading_day, *, high_ticks, low_ticks)` (protocols.py:270) | plugin.py:241-243 | Seeds the prior-day PDH/PDL summary into the level state. |
| `required_bars()` (static, protocols.py:277) | plugin.py:246-249 | Declares one TICK BarSpec: `BarSpec(kind=BarKind.TICK, size=147, label="147t")` (147 = `DEFAULT_TICK_COUNT`, plugin.py:88-89). |
| `decision_bar_label()` (static, protocols.py:282) | plugin.py:251-253 | Returns `"147t"`. |
| `on_event(event, ctx) -> tuple[Level, ...]` (protocols.py:287) | plugin.py:256-263 | The SOLE level fold: a `Trade` is folded into `self._levels` and the full post-fold level set is returned (mapped onto `RuntimeUpdate.levels`); a `Quote` returns `()`. |
| `on_bar_closed(bar, ctx) -> StrategyStep` (protocols.py:297) | plugin.py:265-295 | build_zones → pre-mark fired zones → detect_touches → record fired keys → wrap touches as setups/decisions; returns the sparse `StrategyStep`. |
| `current_levels()` (protocols.py:308) | plugin.py:298-300 | Full current level set for the platform snapshot's `levels`. |
| `snapshot_zones(trading_day)` (protocols.py:312) | plugin.py:302-317 | Display zones from `StrategyLevelState.zones()` (default proximity), pre-marked `touched` from the fired-keys set; `None` day returns unmarked zones. |
| `feature_spec()` (static, protocols.py:327) | plugin.py:320-329 | Declares the 6 feature names + interaction/approach partition + `nan_policy`, single-sourced from constants. |
| `label_policy()` (static, protocols.py:331) | plugin.py:331-343 | Declares `LabelPolicySpec(resolution=mae_first, barrier_mode="fixed_points", barrier=_FIXED_BARRIER, decision_offset_minutes, flatten_time, forward_cutoff, no_resolution_dropped)`. |
| `emitted_event_types()` (static, protocols.py:335) | plugin.py:345-360 | Declares 4 ws payload schemas: `touch.detected`, `observation.updated`, `prediction.created`, `prediction.resolved`. |

#### (b) What the plugin owns beyond the Protocol surface

**Level definitions/emission.** The level *state machine* is `StrategyLevelState` (SC/src/strategy_core/runtime/levels.py:39-125), instantiated and owned by the plugin (plugin.py:212, 225-228). Levels emitted: `pdh`/`pdl` from the prior full trading day's extremes (levels.py:99-100; source descriptor `PDH_PDL_SOURCE = "prior_day_full"`, SC/src/strategy_core/constants.py:213), `asia_high`/`asia_low`/`london_high`/`london_low` from per-session ranges (levels.py:101-107), plus any externally-seeded static levels (levels.py:58-59, 94-95). Canonical name list `SESSION_LEVELS` at constants.py:216-223. Each level carries an `available_from` instant — PDH/PDL from trading-day start (levels.py:99-100, 113-117), session levels from session close (levels.py:103, 119-125). Emission path: `plugin.on_event` → `StrategyLevelState.process_trade` (levels.py:66-90, which banks the completed day's extremes on day roll, levels.py:71-81) → the runtime maps the returned set onto `RuntimeUpdate.levels` (SC/src/strategy_core/runtime/state.py:351, 368).

**Zone semantics.** `build_zones` (SC/src/strategy_core/decisions/zones.py:23-106): greedy single-pass merge of price-sorted levels within `zone_proximity_pts` (default 3.0, constants.py:63) with the chained compare against the *last appended* level (zones.py:70-74), mean representative price (zones.py:79-80), strict-majority side with ties→LOW (zones.py:85-86), zone `available_from` = max of constituent availabilities (zones.py:93-94). There are TWO derivations, both plugin-owned since S-B3a: (1) the DETECTION derivation — the old runtime `_zones_for_detection` equivalent — inside `on_bar_closed`, using the section-driven `touch_rule.zone_proximity_pts` (plugin.py:276-281); (2) the DISPLAY derivation — the deleted runtime `_zones_for_snapshot` equivalent — inside `snapshot_zones`, using `StrategyLevelState.zones()` at default proximity (plugin.py:305-311; levels.py:110-111). The historic `_zones_for_detection` itself lived in the pre-plugin `StrategyRuntime` (quoted at SC/docs/PLATFORM_REFACTOR_PLAN.md:1367).

**Touch-detection hooks.** The platform gates which bars reach the plugin: only bars whose `timeframe_ticks == decision_timeframe` get `on_bar_closed` (state.py:353-358); the plugin deliberately does not re-gate (plugin.py:266-275). Detection is `detect_touches` (SC/src/strategy_core/decisions/touch.py:55-117): closed-interval straddle `bar_low <= rep <= bar_high` (`is_touch`, touch.py:42-52), the v3 availability gate skipping bars that close before `zone.available_from` without consuming first-touch (touch.py:98-102), and the per-day first-touch flag flip (touch.py:104-105). Cross-bar/cross-call dedup is plugin-owned: `self._fired_keys: set[ZoneKey]` (plugin.py:217), pre-mark before detection (plugin.py:282-284), record after (plugin.py:288-289, via `_touch_zone_key_from_touch`, plugin.py:363-373), keyed by the single-sourced `zone_key(trading_day, zone) = (trading_day, zone.names, zone.representative_price, zone.side.value)` (SC/src/strategy_core/decisions/dedup.py:21-30); cleared only on `reset()` (plugin.py:230-235).

**Side→direction map.** Two representations: the typed engine default `DEFAULT_DIRECTION_FROM_SIDE = {Side.LOW: Direction.LONG, Side.HIGH: Direction.SHORT}` (touch.py:36-39), and the plugin-owned lowercase WIRE vocabulary `direction_from_side={"low": "long", "high": "short"}` emitted by the default section (SC/src/strategy_core/strategies/touch_reversal/section.py:191-193; ownership ratified per constants.py:64-67). TL reads the wire map from the active bundle's section at TL/backend/src/trade_lab/services/inference/inference_engine.py:81; the QL emitter sources it verbatim from the plugin section (QL/src/alpha_lab/agents/data_infra/ml/strategy_contract.py:83).

**Contract SectionModel.** `TouchReversalSection(_ContractModel)` (section.py:73-104), fields exactly:
```python
session_scheme: SessionScheme
level_scheme: LevelScheme
touch_rule: TouchRule
feature_windows: FeatureWindows
interaction_features: tuple[str, ...] = Field(max_length=256)
approach_features: tuple[str, ...] = Field(max_length=256)
research_session_experiment: ResearchSessionExperiment | None = None
```
(section.py:88-94), plus an after-validator enforcing the partition is disjoint/duplicate-free (section.py:96-104). Module also owns `validate_feature_partition` (section↔envelope cross-check, section.py:107-123) and `default_touch_reversal_section()` (section.py:157-205). `label_policy` and `inference` are deliberately NOT in the section — they are platform-consumed and live in the contract ENVELOPE (section.py:15-18).

**Feature formulas.** All six formulas are ENGINE-side, in SC/src/strategy_core/decisions/features.py — not in the plugin. The plugin only *declares* names via `feature_spec()` (plugin.py:320-329), single-sourced from `INTERACTION_FEATURES` (constants.py:97-101) and `RUNTIME_APPROACH_FEATURES` (constants.py:115-119); `on_bar_closed` returns `features=()` (plugin.py:293) — the plugin computes no feature values.

| Feature | Formula lives at | Training caller (QL) | Serving caller (TL) |
|---|---|---|---|
| `int_time_beyond_level` | SC/src/strategy_core/decisions/features.py:68-100 | QL/src/alpha_lab/agents/data_infra/ml/engine_decision.py:458 | TL/backend/src/trade_lab/services/inference/features/feature_functions.py:181-186 |
| `int_time_within_2pts` | features.py:103-127 | engine_decision.py:459 | feature_functions.py (wrapper table :290-295) |
| `int_absorption_ratio` | features.py:130-165 | engine_decision.py:460 | feature_functions.py:207-212 |
| `app_large_trade_vol_pct` | features.py:168-191 | engine_decision.py:526 | feature_functions.py:290-295 |
| `app_avg_trade_size` | features.py:194-203 | engine_decision.py:525 | feature_functions.py:290-295 |
| `app_max_spread` | features.py:206-216 | engine_decision.py (quotes path, :515-519 window) | feature_functions.py:240-245 |

TL carries no formula implementations of its own — every feature function delegates to `strategy_core.decisions.features` (TL feature_functions.py:1-14, 27). The declared full approach MENU is 8 features (`APPROACH_FEATURES`, constants.py:103-112) but only 3 have engine formulas (`RUNTIME_APPROACH_FEATURES`, constants.py:113-119); the other 5 (`app_trade_count`, `app_volume_acceleration`, `app_avg_tob_imbalance`, `app_volatility_recent`, `app_volatility_ratio`) have no engine formula (noted at QL engine_decision.py:480-482).

**Label geometry ownership.** Split. The plugin owns the *declaration*: `LabelPolicySpec` via `label_policy()` (plugin.py:331-343) and the concrete `FixedPointsBarrier` (plugin.py:125-154; `stop_price`/`target_price` = entry ∓/± `sl_points`/`tp_points`, plugin.py:146-150; defaults tp=15/sl=30/trap=5 from constants.py:137-139), and the decision instant `touch close + DECISION_OFFSET_MINUTES` stamped into `TouchDecision` (plugin.py:392-401; offset = interaction window = 5 min, constants.py:140, 260-265). The label *kernels* are engine-side, outside the plugin: the MAE-first 3-class ladder `classify_mae_first`/`resolve_outcome` (SC/src/strategy_core/decisions/outcomes.py:63 and :127, header :1-44), the honest decision-time orchestration `resolve_honest_outcome` with flatten/cutoff/no_fill/no_forward drops and the strict `(decision_ts, rth_cutoff)` forward window (SC/src/strategy_core/decisions/honest_entry.py:76-176), and its streaming equivalent `StreamingHonestResolver` (SC/src/strategy_core/decisions/streaming.py:1-47).

#### (c) Every distinct attribute the platform reads off the plugin object

| Attribute | Representative call-site | Reader |
|---|---|---|
| `configure` | SC/src/strategy_core/runtime/state.py:251 | runtime construction |
| `reset` | SC/src/strategy_core/runtime/state.py:259 | runtime reset |
| `set_static_levels` | SC/src/strategy_core/runtime/state.py:272 | runtime lifecycle write-through |
| `load_prior_day_summary` | SC/src/strategy_core/runtime/state.py:276 | runtime lifecycle write-through |
| `on_event` | SC/src/strategy_core/runtime/state.py:351 | per-trade hot path |
| `on_bar_closed` | SC/src/strategy_core/runtime/state.py:358 | decision-bar hot path |
| `current_levels` | SC/src/strategy_core/runtime/state.py:302 | snapshot |
| `snapshot_zones` | SC/src/strategy_core/runtime/state.py:303 (also :369 per-trade) | snapshot + per-trade update |
| `strategy_id` | SC/src/strategy_core/strategies/registry.py:57 (register time); TL/backend/src/trade_lab/services/strategy_core_service.py:129 (`self._runtime._plugin.strategy_id`, a private-attr reach-through) | registry; TL activation guard |
| `strategy_version` | QL/src/alpha_lab/agents/data_infra/ml/strategy_contract.py:145 (emission); TL/backend/src/trade_lab/services/model_registry.py:290 (activation equality check); TL strategy_core_service.py:135; QL/scripts/migrate_contracts_v2.py:88 | QL emitter, TL activation, migration script |
| `SectionModel` | SC/src/strategy_core/contract/loader.py:106-108 (`get_strategy(contract.strategy_id).SectionModel.model_validate(...)`); SC registry.py:76-79 (register-time type assertion) | contract loader, registry |
| `required_bars` | SC/src/strategy_core/strategies/registry.py:65 | register-time assertion ONLY — no runtime caller |
| `label_policy` | QL strategy_contract.py:216 (`get_strategy(strategy_id).label_policy().barrier_mode`) | QL contract emitter |

Negative findings (absence): `feature_spec()` has NO production call-site in SC runtime, QL, or TL — its only caller is the SC test SC/tests/test_touch_reversal_plugin.py:207. `emitted_event_types()` and `decision_bar_label()` have NO call-sites anywhere outside their definitions (grep across all three repos; definitions at plugin.py:252, 321, 346 / protocols.py:282, 327, 335). Of the `StrategyStep` fields, production runtime code reads ONLY `step.touches` (state.py:359, the single `step.*` hit in SC src); `setups`, `decisions`, `features`, and `zones` (protocols.py:180-189) have no production reader.

Adjacent seam — reads through the plugin's *SectionModel instance* (the validated bundle section, not the plugin object): `section.feature_windows` (TL/backend/src/trade_lab/services/runtime.py:384; TL model_registry.py:85; TL feature_functions.py:98), `section.session_scheme` equality-checked against `default_touch_reversal_section().session_scheme` (TL model_registry.py:110), `section.touch_rule.bar_type` (TL model_registry.py:116), `section.touch_rule.direction_from_side` (TL inference_engine.py:81), `section.touch_rule.zone_proximity_pts` (SC plugin.py:276-280), and `validate_feature_partition(...)` run at both validation sites (QL strategy_contract.py:178; TL model_registry.py:456). QL's training data build additionally constructs the whole `StrategyRuntime` (which auto-attaches this plugin) rather than touching plugin attributes directly (QL engine_decision.py:628, 646).

---

## 3. What the platform provides

The design/worklist document referenced by the requester's "9.x" numbers was located: `SC/docs/PLATFORM_REFACTOR_PLAN.md` §9 "Decisions needed from the owner before implementation" (SC/docs/PLATFORM_REFACTOR_PLAN.md:1353). Item 9.10 (the quote accessor) is at SC/docs/PLATFORM_REFACTOR_PLAN.md:1496.

The single platform-provided surface a plugin sees is the `PlatformContext` Protocol (SC/src/strategy_core/strategies/protocols.py:85-119), documented as "EXACTLY the §2.2 surface PLUS the §9.10 `quotes_in_window` accessor; it has NO levels/scheme accessor" (SC/src/strategy_core/strategies/protocols.py:14-16). The concrete implementation is `RuntimePlatformContext` (SC/src/strategy_core/runtime/context.py:55), constructed by `StrategyRuntime.__init__` with live accessors `get_candles` / `get_closed_bars` / `get_scheme` / `get_trade_price` so it stays correct across `reset()` (SC/src/strategy_core/runtime/state.py:240-247). The same `ctx` object is handed to the plugin in `configure(section, ctx)`, `on_event(event, ctx)`, and `on_bar_closed(bar, ctx)` (SC/src/strategy_core/strategies/protocols.py:257,287,297).

#### (a) Bar/candle streams per timeframe

| Fact | Detail | Citation |
|---|---|---|
| Bar builder | `CandleEngine` builds all configured **tick** bars concurrently from trades; quotes are rejected by design ("Only `Trade` events are accepted") | SC/src/strategy_core/candles/streaming.py:86-98 |
| Timeframes | Constructor arg; default `(147, 987, 2000)` tick counts, deduped + sorted | SC/src/strategy_core/candles/streaming.py:100-110; SC/src/strategy_core/runtime/state.py:186,221 |
| Close triggers | `COMPLETE` when `trade_count == timeframe` (streaming.py:180-182); `END_OF_DAY` incomplete freeze on trading-day roll (streaming.py:143-144); `timeframe == 1` closes on its seeding trade (streaming.py:164-165); explicit `finalize_trading_day()` (streaming.py:194-206) | SC/src/strategy_core/candles/streaming.py |
| Decision-bar push | The runtime calls `plugin.on_bar_closed(bar, ctx)` **only** for closed bars whose `timeframe_ticks == self.decision_timeframe` | SC/src/strategy_core/runtime/state.py:353-359 |
| Decision timeframe | `decision_timeframe or min(timeframes)` (state.py:222); TL pins it explicitly to `min(self._display_timeframes)` | SC/src/strategy_core/runtime/state.py:222; TL/backend/src/trade_lab/services/strategy_core_service.py:100-116 |
| Pull access | `ctx.closed_bars(label)` — recent closed bars filtered to the label's timeframe; `ctx.current_bar(label)` — the forming bar via a pure `snapshot_update(())` read | SC/src/strategy_core/runtime/context.py:75-89; protocol at SC/src/strategy_core/strategies/protocols.py:99-105 |
| Label routing | Label→timeframe is parsed as the leading integer of the BarSpec label (e.g. `"147t"` → 147) | SC/src/strategy_core/runtime/context.py:49-52 |
| Closed-bar retention | One shared ring across ALL timeframes, `recent_closed_bar_limit: int = 500` bars, trimmed on append | SC/src/strategy_core/runtime/state.py:191,223-225,344-347 |
| Subscription declaration | `required_bars() -> tuple[BarSpec, ...]` with `BarSpec(kind: BarKind, size: int, label: str)`; `size` = tick count for `TICK` or interval seconds for `TIME` | SC/src/strategy_core/strategies/protocols.py:68-82,276-284 |

`BarKind` has two members: `TICK = "tick"` ("CURRENT engine") and `TIME = "time"` ("NEW close trigger, Phase F") (SC/src/strategy_core/strategies/protocols.py:61-65). TIME bars are declaration-only: `CloseReason` has exactly `COMPLETE` and `END_OF_DAY` (SC/src/strategy_core/types.py:51-55), and the engine validates "tick timeframes must be positive" (SC/src/strategy_core/candles/streaming.py:107-108). Deferral is PLAN decision 9.4 Option A (SC/docs/PLATFORM_REFACTOR_PLAN.md:1416-1432).

**Absence:** nothing in the runtime consumes `required_bars()` / `decision_bar_label()` to wire the candle engine — the only consumer is the registry-time validation of `required_bars()` (SC/src/strategy_core/strategies/registry.py:63-73); `decision_bar_label()` has no consumer at all. Timeframes and the decision bar come from `StrategyRuntime` constructor args (SC/src/strategy_core/runtime/state.py:184-186), set in production by TL (TL/backend/src/trade_lab/services/strategy_core_service.py:102-109).

#### (b) Session folding

- **Where defined:** `SessionScheme` (timezone, `trading_day_boundary`, named `SessionWindow`s, optional `closed_window`) and `SessionWindow.contains(t)` — half-open `start <= t < end`; cross-midnight windows use `t >= start or t < end` (SC/src/strategy_core/types.py:176-214, contains at 187-190).
- **The v3 canonical scheme** `RESEARCH_SESSION_SCHEME` (ET, `US/Eastern`): `asia` 19:00→02:45 with `crosses_midnight=True`, `london` 03:00→08:00, `ny` 09:00→17:00; trading-day boundary 18:00 ET; `closed_window=None` (SC/src/strategy_core/constants.py:168-177). Gaps 18:00-19:00 / 02:45-03:00 / 08:00-09:00 / 17:00-18:00 ET classify as `"none"` (SC/src/strategy_core/decisions/sessions.py:49-52). A non-canonical Chicago scheme `TRADE_LAB_CT_SESSION_SCHEME` exists "only so the divergence is documented" (SC/src/strategy_core/constants.py:179-192).
- **Folding function:** `classify_session(ts_utc, scheme) -> SessionInfo` — closed-window check, 18:00-ET rollover (`local_time >= trading_day_boundary` rolls to next calendar day), first-matching-window name else `"none"`; raises on naive timestamps (SC/src/strategy_core/decisions/sessions.py:71-109). `trading_day_for` (112-122) is the shared candle trading-day primitive (SC/src/strategy_core/candles/streaming.py:130-132).
- **Plugin seam:** `ctx.session_at(ts_utc)` → `classify_session(ts_utc, self._get_scheme()).session` under the live scheme (SC/src/strategy_core/strategies/protocols.py:111-113; SC/src/strategy_core/runtime/context.py:100-101). The runtime also stamps `session`/`trading_day` onto every snapshot (SC/src/strategy_core/runtime/state.py:373-377,308-309).
- **Scheme ownership:** per resolution R2 the plugin gets its scheme from its SECTION, not from `ctx` (SC/src/strategy_core/strategies/protocols.py:14-16); the touch plugin converts the contract-form scheme into the runtime type itself (SC/src/strategy_core/strategies/touch_reversal/plugin.py:92-122). A runtime constructed with a non-default scheme must supply its own plugin+section — fail-loud guard (SC/src/strategy_core/runtime/state.py:207-213).
- **Constraint:** the platform level tracker hardcodes the session-range set `{"asia": _Range(), "london": _Range()}` — a different session set is not pure config (SC/src/strategy_core/runtime/levels.py:49,56,85,101; flagged as PLAN §9.9, SC/docs/PLATFORM_REFACTOR_PLAN.md:1483-1492).

#### (c) Quote accessor (design item 9.10)

- **Protocol seam:** `quotes_in_window(self, start_ts_utc: datetime, end_ts_utc: datetime) -> Sequence[Quote]` — "Buffered top-of-book quotes whose ts is in `[start, end)` (§9.10)" (SC/src/strategy_core/strategies/protocols.py:115-119). `Quote` is L0/L1 top-of-book only: "Deeper book levels are intentionally absent" (SC/src/strategy_core/types.py:74-75); fields `event_ts_utc, bid_price_ticks, ask_price_ticks, bid_size, ask_size` (serialized at SC/src/strategy_core/runtime/state.py:85-94).
- **Current implementation is a STUB:** `RuntimePlatformContext.quotes_in_window` returns `()` with `TODO(§9.10): back this with a bounded quote buffer so app_max_spread has live data when a quote-consuming plugin is wired` (SC/src/strategy_core/runtime/context.py:103-109). The retention-window decision is recorded as open (SC/src/strategy_core/runtime/context.py:19-21; SC/docs/PLATFORM_REFACTOR_PLAN.md:1500).
- **Quotes never reach the plugin at all today:** `StrategyRuntime._process_quote` only updates `_last_quote` / `_last_event_ts_utc` / feed status and does not call `plugin.on_event` (SC/src/strategy_core/runtime/state.py:313-317); the protocol's `on_event(Trade | Quote, ctx)` Quote arm is specified to return `()` (SC/src/strategy_core/strategies/protocols.py:287-294) and the touch plugin notes "Quotes are inert (the runtime's `_process_quote` … never reaches here)" (SC/src/strategy_core/strategies/touch_reversal/plugin.py:256-263).
- **The one quote-consuming feature formula** exists in the engine, `app_max_spread(quotes: Sequence[Quote], tick_size: float) -> float` (SC/src/strategy_core/decisions/features.py:206); at serving time it is fed from TL's `MarketContextBuffer`, not from `ctx` (TL/backend/src/trade_lab/services/inference/features/feature_functions.py:26-30).

#### (d) Prior-day summary (PDH/PDL / prev_full_hl)

- **Computed in** `StrategyLevelState` (SC/src/strategy_core/runtime/levels.py:39-125): per-trade day-extreme accumulation (levels.py:86-87); at a trading-day roll the completed day's extremes are banked into `_summaries` organically ("W1 P2b"), with an explicit seed staying authoritative (levels.py:71-85); `pdh`/`pdl` `Level`s are emitted from the most recent prior summary with `available_from` = the trading-day start (prior 18:00 ET) (levels.py:96-100,113-117); `asia_high/low`, `london_high/low` carry `available_from` = their session close (levels.py:101-107,119-125). Semantics: full prior 18:00→18:00 day extremes, `PDH_PDL_SOURCE = "prior_day_full"` (SC/src/strategy_core/constants.py:209-213).
- **Seam:** since S-B3a the level fold is plugin-owned; the runtime lifecycle methods write through — `StrategyRuntime.load_prior_day_summary(trading_day, *, high_ticks, low_ticks)` → `plugin.load_prior_day_summary(...)` (SC/src/strategy_core/runtime/state.py:274-276), declared "the SOLE level-seed path since S-B3a (W4/D-B2j)" (SC/src/strategy_core/strategies/protocols.py:270-273); same pattern for `set_static_levels` (state.py:270-272; protocols.py:265-268). The touch plugin delegates both into its own `StrategyLevelState` (SC/src/strategy_core/strategies/touch_reversal/plugin.py:237-243).
- **Read-back:** the platform reads levels/zones from the plugin via `current_levels()` / `snapshot_zones(trading_day)` for `RuntimeUpdate`/`RuntimeSnapshot` (SC/src/strategy_core/runtime/state.py:297-311,351,369; SC/src/strategy_core/strategies/touch_reversal/plugin.py:298-317).
- **Production seeding:** TL live warm-start calls `_load_prior_day_summary()` "before any event reaches the engine" (TL/backend/src/trade_lab/services/live.py:236-238) through the adapter `StrategyCoreService.load_prior_day_summary` (TL/backend/src/trade_lab/services/strategy_core_service.py:144-149).

#### (e) Market-context buffer

This service is **TL-owned, not SC-owned**: `MarketContextBuffer` lives at TL/backend/src/trade_lab/domain/market_context.py:50.

| Aspect | Fact | Citation |
|---|---|---|
| Holds | `BufferedTrade(event_ts_utc, price_ticks, size, side)` and `BufferedQuote(event_ts_utc, bid_price_ticks, ask_price_ticks)` — "structurally no field capable of holding L2/L3 depth" | TL/backend/src/trade_lab/domain/market_context.py:1-8,31-48 |
| Retention | Time-based, default `DEFAULT_RETENTION_MINUTES = 45` (config default 45, range 45-240); contract-driven at activation: `approach + interaction + slack` minutes | TL/backend/src/trade_lab/domain/market_context.py:22; TL/backend/src/trade_lab/config.py:64; TL/backend/src/trade_lab/services/runtime.py:368-390 |
| Count ceiling | `DEFAULT_MAX_ELEMENTS = 6_000_000` safety cap ("the old 200k cap evicted the feature windows during NY RTH") | TL/backend/src/trade_lab/domain/market_context.py:23-28 |
| Eviction | Driven by newest appended timestamp (replay evicts identically to live); `set_retention` re-evicts immediately on shrink | TL/backend/src/trade_lab/domain/market_context.py:51-56,128-138,155-176 |
| Fed by | `ApplicationRuntime` appends every quote/trade (`append_quote` / `append_trade`) | TL/backend/src/trade_lab/services/runtime.py:176-179,811,844 |
| Access seam | `trades_in_window(start, end)`, `quotes_in_window(start, end)` (end-exclusive), `latest_mid_price_ticks()`; TL feature functions have signature `[MarketContextBuffer, FeatureWindow, LevelContext] -> float` | TL/backend/src/trade_lab/domain/market_context.py:98-126; TL/backend/src/trade_lab/services/inference/features/feature_functions.py:141 |
| Activation gate | A contract whose required window exceeds `capabilities.market_context_retention_minutes` is refused | TL/backend/src/trade_lab/services/model_registry.py:57,87-91 |

**Absence:** no `PlatformContext` member exposes this buffer to an SC plugin (the full protocol surface is SC/src/strategy_core/strategies/protocols.py:96-119); its SC-side analogue is the stubbed `quotes_in_window` (SC/src/strategy_core/runtime/context.py:103-109).

#### (f) The runtime context object — full attribute inventory

`PlatformContext` (SC/src/strategy_core/strategies/protocols.py:85-119) — the complete surface, verbatim member list:

| Member | Signature / value | Backing (RuntimePlatformContext) |
|---|---|---|
| `tick_size: float` | attribute; the only member on the B2 byte-identity hot path | ctor value (SC/src/strategy_core/runtime/context.py:68; state.py:241) |
| `point_value: float` | attribute | `point_value_for_symbol(symbol)` — `POINT_VALUE` root lookup (NQ=20.0, ES=50.0), `0.0` when unknown (SC/src/strategy_core/runtime/context.py:36-46; SC/src/strategy_core/constants.py:58) |
| `closed_bars(label) -> Sequence[Bar]` | "Recently-closed bars for the BarSpec identified by `label`" | 500-bar shared ring filtered by parsed timeframe (context.py:75-80; state.py:244) |
| `current_bar(label) -> Bar \| None` | the forming bar | pure `snapshot_update(())` read of the candle engine (context.py:82-89) |
| `trade_price_at(ts_utc) -> float \| None` | "Realistic front-month trade-print price at a decision instant (honest fill)" | `StrategyRuntime.trade_price_at`: most recent `price > 0` print at/before `ts` within a 30-min lookback (`TRADE_PRICE_LOOKBACK_MINUTES = 30`, SC/src/strategy_core/decisions/streaming.py:72) over a bounded deque retained 2× the lookback (SC/src/strategy_core/runtime/state.py:235-236,319-332,338-342) |
| `session_at(ts_utc) -> str \| None` | session name under the active scheme | `classify_session(ts, get_scheme()).session` (context.py:100-101) |
| `quotes_in_window(start, end) -> Sequence[Quote]` | §9.10 accessor | stub `()` (context.py:103-109) |

There is deliberately **no** levels accessor, no zones accessor, no scheme attribute on `ctx` (R2, SC/src/strategy_core/strategies/protocols.py:88-94). The runtime-state containers the platform builds *from* plugin output are `RuntimeUpdate` (`feed_status, warnings, current_bars, closed_bars, levels, zones, touches, last_quote` — SC/src/strategy_core/runtime/state.py:123-132) and `RuntimeSnapshot` (those plus `session, trading_day, metadata` — SC/src/strategy_core/runtime/state.py:150-162); the plugin's return path is `StrategyStep(setups, decisions, features, touches, zones)` (SC/src/strategy_core/strategies/protocols.py:170-189).

#### (g) Replay vs live wiring — the shared seam

The shared seam is `StrategyRuntime.process_event(event: Trade | Quote | DataQualityWarning) -> RuntimeUpdate` (SC/src/strategy_core/runtime/state.py:285-292): one event-at-a-time fold that feeds the candle engine, the trade ring, and the single plugin (`on_event` at state.py:351, `on_bar_closed` at state.py:353-359). The plugin is attached once at construction — auto-attach of the registered `touch_reversal` plugin via `touch_reversal_kwargs()` when none is passed (SC/src/strategy_core/runtime/state.py:207-220; SC/src/strategy_core/runtime/wiring.py:27-35), and `wiring.py` is documented as the "ONE helper, called by every production construction site" (SC/src/strategy_core/runtime/wiring.py:1-14).

- **SC replay controller:** `ReplayRuntime(runtime, source, *, process_item=None, on_update=None, ...)` (SC/src/strategy_core/runtime/replay.py:59-94); its `_default_process_item` is literally `self.runtime.process_event(item)` (replay.py:221-224). Adds pacing (`speed`, delay capped 0.25 s, replay.py:142-144), pause/resume/stop (replay.py:198-210), timestamp-regression callback (replay.py:151-159), and the W3b livelock guard (`finally` forces a terminal FAILED state, replay.py:179-196).
- **SC live controller:** `LiveRuntime(source, *, process_item, on_update=None, ...)` — "The source only needs `start()`, `stop()`, and async `events()` methods. Integrations provide `process_item` to map provider/domain items into their runtime update shape" (SC/src/strategy_core/runtime/live.py:46-70); events are consumed in an asyncio task (`_run`, live.py:139-154). Both exported from `strategy_core.runtime` (SC/src/strategy_core/runtime/__init__.py:3-5).
- **TL wiring (both modes → the same fold):** replay builds `CoreReplayRuntime(None, adapter, process_item=self._process_replay_item, ...)` (TL/backend/src/trade_lab/services/replay.py:219-227) and live builds `CoreLiveRuntime(feed, process_item=self._process_live_item, ...)` (TL/backend/src/trade_lab/services/live.py:250-257). Both `process_item` callables converge on `ApplicationRuntime.process_market_event` (TL/backend/src/trade_lab/services/replay.py:263-266; TL/backend/src/trade_lab/services/live.py:341-354; TL/backend/src/trade_lab/services/runtime.py:704-712), which calls `StrategyCoreService.process_market_event` → `self._runtime.process_event(...)` on the one SC `StrategyRuntime` instance constructed with `**touch_reversal_kwargs()` (TL/backend/src/trade_lab/services/strategy_core_service.py:100-116,160-168). So live and replay drive the identical plugin instance through the identical `process_event` seam; only the controller and item-mapping differ.

#### Engine services NOT currently reachable through the plugin seam (absence facts)

1. **Live quote data** — `quotes_in_window` is a stub returning `()` (SC/src/strategy_core/runtime/context.py:103-109) and quotes never reach `plugin.on_event` (SC/src/strategy_core/runtime/state.py:313-317). The §9.10 bounded quote buffer does not exist in SC.
2. **TIME bars** — declarable via `BarSpec(kind=BarKind.TIME, ...)` (SC/src/strategy_core/strategies/protocols.py:61-82) but not buildable: tick-only close trigger (SC/src/strategy_core/candles/streaming.py:180-182), no `INTERVAL` member in `CloseReason` (SC/src/strategy_core/types.py:51-55); deferred per PLAN §9.4 (SC/docs/PLATFORM_REFACTOR_PLAN.md:1416-1432).
3. **Declared-bars wiring** — `required_bars()` is validated at `@register` (SC/src/strategy_core/strategies/registry.py:63-73; `decision_bar_label()` is not validated there and has no consumer) but neither is ever read by `StrategyRuntime` to configure timeframes or the decision bar (constructor args instead, SC/src/strategy_core/runtime/state.py:184-186,221-222).
4. **`StreamingHonestResolver`** (the D1b honest-outcome service, SC/src/strategy_core/decisions/streaming.py:141-344) — not on `PlatformContext`; TL constructs it directly from the *contract's* label policy with a `trade_price_at` closure into the SC trade ring (TL/backend/src/trade_lab/services/runtime.py:392-425), not from the plugin's `label_policy()` declaration. PLAN §9.8 records the tracker-retirement decision it implements (SC/docs/PLATFORM_REFACTOR_PLAN.md:1472-1479).
5. **Feature formulas** — SC ships `int_time_beyond_level`, `int_time_within_2pts`, `int_absorption_ratio`, `app_large_trade_vol_pct`, `app_avg_trade_size`, `app_max_spread` (SC/src/strategy_core/decisions/features.py:68,103,130,168,194,206), but no platform path computes them for a plugin: `StrategyStep.features` exists (SC/src/strategy_core/strategies/protocols.py:182) and the touch plugin returns `features=()` (SC/src/strategy_core/strategies/touch_reversal/plugin.py:292-295); live feature computation happens TL-side over `MarketContextBuffer` importing the SC formulas (TL/backend/src/trade_lab/services/inference/features/feature_functions.py:26-30).
6. **The market-context buffer** — TL-owned (see (e)); no SC plugin seam reaches it.
7. **`StrategyLevelState`** — provided as an importable library class the plugin composes (`from strategy_core.runtime.levels import StrategyLevelState`, SC/src/strategy_core/strategies/touch_reversal/plugin.py:58,212), not via `ctx`; its session-range set is hardcoded to `"asia"`/`"london"` (SC/src/strategy_core/runtime/levels.py:49; PLAN §9.9, SC/docs/PLATFORM_REFACTOR_PLAN.md:1483-1492).

---

## 4. QL training integration

#### (a) training_mode registration — TOUCH_REVERSAL-SPECIFIC (two hardcoded mode literals; no registry)

There is no training-mode registry. `training_mode` is a plain `str` field on `MLPipelineConfig` with default `"extrema_rebound_crossing"` and a docstring naming exactly two modes: `'extrema_rebound_crossing' or 'dashboard_utility'` (QL/src/alpha_lab/agents/data_infra/ml/config.py:353-356). No enum/validator restricts the value; an unrecognized mode simply falls through every `== "dashboard_utility"` branch and gets the minimal (unservable) contract (QL/src/alpha_lab/agents/data_infra/ml/strategy_contract.py:141, 151-166).

Selection is a Streamlit radio with literal English labels `["Extrema Rebound/Crossing", "Dashboard Utility (3-class)"]`; `is_utility_mode` is a literal string comparison (QL/scripts/ml_training_tab.py:1893-1904).

Mode-literal branch sites (all string-equality on `"dashboard_utility"`):

| Site | What it gates |
|---|---|
| QL/scripts/ml_training_tab.py:276-280 | feature-column selection (`int_`/`app_` vs `pl_`/`ms_`/`sig_` prefixes) |
| QL/scripts/ml_training_tab.py:295, 314 | session-experiment scoping applied only in utility mode |
| QL/scripts/ml_training_tab.py:484-492 | eval binarization: class 0 (`tradeable_reversal`) is positive in utility mode, class 1 otherwise |
| QL/scripts/ml_training_tab.py:717-739 | class-balance dicts with literal class names `tradeable_reversal`/`trap_reversal`/`aggressive_blowthrough` |
| QL/scripts/ml_training_tab.py:1397-1398 | leakage-purge horizon derivation |
| QL/scripts/ml_training_tab.py:2197 | label column: `"label_encoded"` in utility mode, else `_LABEL_OPTIONS` literals (`label_20t/40t/60t`, QL/scripts/ml_training_tab.py:35-39) |
| QL/scripts/ml_training_tab.py:2294, 2337, 2473, 2504 | config construction for build/train (both mode literals) |
| QL/scripts/run_dashboard_session_experiment.py:129 | CLI (D-036/W3 path) hardcodes `training_mode="dashboard_utility"` |
| QL/src/alpha_lab/agents/data_infra/ml/config.py:409 | mode literal folded into `dataset_config_hash()` |

A new mode requires: a new literal at every branch above, a builder dispatch (the two builders are hardwired: `build_utility_dataset` vs `ExtremaDatasetBuilder` at QL/scripts/ml_training_tab.py:2312-2322 / 108), and contract-emitter support — today only `mode == "dashboard_utility"` emits a `supported_by_runtime: True` contract (QL/src/alpha_lab/agents/data_infra/ml/strategy_contract.py:151, 193); anything else gets `"supported_by_runtime": False` (line 162), which TL refuses to serve (TL/backend/src/trade_lab/services/model_registry.py:296-297).

#### (b) labeler / dataset-builder hooks — TOUCH_REVERSAL-SPECIFIC (engine drive is shared, but plugin arrives via SC default wiring, not `get_strategy`; labeler is a direct engine-function import)

Entry point: `def build_utility_dataset(dates, data_dir, config, progress_fn=None, *, use_engine: bool = True) -> pd.DataFrame` (QL/src/alpha_lab/agents/data_infra/ml/dashboard_utility_builder.py:110-117); `use_engine=False` raises (retired legacy path, lines 135-139). Per day it delegates to `engine_decision.process_single_date_stream` (builder lines 266-274; QL/src/alpha_lab/agents/data_infra/ml/engine_decision.py:590-598) — a batch drive of the shared SC runtime.

- **Plugin resolution**: the builder constructs `StrategyRuntime(timeframes=(tick_count,), requested_symbol=symbol)` with NO plugin argument (QL/src/alpha_lab/agents/data_infra/ml/engine_decision.py:646). SC then auto-attaches the registered `touch_reversal` plugin + default section via `touch_reversal_kwargs()` (SC/src/strategy_core/runtime/state.py:207-219). So the dataset builder is NOT plugin-resolved via `get_strategy` — it inherits SC's hardwired default. In all of QL, `get_strategy` appears only in the contract emitter (strategy_contract.py:145, 216), migrate_contracts_v2.py:42/88, and tests.
- **Labeler**: `resolve_honest_outcome` is a direct top-level engine import (QL/src/alpha_lab/agents/data_infra/ml/engine_decision.py:65; defined at SC/src/strategy_core/decisions/honest_entry.py:76), called per touch with per-run barrier params `tp_points/sl_points/trap_mfe_min/decision_offset_minutes` from `DashboardUtilityConfig` (engine_decision.py:746-755). It is not routed through the plugin; the plugin's `label_policy()` is consulted only at contract emission (strategy_contract.py:216).
- **Touch-specific hardcodes in the builder**: drop rules `HonestEntryDrop`/`NO_RESOLUTION` (engine_decision.py:756-759) and `< 5 interaction trades -> drop row` (764-766); output row schema literals `direction`, `representative_price`, `level_type`, `label`, `label_encoded`, `max_mfe`, `max_mae`, plus `label_window_end` pinned to `RTH_END` (engine_decision.py:786-802); the PDH/PDL `prev_full_hl` seed carry + parquet seed-stamp guard (dashboard_utility_builder.py:54-107, 147-196; engine_decision.py:647-652 `load_prior_day_summary`); trade-grid assertion `TRADE_TICK == DEFAULT_TICK_SIZE` (engine_decision.py:86-88).
- **Cache**: per-day `ml_utility_<tag>.parquet` (dashboard_utility_builder.py:155) keyed by `dataset_config_hash()` (line 142), which folds SC `BAR_PRICE_SOURCE`/`LABEL_ENTRY_REFERENCE`/`PLATFORM_VERSION` plus the literal `"decision_pipeline=sc_runtime_stream_v1"` (config.py:405-423). The W3 cache warmer warms exactly these files (QL/scripts/w3_cache_warmer.py:9, 147-148, 237-241).

#### (c) feature derivation — TOUCH_REVERSAL-SPECIFIC (QL-recomputed from the stream by literal function calls; feature lists are QL-local literals, not strategy-resolved)

The training feature matrix is NOT harvested from engine-emitted per-touch feature payloads. QL recomputes features batch-side by calling the 6 SC formulas by literal name over the in-memory trade/quote stream: `int_time_beyond_level`, `int_time_within_2pts`, `int_absorption_ratio` (QL/src/alpha_lab/agents/data_infra/ml/engine_decision.py:768-779) and `app_large_trade_vol_pct`, `app_avg_trade_size`, `app_max_spread` (engine_decision.py:805-817); the imports are literal names from `strategy_core` (engine_decision.py:55-63).

Feature lists are module-level literals in QL config, not resolved from the plugin:
- `LIVE_APPROACH_FEATURES` — 8 literal names (QL/src/alpha_lab/agents/data_infra/ml/config.py:17-26); note the stream builder computes only 3 of the 8 (engine_decision.py:811-816), while the `include_approach_features` field description says "27 approach-window order flow features" (config.py:337-342).
- `LIVE_INTERACTION_FEATURES` — 3 literal names (config.py:28-32); `LIVE_ALL_FEATURES` (config.py:35).
- A duplicate literal `DASHBOARD_FEATURES` list in the builder (QL/src/alpha_lab/agents/data_infra/ml/dashboard_utility_builder.py:38-42).
- Stale-cache columns are stripped against the literal lists (dashboard_utility_builder.py:207-219).
- Training/display column selection is by literal prefix `("int_", "app_")` vs `("pl_", "ms_", "sig_")` (QL/scripts/ml_training_tab.py:276-280, 2385-2387; the extrema display-side selection at 2426 uses the prefixes `("pl_", "ms_")` only); the secondary export script hardcodes `FEATURE_COLS` and `CLASS_NAMES` outright (QL/scripts/train_dashboard_model.py:27-39).

The only strategy-resolved touchpoint is at emission: the selected features are partitioned against the QL literal lists (strategy_contract.py:170-171) and cross-checked against the plugin's section via `validate_feature_partition` (strategy_contract.py:177-178) — imported directly from the touch_reversal package (strategy_contract.py:56-60), not from `get_strategy`.

#### (d) contract emission — mechanics GENERIC (registry-resolved id/version, strategy-agnostic envelope, plugin-typed section) but the emission path is single-strategy today

Signature: `def build_strategy_contract(config: MLPipelineConfig, selected_features: list[str] | None, *, strategy_id: str) -> dict | None` (QL/src/alpha_lab/agents/data_infra/ml/strategy_contract.py:110-115).

GENERIC pieces: `strategy_id` is resolved fail-closed via `get_strategy(strategy_id).strategy_version` — an unknown id raises before anything is written (strategy_contract.py:145); `label_policy.barrier_mode` is sourced from the plugin's own `label_policy()` declaration (strategy_contract.py:216); the envelope keeps the section subtree raw and strategy-agnostic (SC/src/strategy_core/contract/schema.py:284-292, 310-312) and TL types it via `get_strategy(...).SectionModel` (SC/src/strategy_core/contract/loader.py:99-113).

TOUCH_REVERSAL hardcodes in the emission path:
- The caller passes the literal: `strategy = build_strategy_contract(config, selected, strategy_id="touch_reversal")` (QL/scripts/ml_training_tab.py:1758).
- Registration import literal: `import strategy_core.strategies.touch_reversal  # noqa: F401` (strategy_contract.py:52) — the registry is empty without it.
- Section construction imports `TouchReversalSection`, `default_touch_reversal_section`, `validate_feature_partition` directly from the plugin package (strategy_contract.py:56-60) and `_build_touch_reversal_section(...)` (strategy_contract.py:71-107) is touch-only; there is no `strategy_id -> section-builder` dispatch.
- Full contract emitted only for `training_mode == "dashboard_utility"` (strategy_contract.py:151); the interaction/approach partition uses the QL literal feature lists (170-171); `class_map` comes from SC `k.CLASS_NAMES` and `inference` from SC constants `k.TRADEABLE_REVERSAL` / `k.INFERENCE_ELIGIBLE_SESSION` / `k.DEFAULT_CONFIDENCE_GATE` (strategy_contract.py:182, 231-233) — touch-reversal values living in SC constants, not plugin methods.

Sections written (strategy_contract.py:184-253): `contract_version`, `platform_version`, `strategy_id`, `strategy_version`, `training_mode`, `supported_by_runtime`, `instrument`, `tick_size`, `point_value`, `model` (`type`/`loss_function`/`file: "model.cbm"`), `class_map`, `feature_set` (`names`, `order_is_contractual`, `nan_policy`), `label_policy` (incl. per-run `tp_points`/`sl_points`/`trap_mfe_min`/`decision_offset_minutes`), `inference`, `data_requirements`, `provenance` (`dataset_config_hash` + catboost params), `section` (dumped from the typed plugin instance).

Version stamping: there is no `engine_version` key in current emission — E1 renamed the axis; the emitter stamps `platform_version = PLATFORM_VERSION` and `contract_version = CONTRACT_VERSION` (strategy_contract.py:185-186), values `"strategy_core_platform_v1"` and `"trade_lab_contract_v3"` (SC/src/strategy_core/__init__.py:61, 75). A standing no-drift test pins structural fields to SC constants and hardcodes `get_strategy("touch_reversal")` (QL/tests/agents/test_strategy_contract_nodrift.py:93, 107, 208).

`migrate_contracts_v2.py` contract-shape assumptions: it migrates only bundles whose legacy `engine_version == "strategy_core_engine_v3"` (`MIGRATABLE_ENGINE_VERSION`, QL/scripts/migrate_contracts_v2.py:48, 67), renames `engine_version -> platform_version` in place (84-85), and stamps `strategy_id = ROUTER_STRATEGY_ID = "touch_reversal"` on EVERY migratable bundle (44, 86-88) — i.e., it assumes all deployed bundles are touch_reversal. Backup to `strategy.json.pre_v2.bak` (74-76); sidecar regenerated as bare-digest text (94-99).

#### (e) save-path / bundle requirements — GENERIC layout (no strategy literals in the file set; the strategy binding rides inside strategy.json)

Save path: bundles are written to `<QL repo>/models/<model_name>` (`_DEFAULT_MODEL_DIR`, QL/scripts/ml_training_tab.py:33; `output_dir = _DEFAULT_MODEL_DIR / model_name`, line 2840; CLI naming at QL/scripts/run_dashboard_session_experiment.py:224-227). `save_trained_model(trained_model, eval_result, config, output_dir, *, training_result, dates_used, allow_failed_gates=False)` (QL/scripts/ml_training_tab.py:1547-1556) refuses to save on failed quality gates unless overridden (1564-1573) and refuses partial bundles (`training_result`/`config` required, 1578-1589).

Files written (all mandatory; any writer failure aborts the save):

| File | Producer | Contents required downstream |
|---|---|---|
| `model.cbm` | `ExtremaModelTrainer.save_model` (ml_training_tab.py:1594; QL/src/alpha_lab/agents/data_infra/ml/model_trainer.py:176) | CatBoost binary; its `feature_names_` must equal contract `feature_set.names` (TL/backend/src/trade_lab/services/model_registry.py:587-589) |
| `metadata.json` | model_trainer.py:178-185 (`selected_features`, `feature_importances`, `train_metrics`, `config`), then augmented with `pipeline_config`/`session_experiment`/`session_filter` (ml_training_tab.py:1728-1740) | `selected_features` must equal contract `feature_set.names` tuple-exactly (TL model_registry.py:317-322) |
| `model.cbm.sha256` | ml_training_tab.py:1598-1600, format `"<hex>  model.cbm\n"` | optional at discovery (TL model_registry.py:235, 542-550) but verified at activation when present — mismatch fails closed (TL model_registry.py:552-567; parses `split()[0]`, so both this format and migrate's bare digest pass) |
| `evaluation.json` | ml_training_tab.py:1720-1721 (gates, `training_mode`, `full_config`, OOS summaries, `dates_used`) | not read by TL's registry |
| `oos_predictions.parquet` | unconditional row-level OOS evidence; missing frame aborts save (ml_training_tab.py:1661-1670) | not read by TL's registry |
| `strategy.json` | unconditional; `None` contract aborts save (ml_training_tab.py:1758-1767) | the serving manifest — see below |

TL serving gate (what the bundle must satisfy): required files are exactly `model.cbm` + `metadata.json` + `strategy.json` (`MODEL_FILE`/`METADATA_FILE`/`STRATEGY_FILE`/`CHECKSUM_FILE`, TL/backend/src/trade_lab/services/model_registry.py:139-142; discovery requires all three at 233-235; activation re-checks `model.cbm` + `strategy.json` at 510-511). The contract is loaded with `expected_platform_version=PLATFORM_VERSION` and `validate_section_via_registry=True` (TL model_registry.py:245-249, 522-526), so the bundle must carry `contract_version == "trade_lab_contract_v3"` and `platform_version == "strategy_core_platform_v1"` (SC/src/strategy_core/contract/loader.py:80-92) and a `section` the registered plugin's `SectionModel` accepts (loader.py:99-113). The strategy binding then requires: `strategy_id` resolvable via `get_strategy`, `strategy_version` equal to the registered plugin's, and `supported_by_runtime is True` (TL model_registry.py:287-297). Bundle directory name is the model id and must match `^[A-Za-z0-9_.:-]+$`, non-symlink, direct child of the models root (TL model_registry.py:144-146, 164-174, 504-511); at most 256 bundles are scanned (146).

Doc-numbering note: this section's items were resolved by description. "Decision 9.1/9.2/9.3" labels do appear in SC docstrings referencing a PLAN document (registry assertions/registry design/two-axis versioning — SC/src/strategy_core/strategies/registry.py:1, 17; SC/src/strategy_core/contract/loader.py:14; SC/src/strategy_core/contract/schema.py:275); no QL-side copy of that numbered document was located under QL top-level `*.md` or `scripts/`.

---

## 5. TL serving integration

#### (a) The activation gate in `model_registry.py`

The "4-check gate" is the **E2 strategy-axis router gate**, enumerated verbatim in the code comment at TL/backend/src/trade_lab/services/model_registry.py:437-439: `"(i) strategy_id resolves in the SC registry, (ii) strategy_version equals the registered plugin's, (iii) the bundle is servable, and (iv) the contract routes to the strategy this service actually runs."` Checks (i)-(iii) live in `_strategy_binding_error(contract)` (TL/backend/src/trade_lab/services/model_registry.py:276-298); check (iv) is inline in `prepare_activation` (TL/backend/src/trade_lab/services/model_registry.py:443-450). The gate runs at BOTH discovery (TL/backend/src/trade_lab/services/model_registry.py:256-259, skip-with-warning) and activation (raise `ModelValidationError`, fail closed).

| # | Check | Exact condition | Cite |
|---|-------|-----------------|------|
| (i) | strategy_id routable | `plugin_cls = get_strategy(contract.strategy_id)` — `except ContractError as exc: return str(exc)`; SC's `get_strategy` raises `ContractError(f"unknown strategy_id {strategy_id!r}; registered: {sorted(_REGISTRY)}")` | TL/backend/src/trade_lab/services/model_registry.py:286-289; SC/src/strategy_core/strategies/registry.py:98-103 |
| (ii) | strategy_version equality | `if plugin_cls.strategy_version != contract.strategy_version:` → `"strategy_version mismatch for {contract.strategy_id!r}: contract declares {contract.strategy_version!r} but the registered plugin is {plugin_cls.strategy_version!r}"` | TL/backend/src/trade_lab/services/model_registry.py:290-295 |
| (iii) | servability flag | `if contract.supported_by_runtime is not True:` → `"contract declares supported_by_runtime=false; the bundle is not servable"` | TL/backend/src/trade_lab/services/model_registry.py:296-297 |
| (iv) | serving-id equality | `if (self._serving_strategy_id is not None and contract.strategy_id != self._serving_strategy_id):` → `raise ModelValidationError(f"contract strategy_id {contract.strategy_id!r} does not match the running service's plugin {self._serving_strategy_id!r}")` | TL/backend/src/trade_lab/services/model_registry.py:443-450 |

The E2 gate is only one stage of the full `prepare_activation` chain (TL/backend/src/trade_lab/services/model_registry.py:425-482), which in order runs: `is_safe_model_id` (:432) → `_resolve_bundle_dir` symlink/traversal guard (:434, :496-514) → `_load_contract` via SC loader with `expected_platform_version=PLATFORM_VERSION` + `validate_section_via_registry=True` (:435, :516-528) → E2 gate (i)-(iv) (:440-450) → `section = contract.section_model` + `validate_feature_partition(contract.feature_set.names, section)` (:454-458) → the **W1 P3d serving-compatibility gate** `serving_compatibility_error(contract, section, capabilities)` (:461-466) → fail-closed metadata `selected_features` cross-check (:469-473) → `_load_and_validate_model` = sha256 sidecar check (:539-567, absent sidecar is debug-logged and allowed :544-550), CatBoost load (:569-581), and model-vs-contract checks: `model.feature_names_` name+order equality (:587-591), class-count equality (:592-595), tick-size metadata equality (:597-599).

The W1 P3d gate itself is an 8-condition fail-closed function `serving_compatibility_error` (TL/backend/src/trade_lab/services/model_registry.py:65-137): unknown feature names vs `capabilities.computable_features` (:79-84); `windows.approach_window_minutes + windows.interaction_window_minutes > market_context_retention_minutes` (:85-92); `contract.instrument != capabilities.instrument_root` (:93-97); `policy.decision_offset_minutes * 60 != capabilities.observation_duration_seconds` (:98-104); `policy.barrier_mode != "fixed_points"` → `"serving implements fixed_points semantics only"` (:105-109); `section.session_scheme != default_touch_reversal_section().session_scheme` (:110-114); `parse_bar_type(section.touch_rule.bar_type) != capabilities.decision_timeframe_ticks` (:115-123); live/replay schemas ⊆ supported sets (:124-136). Regression coverage: TL/backend/tests/test_activation_gate_w1.py (file exists; not quoted here) and TL/backend/tests/test_platform_version_binding.py:109-165.

#### (b) `serving_strategy_id` — origin and every read site

It comes from **neither config nor the bundle manifest nor the contract**: it is derived from the runtime's hardwired plugin instance. TL/backend/src/trade_lab/config.py contains no strategy-id setting (the only "strategy" hit is a comment naming `strategy.json` at TL/backend/src/trade_lab/config.py:33 — absence finding).

Chain of custody:
1. `StrategyCoreService.__init__` constructs `StrategyRuntime(..., **touch_reversal_kwargs())` (TL/backend/src/trade_lab/services/strategy_core_service.py:100-116, the spread at :115). `touch_reversal_kwargs()` "UNCONDITIONALLY builds the registered ``touch_reversal`` plugin": `{"plugin": get_strategy("touch_reversal")(), "strategy_section": default_touch_reversal_section()}` (SC/src/strategy_core/runtime/wiring.py:27-35).
2. Exposed as `plugin_strategy_id` → `return self._runtime._plugin.strategy_id` (TL/backend/src/trade_lab/services/strategy_core_service.py:126-129; note the private `_plugin` reach-in).
3. Injected once at app construction: `ModelRegistry(settings.models_path, serving_strategy_id=runtime.strategy_core_service.plugin_strategy_id, ...)` (TL/backend/src/trade_lab/api/app.py:253-257).
4. Read sites: constructor param (TL/backend/src/trade_lab/services/model_registry.py:378), stored (:387), and the single guard read (:443-450). No other read exists (grep over `backend/src/trade_lab` confirms).

Related-but-distinct: `contract.strategy_id` (from the bundle's `strategy.json`) is surfaced read-only on `ModelBundle` (TL/backend/src/trade_lab/services/model_registry.py:265), `ModelStatus` (TL/backend/src/trade_lab/services/runtime.py:512), and the DTOs `ModelStatusDTO.strategy_id` / `ModelBundleDTO.strategy_id` (TL/backend/src/trade_lab/api/dto.py:147, :160, :347, :360).

#### (c) Contract section reads — loader, envelope vs strategy section

Loader: TL loads contracts exclusively through SC's `load_strategy_contract` (imported at TL/backend/src/trade_lab/services/model_registry.py:30), at two entries — discovery (:245-249) and activation (:522-526) — both with `expected_platform_version=PLATFORM_VERSION` and `validate_section_via_registry=True`. With the hook on, the SC loader resolves `get_strategy(contract.strategy_id)` and types the raw `section` mapping via `plugin_cls.SectionModel.model_validate(...)`, attaching it as `contract._section_model` (SC/src/strategy_core/contract/loader.py:99-113). So section **typing** is keyed by strategy_id; TL's section **reads** are duck-typed touch-section attribute access, explicitly recorded as coupling: "the feature-partition cross-check helper lives on the touch section... the F-era cleanup moves this into the plugin" (TL/backend/src/trade_lab/services/model_registry.py:33-39).

| Contract read | Envelope or section | Keyed by strategy_id vs hardcoded | Cites |
|---|---|---|---|
| `feature_set.names` | envelope | fixed field name | model_registry.py:80, :321, :456, :588; feature_functions.py:332 |
| `instrument`, `training_mode`, `feature_count`, `tick_size`, `class_map` | envelope | fixed field names | model_registry.py:93, :266-269, :593, :598; runtime.py:418, :513-516; inference_engine.py:171, :243; feature_functions.py:102 |
| `label_policy` (`decision_offset_minutes`, `barrier_mode`, `forward_bar_type`, `tp_points`, `sl_points`, `trap_mfe_min`) | envelope | fixed field names | model_registry.py:98-109; runtime.py:407-425 |
| `inference` (`confidence_gate`, `eligible_class`, `eligible_session`) | envelope | fixed field names | inference_engine.py:201-205 |
| `data_requirements` (`live_schemas`, `replay_schemas`) | envelope | fixed field names | model_registry.py:124-136 |
| `strategy_id`, `strategy_version`, `supported_by_runtime`, `platform_version` | envelope | routing axis (registry-keyed) | model_registry.py:287-297; SC loader.py:86-92 |
| `section.feature_windows` (`approach/interaction_window_minutes`, `level_proximity_pts`, `large_trade_threshold`, `within_band_pts`) | section | **duck-typed touch-section attributes** | model_registry.py:85-86; runtime.py:384-389; feature_functions.py:98-105, :323-328 |
| `section.touch_rule.bar_type` | section | duck-typed touch attribute | model_registry.py:116 |
| `section.touch_rule.direction_from_side` | section | duck-typed touch attribute | inference_engine.py:81 |
| `section.session_scheme` | section | compared against a **hardcoded touch_reversal import**: `default_touch_reversal_section().session_scheme` | model_registry.py:110, import :36-39 |
| feature partition (`approach_features`/`interaction_features`) | section | validated via `validate_feature_partition` imported **from `strategy_core.strategies.touch_reversal.section`** | model_registry.py:36-39, :456 |

The envelope field set is defined at SC/src/strategy_core/contract/schema.py:294-312 (`section: Mapping[str, Any]` at :312 is the only strategy-owned subtree). TL also imports the touch plugin module itself for registration side effects: `import strategy_core.strategies.touch_reversal  # noqa: F401` (TL/backend/src/trade_lab/services/model_registry.py:29) — the only strategy TL's process ever registers.

#### (d) The inference path — engine events → features → decision

Path, in hot-path order (all inside `ApplicationRuntime._process_trade`, TL/backend/src/trade_lab/services/runtime.py:843-870):
1. **Event → SC**: trades/quotes convert to SC neutral events and run through `StrategyRuntime.process_event` (TL/backend/src/trade_lab/services/strategy_core_service.py:160-171); the SC plugin emits `Touch`es.
2. **Touch mapping**: `_touch_to_trade_lab` builds a TL `TouchEvent` (strategy_core_service.py:251-283) — SC `Touch.direction` maps by a fixed LONG/SHORT table (:49-52, :279), `level_kind=_level_kind(touch.level_type) or LevelKind.PDH` (:268; unknown SC level names elsewhere are dropped by `_level_kind` returning None, :360-364), and the exact zone `level_price` is carried (:280-283).
3. **Touch → Observation**: each touch starts a fixed-duration observation, `self.observations.start_from_touch(touch)` (runtime.py:854-855; TL/backend/src/trade_lab/domain/observations.py:50-65), carrying `level_kind`, `level_price_ticks`, `direction`, `level_price` (observations.py:32-40).
4. **Observation expiry → predict**: `_run_inference` fires only for `ObservationStatus.EXPIRED` (runtime.py:555-556) and calls `engine.predict_for_observation(observation, self.market_context)` (runtime.py:559).
5. **Features**: `InferenceEngine._predict` (TL/backend/src/trade_lab/services/inference/inference_engine.py:154-229) builds `LevelContext.from_contract(...)` (:183-185; section bands read at feature_functions.py:98-105) and `build_feature_vector(...)` in strict `feature_set.names` order (:186-193; feature_functions.py:330-341), windows derived from `section.feature_windows` around the touch timestamp (feature_functions.py:320-328).
6. **Decision**: `active.model.predict_proba([ordered])[0]` (:196), class-map label alignment (:231-248), eligibility = `predicted_class == eligible_class and _session_matches(...) and probabilities >= confidence_gate` (:200-207).
7. **Outcome**: predictions register with the SC `StreamingHonestResolver` off the touch anchors + direction (runtime.py:438-477, direction passed at :466), advance on forward-bar closes (:592-641), and resolutions map to `Outcome` (TL/backend/src/trade_lab/services/inference/resolution_adapter.py:44-72).

Parts of that path that assume **touch_reversal semantics**, with cites:
- The runtime plugin is hardwired: `**touch_reversal_kwargs()` (strategy_core_service.py:115; SC wiring.py:33).
- `LevelKind` is a closed 8-member enum of touch levels (pdh/pdl/asia/london/ny high-low) (TL/backend/src/trade_lab/domain/levels.py:17-25); non-matching SC level names are unmappable (strategy_core_service.py:344, :360-364) and touch `level_kind` silently defaults to `PDH` (:268).
- Side→direction: `_level_side` hardcodes `"pdh"/"*_high"→"high"`, `"pdl"/"*_low"→"low"` (inference_engine.py:62-74) and `_direction_from_section` reads `section.touch_rule.direction_from_side` (:77-84) — fallback only; the primary direction is the SC touch's zone-side direction (:179-182, observations.py:37).
- Zone/window params (`within_band_pts`, `level_proximity_pts`, `large_trade_threshold`, approach/interaction minutes) come from the touch section (feature_functions.py:98-105; inference_engine.py:161-164; the "duck-typed touch-section access; recorded coupling" wording sits in the docstrings at feature_functions.py:93-94 and inference_engine.py:78-79).
- The feature registry is a fixed six-touch-feature table (`int_time_beyond_level` ... `app_max_spread`) (feature_functions.py:288-297), and `ServingCapabilities.computable_features = DEFAULT_FEATURE_REGISTRY.names` (app.py:261) — the capability gate can only ever admit those six names.
- The outcome adapter hardcodes the three touch class labels `tradeable_reversal/aggressive_blowthrough/trap_reversal` → TP/SL and raises `ValueError(f"unmapped resolution label {label!r}")` on anything else (resolution_adapter.py:28-32, :54-58); the resolver build assumes `forward_bar_type` of shape `"<n>t"` (`parse_bar_type`, resolution_adapter.py:35-41) plus `tp_points/sl_points/trap_mfe_min` fixed-points fields (runtime.py:416-425).
- API edge: `TouchDTO`/`ObservationDTO`/`PredictionDTO` bake in `level_kind`/`level_price_ticks` fields, and `PredictionDTO` additionally a `direction` field (TL/backend/src/trade_lab/api/dto.py:74-99, :101-116, direction at :111; `TouchDTO`/`ObservationDTO` carry no direction field), and the WS vocabulary includes `"touch.detected"`/`"observation.updated"` (dto.py:25-40).

#### (e) Would a second strategy's bundle activate today with zero TL changes? — NO (two independent hard stops)

Walking `POST /api/v1/models/activate` (TL/backend/src/trade_lab/api/app.py:417-455) for a bundle whose `strategy.json` declares `strategy_id != "touch_reversal"`:

1. `is_safe_model_id` (app.py:423-424) — **passes** (id shape only).
2. `registry.prepare_activation(model_id)` (app.py:431) → `_resolve_bundle_dir` (model_registry.py:434) — **passes** (filesystem only).
3. `_load_contract` with `validate_section_via_registry=True` (model_registry.py:522-526) → SC loader calls `get_strategy(contract.strategy_id)` (SC/src/strategy_core/contract/loader.py:104-106). TL's process registers **only** `touch_reversal` (the imports at model_registry.py:29 and SC wiring.py:21). For any other id, `get_strategy` raises `ContractError("unknown strategy_id {id!r}; registered: [...]")` (SC/src/strategy_core/strategies/registry.py:98-103) → **FAIL STOP #1**: `ModelValidationError(f"invalid strategy contract: {exc}")` (model_registry.py:527-528) → HTTP 409 (app.py:434-436). The same failure at discovery means the bundle never even lists in `GET /api/v1/models` (model_registry.py:250-259).
4. Hypothetically, if the plugin were registered in the process (an SC+import change, i.e., not "zero TL changes"): E2 checks (i)-(iii) could pass, but check (iv) fires unconditionally — `contract.strategy_id != self._serving_strategy_id` where `serving_strategy_id` is always supplied by the app as the hardwired touch plugin's id (app.py:257) → **FAIL STOP #2**: `"contract strategy_id {…!r} does not match the running service's plugin 'touch_reversal'"` (model_registry.py:443-450).
5. Even past that, three further gates assume the touch shape: `validate_feature_partition` imported from the touch plugin's section module (model_registry.py:36-39, :456); `serving_compatibility_error`'s duck-typed `section.feature_windows` / `section.touch_rule.bar_type` reads (:85, :116 — `AttributeError` for a section lacking them), its equality against `default_touch_reversal_section().session_scheme` (:110-114), and `capabilities.computable_features` fixed to the six touch feature names (:79-84; app.py:261).

Binding assumptions confirmed in tests: unknown `strategy_id` rejected at discovery AND activation with `match="unknown strategy_id"` (TL/backend/tests/test_platform_version_binding.py:112-124); `strategy_version` mismatch (:127-139); `supported_by_runtime=False` (:142-154); the serving-id guard rejecting "foreign routing" even for a registry-valid contract (:157-165); the section hook + partition cross-check (:171-198). `test_strategy_contract.py` pins the fixture to touch specifics — `section.touch_rule.bar_type == "147t"`, 5/30-minute windows, the three-class map, the six feature names (TL/backend/tests/test_strategy_contract.py:51-84). `api/dto.py` and `services/runtime.py` carry `strategy_id` only as a passthrough display field (dto.py:147, :160; runtime.py:105, :512) — no additional guards there; `api/app.py`'s only binding is the constructor injection at app.py:257.

Doc-number note: the numbered items cited in code docstrings resolve to SC/docs/PLATFORM_REFACTOR_PLAN.md §9 "Decisions needed from the owner" (SC/docs/PLATFORM_REFACTOR_PLAN.md:1353) — 9.1 plugin-interface/registry-time assertions (:1359; referenced at SC/src/strategy_core/strategies/registry.py:17-22), 9.2 registry mechanism (:1384; registry.py:1), 9.3 section versioning (:1395; referenced at SC/src/strategy_core/contract/loader.py:14), 9.8 TL outcome-tracker retirement (:1472), 9.10 quote handling (:1496).

---

## 6. Event-type candidate surfaces (facts only)

#### Grounding: the shared surfaces every candidate maps onto

**Doc-number resolution.** The numbered items resolve to SC/docs/PLATFORM_REFACTOR_PLAN.md §9.1–§9.10 (SC/docs/PLATFORM_REFACTOR_PLAN.md:1359, 1416, 1472, 1483, 1496). Item **9.8** is the Barrier decision — "Retire TL's local outcome tracker onto the engine's honest-entry path?" (SC/docs/PLATFORM_REFACTOR_PLAN.md:1472) — ratified as "Option A (Barrier abstraction + retire TL outcome tracker…)" (SC/docs/PLATFORM_REFACTOR_PROGRESS.md:47; SC/docs/DECISIONS.md:24).

**The 9.8 Barrier protocol, verbatim** (SC/src/strategy_core/strategies/protocols.py:138-152):

```python
class Barrier(Protocol):
    """A per-setup stop/target (PLAN §2.2). ``kind`` is ``"fixed_points"`` or ``"r_relative"``. ..."""
    kind: str
    def stop_price(self, entry_price: float, direction: str) -> float: ...
    def target_price(self, entry_price: float, direction: str) -> float: ...
```

Its fields are exactly `kind` + two functions of `(entry_price, direction)`; it carries **no timeout, level-price, or window fields**. Timeout geometry lives outside the Barrier: `LabelPolicySpec` carries `decision_offset_minutes` / `flatten_time` / `forward_cutoff` / `no_resolution_dropped` (SC/src/strategy_core/strategies/protocols.py:208-223), and the kernel timeout is the forced classification at the ET wall-clock cutoff (`classify_mae_first(..., forced=True)` SC/src/strategy_core/decisions/outcomes.py:102-104; cutoff anchored as `datetime.combine(touch.trading_day, rth_end, tzinfo=tz)` SC/src/strategy_core/decisions/honest_entry.py:132; streaming finalize at cutoff SC/src/strategy_core/decisions/streaming.py:252-253, flush 304-321). The only implementation is `FixedPointsBarrier` (SC/src/strategy_core/strategies/touch_reversal/plugin.py:126-150; defaults tp=15/sl=30/trap=5 from SC/src/strategy_core/constants.py:137-139). The contract types `barrier_mode: Literal["fixed_points", "r_relative"] = "fixed_points"` with the in-source note that `r_relative` is "declared for forward compatibility, no producer emits it yet" (SC/src/strategy_core/contract/schema.py:180-185), and **TL activation fail-closes on anything but fixed_points**: `if policy.barrier_mode != "fixed_points": return "unsupported barrier_mode …: serving implements fixed_points semantics only"` (TL/backend/src/trade_lab/services/model_registry.py:105-109).

**Shared seam facts used below (each cited once here):**

| Fact | Citation |
|---|---|
| Plugin-defined levels are a first-class seam: the plugin owns the SOLE level fold; `on_event(event, ctx) -> tuple[Level, ...]` returns the level set the platform maps verbatim onto `RuntimeUpdate.levels` | SC/src/strategy_core/strategies/protocols.py:287-294; SC/src/strategy_core/runtime/state.py:348-351 |
| `Level` is a free-name dataclass (`name: str, price, side, available_from`); no code constrains names to `SESSION_LEVELS` (a contract *descriptor* tuple), and the section's `level_scheme` fields are "descriptive and do not affect the plugin's touch output" | SC/src/strategy_core/types.py:124-139; SC/src/strategy_core/constants.py:216-223; SC/src/strategy_core/strategies/touch_reversal/section.py:166-169 |
| Event definition is plugin-internal behind `on_bar_closed(bar, ctx) -> StrategyStep`; the platform gates which bars reach it (`bar.timeframe_ticks == decision_timeframe` only) | SC/src/strategy_core/strategies/protocols.py:297-304; SC/src/strategy_core/runtime/state.py:353-358 |
| The platform consumes ONLY `step.touches` from a `StrategyStep` — `setups`/`decisions`/`features` have no reader in SC src (sole call-site state.py:358, sole read line 359) and zero references in TL backend; TL's serving pipeline starts observations from `core_update.touches` | SC/src/strategy_core/runtime/state.py:358-359; TL/backend/src/trade_lab/services/runtime.py:854-855 |
| The engine's only detection predicate is the closed-interval straddle `is_touch` (`bar_low <= rep <= bar_high`); no close-beyond/"break" predicate exists in `decisions/` (grep for breakout/opening_range/retest over SC src: zero hits) | SC/src/strategy_core/decisions/touch.py:42-52 |
| The v3 availability gate makes the recorded touch "the first qualifying RETURN to the level once it exists, never the forming bar" | SC/src/strategy_core/decisions/touch.py:80-86, 98-101 |
| `direction_from_side` is an injectable parameter of `detect_touches` (default LOW→LONG / HIGH→SHORT) and contract data (`TouchRule.direction_from_side: dict[str, str]`) | SC/src/strategy_core/decisions/touch.py:36-39, 55-62; SC/src/strategy_core/contract/schema.py:148 |
| Bars are TICK-only; `BarKind.TIME` is declaration-only ("TIME bars are not yet buildable by the engine (decision 9.4 / Phase F)"); `CloseReason` has only COMPLETE/END_OF_DAY | SC/src/strategy_core/strategies/protocols.py:23-25, 61-66; SC/src/strategy_core/types.py:51-56; SC/docs/PLATFORM_REFACTOR_PLAN.md:1416-1432 |
| Batch label resolver is Touch-typed (`resolve_honest_outcome(touch: Touch, …)`); the streaming resolver is generic (`register(self, key: object, *, touch_bar_ts_utc, trading_day, direction)`) | SC/src/strategy_core/decisions/honest_entry.py:76-89; SC/src/strategy_core/decisions/streaming.py:194-201 |
| Contract pins `decision_offset_minutes: int = Field(gt=0, le=1440)` — a zero offset (decide on the event bar itself) is not representable; TL activation additionally requires offset == the configured observation window | SC/src/strategy_core/contract/schema.py:187; TL/backend/src/trade_lab/services/model_registry.py:98-104 |
| Registry supports multiple strategy ids (`@register` + fail-closed `get_strategy`); TL activation routes `get_strategy(contract.strategy_id)` + equality-checks `strategy_version`, but a running service pins ONE serving plugin (`contract.strategy_id != self._serving_strategy_id` → reject) and requires `section.session_scheme ==` the default touch section's scheme and `touch_rule.bar_type ==` the runtime decision timeframe | SC/src/strategy_core/strategies/registry.py:38-103; TL/backend/src/trade_lab/services/model_registry.py:287, 290, 444-451, 110-123 |
| Six engine features: 3 interaction formulas take `(trades, level_points, tick_size)` — two of the three also take `direction`; `int_time_within_2pts` does not; 3 approach formulas take only a trade/quote window; windows are derived from the event timestamp (interaction `[t, t+5m)`, approach `[t−90m, t)`) | SC/src/strategy_core/decisions/features.py:68-76, 103-110, 130-137, 168-216; TL/backend/src/trade_lab/services/inference/features/feature_functions.py:109-136 |
| `PlatformContext` gives plugins `session_at`, `trade_price_at`, `closed_bars`/`current_bar`, and `quotes_in_window`; the quote accessor is a stub returning `()` in the runtime implementation (§9.10 buffer open) | SC/src/strategy_core/strategies/protocols.py:85-119; SC/src/strategy_core/runtime/context.py:103-109 |
| QL's emitter stamps the label policy from the registered plugin + engine constants (`barrier_mode` via `get_strategy(strategy_id).label_policy()`) | QL/src/alpha_lab/agents/data_infra/ml/strategy_contract.py:216-227 |

#### (a) Opening-range breakout — session-open high/low over the first N minutes, entry on break

**(i) LEVEL SOURCE — not computed.** `StrategyLevelState` hardcodes range trackers for exactly two sessions: `self._ranges = {"asia": _Range(), "london": _Range()}` (SC/src/strategy_core/runtime/levels.py:49; the hardcode is the recorded §9.9 fact, SC/docs/PLATFORM_REFACTOR_PLAN.md:1485-1489). There is no "ny" range and no first-N-minutes sub-window concept anywhere in SC src (grep: zero hits). The intraday running day high/low IS tracked per trade (SC/src/strategy_core/runtime/levels.py:86-87) but is only banked at the day roll into next-day pdh/pdl (levels.py:71-81) — it is never emitted as an intraday level. Plugin-defined levels ARE supported via the `on_event` level-fold seam (protocols.py:287-294 → state.py:348-351): a plugin sees every `Trade` and can emit e.g. `Level("or_high", …)` — `Level.name` is unconstrained (types.py:136-139) and `build_zones` is generic over any `Level` list (SC/src/strategy_core/decisions/zones.py:23-26). The plugin can window trades itself: `classify_session` returns `SessionInfo(trading_day, session, local_ts)` (SC/src/strategy_core/decisions/sessions.py:71-109; SC/src/strategy_core/types.py:215-227) and `ctx.session_at` is on the context (protocols.py:111-113). A TIME bar for "first N minutes" is not buildable (BarKind.TIME declaration-only, protocols.py:61-66).

**(ii) EVENT DEFINITION SEAM.** The carrying member is `on_bar_closed` (protocols.py:297-304), invoked only for decision-timeframe tick bars (state.py:353-358) — the seam itself is generic (the touch plugin's `build_zones → detect_touches` body is plugin-internal, plugin.py:265-295). The current detection function is touch-only: `is_touch` fires when a bar's range *straddles* the level (touch.py:42-52) — it fires whether the bar closes beyond or rejects; a close-beyond "break" predicate does not exist in the engine. Break direction (HIGH break → LONG) is expressible as data: `detect_touches(..., direction_from_side=...)` (touch.py:55-62) and `TouchRule.direction_from_side: dict[str, str]` (schema.py:148). Downstream, only `step.touches` reaches the platform (state.py:359) and TL starts observations only from touches (TL runtime.py:854-855); `DecisionEvent`/`SetupState` are protocol vocabulary without a platform reader.

**(iii) LABEL GEOMETRY FIT.** Direction: `Barrier.stop_price/target_price` take `(entry_price, direction)` (protocols.py:148-152) — long/short both expressible. Entry-relative fixed TP/SL: `FixedPointsBarrier` as-is (plugin.py:126-150). A stop at the *opposite OR bound* is an absolute price, not a function of `(entry_price, direction)`; the protocol allows per-setup barrier instances (`DecisionEvent.barrier` per decision, protocols.py:155-167; "per-setup barriers", protocols.py:26-27), but the Barrier fields themselves carry no level price. Timeout: only the daily ET wall-clock cutoff/flatten pair exists (parameters `rth_end`/`flatten_time`, honest_entry.py:85-88 and streaming.py:160-163; forced arm outcomes.py:102-104) — no bar-count or duration timeout parameter. Label vocabulary is the fixed 3-class MAE-first ladder (outcomes.py:94-106; class names constants.py:122-131). Entry timing: `decision_ts = event bar close + decision_offset_minutes` with contract bound `gt=0` (honest_entry.py:127; schema.py:187) — an "on break" (offset-0) entry is outside the contract field's range, and TL activation requires the offset to equal the observation window (model_registry.py:98-104).

**(iv) FEATURE AVAILABILITY.** All six features are computable at an OR-break decision given `(event_ts, level_points, direction)`: the 3 interaction formulas are level-anchored pure functions over the post-event trade window (two of the three also take `direction`; `int_time_within_2pts` does not) (features.py:68-76, 103-110, 130-137), the 3 approach formulas need only the pre-event window (features.py:168-216). None takes a `Touch` object — the touch-specificity is in the serving wiring, which anchors windows on the observation start (= touch bar close, TL inference_engine.py:186-193) and takes the reference price from the observation's level (inference_engine.py:166-172). `app_max_spread`'s live quotes come from TL's `MarketContextBuffer` (feature_functions.py:240-248), not from SC's stub `quotes_in_window` (context.py:103-109).

| Served by existing surface (citation) | Plugin must build new |
|---|---|
| Level delta seam: `on_event` → `RuntimeUpdate.levels` verbatim (protocols.py:287-294; state.py:348-351); free `Level.name` (types.py:136-139) | OR high/low computation over the first N minutes of a session — no "ny" range tracker (levels.py:49), no time-window bar (protocols.py:61-66) |
| Zone merge + availability-gated detection over any level list (zones.py:23-26, 93-101; touch.py:98-106) | A close-beyond "break" predicate — only the straddle `is_touch` exists (touch.py:42-52) |
| Break direction as data: injectable `direction_from_side` (touch.py:55-62; schema.py:148) | — |
| Entry-relative fixed TP/SL (`FixedPointsBarrier`, plugin.py:126-150) + MAE-first kernel (outcomes.py:127-215) + generic-key streaming resolver (streaming.py:194-201) | An opposite-OR-bound (absolute-price) stop — not expressible through Barrier's `(entry_price, direction)` fields (protocols.py:146-152); any non-wall-clock timeout (outcomes.py:102-104; honest_entry.py:132) |
| All six feature formulas given `(ts, level, direction)` (features.py:68-216; feature_functions.py:109-136) | Any new feature name — the TL registry fail-closes on unknown names (feature_functions.py:251-277; model_registry.py:79-84) |
| ws fan-out declaration for new event types (`EventTypeSpec`, protocols.py:226-236) | Platform/TL consumption of a non-touch event stream — only `step.touches` is read (state.py:358-359; TL runtime.py:854-855) |

#### (b) Session-extreme continuation — break of asia/london high/low with retest

**(i) LEVEL SOURCE — already computed.** `asia_high/asia_low/london_high/london_low` are produced by the platform-owned level state (SC/src/strategy_core/runtime/levels.py:101-107) with `available_from` = the defining session's close (levels.py:119-125; documented instants: Asia close 02:45 ET, London close 08:00 ET — SC/src/strategy_core/constants.py:225-233), and the availability gate is enforced at detection (touch.py:98-101; merged-zone max at zones.py:93-94). The session set is hardcoded to those two names (levels.py:49).

**(ii) EVENT DEFINITION SEAM.** Same `on_bar_closed` seam and bar gate as (a). The **retest half** of the event is exactly what the current detector records: the availability gate makes the first straddle "the first qualifying RETURN to the level once it exists" (touch.py:80-86), deduped once-per-zone-per-day via the plugin-owned fired-keys set (plugin.py:283-289; SC/src/strategy_core/decisions/dedup.py:24-30). The **break half** has no detector (no close-beyond predicate; see grounding), and a break→retest two-phase sequence has no engine state machine: `SetupState` declares the multi-phase vocabulary (`phase: str  # e.g. "scanning"|"htf_tapped"|"parent_locked"|"armed"|"invalidated"`, protocols.py:123-135) but no platform reader consumes setups (state.py:358-359). Note the current detector cannot distinguish "retest after break" from "plain first touch" — `is_touch` fires on the first post-availability straddle regardless of an intervening break (touch.py:94-106).

**(iii) LABEL GEOMETRY FIT.** Continuation direction (HIGH break → LONG) is data-expressible via an inverted `direction_from_side` (touch.py:55-62; schema.py:148; the map is plugin-owned wire vocabulary per constants.py:64-67). Fixed entry-relative TP/SL, MAE-first ladder, wall-clock cutoff/flatten: identical fit to (a) (plugin.py:126-150; outcomes.py:94-106; honest_entry.py:132-145). An R-relative geometry (SL = the broken swing/extreme, TP = 1R) is what `r_relative` names — "``r_relative`` computes ``SL = swing level`` and ``TP = entry ± 1R``" (protocols.py:139-144) — but no producer emits it (schema.py:181-185) and TL serving rejects it at activation (model_registry.py:105-109).

**(iv) FEATURE AVAILABILITY.** At a retest event the anchor triple `(event_ts, session-extreme level price, direction)` exists, so all 3 interaction + 3 approach formulas are defined exactly as in production (features.py:68-216); windows derive from the event timestamp (feature_functions.py:123-136). Nothing in the formulas encodes "reversal" — direction is a parameter — but no feature describes the *break* itself (e.g. break magnitude/elapsed-since-break); the implemented menu is the six names in `DEFAULT_FEATURE_REGISTRY` (feature_functions.py:288-297) mirroring `RUNTIME_APPROACH_FEATURES` + `INTERACTION_FEATURES` (constants.py:97-119).

| Served by existing surface (citation) | Plugin must build new |
|---|---|
| Session-extreme levels computed + availability-stamped by the platform (levels.py:101-107, 119-125) | Nothing for the levels themselves; a *different* session set would hit the asia/london hardcode (levels.py:49; PLAN §9.9, PLATFORM_REFACTOR_PLAN.md:1483-1492) |
| Retest-as-first-return semantics (availability gate, touch.py:80-86, 98-101) + per-zone-per-day dedup (plugin.py:283-289; dedup.py:24-30) | Break detection (no close-beyond predicate) and break→retest sequencing state (SetupState vocabulary exists, protocols.py:123-135, but is platform-unread, state.py:358-359) |
| Continuation direction as data (`direction_from_side`, touch.py:55-62; schema.py:148) | — |
| Fixed-points barrier + MAE-first kernel + streaming resolver (plugin.py:126-150; outcomes.py:127-215; streaming.py:194-201) | An R-relative barrier producer + serving support — `r_relative` is contract-declared but unproduced (schema.py:181-185) and activation-rejected (model_registry.py:105-109) |
| Six features defined at the retest anchor (features.py:68-216) | Any break-describing feature (not in the registry, feature_functions.py:288-297) |

#### (c) Prior-day-range fade at open — fade at PDH/PDL touched near the NY open

**(i) LEVEL SOURCE — already computed.** PDH/PDL are emitted as full-prior-day extremes (`PDH_PDL_SOURCE = "prior_day_full"`, constants.py:213; emission levels.py:96-100), available from the trading-day start (prior 18:00 ET; levels.py:113-117; constants.py:225-233). They are seeded externally through the sole lifecycle seam `load_prior_day_summary` (protocols.py:270-272; runtime write-through state.py:274-276) or banked organically at the day roll (levels.py:71-81).

**(ii) EVENT DEFINITION SEAM.** This candidate's event is the existing first-touch event restricted to `pdh`/`pdl` level types plus a time-of-day gate. `Touch.level_type` is carried (types.py:164-172) and equals `zone.names[0]` (touch.py:112) — with the caveat that `build_zones` merges ALL current levels within 3.0 pts (plugin.py:271-281 "merge-all"; chained merge zones.py:70-74; mean representative price zones.py:78-80), so a pdh near a session level becomes one mixed-name zone; a pdh/pdl-only event needs the plugin to select its level subset before zone building (the section's `level_scheme.session_levels` is descriptive only, section.py:166-169). Time gate "near the NY open": session names are generic scheme data (`sessions: Mapping[str, SessionWindow]`, types.py:203-211; contract form schema.py:128; generic iteration sessions.py:103-107), and plugins can read `ctx.session_at` (protocols.py:111-113; context.py:100-101) — but the shipped gate granularity is the whole named session: `InferencePolicy.eligible_session` (schema.py:196-201) enforced by prefix-match at TL inference (inference_engine.py:87-99, 202-207), and `"ny"` spans 09:00–17:00 ET (constants.py:174, 286). No "first-X-minutes-of-ny" field exists in the contract or the runtime; a custom named window added to a `SessionScheme` changes classification only (the level tracker still ranges only asia/london, levels.py:49), and TL activation requires the section's scheme to equal the default touch scheme byte-for-byte (model_registry.py:110-114).

**(iii) LABEL GEOMETRY FIT — expressible as-is.** The fade direction is the engine default reversal map verbatim (`Side.LOW → LONG, Side.HIGH → SHORT`, touch.py:36-39). Barrier: `FixedPointsBarrier` with tp/sl/trap (plugin.py:126-150) is the exact production geometry; `decision_ts = touch close + 5 min` (plugin.py:396; constants.py:265), flatten 16:40 ET / forward cutoff 17:00 ET (constants.py:273, 152), MAE-first + forced cutoff resolution (outcomes.py:94-106; honest_entry.py:127-176). This candidate's outcome geometry introduces no field the Barrier/LabelPolicy surface lacks.

**(iv) FEATURE AVAILABILITY — identical to production.** A PDH/PDL touch is precisely the event class the six shipped features are computed on today: the plugin's declared spec is the 3+3 partition (plugin.py:321-329; constants.py:97-119), computed at observation completion anchored on the touch bar close (inference_engine.py:186-193; feature_functions.py:123-136). A time-of-day restriction changes which events are gated eligible (inference_engine.py:202-207), not which features are defined.

| Served by existing surface (citation) | Plugin must build new |
|---|---|
| PDH/PDL levels + availability + seeding lifecycle (levels.py:96-100, 113-117; protocols.py:270-272; state.py:274-276) | — |
| Touch detection, first-touch dedup, direction map = the production fade semantics unchanged (touch.py:36-39, 94-106; plugin.py:265-295) | A pdh/pdl-only level filter — the current plugin zones ALL levels merge-all (plugin.py:271-281), and `level_type` is only the first constituent of a merged zone (touch.py:112; zones.py:70-80) |
| Session gating by name at inference (`eligible_session` schema.py:196-201; inference_engine.py:87-99, 202-207) with `"ny"` = 09:00–17:00 ET (constants.py:174) | A narrower "near the open" window — no sub-session gate field exists; a new named window is scheme data only (types.py:203-211) and TL activation pins the scheme to the default (model_registry.py:110-114) |
| Full label geometry as-is: FixedPointsBarrier + offset/flatten/cutoff + MAE-first (plugin.py:126-150, 396; constants.py:152, 265, 273; outcomes.py:94-106) | — |
| All six features at the touch anchor (plugin.py:321-329; feature_functions.py:288-297) | — |

---

## 7. Naming / versioning

#### 7(a) strategy_id / strategy_version conventions (design-doc item 9.3)

The design doc was located: `SC/docs/PLATFORM_REFACTOR_PLAN.md` — item **9.3** is "How the contract's strategy SECTION is versioned (and where the version line is drawn)" (SC/docs/PLATFORM_REFACTOR_PLAN.md:1395), ratifying the two-axis split "Option A: `platform_version` + per-plugin `strategy_version`" (SC/docs/PLATFORM_REFACTOR_PLAN.md:1404-1412). The `strategy_id` *scheme* is item 9.5(c): "a stable, registry-keyed slug distinct from the bundle directory name and from `strategy_version` … Do NOT encode the version into `strategy_id`" (SC/docs/PLATFORM_REFACTOR_PLAN.md:1444).

| Stamp | Defined | Value / format | Semantics | Checked where |
|---|---|---|---|---|
| `strategy_id` | Protocol identity attr `strategy_id: str` (SC/src/strategy_core/strategies/protocols.py:252); the one concrete value `strategy_id = "touch_reversal"` (SC/src/strategy_core/strategies/touch_reversal/plugin.py:199) | Contract field `str`, 1–256 chars (SC/src/strategy_core/contract/schema.py:296). Code enforces only "non-empty str" at register (SC/src/strategy_core/strategies/registry.py:58-61); the lowercase-slug convention is doc-level only (PLAN:1444-1446) | "the REGISTRY ROUTER KEY — it must resolve via `strategies.registry.get_strategy` (it is no longer the bundle name; bundle identity stays the directory name)" (SC/src/strategy_core/contract/schema.py:278-280) | QL emission fail-closes on unknown id (QL/src/alpha_lab/agents/data_infra/ml/strategy_contract.py:144-145); TL discovery + activation resolve it via `get_strategy` (TL/backend/src/trade_lab/services/model_registry.py:287) and equality-check it against the wired plugin (TL/backend/src/trade_lab/services/model_registry.py:443-450) |
| `strategy_version` | Protocol identity attr `strategy_version: str` (SC/src/strategy_core/strategies/protocols.py:253); concrete value `strategy_version = "1"` (SC/src/strategy_core/strategies/touch_reversal/plugin.py:205) | Contract field `str`, 1–64 chars (SC/src/strategy_core/contract/schema.py:297). **Not semver**: an opaque string, manually bumped, compared only by equality | Bump policy is the comment on the attr: "Bump it when THIS strategy's semantics change (a touch-rule/label change that is not a platform mechanism); platform-wide changes bump PLATFORM_VERSION instead" (SC/src/strategy_core/strategies/touch_reversal/plugin.py:200-205) | Equality gate at TL: "strategy_version mismatch for {id!r}: contract declares … but the registered plugin is …" (TL/backend/src/trade_lab/services/model_registry.py:290-295); QL no-drift test asserts `contract["strategy_version"] == get_strategy("touch_reversal").strategy_version == "1"` (QL/tests/agents/test_strategy_contract_nodrift.py:208) |
| `PLATFORM_VERSION` | `PLATFORM_VERSION = "strategy_core_platform_v1"` (SC/src/strategy_core/__init__.py:61) | vN-suffixed label; **renamed at E1 from `ENGINE_VERSION` ("strategy_core_engine_v3")** (SC/src/strategy_core/__init__.py:30-32) | Structural platform axis: "Bump this only when a genuinely new platform mechanism is added … never for a parameter change … never for a single strategy's semantics, which is that plugin's `strategy_version`" (SC/src/strategy_core/__init__.py:12-17) | Loader equality hook `expected_platform_version` (SC/src/strategy_core/contract/loader.py:45, 86-92); TL passes `expected_platform_version=PLATFORM_VERSION` at discovery (TL model_registry.py:245-249) and activation (TL model_registry.py:522-526) |
| `CONTRACT_VERSION` | `CONTRACT_VERSION = "trade_lab_contract_v3"` (SC/src/strategy_core/__init__.py:75) | vN-suffixed label of the strategy.json *format* (schema shape) | v1→v2 added `strategy_version` + renamed `engine_version`→`platform_version`; v2→v3 split envelope/section (SC/src/strategy_core/__init__.py:63-75) | First loader check, before everything else (SC/src/strategy_core/contract/loader.py:80-84) |

**Where stamped.**
- **Contract (strategy.json)**: the QL emitter resolves `strategy_version = get_strategy(strategy_id).strategy_version` (QL/src/alpha_lab/agents/data_infra/ml/strategy_contract.py:145) and stamps `strategy_id`/`strategy_version` into both the minimal record (QL strategy_contract.py:159-160) and the full contract (QL strategy_contract.py:187-188), alongside `contract_version`/`platform_version` imported from SC "not restated" (QL strategy_contract.py:53, 157-158, 185-186). The envelope schema requires all four (SC/src/strategy_core/contract/schema.py:294-297).
- **Bundle manifest (metadata.json)**: carries **no strategy stamp**. Inspected `QL/models/NQ_W3_20260617T220752Z/metadata.json` — keys are `config, feature_importances, pipeline_config, selected_features, session_experiment, session_filter, train_metrics`; TL's metadata cross-check reads only `selected_features` (TL/backend/src/trade_lab/services/model_registry.py:317-321). The version stamps live solely in `strategy.json`.
- **Bundle identity** is the directory name, independent of `strategy_id` (SC/src/strategy_core/contract/schema.py:279-280); TL's per-prediction `contract_id` is the bundle dir name because "`contract.strategy_id` is now the registry ROUTER key (identical across bundles)" (TL/backend/src/trade_lab/services/inference/inference_engine.py:223-227).
- **Migration stamps**: `ROUTER_STRATEGY_ID = "touch_reversal"` (QL/scripts/migrate_contracts_v2.py:44); the v2 migrator renames `engine_version`→`platform_version` in place and inserts `strategy_version` resolved from the registry right after `strategy_id` (QL/scripts/migrate_contracts_v2.py:84-88), skipping bundles whose `engine_version != "strategy_core_engine_v3"` (`MIGRATABLE_ENGINE_VERSION`, QL/scripts/migrate_contracts_v2.py:48, 67).
- **TL runtime exposure**: `plugin_strategy_id` (TL/backend/src/trade_lab/services/strategy_core_service.py:126-129, reads `self._runtime._plugin.strategy_id`), `plugin_strategy_version` (TL strategy_core_service.py:131-135), `platform_version` (TL strategy_core_service.py:121-123).

**engine_version interplay.** The strategy axis is deliberately NOT checked in the SC loader — the loader checks `contract_version` then `platform_version` only (SC/src/strategy_core/contract/loader.py:80-92); the strategy axis is enforced at QL emission (strategy_contract.py:145) and at TL discovery/activation via `_strategy_binding_error` — (i) id resolves in the registry, (ii) `strategy_version` equality, (iii) `supported_by_runtime is True` (TL/backend/src/trade_lab/services/model_registry.py:276-298), plus (iv) the `serving_strategy_id` guard (TL model_registry.py:443-450). Legacy `engine_version` naming survives in QL migration/audit scripts: `migrate_contracts_v3.py:101` reads `payload.get("engine_version")`, and `scripts/v3_verify/census_v3.py:183` emits an `"engine_version": sc.PLATFORM_VERSION` key.

#### 7(b) Registry router key: type, format, unknown-id behavior

The table is `_REGISTRY: dict[str, type[StrategyPlugin]]`, "Keyed by ``plugin_cls.strategy_id``" (SC/src/strategy_core/strategies/registry.py:34-35). The key type is a plain `str`; the only registration-time format check is non-emptiness: `if not isinstance(strategy_id, str) or not strategy_id: raise ContractError(f"{plugin_cls.__name__} must declare a non-empty str strategy_id")` (SC/src/strategy_core/strategies/registry.py:57-61). Lookup signature: `def get_strategy(strategy_id: str) -> type[StrategyPlugin]:` (SC/src/strategy_core/strategies/registry.py:92). Unknown-id behavior, quoted exactly (SC/src/strategy_core/strategies/registry.py:98-103):

```python
    try:
        return _REGISTRY[strategy_id]
    except KeyError:
        raise ContractError(
            f"unknown strategy_id {strategy_id!r}; registered: {sorted(_REGISTRY)}"
        ) from None
```

Per its docstring it "Raises :class:`…ContractError` (never ``KeyError``) listing the registered ids, so callers fail closed on a single exception type" (SC/src/strategy_core/strategies/registry.py:93-97). Duplicate ids also fail closed: `f"strategy_id {strategy_id!r} is already registered to {existing.__name__}"` (SC registry.py:82-86). The registry starts **empty** — populating it is an `@register` import side effect and `strategy_core.strategies.__init__` is "intentionally SIDE-EFFECT-FREE" (SC/src/strategy_core/strategies/__init__.py:8-14; registry.py:10-15). Production `get_strategy` callers: SC/src/strategy_core/runtime/wiring.py:33, SC/src/strategy_core/contract/loader.py:104-106 (the opt-in `validate_section_via_registry` hook), QL/src/alpha_lab/agents/data_infra/ml/strategy_contract.py:145 and :216, TL/backend/src/trade_lab/services/model_registry.py:287.

#### 7(c) Exhaustive grep: literal `touch_reversal` across SC / QL / TL

Method: `rg -n 'touch_reversal'` over each repo, excluding `__pycache__`, `.git`, `*.pyc`, `node_modules`, `.venv`/`venv`; run twice (default gitignore-respecting pass + `--no-ignore --hidden` pass to catch untracked/ignored files). Totals: **SC 211 hits / 22 files (17 tracked + 5 untracked docs/diffs)**, **QL 85 hits / 11 files (7 tracked + 4 untracked diffs, +8 gitignored bundle contracts)**, **TL 20 hits / 7 files (+2 gitignored diff files)**.

**SC — code hits (every site):**

| Site | What it does |
|---|---|
| SC/src/strategy_core/strategies/touch_reversal/plugin.py:199 | **THE id definition**: `strategy_id = "touch_reversal"` on the `@register`ed plugin class |
| SC/src/strategy_core/strategies/touch_reversal/plugin.py:5, 6 | module docstring (production path; register side effect) |
| SC/src/strategy_core/strategies/touch_reversal/plugin.py:69 | imports `TouchReversalSection` |
| SC/src/strategy_core/strategies/touch_reversal/__init__.py:1, 5 | package docstring ("registers `strategy_id=\"touch_reversal\"`") |
| SC/src/strategy_core/strategies/touch_reversal/__init__.py:12, 16 | re-export imports of plugin + section |
| SC/src/strategy_core/strategies/touch_reversal/section.py:1 | module docstring |
| SC/src/strategy_core/strategies/touch_reversal/section.py:68 | `__all__` entry `"default_touch_reversal_section"` |
| SC/src/strategy_core/strategies/touch_reversal/section.py:157 | `def default_touch_reversal_section()` — the canonical default-section builder |
| SC/src/strategy_core/strategies/__init__.py:20 | docstring usage example (registration import) |
| SC/src/strategy_core/runtime/wiring.py:6, 12, 30 | docstrings ("UNCONDITIONALLY builds the registered ``touch_reversal`` plugin") |
| SC/src/strategy_core/runtime/wiring.py:21 | registration import (`# noqa: F401 -- registers`) |
| SC/src/strategy_core/runtime/wiring.py:22 | imports `default_touch_reversal_section` |
| SC/src/strategy_core/runtime/wiring.py:24 | `__all__ = ["touch_reversal_kwargs"]` |
| SC/src/strategy_core/runtime/wiring.py:27 | `def touch_reversal_kwargs()` — the production wiring helper |
| SC/src/strategy_core/runtime/wiring.py:33 | **hardcoded router lookup** `"plugin": get_strategy("touch_reversal")()` |
| SC/src/strategy_core/runtime/wiring.py:34 | default section attach |
| SC/src/strategy_core/runtime/state.py:24, 201 | comments (bare-constructor auto-attach) |
| SC/src/strategy_core/runtime/state.py:210 | error-message literal ("StrategyRuntime auto-attaches the default touch_reversal plugin …") |
| SC/src/strategy_core/runtime/state.py:214, 216 | deferred import + call of `touch_reversal_kwargs()` — the auto-attach default path |
| SC/src/strategy_core/constants.py:67 | comment (constants surfaced via `default_touch_reversal_section()`, sourced by the QL emitter) |
| SC/src/strategy_core/decisions/touch.py:35 | comment (same cross-reference) |
| SC/tests/test_touch_reversal_plugin.py:36, 40 | test imports (registration side effect) |
| SC/tests/test_touch_reversal_plugin.py:146, 179 | `get_strategy("touch_reversal")()` lookups |
| SC/tests/test_touch_reversal_plugin.py:190, 192 | registry-resolution test + `assert get_strategy("touch_reversal") is TouchReversalPlugin` |
| SC/tests/test_plugin_wiring.py:7, 19, 51 | docstring / import / call of `touch_reversal_kwargs()` |
| SC/tests/test_plugin_wiring.py:53, 58 | asserts `strategy_id == "touch_reversal"` |
| SC/tests/test_plugin_cross_bar_suppression.py:9, 43 | comments (auto-attach default) |
| SC/tests/test_plugin_cross_bar_suppression.py:49 | assert `runtime._plugin.strategy_id == "touch_reversal"` |
| SC/tests/test_contract.py:26, 34 | registration + section imports |
| SC/tests/test_contract.py:55 | docstring ("the real router key") |
| SC/tests/test_contract.py:64 | test-fixture contract literal `"strategy_id": "touch_reversal"` |
| SC/validation/test_b3_golive_plugin_regression.py:4, 83 | docstrings |
| SC/validation/test_b3_golive_plugin_regression.py:28, 88 | import + `**touch_reversal_kwargs()` |
| SC/validation/test_b3_multiday_reset_plugin_regression.py:5 | docstring |

**SC — docs/reports (aggregated):** docs/PLATFORM_REFACTOR_PROGRESS.md (35), b2_context.md (31, untracked), docs/PLATFORM_REFACTOR_PLAN.md (27), E3_SC_DIFF.txt (26, untracked), ARCH_STATE_RECON.md (18, untracked), E_SC_DIFF.txt (13, untracked), W1_SC_DIFF.txt (9, untracked), V3_COMPATIBILITY_MATRIX.md (1); `--no-ignore` pass adds `.pytest_cache/v/cache/nodeids` (7, test-cache artifact).

**QL — code hits (every site):**

| Site | What it does |
|---|---|
| QL/src/alpha_lab/agents/data_infra/ml/strategy_contract.py:52 | registration import so `get_strategy` can route (comment at :49) |
| QL/…/ml/strategy_contract.py:56, 58 | imports `TouchReversalSection` + `default_touch_reversal_section` |
| QL/…/ml/strategy_contract.py:71 | `def _build_touch_reversal_section(…)` — the emitter's section builder |
| QL/…/ml/strategy_contract.py:88 | `base = default_touch_reversal_section()` |
| QL/…/ml/strategy_contract.py:177 | **unconditional call** `section = _build_touch_reversal_section(config, interaction, approach)` |
| QL/…/ml/strategy_contract.py:36, 78, 130 | docstrings (`strategy_id` example `"touch_reversal"`) |
| QL/scripts/ml_training_tab.py:1758 | **hardcoded emission id**: `build_strategy_contract(config, selected, strategy_id="touch_reversal")` |
| QL/scripts/migrate_contracts_v2.py:44 | `ROUTER_STRATEGY_ID = "touch_reversal"` — the stamp applied to every migrated v1 bundle |
| QL/scripts/migrate_contracts_v2.py:18, 40 | docstring; registration import |
| QL/scripts/run_databento_acceptance.py:163 | registration import in the acceptance harness |
| QL/tests/agents/test_strategy_contract_nodrift.py:76, 285 | build fixtures with `strategy_id="touch_reversal"` |
| QL/tests/agents/test_strategy_contract_nodrift.py:93, 107 | expected values via `get_strategy("touch_reversal")` (strategy_version; barrier_mode) |
| QL/tests/agents/test_strategy_contract_nodrift.py:207, 208 | asserts contract `strategy_id` and `strategy_version == "1"` |
| QL/tests/agents/test_strategy_contract_nodrift.py:230-232, 237 | section import + `default_touch_reversal_section()` |
| QL/tests/agents/test_strategy_contract_repoint.py:57, 63, 84, 186 | build fixtures with the id |
| QL/tests/agents/test_strategy_contract_repoint.py:93 | assert `contract["strategy_id"] == "touch_reversal"` |
| QL/tests/agents/test_strategy_contract_repoint.py:152-153, 156 | section import + default value comparison |
| QL/tests/agents/test_databento_acceptance_cli.py:109 | fake acceptance-summary fixture |
| QL/tests/agents/test_databento_acceptance_cli.py:143 | asserts `summary["runtime"]["strategy_id"] == "touch_reversal"` |
| QL/models/NQ_W3_20260617T220752Z/strategy.json:4 (gitignored) | shipped contract literal `"strategy_id": "touch_reversal"` |
| QL/models/NQ_W3_20260613T055600Z/strategy.json:4 (gitignored) | same |
| QL/models/NQ_20260604_015413/strategy.json:4 + strategy.json.pre_v3.bak:4 (gitignored) | same (contract + pre-v3 backup) |
| QL/models/NQ_20260604_012623/strategy.json:4 + .pre_v3.bak:4 (gitignored) | same |
| QL/models/NQ_20260603_233847/strategy.json:4 + .pre_v3.bak:4 (gitignored) | same |

**QL — docs/reports (aggregated, all untracked diffs):** E_QL_DIFF.txt (24), E3_QL_DIFF.txt (18), W1_QL_DIFF.txt (6), W2_QL_DIFF.txt (3).

**TL — code hits (every site):**

| Site | What it does |
|---|---|
| TL/backend/src/trade_lab/services/strategy_core_service.py:21 | imports `touch_reversal_kwargs` |
| TL/backend/src/trade_lab/services/strategy_core_service.py:113-114 | comment ("the flag and the hardwired None path were removed") |
| TL/backend/src/trade_lab/services/strategy_core_service.py:115 | **unconditional wiring** `**touch_reversal_kwargs()` in `StrategyRuntime(...)` construction |
| TL/backend/src/trade_lab/services/model_registry.py:29 | registration import — "TL's first (intended) import of the strategy registry" |
| TL/backend/src/trade_lab/services/model_registry.py:36-37 | imports `default_touch_reversal_section` + `validate_feature_partition` (the import statement spans :36-39) from the touch section (self-labelled "E3 KNOWN COUPLING", :33-35) |
| TL/backend/src/trade_lab/services/model_registry.py:110 | activation gate compares `section.session_scheme != default_touch_reversal_section().session_scheme` |
| TL/backend/src/trade_lab/services/model_registry.py:383 | comment: "the hardcoded touch_reversal wiring stays, now guarded instead of implicit" |
| TL/backend/tests/test_activation_gate_w1.py:7, 8 | registration + default-section imports |
| TL/backend/tests/test_activation_gate_w1.py:54, 104 | fixture/assert built from `default_touch_reversal_section().session_scheme` |
| TL/backend/tests/test_feature_functions.py:19 | registration import |
| TL/backend/tests/test_inference_engine.py:251 | imports `TouchReversalSection` |
| TL/backend/tests/fixtures/strategy.json:4 | test-fixture contract literal `"strategy_id": "touch_reversal"` |

**TL — docs/reports (aggregated):** test.md (4 hits; untracked scratch note at repo root); `--no-ignore` pass adds E_TL_DIFF.txt (7) and E3_TL_DIFF.txt (5). No TL frontend/UI file contains the literal.

#### 7(d) Single-strategy assumptions (from the grep + reading)

**SC**
1. Hardcoded default router lookup: the single production wiring helper resolves the literal — `"plugin": get_strategy("touch_reversal")()` (SC/src/strategy_core/runtime/wiring.py:33) with the default section at :34.
2. Bare-constructor auto-attach: `StrategyRuntime(plugin=None)` attaches the touch plugin via `touch_reversal_kwargs()` (SC/src/strategy_core/runtime/state.py:207-219); the raised error message itself names "the default touch_reversal plugin" (SC/src/strategy_core/runtime/state.py:210-213); the auto-attach is only permitted under `RESEARCH_SESSION_SCHEME` (SC/src/strategy_core/runtime/state.py:208).
3. Registration is exclusively by explicit import of the one plugin module — there is no discovery mechanism; each consumer that routes must add its own import (SC/src/strategy_core/strategies/registry.py:10-15; import sites: SC wiring.py:21, QL strategy_contract.py:52, TL model_registry.py:29).
4. The plugin Protocol still carries touch vocabulary: `StrategyStep.touches`/`zones` fields (SC/src/strategy_core/strategies/protocols.py:188-189) and the `current_levels`/`snapshot_zones` accessors, with a recorded deviation note that `RuntimeUpdate`/`RuntimeSnapshot` "still expose typed ``levels``/``zones`` fields" (SC/src/strategy_core/strategies/protocols.py:308-322).
5. Only archetype-1's declared bars are buildable: "TIME bars are not yet buildable by the engine (decision 9.4 / Phase F) — only the declaration shape exists here" (SC/src/strategy_core/strategies/protocols.py:23-25, :65).

**QL**
6. The emission id is a call-site literal, not config: `strategy_id="touch_reversal"` (QL/scripts/ml_training_tab.py:1758); `rg 'strategy_id'` over QL/src/alpha_lab/agents/data_infra/ml/config.py returns **zero hits** — the pipeline config admits no strategy selector.
7. `build_strategy_contract` accepts `strategy_id` as a parameter but ALWAYS builds a `TouchReversalSection` regardless of its value (`section = _build_touch_reversal_section(...)`, QL/src/alpha_lab/agents/data_infra/ml/strategy_contract.py:177; touch-only imports at :52-60).
8. Only `training_mode == "dashboard_utility"` emits a full servable contract; every other mode gets a minimal `supported_by_runtime: False` record (QL/src/alpha_lab/agents/data_infra/ml/strategy_contract.py:151-166).
9. The v1→v2 migrator stamps every migratable bundle with the literal: `ROUTER_STRATEGY_ID = "touch_reversal"` (QL/scripts/migrate_contracts_v2.py:44, applied at :86-88).
10. The migrated/current bundle contracts on disk carry the literal: 5 × `models/*/strategy.json:4` plus 3 × `.pre_v3.bak:4` (all gitignored; see 7(c)). Three legacy un-migrated bundles on disk (`NQ_20260602_184719`, `NQ_20260602_232808`, `NQ_20260405_147t_…`) still carry their bundle-dir name as `strategy_id` instead of the literal.

**TL**
11. The serving runtime is constructed with the touch plugin unconditionally — "the flag and the hardwired None path were removed" (TL/backend/src/trade_lab/services/strategy_core_service.py:112-115).
12. Only `touch_reversal` is registered in TL's process (the sole registration import, TL/backend/src/trade_lab/services/model_registry.py:29), so a contract naming any other `strategy_id` fail-closes at `get_strategy` even if that plugin existed in SC.
13. The generically-named activation gate hardcodes the touch section: `if section.session_scheme != default_touch_reversal_section().session_scheme:` (TL/backend/src/trade_lab/services/model_registry.py:110), and `validate_feature_partition` is imported from the touch section module (:36-39) — self-described as "acceptable single-strategy behavior; the F-era cleanup moves this into the plugin" (:33-35).
14. One strategy per process: `serving_strategy_id` guard — activation refuses any contract "routed to ANY other strategy — the hardcoded touch_reversal wiring stays, now guarded instead of implicit" (TL/backend/src/trade_lab/services/model_registry.py:381-383, check at :443-450), wired from the single plugin's id at TL/backend/src/trade_lab/api/app.py:257 (and TL/backend/scripts/w3b/headless_replay.py:138).
15. Singleton active-model slot: `self._active: ActiveModel | None` (TL/backend/src/trade_lab/services/model_registry.py:390), swapped whole under one lock (:487-488), cleared by `deactivate()` (:490-494) — exactly one bundle serves at a time.
16. Single bundle root: one `settings.models_path` directory feeds the registry (TL/backend/src/trade_lab/api/app.py:253-254).
17. Barrier gating: "serving implements fixed_points semantics only" — any other `barrier_mode` is refused at activation (TL/backend/src/trade_lab/services/model_registry.py:105-109); the contract enum itself admits only `"fixed_points" | "r_relative"` with "no producer emits it [r_relative] yet" (SC/src/strategy_core/contract/schema.py:181-185).
18. Test fixture pins the literal contract (TL/backend/tests/fixtures/strategy.json:4).
19. UI strings: **no** `touch_reversal` literal exists in any TL UI/frontend file (the repo-wide grep's TL hits are backend code, tests, one fixture, and the untracked test.md); `strategy_id` reaches the UI only as a generic pass-through field (`ModelStatusDTO.strategy_id`, TL/backend/src/trade_lab/api/dto.py:147; `ModelBundleDTO.strategy_id`, :160; populated from the contract at TL/backend/src/trade_lab/services/runtime.py:512 and dto.py:347, 360).
