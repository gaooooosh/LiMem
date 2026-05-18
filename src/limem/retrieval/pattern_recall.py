"""Entity Pattern v2 召回工具：H2 章节切片 + 朴素打分。

设计要点（详见 /home/gaooooosh/.claude/plans/pattern-polymorphic-moon.md）：

- 仅按 `^## ` 切片，H3/H4 不下钻；无 H2 整篇作为单 anonymous Section。
- 复用 retrieval/bm25.py:tokenize（jieba search 模式 + 英文 regex），不重复造轮子。
- 打分：heading 命中权重 2 + body 命中权重 1；单 token 单段命中次数 clip 5。
- 三模式 auto / full / section：query="" → full；超长且 query 非空 → section；否则 full。
- 纯函数，无 IO，便于单元测试。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Literal, Optional

from .bm25 import tokenize


_H2_LINE_RE = re.compile(r"^##\s+.+$", re.MULTILINE)
_SECTION_TOKEN_HIT_CLIP = 5
_SECTION_SEPARATOR = "\n\n---\n\n"


@dataclass
class Section:
    heading: str       # "## 偏好"；无 H2 整篇时为 ""
    body: str          # 章节正文（不含 heading 行）
    char_offset: int   # heading 行在原文中的起始字符偏移


def split_h2_sections(content: str) -> list[Section]:
    """按 H2 (^## ) 切片。H3/H4 不下钻；无 H2 整篇为单 anonymous Section。

    多个连续 H2 之间的内容归属上一个 H2；文档开头到第一个 H2 之间的"序章"作为
    一个 anonymous Section 在最前（heading=""，char_offset=0）。
    """
    text = content or ""
    matches = list(_H2_LINE_RE.finditer(text))
    if not matches:
        return [Section(heading="", body=text, char_offset=0)]

    sections: list[Section] = []
    first_start = matches[0].start()
    if first_start > 0:
        prelude = text[:first_start].rstrip("\n")
        if prelude:
            sections.append(Section(heading="", body=prelude, char_offset=0))

    for i, m in enumerate(matches):
        heading_line = m.group(0).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].lstrip("\n").rstrip("\n")
        sections.append(Section(heading=heading_line, body=body, char_offset=m.start()))
    return sections


def score_section(section: Section, query_tokens: list[str]) -> int:
    """heading 命中权重 2 + body 命中权重 1；单 token 单段命中次数 clip 5。"""
    if not query_tokens:
        return 0
    heading_tokens = tokenize(section.heading)
    body_tokens = tokenize(section.body)
    heading_counts = Counter(heading_tokens)
    body_counts = Counter(body_tokens)
    score = 0
    for token in query_tokens:
        h_hits = min(heading_counts.get(token, 0), _SECTION_TOKEN_HIT_CLIP)
        b_hits = min(body_counts.get(token, 0), _SECTION_TOKEN_HIT_CLIP)
        score += 2 * h_hits + 1 * b_hits
    return score


def _section_text(section: Section) -> str:
    """重组单个章节的输出文本：heading + 空行 + body（空 heading 退化为 body）。"""
    if section.heading and section.body:
        return f"{section.heading}\n{section.body}"
    return section.heading or section.body


def recall_pattern(
    content: str,
    query: str = "",
    mode: Literal["auto", "full", "section"] = "auto",
    top_k_sections: int = 3,
    full_return_max_chars: int = 2000,
) -> dict:
    """对单实体的 pattern markdown 执行召回。

    auto 规则：
      - query=="" → full
      - len(content) >= full_return_max_chars 且 query 非空 → section
      - 否则 → full

    section 模式：
      - tokenize(query) → query_tokens
      - 对每个 Section 打分，过滤 score==0
      - 按 score 降序取 top_k_sections，再按 char_offset 升序拼接
      - 分隔符 "\\n\\n---\\n\\n"

    Returns:
        {
          "mode": "full"|"section",
          "content": str,                 # 拼接后的输出
          "total_chars": int,             # 原文字符数
          "matched_sections": [           # full 模式为 []
              {"heading": str, "score": int, "char_offset": int}
          ]
        }
    """
    text = content or ""
    total_chars = len(text)
    q = (query or "").strip()

    effective_mode: Literal["full", "section"]
    if mode == "full":
        effective_mode = "full"
    elif mode == "section":
        effective_mode = "section"
    else:  # auto
        if not q:
            effective_mode = "full"
        elif total_chars >= int(full_return_max_chars):
            effective_mode = "section"
        else:
            effective_mode = "full"

    if effective_mode == "full":
        return {
            "mode": "full",
            "content": text,
            "total_chars": total_chars,
            "matched_sections": [],
        }

    # section 模式
    sections = split_h2_sections(text)
    query_tokens = tokenize(q) if q else []
    scored: list[tuple[int, Section]] = []
    for sec in sections:
        s = score_section(sec, query_tokens)
        if s > 0:
            scored.append((s, sec))

    if not scored:
        return {
            "mode": "section",
            "content": "",
            "total_chars": total_chars,
            "matched_sections": [],
        }

    k = max(1, int(top_k_sections))
    scored.sort(key=lambda item: item[0], reverse=True)
    picked = scored[:k]
    picked.sort(key=lambda item: item[1].char_offset)  # 输出按原文顺序

    body = _SECTION_SEPARATOR.join(_section_text(sec) for _, sec in picked)
    return {
        "mode": "section",
        "content": body,
        "total_chars": total_chars,
        "matched_sections": [
            {"heading": sec.heading, "score": s, "char_offset": sec.char_offset}
            for s, sec in picked
        ],
    }
