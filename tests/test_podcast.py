import copy
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from podcast import parse_script, resolve_config, should_generate, spoken_text, topic_folder  # noqa: E402
from build import ui  # noqa: E402


class PodcastTests(unittest.TestCase):
    def setUp(self):
        self.config = yaml.safe_load((ROOT / "config/subjects.yml").read_text(encoding="utf-8"))
        self.ref = "geografia/1-eso/els-fonaments-del-planeta-terra"
        self.folder = topic_folder(ROOT / "content", self.ref)
        self.topic = yaml.safe_load((self.folder / "topic.yml").read_text(encoding="utf-8"))
        self.policy = resolve_config(self.config, "geografia", self.topic)

    def test_global_subject_topic_precedence_and_modes(self):
        self.assertEqual(resolve_config(self.config, "matematiques", {})["generation"], "on_demand")
        self.assertEqual(self.policy["generation"], "default")
        override = copy.deepcopy(self.topic)
        override["materials"] = {"podcast": {"generation": "disabled", "duration_minutes": 9}}
        result = resolve_config(self.config, "geografia", override)
        self.assertEqual((result["generation"], result["duration_minutes"], result["format"]),
                         ("disabled", 9, "narration"))
        self.assertFalse(should_generate(result, all_materials=True, explicit=True))
        self.assertTrue(should_generate(self.policy, all_materials=True))
        self.assertFalse(should_generate(self.policy, all_materials=True, script_exists=True))
        self.assertTrue(should_generate(self.policy, explicit=True, script_exists=True))
        self.assertFalse(should_generate(self.policy))
        demand = resolve_config(self.config, "matematiques", {})
        self.assertFalse(should_generate(demand, all_materials=True))
        self.assertTrue(should_generate(demand, explicit=True))

    def test_bad_config_and_topic_reference(self):
        bad = copy.deepcopy(self.config)
        bad["materials"]["podcast"]["generation"] = "always"
        with self.assertRaises(ValueError):
            resolve_config(bad, "musica", {})
        with self.assertRaises(ValueError):
            topic_folder(ROOT / "content", "../elsewhere")
        with self.assertRaises(ValueError):
            topic_folder(ROOT / "content", "geografia/1-eso/missing")

    def test_real_script_is_deterministic_and_read_only(self):
        path = self.folder / "podcast" / "script.md"
        before = path.read_bytes()
        other_materials = {name: (self.folder / name).read_bytes()
                           for name in ("notes.yml", "exercises.yml", "flashcards.yml")}
        parsed = parse_script(path, self.topic, self.policy)
        self.assertEqual(parsed, parse_script(path, self.topic, self.policy))
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(other_materials, {name: (self.folder / name).read_bytes()
                                           for name in other_materials})
        kinds = [event["type"] for event in parsed["events"]]
        self.assertEqual(kinds[0], "chapter")
        self.assertIn("pause", kinds)
        self.assertIn("speech", kinds)
        text = spoken_text(parsed)
        self.assertIn("rotació", text)
        self.assertNotIn("[pause:", text)
        self.assertNotIn("source_ids:", text)
        self.assertNotIn("## ", text)

    def test_biology_script_covers_its_objectives(self):
        folder = topic_folder(ROOT / "content", "biologia/1-eso/els-essers-vius")
        topic = yaml.safe_load((folder / "topic.yml").read_text(encoding="utf-8"))
        policy = resolve_config(self.config, "biologia", topic)
        parsed = parse_script(folder / "podcast" / "script.md", topic, policy)
        self.assertEqual(set(parsed["metadata"]["objective_ids"]),
                         {objective["id"] for objective in topic["objectives"]})
        self.assertGreaterEqual(sum(event["type"] == "chapter" for event in parsed["events"]), 6)
        self.assertGreaterEqual(sum(event["type"] == "pause" for event in parsed["events"]), 4)

    def test_podcast_labels_follow_teaching_language(self):
        self.assertEqual(ui("ca-valencia", "podcast"), "Pòdcast")
        self.assertEqual(ui("es", "podcast"), "Podcast")

    def test_parser_order_and_rejects_technical_text(self):
        sample = """---
title: Prova
language: ca-valencia
format: narration
estimated_duration_minutes: 12
objective_ids: [o1]
source_ids: [guide]
---
## Inici
Una pregunta?
[pause: 4.5]
La resposta.
## Final
Adéu.
"""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "script.md"
            path.write_text(sample, encoding="utf-8")
            parsed = parse_script(path, self.topic, self.policy)
            self.assertEqual([event["type"] for event in parsed["events"]],
                             ["chapter", "speech", "pause", "speech", "chapter", "speech"])
            self.assertEqual(parsed["events"][2]["duration_seconds"], 4.5)
            self.assertEqual(spoken_text(parsed), "Una pregunta?\n\nLa resposta.\n\nAdéu.")
            for invalid in ("[pause: 0]", "[pause: 31]", "![imatge](a.png)", "- llista", "[pause: bad]"):
                with self.subTest(invalid=invalid):
                    path.write_text(sample.replace("La resposta.", invalid), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        parse_script(path, self.topic, self.policy)
            path.write_text(sample.replace("language: ca-valencia", "language: es"), encoding="utf-8")
            with self.assertRaises(ValueError):
                parse_script(path, self.topic, self.policy)
            path.write_text(sample.replace("Adéu.", ""), encoding="utf-8")
            with self.assertRaises(ValueError):
                parse_script(path, self.topic, self.policy)


if __name__ == "__main__":
    unittest.main()
