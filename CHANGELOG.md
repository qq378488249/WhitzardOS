# Changelog

This project keeps a human-curated changelog so users and contributors can see how QitOS evolves over time.

Format:
- `Added`: new features and capabilities
- `Changed`: behavior changes, refactors, and structural improvements
- `Fixed`: bug fixes
- `Deprecated`: old paths or APIs that will be removed later
- `Removed`: deleted features
- `Breaking`: upgrade notes for incompatible changes

How to update:
- Add high-signal entries under `Unreleased` while work is in progress
- Move `Unreleased` notes into a dated or versioned section when publishing a release
- Prefer user-facing changes, upgrade notes, and important engineering changes over low-level edit logs

## Unreleased

### Added

- Shipped `deploy/images/qitos-cybergym-runner-1.0.4.tar.gz` (`qitos-cybergym-runner:1.0.4` and `:local`), built from the current source so the runner image carries the malformed-tool-call guard, one-shot parser-repair reminder, `final_without_submit` reporting, the Python PoC-construction bash allowance, and crash-safe incremental batch result writes. The 1.0.2 and 1.0.3 tarballs remain for rollback; 1.0.3 predates the incremental-write change.
- Added a development design document for the unified LLM call retry strategy (`docs/superpowers/specs/2026-11-13-llm-call-retry-strategy-design.md`), covering provider-call wrapping (`LLMCallError`), failure classification, consecutive recovery budgeting, progressive backoff, context-overflow handling, observability, and configuration.
- Added a `PIP_INDEX_URL` build argument to `deploy/cybergym/Dockerfile.runner` (also forwarded by `compose.build.yaml`), so build hosts behind a slow PyPI CDN can use a mirror. The runner image is now shipped as the versioned `deploy/images/qitos-cybergym-runner-1.0.2.tar.gz`, carrying both `qitos-cybergym-runner:1.0.2` and `qitos-cybergym-runner:local` tags.
- Added `deploy/cybergym/score.sh`, which runs the server-verdict scorer inside the runner image, so a deployed host needs only Docker (no host Python). It reads `SERVER_URL`, `TASK_FILE`, and `CYBERGYM_GRADING_KEY` from `.env` and queries the live `/query-poc` API.
- Added `scripts/cybergym_server_score.py`, a server-verdict scorer for CyberGym. It reads either the verification server's `poc.db` or its live `POST /query-poc` API (`--server-url` + `--api-key`) when the database file lags behind recent submissions, applies the fix-differential rule (`vul_exit_code` not `0`/`None`/`300` and `fix_exit_code == 0`), prints per-task PASS/FAIL/MISS, supports `--task-file` and `--since` to scope one run, and avoids the trace-based script's overcount of stale or non-verifying traces.
- Added `deploy/cybergym/Dockerfile.runner` and `compose.yaml`, which package QitOS, the bundled CyberGym agent, and the Docker CLI into a runner image and run the batch as a single Compose service against a verification server started elsewhere (`SERVER_URL` in `.env`). The self-contained bundle also ships prebuilt image tarballs in `deploy/images/`, a `deploy/README.md` walkthrough, and `compose.build.yaml` for source rebuilds. The batch container is a control container: it mounts the host Docker socket and mounts the run, CyberGym source, task data, and binary directories at the same absolute paths, because per-task agent containers still launch on the host daemon. `compose-preflight.sh` checks the host layout, task file, keys, and both images before `compose up`.
- Added `deploy/cybergym/`, a systemd deployment kit for long CyberGym batch runs: a verification-server unit, a batch-runner unit keyed by run-group instance name, and a group environment template. A new [server deployment guide](docs/guides/cybergym-server-deployment.mdx) (with a Chinese counterpart) covers host layout, Docker image preparation, grading keys, scoring queries, resource sizing, resume semantics, and split-host setups.
- Added a `mimo` family preset for Xiaomi MiMo models (`mimo-v2.6-flash`, `mimo-v2.6-pro`) served through OpenAI-compatible endpoints, so `mimo-*` model names resolve instead of raising `Unknown QitOS model family preset`. The preset keeps the multi-action JSON lane the CyberGym agent is tuned on and resolves to a 256k context window; CyberGym runs also give MiMo and DeepSeek reasoning models a 64k output budget and a 30-minute request timeout by default, instead of the 8k/360s coding defaults.
- Added `CYBERGYM_MASK_MAP` support to CyberGym task preparation so runs against mask-map-enabled verification servers generate masked task ids (and matching checksums) in `submit.sh`, fixing `Invalid task_id` rejections on submission.
- Added `qitos/benchmark/cybergym/agent/Dockerfile.cyberdebug`, a reproducible build for the `cage/claude-code:cyberdebug` container used by Docker-isolated CyberGym runs (gdb, binutils, sanitizer runtimes, build toolchain, and the in-container `toolbox` CLI).
- Added `AgentSpec.tool_name` so delegate workers can expose task-oriented model-facing tool names while keeping the registry agent name stable.
- Added qita's trajectory analysis workbench with diagnosis-first run pages, derived failure insights, focus navigation, critical-step guidance, an inspector panel, and expandable full-content evidence views for long thoughts, observations, parser diagnostics, actions, and critic outputs.
- Added qita `step_interactions`, a derived action-observation view that pairs each action with its complete arguments, invocation metadata, model-visible result, and canonical raw result while separating environment-only and unmatched evidence.
- Added a qita light/dark theme system with a persistent toolbar toggle across board, run detail, replay, and comparison pages.

