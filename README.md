# ManimatedAI — README

**AI-Powered Educational Animation Generator**
MIS 790 Culminating Experience | San Diego State University

ManimatedAI takes a plain-English topic prompt (e.g. *"What is a Linear Transformation?"*) and automatically generates a fully narrated, multi-scene educational animation video using a 10-node LangGraph pipeline.

---

## Table of Contents

1. [System Requirements](#1-system-requirements)
2. [Project Structure](#2-project-structure)
3. [Step-by-Step Setup](#3-step-by-step-setup)
   - [Step 1 — Clone or Download the Project](#step-1--clone-or-download-the-project)
   - [Step 2 — Create a Python Virtual Environment](#step-2--create-a-python-virtual-environment)
   - [Step 3 — Install All Dependencies](#step-3--install-all-dependencies)
   - [Step 4 — Set Up Your API Keys (.env file)](#step-4--set-up-your-api-keys-env-file)
   - [Step 5 — Download Kokoro TTS Model Files](#step-5--download-kokoro-tts-model-files)
4. [Running the System](#4-running-the-system)
   - [Option A — Command Line (Headless)](#option-a--command-line-headless)
   - [Option B — Full Stack (Backend + Frontend)](#option-b--full-stack-backend--frontend)
5. [Output Files](#5-output-files)
6. [Troubleshooting](#6-troubleshooting)

---

## 1. System Requirements

Before you begin, make sure your machine has the following installed:

| Requirement | Version | Why It's Needed |
|---|---|---|
| **Python** | 3.11 or 3.12 | Runtime for all pipeline code |
| **FFmpeg** | Any recent version | Manim uses it to stitch scene videos together |
| **LaTeX** (MiKTeX or TeX Live) | Any recent version | Manim uses it to render math equations |
| **Git** | Any version | For cloning the repository |

**How to install FFmpeg:**
- macOS: `brew install ffmpeg`
- Ubuntu/Debian: `sudo apt install ffmpeg`
- Windows: Download from [ffmpeg.org](https://ffmpeg.org/download.html) and add to PATH

**How to install LaTeX:**
- macOS: Install [MacTeX](https://tug.org/mactex/)
- Ubuntu/Debian: `sudo apt install texlive-full`
- Windows: Install [MiKTeX](https://miktex.org/download)

---

## 2. Project Structure

```
MIS790/
├── main.py                  ← CLI entry point (run to generate a video)
├── setup_kokoro.py          ← Downloads Kokoro TTS model files (run once)
├── requirements.txt         ← All Python dependencies
├── .env                     ← Your API keys (you create this — see Step 4)
│
├── src/
│   ├── orchestrator/        ← 10-node LangGraph pipeline (N1–N10)
│   ├── rag/                 ← Retrieval-Augmented Generation (RAG) modules
│   ├── core/                ← Ledger, layout engine, artifacts, job tracking
│   ├── backend/             ← FastAPI REST API server
│   ├── app/                 ← Streamlit frontend UI
│   ├── agent/               ← Critic and agent logic
│   ├── tools/               ← Manim sandbox and safety checker
│   └── common/              ← Shared settings, LLM client, types
│
├── prompts/                 ← LLM system prompts and code patterns
├── corpus/
│   ├── textbooks/           ← Reference text used by the RAG system
│   └── manim_examples/      ← Reference Manim code used by the RAG system
│
└── out/
    └── jobs/                ← Output folder (created automatically on first run)
        └── <job_id>/        ← All artifacts for each video job
```

---

## 3. Step-by-Step Setup

### Step 1 — Clone or Download the Project

If you have the zip file, extract it. If using Git:

```bash
git clone <repository-url>
cd MIS790
```

---

### Step 2 — Create a Python Virtual Environment

A virtual environment keeps all dependencies isolated from your system Python. **Do this inside the `MIS790` project folder.**

**macOS / Linux:**
```bash
python3.11 -m venv venv
source venv/bin/activate
```

**Windows (Command Prompt):**
```cmd
python -m venv venv
venv\Scripts\activate
```

**Windows (PowerShell):**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

You will know the environment is active when you see `(venv)` at the beginning of your terminal prompt.

> ⚠️ **Important:** Every time you open a new terminal session, you must re-activate the virtual environment using the `source venv/bin/activate` (or `venv\Scripts\activate` on Windows) command before running any project commands.

---

### Step 3 — Install All Dependencies

With the virtual environment active, install everything from `requirements.txt`:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

This will install Manim, LangGraph, FastAPI, Streamlit, the Kokoro TTS engine, and all other required libraries. This step may take a few minutes on first run.

---

### Step 4 — Set Up Your API Keys (.env file)

The system uses an LLM (Large Language Model) API to generate animation plans and code. You need at least **one** of the following API keys.

Create a file named `.env` in the root of the project folder (same folder as `main.py`) and paste in the following template:

```env
# ─── LLM Provider ───────────────────────────────────────────
# Set to "openai", "anthropic", or "deepseek"
LLM_PROVIDER=openai

# ─── API Keys — fill in at least ONE ────────────────────────
OPENAI_API_KEY=your-openai-api-key-here
ANTHROPIC_API_KEY=your-anthropic-api-key-here
DEEPSEEK_API_KEY=your-deepseek-api-key-here

# ─── Optional: Model Overrides ──────────────────────────────
# Leave these commented out to use the defaults
# OPENAI_MODEL=gpt-4o
# ANTHROPIC_MODEL=claude-3-haiku-20240307
# DEEPSEEK_MODEL=deepseek-chat

# ─── Optional: Fallback Provider ────────────────────────────
# If the primary provider fails, it will try this one next
LLM_FALLBACK_PROVIDER=deepseek

# ─── Optional: Pipeline Settings ────────────────────────────
MAX_SCENES=8          # Maximum number of scenes per video (default: 8)
LOG_LEVEL=INFO        # Log verbosity: DEBUG, INFO, WARNING, ERROR
SANDBOX_ENABLED=true  # Validate each scene before rendering (recommended)
SANDBOX_MAX_ATTEMPTS=3
SANDBOX_TIMEOUT_S=45
```

> 🔒 **Never share or commit your `.env` file.** It contains private API keys. The `.gitignore` should already exclude it, but double-check before pushing to GitHub.

---

### Step 5 — Download Kokoro TTS Model Files

ManimatedAI uses the **Kokoro** text-to-speech engine to add narration to animations. The model files are large (~90MB combined) and are **not included** in the repository — they must be downloaded separately.

Run this script **once** from the project root:

```bash
python setup_kokoro.py
```

This will download two files into the project root directory:
- `kokoro-v0_19.onnx` — the Kokoro voice model
- `voices.bin` — the voice data file

**Expected output:**
```
Downloading kokoro-v0_19.onnx...
✅ Downloaded: kokoro-v0_19.onnx
Downloading voices.bin...
✅ Downloaded: voices.bin
```

If the files already exist, it will skip the download and print a checkmark. You only need to run this once.

> ⚠️ **The system will not start without these two files.** If you skip this step, you will see a `FileNotFoundError` when running `main.py`.

---

## 4. Running the System

### Option A — Command Line (Headless)

The simplest way to run the full pipeline is directly from the command line:

```bash
python main.py --prompt "What is a Linear Transformation in Linear Algebra?"
```

**Additional options:**

```bash
# Limit the number of scenes generated
python main.py --prompt "Your topic here" --max-scenes 5

# Set video quality (low = 480p, medium = 720p, high = 1080p)
python main.py --prompt "Your topic here" --quality medium

# Use a specific job ID (useful for re-running or debugging)
python main.py --prompt "Your topic here" --job-id myjob123
```

The output video and all intermediate files will be saved to:
```
out/jobs/<job_id>/final_video.mp4
```

---

### Option B — Full Stack (Backend + Frontend)

To use the web interface, you need to start **two processes** — the backend API and the Streamlit frontend. Open two separate terminal windows.

**Terminal 1 — Start the FastAPI Backend:**
```bash
# Make sure virtual environment is active
source venv/bin/activate  # (or venv\Scripts\activate on Windows)

uvicorn src.backend.main:app --reload --port 8000
```

You should see:
```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

**Terminal 2 — Start the Streamlit Frontend:**
```bash
# Make sure virtual environment is active
source venv/bin/activate  # (or venv\Scripts\activate on Windows)

streamlit run src/app/app.py
```

Streamlit will automatically open a browser tab at `http://localhost:8501`. If it doesn't, open it manually.

> The backend must be running **before** you submit a job from the Streamlit interface.

---

## 5. Output Files

Every run creates a folder under `out/jobs/<job_id>/` containing:

| File | What it is |
|---|---|
| `final_video.mp4` | The complete rendered animation with narration |
| `plan.json` | The animation plan generated by the planner (N3) |
| `animation_descriptions.json` | Scene-by-scene descriptions (N4) |
| `layout_specs.json` | Spatial layout guide for each scene (N5) |
| `pseudocode.json` | JSON blueprint of animation objects and calls (N7) |
| `scene_01_*.py` … `scene_N_*.py` | Generated Manim Python scene files (N8) |
| `render_00_stdout.txt` … | Render logs for each scene (N10) |
| `n9_fix_summary.json` | Summary of any auto-fix cycles applied (N9) |
| `n10_render_summary.json` | Final render summary with success/failure per scene |
| `ledger.json` | Cross-scene consistency ledger (colors, notation, objects) |
| `job_journey.json` | Full audit trail of every pipeline step |
| `input.json` | Original input prompt and job metadata |

---

## 6. Troubleshooting

**`FileNotFoundError: Kokoro TTS model not found`**
→ You skipped Step 5. Run `python setup_kokoro.py` from the project root.

**`LLMClientError: OPENAI_API_KEY is not set`**
→ You either didn't create a `.env` file or it's in the wrong folder. Make sure `.env` is in the same folder as `main.py`.

**`ModuleNotFoundError: No module named 'manim'`**
→ Your virtual environment is not active. Run `source venv/bin/activate` first.

**Manim rendering fails with LaTeX errors**
→ LaTeX is not installed. See the System Requirements section above.

**FFmpeg not found**
→ FFmpeg is not installed or not on your PATH. See the System Requirements section above.

**Streamlit can't connect to backend**
→ Make sure the FastAPI backend (`uvicorn`) is running in a separate terminal on port 8000 before submitting a job.

---

*For questions about the project, refer to the Final Report (Deliverable 5) or contact the project team.*
