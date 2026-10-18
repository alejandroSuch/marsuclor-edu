import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from botocore.exceptions import ClientError, EndpointConnectionError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from podcast_publish import fingerprint, resolve, sync_one, verify_public_audio, write_local_provenance  # noqa: E402
from storage import InconsistentObject, StorageError, canonical_bytes, check_record, object_keys, put_record, sha256_file  # noqa: E402
from storage.local import LocalStorage  # noqa: E402
from storage.r2 import R2Storage  # noqa: E402
from storage.inventory import inventory, load_manifest, retention, summary  # noqa: E402


REFERENCE = "geografia/1-eso/terra"
GENERATION = {"pipeline": 1, "tools": {"piper": "test"},
              "voice": {"model": "voice", "sha256": "b" * 64, "config_sha256": "c" * 64}}
HASH = hashlib.sha256(canonical_bytes(GENERATION)).hexdigest()


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = LocalStorage(self.root / "store")
        self.mp3 = self.root / "audio.mp3"
        self.mp3.write_bytes(b"a" * 2048)
        self.record = {"topic": REFERENCE, "generation_hash": HASH, "generation": GENERATION,
                       "voice": "voice", "voice_sha256": "b" * 64, "voice_config_sha256": "c" * 64,
                       "pipeline_version": 1,
                       "mp3_size": self.mp3.stat().st_size, "mp3_sha256": sha256_file(self.mp3)}

    def test_deterministic_hash_and_audio_inputs(self):
        parsed = {"events": [{"type": "chapter", "title": "Intro"},
                             {"type": "speech", "text": "La Terra gira."},
                             {"type": "pause", "duration_seconds": 3.0}]}
        settings = {key: 1 for key in ("length_scale", "noise_scale", "noise_w_scale", "volume",
                                      "output_sample_rate", "mp3_bitrate_kbps", "loudness_lufs", "true_peak_db",
                                      "sentence_pause_seconds", "paragraph_pause_seconds")}
        args = [REFERENCE, parsed, settings, "voice", "b" * 64, "c" * 64,
                {"piper": "1"}, {}]
        initial, _ = fingerprint(*args)
        self.assertEqual(initial, fingerprint(*args)[0])
        changed = copy.deepcopy(args)
        changed[1]["events"][1]["text"] = "El planeta gira."
        self.assertNotEqual(initial, fingerprint(*changed)[0])
        changed = copy.deepcopy(args)
        changed[3] = "another-voice"
        self.assertNotEqual(initial, fingerprint(*changed)[0])
        changed = copy.deepcopy(args)
        changed[2]["mp3_bitrate_kbps"] = 160
        self.assertNotEqual(initial, fingerprint(*changed)[0])
        changed = copy.deepcopy(args)
        changed[7] = {"Terra": "Tèrra"}
        self.assertNotEqual(initial, fingerprint(*changed)[0])
        changed = copy.deepcopy(args)
        changed[1]["events"][0]["title"] = "Títol editorial"
        self.assertEqual(initial, fingerprint(*changed)[0])
        changed = copy.deepcopy(args)
        changed[1]["events"].insert(1, {"type": "chapter", "title": "Nou títol"})
        self.assertEqual(initial, fingerprint(*changed)[0])

    def test_local_creation_reuse_and_no_overwrite(self):
        self.assertIsNone(check_record(self.store, REFERENCE, HASH))
        put_record(self.store, REFERENCE, HASH, self.mp3, self.record)
        self.assertEqual(check_record(self.store, REFERENCE, HASH), self.record)
        with self.assertRaises(InconsistentObject):
            put_record(self.store, REFERENCE, HASH, self.mp3, self.record)
        key, _ = object_keys(REFERENCE, HASH)
        self.assertEqual(self.store.head(key)["size"], 2048)
        self.assertTrue(self.store.url(key).startswith("file://"))

    def test_inconsistent_metadata_and_incomplete_upload(self):
        key, json_key = object_keys(REFERENCE, HASH)
        self.store.put_file_once(key, self.mp3, "audio/mpeg", self.record["mp3_sha256"])
        with self.assertRaisesRegex(InconsistentObject, "incomplet"):
            check_record(self.store, REFERENCE, HASH)
        self.store.put_bytes_once(json_key, json.dumps({**self.record, "mp3_size": 10}).encode(), "application/json")
        with self.assertRaisesRegex(InconsistentObject, "inconsistents"):
            check_record(self.store, REFERENCE, HASH)

    def test_new_runner_reuses_remote_without_synthesis_or_upload(self):
        put_record(self.store, REFERENCE, HASH, self.mp3, self.record)
        fresh_runner_store = LocalStorage(self.root / "store")
        state = {"hash": HASH}
        with patch("podcast_publish.resolve", return_value=state), \
             patch("podcast_publish.PiperSynthesizer") as piper, \
             patch("podcast_publish.build_audio") as ffmpeg, \
             patch.object(fresh_runner_store, "put_file_once") as upload:
            result = sync_one(self.root, REFERENCE, fresh_runner_store)
        self.assertTrue(result["reused"])
        self.assertTrue(result["url"].endswith(f"/{HASH}.mp3"))
        piper.assert_not_called()
        ffmpeg.assert_not_called()
        upload.assert_not_called()

    def test_persistence_without_local_model_or_audio_cache(self):
        reference = "geografia/1-eso/els-fonaments-del-planeta-terra"
        folder = self.root / "content" / reference
        (folder / "podcast").mkdir(parents=True)
        (self.root / "config").mkdir()
        shutil.copy2(ROOT / "config/subjects.yml", self.root / "config/subjects.yml")
        for name in ("topic.yml", "podcast/script.md", "podcast/pronunciation.yml"):
            shutil.copy2(ROOT / "content" / reference / name, folder / name)
        with patch("podcast_publish.versions", return_value={"piper": "1.8.0"}):
            state = resolve(self.root, reference)
            generation = state["generation"]
            digest = state["hash"]
            record = {"topic": reference, "generation_hash": digest, "generation": generation,
                      "voice": state["model"], "voice_sha256": generation["voice"]["sha256"],
                      "voice_config_sha256": generation["voice"]["config_sha256"],
                      "pipeline_version": generation["pipeline"], "mp3_size": 2048,
                      "mp3_sha256": sha256_file(self.mp3)}
            put_record(self.store, reference, digest, self.mp3, record)
            self.assertFalse((self.root / "models").exists())
            self.assertFalse((self.root / "audio").exists())
            fresh_runner = LocalStorage(self.root / "store")
            with patch("podcast_publish.PiperSynthesizer") as piper, \
                 patch("podcast_publish.build_audio") as ffmpeg, \
                 patch("podcast_publish.download_voice") as download, \
                 patch.object(fresh_runner, "put_file_once") as upload:
                row = sync_one(self.root, reference, fresh_runner, download=True)
                model_file = self.root / "models/piper" / f"{state['model']}.onnx"
                model_file.parent.mkdir(parents=True)
                model_file.write_bytes(b"corrupted model cache")
                Path(str(model_file) + ".json").write_bytes(b"corrupted config cache")
                reused_with_bad_cache = sync_one(self.root, reference, fresh_runner, download=True)
        self.assertEqual(row["hash"], digest)
        self.assertTrue(row["reused"])
        self.assertEqual(reused_with_bad_cache["hash"], digest)
        self.assertTrue(reused_with_bad_cache["reused"])
        piper.assert_not_called()
        ffmpeg.assert_not_called()
        download.assert_not_called()
        upload.assert_not_called()

    def test_missing_object_is_generated_once_and_inaccessible_store_fails_closed(self):
        model_file = self.root / "voice.onnx"
        model_file.write_bytes(b"model")
        Path(str(model_file) + ".json").write_bytes(b"config")
        generation = {"pipeline": 1, "tools": {"piper": "test"},
                      "voice": {"model": "voice", "sha256": sha256_file(model_file),
                                "config_sha256": sha256_file(Path(str(model_file) + ".json"))}}
        digest = hashlib.sha256(canonical_bytes(generation)).hexdigest()
        state = {"hash": digest, "model_file": model_file, "model": "voice", "settings": {},
                 "events": [{"type": "speech", "text": "test"}], "parsed": {"events": []},
                 "generation": generation}

        def fake_build(_events, output, _settings, _synthesizer):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"z" * 2048)

        with patch("podcast_publish.resolve", return_value=state), \
             patch("podcast_publish.PiperSynthesizer") as piper, \
             patch("podcast_publish.build_audio", side_effect=fake_build) as ffmpeg, \
             patch("podcast_publish.validate_mp3", return_value=2.5):
            row = sync_one(self.root, REFERENCE, self.store)
        self.assertFalse(row["reused"])
        piper.assert_called_once()
        ffmpeg.assert_called_once()
        self.assertIsNotNone(check_record(self.store, REFERENCE, digest))

        denied = Mock()
        denied.head.side_effect = StorageError("403")
        with patch("podcast_publish.resolve", return_value=state), \
             patch("podcast_publish.PiperSynthesizer") as piper, \
             patch("podcast_publish.build_audio") as ffmpeg:
            with self.assertRaisesRegex(StorageError, "403"):
                sync_one(self.root, REFERENCE, denied)
        piper.assert_not_called()
        ffmpeg.assert_not_called()

    def test_r2_404_403_network_and_conditional_put(self):
        backend = R2Storage.__new__(R2Storage)
        backend.bucket = "bucket"
        backend.public_base = "https://audio.example.workers.dev"
        client = Mock()
        backend.client = client

        def error(status, code):
            return ClientError({"Error": {"Code": code},
                                "ResponseMetadata": {"HTTPStatusCode": status}}, "HeadObject")

        client.head_object.side_effect = error(404, "NoSuchKey")
        self.assertIsNone(backend.head("key"))
        client.head_object.side_effect = error(403, "AccessDenied")
        with self.assertRaises(StorageError):
            backend.head("key")
        client.head_object.side_effect = EndpointConnectionError(endpoint_url="https://invalid")
        with self.assertRaises(StorageError):
            backend.head("key")
        client.put_object.side_effect = error(412, "PreconditionFailed")
        with self.assertRaises(InconsistentObject):
            backend.put_file_once("key", self.mp3, "audio/mpeg", sha256_file(self.mp3))
        self.assertEqual(client.put_object.call_args.kwargs["IfNoneMatch"], "*")
        self.assertTrue(backend.url("key").endswith("/key"))

    def test_inventory_published_history_and_incomplete_pairs_are_read_only(self):
        put_record(self.store, REFERENCE, HASH, self.mp3, self.record)
        previous = "a" * 64
        old_mp3, _ = object_keys(REFERENCE, previous)
        self.store.put_file_once(old_mp3, self.mp3, "audio/mpeg", sha256_file(self.mp3))
        manifest = {REFERENCE: {"hash": HASH,
                    "url": f"https://audio.example.workers.dev/podcasts/{REFERENCE}/{HASH}.mp3"}}
        with patch.object(self.store, "put_file_once") as write_audio, \
             patch.object(self.store, "put_bytes_once") as write_json:
            report = inventory(self.store, manifest)
        write_audio.assert_not_called()
        write_json.assert_not_called()
        self.assertEqual({row["status"] for row in report["versions"]}, {"unreferenced", "published"})
        self.assertEqual(next(row for row in report["versions"] if row["status"] == "unreferenced")["problems"],
                         ["pareja MP3/JSON incompleta"])
        self.assertEqual(summary(report)["unreferenced_mp3_bytes"], 2048)
        self.assertEqual(summary(report)["subjects"]["geografia"]["versions"], 2)
        self.assertEqual(summary(report)["courses"]["geografia/1-eso"]["mp3_bytes"], 4096)
        self.assertEqual(summary(report)["topics"][REFERENCE]["versions"], 2)
        self.assertEqual(len(retention(report)["candidates"]), 1)
        self.assertEqual(retention(report)["candidate_mp3_bytes"], 2048)
        self.assertEqual(report["missing_published"], [])
        self.assertTrue(all(row["published"] is None for row in inventory(self.store)["versions"]))
        with self.assertRaisesRegex(ValueError, "manifest"):
            retention(inventory(self.store))

    def test_inventory_detects_missing_published_and_invalid_manifest(self):
        absent = "d" * 64
        manifest = {REFERENCE: {"hash": absent,
                    "url": f"https://audio.example.workers.dev/podcasts/{REFERENCE}/{absent}.mp3"}}
        report = inventory(self.store, manifest)
        self.assertEqual(report["missing_published"][0]["hash"], absent)
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        self.assertEqual(load_manifest(str(manifest_path)), manifest)
        manifest[REFERENCE]["url"] = "https://invalid.example/audio.mp3"
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaises(StorageError):
            load_manifest(str(manifest_path))

    def test_inventory_capacity_levels_use_payload_estimate(self):
        base = {"versions": [], "unexpected_objects": [], "missing_published": []}
        for percentage, expected in ((69, "ok"), (70, "notice"), (85, "review"), (95, "intervention")):
            report = {**base, "unexpected_objects": [{"size": percentage * 100_000_000}]}
            self.assertEqual(summary(report)["capacity_level"], expected)

    def test_r2_inventory_paginates_and_network_error_fails_closed(self):
        backend = R2Storage.__new__(R2Storage)
        backend.bucket = "bucket"
        backend.client = Mock()
        backend.client.list_objects_v2.side_effect = [
            {"Contents": [{"Key": "podcasts/a.mp3", "Size": 3,
                            "LastModified": __import__("datetime").datetime(2026, 1, 1)}],
             "IsTruncated": True, "NextContinuationToken": "next"},
            {"Contents": [], "IsTruncated": False},
        ]
        self.assertEqual(len(list(backend.list_objects())), 1)
        self.assertEqual(backend.client.list_objects_v2.call_args_list[1].kwargs["ContinuationToken"], "next")
        backend.client.list_objects_v2.side_effect = EndpointConnectionError(endpoint_url="https://invalid")
        with self.assertRaises(StorageError):
            list(backend.list_objects())

    def test_public_worker_head_must_match_remote_object(self):
        row = {"topic": REFERENCE, "url": "https://audio.example.workers.dev/podcasts/" + REFERENCE + "/" + HASH + ".mp3",
               "size": 2048}
        response = MagicMock()
        response.status = 200
        response.url = row["url"]
        response.headers = {"Content-Type": "audio/mpeg", "Accept-Ranges": "bytes", "Content-Length": "2048"}
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        partial = MagicMock()
        partial.status = 206
        partial.headers = {"Content-Range": "bytes 0-0/2048", "Content-Length": "1"}
        partial.read.return_value = b"a"
        partial.__enter__ = Mock(return_value=partial)
        partial.__exit__ = Mock(return_value=False)
        download = MagicMock()
        download.status = 200
        download.headers = {"Content-Disposition": 'attachment; filename="test.mp3"', "Content-Length": "2048"}
        download.__enter__ = Mock(return_value=download)
        download.__exit__ = Mock(return_value=False)
        with patch("podcast_publish.urlopen", side_effect=[response, partial, download]) as fetch:
            verify_public_audio(row)
        self.assertEqual([call.args[0].get_method() for call in fetch.call_args_list], ["HEAD", "GET", "HEAD"])
        partial.headers["Content-Range"] = "bytes 0-2/2048"
        with patch("podcast_publish.urlopen", side_effect=[response, partial]):
            with self.assertRaisesRegex(ValueError, "no disponible"):
                verify_public_audio(row)
        response.headers["Content-Length"] = "17"
        with patch("podcast_publish.urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "no disponible"):
                verify_public_audio(row)

    def test_local_publish_requires_matching_provenance(self):
        state = {"hash": HASH, "generation": GENERATION, "model": "voice", "parsed": {"events": []}}
        with patch("podcast_publish.resolve", return_value=state), \
             patch("podcast_publish.validate_mp3", return_value=2.0):
            with self.assertRaisesRegex(ValueError, "procedència"):
                sync_one(self.root, REFERENCE, self.store, local_mp3=self.mp3)
            sidecar = write_local_provenance(self.root, REFERENCE, self.mp3)
            self.assertTrue(sidecar.is_file())
            result = sync_one(self.root, REFERENCE, self.store, local_mp3=self.mp3)
            self.assertFalse(result["reused"])
            self.mp3.write_bytes(b"different" * 300)
            with self.assertRaisesRegex(ValueError, "generació validada"):
                sync_one(self.root, REFERENCE, LocalStorage(self.root / "another-store"), local_mp3=self.mp3)


@unittest.skipUnless(os.getenv("MARTINA_R2_INTEGRATION") == "1", "Integració R2 optativa")
class R2IntegrationTests(unittest.TestCase):
    def test_existing_remote_version_is_readable(self):
        reference = os.environ["MARTINA_R2_TEST_TOPIC"]
        digest = os.environ["MARTINA_R2_TEST_HASH"]
        record = check_record(R2Storage.from_env(), reference, digest)
        self.assertIsNotNone(record)


if __name__ == "__main__":
    unittest.main()
