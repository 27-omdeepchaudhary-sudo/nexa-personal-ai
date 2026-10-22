# NEXA — Personal AI Companion

NEXA is a self-initiated Python project exploring local AI, voice interaction, an animated avatar interface, and macOS automation.

The goal is to build a personal AI companion that supports natural conversation, voice-driven interaction, and local language models while keeping privacy and user control in mind.

## Demo

### NEXA Interface

![NEXA interface screenshot](media/nexa-screenshot.HEIC)

**Demo video:** [Watch the NEXA demo](media/nexa-demo.MOV)

## Features

- Local AI experimentation using Ollama and compatible language models.
- English, Hindi, and Hinglish conversation goals.
- Animated avatar interface built with Tkinter.
- Voice input and command handling.
- Experimental macOS automation.
- Optional OpenAI API integration, disabled by default.

Some features may require additional dependencies, configuration, or macOS permissions.

## Technology Stack

- Python 3.11
- Ollama
- Tkinter
- Requests
- SpeechRecognition
- Pillow
- NumPy
- OpenAI Python library for optional API mode

## Requirements

- macOS
- Python 3.11
- Ollama
- A compatible Ollama model
- Microphone access for voice features
- Relevant macOS permissions for automation features

## Run Locally

### 1. Clone the repository

```bash
git clone https://github.com/27-omdeepchaudhary-sudo/nexa-personal-ai.git
cd nexa-personal-ai
```

### 2. Create and activate a virtual environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 4. Set up Ollama

Install Ollama for macOS and download a compatible model. The following command uses Qwen 3 8B, which matches the example configuration in this repository:

```bash
ollama pull qwen3:8b
```

Make sure Ollama is running before launching NEXA.

### 5. Launch the avatar interface

From the project root, run:

```bash
PYTHONPATH=src python -m nexa.avatar_ui
```

Keep the virtual environment activated while running the application.

## Configuration and Privacy

- Never commit API keys, passwords, access tokens, or private credentials.
- Keep local `.env` files out of version control.
- Use `.env.example` as a reference for configuration; do not put real secrets in it.
- Optional OpenAI mode may send prompts to an external service when enabled and configured.
- Dictionary lookups may use an external public API.
- Voice recognition and macOS automation may require system permissions.

## Project Status

NEXA is an experimental personal prototype under development. Features, behavior, and reliability may change over time. Not all features have been tested on a fresh installation.

## Author

**Omdeep Chaudhary**

GitHub: [@27-omdeepchaudhary-sudo](https://github.com/27-omdeepchaudhary-sudo)

---

*Built as a personal exploration of AI, Python, voice interaction, and macOS automation.*