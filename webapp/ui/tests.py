"""End-to-end web UI tests: real pipeline runs on the bundled sample data.

Each test gets an isolated temporary pipeline DB/output dir, so the suite never
touches the developer's real ``output/pipeline.db``.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from src.reasons import PipelineError

from . import services


def upload(path: Path, name: str | None = None) -> SimpleUploadedFile:
    return SimpleUploadedFile(name or path.name, path.read_bytes(), content_type="text/csv")


class WebUITestCase(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        ov = override_settings(
            PIPELINE_DB_PATH=root / "pipeline.db",
            PIPELINE_OUTPUT_DIR=root / "output",
            PIPELINE_UPLOAD_DIR=root / "output" / "uploads",
        )
        ov.enable()
        self.addCleanup(ov.disable)
        self.sample = Path(settings.BASE_DIR) / "data" / "sample"

    def wait_run(self, run_id: str, timeout: float = 20.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            run = services.get_run(run_id)
            if run and run["status"] in ("SUCCESS", "FAILED"):
                time.sleep(0.1)  # let the worker thread close its SQLite handle
                return run
            time.sleep(0.05)
        self.fail(f"run {run_id} did not finish within {timeout}s")

    def run_id_from_redirect(self, response) -> str:
        self.assertEqual(response.status_code, 302)
        from urllib.parse import urlparse
        return urlparse(response.url).path.rstrip("/").split("/")[-1]

    def start_sample_run(self) -> str:
        response = self.client.post(reverse("ui:run_sample"))
        run_id = self.run_id_from_redirect(response)
        self.wait_run(run_id)
        return run_id

    def post_new_run(self, **extra) -> tuple:
        data = {
            "input_file": upload(self.sample / "incoming_records.csv"),
            "new_reference_version": "v1",
            "new_reference_file": upload(self.sample / "reference_codes_v1.csv"),
            "entities_file": upload(self.sample / "entities.csv"),
            "deprecated_policy": "allow_historical",
        }
        data.update(extra)
        response = self.client.post(reverse("ui:run_new"), data)
        return response, data


class DashboardTests(WebUITestCase):
    def test_empty_dashboard(self):
        response = self.client.get(reverse("ui:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Welcome to your validation console")

    def test_dashboard_after_sample_run(self):
        self.start_sample_run()
        response = self.client.get(reverse("ui:dashboard"))
        self.assertContains(response, "Pipeline runs")
        self.assertContains(response, "sample_v1")
        self.assertContains(response, "Rejection reasons")


class RunFlowTests(WebUITestCase):
    def test_sample_run_end_to_end(self):
        run_id = self.start_sample_run()
        run = services.get_run(run_id)
        self.assertEqual(run["status"], "SUCCESS")
        self.assertEqual(run["input_count"], 48)
        self.assertEqual(run["valid_count"], 22)
        self.assertEqual(run["rejected_count"], 26)
        detail = self.client.get(reverse("ui:run_detail", args=[run_id]))
        self.assertContains(detail, "Run success")
        self.assertContains(detail, "Reconciliation")
        checks = self.client.get(reverse("ui:run_detail", args=[run_id]) + "?tab=reconciliation")
        self.assertContains(checks, "PASS")
        rejects = self.client.get(reverse("ui:run_detail", args=[run_id]) + "?tab=rejects")
        self.assertContains(rejects, "Quarantined rows")
        outcomes = self.client.get(reverse("ui:run_detail", args=[run_id]) + "?tab=outcomes")
        self.assertContains(outcomes, "Every processed row")

    def test_new_run_with_uploads(self):
        response, _ = self.post_new_run(run_id="web_test_v1")
        run_id = self.run_id_from_redirect(response)
        self.assertEqual(run_id, "web_test_v1")
        run = self.wait_run(run_id)
        self.assertEqual(run["status"], "SUCCESS")
        self.assertEqual(run["reference_version"], "v1")
        self.assertEqual(run["valid_count"], 22)
        self.assertIsNotNone(services.get_version("v1"))
        self.assertGreater(services.entity_count(), 0)

    def test_new_run_against_loaded_version(self):
        self.post_new_run(run_id="first")
        self.wait_run("first")
        response = self.client.post(reverse("ui:run_new"), {
            "input_file": upload(self.sample / "incoming_records.csv"),
            "reference_version": "v1",
            "check_entities": "on",
            "deprecated_policy": "allow_historical",
            "run_id": "second",
        })
        run_id = self.run_id_from_redirect(response)
        run = self.wait_run(run_id)
        self.assertEqual(run["status"], "SUCCESS")
        self.assertEqual(run["valid_count"], 22)

    def test_idempotent_rerun_publishes_nothing(self):
        self.post_new_run(run_id="r1")
        self.wait_run("r1")
        response = self.client.post(reverse("ui:run_new"), {
            "input_file": upload(self.sample / "incoming_records.csv"),
            "reference_version": "v1",
            "check_entities": "on",
            "deprecated_policy": "allow_historical",
            "run_id": "r2",
        })
        run = self.wait_run(self.run_id_from_redirect(response))
        self.assertEqual(run["published_count"], 0)
        self.assertEqual(run["unchanged_count"], 22)

    def test_failed_run_marks_failed_and_keeps_target(self):
        bad = SimpleUploadedFile("bad.csv", b"foo,bar\n1,2\n", content_type="text/csv")
        response = self.client.post(reverse("ui:run_new"), {
            "input_file": bad,
            "new_reference_version": "v1",
            "new_reference_file": upload(self.sample / "reference_codes_v1.csv"),
            "deprecated_policy": "allow_historical",
            "run_id": "badrun",
        })
        run = self.wait_run(self.run_id_from_redirect(response))
        self.assertEqual(run["status"], "FAILED")
        self.assertIn("SCHEMA_MISMATCH", run["error_message"])
        detail = self.client.get(reverse("ui:run_detail", args=["badrun"]))
        self.assertContains(detail, "Run failed")
        self.assertEqual(services.validated_records()["total"], 0)

    def test_form_requires_reference(self):
        response = self.client.post(reverse("ui:run_new"), {
            "input_file": upload(self.sample / "incoming_records.csv"),
            "deprecated_policy": "allow_historical",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose a loaded version")

    def test_swapped_files_rejected_before_run(self):
        response = self.client.post(reverse("ui:run_new"), {
            "input_file": upload(self.sample / "reference_codes_v1.csv"),
            "new_reference_version": "v1",
            "new_reference_file": upload(self.sample / "incoming_records.csv"),
            "deprecated_policy": "allow_historical",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "looks like a reference snapshot")
        self.assertContains(response, "looks like an incoming records batch")

    def test_form_new_file_needs_name(self):
        response = self.client.post(reverse("ui:run_new"), {
            "input_file": upload(self.sample / "incoming_records.csv"),
            "new_reference_file": upload(self.sample / "reference_codes_v1.csv"),
            "deprecated_policy": "allow_historical",
        })
        self.assertContains(response, "Give the new version a name")

    def test_duplicate_run_id_rejected(self):
        self.post_new_run(run_id="dup")
        self.wait_run("dup")
        response = self.client.post(reverse("ui:run_new"), {
            "input_file": upload(self.sample / "incoming_records.csv"),
            "reference_version": "v1",
            "deprecated_policy": "allow_historical",
            "run_id": "dup",
        })
        self.assertContains(response, "already exists")

    def test_check_entities_without_loaded_entities(self):
        self.post_new_run(run_id="e0")
        self.wait_run("e0")
        with services.pipeline_conn() as conn:
            conn.execute("DELETE FROM entities")
        response = self.client.post(reverse("ui:run_new"), {
            "input_file": upload(self.sample / "incoming_records.csv"),
            "reference_version": "v1",
            "check_entities": "on",
            "deprecated_policy": "allow_historical",
        })
        self.assertContains(response, "No entity ids are loaded yet")

    def test_status_endpoint_and_404s(self):
        run_id = self.start_sample_run()
        status = self.client.get(reverse("ui:run_status", args=[run_id]))
        self.assertEqual(status.json()["status"], "SUCCESS")
        unknown = self.client.get(reverse("ui:run_status", args=["nope"]))
        self.assertEqual(unknown.json()["status"], "STARTING")
        self.assertEqual(self.client.get(reverse("ui:run_detail", args=["nope"])).status_code, 404)

    def test_downloads(self):
        run_id = self.start_sample_run()
        for filename in ("run_summary.json", "reconciliation.json", "rejects.csv"):
            response = self.client.get(reverse("ui:run_download", args=[run_id, filename]))
            self.assertEqual(response.status_code, 200)
            self.assertIn("attachment", response["Content-Disposition"])
        bad = self.client.get(reverse("ui:run_download", args=[run_id, "..\\..\\config.toml"]))
        self.assertEqual(bad.status_code, 404)


class CompareTests(WebUITestCase):
    def test_compare_v1_v2(self):
        run_a = self.start_sample_run()
        response = self.client.post(reverse("ui:run_new"), {
            "input_file": upload(self.sample / "incoming_records.csv"),
            "new_reference_version": "v2",
            "new_reference_file": upload(self.sample / "reference_codes_v2.csv"),
            "check_entities": "on",
            "deprecated_policy": "allow_historical",
        })
        run_b = self.run_id_from_redirect(response)
        self.wait_run(run_b)
        result = self.client.get(reverse("ui:compare"), {"run_a": run_a, "run_b": run_b})
        self.assertContains(result, "What changed")
        self.assertContains(result, "rejected_now_valid")
        data = services.compare(run_a, run_b)
        self.assertEqual(data["categories"].get("rejected_now_valid"), 6)
        self.assertEqual(data["categories"].get("valid_now_deprecated"), 1)
        self.assertTrue(data["changed_rows"])

    def test_compare_same_run_rejected(self):
        run_id = self.start_sample_run()
        response = self.client.get(reverse("ui:compare"), {"run_a": run_id, "run_b": run_id})
        self.assertContains(response, "two different runs")


class ReferenceTests(WebUITestCase):
    def test_load_and_browse(self):
        response = self.client.post(reverse("ui:reference_load"), {
            "reference_version": "v1",
            "reference_file": upload(self.sample / "reference_codes_v1.csv"),
        })
        self.assertEqual(response.status_code, 302)
        listing = self.client.get(reverse("ui:reference_list"))
        self.assertContains(listing, "v1")
        detail = self.client.get(reverse("ui:reference_detail", args=["v1"]))
        self.assertContains(detail, "Codes in this version")
        filtered = self.client.get(reverse("ui:reference_detail", args=["v1"]) + "?status=DEPRECATED")
        self.assertEqual(filtered.status_code, 200)

    def test_immutability_enforced_in_form(self):
        self.client.post(reverse("ui:reference_load"), {
            "reference_version": "v1",
            "reference_file": upload(self.sample / "reference_codes_v1.csv"),
        })
        response = self.client.post(reverse("ui:reference_load"), {
            "reference_version": "v1",
            "reference_file": upload(self.sample / "reference_codes_v2.csv"),
        })
        self.assertContains(response, "REFERENCE_IMMUTABLE")

    def test_invalid_reference_shows_errors(self):
        bad = SimpleUploadedFile("bad_ref.csv", b"product_code,status\nA,ACTIVE\n", content_type="text/csv")
        response = self.client.post(reverse("ui:reference_load"), {
            "reference_version": "vbad", "reference_file": bad,
        })
        self.assertContains(response, "REFERENCE_INVALID")

    def test_download_reference_csv(self):
        self.client.post(reverse("ui:reference_load"), {
            "reference_version": "v1",
            "reference_file": upload(self.sample / "reference_codes_v1.csv"),
        })
        listing = self.client.get(reverse("ui:reference_list"))
        self.assertContains(listing, "Download CSV")
        response = self.client.get(reverse("ui:reference_download", args=["v1"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn('filename="reference_codes_v1.csv"', response["Content-Disposition"])
        lines = response.content.decode().splitlines()
        self.assertEqual(lines[0], "reference_version,product_code,description,status,effective_from,effective_to")
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(line.startswith("v1,") for line in lines[1:]))
        missing = self.client.get(reverse("ui:reference_download", args=["nope"]))
        self.assertEqual(missing.status_code, 404)
        self.assertIsNone(services.get_version("vbad"))

    def test_same_content_reload_is_noop(self):
        for _ in range(2):
            response = self.client.post(reverse("ui:reference_load"), {
                "reference_version": "v1",
                "reference_file": upload(self.sample / "reference_codes_v1.csv"),
            })
            self.assertEqual(response.status_code, 302)
        self.assertEqual(len(services.reference_versions()), 1)

    def test_entities_load(self):
        response = self.client.post(reverse("ui:entities"), {
            "entities_file": upload(self.sample / "entities.csv"),
        })
        self.assertEqual(response.status_code, 302)
        page = self.client.get(reverse("ui:entities"))
        self.assertContains(page, "Loaded ids")
        self.assertGreater(services.entity_count(), 0)


class BrowserTests(WebUITestCase):
    def test_validated_and_quarantine_browsers(self):
        run_id = self.start_sample_run()
        validated = self.client.get(reverse("ui:validated"))
        self.assertContains(validated, "Target table")
        self.assertEqual(services.validated_records()["total"], 22)
        search = self.client.get(reverse("ui:validated") + "?q=REC")
        self.assertEqual(search.status_code, 200)
        quarantine = self.client.get(reverse("ui:quarantine"))
        self.assertContains(quarantine, "Rejected records")
        filtered = self.client.get(reverse("ui:quarantine") + f"?reason=UNKNOWN_CODE&run_id={run_id}")
        self.assertEqual(filtered.status_code, 200)
        runs = self.client.get(reverse("ui:run_list") + "?status=SUCCESS")
        self.assertContains(runs, run_id)