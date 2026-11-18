# Interfaz Gradio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide a secure local Gradio interface to translate DOCX files through the existing pipeline without changing translation behavior.

**Architecture:** `interfaz_logica.py` owns file validation, output lifecycle, execution-thread coordination, packaging, filtering, and error mapping. `interfaz.py` is a thin Gradio Blocks adapter; the CLI starts it lazily. Pipeline cancellation is cooperative between batches and raises a dedicated exception before any output document is saved.

**Tech Stack:** Python 3.13, Gradio (optional dependency), pytest, pyflakes.

**Spec:** User request in this chat; `AGENTS.md`; `PLAN_INTERFAZ_Y_EN-ES.md` Fase 2.

## Global Constraints

- Never read, print, log, or summarize `.env` or secret environment variables.
- Never call DeepSeek during tests; use `cliente="falso"` only.
- Do not modify the protected translation modules named by the user.
- Listen on `127.0.0.1` with Gradio sharing disabled and retain one queued translation.
- Store each run under ignored `salidas/<timestamp>/`; no partial artifacts after cancellation.

## Review Focus

- Malicious ZIP members and compression-ratio bombs must be rejected before DOCX processing.
- A cancelled pipeline must not write a DOCX, report, comparison, or ZIP.
- Explicit `cancelar=None` must retain byte-identical fake-client output.
- The UI must not import or expose secrets and must lazily import Gradio from the CLI command.
- UI error mapping must make authentication, balance, model, network, and file errors actionable in Spanish.

---

### Task 1: Dependencies and cancellation contract

**Files:**
- Modify: `pyproject.toml`, `.gitignore`, `src/traductor/pipeline.py`, `tests/test_pipeline.py`

**Interfaces:**
- Produces: `TraduccionCancelada`, `traducir_documento(..., cancelar: threading.Event | None = None)`.

- [x] Write failing tests for between-batch cancellation and an untouched cancellation event producing the same fake-client DOCX bytes.
- [x] Run the focused tests and verify the missing cancellation contract fails.
- [x] Add the optional cancellation event and cleanup-safe abort behavior; declare runtime and optional UI dependencies.
- [x] Run focused tests and the pre-existing pipeline tests.

### Task 2: Testable interface logic

**Files:**
- Create: `src/traductor/interfaz_logica.py`, `tests/test_interfaz_logica.py`

**Interfaces:**
- Produces: upload validation, output cleanup/preparation, dry-run estimate, threaded execution, ZIP packaging, table filtering, and Spanish error mapping.

- [x] Write failing tests for valid/corrupt/non-DOCX/unsafe/bomb uploads, outputs, estimate, partial fake run, cancellation, filtering, and error mapping.
- [x] Run the focused tests and verify import/behavior failures.
- [x] Implement only the logic required by those tests using the existing pipeline and false client.
- [x] Run the focused tests.

### Task 3: Gradio adapter and launch paths

**Files:**
- Create: `src/traductor/interfaz.py`, `src/traductor/__main__.py`, `iniciar_interfaz.bat`
- Modify: `src/traductor/cli.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `interfaz_logica` services and `deepseek.MODELOS`.
- Produces: `crear_interfaz()` and `lanzar_interfaz(puerto: int)`.

- [x] Write failing CLI and construction tests that do not launch a server.
- [x] Run them and verify the command/module are absent.
- [x] Implement the thin Spanish Gradio UI, safe local launch, CLI subcommand, module entrypoint, and relative Windows launcher.
- [x] Run focused tests.

### Task 4: Full verification

**Files:**
- Verify: all modified and new files

- [x] Install the optional `interfaz` extra in the project virtual environment.
- [x] Run `pytest -q` and `pyflakes src tests`.
- [x] Review the diff against all global constraints and report exact outputs.
