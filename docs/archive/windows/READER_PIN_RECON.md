# READER-PIN RECON — do QL and TL consume the adopted vectorized reader everywhere?

**Date:** 2026-07-10 · **Method:** 4 parallel evidence agents + 1 adversarial verifier that independently re-ran every decisive probe (verdict: nothing refuted). All facts below are from command output, quoted verbatim; nothing inferred from docs.

**TL;DR — PASS in all four consumer×mode cells.** Both consumers load the VECTORIZED reader in local runtime and CI cold-install. One provenance caveat (the local install is a `file://` snapshot of the SC checkout, not an enforced git-pin install — byte-identical to the pin *today*, verified) and one pin-bypass script in QL (acceptance harness only, not the serving path). Details in §5.

---

## 1. DECLARED PINS

### Repo tips (fetched fresh, exit 0 — not cached refs)

```
$ git -C /c/Users/gonza/Documents/Trade-Lab rev-parse HEAD; git branch --show-current
b23cc57e0d085cc6f9754429dbb7c189b214c670
platform-refactor
$ git -C /c/Users/gonza/Documents/Trade-Lab fetch origin --quiet; echo "TL fetch exit: $?"; git for-each-ref --format='%(refname) %(objectname)' refs/remotes/origin/platform-refactor
TL fetch exit: 0
refs/remotes/origin/platform-refactor b23cc57e0d085cc6f9754429dbb7c189b214c670
```

```
$ git -C /c/Users/gonza/Documents/Claude-Quant-Lab rev-parse HEAD; git branch --show-current; git rev-parse '@{u}'
76546b0a71810f0b0a48159bcd49d45e51a881a8
platform-refactor
76546b0a71810f0b0a48159bcd49d45e51a881a8   (upstream == local HEAD == origin/platform-refactor; QL fetch exit: 0)
```

Local HEAD == origin/platform-refactor tip in **both** repos.

### Pin lines — local tips

```
$ grep -n "strategy" /c/Users/gonza/Documents/Trade-Lab/backend/pyproject.toml
18:  "strategy-core @ git+https://github.com/thealgochef/Strategy-Core.git@3d4193e6bf3e22caa23ad4246cffed153caede8f",

$ grep -n "strategy" /c/Users/gonza/Documents/Claude-Quant-Lab/pyproject.toml
36:    "strategy-core @ git+https://github.com/thealgochef/Strategy-Core.git@3d4193e6bf3e22caa23ad4246cffed153caede8f",
```

### Pin lines — origin tips

```
$ git -C /c/Users/gonza/Documents/Trade-Lab show origin/platform-refactor:backend/pyproject.toml | grep -n strategy
18:  "strategy-core @ git+https://github.com/thealgochef/Strategy-Core.git@3d4193e6bf3e22caa23ad4246cffed153caede8f",

$ git -C /c/Users/gonza/Documents/Claude-Quant-Lab show origin/platform-refactor:pyproject.toml | grep -n strategy
36:    "strategy-core @ git+https://github.com/thealgochef/Strategy-Core.git@3d4193e6bf3e22caa23ad4246cffed153caede8f",
```

Sanity: these are the **only** `git+` dependencies in either pyproject (grep `git+` over both files hits exactly those two lines).

**Fact:** TL and QL declare the identical pin `3d4193e6bf3e22caa23ad4246cffed153caede8f`; local == origin in both repos.

### Other pinning mechanisms — full sweep

