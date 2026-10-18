"""Local podcast synthesis from the validated narration events."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import yaml

from podcast import parse_script, resolve_config, topic_folder


LANGUAGE_PREFIX = {"ca-valencia": "ca_ES-", "es": "es_ES-"}
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-Ú])")


def resolve_tts_config(config: dict, subject: str, topic: dict) -> dict:
    """Merge global, subject and topic TTS settings, including voice entries."""
    result: dict = {}
    voices: dict = {}
    for layer in (config, config["subjects"][subject], topic):
        podcast = layer.get("materials", {}).get("podcast", {})
        override = podcast.get("tts", {})
        if not isinstance(override, dict):
            raise ValueError("La configuració TTS ha de ser un mapa")
        entry = override.get("voices", {})
        if not isinstance(entry, dict):
            raise ValueError("Les veus TTS han de ser un mapa")
        for language, voice in entry.items():
            if not isinstance(voice, dict):
                raise ValueError(f"Veu TTS invàlida: {language}")
            voices[language] = {**voices.get(language, {}), **voice}
        result.update({key: value for key, value in override.items() if key != "voices"})
    result["voices"] = voices
    if result.get("engine") != "piper":
        raise ValueError("El motor TTS configurat no és compatible: cal piper")
    if result.get("audio_format") != "mp3":
        raise ValueError("El format d'àudio ha de ser mp3")
    for key, minimum, maximum in (
        ("length_scale", 0.5, 2.5), ("noise_scale", 0, 2),
        ("noise_w_scale", 0, 2), ("volume", 0.1, 3),
        ("loudness_lufs", -30, -12), ("true_peak_db", -6, -0.1),
        ("sample_seconds", 30, 45),
        ("sentence_pause_seconds", 0, 2), ("paragraph_pause_seconds", 0, 3),
    ):
        value = result.get(key)
        if type(value) not in (int, float) or not minimum <= value <= maximum:
            raise ValueError(f"Paràmetre TTS invàlid: {key}")
    if result.get("output_sample_rate") not in (22050, 44100, 48000):
        raise ValueError("Freqüència d'eixida TTS invàlida")
    if result.get("mp3_bitrate_kbps") not in (96, 128, 160, 192):
        raise ValueError("Qualitat MP3 invàlida")
    return result


def resolve_voice(tts_config: dict, language: str) -> str:
    voice = tts_config["voices"].get(language)
    if not isinstance(voice, dict) or not isinstance(voice.get("model"), str):
        raise ValueError(f"No hi ha una veu Piper configurada per a {language}")
    model = voice["model"]
    prefix = LANGUAGE_PREFIX.get(language)
    if not prefix or not model.startswith(prefix) or not re.fullmatch(r"[A-Za-z0-9_-]+", model):
        raise ValueError(f"La veu {model} no correspon a la llengua {language}")
    return model


def model_path(root: Path, model: str) -> Path:
    return root / "models" / "piper" / f"{model}.onnx"


def audio_path(root: Path, reference: str, sample: bool = False) -> Path:
    folder = root / "audio" / Path(reference)
    return folder / ("sample.mp3" if sample else "podcast.mp3")


def load_pronunciations(folder: Path) -> dict[str, str]:
    path = folder / "podcast" / "pronunciation.yml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("replacements"), dict):
        raise ValueError(f"Fitxer de pronunciació invàlid: {path}")
    result = data["replacements"]
    if any(not isinstance(key, str) or not key.strip() or not isinstance(value, str) or not value.strip()
           for key, value in result.items()):
        raise ValueError("Les substitucions de pronunciació han de ser text no buit")
    return result


def apply_pronunciations(text: str, replacements: dict[str, str]) -> str:
    if not replacements:
        return text
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(key) for key in sorted(replacements, key=len, reverse=True)) + r")(?!\w)")
    return pattern.sub(lambda match: replacements[match.group()], text)


def narration_events(parsed: dict, replacements: dict[str, str]) -> list[dict]:
    """Keep only speech and explicit pauses, preserving script order."""
    events = []
    for event in parsed["events"]:
        if event["type"] == "speech":
            events.append({"type": "speech", "text": apply_pronunciations(event["text"], replacements)})
        elif event["type"] == "pause":
            events.append(event.copy())
    if not any(event["type"] == "speech" for event in events):
        raise ValueError("El guió no conté text narrat")
    return events


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as sound:
        if sound.getnframes() == 0:
            raise ValueError("Piper ha generat un fragment buit")
        return sound.getnframes() / sound.getframerate()


def silence_wav(path: Path, seconds: float, example: Path) -> None:
    with wave.open(str(example), "rb") as source:
        params = source.getparams()
    frames = round(seconds * params.framerate)
    with wave.open(str(path), "wb") as target:
        target.setnchannels(params.nchannels)
        target.setsampwidth(params.sampwidth)
        target.setframerate(params.framerate)
        block = b"\x00" * (params.nchannels * params.sampwidth * 4096)
        while frames:
            count = min(frames, 4096)
            target.writeframesraw(block[:count * params.nchannels * params.sampwidth])
            frames -= count


def validate_wav_parts(parts: list[Path]) -> None:
    """Ensure every WAV can be concatenated without changing audio format."""
    expected_format = None
    for path in parts:
        with wave.open(str(path), "rb") as sound:
            audio_format = (sound.getnchannels(), sound.getsampwidth(), sound.getframerate())
        if audio_format[0:2] != (1, 2):
            raise ValueError("Els fragments TTS han de ser PCM mono de 16 bits")
        if expected_format is not None and audio_format != expected_format:
            raise ValueError("Els fragments TTS tenen formats diferents")
        expected_format = audio_format


def paced_events(events: list[dict], settings: dict) -> list[dict]:
    """Add measured gaps at sentence and paragraph boundaries, not beside script pauses."""
    result = []
    for index, event in enumerate(events):
        if event["type"] == "pause":
            result.append(event.copy())
            continue
        sentences = SENTENCE_END.split(event["text"])
        for sentence_index, sentence in enumerate(sentences):
            if not sentence:
                continue
            result.append({"type": "speech", "text": sentence})
            if sentence_index < len(sentences) - 1 and settings["sentence_pause_seconds"]:
                result.append({"type": "pause", "duration_seconds": settings["sentence_pause_seconds"],
                               "origin": "sentence"})
        if (index + 1 < len(events) and events[index + 1]["type"] == "speech"
                and settings["paragraph_pause_seconds"]):
            result.append({"type": "pause", "duration_seconds": settings["paragraph_pause_seconds"],
                           "origin": "paragraph"})
    return result


class PiperSynthesizer:
    def __init__(self, model: Path, settings: dict):
        if not model.is_file() or not Path(str(model) + ".json").is_file():
            raise ValueError(f"Falta el model {model.name} o el seu JSON. Executa tts download abans de sintetitzar.")
        try:
            from piper import PiperVoice, SynthesisConfig
        except ImportError as exc:
            raise ValueError("Falta Piper. Instal·la requirements-tts.txt en un entorn Python compatible.") from exc
        self.voice = PiperVoice.load(str(model), use_cuda=False)
        self.syn_config = SynthesisConfig(
            length_scale=settings["length_scale"], noise_scale=settings["noise_scale"],
            noise_w_scale=settings["noise_w_scale"], volume=settings["volume"],
            normalize_audio=True,
        )

    def synthesize(self, text: str, path: Path) -> None:
        with wave.open(str(path), "wb") as target:
            self.voice.synthesize_wav(text, target, syn_config=self.syn_config)


def run_command(command: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise ValueError(f"No s'ha trobat {command[0]}. Instal·la la dependència local.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "error desconegut").strip().splitlines()[-1]
        raise ValueError(f"Ha fallat {command[0]}: {detail}") from exc


def encode_mp3(parts: list[Path], output: Path, settings: dict, runner=run_command) -> None:
    manifest = parts[0].parent / "concat.txt"
    manifest.write_text("".join(f"file '{part.name}'\n" for part in parts), encoding="utf-8")
    temporary_output = output.with_name(output.name + ".part.mp3")
    try:
        target = f"I={settings['loudness_lufs']}:TP={settings['true_peak_db']}:LRA=11"
        analysis = runner([
            "ffmpeg", "-hide_banner", "-nostats", "-nostdin", "-y",
            "-f", "concat", "-safe", "0", "-i", str(manifest),
            "-af", f"loudnorm={target}:print_format=json", "-f", "null", "-",
        ])
        match = re.search(r'\{\s*"input_i"\s*:.*?\}', analysis.stderr, re.S)
        if not match:
            raise ValueError("FFmpeg no ha retornat les mesures de normalització")
        measured = json.loads(match.group())
        normalized = (f"loudnorm={target}:measured_I={measured['input_i']}"
                      f":measured_TP={measured['input_tp']}:measured_LRA={measured['input_lra']}"
                      f":measured_thresh={measured['input_thresh']}:offset={measured['target_offset']}"
                      ":linear=true")
        runner([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "concat", "-safe", "0", "-i", str(manifest),
            "-af", normalized,
            "-ar", str(settings["output_sample_rate"]), "-c:a", "libmp3lame",
            "-b:a", f"{settings['mp3_bitrate_kbps']}k", str(temporary_output),
        ])
        if not temporary_output.is_file() or temporary_output.stat().st_size == 0:
            raise ValueError("FFmpeg no ha produït cap MP3")
        runner(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-xerror",
                "-i", str(temporary_output), "-f", "null", "-"])
        temporary_output.replace(output)
    finally:
        temporary_output.unlink(missing_ok=True)


def build_audio(events: list[dict], output: Path, settings: dict, synthesizer,
                *, sample: bool = False, runner=run_command) -> dict:
    start = time.monotonic()
    chosen = paced_events(events, settings)
    output.parent.mkdir(parents=True, exist_ok=True)
    speech_seconds = 0.0
    pause_seconds = 0.0
    pause_count = 0
    explicit_pause_count = 0
    explicit_pause_seconds = 0.0
    last_event = None
    with tempfile.TemporaryDirectory(prefix="martina-tts-") as temporary:
        directory = Path(temporary)
        parts = []
        first_speech = None
        for event in chosen:
            if event["type"] == "speech":
                path = directory / f"{len(parts):04d}.wav"
                synthesizer.synthesize(event["text"], path)
                duration = wav_duration(path)
                if sample and speech_seconds + pause_seconds >= 30 and speech_seconds + pause_seconds + duration > 45:
                    path.unlink()
                    break
                parts.append(path)
                first_speech = first_speech or path
                speech_seconds += duration
                last_event = event
            elif event["type"] == "pause":
                if first_speech is None:
                    raise ValueError("Pausa abans de la primera narració")
                seconds = event["duration_seconds"]
                if sample and speech_seconds + pause_seconds + seconds > 45:
                    break
                path = directory / f"{len(parts):04d}.wav"
                silence_wav(path, seconds, first_speech)
                parts.append(path)
                pause_seconds += seconds
                pause_count += 1
                last_event = event
                if not event.get("origin"):
                    explicit_pause_count += 1
                    explicit_pause_seconds += seconds
            if sample and event["type"] == "speech" and speech_seconds + pause_seconds >= settings["sample_seconds"]:
                break
        if sample and parts and last_event and last_event["type"] == "pause":
            # A sample must end in speech; remove a pending gap if its next phrase would exceed 45 s.
            parts.pop().unlink()
            pause_seconds -= last_event["duration_seconds"]
            pause_count -= 1
            if not last_event.get("origin"):
                explicit_pause_count -= 1
                explicit_pause_seconds -= last_event["duration_seconds"]
        if not parts or (sample and not 30 <= speech_seconds + pause_seconds <= 45):
            raise ValueError("No es pot crear una mostra de 30–45 segons amb frases completes")
        validate_wav_parts(parts)
        encode_mp3(parts, output, settings, runner=runner)
    return {"path": output, "duration_estimate": speech_seconds + pause_seconds,
            "pause_seconds": pause_seconds, "pause_count": pause_count,
            "explicit_pause_count": explicit_pause_count, "explicit_pause_seconds": explicit_pause_seconds,
            "elapsed_seconds": time.monotonic() - start}


def context(root: Path, reference: str) -> tuple[Path, dict, dict, list[dict], str]:
    folder = topic_folder(root / "content", reference)
    topic = yaml.safe_load((folder / "topic.yml").read_text(encoding="utf-8"))
    config = yaml.safe_load((root / "config" / "subjects.yml").read_text(encoding="utf-8"))
    subject = reference.split("/")[0]
    policy = resolve_config(config, subject, topic)
    if policy["generation"] == "disabled":
        raise ValueError("La política del pòdcast està desactivada per a este tema")
    settings = resolve_tts_config(config, subject, topic)
    language = topic["teaching_language"]
    model = resolve_voice(settings, language)
    parsed = parse_script(folder / "podcast" / "script.md", topic, policy)
    events = narration_events(parsed, load_pronunciations(folder))
    return folder, settings, topic, events, model


def download_voice(root: Path, model: str) -> Path:
    directory = root / "models" / "piper"
    directory.mkdir(parents=True, exist_ok=True)
    run_command([sys.executable, "-m", "piper.download_voices", "--data-dir", str(directory), model])
    path = model_path(root, model)
    if not path.is_file() or not Path(str(path) + ".json").is_file():
        raise ValueError(f"La descàrrega de {model} no ha produït el model i el JSON")
    return path


def play_audio(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"No existeix {path}. Executa primer tts build o tts sample.")
    if sys.platform == "darwin":
        command = ["afplay", str(path)]
    elif sys.platform.startswith("linux"):
        player = next((name for name in ("ffplay", "mpg123", "xdg-open") if shutil.which(name)), None)
        if not player:
            raise ValueError("No hi ha reproductor MP3; instal·la ffplay o mpg123")
        command = [player, "-nodisp", "-autoexit", str(path)] if player == "ffplay" else [player, str(path)]
    else:
        raise ValueError(f"Sistema operatiu no compatible: {sys.platform}")
    run_command(command)
