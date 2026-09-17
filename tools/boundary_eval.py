"""Locked A/B/C evaluation runner. Standard library only; no model SDK or classifier.

The company adapter executes the EXISTING staged pipeline. This tool does not
establish semantic correctness, provenance completeness, or real API execution
merely because a response claims it. Keep plans, inputs and traces private.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

STAGES = ("STEP1", "STEP2_PLAN", "STEP2_REVIEW", "FINAL_DECISION")
ARMS = ("A", "B", "C")
SPLITS = ("reference_excluded_known17", "sealed_new_cases", "seen_smoke")
HEADER = ("以下は別案件の解釈参照資料です。正式ルールの追加・上書きではありません。"
          "対象案件の事実は対象案件の原文で確認し、参照案件の事実を移植しないでください。"
          "当該STEPの役割、入力範囲、出力形式は変更しません。\n")


class StudyError(ValueError):
    pass


def require(condition: Any, message: str) -> None:
    if not condition:
        raise StudyError(message)


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                               allow_nan=False) + "\n", encoding="utf-8")


def check_texts(views: dict[str, str], name: str) -> None:
    require(set(views) == set(STAGES), f"{name}: all four stage views are required")
    require(all(isinstance(x, str) and x.strip() for x in views.values()),
            f"{name}: empty stage view")


def no_placeholders(value: Any) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            require(k.lower() not in {"api_key", "password", "secret", "access_token",
                                      "authorization"}, "Do not put credentials in the manifest")
            no_placeholders(v)
    elif isinstance(value, list):
        for v in value:
            no_placeholders(v)
    elif isinstance(value, str):
        require("__FILL_" not in value, "Fill all __FILL_* placeholders before preparing")


def make_plan(spec: dict[str, Any], base: Path) -> dict[str, Any]:
    """Validate declared provenance and create gold-free per-trial requests."""
    no_placeholders(spec)
    require(spec.get("version") == 1, "Unsupported version")
    require(spec.get("split_mode") in SPLITS, "Unknown split_mode")
    require(isinstance(spec.get("study_id"), str) and spec["study_id"], "study_id required")
    require(isinstance(spec.get("model_settings"), dict) and spec["model_settings"],
            "Freeze actual model/settings, not a recommended model")
    for key in ("repeats", "max_runs", "max_wall_seconds", "per_run_timeout_seconds"):
        require(type(spec.get(key)) is int and spec[key] > 0, f"Positive integer {key} required")
    command = spec.get("adapter_command", [])
    require(isinstance(command, list) and command and
            all(isinstance(s, str) for s in command), "adapter_command must be a string array")
    require(sum(s.count("{request}") for s in command) == 1 and
            sum(s.count("{response}") for s in command) == 1,
            "adapter_command needs exactly one {request} and {response}")
    require(spec.get("freeze_files"), "Freeze rules, prompts, pipeline and adapter source files")
    frozen = {}
    for name in spec["freeze_files"]:
        path = (base / name).resolve()
        require(path.is_file(), f"Missing frozen file: {path}")
        frozen[str(path)] = file_digest(path)
    cases = spec.get("cases", [])
    require(cases and len(cases) == spec.get("expected_case_count"), "Case count mismatch")
    require(len({c["case_id"] for c in cases}) == len(cases), "Duplicate case_id")
    refs = spec.get("references", {})
    for rid, card in refs.items():
        require(card.get("status") == "approved", f"Unapproved reference: {rid}")
        require(card.get("source_family_ids") and card.get("source_case_ids"),
                f"Reference provenance missing: {rid}")
        require(card.get("gold_origin") in {"requester", "user", "reviewed_synthetic"},
                f"Reference label source missing: {rid}")
        check_texts(card["views"], rid)
    tasks, gold = [], {}
    rng = random.Random(spec.get("order_seed", 0))
    for case in cases:
        cid, family = case["case_id"], case["family_id"]
        require(isinstance(cid, str) and cid and isinstance(family, str) and family,
                "case_id and family_id required; derivatives share family_id")
        require(case.get("kind") in {"real", "control", "synthetic"}, f"Invalid case kind: {cid}")
        require(case.get("gold_origin") in {"requester", "user", "reviewed_synthetic"},
                f"No authorized gold source: {cid}")
        payload = case["input"]
        require(set(payload) == {"claims", "description", "candidates"},
                f"{cid}: only claims, description and candidates go to the adapter")
        require(isinstance(payload["claims"], str) and payload["claims"].strip(), "Empty claims")
        require(isinstance(payload["description"], str) and payload["description"].strip(),
                "Full description required (stage exposure remains adapter's responsibility)")
        require(isinstance(payload["candidates"], list) and payload["candidates"], "Empty candidates")
        labels = [c["label"] for c in payload["candidates"]]
        require(all(isinstance(x, str) and x for x in labels) and len(set(labels)) == len(labels),
                f"{cid}: duplicate/invalid canonical label")
        require(isinstance(case.get("gold_label"), str) and case["gold_label"], f"{cid}: missing gold label")
        for candidate in payload["candidates"]:
            require(set(candidate) == {"candidate_id", "label", "official_rule"},
                    "Do not pass old results, gold or rationale through candidates")
            require(isinstance(candidate["official_rule"], (str, dict)), "Invalid official_rule")
        require(len({c["candidate_id"] for c in payload["candidates"]}) == len(labels),
                f"{cid}: duplicate candidate_id")
        fold = spec["folds"][cid]
        selected = fold["reference_ids"]
        require(selected and len(set(selected)) == len(selected), f"{cid}: empty/duplicate reference selection")
        require(all(r in refs for r in selected), f"{cid}: unknown reference")
        families = set().union(*(set(refs[r]["source_family_ids"]) for r in selected))
        source_cases = set().union(*(set(refs[r]["source_case_ids"]) for r in selected))
        if spec["split_mode"] != "seen_smoke":
            require(family not in families and cid not in source_cases,
                    f"{cid}: target/family leaked through a reference or derivative")
        generalization = fold["generalization"]
        require(generalization.get("status") == "approved", f"{cid}: unapproved generalization")
        require(len(generalization["source_reference_ids"]) == len(selected) and
                set(generalization["source_reference_ids"]) == set(selected),
                f"{cid}: C must use exactly B's source references")
        require(set(generalization["source_family_ids"]) == families and
                set(generalization["source_case_ids"]) == source_cases,
                f"{cid}: C provenance must match B, including all contributing examples")
        check_texts(generalization["views"], f"{cid}/C")
        supplements = {"A": {s: "" for s in STAGES},
                       "B": {s: HEADER + "\n\n".join(refs[r]["views"][s] for r in selected)
                             for s in STAGES},
                       "C": {s: HEADER + generalization["views"][s] for s in STAGES}}
        gold[cid] = {k: case[k] for k in ("gold_label", "gold_origin", "family_id", "kind")}
        gold[cid]["gold_absent_from_input_candidates"] = case["gold_label"] not in labels
        for repeat in range(spec["repeats"]):
            order = list(ARMS)
            rng.shuffle(order)
            for arm in order:
                request = {"trial_id": f"t{len(tasks):05d}", "case_id": cid,
                           "arm": arm, "repeat": repeat, "input": payload,
                           "reference_by_stage": supplements[arm],
                           "model_settings": spec["model_settings"],
                           "frozen_sha256": digest(frozen)}
                tasks.append({"request": request, "request_sha256": digest(request)})
    require(len(tasks) <= spec["max_runs"], "Planned runs exceed max_runs; freeze a smaller pilot")
    return {"version": 1, "study_id": spec["study_id"], "split_mode": spec["split_mode"],
            "model_settings": spec["model_settings"], "frozen_files": frozen,
            "source_manifest_sha256": digest(spec), "repeats": spec["repeats"],
            "max_wall_seconds": spec["max_wall_seconds"],
            "per_run_timeout_seconds": spec["per_run_timeout_seconds"],
            "adapter_command": command, "gold": gold, "tasks": tasks}


def load_plan(path: Path) -> dict[str, Any]:
    envelope = read_json(path)
    require(digest(envelope["plan"]) == envelope["sha256"], "Plan modified after locking")
    return envelope["plan"]


def check_frozen(plan: dict[str, Any]) -> None:
    for name, expected in plan["frozen_files"].items():
        path = Path(name)
        require(path.is_file() and file_digest(path) == expected, f"Frozen file changed: {path}")


def validate_response(response: dict[str, Any], request: dict[str, Any], directory: Path) -> None:
    require(response.get("trial_id") == request["trial_id"], "Wrong trial_id")
    require(response.get("request_sha256") == digest(request), "Wrong request digest")
    require(response.get("execution_kind") == "live_llm", "Mock/dry run is not a live classification result")
    require(response.get("model_settings_sha256") == digest(request["model_settings"]), "Model/settings drift")
    require(response.get("frozen_sha256") == request["frozen_sha256"], "Baseline/rule drift")
    require(response.get("status") in {"classified", "abstain"}, "Not a completed model result")
    label = response.get("final_label")
    if response["status"] == "classified":
        require(label in [c["label"] for c in request["input"]["candidates"]], "Unknown final label")
    else:
        require(label is None, "Abstention must have null final_label")
    stages = response.get("stages", [])
    require([s.get("stage") for s in stages] == list(STAGES), "Missing/reordered staged execution trace")
    statuses = []
    for stage in stages:
        name = stage["stage"]
        statuses.append(stage.get("status"))
        require(stage.get("status") in {"called", "skipped"}, "Invalid stage status")
        if stage["status"] == "skipped":
            require(name in {"STEP2_PLAN", "STEP2_REVIEW"} and
                    stage.get("skip_reason") == "no_pending_candidates" and
                    stage.get("pending_count") == 0, "Only existing no-pending STEP2 skip is allowed")
            continue
        expected = hashlib.sha256(request["reference_by_stage"][name].encode("utf-8")).hexdigest()
        require(stage.get("supplement_sha256") == expected, "Wrong/missing stage supplement")
        for key in ("request_file", "response_file"):
            path = (directory / stage[key]).resolve()
            require(path.is_relative_to(directory.resolve()) and path.is_file(), "Missing/local trace required")
            require(path.stat().st_size > 0, "Empty model trace")
            require(stage.get(key + "_sha256") == file_digest(path), "Raw trace hash mismatch")
    require((statuses[1] == "skipped") == (statuses[2] == "skipped"), "Inconsistent STEP2 skip")


def run_plan(plan: dict[str, Any], out: Path) -> None:
    require(not out.exists(), "Use a new output directory; previous runs must not be overwritten")
    check_frozen(plan)
    out.mkdir(parents=True)
    start = time.monotonic()
    write_json(out / "run.json", {"plan_sha256": digest(plan), "adapter_command": plan["adapter_command"]})
    with (out / "results.jsonl").open("w", encoding="utf-8") as stream:
        for task in plan["tasks"]:
            request = task["request"]
            row = {"trial_id": request["trial_id"], "request_sha256": task["request_sha256"],
                   "status": "error", "final_label": None}
            remaining = plan["max_wall_seconds"] - (time.monotonic() - start)
            if remaining <= 0:
                row["status"] = "not_run_budget"
            else:
                jobdir = out / request["trial_id"]
                jobdir.mkdir()
                req, res = jobdir / "request.json", jobdir / "response.json"
                write_json(req, request)
                command = [v.replace("{request}", str(req.resolve())).replace("{response}", str(res.resolve()))
                           for v in plan["adapter_command"]]
                job_start = time.monotonic()
                try:
                    check_frozen(plan)
                    completed = subprocess.run(command, shell=False, capture_output=True,
                                               text=True, encoding="utf-8", errors="replace",
                                               timeout=min(remaining, plan["per_run_timeout_seconds"]))
                    (jobdir / "adapter.stdout.log").write_text(completed.stdout, encoding="utf-8")
                    (jobdir / "adapter.stderr.log").write_text(completed.stderr, encoding="utf-8")
                    require(completed.returncode == 0, f"Adapter exited {completed.returncode}")
                    check_frozen(plan)
                    response = read_json(res)
                    validate_response(response, request, jobdir)
                    row.update(status=response["status"], final_label=response["final_label"],
                               response_file=f"{request['trial_id']}/response.json",
                               response_sha256=file_digest(res), usage=response.get("usage"))
                except (StudyError, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
                    row["error"] = f"{type(exc).__name__}: {exc}"
                row["elapsed_seconds"] = round(time.monotonic() - job_start, 3)
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()


def summarize(plan: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    tasks = {t["request"]["trial_id"]: t for t in plan["tasks"]}
    observed = {}
    for row in rows:
        tid = row["trial_id"]
        require(tid in tasks and tid not in observed, "Unknown/duplicate result")
        require(row["request_sha256"] == tasks[tid]["request_sha256"], "Result belongs to another request")
        require(row["status"] in {"classified", "abstain", "error", "not_run_budget"}, "Unknown result status")
        observed[tid] = row
    grouped = defaultdict(list)
    for tid, task in tasks.items():
        request = task["request"]
        grouped[(request["case_id"], request["arm"])].append(
            observed.get(tid, {"status": "missing", "final_label": None}))
    report = {"study_id": plan["study_id"], "split_mode": plan["split_mode"],
              "plan_sha256": digest(plan), "expected_trials": len(tasks),
              "observed_trials": len(rows), "case_results": {}, "comparisons": {}}
    complete = len(observed) == len(tasks)
    for cid, truth in plan["gold"].items():
        stats = {}
        for arm in ARMS:
            group = grouped[(cid, arm)]
            complete &= all(r["status"] in {"classified", "abstain"} for r in group)
            stats[arm] = {"correct": sum(r["status"] == "classified" and
                                         r.get("final_label") == truth["gold_label"] for r in group),
                          "runs": len(group),
                          "abstain": sum(r["status"] == "abstain" for r in group),
                          "errors_or_missing": sum(r["status"] not in {"classified", "abstain"} for r in group),
                          "varying": len({(r["status"], r.get("final_label")) for r in group}) > 1,
                          "outputs": [{"status": r["status"], "label": r.get("final_label")} for r in group]}
        report["case_results"][cid] = {"gold": truth, "arms": stats}
    for arm in ("B", "C"):
        improved, regressed, new_unstable = [], [], []
        for cid, case in report["case_results"].items():
            a, b = case["arms"]["A"], case["arms"][arm]
            if b["correct"] > a["correct"]:
                improved.append(cid)
            if b["correct"] < a["correct"]:
                regressed.append(cid)
            if b["varying"] and not a["varying"]:
                new_unstable.append(cid)
        if not complete or plan["repeats"] < 3:
            decision = "inconclusive"
        elif plan["split_mode"] == "seen_smoke":
            decision = "smoke_only_not_transfer_evidence"
        elif regressed or new_unstable:
            decision = "rejected_regression_or_instability"
        elif not improved:
            decision = "no_observed_improvement"
        else:
            decision = "candidate_requires_semantic_and_transfer_review"
        report["comparisons"][arm] = {"vs": "A", "decision": decision,
                                      "improved_cases": improved, "regressed_cases": regressed,
                                      "new_unstable_cases": new_unstable}
    report["complete"] = complete
    report["production_approved"] = False
    report["limitations"] = ["This report does not judge correctness of reasoning or completeness of provenance.",
                              "Known-17 exclusion is not a clean unseen test: existing prompts were tuned on those cases.",
                              "Mock/format tests are not evidence of classification improvement."]
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("manifest", type=Path)
    p.add_argument("--out", required=True, type=Path)
    p = sub.add_parser("run")
    p.add_argument("plan", type=Path)
    p.add_argument("--out", required=True, type=Path)
    p = sub.add_parser("report")
    p.add_argument("plan", type=Path)
    p.add_argument("results", type=Path)
    p.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            require(not args.out.exists(), "Do not overwrite a locked plan")
            plan = make_plan(read_json(args.manifest), args.manifest.resolve().parent)
            write_json(args.out, {"sha256": digest(plan), "plan": plan})
            print(f"Locked {len(plan['tasks'])} staged runs; no model calls executed.")
        elif args.action == "run":
            run_plan(load_plan(args.plan), args.out)
            print("Finished. Inspect errors, actual traces and report; this is not an accuracy claim.")
        else:
            require(not args.out.exists(), "Do not overwrite a report")
            plan = load_plan(args.plan)
            rows = [json.loads(line) for line in args.results.read_text(encoding="utf-8").splitlines() if line.strip()]
            # Recheck recorded model responses/traces, not just self-edited summary labels.
            for row in rows:
                if row["status"] in {"classified", "abstain"}:
                    task = next((t for t in plan["tasks"] if t["request"]["trial_id"] == row["trial_id"]), None)
                    require(task is not None, "Unknown trial")
                    path = (args.results.parent / row["response_file"]).resolve()
                    require(path.is_relative_to(args.results.parent.resolve()), "Response outside run directory")
                    require(file_digest(path) == row["response_sha256"], "Response edited after run")
                    response = read_json(path)
                    validate_response(response, task["request"], path.parent)
                    require(row["status"] == response["status"] and row["final_label"] == response["final_label"],
                            "Summary does not match original response")
            write_json(args.out, summarize(plan, rows))
            print("Report written. production_approved remains false pending semantic/transfer review.")
    except (StudyError, OSError, KeyError, TypeError, ValueError) as exc:
        parser.exit(2, f"{type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    main()
