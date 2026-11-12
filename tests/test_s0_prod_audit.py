"""S0 survey tests: fake metrics only, never start or stop Docker."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "s0_prod_audit.py"
spec = importlib.util.spec_from_file_location("s0_prod_audit", SOURCE)
s0 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s0)


def sample(epoch, mem_gib=4, cpu=10, health="healthy", readable=True, oom=0, controllers=None):
    return {
        "epoch": epoch, "cpu_busy_percent": cpu,
        "mem_available_bytes": int(mem_gib * s0.GIB),
        "root_disk": {"available_bytes": 40 * s0.GIB, "free_percent": 55},
        "oom_kill_delta": oom, "systemd_pid1": True,
        "cgroup_controllers": controllers if controllers is not None else ["cpu", "memory", "pids"],
        "docker": {"readable": readable, "cgroup_version": "2",
                   "docker_storage": {"available_bytes": 40 * s0.GIB, "free_percent": 55}, "containers": [
            {"name": "erp-prod", "state": "running", "health": health}
        ]}
    }


def normal_day():
    return [sample(i * 60) for i in range(1441)]


class SurveyTests(unittest.TestCase):
    def test_cpu_from_proc_ticks(self):
        self.assertEqual(s0.cpu_ticks("cpu 10 0 10 50 5 0 0 0 0\n"), [75, 55])
        self.assertEqual(s0.cpu_busy([100, 80], [200, 150]), 30.0)
        self.assertIsNone(s0.cpu_busy([200, 150], [100, 80]))

    def test_not_enough_24_hour_history(self):
        report = s0.analyze([sample(0), sample(60)], ["erp-prod"])
        self.assertEqual(report["decision"], "S0_NEEDS_REVIEW")
        self.assertFalse(report["qa_enabled"])
        self.assertIn("Baseline 24 jam belum lengkap", report["reasons"])

    def test_full_baseline_candidate_does_not_enable_qa(self):
        report = s0.analyze(normal_day(), ["erp-prod"])
        self.assertEqual(report["decision"], "S0_BASELINE_CANDIDATE")
        self.assertFalse(report["qa_enabled"])
        self.assertEqual(report["sample_count"], 1441)

    def test_blocks_low_memory(self):
        samples = normal_day()
        samples[40]["mem_available_bytes"] = 2 * s0.GIB
        self.assertEqual(s0.analyze(samples, ["erp-prod"])["decision"], "S0_NEEDS_REVIEW")

    def test_blocks_gaps(self):
        samples = normal_day()
        samples[80]["epoch"] += 400
        self.assertTrue(any("jeda" in reason for reason in s0.analyze(samples, ["erp-prod"])["reasons"]))

    def test_blocks_missing_critical_list(self):
        self.assertTrue(any("Daftar container" in reason for reason in s0.analyze(normal_day(), [])["reasons"]))

    def test_blocks_unhealthy_service(self):
        samples = normal_day()
        samples[-1]["docker"]["containers"][0]["health"] = "unknown"
        self.assertTrue(any("erp-prod" in reason for reason in s0.analyze(samples, ["erp-prod"])["reasons"]))

    def test_blocks_docker_disk_unavailable(self):
        samples = normal_day()
        samples[100]["docker"]["docker_storage"] = None
        self.assertTrue(any("Disk Docker" in reason for reason in s0.analyze(samples, ["erp-prod"])["reasons"]))

    def test_blocks_missing_docker_access(self):
        samples = normal_day()
        samples[100]["docker"]["readable"] = False
        self.assertTrue(any("Docker tidak dapat" in reason for reason in s0.analyze(samples, ["erp-prod"])["reasons"]))

    def test_blocks_no_cgroup_controllers(self):
        samples = normal_day()
        samples[-1]["cgroup_controllers"] = ["cpu"]
        self.assertTrue(any("Controller" in reason for reason in s0.analyze(samples, ["erp-prod"])["reasons"]))

    def test_blocks_any_oom(self):
        samples = normal_day()
        samples[55]["oom_kill_delta"] = 1
        self.assertTrue(any("OOM" in reason for reason in s0.analyze(samples, ["erp-prod"])["reasons"]))

    def test_docker_output_only_saves_selected_nonsecret_fields(self):
        info = "2|systemd|/does-not-exist"
        line = json.dumps({"Names": "erp-prod", "State": "running", "Status": "Up 3 days (healthy)",
                           "SensitiveFakeField": "NEVER_SAVE_ME"})
        with patch.object(s0, "local_docker", side_effect=[info, line]):
            obj = s0.docker_snapshot()
        self.assertEqual(obj["containers"], [{"name": "erp-prod", "state": "running", "health": "healthy"}])
        self.assertNotIn("NEVER_SAVE_ME", json.dumps(obj))

    def test_docker_error_is_fail_closed(self):
        with patch.object(s0, "local_docker", side_effect=[None, None]):
            obj = s0.docker_snapshot()
        self.assertFalse(obj["readable"])
        self.assertEqual(obj["containers"], [])

    def test_private_jsonl_permissions_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "private" / "samples.jsonl"
            s0._secure_append(file, sample(0))
            s0._secure_append(file, sample(60))
            self.assertEqual(len(s0._read_samples(file)), 2)
            if os.name != "nt":
                self.assertEqual(file.stat().st_mode & 0o777, 0o600)

    def test_progress_one_sample_explains_unfinished_survey(self):
        result = s0.progress([sample(100)])
        self.assertEqual(result["sample_count"], 1)
        self.assertEqual(result["duration_hours"], 0)
        self.assertEqual(result["remaining_hours"], 24)
        self.assertFalse(result["qa_enabled"])

    def test_progress_detects_sampling_gap(self):
        result = s0.progress([sample(100), sample(400)])
        self.assertTrue(result["has_sampling_gap"])
        self.assertEqual(result["sample_count"], 2)

    def test_resume_with_short_gap_preserves_previous_ticks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s0.jsonl"
            obj = sample(100)
            obj["cpu_ticks"] = [333, 222]
            obj["oom_kill_count"] = 1
            s0._secure_append(path, obj)
            with patch.object(s0.time, "time", return_value=120):
                started, ticks, oom, count = s0.resume_state(path)
            self.assertEqual((started, ticks, oom, count), (100, [333, 222], 1, 1))

    def test_resume_rejects_gap_longer_than_three_minutes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s0.jsonl"
            s0._secure_append(path, sample(100))
            with patch.object(s0.time, "time", return_value=100 + 181):
                with self.assertRaisesRegex(ValueError, "lebih dari 3 menit"):
                    s0.resume_state(path)

    def test_report_rejects_placeholder_container_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s0.jsonl"
            s0._secure_append(path, sample(100))
            with self.assertRaises(SystemExit) as ctx:
                s0.main(["report", "--input", str(path), "--critical", "nama-container-production"])
            self.assertEqual(ctx.exception.code, 2)

    def test_interrupt_keeps_sample_and_returns_130(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s0.jsonl"
            obj = sample(100)
            obj["sampled_at"] = "2026-11-12T00:00:00Z"
            obj["cpu_ticks"] = None
            obj["oom_kill_count"] = None
            with patch.object(s0, "snapshot", return_value=obj):
                with patch.object(s0.time, "sleep", side_effect=KeyboardInterrupt):
                    code = s0.main(["collect", "--hours", "24", "--interval", "60", "--output", str(path)])
            self.assertEqual(code, 130)
            self.assertEqual(len(s0._read_samples(path)), 1)

    def test_list_containers_uses_read_only_snapshot(self):
        with patch.object(s0, "docker_snapshot", return_value={"readable": True, "containers": [
            {"name": "app", "state": "running", "health": "healthy"}
        ]}):
            self.assertEqual(s0.main(["containers"]), 0)

    def test_status_reads_existing_samples_without_authorizing_qa(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s0.jsonl"
            s0._secure_append(path, sample(100))
            self.assertEqual(s0.main(["status", "--input", str(path)]), 0)

    def test_reject_invalid_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.jsonl"
            path.write_text("hello\n")
            with self.assertRaisesRegex(ValueError, "baris 1"):
                s0._read_samples(path)


if __name__ == "__main__":
    unittest.main()
