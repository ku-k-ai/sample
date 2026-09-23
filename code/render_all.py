# -*- coding: utf-8 -*-
import json, glob, os
import route_v3 as R
rules = json.load(open("rules_E.json", encoding="utf-8"))
for p in sorted(glob.glob("cases/*.json")):
    c = json.load(open(p, encoding="utf-8"))
    system, user = R.render_prompt(c["独立請求項"], c["前段の発明整理"], c["前段の工程推定"], c["本文の原文引用"], rules)
    out = f"prompts/{c['case_id']}.txt"
    open(out, "w", encoding="utf-8").write(f"<<SYSTEM>>\n{system}\n\n<<USER>>\n{user}\n")
    print(out, len(system) + len(user))
