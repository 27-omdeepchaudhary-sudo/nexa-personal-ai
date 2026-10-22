"""NEXA voice loop: English-first, local-first, and command-oriented."""
import fcntl
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
import speech_recognition as sr
from nexa.main import ask_nexa
from nexa.conversation import get_history, add_exchange
from nexa.calculator import calculate_spoken
from nexa.mac_actions import (
    research_google,
    get_current_page_content,
    open_google_result,
    search_google_followup,
    open_google,
    search_google,
    next_google_results,
    open_youtube,
    play_song,
    search_youtube,
    search_news,
    player_action,
    change_system_volume,
    change_brightness,
    control_brave_window,
)
# Keep the selected voice and speed unchanged.
VOICE = "Samantha"
RATE = "175"
AWAKE = True
LOCK_HANDLE = None
SPEECH_PROCESS = None
SPEECH_LOCK = threading.Lock()
STATE_LOCK = threading.Lock()
COMMAND_LOCK = threading.Lock()
COMMAND_VERSION = 0
COMMAND_BUSY = False
SPEECH_GENERATION = 0
LAST_SPEECH_END = 0.0
# Optional callbacks used by the avatar GUI. The standalone voice app still works.
GUI_EVENT_CALLBACK = None
LISTENER_STOP_EVENT = None
def configure_gui(callback=None, stop_event=None):
    """Connect voice events to a GUI; callback(event, text) must be thread-safe."""
    global GUI_EVENT_CALLBACK, LISTENER_STOP_EVENT
    GUI_EVENT_CALLBACK = callback
    LISTENER_STOP_EVENT = stop_event
def _emit_gui(event, text=""):
    callback = GUI_EVENT_CALLBACK
    if callback is not None:
        try:
            callback(event, str(text))
        except Exception:
            pass
def stop_speech():
    """Stop only the speech process started by NEXA."""
    global SPEECH_PROCESS, LAST_SPEECH_END
    with SPEECH_LOCK:
        process = SPEECH_PROCESS
        SPEECH_PROCESS = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        LAST_SPEECH_END = time.monotonic()
def _speech_is_running():
    global SPEECH_PROCESS, LAST_SPEECH_END
    with SPEECH_LOCK:
        if SPEECH_PROCESS is None:
            return False
        if SPEECH_PROCESS.poll() is None:
            return True
        SPEECH_PROCESS = None
        LAST_SPEECH_END = time.monotonic()
        return False
def _read_audio_levels(wav_path, window_seconds=0.05):
    """Return silence-gated, normalized RMS levels from PCM WAV audio."""
    import wave
    import numpy as np

    with wave.open(str(wav_path), "rb") as audio:
        channels = audio.getnchannels()
        sample_rate = audio.getframerate()
        sample_width = audio.getsampwidth()
        frames = audio.getnframes()
        raw = audio.readframes(frames)

    if sample_width != 2:
        raise ValueError(f"Expected 16-bit PCM WAV, got {sample_width * 8}-bit")

    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    samples /= 32768.0

    window_size = max(1, int(sample_rate * window_seconds))
    rms_values = []

    for offset in range(0, len(samples), window_size):
        chunk = samples[offset:offset + window_size]
        if len(chunk):
            rms_values.append(float(np.sqrt(np.mean(chunk * chunk))))

    peak = max(rms_values, default=0.0)
    if peak <= 1e-6:
        return [0.0 for _ in rms_values]

    # Use both an absolute silence floor and a relative peak threshold.
    # This prevents very quiet audio/noise from keeping the mouth open.
    silence_floor = max(0.004, peak * 0.025)
    levels = []

    for value in rms_values:
        if value < silence_floor:
            levels.append(0.0)
        else:
            normalized = max(0.0, min(1.0, value / peak))
            levels.append(normalized)

    return levels

def _run_mouth_timeline(process, levels, temp_dir, generation, window_seconds=0.05):
    """Send audio-derived mouth activity while afplay is running, then clean up."""
    global SPEECH_PROCESS, LAST_SPEECH_END
    started = time.monotonic()
    try:
        for index, level in enumerate(levels):
            deadline = started + index * window_seconds
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
            if process.poll() is not None:
                break
            _emit_gui("mouth", f"{level:.4f}")
        process.wait()
    except Exception as exc:
        print(f"[NEXA] Lip-sync timeline warning: {type(exc).__name__}: {exc}", flush=True)
    finally:
        _emit_gui("mouth", "0.0")
        with SPEECH_LOCK:
            if SPEECH_PROCESS is process:
                SPEECH_PROCESS = None
            LAST_SPEECH_END = time.monotonic()
        try:
            temp_dir.cleanup()
        except Exception:
            pass



