# -*- coding: utf-8 -*-
"""decide() の分岐テスト。LLM出力は作り物。"""
import json
import route_v3 as R

RULES = json.load(open("rules_E.json", encoding="utf-8"))
L = {R.core_label(r["ラベル"]): r["ラベル"] for r in RULES}   # 例：L["リーダ"] = "⑫リーダー"
KIND = {"オプション": "場所型", "レーザ": "場所型", "スキャナ": "場所型", "タグ関連": "技術型", "低圧電源": "技術型",
        "高圧電源": "技術型", "モータ": "技術型", "シート搬送": "場所型", "FAN": "技術型", "定着": "場所型",
        "センサ": "技術型", "その他": "残余", "リーダ": "場所型", "インクジェット": "場所型"}
_, IDX = R.build_rule_units(RULES)
UID = {(v["ラベル"], v["記載"]): k for k, v in IDX.items()}

def uid(core, text): return UID[(L[core], text)]

def facts(area="ユニット", owners=(), explicit=(), best="", tech=(), place="x", no_owner=False):
    return {
        "場所の担当項目が表に無い": no_owner,
        "各項目の種類": [{"ラベル": L[c], "種類": k} for c, k in KIND.items()],
        "場所": place, "場所の区分": area, "場所の根拠区分": "本文確認",
        "場所の担当項目": [L[o] if o in L else o for o in owners],
        "明示記載": [{"記載ID": u, "記載の種類": t} for u, t in explicit],
        "最も直接な記載ID": best,
        "技術の一致": [{"ラベル": L[c], "一致": c in tech} for c, k in KIND.items() if k == "技術型"],
    }

def sel(*cores): return {L[c]: f"C{i+1}" for i, c in enumerate(cores)}

CASES = []
def case(name, f, s, want, honrai=""):
    CASES.append((name, f, s, want, honrai))

# 場所
case("場所の担当が選択可能", facts(owners=["リーダ"]), sel("センサ", "リーダ", "低圧電源"), "⑫リーダー")
case("場所の担当が選択不可・その他は選択可能 → 自信無", facts(owners=["リーダ"], tech=["低圧電源"]), sel("センサ", "低圧電源", "その他"), "自信無", "⑫リーダー")
case("場所の担当が2つ → 自信無", facts(owners=["レーザ", "スキャナ"]), sel("レーザ", "スキャナ"), "自信無")
case("場所の担当に技術型 → 自信無（技術へ落とさない）", facts(owners=["低圧電源"], tech=["低圧電源"]), sel("低圧電源", "リーダ"), "自信無")
case("担当が表に無いと明言 → 技術", facts(owners=[], tech=["高圧電源"], no_owner=True), sel("高圧電源", "低圧電源"), "⑥高圧電源")
case("担当が空・明言なし → 自信無（技術へ落とさない）", facts(owners=[], tech=["低圧電源"]), sel("低圧電源", "リーダ"), "自信無")
case("特定できない → 自信無", facts(area="特定できない"), sel("リーダ"), "自信無")
# 技術
case("本体共通・技術1つ → 技術", facts(area="本体共通", tech=["低圧電源"]), sel("低圧電源", "定着"), "⑤低圧電源")
case("本体共通・技術2つ → 自信無", facts(area="本体共通", tech=["低圧電源", "FAN"]), sel("低圧電源", "FAN"), "自信無")
case("限定なし・技術なし・その他あり → 残余", facts(area="限定なし"), sel("低圧電源", "その他"), "⑭その他")
# 明示記載
case("振分けの記載 → 振り先", facts(owners=["リーダ"], explicit=[(uid("シート搬送", "リーダの紙搬送は13リーダー へ"), "")]), sel("シート搬送", "リーダ"), "⑫リーダー")
case("シート搬送を選んでも、場所の担当がリーダーなら備考でリーダーへ", facts(owners=["リーダ"], explicit=[(uid("シート搬送", "給紙動作"), "")]), sel("シート搬送", "リーダ"), "⑫リーダー")
case("技術型の用途記載＋技術一致 → 技術（場所より先）", facts(owners=["シート搬送"], explicit=[(uid("モータ", "レジ駆動"), "用途")], tech=["モータ"]), sel("シート搬送", "モータ"), "⑦モータ制御")
case("技術型の技術自体の記載は無視 → 場所", facts(owners=["リーダ"], explicit=[(uid("低圧電源", "フィルタ構成"), "技術自体")], tech=["低圧電源"]), sel("低圧電源", "リーダ"), "⑫リーダー")
case("用途記載でも技術一致がfalseなら無視 → 場所", facts(owners=["定着"], explicit=[(uid("FAN", "定着制御との連携"), "用途")]), sel("FAN", "定着"), "⑩定着制御")
case("観点語を挙げても無視 → 場所", facts(owners=["リーダ"], explicit=[(uid("低圧電源", "ユニット構成(1コントローラ/2コントローラ等)"), "用途")], tech=["低圧電源"]), sel("低圧電源", "リーダ"), "⑫リーダー")
case("明示が2項目・最も直接あり → そちら", facts(owners=["シート搬送"], explicit=[(uid("モータ", "レジ駆動"), "用途"), (uid("シート搬送", "レジ動作"), "")], best=uid("モータ", "レジ駆動"), tech=["モータ"]), sel("シート搬送", "モータ"), "⑦モータ制御")
case("明示が2項目・最も直接なし → 場所で決める", facts(owners=["リーダ"], explicit=[(uid("リーダ", "光源制御"), ""), (uid("レーザ", "LED"), "")]), sel("リーダ", "レーザ"), "⑫リーダー")
case("場所型の明示が場所の担当より優先（給紙デッキ）", facts(owners=["オプション"], explicit=[(uid("シート搬送", "給紙デッキの制御(エアー給紙など)"), "")]), sel("オプション", "シート搬送"), "⑧シート搬送制御")
case("明示の項目が選択不可 → 自信無", facts(area="本体共通", explicit=[(uid("定着", "フリッカ対応"), "")], tech=["低圧電源"]), sel("低圧電源"), "自信無", "⑩定着制御")
case("残余の記載は明示にしない", facts(area="限定なし", explicit=[(uid("その他", "上記以外"), "")], tech=["低圧電源"]), sel("低圧電源", "その他"), "⑤低圧電源")
case("存在しない記載IDは無視して要確認", facts(owners=["リーダ"], explicit=[("R99-1", "")]), sel("リーダ"), "⑫リーダー")
case("番号なしのラベルも解決", {**facts(owners=[]), "場所の担当項目": ["リーダー"]}, sel("リーダ"), "⑫リーダー")

ok = 0
for name, f, s, want, honrai in CASES:
    r = R.decide(f, RULES, s)
    good = r["ラベル"] == want and (not honrai or r["本来の届け先"] == honrai)
    ok += good
    print(("OK " if good else "NG ") + f"{name:　<34} → {r['ラベル']:<10} {('本来='+r['本来の届け先']) if r['本来の届け先'] else ''}  [{r['適用した順位']}] 要確認={r['要確認']}")
    if not good:
        print("    ", json.dumps(r, ensure_ascii=False))
print(f"\n{ok}/{len(CASES)}")
assert ok == len(CASES)
