"""Synthetic contract tests. These are NOT patent classification evaluations."""
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.boundary_eval import (STAGES, StudyError, check_frozen, digest,
                                file_digest, load_plan, make_plan, run_plan,
                                summarize, validate_response, write_json)


class BoundaryEvalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "baseline.txt").write_text("synthetic immutable baseline", encoding="utf-8")
        views = {s: "Synthetic reference only: training A differs from training B." for s in STAGES}
        self.spec = {
            "version": 1, "study_id": "synthetic-contract-tests",
            "split_mode": "reference_excluded_known17", "model_settings": {"model": "NOT-A-REAL-MODEL"},
            "freeze_files": ["baseline.txt"], "expected_case_count": 2, "repeats": 3,
            "order_seed": 42, "max_runs": 18, "max_wall_seconds": 30,
            "per_run_timeout_seconds": 5,
            "adapter_command": [sys.executable, "unused.py", "{request}", "{response}"],
            "cases": [], "references": {"pair": {
                "source_family_ids": ["train-A", "train-B"], "source_case_ids": ["train-1", "train-2"],
                "gold_origin": "reviewed_synthetic", "status": "approved", "views": views}},
            "folds": {}}
        for cid in ("test-1", "test-2"):
            self.spec["cases"].append({"case_id": cid, "family_id": cid + "-family", "kind": "synthetic",
                "gold_label": "LABEL-A", "gold_origin": "reviewed_synthetic", "input": {
                    "claims": "Synthetic object; not a patent.", "description": "Synthetic description.",
                    "candidates": [{"candidate_id": "a", "label": "LABEL-A", "official_rule": "Synthetic A"},
                                   {"candidate_id": "b", "label": "LABEL-B", "official_rule": "Synthetic B"}]}})
            self.spec["folds"][cid] = {"reference_ids": ["pair"], "generalization": {
                "source_reference_ids": ["pair"], "source_family_ids": ["train-A", "train-B"],
                "source_case_ids": ["train-1", "train-2"], "status": "approved",
                "views": {s: "Synthetic abstraction without an instance." for s in STAGES}}}

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self):
        return make_plan(self.spec, self.root)

    def rows(self, plan, fn):
        result = []
        for t in plan["tasks"]:
            r = t["request"]
            status, label = fn(r)
            result.append({"trial_id": r["trial_id"], "request_sha256": t["request_sha256"],
                           "status": status, "final_label": label})
        return result

    def response(self, request):
        stages = []
        for s in STAGES:
            a, b = self.root / (s + ".request.json"), self.root / (s + ".response.json")
            write_json(a, {"synthetic_contract_fixture": True})
            write_json(b, {"synthetic_contract_fixture": True})
            stages.append({"stage": s, "status": "called",
                           "supplement_sha256": hashlib.sha256(request["reference_by_stage"][s].encode()).hexdigest(),
                           "request_file": a.name, "request_file_sha256": file_digest(a),
                           "response_file": b.name, "response_file_sha256": file_digest(b)})
        # This fixture tests the response contract only. It is never presented as an actual API run.
        return {"trial_id": request["trial_id"], "request_sha256": digest(request),
                "execution_kind": "live_llm", "model_settings_sha256": digest(request["model_settings"]),
                "frozen_sha256": request["frozen_sha256"], "status": "classified",
                "final_label": "LABEL-A", "stages": stages}

    def test_plan_matrix_and_gold_separation(self):
        plan = self.plan()
        self.assertEqual(len(plan["tasks"]), 18)
        for t in plan["tasks"]:
            self.assertNotIn("gold_label", json.dumps(t["request"]))
            self.assertEqual(set(t["request"]["input"]), {"claims", "description", "candidates"})

    def test_a_has_no_added_instruction(self):
        for t in self.plan()["tasks"]:
            if t["request"]["arm"] == "A":
                self.assertEqual(set(t["request"]["reference_by_stage"].values()), {""})

    def test_order_reproducible_and_inputs_identical(self):
        p, q = self.plan(), self.plan()
        self.assertEqual(p, q)
        self.assertEqual(len({digest(t["request"]["input"]) for t in p["tasks"]}), 1)

    def test_target_family_leak_rejected(self):
        self.spec["references"]["pair"]["source_family_ids"].append("test-1-family")
        with self.assertRaisesRegex(StudyError, "leaked"):
            self.plan()

    def test_target_case_leak_even_wrong_family_rejected(self):
        self.spec["references"]["pair"]["source_case_ids"].append("test-1")
        with self.assertRaisesRegex(StudyError, "leaked"):
            self.plan()

    def test_c_other_training_data_rejected(self):
        self.spec["folds"]["test-1"]["generalization"]["source_family_ids"].append("extra")
        with self.assertRaisesRegex(StudyError, "provenance"):
            self.plan()

    def test_c_different_reference_set_rejected(self):
        self.spec["folds"]["test-1"]["generalization"]["source_reference_ids"] = ["other"]
        with self.assertRaisesRegex(StudyError, "exactly"):
            self.plan()

    def test_no_reference_is_not_fabricated(self):
        self.spec["folds"]["test-1"]["reference_ids"] = []
        with self.assertRaises(StudyError):
            self.plan()

    def test_unapproved_cards_rejected(self):
        self.spec["references"]["pair"]["status"] = "draft"
        with self.assertRaisesRegex(StudyError, "Unapproved"):
            self.plan()

    def test_placeholders_rejected(self):
        self.spec["study_id"] = "__FILL_STUDY__"
        with self.assertRaisesRegex(StudyError, "Fill all"):
            self.plan()

    def test_credentials_rejected(self):
        self.spec["model_settings"]["api_key"] = "not-a-real-key"
        with self.assertRaisesRegex(StudyError, "credentials"):
            self.plan()

    def test_budget_rejected_before_calls(self):
        self.spec["max_runs"] = 17
        with self.assertRaisesRegex(StudyError, "max_runs"):
            self.plan()

    def test_case_count_required(self):
        self.spec["expected_case_count"] = 17
        with self.assertRaisesRegex(StudyError, "Case count"):
            self.plan()

    def test_gold_not_in_upstream_catalogue_detected(self):
        self.spec["cases"][0]["gold_label"] = "absent"
        p = self.plan()
        self.assertTrue(p["gold"]["test-1"]["gold_absent_from_input_candidates"])
        self.assertNotIn("absent", [c["label"] for c in p["tasks"][0]["request"]["input"]["candidates"]])

    def test_old_results_cannot_enter_payload(self):
        self.spec["cases"][0]["input"]["STEP2"] = {"answer": "gold"}
        with self.assertRaisesRegex(StudyError, "only claims"):
            self.plan()

    def test_frozen_file_drift_rejected(self):
        p = self.plan()
        (self.root / "baseline.txt").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(StudyError, "changed"):
            check_frozen(p)

    def test_lock_tampering_rejected(self):
        p = self.plan()
        env = {"plan": p, "sha256": digest(p)}
        env["plan"]["repeats"] = 2
        path = self.root / "lock.json"
        write_json(path, env)
        with self.assertRaisesRegex(StudyError, "modified"):
            load_plan(path)

    def test_response_contract_fixture(self):
        req = self.plan()["tasks"][0]["request"]
        validate_response(self.response(req), req, self.root)

    def test_mock_response_cannot_count_as_real(self):
        req = self.plan()["tasks"][0]["request"]
        res = self.response(req)
        res["execution_kind"] = "mock"
        with self.assertRaisesRegex(StudyError, "Mock"):
            validate_response(res, req, self.root)

    def test_setting_drift_rejected(self):
        req = self.plan()["tasks"][0]["request"]
        res = self.response(req)
        res["model_settings_sha256"] = "other"
        with self.assertRaisesRegex(StudyError, "drift"):
            validate_response(res, req, self.root)

    def test_wrong_supplement_rejected(self):
        req = self.plan()["tasks"][0]["request"]
        res = self.response(req)
        res["stages"][0]["supplement_sha256"] = "wrong"
        with self.assertRaisesRegex(StudyError, "supplement"):
            validate_response(res, req, self.root)

    def test_raw_trace_edit_rejected(self):
        req = self.plan()["tasks"][0]["request"]
        res = self.response(req)
        (self.root / "STEP1.request.json").write_text("edited", encoding="utf-8")
        with self.assertRaisesRegex(StudyError, "hash mismatch"):
            validate_response(res, req, self.root)

    def test_only_existing_step2_skip_allowed(self):
        req = self.plan()["tasks"][0]["request"]
        res = self.response(req)
        for i in (1, 2):
            res["stages"][i] = {"stage": STAGES[i], "status": "skipped",
                "skip_reason": "no_pending_candidates", "pending_count": 0}
        validate_response(res, req, self.root)
        res["stages"][1]["pending_count"] = 1
        with self.assertRaises(StudyError):
            validate_response(res, req, self.root)

    def test_all_three_arms_scored(self):
        p = self.plan()
        rows = self.rows(p, lambda r: ("classified", "LABEL-B" if r["arm"] == "A" else "LABEL-A"))
        report = summarize(p, rows)
        self.assertEqual(report["comparisons"]["B"]["decision"], "candidate_requires_semantic_and_transfer_review")
        self.assertEqual(report["comparisons"]["C"]["improved_cases"], ["test-1", "test-2"])
        self.assertFalse(report["production_approved"])

    def test_regression_not_hidden_by_other_improvement(self):
        p = self.plan()
        def predict(r):
            correct = (r["case_id"] == "test-1") == (r["arm"] == "A")
            return "classified", "LABEL-A" if correct else "LABEL-B"
        report = summarize(p, self.rows(p, predict))
        self.assertEqual(report["comparisons"]["B"]["regressed_cases"], ["test-1"])
        self.assertEqual(report["comparisons"]["B"]["decision"], "rejected_regression_or_instability")

    def test_abstention_not_correct(self):
        p = self.plan()
        report = summarize(p, self.rows(p, lambda r: ("abstain", None)))
        self.assertEqual(report["case_results"]["test-1"]["arms"]["B"]["correct"], 0)
        self.assertEqual(report["comparisons"]["B"]["decision"], "no_observed_improvement")

    def test_missing_run_is_inconclusive(self):
        p = self.plan()
        rows = self.rows(p, lambda r: ("classified", "LABEL-A"))[:-1]
        self.assertEqual(summarize(p, rows)["comparisons"]["B"]["decision"], "inconclusive")

    def test_duplicate_results_rejected(self):
        p = self.plan()
        rows = self.rows(p, lambda r: ("classified", "LABEL-A"))
        with self.assertRaisesRegex(StudyError, "duplicate"):
            summarize(p, rows + rows[:1])

    def test_new_instability_is_failure(self):
        p = self.plan()
        rows = self.rows(p, lambda r: ("classified", "LABEL-A" if r["arm"] == "B" and r["repeat"] == 0 else "LABEL-B"))
        report = summarize(p, rows)
        self.assertEqual(report["comparisons"]["B"]["decision"], "rejected_regression_or_instability")

    def test_seen_cases_cannot_claim_transfer(self):
        self.spec["split_mode"] = "seen_smoke"
        p = self.plan()
        report = summarize(p, self.rows(p, lambda r: ("classified", "LABEL-A")))
        self.assertEqual(report["comparisons"]["B"]["decision"], "smoke_only_not_transfer_evidence")

    def test_pilot_not_final_acceptance(self):
        self.spec["repeats"] = 1
        p = self.plan()
        report = summarize(p, self.rows(p, lambda r: ("classified", "LABEL-A")))
        self.assertEqual(report["comparisons"]["B"]["decision"], "inconclusive")

    def test_actual_cli_prepare_and_incomplete_report(self):
        script = Path(__file__).resolve().parents[1] / "tools" / "boundary_eval.py"
        manifest, lock, results, report = [self.root / n for n in ("study.json", "lock.json", "results.jsonl", "report.json")]
        write_json(manifest, self.spec)
        subprocess.run([sys.executable, str(script), "prepare", str(manifest), "--out", str(lock)],
                       check=True, capture_output=True)
        results.write_text("", encoding="utf-8")
        subprocess.run([sys.executable, str(script), "report", str(lock), str(results), "--out", str(report)],
                       check=True, capture_output=True)
        self.assertFalse(json.loads(report.read_text())["complete"])

    def test_actual_subprocess_failure_kept_for_every_trial(self):
        self.spec["repeats"] = 1
        self.spec["adapter_command"] = [sys.executable, "-c", "import sys; sys.exit(3)", "{request}", "{response}"]
        p = self.plan()
        out = self.root / "runs"
        run_plan(p, out)
        rows = [json.loads(line) for line in (out / "results.jsonl").read_text().splitlines()]
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(r["status"] == "error" for r in rows))
        self.assertEqual(summarize(p, rows)["comparisons"]["B"]["decision"], "inconclusive")
        with self.assertRaisesRegex(StudyError, "new output"):
            run_plan(p, out)


if __name__ == "__main__":
    unittest.main()
