# NEXA — Personal AI Companion

NEXA is a self-initiated Python project exploring local AI, voice interaction, an animated avatar interface, and macOS automation.

## Features

- Local AI experiments using Ollama and compatible language models.
- English, Hindi, and Hinglish conversation goals.
- Custom animated avatar built with Tkinter.
- Voice input and command handling.
- Experimental macOS automation.
- Optional OpenAI API mode, disabled by default.

## Technology

Python 3.11, Ollama, Tkinter, Requests, and SpeechRecognition.

## Run locally

1. Install Python 3.11 and Ollama.
2. Create and activate a Python virtual environment.
3. Install dependencies: `python -m pip install -r requirements.txt`
4. Download a compatible model: `ollama pull qwen2.5:7b`
5. Launch NEXA: `PYTHONPATH=src python -m nexa.avatar_ui`

## Project status

NEXA is an experimental personal prototype. Some features may require additional dependencies, permissions, or configuration.

## Privacy

Never commit API keys, credentials, tokens, private logs, or local environment files. Dictionary lookups use an external public API. Optional OpenAI mode sends requests to an external service when enabled.

## Author

Omdeep Chaudhary
wc -c README.md