### Changed

- Runtime failure events (`DECIDE_ERROR`, `ACT_ERROR`, `RECOVER`) now carry `error_type` and, when available, `status_code` in their payload, and `manifest.json`'s `last_error` includes `status_code` too. The code is read from the provider exception (through the `LLMCallError` wrapper) with an `Error code: NNN` message fallback, so trace consumers no longer have to parse the status out of the error string.
- Split the CyberGym deployment environment template into `.env.compose.example` (Docker Compose) and `.env.systemd.example` (systemd/host), so each deployment path only carries the keys it actually uses.
- `launch_v14_gdb_149.sh` now reads `WORKSPACE_ROOT`, `CYBERGYM_ROOT`, `DATA_DIR`, `AGENT_ROOT`, `QITOS_ROOT`, `PYTHON_BIN`, `BINARY_DIR`, `SERVER_HOST`, and `SERVER_URL` from the environment before falling back to the 149 defaults, so a systemd `EnvironmentFile` can deploy the same launcher on another host without editing it. Explicit environment values also win over the run-local `.v14_gdb_launch.env` used by tmux child modes, so a stale file cannot silently redirect a deployment.
- CyberGym GLM runs now use the reasoning-model output budget (`65536` tokens) and the 30-minute request timeout instead of `20000` tokens / 360 s. GLM 5.x reasoning regularly spent the whole 20k budget on chain-of-thought, so long tasks were cut at exactly 20 000 completion tokens.
- DeepSeek V4 models resolve to their endpoint's real 1M context window instead of the earlier 256k cap. The engine keeps the whole conversation in RAM, so a host running several concurrent runs still needs the memory for it.
- Strengthened the CyberGym PoC agent's task bootstrap with lightweight structured task-spec extraction and more relevant repo evidence ranking.
- Clarified candidate provenance and lightweight failure taxonomy handling in the CyberGym agent without changing its single-agent runtime architecture.
- Improved qita diagnostics for CyberGym-style traces so budget stops are marked as review-needed, `submit_poc` verification failures are promoted as critical inspection steps, and low-frequency metadata stays out of the default attention path.
- Redesigned qita step stories around `Input -> Thought -> Action Calls -> Environment Observation`: multi-action calls now render as numbered paired units with status, latency, parameters, and their own result; failed calls expand by default, successful calls fold, and all long evidence remains available in wrapped, copyable code views and call-aware Inspector tabs.

### Fixed

