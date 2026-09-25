"""明細書の全文から、最終判定に渡す「発明の課題と効果」を見出しで切り出す。

- 切り出すのは【発明が解決しようとする課題】と【発明の効果】の2つだけ。
- 【背景技術】（従来技術・周辺の用途が書かれ、判定を引っ張る）と
  【課題を解決するための手段】（従属項の細部が並び、判定を引っ張る）は切り出さない。
- 見出しが見つからない（US/EPなどで列に含まれない）ときは空のリストを返す。
  その場合、最終判定は独立請求項だけで「何のための構成か」を答える（v4と同じ動き）。
"""
from __future__ import annotations

import re
from typing import Any

WANTED = ("発明が解決しようとする課題", "発明の効果")
# 見出し：【】で囲まれ、先頭が数字でないもの（段落番号【0004】【０００４】は見出しではない）
_HEADING = re.compile(r"【([^】0-9０-９][^】]*)】")
MAX_CHARS = 1500


def extract_problem_and_effect(full_text: str, max_chars: int = MAX_CHARS) -> list[dict[str, Any]]:
    """[{"見出し": ..., "原文": ...}] を返す。原文は次の見出しの直前まで（段落番号は残す）。"""
    if not isinstance(full_text, str) or not full_text:
        return []
    out = []
    for want in WANTED:
        m = re.search("【" + re.escape(want) + "】", full_text)
        if not m:
            continue
        rest = full_text[m.end():]
        nxt = _HEADING.search(rest)
        body = (rest[:nxt.start()] if nxt else rest).strip()
        if body:
            out.append({"見出し": want, "原文": body[:max_chars]})
    return out
