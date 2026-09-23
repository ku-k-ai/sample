# -*- coding: utf-8 -*-
"""
届け先の決定 v3

役割分担
- LLM（prompt_v3.txt）：事実だけを整理する。各項目の種類、請求物、場所、場所の担当項目、明示記載、技術の一致。
  LLMには「選択可能候補」を見せない。事実が候補の顔ぶれに引きずられないようにするため。
- このファイル：順番と「自信無」をコードで決める。
    1 明示記載   表の記載単位が特徴部分を名前で挙げている（振分けの振り先 → 最も直接 → 単独）
    2 場所       場所の担当項目（場所型）
    3 技術       場所が本体共通・限定なし、または場所の担当項目が表に無いときだけ
    4 残余
  決まった項目が選択可能に無ければ、代用せず「自信無」にして「本来の届け先」に書く。

ルール表に列は足さない。記載単位の分割・観点語の印・振り先の解決は、既存の表から毎回ここで作る。
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
PROMPT_PATH = HERE / "prompt_v3.txt"

KINDS = ("場所型", "技術型", "残余")
AREAS = ("ユニット", "本体共通", "限定なし", "特定できない")

# 観点語：対象の名前ではなく、どの項目にも出てくる観点の語。記載単位の頭（括弧の前）がこれに一致したら観点語。
# 表に2項目以上出てくる頭も自動で観点語にする（build_rule_units）。他部門の表では必要に応じて足す。
SEED_ASPECT_HEADS = {
    "回路構成", "駆動回路構成", "駆動制御", "制御方式", "ユニット構成", "駆動系構成",
    "異常検知", "故障検知", "エラー検知", "フェールセーフ思想", "新方式", "タイミング",
    "低コスト", "高精度", "省エネ", "起動/立上げ", "起動", "立上げ", "実行頻度",
}


# ---------------------------------------------------------------- 文字列の下ごしらえ

def norm(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def clean_cell(s: Any) -> str:
    s = "" if s is None else str(s)
    if s.strip().lower() in ("nan", "none", "null"):
        return ""
    return s.replace("¥n", "\n").replace("\\n", "\n").strip()


def core_label(label: str) -> str:
    """番号と末尾の「制御」「ー」を外す（⑩定着制御→定着、⑫リーダー→リーダ）。"""
    s = unicodedata.normalize("NFKC", label or "").strip()
    s = re.sub(r"^[①-⑳\d\s.．)）]+", "", s)
    s = re.sub(r"制御$", "", s)
    return s.rstrip("ー") or s


def split_top(text: str, seps: str = "、。・\n") -> list[str]:
    """括弧の外にある区切りでだけ分ける。"""
    out, buf, depth = [], [], 0
    for ch in text:
        if ch in "(（":
            depth += 1
        elif ch in ")）":
            depth = max(0, depth - 1)
        if depth == 0 and ch in seps:
            if "".join(buf).strip():
                out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if "".join(buf).strip():
        out.append("".join(buf).strip())
    return out


def head_of(unit: str) -> str:
    return norm(re.split(r"[(（]", unit, maxsplit=1)[0])


# ---------------------------------------------------------------- ルール表 → 記載単位

ROUTE_RE = re.compile(r"^(?P<what>.+?)は\s*(?P<to>[^、。]+?)\s*へ")


def resolve_label(text: str, labels: list[str]) -> str | None:
    """文字列からルール表のラベルを1つに決める（完全一致 → 正規化一致 → 核の語の包含）。"""
    if text in labels:
        return text
    n = norm(text)
    for lab in labels:
        if norm(lab) == n:
            return lab
    hits = [lab for lab in labels if core_label(lab) and norm(core_label(lab)) in n]
    if len(hits) == 1:
        return hits[0]
    hits = [lab for lab in labels if n and n in norm(lab)]
    return hits[0] if len(hits) == 1 else None


def build_rule_units(rules: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    """
    rules: [{"ラベル", "担当範囲の定義", "備考・振分け"}]（既存の表のまま）
    返り値: (LLMに渡す記載単位つきの表, 記載ID → 情報 の索引)
    """
    labels = [r["ラベル"] for r in rules]
    staged = []
    for i, r in enumerate(rules, 1):
        pre = f"R{i:02d}"
        d_units = []
        for u in split_top(clean_cell(r.get("担当範囲の定義"))):
            if norm(u) not in {norm(x) for x in d_units}:   # 同じ項目の中の重複は1つに
                d_units.append(u)
        b_units = split_top(clean_cell(r.get("備考・振分け")), seps="。\n")
        scope = [u for u in d_units if u.endswith("に関する")]
        d_units = [u for u in d_units if not u.endswith("に関する")]
        staged.append((pre, r["ラベル"], scope, d_units, b_units))

    head_labels: dict[str, set] = {}
    for _, lab, _, d_units, _ in staged:
        for u in d_units:
            head_labels.setdefault(head_of(u), set()).add(lab)
    auto_aspect = {h for h, labs in head_labels.items() if len(labs) >= 2}
    aspect_heads = {norm(h) for h in SEED_ASPECT_HEADS} | auto_aspect

    table, index = [], {}
    for pre, lab, scope, d_units, b_units in staged:
        entry = {"ラベル": lab, "適用範囲": "、".join(scope), "記載単位": [], "振分け": []}
        for n, u in enumerate(d_units, 1):
            uid = f"{pre}-{n}"
            asp = head_of(u) in aspect_heads
            entry["記載単位"].append({"ID": uid, "記載": u, "観点語": asp})
            index[uid] = {"ラベル": lab, "記載": u, "観点語": asp, "振り先": None}
        for n, u in enumerate(b_units, 1):
            uid = f"{pre}-B{n}"
            m = ROUTE_RE.match(u)
            target = resolve_label(m.group("to"), labels) if m else None
            if target and target != lab:
                entry["振分け"].append({"ID": uid, "記載": u, "振り先": target})
                index[uid] = {"ラベル": lab, "記載": u, "観点語": False, "振り先": target}
            else:
                entry["記載単位"].append({"ID": uid, "記載": u, "観点語": False})
                index[uid] = {"ラベル": lab, "記載": u, "観点語": False, "振り先": None}
        table.append(entry)
    return table, index


# ---------------------------------------------------------------- プロンプト

def dumps(o: Any) -> str:
    return json.dumps(o, ensure_ascii=False, indent=2)


def render_prompt(claims: str, step0: Any, process: Any, quotes: list, rules: list[dict],
                  template_path: Path = PROMPT_PATH) -> tuple[str, str]:
    table, _ = build_rule_units(rules)
    t = template_path.read_text(encoding="utf-8")
    t = (t.replace("{{INDEPENDENT_CLAIMS}}", strip_stage_notes(claims))
          .replace("{{STEP0_INVENTION_PROFILE_JSON}}", dumps(step0 or {}))
          .replace("{{PROCESS_AND_DEVICE_CONTEXT_JSON}}", dumps(process or {}))
          .replace("{{BODY_QUOTES_JSON}}", dumps(quotes or []))
          .replace("{{RULE_UNITS_JSON}}", dumps(table)))
    system, user = t.split("<<USER>>", 1)
    return system.replace("<<SYSTEM>>", "").strip(), user.strip()


def strip_stage_notes(claims: str) -> str:
    """独立請求項の頭に残る前段向けの指示文（「LLM用分類コンテキストパック」等）を落とす。"""
    m = re.search(r"請求項\s*[1１]", claims)
    return claims[m.start():].strip() if m else claims.strip()


# ---------------------------------------------------------------- 既存パイプラインのJSONから入力を作る

def quotes_from_candidates(candidates: list[dict]) -> list[dict]:
    """CANDIDATES_WITH_STEP1_STEP2_JSON から本文の原文引用だけを重複なしで取り出す（前段の判定文は渡さない）。"""
    seen, out = set(), []
    for c in candidates:
        for q in c.get("本文確認結果") or []:
            for r in q.get("根拠引用") or []:
                key = r.get("段落番号") or r.get("抜粋ID")
                if key and key not in seen and r.get("原文引用"):
                    seen.add(key)
                    out.append({"段落": r.get("段落番号", ""), "原文": r["原文引用"]})
    return out


def selectable_map(candidates: list[dict], selectable_ids: list[str]) -> dict[str, str]:
    """ラベル → 候補ID（選択可能なものだけ）。"""
    sel = set(selectable_ids)
    return {c["ラベル"]: c["候補ID"] for c in candidates if c.get("候補ID") in sel}


# ---------------------------------------------------------------- LLM出力の読み込みと検証

def parse_facts(text: str) -> dict:
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("JSONが見つからない")
    return json.loads(s[start:end + 1])


# ---------------------------------------------------------------- 判定

def decide(facts: dict, rules: list[dict], selectable: dict[str, str]) -> dict:
    """
    facts: LLMの出力JSON
    rules: ルール表（既存の列のまま）
    selectable: ラベル → 候補ID（選択可能なものだけ）
    """
    labels = [r["ラベル"] for r in rules]
    _, index = build_rule_units(rules)
    notes: list[str] = []   # 警告：LLM出力の不備。要確認にする
    logs: list[str] = []    # 記録：判定の途中経過

    def lab_of(x: str) -> str | None:
        r = resolve_label(x or "", labels)
        if x and not r:
            notes.append(f"表に無いラベル「{x}」を無視")
        return r

    # 種類
    kinds: dict[str, str] = {}
    for k in facts.get("各項目の種類") or []:
        lab = lab_of(k.get("ラベル", ""))
        if lab and k.get("種類") in KINDS:
            kinds[lab] = k["種類"]
    missing = [l for l in labels if l not in kinds]
    if missing:
        notes.append(f"種類が無い項目：{'、'.join(missing)}")

    area = facts.get("場所の区分")
    if area not in AREAS:
        notes.append(f"場所の区分が不正（{area}）→ 特定できないとして扱う")
        area = "特定できない"

    # 技術の一致（技術型だけ有効）
    tech_ok: set[str] = set()
    for t in facts.get("技術の一致") or []:
        lab = lab_of(t.get("ラベル", ""))
        if lab and kinds.get(lab) == "技術型" and t.get("一致") is True:
            tech_ok.add(lab)

    # 場所の担当項目（場所型だけ有効）
    owners: list[str] = []
    bad_owner = False
    if area == "ユニット":
        for x in facts.get("場所の担当項目") or []:
            lab = lab_of(x)
            if not lab:
                continue
            if kinds.get(lab) != "場所型":
                notes.append(f"場所の担当項目「{lab}」は場所型でないので無視")
                bad_owner = True
                continue
            if lab not in owners:
                owners.append(lab)

    out = {
        "最終判定": "自信無", "ラベル": "自信無", "適用した順位": "なし", "本来の届け先": "",
        "要確認": False, "理由": "",
        "場所": facts.get("場所", ""), "場所の区分": area, "場所の根拠区分": facts.get("場所の根拠区分", ""),
        "場所の担当項目": owners, "技術の一致": sorted(tech_ok), "明示記載": [], "注意": notes, "記録": logs,
    }

    def finish(rank: str, label: str | None, reason: str) -> dict:
        out["適用した順位"] = rank
        out["理由"] = reason
        if label and label in selectable:
            out.update({"最終判定": selectable[label], "ラベル": label})
        else:
            out.update({"最終判定": "自信無", "ラベル": "自信無"})
            if label:
                out["本来の届け先"] = label
                out["理由"] = f"{reason}。ただし {label} は選択可能に無い（代用しない）"
        if out["最終判定"] == "自信無" or notes:
            out["要確認"] = True
        return out

    if area == "特定できない":
        return finish("なし", None, "場所を位置づけられない")

    # 1 明示記載 -----------------------------------------------------------
    cited: list[tuple[str, str, str]] = []   # (種別, ラベル, 記載ID)
    routes: set[str] = set()
    for e in facts.get("明示記載") or []:
        uid = (e.get("記載ID") or "").strip()
        u = index.get(uid)
        if not u:
            notes.append(f"存在しない記載ID「{uid}」を無視")
            continue
        lab, kind = u["ラベル"], kinds.get(u["ラベル"])
        if u["観点語"]:
            notes.append(f"観点語の記載単位 {uid}「{u['記載']}」は明示記載にしない")
            continue
        if kind == "残余":
            continue
        if u["振り先"]:
            routes.add(u["振り先"])
            out["明示記載"].append(f"{uid}（{lab}の振分け → {u['振り先']}）")
            continue
        if kind == "技術型":
            if e.get("記載の種類") != "用途":
                logs.append(f"{uid}「{u['記載']}」は技術自体の記載なので明示記載にしない")
                continue
            if lab not in tech_ok:
                logs.append(f"{uid}「{u['記載']}」は用途の記載だが、技術の一致が false なので明示記載にしない")
                continue
        cited.append(("unit", lab, uid))
        out["明示記載"].append(f"{uid}「{u['記載']}」（{lab}）")

    if len(routes) >= 2:
        return finish("1 明示記載", None, f"振分けの振り先が複数（{'、'.join(sorted(routes))}）")
    if len(routes) == 1:
        target = next(iter(routes))
        return finish("1 明示記載", target, f"備考・振分けにより {target}")

    cited_labels = []
    for _, lab, _ in cited:
        if lab not in cited_labels:
            cited_labels.append(lab)
    chosen = None
    best = (facts.get("最も直接な記載ID") or "").strip()
    if len(cited_labels) == 1:
        chosen = cited_labels[0]
    elif len(cited_labels) >= 2:
        best_lab = next((lab for _, lab, uid in cited if uid == best), None)
        if best_lab:
            chosen = best_lab
        else:
            notes.append(f"明示記載が複数の項目にあり、最も直接な記載が決まっていない（{'、'.join(cited_labels)}）→ 場所で決める")
    if chosen:
        # 選んだ項目の備考が、場所の担当項目へ振り分けている場合はそちら（例：リーダの紙搬送は リーダーへ）
        for uid, u in index.items():
            if u["ラベル"] == chosen and u["振り先"] and u["振り先"] in owners:
                return finish("1 明示記載", u["振り先"], f"{chosen} の備考が {u['振り先']} へ振り分けている")
        uids = [uid for _, lab, uid in cited if lab == chosen]
        uid = best if best in uids else uids[0]
        return finish("1 明示記載", chosen, f"{chosen} の記載「{index[uid]['記載']}」が特徴部分を挙げている")

    # 2 場所 ---------------------------------------------------------------
    no_owner_declared = facts.get("場所の担当項目が表に無い") is True
    if area == "ユニット" and not owners and not bad_owner and not no_owner_declared:
        # 場所を決めたのに担当項目が空。表に無いとも言っていない＝情報が足りない。技術型へ落とさない
        return finish("2 場所", None, "場所の担当項目が空で、「表に無い」とも書かれていない")
    if area == "ユニット" and not owners and bad_owner:
        # 場所の担当に技術型を書いてきた＝場所の読みが崩れている。技術型へ落とさず止める
        return finish("2 場所", None, "場所の担当項目に場所型でない項目が書かれている")
    if area == "ユニット" and owners:
        if len(owners) >= 2:
            return finish("2 場所", None, f"場所の担当が複数（{'、'.join(owners)}）")
        return finish("2 場所", owners[0], f"場所「{facts.get('場所', '')}」の担当は {owners[0]}")

    # 3 技術 ---------------------------------------------------------------
    if area in ("本体共通", "限定なし") or (area == "ユニット" and not owners):
        techs = [l for l in labels if l in tech_ok]
        if len(techs) == 1:
            return finish("3 技術", techs[0], f"場所が{area if area != 'ユニット' else '担当の無いユニット'}で、特徴部分は {techs[0]} の技術そのもの")
        if len(techs) >= 2:
            return finish("3 技術", None, f"技術の一致が複数（{'、'.join(techs)}）")

    # 4 残余 ---------------------------------------------------------------
    residual = [l for l in labels if kinds.get(l) == "残余"]
    if residual:
        return finish("4 残余", residual[0], "場所・技術のどれにも当たらない")
    return finish("なし", None, "当たる項目が無い")


def early_keep(facts: dict, rules: list[dict]) -> list[str]:
    """
    ゲート保護用。請求項だけ（本文引用なし）で同じプロンプトを先に回し、その結果から
    「前段のゲートで落としてはいけない項目」を返す（場所の担当項目＋判定結果）。
    """
    everything = {r["ラベル"]: r["ラベル"] for r in rules}
    r = decide(facts, rules, everything)
    keep = list(r["場所の担当項目"])
    if r["ラベル"] not in ("自信無",) and r["ラベル"] not in keep:
        keep.append(r["ラベル"])
    return keep