| Mechanism | TL | QL |
|---|---|---|
| `requirements*.txt` / `constraints*.txt` | no hits (Glob) | no hits |
| Lockfiles (`uv.lock`, `poetry.lock`, `Pipfile.lock`, `pdm.lock`, `pylock*`) | no hits | no hits |
| Git submodules (`.gitmodules` + `git submodule status`) | none (file absent, empty status, exit 0) | none |
| `.pth` files in repo (Glob + `find` incl. gitignored dirs) | none | none |
| In-repo venvs (`.venv`/`venv` at root and `backend/`) | none | none |
| `.vscode/` (interpreter pin) | directory absent | directory absent |
| `conftest.py` sys.path/Strategy-core/PYTHONPATH | no hits (grep exit 1) | no hits |
| `.env*` PYTHONPATH/strategy lines | no hits | no hits |
| PYTHONPATH set anywhere | comments/docs only (`backend-ci.yml:3` comment; `w3b/REPORT.md:122` points at TL's own `scripts`) | comments/docstrings only (`ci.yml:5`; `PYTHONPATH=src` recipes point at QL's own `src`) |

**sys.path manipulation — every code hit:**

- TL `backend/scripts/w3b/window.py:127-128` inserts `QL_REPO / "src"` and `QL_REPO / "scripts"` (`QL_REPO` default at `window.py:99` = `C:\Users\gonza\Documents\Claude-Quant-Lab`) — inserts **QL**, not Strategy-core. All other TL hits (`w3b/run_window.py:32-33`, five `test_w3b_*.py` files) insert TL's own `backend/scripts`. **No TL code path puts a Strategy-core checkout on sys.path.**
- **QL `scripts/run_databento_acceptance.py` — the one pin bypass found:**
  ```
  33:REPO_ROOT = Path(__file__).resolve().parents[1]
  34:DEFAULT_STRATEGY_CORE_ROOT = REPO_ROOT.parent / "Strategy-Core"
  157:    strategy_src = strategy_core_root / "src"
  158:    if strategy_src.exists():
  159:        sys.path.insert(0, str(strategy_src))
  ```
  When run with the default root, `C:/Users/gonza/Documents/Strategy-Core/src` (the live working tree, whatever commit it's on) is prepended ahead of the installed pinned package before `import strategy_core...` at lines 163-165. Acceptance/validation script only — not the serving or training path.
- Other QL hits (`w3_cache_warmer.py:68-73`, `decision_repoint_proof.py:32`, `dashboard.py:26`, scratch files) insert QL's own `src`/`scripts`.
- Non-mechanism reference: `QL docs/pipeline_state.yaml:135-136` points at `../Strategy-Core` (documentation only).

### CI cold-install path

One workflow per repo: TL `.github/workflows/backend-ci.yml`, QL `.github/workflows/ci.yml`. Both install with a single step (verbatim):

```yaml
# TL backend-ci.yml:36-41 (working-directory: backend)
      # Clean install from scratch. This resolves the strategy-core dependency from
      # its git+https pin against the public Strategy-Core repo (anonymous, no key or
      # token) and installs all runtime + dev dependencies. A git+ssh pin or a missing
      # test dependency (e.g. pytest-asyncio) fails here rather than silently in prod.
      - name: Install (cold)
        run: pip install -e ".[dev]"
```

```yaml
# QL ci.yml:40-45 (repo root)
      # Clean install from scratch. Resolves the strategy-core dependency from its
      # git+https pin against the public Strategy-Core repo (anonymous, no key or
      # token) and installs all runtime + dev dependencies — the pin goes from
      # DECLARED to ENFORCED here (decision 9.6).
      - name: Install (cold)
        run: pip install -e ".[dev]"
```

No requirements/constraints/lockfile exists (table above), so CI resolves strategy-core from **exactly the pyproject git URL sha** — `3d4193e` for both, identical at local and origin tips. Both workflows use `actions/setup-python` with `python-version: "3.13"` (TL `backend-ci.yml:31-34`, QL `ci.yml:35-38`).

---

## 2. PINNED CONTENT (sha `3d4193e6bf3e22caa23ad4246cffed153caede8f`)

Both consumers pin the same single sha, so one content analysis covers both.

```
$ git -C /c/Users/gonza/Documents/Strategy-core rev-parse --verify 3d4193e6bf3e22caa23ad4246cffed153caede8f
3d4193e6bf3e22caa23ad4246cffed153caede8f    (exit=0)
$ git log --oneline -1 3d4193e
3d4193e feat(streaming): OpenSetupView accessor - open_setups() read-only projection (EXEC P1)
```

`src/strategy_core/data/databento_parquet.py` exists at the pin (extracted via `git show`, exit 0, 1148 lines).

### (a) Vectorized markers — ALL PRESENT

```
$ grep -n -E '_decode_batches|_DecodedBatch|emit_deduped' <file@3d4193e>
107:class _DecodedBatch:
131:    def emit_deduped(
374:            for item in self._decode_batches(Path(path), self._window_for(index)):
378:                events, last_tob = item.emit_deduped(last_tob)
465:        for item in self._decode_batches(path, window):
473:    def _decode_batches(
475:    ) -> Iterator[DataQualityWarning | _DecodedBatch]:
804:    ) -> tuple[list[DataQualityWarning], _DecodedBatch | None]:
963:        decoded = _DecodedBatch(
```

### (b) Deleted row-wise symbols

- `_normalize_row` — **0 hits** (the deleted row-wise entry point is absent).
- `to_pylist` — 8 hits (lines 623, 667, 708, 728, 749, 773, 835, 906), but all fall inside per-column helper/fallback functions (`_ts_arrays` @579, `_grid_ticks` @643, `_strict_size`/`_optional_size` @675/689, `_upper_text` @716, `_effective_sequence` @732, `_spread_mask` @758) and `_decode_batch` (@791) — per-column conversions inside the **batch** decoder, not a `_normalize_row`-style per-event scan loop. No row-wise reader path exists at the pin.

### Reader-chain ancestry

```
$ git log --oneline -1 332ad3e
332ad3e perf(data): W3A-READER P2 — vectorized DatabentoParquetSource decode; row-wise path kept until P3
$ git log --oneline -1 37359ae
37359ae perf(data): W3A-READER P3c — delete the row-wise reader path on full green

$ git merge-base --is-ancestor 332ad3e 3d4193e; echo exit=$?   → exit=0  (IS ancestor)
$ git merge-base --is-ancestor 37359ae 3d4193e; echo exit=$?   → exit=0  (IS ancestor)
```

Branch containment note: the pin (and both reader commits) exist **only on `platform-refactor`** (local + origin). None are ancestors of `origin/main` (tip `fd53e06`) — expected, since the platform work hasn't been merged to main; flagged so a future main-based install isn't assumed to carry the reader.

### SC checkout state (relevant to §3's install provenance)

Local SC HEAD = `0e86cb5` (platform-refactor) = pin + 3 commits, **all docs-only**:

```
$ git log --oneline 3d4193e..0e86cb5
0e86cb5 docs: COCKPIT window close record - ...
a36b858 docs: EXEC pushed annotation w/ greenlight CI ids + COCKPIT status line (COCKPIT P0 doc-op)
f2f0d16 docs: EXEC window close record - ...
$ git diff --stat 3d4193e 0e86cb5 -- src/strategy_core    → empty (exit 0): zero src changes between pin and HEAD
```

`git status --porcelain`: untracked report/scratch files only; **tracked tree clean** (no M/A/D entries, `src/strategy_core` clean).

---

## 3. ACTUAL RUNTIME RESOLUTION (the stale-site-packages class)

### There are no consumer venvs — both consumers run the shared system Python 3.13

Exactly two interpreters exist on the machine, and nothing else is referenced anywhere:

```
$ py --list-paths
 -V:3.13 *        C:\Users\gonza\AppData\Local\Programs\Python\Python313\python.exe
 -V:3.11          C:\Users\gonza\AppData\Local\Programs\Python\Python311\python.exe
```

Bare `python` resolves to Python313 in **both** Git Bash and PowerShell (Python313 precedes Python311 in PATH; `sys.executable` = `C:\...\Python313\python.exe` in both shells). Negative sweep: no `uv run`/`uv sync`, no `Scripts/python.exe` venv paths, no `defaultInterpreterPath` anywhere in either repo; the sibling `Trade-Lab-verify` / `Strategy-Core-verify` checkouts are referenced by nothing in TL/QL.

### Env 1 — Python 3.13.1 (the live environment for both consumers)

```
$ Python313/python.exe -c "import strategy_core; print(strategy_core.__file__)"
C:\Users\gonza\AppData\Local\Programs\Python\Python313\Lib\site-packages\strategy_core\__init__.py

$ Python313/python.exe -c "from strategy_core.data.databento_parquet import DatabentoParquetSource as S; import inspect; src=inspect.getsource(S); print('VECTORIZED' if ('_decode_batches' in src or 'emit_deduped' in src) else 'ROW-WISE/UNKNOWN')"
VECTORIZED
```

Direct grep of the resolved file (`site-packages\strategy_core\data\databento_parquet.py`): `emit_deduped` @131, `_decode_batches` @374/465/473, `to_pylist` per-column hits at the same 8 lines as the pin, and **zero `_normalize_row` hits** — line-for-line identical to the pin content in §2.

**Install provenance — NOT editable, NOT from the git pin:**

```
$ Python313/python.exe -m pip show strategy-core
Name: strategy-core / Version: 0.1.0 / Location: ...\Python313\Lib\site-packages
Requires: numpy, pyarrow, pydantic, tzdata / Required-by: trade-lab
(no "Editable project location" line)

$ python -c "...Distribution.from_name('strategy-core').read_text('direct_url.json')"
{"dir_info": {}, "url": "file:///C:/Users/gonza/Documents/Strategy-core"}
```

`INSTALLER` = pip; no `__editable__*strategy*` or strategy-mentioning `.pth` in site-packages (grep exit 1). The installed copy is a **non-editable snapshot of the local SC checkout directory**, laid down **2026-07-10 03:16:35 -0500** (file mtime/birth).

**Snapshot ↔ pin identity (verified, not assumed):**

```
$ diff -q site-packages/.../databento_parquet.py  Strategy-core/src/.../databento_parquet.py   → IDENTICAL
$ diff -r -q -x "__pycache__" site-packages/strategy_core  Strategy-core/src/strategy_core     → exit=0 (whole package byte-identical)
```

Checkout HEAD `0e86cb5` has zero `src/strategy_core` diff vs the pin `3d4193e` (§2), so **installed code == pinned code today**. This equality is coincidental, not enforced — see §5.

**No shadowing:** the three editable `.pth` entries add `Claude-Quant-Lab\src` (alpha_lab), `Trade-Dashboard\backend\src` (alpha_lab copy), and `Trade-Lab\backend\src` (trade_lab); none contains a `strategy_core` package, so site-packages is the true resolution. (Side observation, out of scope: Trade-Dashboard's `src` also holds an `alpha_lab` package on the same sys.path; `import alpha_lab` currently resolves to the QL copy.)

### Env 2 — Python 3.11.0

```
$ Python311/python.exe -c "import strategy_core; print(strategy_core.__file__)"
ModuleNotFoundError: No module named 'strategy_core'
$ Python311/python.exe -m pip show strategy-core
WARNING: Package(s) not found: strategy-core   (exit=1)
```

Not a consumer environment: no strategy_core, no trade_lab, no alpha_lab, and nothing in either repo invokes it.

### Consumer → environment mapping (evidence chain)

- **TL backend → Python313.** Launch is bare `python -m trade_lab.api` from `backend/` (README.md:18/65, backend/README.md:24); bare `python` = Python313 in both shells; `trade-lab` is editable-installed only in Python313 (`Editable project location: C:\Users\gonza\Documents\Trade-Lab\backend`; `import trade_lab` → the checkout); uvicorn is launched in-process (`uvicorn.run(` at `backend/src/trade_lab/api/__main__.py:61`) — same interpreter.
- **QL → Python313.** `alpha-signal-lab` editable install exists only in Python313 (`Editable project location: C:\Users\gonza\Documents\Claude-Quant-Lab`; `import alpha_lab` → the QL checkout); launch recipes use bare `python` (AGENTS.md:22/43/53); QL's `.claude/settings.local.json:5-6` records explicit Python313 invocations; `.python-version` = `3.13.1`. Doc discrepancy, stated for the record: `AGENTS.md:27` claims Python `>=3.14` via `uv` with `.python-version` `3.14.5` — contradicted by the tree (`.python-version` reads 3.13.1, no `uv.lock`, `uv` not on PATH: `command not found`, pyproject `requires-python = ">=3.13"`); it cannot be the actual launcher.

---

## 4. OWNER'S GAP-ERA PIN WORK (since 2026-06-13)

`git log --all --follow --date=iso --format='%h %ad %an %s' --since=2026-06-13 -- <pyproject>`:

**TL `backend/pyproject.toml`:**

```
c9a06d5 2026-07-10 09:52:25 -0500 algochef chore: pin strategy-core @ EXEC land - 1650327 -> 3d4193e (OpenSetupView accessor)
4d0eb81 2026-07-07 22:35:57 -0500 algochef fix(live): marshal SDK subscribe/start onto the session loop (wedge fix, P1)
c92f13b 2026-07-06 22:43:06 -0500 algochef chore: pin strategy-core @ SEED land — f9a1f63 -> 1650327 (prior-day store-walk)
fc994f9 2026-07-03 02:53:43 -0500 algochef chore: pin strategy-core @ W3b land — 256020c -> f9a1f63 (vectorized reader ADOPTED, D-P-16)
```

**QL `pyproject.toml`:**

```
76546b0 2026-07-10 09:53:08 -0500 Luis Gonzalez chore: pin strategy-core @ EXEC land - 1650327 -> 3d4193e (OpenSetupView accessor)
75f83dc 2026-07-06 22:43:43 -0500 Luis Gonzalez chore: pin strategy-core @ SEED land — f9a1f63 -> 1650327 (prior-day store-walk)
3f451bd 2026-07-03 02:53:35 -0500 Luis Gonzalez chore: pin strategy-core @ W3b land — 256020c -> f9a1f63 (vectorized reader ADOPTED, D-P-16)
```

Per-commit pin diffs (all quoted from `git show <sha> -- <path> | grep -E '^[+-].*strategy'`):

| Date | TL commit | QL commit | Pin change |
|---|---|---|---|
| entering window | 81a54b2 (2026-06-12) | 393716d (2026-06-12) | → `256020c` (W1 canonical ingestion) |
| 2026-07-03 (W3b land) | fc994f9 | 3f451bd | `256020c` → `f9a1f63` — **vectorized reader ADOPTED (D-P-16)** |
| 2026-07-06 (SEED land) | c92f13b | 75f83dc | `f9a1f63` → `1650327` |
| 2026-07-10 (EXEC land) | c9a06d5 | 76546b0 | `1650327` → `3d4193e` (current) |

- TL `4d0eb81` (wedge fix) touched the file but **not** the pin line (empty `^[+-].*strategy` grep — verified).
- Branch containment: every pin-touching commit is on `platform-refactor` + `origin/platform-refactor` **only** — no stray pin work on unmerged side branches, none on `main`.
- The two author identities (`algochef` / `Luis Gonzalez`) are the same owner's two git configs; the repos moved in lock-step pairs minutes apart.
- Consequence: since 2026-07-03 every declared pin (`f9a1f63`, `1650327`, `3d4193e`) post-dates reader adoption, and §2 proved `332ad3e`/`37359ae` are ancestors of the current pin. **No gap-era pin ever pointed back at a row-wise reader after adoption.**

---

## 5. VERDICT TABLE

Mode correction, established with evidence in §3: neither consumer has a "local editable" strategy-core — the actual local mode is **shared system-Python 3.13 site-packages, non-editable `file://` snapshot of the SC checkout**. Strategy-core is not editable-installed anywhere.

| Consumer | Mode | Reader that actually loads | Comes from | PASS/FAIL |
|---|---|---|---|---|
| TL backend | Local runtime (system Py3.13 site-packages) | **VECTORIZED** (probe printed `VECTORIZED`; `_normalize_row` = 0 hits in resolved file) | snapshot of `file:///C:/Users/gonza/Documents/Strategy-core` @ HEAD `0e86cb5`, whose `src/strategy_core` is **byte-identical** to pin `3d4193e` (diff -r exit 0) | **PASS** |
| TL backend | CI cold-install | **VECTORIZED** | `pip install -e ".[dev]"` resolves `git+...@3d4193e` (backend/pyproject.toml:18); §2 proved 3d4193e content | **PASS** |
| QL | Local runtime (same interpreter + site-packages as TL) | **VECTORIZED** (same single installed copy) | same snapshot as above | **PASS** |
| QL | CI cold-install | **VECTORIZED** | `pip install -e ".[dev]"` resolves `git+...@3d4193e` (pyproject.toml:36) | **PASS** |

**Overall: PASS — "both consumers consume the adopted reader everywhere" holds in every mode that exists on this machine and in CI**, verified down to the resolved-file greps and re-confirmed by an independent adversarial re-run.

### Caveats and residual risks (no code changes made; exact fixes stated)

1. **Local install is pin-shaped by coincidence, not by enforcement.** `direct_url.json` says the installed copy came from the local directory (`file:///C:/Users/gonza/Documents/Strategy-core`, installed 2026-07-10 03:16:35), not from the git pin. Today it is byte-identical to `3d4193e` (proven). But (a) any SC `src` edit followed by `pip install .` silently diverges the runtime from the declared pin, and (b) any SC checkout movement **without** reinstall leaves the snapshot stale in the other direction — and nothing would fail. CI is the only place the pin is enforced. **Exact fix if enforcement is wanted locally:** reinstall from the pin — `Python313/python.exe -m pip install --force-reinstall --no-deps "strategy-core @ git+https://github.com/thealgochef/Strategy-Core.git@3d4193e6bf3e22caa23ad4246cffed153caede8f"` — and adopt that as the only local install recipe (or add a startup guard comparing the installed dist's `direct_url.json` sha against the pyproject pin).
2. **QL `scripts/run_databento_acceptance.py:157-159` bypasses the pin** by prepending `../Strategy-Core/src` (live working tree) to sys.path when the directory exists. Acceptance harness only — but its results describe whatever the checkout is on, not the pin. **Exact fix:** delete the `sys.path.insert` (rely on the installed package) or require an explicit `--strategy-core-root` argument with no default.
3. **One shared site-packages = one SC version for both consumers.** TL and QL declare pins independently but resolve locally to the same single installed copy; if their pyproject pins ever diverge, the local runtime cannot honor both. Currently identical (`3d4193e` == `3d4193e`), so no conflict — flagged as a structural property, not a defect.
4. **Pin lineage lives only on `platform-refactor`.** Neither the pin nor the reader commits are ancestors of `origin/main` (`fd53e06`); an install driven off main would not carry the vectorized reader. Not a current failure — both consumers' CI and pyprojects sit on platform-refactor.
5. **Stale doc:** QL `AGENTS.md:27` (Python ≥3.14 via `uv`, `.python-version` 3.14.5) contradicts the machine (`3.13.1`, no uv). Doc-only; could misdirect a future environment rebuild.
6. Python 3.11 exists on the machine but has no strategy_core and is referenced by nothing in either repo — not a consumer mode.