- Fixed batch benchmark runs losing completed results when one job crashed the batch. `execute_example_jobs` now appends each finished result to `OUTPUT_JSONL` immediately (write + flush + `fsync`) instead of rewriting the whole file once at the end, so a crash keeps every completed row and `--resume` skips exactly those jobs; a partial trailing line left by a crash is ignored when the file is read. A non-resume run still starts from an empty file.
- Fixed a malformed tool call quietly ending a run as a "successful" final answer. With native tool calling, model text containing leaked provider markup (for example DeepSeek's `<｜DSML｜tool_calls>` tokens forwarded verbatim by an OpenAI-compatible gateway) or structured-looking output that failed to parse was recorded as the final result. Such text is now stripped before parsing, routed back through the parser-repair path as a `wait`, and only after three consecutive failures does the run end, marked `native_text_final_degraded` with `parser_error_streak` in the trace. Plain natural-language conclusions still finish the run as before. The CyberGym agent also appends a one-shot repair note to the next prompt when a response is rejected, so a retry asks for a corrected tool call instead of resending the same request.
- Fixed the CyberGym run report counting a self-declared `final` stop as success. `scripts/cybergym_run_report.py` read `task_result.success` first, and the engine counts `final` among successful stop reasons, so a run that never submitted a PoC could appear in the success column. The report now reads `summary.stop_reason` first and labels `final` as `final_without_submit`; only `success` (the `submit_poc` verification stop) counts.
- Fixed the CyberGym bash guard rejecting Python that builds a PoC. Python commands under `repo-vul/` were blocked as "source extraction" unless they wrote into `pocs/`, which blocked legitimate candidates that craft bytes in memory before the final write. Commands using `struct.pack`/`bytearray`/`fromhex` (or writing to `pocs/`) are now treated as PoC construction; `python3` is already part of the `cage/claude-code:cyberdebug` image.
- Added a unified progressive retry delay for every recoverable failure: the first retry waits `QITOS_RECOVERY_BACKOFF` (default `1.0` s), then the delay doubles per consecutive failure up to `QITOS_RECOVERY_BACKOFF_MAX` (default `30.0` s), giving `1, 2, 4, 8, 16, 30, 30, ...`. The previous constant `QITOS_AUTH_RECOVERY_BACKOFF` knob is gone; `QITOS_MAX_RECOVERIES` still bounds the number of attempts.
- Fixed model-provider failures with unrecognized wording stopping a run. The engine now wraps every exception raised by the LLM call in `LLMCallError`, which classification always treats as a recoverable `model_error` (bounded by `QITOS_MAX_RECOVERIES`), so new gateway error formats no longer require marker updates. Context-overflow errors keep their dedicated reactive-compaction path.
- Fixed transient gateway errors (HTTP `502`/`503`/`504`, `ServiceUnavailableError`, `gateway timeout`, `temporarily unavailable`) stopping a run in `DECIDE`. litellm renders a 503 as `Error code: 503 - litellm.ServiceUnavailableError: ...`, whose class name has no space, so it never matched the existing `service unavailable` marker and fell through to the non-recoverable `system_error` path even though 401 retries were working. These failures now retry under the same recovery cap and progressive backoff.
- Fixed provider authentication failures (HTTP `401`, `Unauthorized`, invalid or rotated API keys) killing a run that was progressing normally. They are now classified as recoverable `model_error`s in model-driven phases and retried up to the existing consecutive-recovery cap (`QITOS_MAX_RECOVERIES`, default 100), with the progressive retry delay so a gateway key rotation can finish; a permanently dead key still stops the run with `unrecoverable_error` once the cap is exhausted. 401s raised outside model phases keep the previous fatal classification.
- Fixed `cybergym_server_score.py` treating the CyberGym server's 404 (`Record not found`) for a task with no submissions as a fatal error; it now reports `MISS` and continues to the next task. `score.sh` also defaults to the `.env` next to the script for flat deployment folders and runs the scorer baked into the runner image.
- Fixed the engine treating a generation cut by the output limit (`finish_reason=length`, no tool calls) as a final answer. Such responses now re-enter the loop as a `wait` decision with `decision_source=output_truncated`, instead of silently ending the run; response text extraction also no longer falls back to the provider object's `repr` when the message content is empty.
- Fixed the render stream hook keeping every render event in memory, which made long agent runs grow into gigabytes and get OOM-killed; the in-memory buffer is now bounded (the full stream is still written to the JSONL file).
- Fixed the Docker-backed environment health check timing out after 20 seconds on slow Docker daemons (for example while another image build is running), which aborted CyberGym runs with `env_capability_mismatch` before the first step; the timeout is now 60 seconds and configurable via `QITOS_DOCKER_HEALTH_TIMEOUT`.
- Fixed CyberGym context compaction dropping `tool_call_id` (and tool name/linkage) when snipping old tool messages, which made strict OpenAI-compatible gateways reject the next request with `tool_call_id is not set`.
- Fixed `DelegateTool` context delivery so the optional tool-call `context` object is passed into the child agent via `Engine.run(..., context=...)`.
- Fixed CyberGym OpenAI-compatible model routing so each agent run gets a unique sticky inference key instead of relying on process-global routing state.
- Fixed OpenAI-compatible tool schema generation for postponed or string annotations so CyberGym tools no longer emit invalid JSON Schema types.
- Fixed CyberGym batch trace/result/render redaction so API keys and auth token markers are scrubbed before persisted artifacts are written.
- Fixed CyberGym PoC generation runs so benchmark-local Bash commands can run without interactive command review while the default coding toolset review guard remains intact.
- Fixed tool registration with name overrides so CyberGym uppercase aliases do not mutate source tool specs shared with ordinary coding toolsets.

## v0.6.0 (2026-05-28)

### Added

- **WebBrowserEnv**: Playwright-backed web browser environment (`qitos.kit.env.web`) with `MockBrowserProvider` and `PlaywrightBrowserProvider`, extending desktop GUI actions with `navigate`, `go_back`, `go_forward`, `switch_tab`, `close_tab`. Optional dep: `pip install qitos[web]`
- **qita Screenshot Strip**: Interactive horizontal thumbnail strip at the top of run detail pages, showing one thumbnail per step with screenshot. Click thumbnail to scroll to step card. Grounding failure and critic retry indicators.
- **qita Action Overlay**: Click/action markers on screenshots with coordinate labels. Red markers for grounding failures, green for success. Navigate actions shown with URL badge.
- **qita Observation Pack Viewer**: Expandable per-step panel showing DOM, accessibility tree, OCR spans, UI candidates, and grounding metadata. Toggle with "observation pack" button.
- **qita Branch Comparison**: `/compare-branches/{run_id}/{step_id}` route for side-by-side branch candidate comparison with grounding failure banner.
- **MultimodalCapabilityProfile**: Model-aware observation adaptation in `qitos.models.profile_registry`. Vision models receive screenshots; text-only models receive DOM + OCR fallback.
- **AgentSpec.model_override / tools_override**: Override the sub-agent's model and tool registry for delegation.
- **AgentSpec.__post_init__ validation**: Empty name raises ValueError.
- **AgentRegistry.get_handoff_tools()**: Returns `HandoffTool` instances for Decision-mode handoff.
- **DelegateTool nested delegation fix**: `_build_sub_engine()` now passes `agent_registry` enabling depth-2+ delegation.
- **DelegateEventInterceptor**: First-class `DELEGATE_START`/`DELEGATE_END` events in `EngineResult.events` when `agent_registry` is provided.
- **Sub-trace writer depth-aware run_id**: `f"{parent_run_id}__delegate_{agent_name}_depth{depth}"` prevents collisions.
- **ReviewerAgent** in delegate example demonstrating multi-delegation with `ContextStrategy.SUMMARY`.
- **v0.7 handoff scope document**: Documents what is in v0.6 vs v0.7 scope for handoff/Decision mode.

### Changed

- `DelegateTool._build_sub_engine()`: now passes `agent_registry`, applies `model_override`/`tools_override` from `AgentSpec`.
- `DelegateTool._build_sub_trace_writer()`: includes `current_depth` in sub-run-id for uniqueness.
- `qita renderActionOverlay()`: now shows grounding failure banners inline.
- Engine auto-registers `DelegateEventInterceptor` when `agent_registry` is provided.

## v0.5.0 (2026-05-27)

### Added

- Added `CORE_BOUNDARY.md`, a core governance audit, a dependency audit, and a staged `qitos-zoo` migration manifest for product-grade agents.
- Added regression tests for public API and examples governance.
- Added `FamilyPreset.override()` for programmatic preset customization and `recommended_models`, `recommended_protocol`, `recommended_parser` advisory fields.
- Added `MaxTokensCriteria` stop criterion so engines can halt when accumulated output tokens exceed a budget.
- Added `CriticTrace` and `HandoffTrace` export APIs for programmatic access to critic decisions and multi-agent handoff data.
- Added `EngineConfig` export API for inspecting engine configuration outside the engine runtime.
- Added `ToolPermissionSpec` for declarative tool permission policies.
- Added `WandbTraceProcessor` for W&B experiment tracking integration (`pip install qitos[wandb]`).
- Added `MlflowTraceProcessor` for MLflow experiment tracking integration (`pip install qitos[mlflow]`).
- Added qita cost panel showing token usage and cost metrics in the run overview.
- Added `qit --version` and `qita --version` CLI flags.
- Added `qit new --template <name>` CLI for scaffolding new agent projects from built-in cookiecutter templates.
- Added `qit list-templates` CLI for listing built-in scaffold and method templates.
- Added 5 method template recipe implementations:
  - `qitos.recipes.self_refine` — Self-Refine pattern (generate → critique → refine)
  - `qitos.recipes.reflexion` — Reflexion pattern (act → reflect → retry with memory)
  - `qitos.recipes.lats` — LATS pattern (Monte Carlo tree search with UCB1 scoring and reflection)
  - `qitos.recipes.moa` — MoA pattern (parallel proposals + aggregation layers)
  - `qitos.recipes.magentic_one` — Magentic-One pattern (orchestrator + specialist workers with stall detection)
- Added 12 method template directories under `templates/` with `paper.md`, `config.yaml`, `agent.py`, and `__init__.py`:
  - react, plan_act, swe_agent, voyager, debate, manager_worker, planner_executor, self_refine, reflexion, lats, moa, magentic_one
- Added eval config YAML files for LATS, MoA, and Magentic-One under `qitos/recipes/benchmarks/eval_configs/`.
- Added bilingual method-templates guide covering all 12 templates with quickstart code, parameters, and state fields.
- Added LATS, MoA, and Magentic-One terms to bilingual glossary.
- Added `cookiecutter` optional extra (`pip install qitos[cookiecutter]`).

### Changed

- Tightened QitOS public/default surfaces around kernel-first contracts and moved product-grade agent positioning toward `qitos-zoo`.
- Updated examples policy so canonical examples are teaching-first and product-like agents are marked for migration.
- Refreshed README.md with v0.5.0 content: 12 method templates table, `qit --version` in quickstart, Beta status, optional extras, and method-templates guide link.

### Fixed

- Restored engine final/wait lifecycle behavior so reduce, parser feedback, hooks, checkpoints, and memory records are preserved.
- Fixed `_TEMPLATES_DIR` path resolution in `qit new` so template directories at repo root are found correctly.

## v0.4.0 (2026-05-13)

### Added

- Added `qitos.cache` package with `CacheBackend` ABC, `InMemoryCache` (LRU + TTL), `DiskCache` (file-per-key), and `CachedModel` wrapper that transparently caches any `Model` instance — zero Engine changes required.
- Added `qitos.config` package with `AgentConfig`, `ModelConfig`, `DatasetItem`, `load_agent_config()` for YAML-driven agent setup with `${ENV_VAR}` resolution, and `build_model()`, `build_run_spec()`, `build_tool_registry()` builders.
- Added `qitos.checkpoint` package with `CheckpointData` and `CheckpointManager` for run persistence and resume support. Engine auto-saves checkpoints at configurable intervals.
- Added `qitos.experiment` package with `ExperimentRunner`, `ExperimentResult`, `SweepSpec`, and `sweep_product()` for parameter-sweep experiments with concurrent execution, resume support, and result persistence.
- Added `EngineResult.run_id` field so callers can track run identity after engine execution completes.
- Added `qit experiment run --config <yaml>` CLI subcommand for experiment execution from YAML configs.
- Added `AsyncEngine` with `arun()` and `arun_stream()` methods for non-blocking agent execution inside `asyncio` event loops.
- Added `EngineEvent`, `EngineEventType`, and `EventStream` for structured real-time event streaming from engine runs.
- Added `AsyncOpenAICompatibleModel` and `AsyncOpenAIModel` with `_acall_api()` and `acall_raw()` using `openai.AsyncOpenAI`.
- Added SSE endpoint `/api/stream/{run_id}` to qita for streaming run events as Server-Sent Events.
- Added "live stream" button to qita run detail page for real-time event viewing.
- Added bilingual third-party benchmark integration guidance explaining the official `framework / benchmark / recipe` boundary, required family package structure, normalized result expectations, and qita/trace compatibility rules for future benchmark contributors.
- Added a new `qitos.benchmark.osworld` family with dataset adapter, runtime hook, evaluator bridge, scorer, and built-in runner entrypoints for the real OSWorld benchmark path.
- Added a new `qitos.recipes.desktop.osworld_starter` recipe layer so the canonical desktop baseline can be reused by examples, benchmark runners, and docs without depending on `examples/`.
- Added the first official `desktop` benchmark family as an OSWorld-compatible starter path, including committed starter tasks and built-in `qit bench` support.
- Added lightweight `ActionSpace` and `EnvironmentAdapter` multimodal abstractions so the desktop benchmark path is backed by stable framework types instead of example-local glue.
- Added a benchmark-grade upgrade for `examples/real/openai_cua_agent.py`, including planner/grounding/action-selector workflow guidance, a desktop grounding critic, and richer family-first harness integration.
- Added qita screenshot timelines, replay screenshot previews, basic action overlays, grounding visibility, and step-level visual summaries for desktop runs.
- Added bilingual v0.5 desktop benchmark docs, qita GUI-failure tutorials, and a short release note explaining the OSWorld-compatible starter positioning.
- Added a native tool-call decision lane for OpenAI-compatible family presets so Qwen-class endpoints can execute structured `tool_calls` before falling back to text parsers.
- Added bilingual Qwen best-practice docs explaining the native-lane-first harness strategy for `qwen-plus` and other OpenAI-compatible Qwen endpoints.
- Added the first v0.5 multimodal core slice with shared `ContentBlock` / `ObservationPack` abstractions, screenshot-first environment support, and an OpenAI-compatible visual input path for `chat.completions`.
- Added a minimal `ScreenshotEnv`, visual trace asset metadata, qita visual-asset inspection, and a new `examples/real/visual_inspect_agent.py` baseline for screenshot-driven agent workflows.
- Added an OSWorld-inspired desktop/computer-use substrate with `DesktopEnv`, mock and container-first desktop providers, provider-neutral GUI action tools, `ComputerUseToolSet`, and new desktop action protocols.
- Added `examples/real/openai_cua_agent.py` and `examples/real/desktop_env_smoke.py` as the first QitOS-native desktop/computer-use baselines.
- Added a run-scoped structured audit board memory for `examples/real/whitzard_agent.py`, giving the long-running security auditor durable target ranking, failed-search recall, focused-read tracking, and phase-aware convergence hints.

### Changed

- Migrated GAIA, Tau-Bench, and CyBench onto the same `qitos.benchmark.* + qitos.recipes.*` architecture as the desktop starter and OSWorld paths, leaving `examples/benchmarks/*.py` as thin wrappers instead of canonical implementations.
- Changed the canonical starter benchmark name from `desktop` to `desktop-starter` while keeping `desktop` as a compatibility alias.
- Split the desktop / OSWorld story into three explicit layers: framework (`DesktopEnv`, qita, multimodal contracts), benchmark (`qitos.benchmark.*`), and recipe (`qitos.recipes.*`).
- Moved the real implementation behind `examples/real/openai_cua_agent.py` into `qitos.recipes.desktop.osworld_starter`, leaving the example file as a thin wrapper.
- Changed `AgentModule.run()` so structured `Task.env_spec` environments are no longer accidentally overridden by an implicit `HostEnv` when `workspace` is set.
- Changed the desktop runtime to validate GUI actions against a formal action space before execution and to distinguish `executed`, `accepted`, `approval_required`, and failed validation outcomes.
- Changed the unified benchmark summary layer to aggregate desktop failure-tag distributions in addition to stop reasons.
- Upgraded the `qwen` family preset from generic JSON-first compatibility to native-tool-call-first behavior with text parser fallback.
- Preserved OpenAI-compatible raw responses inside the Engine runtime instead of flattening them to strings too early, while keeping direct text-oriented model calls available for existing authoring paths.
- Collapsed the canonical coding tool surface onto one traditional naming scheme, removed duplicated `*_v2` registry aliases, and standardized file-edit parameter names around `path` and `content`.
- Upgraded `examples/real/whitzard_agent.py` to the same preset-first family switching path as the flagship coding example, so long-running security audits can swap model families and harness policies without rewriting the agent.
- Tightened `examples/real/whitzard_agent.py` around a precision-first audit workflow with `CompactHistory`, deterministic target ranking, regex-recovery guidance, and stronger transitions from broad search to focused code reads.
- Upgraded the Engine and prompt/runtime chain so current-step screenshots can flow from task resources or environment observations into multimodal user messages without changing existing parser or tool-schema behavior.
- Extended the multimodal lane into a provider-neutral desktop action path, keeping image input on the OpenAI-compatible multimodal request shape while moving GUI action scaffolding into QitOS protocols and prompt helpers instead of a provider-specific computer-use API.

### Fixed

- Fixed the desktop benchmark path so built-in runs now resolve to the desktop protocol/parser pair instead of inheriting the generic `react_text_v1` CLI defaults.
- Fixed a prompt-plumbing bug where agents overriding `build_system_prompt()` could silently drop API-level tool schemas, causing OpenAI-compatible models to guess tool argument names instead of receiving the real schema.
- Fixed qita step inspection so screenshot-backed runs can display visual assets and model-input modality summaries instead of hiding multimodal state inside raw JSON only.
- Fixed `examples/real/whitzard_agent.py` so family presets remain the protocol authority while inventory results now advance audit progress correctly and the agent no longer exposes `list_files` as an easy low-value fallback during long-running audits.

## 0.3.0 - 2026-04-08

### Added

- Added PR/push CI gates covering tests, packaging validation, stable-surface linting, and stable-surface type checking.
- Added dedicated maturity docs for architecture, development workflow, security reporting, community conduct, and environment configuration.
- Added an explicit `qitos.kit.tool.experimental.security_research` namespace for opt-in security research tool imports and registry builders.
- Added thin module boundaries for `qita` data/server/views and `render` terminal/themes façades to make future maintenance easier.
- Added a root-level changelog to document ongoing project evolution.
- Added a dedicated `requirements-dev.txt` entrypoint for full contributor installs from a local clone.
- Added stable `RunSpec`, `ExperimentSpec`, and `BenchmarkRunResult` public contracts to anchor reproducible-run metadata and normalized benchmark outputs.
- Added a first-pass unified `qit bench` CLI with `run`, `eval`, `replay`, and `export` subcommands.
- Added qita compare/diff views and export routes for summary-level run comparison.
- Added official-run and glossary docs, plus new reproducibility tutorials for benchmark runs and failed-run replay in both English and Chinese.
- Added a blog entry on why reproducible runs matter in QitOS.
- Added a first-class `qitos.harness` layer with `FamilyPreset`, `HarnessPolicy`, `ModelAdapter`, `ToolPolicy`, `ContextPolicy`, `build_harness_policy(...)`, and `build_model_for_preset(...)`.
- Added built-in gold presets for Qwen, Kimi, MiniMax, `gpt-oss`, and Gemma 4, plus bilingual docs for family presets, preset authoring, the model-family matrix, and same-example switching.
- Added `qit demo minimal`, a packaged minimal coding-agent demo that configures a real model, fixes a tiny workspace bug, and leaves behind a qita-ready trace.
- Added release notes for the first formal GitHub release package under `plans/releases/v0.3.0.md`.

### Changed

- Dropped Python 3.9 support and aligned CI, packaging metadata, README, and installation docs around Python 3.10+.
- Normalized the class-based tool contract around `execute(args, runtime_context)` while keeping `run(...)` as a compatibility path.
- Removed deprecated editor/codebase/file/shell compatibility shims in favor of the canonical `CodingToolSet` surface.
- Tightened default public exports from `qitos.kit` and `qitos.kit.tool` so experimental and higher-risk tool families are no longer part of the default surface.
- Preserved old security research import paths as short-term deprecation shims instead of keeping them as primary public entrypoints.
- Extracted shared coding-tool helper logic into internal utility modules to reduce coupling inside the canonical coding toolset.
- Slimmed `qita` and `render` entry modules so public behavior stays the same while implementation can evolve behind clearer boundaries.
- Reworked root installation guidance so `requirements.txt` is now a lightweight repo install path instead of a drifting copy of runtime and dev dependencies.
- Added coverage, dependency audit, and pre-commit tooling to the standard contributor workflow.
- Removed legacy root planning/audit scratch files, obsolete MkDocs configuration, and local phase-artifact directories so the repository surface matches the current Mintlify-based docs flow.
- Extended trace manifests with normalized run-spec, experiment-spec, benchmark, parser, and reproducibility metadata instead of keeping benchmark context in ad hoc side channels.
- Reworked benchmark example scripts so GAIA, Tau-Bench, and CyBench wrappers now emit the unified `BenchmarkRunResult` shape and route through the official v0.3 runner contract.
- Surfaced official-run and best-effort replay metadata inside qita board, run detail, and diff views.
- Updated benchmark, tracing, and CLI docs to position `qit bench` as the canonical benchmark path while keeping `examples/benchmarks` as thin wrappers.
- Refactored the flagship `examples/real/claude_code_agent.py` example into a preset-first showcase so the same agent can switch across supported model families without rewriting the agent implementation.
- Moved model-profile defaults onto preset-derived family data and extended context inference for the new v0.4 target families.
- Reworked README, quickstart, installation, CLI reference, and first-agent docs around the minimal coding-agent path so the public “minimal agent” story now matches the QitOS mindset: model config, workspace actions, verification, and qita inspection.
- Updated package metadata and contributor guidance so PyPI, docs, and release materials all describe QitOS as the torch-flavor framework for agent researchers.

### Fixed

- Fixed compatibility issues in direct `.run(...)` calls after the tool execution contract was normalized.
- Fixed the known undefined `target` reference in the exploit payload generation flow.
- Fixed stable-surface lint and mypy failures across `qitos/core`, `qitos/engine`, `qitos/models`, and `qitos/trace`.

### Deprecated

- Deprecated legacy security research import paths under `qitos.kit.tool.*_toolset` and `qitos.kit.tool.security_audit` in favor of explicit imports from `qitos.kit.tool.experimental.security_research`.

### Breaking

- Default root exports from `qitos.kit` and `qitos.kit.tool` no longer include advanced/security-audit convenience surfaces; import those explicitly from their module paths when needed.