def _prepare_speech_text(message):
    """Remove display-only formatting while preserving the original wording."""
    spoken = str(message)
    spoken = re.sub(r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF]", "", spoken)

    # Keep link labels but omit long URLs from spoken responses.
    spoken = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", spoken)

    # Remove Markdown headings, code fences, emphasis, and inline code markers.
    spoken = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", spoken)
    spoken = re.sub(r"```[a-zA-Z0-9_+-]*", "", spoken)
    spoken = re.sub(r"(?<!\w)(\*\*|__)(.+?)\1(?!\w)", r"\2", spoken)
    spoken = re.sub(r"(?<!\w)(\*|_)(.+?)\1(?!\w)", r"\2", spoken)
    spoken = re.sub(r"`([^`]*)`", r"\1", spoken)

    # Remove list markers without removing the words that follow them.
    spoken = re.sub(r"(?m)^\s*[-*+]\s+", "", spoken)
    spoken = re.sub(r"(?m)^\s*\d+[.)]\s+", "", spoken)

    # Convert layout-only separators into natural pauses.
    spoken = re.sub(r"(?m)^\s*[-=_]{3,}\s*$", "", spoken)
    spoken = spoken.replace("|", ", ")
    spoken = re.sub(r"\n+", ". ", spoken)
    spoken = re.sub(r"\s+([,.;:!?])", r"\1", spoken)
    spoken = re.sub(r"([,;:])(?=\S)", r"\1 ", spoken)
    spoken = re.sub(r"\s{2,}", " ", spoken)

    return spoken.strip(" \t\r\n")



