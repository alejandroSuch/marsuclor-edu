import copy
import os
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from tts import (PiperSynthesizer, apply_pronunciations, audio_path, build_audio,
                 context, paced_events, play_audio, resolve_tts_config, resolve_voice,
                 run_command, silence_wav, wav_duration)  # noqa: E402
from tts_compare import comparison_excerpt, profile_plan  # noqa: E402


class FakeSynthesizer:
    def synthesize(self, text, path):
        # A fixed duration makes the sample and concatenation deterministic.
        with wave.open(str(path), "wb") as sound:
            sound.setnchannels(1)
            sound.setsampwidth(2)
            sound.setframerate(1000)
            sound.writeframes(b"\x01\x00" * (1000 * 10))


class TTSTests(unittest.TestCase):
    def setUp(self):
        self.config = yaml.safe_load((ROOT / "config/subjects.yml").read_text())
        self.settings = resolve_tts_config(self.config, "geografia", {})
        self.ref = "geografia/1-eso/els-fonaments-del-planeta-terra"

    def test_accepted_valencian_defaults(self):
        self.assertEqual(resolve_voice(self.settings, "ca-valencia"), "ca_ES-upc_ona-medium")
        self.assertEqual(self.settings["loudness_lufs"], -20)
        self.assertEqual(self.settings["length_scale"], 1.0)
        self.assertEqual(self.settings["sentence_pause_seconds"], 0.5)
        self.assertEqual(self.settings["paragraph_pause_seconds"], 0.8)

    def test_voice_resolution_and_language_configuration(self):
        self.assertEqual(resolve_voice(self.settings, "ca-valencia"), "ca_ES-upc_ona-medium")
        self.assertEqual(resolve_voice(self.settings, "es"), "es_ES-davefx-medium")
        with self.assertRaisesRegex(ValueError, "No hi ha una veu"):
            resolve_voice(self.settings, "fr")
        changed = copy.deepcopy(self.config)
        changed["subjects"]["geografia"]["materials"] = {"podcast": {"tts": {"length_scale": 1.2}}}
        topic = {"materials": {"podcast": {"tts": {"voices": {"ca-valencia": {"model": "ca_ES-upc_pau-x_low"}}}}}}
        resolved = resolve_tts_config(changed, "geografia", topic)
        self.assertEqual(resolved["length_scale"], 1.2)
        self.assertEqual(resolve_voice(resolved, "ca-valencia"), "ca_ES-upc_pau-x_low")
        self.assertEqual(resolve_voice(resolved, "es"), "es_ES-davefx-medium")
        bad = copy.deepcopy(resolved)
        bad["voices"]["ca-valencia"]["model"] = "es_ES-davefx-medium"
        with self.assertRaisesRegex(ValueError, "no correspon"):
            resolve_voice(bad, "ca-valencia")
        changed["materials"]["podcast"]["tts"]["audio_format"] = "wav"
        with self.assertRaisesRegex(ValueError, "mp3"):
            resolve_tts_config(changed, "geografia", {})

    def test_segments_pronunciation_and_no_metadata(self):
        _, _, _, events, _ = context(ROOT, self.ref)
        self.assertTrue(events)
        self.assertEqual(events[0]["type"], "speech")
        self.assertNotIn("##", events[0]["text"])
        self.assertNotIn("source_ids", events[0]["text"])
        self.assertIn("quan Ix el Sol", events[0]["text"])
        self.assertIn("quan ix el Sol", (ROOT / "content" / self.ref / "podcast" / "script.md").read_text())
        self.assertTrue(any(event["type"] == "pause" for event in events))
        self.assertEqual(apply_pronunciations("Pangea i Pangea. Pangeana", {"Pangea": "Pan-ge-a"}),
                         "Pan-ge-a i Pan-ge-a. Pangeana")
        self.assertEqual([event["text"] for event in paced_events([
            {"type": "speech", "text": "Primera frase. Segona frase!"}
        ], self.settings) if event["type"] == "speech"], ["Primera frase.", "Segona frase!"])

    def test_sentence_and_paragraph_pauses_skip_explicit_pause(self):
        events = [{"type": "speech", "text": "Una frase. Una altra."},
                  {"type": "speech", "text": "Paràgraf següent."},
                  {"type": "pause", "duration_seconds": 5},
                  {"type": "speech", "text": "Resposta."}]
        paced = paced_events(events, self.settings)
        self.assertEqual([event["type"] for event in paced],
                         ["speech", "pause", "speech", "pause", "speech", "pause", "speech"])
        self.assertEqual([event["duration_seconds"] for event in paced if event["type"] == "pause"],
                         [0.5, 0.8, 5])
        self.assertEqual([event.get("origin") for event in paced if event["type"] == "pause"],
                         ["sentence", "paragraph", None])

    def test_pause_wav_has_exact_duration_and_silence(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.wav"
            FakeSynthesizer().synthesize("test", source)
            output = Path(temp) / "pause.wav"
            silence_wav(output, 4.5, source)
            self.assertEqual(wav_duration(output), 4.5)
            with wave.open(str(output), "rb") as sound:
                self.assertEqual(set(sound.readframes(sound.getnframes())), {0})

    def test_audio_build_and_error_handling_without_piper(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "podcast.mp3"
            seen = []

            def fake_runner(command):
                seen.append(command)
                if "print_format=json" in " ".join(command):
                    return subprocess.CompletedProcess(command, 0, stderr='{"input_i":"-20","input_tp":"-2","input_lra":"3","input_thresh":"-30","target_offset":"0"}')
                if command[0] == "ffmpeg" and command[-1].endswith(".mp3"):
                    manifest = Path(command[command.index("-i") + 1]).read_text()
                    self.assertEqual(manifest.count("file '"), 3)
                    Path(command[-1]).write_bytes(b"fake mp3")

            events = [{"type": "speech", "text": "Una frase."},
                      {"type": "pause", "duration_seconds": 3.5},
                      {"type": "speech", "text": "Una altra."}]
            result = build_audio(events, output, self.settings, FakeSynthesizer(), runner=fake_runner)
            self.assertTrue(output.is_file())
            self.assertEqual(result["pause_seconds"], 3.5)
            self.assertEqual(result["pause_count"], 1)
            self.assertEqual(len(seen), 3)  # Analyze, encode and decode check.
            self.assertIn("measured_I", " ".join(seen[1]))
            with self.assertRaisesRegex(ValueError, "Pausa abans"):
                build_audio([events[1], events[0]], output, self.settings, FakeSynthesizer(), runner=fake_runner)
            with self.assertRaisesRegex(ValueError, "model"):
                PiperSynthesizer(Path(temp) / "missing.onnx", self.settings)

    def test_sample_uses_complete_sentences(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "sample.mp3"
            def fake_runner(command):
                if "print_format=json" in " ".join(command):
                    return subprocess.CompletedProcess(command, 0, stderr='{"input_i":"-20","input_tp":"-2","input_lra":"3","input_thresh":"-30","target_offset":"0"}')
                if command[-1].endswith(".mp3"):
                    Path(command[-1]).write_bytes(b"fake mp3")
            events = [{"type": "speech", "text": "Primera frase. Segona frase. Tercera frase. Quarta frase."}]
            result = build_audio(events, output, self.settings, FakeSynthesizer(), sample=True, runner=fake_runner)
            self.assertEqual(result["duration_estimate"], 41.5)
            self.assertEqual(audio_path(ROOT, self.ref, sample=True).name, "sample.mp3")

    def test_missing_external_tool_has_clear_error(self):
        with self.assertRaisesRegex(ValueError, "No s'ha trobat"):
            run_command(["martina-tts-tool-that-does-not-exist"])

    def test_play_requires_file_and_uses_system_player(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.mp3"
            with self.assertRaisesRegex(ValueError, "No existeix"):
                play_audio(path)
            path.write_bytes(b"mp3")
            with patch("tts.sys.platform", "darwin"), patch("tts.run_command") as call:
                play_audio(path)
                call.assert_called_once_with(["afplay", str(path)])

    def test_comparison_uses_one_unchanged_paragraph_for_every_profile(self):
        folder, settings, topic, _, _ = context(ROOT, self.ref)
        policy = yaml.safe_load((ROOT / "config/subjects.yml").read_text())["materials"]["podcast"]
        excerpt = comparison_excerpt(folder, topic, policy, "Girar sobre si mateixa")
        self.assertTrue(excerpt.startswith("La Terra fa dos moviments principals."))
        self.assertIn("vint-i-quatre hores", excerpt)
        self.assertNotIn("##", excerpt)
        profiles = profile_plan(settings)
        self.assertEqual(len({profile["name"] for profile in profiles}), len(profiles))
        self.assertEqual(profiles[0]["model"], "ca_ES-upc_ona-medium")
        self.assertEqual(profiles[0]["settings"]["loudness_lufs"], -20)
        self.assertEqual(profiles[5]["settings"]["loudness_lufs"], -18)
        self.assertEqual(profiles[-1]["model"], "ca_ES-upc_pau-x_low")
        self.assertTrue(all(profile["settings"]["audio_format"] == "mp3" for profile in profiles))
        with self.assertRaisesRegex(ValueError, "No existeix"):
            comparison_excerpt(folder, topic, policy, "Capítol absent")


@unittest.skipUnless(os.getenv("MARTINA_TTS_INTEGRATION") == "1", "Integració Piper optativa")
class PiperIntegrationTests(unittest.TestCase):
    def test_ix_is_not_read_as_nine(self):
        from piper import PiperVoice
        from tts import model_path
        voice = PiperVoice.load(str(model_path(ROOT, "ca_ES-upc_ona-medium")))
        spoken = apply_pronunciations("quan ix el Sol", {"ix": "Ix"})
        phonemes = "".join(voice.phonemize(spoken)[0])
        self.assertIn("ˈiʃ", phonemes)
        self.assertNotIn("nˈɔw", phonemes)

    def test_real_sample(self):
        folder, settings, _, events, model = context(ROOT, "geografia/1-eso/els-fonaments-del-planeta-terra")
        from tts import model_path
        with tempfile.TemporaryDirectory() as temp:
            result = build_audio(events, Path(temp) / "sample.mp3", settings,
                                 PiperSynthesizer(model_path(ROOT, model), settings), sample=True)
            self.assertGreater(result["path"].stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
