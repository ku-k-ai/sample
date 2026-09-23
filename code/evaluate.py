# -*- coding: utf-8 -*-
import json, glob, os, sys
import route_v3 as R
CASES_DIR = "cases"
rules = json.load(open("rules_E.json", encoding="utf-8"))
ok = n = 0
rows = []
for p in sorted(glob.glob(f"{CASES_DIR}/*.json")):
    c = json.load(open(p, encoding="utf-8"))
    rp = f"results/{c['case_id']}.json"
    if not os.path.exists(rp):
        rows.append((c["case_id"], "（未実行）", c["期待"], "", "", "", "")); continue
    facts = R.parse_facts(open(rp, encoding="utf-8").read())
    sel = {lab: f"C{i+1}" for i, lab in enumerate(c["選択可能"])}
    d = R.decide(facts, rules, sel)
    n += 1
    good = d["ラベル"] == c["期待"] and (not c["期待_本来の届け先"] or d["本来の届け先"] == c["期待_本来の届け先"])
    ok += good
    rows.append((c["case_id"], d["ラベル"] + (f"（本来={d['本来の届け先']}）" if d["本来の届け先"] else ""),
                 c["期待"] + (f"（本来={c['期待_本来の届け先']}）" if c["期待_本来の届け先"] else ""),
                 "OK" if good else "NG", d["適用した順位"], f"{facts.get('請求物の種類')}/{d['場所の区分']}/{d['場所']}", c["期待の根拠"]))
    if not good or "-v" in sys.argv:
        print(f"--- {c['case_id']}  判定={d['ラベル']} 期待={c['期待']}")
        print("   場所:", d["場所"], "|担当:", d["場所の担当項目"], "|明示:", d["明示記載"], "|技術一致:", d["技術の一致"])
        print("   理由:", d["理由"], "|注意:", d["注意"], "|記録:", d["記録"][:3])
print(f"\n{'ケース':<22}{'判定':<24}{'期待':<24}{'':<4}{'順位':<10}{'種類/区分/場所':<34}根拠")
for r in rows:
    print(f"{r[0]:<22}{r[1]:<24}{r[2]:<24}{r[3]:<4}{r[4]:<10}{r[5]:<34}{r[6]}")
print(f"\n正解 {ok}/{n}")