def speak(message, version=None):
    """Speak with Samantha at the configured rate and drive mouth poses from audio RMS."""
    global SPEECH_PROCESS, SPEECH_GENERATION
    message = str(message).strip()
    speech_message = _prepare_speech_text(message)
    if not message or not speech_message:
        return
    with STATE_LOCK:
        if version is not None and version != COMMAND_VERSION:
            return
    stop_speech()
    print(f"NEXA: {message}", flush=True)
    _emit_gui("reply", message)

    # Build the audio before announcing Speaking, so the timeline is ready at playback start.
    temp_dir = tempfile.TemporaryDirectory(prefix="nexa_lipsync_")
    temp_path = Path(temp_dir.name)
    aiff_path = temp_path / "speech.aiff"
    wav_path = temp_path / "speech.wav"
    try:
        subprocess.run(
            ["say", "-v", VOICE, "-r", RATE, "-o", str(aiff_path), speech_message],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", "LEI16", str(aiff_path), str(wav_path)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        levels = _read_audio_levels(wav_path)
        if not levels:
            raise ValueError("No audio samples were generated")
    except Exception as exc:
        temp_dir.cleanup()
        print(f"[NEXA] Audio-driven lip-sync unavailable; using normal speech: {type(exc).__name__}: {exc}", flush=True)
        _emit_gui("status", "Speaking")
        with SPEECH_LOCK:
            SPEECH_GENERATION += 1
            SPEECH_PROCESS = subprocess.Popen(
                ["say", "-v", VOICE, "-r", RATE, speech_message],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        return

    _emit_gui("status", "Speaking")
    with SPEECH_LOCK:
        SPEECH_GENERATION += 1
        generation = SPEECH_GENERATION
        process = subprocess.Popen(
            ["afplay", str(wav_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        SPEECH_PROCESS = process
    threading.Thread(
        target=_run_mouth_timeline,
        args=(process, levels, temp_dir, generation),
        daemon=True,
    ).start()
def acquire_single_instance():
    global LOCK_HANDLE
    path = Path(tempfile.gettempdir()) / "nexa_voice_assistant.lock"
    LOCK_HANDLE = open(path, "w")
    try:
        fcntl.flock(LOCK_HANDLE.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("NEXA is already running. Use its existing Terminal window.", flush=True)
        sys.exit(0)
def normalize(text):
    return re.sub(r"\s+", " ", text.strip().lower()).strip(" .,!?।")
def remove_wake(text):
    return re.sub(
        r"^\s*(?:hey\s+)?nexa(?:[\s,:-]+|$)",
        "",
        text,
        count=1,
        flags=re.I,
    ).strip()
def _standby_wake_match(text):
    cleaned = text.strip(" \t\r\n.,!?।:;-").lower()
    return bool(re.fullmatch(
        r"(?:hey\s+)?nexa(?:[\s,:-]+(?:wake\s+up|wake|activate|start listening))?",
        cleaned,
        re.I,
    ))
def _strip_wake_phrase(text):
    return re.sub(
        r"^\s*(?:hey\s+)?nexa(?:\s*[,:-]\s*|\s+)(?:wake\s+up|wake|activate|start listening)\b[\s,:-]*",
        "",
        text,
        count=1,
        flags=re.I,
    ).strip()
def _new_command_version():
    global COMMAND_VERSION
    with STATE_LOCK:
        COMMAND_VERSION += 1
        return COMMAND_VERSION
def _speak_current(message, version):
    speak(message, version=version)
def _confirm_action(result, version):
    """Say a short confirmation for known successful actions; keep errors concise."""
    message = str(result or "").strip()
    lowered = message.lower()
    failure_phrases = (
        "couldn't", "could not", "failed", "error:", "refused",
        "don't recognize", "do not recognize", "permission denied",
        "unable to", "not available", "didn't work", "need the free", "install it",
    )
    if not message or any(phrase in lowered for phrase in failure_phrases):
        _speak_current(message or "Sorry, that action could not be completed.", version)
        return
    _speak_current("Okay, Boss.", version)
def _volume_command(command, version):
    """Handle exact and relative volume commands without asking the AI model."""
    percent_match = re.search(
        r"\b(\d{1,3})\s*(?:percent\b|per\s+cent\b|%)|"
        r"\b(?:volume|to)\s+(\d{1,3})\b",
        command,
    )
    if percent_match:
        value = int(next(group for group in percent_match.groups() if group is not None))
        result = change_system_volume("set", max(0, min(100, value)))
        _confirm_action(result, version)
        return
    if re.search(r"\b(mute|silent|zero)\b|\bvolume off\b|बंद कर", command):
        _confirm_action(change_system_volume("set", 0), version)
        return
    if re.search(r"\b(unmute|full|maximum|max|full volume)\b|फुल कर", command):
        _confirm_action(change_system_volume("set", 100), version)
        return
    if re.search(r"\bhalf\b|आधी आवाज", command):
        _confirm_action(change_system_volume("set", 50), version)
        return
    direction = "up" if re.search(
        r"\b(up|increase|higher|louder|raise|high|more|turn up)\b|"
        r"बढ़ा|बढ़ाओ|ज्यादा|ऊपर",
        command,
    ) else "down"
    _confirm_action(change_system_volume(direction), version)
def route_command(text, version=None):
    global AWAKE
    if version is None:
        version = _new_command_version()
    raw = text.strip()
    command = normalize(remove_wake(raw))

    # FAST, DETERMINISTIC BROWSER/MEDIA ROUTING (kept before the local AI fallback).
    # Search on YouTube must be checked before the generic "search ..." Google rule.
    yt_search = re.match(
        r"^(?:please\s+)?(?:search|look\s+up)\s+(.+?)\s+(?:on|in)\s+youtube$|"
        r"^(?:youtube\s+(?:search|par\s+search))\s+(.+)$|"
        r"^(?:search|look\s+up)\s+youtube\s+for\s+(.+)$",
        command, re.I,
    )
    if yt_search:
        query = next((g for g in yt_search.groups() if g), "").strip()
        _confirm_action(search_youtube(query), version)
        return

    # "Play <title> on YouTube" and Hindi/Hinglish variants.
    yt_play = re.match(
        r"^(?:please\s+)?(?:play|put\s+on|start|chalao|chala\s+do)\s+(.+?)"
        r"(?:\s+(?:on|in)\s+youtube)?(?:\s+please|\s+for\s+me)?$",
        command, re.I,
    )
    if yt_play and re.search(r"\b(on|in)\s+youtube\b|\b(play|put on|start)\b|chala", command, re.I):
        query = yt_play.group(1).strip(" \t\"'“”‘’.,!")
        query = re.sub(r"^(?:the\s+)?(?:song|track)\s+", "", query, flags=re.I).strip()
        if query and query not in {"music", "song", "video"}:
            _speak_current(play_song(query), version)
            return

    # Direct Google search/open, including "open Antarctica on Google".
    google_query = re.match(
        r"^(?:please\s+)?(?:open|search|look\s+up|find|show)\s+(.+?)\s+(?:on|in)\s+google$",
        command, re.I,
    )
    if not google_query:
        google_query = re.match(
            r"^(?:please\s+)?(?:google\s+(?:search\s+)?|search\s+on\s+google\s+for\s+)(.+)$",
            command, re.I,
        )
    if not google_query:
        google_query = re.match(
            r"^(?:google\s+par|google\s+pe)\s+(.+?)\s+(?:search|dhoondo|khojo)(?:\s+karo|\s+kar\s+do)?$",
            command, re.I,
        )
    if google_query:
        query = google_query.group(1).strip(" \t\"'“”‘’.,!")
        if query:
            _confirm_action(search_google(query), version)
            return

    # Media controls: direct actions, no LLM round-trip.
    if re.search(r"\b(pause|pause the music|pause the song|pause the video|stop the music|stop the song|stop the video)\b|गाना रोक|म्यूजिक रोक|वीडियो रोक", command, re.I):
        _confirm_action(player_action("pause"), version)
        return
    if re.search(r"\b(resume|continue playing|unpause|start playing again)\b|फिर से चलाओ|दोबारा चलाओ|चलाते रहो", command, re.I):
        _confirm_action(player_action("resume"), version)
        return
    if re.search(r"\b(next(?:\s+(?:song|video|track))?|skip to next)\b|अगला गाना|अगला सॉन्ग|नेक्स्ट गाना|agla gana|next song", command, re.I):
        _confirm_action(player_action("next"), version)
        return
    if re.search(r"\b(previous|prev|previous song|previous video|go back|last song)\b|पिछला गाना|पिछला सॉन्ग|पिछला गाना चलाओ|pichla gana|pichhla gana", command, re.I):
        _confirm_action(player_action("previous"), version)
        return
    if re.search(r"\b(skip forward|fast forward|forward 10 seconds)\b|आगे चलाओ", command, re.I):
        _confirm_action(player_action("forward"), version)
        return
    if re.search(r"\b(skip back|rewind|back 10 seconds)\b|पीछे चलाओ", command, re.I):
        _confirm_action(player_action("back"), version)
        return

    # System volume, including exact percentages and Hindi/Hinglish commands.
    volume_words = (
        r"\b(volume|sound|louder|quieter|mute|unmute)\b|"
        r"आवाज|आवाज़|वॉल्यूम|awaaz|awaz|"
        r"\b\d{1,3}\s*(?:%|percent\b|per\s+cent\b)|"
        r"\b\d{1,3}\s+(?:kar\s+do|karna|set\s+kar\s+do)\b"
    )
    if re.search(volume_words, command, re.I):
        _volume_command(command, version)
        return

    # Screen brightness, including Hindi/Hinglish commands.
    if re.search(r"\b(brightness|screen light|display light)\b|ब्राइटनेस|रोशनी|screen chamak|screen light", command, re.I):
        direction = "up" if re.search(r"\b(up|increase|higher|brighter|raise|more)\b|zyada|jyada|badhao|badhhao|बढ़ा|बढ़ाओ|ज्यादा|ज़्यादा|तेज|तेज़", command, re.I) else "down"
        _confirm_action(change_brightness(direction), version)
        return

    if not command:
        _speak_current("I'm listening, Boss.", version)
        return
    # Standby / wake controls.
    if command in {"exit", "quit", "stop listening", "standby", "go to standby", "sleep", "go to sleep"}:
        with STATE_LOCK:
            AWAKE = False
        stop_speech()
        _speak_current("Going into standby. Say NEXA, wake up when you need me.", version)
        return
    if command in {"wake up", "wake", "activate", "start listening"}:
        with STATE_LOCK:
            AWAKE = True
        _speak_current("I'm awake and listening.", version)
        return
    if command in {"stop", "stop speaking", "be quiet", "quiet", "चुप", "चुप हो जाओ", "रुको"}:
        stop_speech()
        return
    if command in {"how are you", "how are you doing", "how r you"}:
        _speak_current("I'm doing well, Boss. How are you?", version)
        return
    math_answer = calculate_spoken(command)
    if math_answer:
        _speak_current(math_answer, version)
        return
    # Window controls.
    if re.search(r"\b(minimi[sz]e|hide)\b", command) and re.search(r"\b(window|screen|brave|browser|youtube)\b", command):
        _confirm_action(control_brave_window("minimize"), version)
        return
    if re.search(r"\b(maximi[sz]e|restore)\b", command) and re.search(r"\b(window|screen|brave|browser|youtube)\b", command):
        _confirm_action(control_brave_window("maximize"), version)
        return
    # Brightness: exact percentages first, then relative up/down controls.
    brightness_intent = re.search(
        r"\b(brightness|screen light|display light)\b|\blight\b|ब्राइटनेस|रोशनी",
        command,
    )
    if brightness_intent:
        percent_match = re.search(
            r"\b(\d{1,3})\s*(?:%|percent\b|per\s+cent\b)",
            command,
            re.I,
        )
        if percent_match:
            level = max(0, min(100, int(percent_match.group(1))))
            _confirm_action(change_brightness("set", level=level), version)
            return

        direction = "up" if re.search(
            r"\b(up|increase|higher|brighter|raise|high|more|turn up)\b|"
            r"badhao|badh|zyada|jyada|kam kar|kam karo|kardo|"
            r"बढ़ा|बढ़ाओ|ज्यादा|ज़्यादा|ऊपर|तेज़",
            command,
        ) else "down"
        _confirm_action(change_brightness(direction), version)
        return
    # Volume controls: execute directly and only confirm briefly.
    volume_intent = re.search(
        r"\b(volume|louder|quieter|mute|unmute)\b|"
        r"\b(turn|set) (?:the )?volume\b|"
        r"\b(sound)\b|आवाज|आवाज़|वॉल्यूम",
        command,
    )
    if volume_intent:
        _volume_command(command, version)
        return
    # PAGE SUMMARY COMMANDS
    if re.search(
        r"\b(?:summari[sz]e|read|explain)\s+(?:this\s+)?(?:web)?page\b|"
        r"\b(?:summari[sz]e|read)\s+(?:this\s+)?article\b|"
        r"(?:इस पेज|इस आर्टिकल|इस वेबपेज).*(?:समझाओ|समरी|सारांश|पढ़कर बताओ)",
        command,
        re.I,
    ):
        _speak_current("I'll try to read the current webpage and summarize it.", version)
        page, error = get_current_page_content()
        if error:
            _speak_current(error, version)
            return
        url, page_text = page
        prompt = (
            "Summarize the following webpage in the language the user normally uses. "
            "Use only information present in the extracted page text. "
            "Start with the main point, then give 3 to 5 useful details. "
            "If the text is incomplete, explicitly say so. Keep the spoken answer concise.\\n\\n"
            f"Page URL: {url}\\n"
            f"Extracted webpage text:\\n{page_text}"
        )
        summary = ask_nexa(prompt)
        _speak_current(summary, version)
        return

    # MULTI-SOURCE RESEARCH COMMANDS
    research_match = re.match(
        r"^(?:please\s+)?(?:research|investigate|compare\s+sources\s+on|"
        r"do\s+research\s+on|search\s+multiple\s+sources\s+for)\s+(.+)$",
        command,
        re.I,
    )
    if research_match:
        topic = research_match.group(1).strip(" \t\"'“”")
        if not topic:
            _speak_current("Tell me what topic you want me to research.", version)
            return

        _speak_current("I'll research this topic across public sources using my local AI.", version)
        result = research_google(topic, max_sources=3)
        sources = result.get("sources", [])
        if not sources:
            _speak_current(
                "I couldn't retrieve readable content from enough public sources. Try a more specific topic or a different search phrase.",
                version,
            )
            return

        source_text = []
        for index, source in enumerate(sources, 1):
            print(f"[NEXA Research {index}] {source['title'] or source['url']}", flush=True)
            print(f"[NEXA Source URL] {source['url']}", flush=True)
            source_text.append(
                f"SOURCE {index}\n"
                f"Title: {source['title']}\n"
                f"URL: {source['url']}\n"
                f"Extracted text: {source['content']}"
            )

        prompt = (
            "You are NEXA, a careful research assistant. Use only the source material below. "
            "Give a concise answer in natural Hindi/Hinglish when appropriate, otherwise use the user's language. "
            "First explain the main finding, then give 3 to 5 useful points. "
            "Compare sources where they agree or disagree. Do not invent facts, statistics, dates, or citations. "
            "If sources are insufficient or contradictory, clearly say so. "
            "Mention the source titles or domains in the answer, and remember the full source URLs are printed in the terminal.\n\n"
            f"Research question: {topic}\n\n"
            + "\n\n".join(source_text)
        )
        try:
            summary = ask_nexa(prompt)
            if not summary or not str(summary).strip():
                summary = "I found sources, but my local model didn't return a usable summary."
            _speak_current(str(summary), version)
        except Exception as exc:
            print(f"[NEXA] Local research summary failed: {type(exc).__name__}", flush=True)
            _speak_current("I collected sources, but the local AI couldn't summarize them this time.", version)
        return

    # GOOGLE SEARCH COMMANDS
    # SMART GOOGLE FOLLOW-UPS AND RESULT SELECTION
    result_match = re.search(
        r"\b(?:open|click)\s+(?:the\s+)?(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+(?:link|result|one)\b|"
        r"\b(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+(?:link|result)\s+(?:open|kholo|khol\s+do)\b|"
        r"\b(?:पहला|पहली|पहले|दूसरा|दूसरी|दूसरे|तीसरा|तीसरी|चौथा|चौथी|पांचवां|पाँचवाँ)\s+(?:लिंक|रिजल्ट|result)\s+(?:खोलो|खोल\s+दो)\b",
        command,
        re.I,
    )
    if result_match:
        spoken_number = next((g for g in result_match.groups() if g), "")
        numbers = {
            "first": 1, "1st": 1, "second": 2, "2nd": 2,
            "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
            "fifth": 5, "5th": 5,
            "पहला": 1, "पहली": 1, "पहले": 1,
            "दूसरा": 2, "दूसरी": 2, "दूसरे": 2,
            "तीसरा": 3, "तीसरी": 3, "चौथा": 4, "चौथी": 4,
            "पांचवां": 5, "पाँचवाँ": 5,
        }
        _confirm_action(open_google_result(numbers[spoken_number]), version)
        return

    if re.search(
        r"\b(?:search more about this|more details about this|"
        r"search more|look for more details|tell me more about this)\b|"
        r"(?:iske baare mein|is topic par|is topic ke baare mein).*(?:aur search|detail|jaankari)|"
        r"(?:इसके बारे में|इस टॉपिक पर).*(?:और सर्च|डिटेल|जानकारी)",
        command,
        re.I,
    ):
        _confirm_action(search_google_followup(), version)
        return

    if re.search(
        r"^(?:google\s+)?(?:next page|next results|show more results)$|"
        r"^(?:google\s+)?(?:अगला पेज|अगले रिजल्ट)$",
        command,
    ):
        _confirm_action(next_google_results(), version)
        return

    if re.search(
        r"^(?:open|launch|show)\s+google$|"
        r"^(?:google kholo|google khol do|google open karo|"
        r"गूगल खोलो|गूगल खोल दो)$",
        command,
    ):
        _confirm_action(open_google(), version)
        return

    google_match = re.match(
        r"^(?:google\s+(?:pe|par|on)\s+)(.+?)\s+search"
        r"(?:\s+karo|\s+kar\s+do)?$",
        command,
        re.I,
    )
    if not google_match:
        google_match = re.match(
            r"^(?:search|look up)\s+(.+?)\s+on\s+google$",
            command,
            re.I,
        )
    if not google_match:
        google_match = re.match(
            r"^google\s+(?:and\s+)?search(?:\s+for)?\s+(.+)$",
            command,
            re.I,
        )
    if not google_match:
        google_match = re.match(
            r"^search(?:\s+for)?\s+(.+)$",
            command,
            re.I,
        )
    if google_match:
        _confirm_action(search_google(google_match.group(1).strip()), version)
        return

    if re.search(r"\b(open youtube|youtube kholo)\b", command):
        _confirm_action(open_youtube(), version)
        return
    # YouTube searches.
    match = re.match(r"^(?:open\s+)?youtube\s+(?:and\s+)?search\s+(.+)$", command, re.I)
    if match:
        _confirm_action(search_youtube(match.group(1).strip(" \t\"'“”")), version)
        return
    match = re.match(r"^(?:search|look up)\s+(?:youtube for\s+)?(.+)$", command, re.I)
    if match and "news" not in command:
        _confirm_action(search_youtube(match.group(1)), version)
        return
    if re.search(r"\b(news|headlines)\b", command) and re.search(r"\b(open|search|latest|show|find|tell me)\b", command):
        query = re.sub(r"\b(open|search|latest|show|find|tell me|the|news|headlines)\b", " ", command).strip() or "latest news"
        _confirm_action(search_news(query), version)
        return
    # Playback controls. YouTube uses the same K shortcut for pause/resume.
    playback_intent = re.search(
        r"\b(pause|resume|continue|previous|prev|skip forward|skip back|rewind|fast forward)\b|"
        r"^\s*next(?:\s+(?:song|video))?\s*$|"
        r"\bstop (?:the )?(?:song|music|video)\b|"
        r"गाना रोक|फिर से चलाओ|अगला गाना|पिछला गाना|अगला गाना चलाओ|पिछला गाना चलाओ|"
        r"pichla gana|pichhla gana|agla gana| अगला सॉन्ग",
        command,
    )
    if playback_intent:
        if re.search(r"\b(next(?:\s+song|\s+video)?|agla gana)\b|अगला गाना|अगला सॉन्ग", command):
            action = "next"
        elif re.search(r"\b(previous(?:\s+song|\s+video)?|prev(?:ious)?(?:\s+song)?|go back|pichhla? gana)\b|पिछला गाना|पिछला सॉन्ग", command):
            action = "previous"
        elif re.search(r"\b(skip forward|fast forward)\b", command):
            action = "forward"
        elif re.search(r"\b(skip back|rewind)\b", command):
            action = "back"
        elif re.search(r"\b(resume|continue|play)\b|फिर से चलाओ", command):
            action = "resume"
        else:
            action = "pause"
        _confirm_action(player_action(action), version)
        return
    # Play a named song; this response can identify the selected result.
    music_match = re.match(
        r"^(?:please\s+)?(?:play|put on|listen to)\s+(?:(?:the|a)\s+)?(?:song\s+)?(.+)$",
        remove_wake(raw),
        re.I,
    )
    if music_match:
        query = music_match.group(1).strip(" \t\"'“”‘’.,!")
        query = re.sub(r"^(?:song|track)\s+", "", query, flags=re.I).strip()
        query = re.sub(r"\s+(?:please|for me)$", "", query, flags=re.I).strip()
        _speak_current(play_song(query), version)
        return
    if command in {"thank you", "thanks", "thank you nexa"}:
        _speak_current("You're welcome, Boss.", version)
        return
    # AI conversation: use existing local-first engine and short conversation history.
    answer = ask_nexa(raw, get_history())
    with STATE_LOCK:
        if version != COMMAND_VERSION:
            return
        if answer:
            add_exchange(raw, answer)
    if answer:
        _speak_current(answer, version)
    else:
        _speak_current("Sorry, I couldn't prepare an answer just now.", version)
def _process_command(text, version):
    global COMMAND_BUSY
    try:
        route_command(text, version)
    except Exception as exc:
        print(f"[NEXA] Command error: {type(exc).__name__}: {exc}", flush=True)
        _speak_current("Sorry, I couldn't complete that command.", version)
    finally:
        # An older interrupted command must not clear the busy state
        # belonging to a newer command.
        with COMMAND_LOCK:
            if version == COMMAND_VERSION:
                COMMAND_BUSY = False
def _is_interrupt_command(text):
    command = normalize(remove_wake(text))
    return bool(re.search(
        r"\b(stop|stop speaking|be quiet|quiet|exit|standby|next(?: song| video)?|"
        r"previous(?: song| video)?|pause|resume|continue|volume|louder|quieter|"
        r"brightness|minimize|maximize|restore|mute|unmute)\b|"
        r"आवाज|आवाज़|वॉल्यूम|बढ़ाओ|कम करो|गाना रोक|फिर से चलाओ|रुको|चुप हो जाओ",
        command,
    ))
def listen_loop(stop_event=None, event_callback=None):
    """Keep one microphone session open and process each completed command once.
    While NEXA speaks, short audio chunks are consumed and discarded to prevent
    NEXA's own voice from becoming a new command. While a command is running,
    ordinary speech is discarded; only clear interrupt commands are accepted.
    """
    global AWAKE, COMMAND_BUSY, GUI_EVENT_CALLBACK, LISTENER_STOP_EVENT
    if event_callback is not None or stop_event is not None:
        configure_gui(event_callback, stop_event)
    stop_event = stop_event or LISTENER_STOP_EVENT
    def stopping():
        return stop_event is not None and stop_event.is_set()
    recognizer = sr.Recognizer()
    recognizer.dynamic_energy_threshold = True
    recognizer.pause_threshold = 0.60
    recognizer.non_speaking_duration = 0.45
    recognizer.operation_timeout = 8
    mic = sr.Microphone()
    print("Calibrating microphone...", flush=True)
    _emit_gui("status", "Calibrating microphone")
    # Keep the microphone stream open for the whole session instead of
    # opening/closing the device for every phrase.
    with mic as source:
        recognizer.adjust_for_ambient_noise(source, duration=0.8)
        print(
            "NEXA is ready. Speak commands in English. In standby, say 'NEXA, wake up'. Ctrl+C to stop.",
            flush=True,
        )
        _emit_gui("status", "Listening")
        while not stopping():
            try:
                with COMMAND_LOCK:
                    busy_at_capture_start = COMMAND_BUSY
                # During speech, listen for explicit NEXA interrupts only.
                captured_while_speaking = _speech_is_running()
                if captured_while_speaking:
                    try:
                        audio = recognizer.listen(source, timeout=1, phrase_time_limit=3)
                    except sr.WaitTimeoutError:
                        continue
                else:
                    try:
                        audio = recognizer.listen(source, timeout=1, phrase_time_limit=8)
                    except sr.WaitTimeoutError:
                        continue
                    if stopping() or _speech_is_running():
                        continue
                try:
                    heard = recognizer.recognize_google(audio, language="en-IN").strip()
                except sr.UnknownValueError:
                    continue
                except sr.RequestError:
                    print("Speech recognition needs an internet connection.", flush=True)
                    _emit_gui("status", "Speech recognition connection issue")
                    time.sleep(0.5)
                    continue
                explicit_wake = bool(
                    re.match(r"^\s*(?:hey\s+)?nexa\b", heard, re.I)
                )
                if captured_while_speaking:
                    normalized_heard = normalize(heard)
                    bare_stop = normalized_heard in {
                        "stop",
                        "stop speaking",
                        "be quiet",
                        "quiet",
                    }
                    # Accept a new question only when it explicitly addresses
                    # NEXA; also preserve the existing bare-stop behavior.
                    if not (
                        explicit_wake
                        or (
                            _is_interrupt_command(heard)
                            and bare_stop
                        )
                    ):
                        continue
                if not heard:
                    continue
                print(f"You: {heard}", flush=True)
                _emit_gui("transcript", heard)
                with STATE_LOCK:
                    awake_now = AWAKE
                if not awake_now:
                    if _standby_wake_match(heard):
                        with STATE_LOCK:
                            AWAKE = True
                        remainder = _strip_wake_phrase(heard)
                        if remainder:
                            with COMMAND_LOCK:
                                if COMMAND_BUSY:
                                    continue
                                COMMAND_BUSY = True
                            version = _new_command_version()
                            _emit_gui("status", "Processing voice command")
                            threading.Thread(
                                target=_process_command,
                                args=(remainder, version),
                                daemon=True,
                            ).start()
                        else:
                            version = _new_command_version()
                            speak("I'm awake and listening, Boss.", version)
                    else:
                        _emit_gui("status", "Standby — say NEXA, wake up")
                    continue
                # An explicit wake phrase plus a new question interrupts
                # the current answer, even if the question is not a control.
                interrupt = _is_interrupt_command(heard) or (
                    captured_while_speaking and explicit_wake
                )
                # If a command was already running when this phrase began,
                # never replay it as a fresh action after the command finishes.
                # Only explicit stop/pause/volume/window controls may pass.
                if busy_at_capture_start and not interrupt:
                    continue
                with COMMAND_LOCK:
                    busy_now = COMMAND_BUSY
                    if busy_now and not interrupt:
                        continue
                    if not busy_now:
                        COMMAND_BUSY = True
                if busy_now and interrupt:
                    version = _new_command_version()
                    stop_speech()
                else:
                    version = _new_command_version()
                _emit_gui("status", "Processing voice command")
                threading.Thread(
                    target=_process_command,
                    args=(heard, version),
                    daemon=True,
                ).start()
            except KeyboardInterrupt:
                stop_speech()
                print("\nNEXA stopped.", flush=True)
                break
            except Exception as exc:
                import traceback
                print(
                    f"[NEXA] Recoverable error: {type(exc).__name__}: {exc}",
                    flush=True,
                )
                traceback.print_exc()
                _emit_gui("error", f"{type(exc).__name__}: {exc}")
                time.sleep(0.3)
    stop_speech()
    _emit_gui("status", "Voice listener stopped")
if __name__ == "__main__":
    acquire_single_instance()
    try:
        listen_loop()
    except Exception as exc:
        stop_speech()
        print(f"Could not start microphone listener: {exc}", flush=True)
        print(
            "Check Terminal's Microphone permission and the speech_recognition/PyAudio installation.",
            flush=True,
        )
        sys.exit(1)
