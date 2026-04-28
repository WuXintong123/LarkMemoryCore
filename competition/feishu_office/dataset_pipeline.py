"""Dataset construction helpers for the Feishu Office Assistant track."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPETITION_ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST_PATH = COMPETITION_ROOT / "source_manifest.json"
DEFAULT_OUTPUT_DIR = COMPETITION_ROOT / "data"
DEFAULT_CACHE_DIR = COMPETITION_ROOT / "cache"
USER_AGENT = "LarkMemoryCore-FeishuOfficeDatasetBuilder/1.0 (+https://github.com/WuXintong123/LarkMemoryCore)"
TASK_ORDER = (
    "knowledge_qa",
    "information_summary",
    "meeting_minutes",
    "weekly_report",
    "standardized_response",
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？；!?;])")
WHITESPACE_RE = re.compile(r"[ \t\u3000]+")
MULTI_BLANK_RE = re.compile(r"\n{3,}")
HTTP_SESSION = requests.Session()
HTTP_SESSION.mount(
    "https://",
    HTTPAdapter(
        max_retries=Retry(
            total=4,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
    ),
)
HTTP_SESSION.mount(
    "http://",
    HTTPAdapter(
        max_retries=Retry(
            total=4,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
    ),
)


@dataclass
class DocumentRecord:
    source_id: str
    category: str
    title: str
    source_url: str
    license: str
    text: str


@dataclass
class DatasetSample:
    id: str
    task: str
    source_title: str
    source_url: str
    license: str
    instruction: str
    input: str
    output: str
    grounding: List[str]
    split: str


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for raw_line in text.splitlines():
        line = WHITESPACE_RE.sub(" ", raw_line).strip()
        if line:
            lines.append(line)
    return MULTI_BLANK_RE.sub("\n\n", "\n".join(lines)).strip()


def _source_hash(*parts: str) -> str:
    digest = hashlib.sha256("::".join(parts).encode("utf-8")).hexdigest()
    return digest[:12]


def _load_repo_text(path: str) -> str:
    resolved = (REPO_ROOT / path).resolve()
    return resolved.read_text(encoding="utf-8")


def _fetch_text(url: str) -> str:
    cache_path = DEFAULT_CACHE_DIR / "raw_html" / f"{_source_hash(url)}.html"
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8")

    try:
        response = HTTP_SESSION.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        if response.apparent_encoding:
            response.encoding = response.apparent_encoding
        text = response.text
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(text, encoding="utf-8")
        return text
    except Exception:
        if cache_path.exists():
            return cache_path.read_text(encoding="utf-8")
        raise


def _extract_sentences(text: str, *, min_len: int = 12) -> List[str]:
    text = _normalize_text(text)
    if not text:
        return []
    sentences = []
    seen = set()
    for piece in SENTENCE_SPLIT_RE.split(text):
        sentence = piece.strip()
        if len(sentence) < min_len:
            continue
        if sentence not in seen:
            seen.add(sentence)
            sentences.append(sentence)
    return sentences


def _pick_action_sentences(sentences: Sequence[str], fallback_count: int = 2) -> List[str]:
    action_keywords = ("应", "需", "请", "将", "推进", "落实", "开展", "做好", "完善", "优化")
    actions = [sentence for sentence in sentences if any(keyword in sentence for keyword in action_keywords)]
    if len(actions) >= fallback_count:
        return list(actions[:fallback_count])
    combined = list(actions)
    for sentence in sentences:
        if sentence not in combined:
            combined.append(sentence)
        if len(combined) >= fallback_count:
            break
    return combined


def _make_chunks(text: str, *, max_chars: int = 450) -> List[str]:
    normalized = _normalize_text(text)
    paragraphs = [part.strip() for part in normalized.split("\n\n") if part.strip()]
    if len(paragraphs) <= 1:
        paragraphs = _extract_sentences(normalized, min_len=1)
    chunks: List[str] = []
    current: List[str] = []
    current_length = 0
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            for start in range(0, len(paragraph), max_chars):
                segment = paragraph[start : start + max_chars].strip()
                if segment:
                    if current:
                        chunks.append("\n\n".join(current))
                        current = []
                        current_length = 0
                    chunks.append(segment)
            continue
        paragraph_length = len(paragraph)
        if current and current_length + paragraph_length + 2 > max_chars:
            chunks.append("\n\n".join(current))
            current = [paragraph]
            current_length = paragraph_length
            continue
        current.append(paragraph)
        current_length += paragraph_length + (2 if current_length else 0)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _split_for_source_ids(source_ids: Sequence[str]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for index, source_id in enumerate(sorted(source_ids)):
        if index % 6 == 0:
            mapping[source_id] = "test"
        elif index % 5 == 0:
            mapping[source_id] = "validation"
        else:
            mapping[source_id] = "train"
    return mapping


def _extract_feishu_richtext(richtext_payload: Dict[str, Any]) -> str:
    raw_content = richtext_payload.get("content")
    if not raw_content:
        return ""
    try:
        blocks = json.loads(raw_content)
    except json.JSONDecodeError:
        return ""

    parts: List[str] = []
    for key in sorted(blocks.keys(), key=lambda value: int(value) if str(value).isdigit() else 0):
        block = blocks[key]
        ops = block.get("ops", [])
        for op in ops:
            insert = op.get("insert")
            attributes = op.get("attributes", {})
            if attributes.get("image") == "true":
                continue
            if isinstance(insert, str):
                cleaned = insert.replace("*", "").strip()
                if cleaned:
                    parts.append(cleaned)
    return _normalize_text("\n".join(parts))


def _parse_feishu_page(url: str, html: str) -> Dict[str, Any]:
    match = re.search(r"window\._templateValue\s*=\s*(\{.*?\});", html)
    if not match:
        raise RuntimeError(f"Failed to locate Feishu template payload for {url}")
    payload = json.loads(match.group(1))
    article_title = payload.get("articleTitle") or payload.get("title") or url
    richtext = payload.get("richtext") or {}
    text = _extract_feishu_richtext(richtext)
    if payload.get("description"):
        text = _normalize_text(payload["description"] + "\n\n" + text)

    recommendations = []
    for candidate in payload.get("recommendList", []):
        path = candidate.get("path")
        title = candidate.get("title")
        if path and title:
            recommendations.append({"url": urljoin("https://www.feishu.cn", path), "title": title})

    return {
        "title": article_title,
        "text": text,
        "recommendations": recommendations,
    }


def _parse_generic_html(url: str, html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)
    paragraphs = []
    for tag in soup.find_all(["h1", "h2", "h3", "p", "li"]):
        text = tag.get_text(" ", strip=True)
        if len(text) >= 20:
            paragraphs.append(text)
    return {
        "title": title or url,
        "text": _normalize_text("\n\n".join(paragraphs)),
        "recommendations": [],
    }


def _fetch_document_from_url(url: str) -> Dict[str, Any]:
    html = _fetch_text(url)
    if "window._templateValue" in html and "feishu.cn/content/" in url:
        return _parse_feishu_page(url, html)
    return _parse_generic_html(url, html)


def _expand_feishu_cluster(cluster_manifest: Dict[str, Any]) -> List[DocumentRecord]:
    documents: List[DocumentRecord] = []
    seen_urls = set()
    queue: deque[str] = deque(cluster_manifest["seed_urls"])
    max_pages = int(cluster_manifest.get("max_pages", 12))
    while queue and len(documents) < max_pages:
        current_url = queue.popleft()
        if current_url in seen_urls:
            continue
        seen_urls.add(current_url)
        try:
            page = _fetch_document_from_url(current_url)
        except Exception:
            continue
        documents.append(
            DocumentRecord(
                source_id=f"feishu_{_source_hash(current_url, page['title'])}",
                category=cluster_manifest["category"],
                title=page["title"],
                source_url=current_url,
                license=cluster_manifest["license"],
                text=page["text"],
            )
        )
        for recommendation in page["recommendations"]:
            recommended_url = recommendation["url"]
            if recommended_url not in seen_urls:
                queue.append(recommended_url)
    return documents


def _expand_html_listing(entry: Dict[str, Any]) -> List[DocumentRecord]:
    html = _fetch_text(entry["url"])
    soup = BeautifulSoup(html, "html.parser")
    base_url = entry["url"]
    links: List[str] = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(base_url, anchor["href"])
        text = _normalize_text(anchor.get_text(" ", strip=True))
        if not text:
            continue
        if entry.get("link_pattern") and entry["link_pattern"] not in href:
            continue
        if href.endswith(".html") and href not in links:
            links.append(href)
    documents: List[DocumentRecord] = []
    for href in links[: int(entry.get("max_links", 10))]:
        try:
            parsed = _fetch_document_from_url(href)
        except Exception:
            continue
        documents.append(
            DocumentRecord(
                source_id=f"{entry['id']}_{_source_hash(href, parsed['title'])}",
                category=entry["category"],
                title=parsed["title"],
                source_url=href,
                license=entry["license"],
                text=parsed["text"],
            )
        )
    return documents


def load_source_documents(manifest_path: Path = DEFAULT_MANIFEST_PATH) -> List[DocumentRecord]:
    manifest = _read_json(manifest_path)
    documents: List[DocumentRecord] = []
    for repo_source in manifest.get("repo_sources", []):
        documents.append(
            DocumentRecord(
                source_id=repo_source["id"],
                category=repo_source["category"],
                title=repo_source["title"],
                source_url=f"repo://{repo_source['path']}",
                license=repo_source["license"],
                text=_normalize_text(_load_repo_text(repo_source["path"])),
            )
        )

    documents.extend(_expand_feishu_cluster(manifest["feishu_cluster"]))

    for listing in manifest.get("public_office_listings", []):
        documents.extend(_expand_html_listing(listing))

    return documents


def _task_prompt(task: str, title: str, chunk: str) -> Dict[str, str]:
    sentences = _extract_sentences(chunk)
    key_sentences = list(sentences[:4]) or [chunk[:180]]
    action_sentences = _pick_action_sentences(sentences, fallback_count=2)
    background = key_sentences[0]
    supporting = key_sentences[1:4] if len(key_sentences) > 1 else key_sentences

    if task == "knowledge_qa":
        return {
            "instruction": "请严格依据给定材料，输出结论与依据，不要添加材料之外的信息。",
            "input": f"主题：{title}\n\n材料：\n{chunk}",
            "output": _normalize_text(
                "\n".join(
                    [
                        f"结论：{background}",
                        "依据：",
                        *(f"{index}. {sentence}" for index, sentence in enumerate(supporting[:3], start=1)),
                    ]
                )
            ),
        }

    if task == "information_summary":
        bullets = supporting[:3] if supporting else [background]
        return {
            "instruction": "请将材料浓缩为便于飞书办公场景转发的摘要，保留关键事实。",
            "input": f"摘要主题：{title}\n\n原始材料：\n{chunk}",
            "output": _normalize_text(
                "\n".join(["摘要：", *(f"- {bullet}" for bullet in bullets)])
            ),
        }

    if task == "meeting_minutes":
        todo_items = action_sentences or supporting[:2]
        return {
            "instruction": "请将材料整理成正式会议纪要格式，必须包含会议主题、背景、讨论要点、待办事项。",
            "input": f"纪要主题：{title}\n\n会议材料：\n{chunk}",
            "output": _normalize_text(
                "\n".join(
                    [
                        f"会议主题：{title}",
                        f"背景：{background}",
                        "讨论要点：",
                        *(f"{index}. {sentence}" for index, sentence in enumerate(supporting[:3], start=1)),
                        "待办事项：",
                        *(f"{index}. {sentence}" for index, sentence in enumerate(todo_items[:2], start=1)),
                    ]
                )
            ),
        }

    if task == "weekly_report":
        risks = action_sentences[1:2] or supporting[2:3] or supporting[:1]
        next_steps = action_sentences[:2] or supporting[:2]
        return {
            "instruction": "请整理成周报格式，必须包含本周进展、风险与关注、下周计划三个小节。",
            "input": f"周报主题：{title}\n\n项目材料：\n{chunk}",
            "output": _normalize_text(
                "\n".join(
                    [
                        "本周进展：",
                        *(f"{index}. {sentence}" for index, sentence in enumerate(supporting[:2], start=1)),
                        "风险与关注：",
                        *(f"{index}. {sentence}" for index, sentence in enumerate(risks[:2], start=1)),
                        "下周计划：",
                        *(f"{index}. {sentence}" for index, sentence in enumerate(next_steps[:2], start=1)),
                    ]
                )
            ),
        }

    return {
        "instruction": "请输出适合企业内部场景直接发送的标准化答复，语气正式、信息准确。",
        "input": f"答复主题：{title}\n\n参考材料：\n{chunk}",
        "output": _normalize_text(
            "\n".join(
                [
                    "标准回复：",
                    f"您好，关于“{title}”，请您重点关注以下事项：",
                    *(f"{index}. {sentence}" for index, sentence in enumerate(supporting[:3], start=1)),
                    "如需进一步处理，请以原文要求和正式发布内容为准。",
                ]
            )
        ),
    }


def build_dataset_rows(documents: Sequence[DocumentRecord]) -> List[DatasetSample]:
    split_map = _split_for_source_ids([document.source_id for document in documents])
    rows: List[DatasetSample] = []
    for document in documents:
        chunks = _make_chunks(document.text)
        for chunk_index, chunk in enumerate(chunks, start=1):
            grounding_sentences = _extract_sentences(chunk)[:3] or [chunk[:180]]
            for task in TASK_ORDER:
                prompt = _task_prompt(task, document.title, chunk)
                rows.append(
                    DatasetSample(
                        id=f"{document.source_id}-{chunk_index:03d}-{task}",
                        task=task,
                        source_title=document.title,
                        source_url=document.source_url,
                        license=document.license,
                        instruction=prompt["instruction"],
                        input=prompt["input"],
                        output=prompt["output"],
                        grounding=grounding_sentences,
                        split=split_map[document.source_id],
                    )
                )
    return rows


def _dataset_stats(rows: Sequence[DatasetSample]) -> Dict[str, Any]:
    rows_by_split: Dict[str, List[DatasetSample]] = defaultdict(list)
    source_by_split: Dict[str, set[str]] = defaultdict(set)
    task_counter = Counter()
    category_counter = Counter()

    for row in rows:
        rows_by_split[row.split].append(row)
        source_by_split[row.split].add(row.source_url)
        task_counter[row.task] += 1
        category_counter[urlparse(row.source_url).scheme or "repo"] += 1

    return {
        "total_rows": len(rows),
        "rows_by_split": {split: len(items) for split, items in rows_by_split.items()},
        "unique_sources_by_split": {split: len(items) for split, items in source_by_split.items()},
        "rows_by_task": dict(task_counter),
        "rows_by_source_scheme": dict(category_counter),
        "avg_input_chars": round(sum(len(row.input) for row in rows) / max(1, len(rows)), 2),
        "avg_output_chars": round(sum(len(row.output) for row in rows) / max(1, len(rows)), 2),
    }


def materialize_dataset(
    *,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> Dict[str, Any]:
    documents = load_source_documents(manifest_path)
    rows = build_dataset_rows(documents)

    output_dir.mkdir(parents=True, exist_ok=True)
    all_rows = [asdict(row) for row in rows]
    train_rows = [row for row in all_rows if row["split"] == "train"]
    validation_rows = [row for row in all_rows if row["split"] == "validation"]
    test_rows = [row for row in all_rows if row["split"] == "test"]

    _write_jsonl(output_dir / "corpus.jsonl", (asdict(document) for document in documents))
    _write_jsonl(output_dir / "all.jsonl", all_rows)
    _write_jsonl(output_dir / "train.jsonl", train_rows)
    _write_jsonl(output_dir / "validation.jsonl", validation_rows)
    _write_jsonl(output_dir / "test.jsonl", test_rows)
    _write_json(
        output_dir / "dataset_manifest.json",
        {
            "manifest_path": str(manifest_path.relative_to(REPO_ROOT)),
            "task_order": list(TASK_ORDER),
            "document_count": len(documents),
            "row_count": len(rows),
            "train_row_count": len(train_rows),
            "validation_row_count": len(validation_rows),
            "test_row_count": len(test_rows),
        },
    )
    _write_json(output_dir / "quality_report.json", _dataset_stats(rows))
    return {
        "documents": documents,
        "rows": rows,
    }


def validate_materialized_dataset(output_dir: Path = DEFAULT_OUTPUT_DIR) -> Dict[str, Any]:
    required_files = [
        output_dir / "all.jsonl",
        output_dir / "train.jsonl",
        output_dir / "validation.jsonl",
        output_dir / "test.jsonl",
        output_dir / "dataset_manifest.json",
        output_dir / "quality_report.json",
    ]
    for path in required_files:
        if not path.exists():
            raise FileNotFoundError(f"Required dataset artifact is missing: {path}")

    rows: List[Dict[str, Any]] = []
    for line in (output_dir / "all.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))

    required_keys = {
        "id",
        "task",
        "source_title",
        "source_url",
        "license",
        "instruction",
        "input",
        "output",
        "grounding",
        "split",
    }
    source_sets: Dict[str, set[str]] = defaultdict(set)
    rows_by_split = Counter()
    rows_by_task = Counter()
    for row in rows:
        missing = required_keys.difference(row.keys())
        if missing:
            raise ValueError(f"Dataset row {row.get('id')} is missing fields: {sorted(missing)}")
        if row["task"] not in TASK_ORDER:
            raise ValueError(f"Dataset row {row['id']} has unsupported task {row['task']}")
        if row["split"] not in {"train", "validation", "test"}:
            raise ValueError(f"Dataset row {row['id']} has unsupported split {row['split']}")
        if not isinstance(row["grounding"], list) or not row["grounding"]:
            raise ValueError(f"Dataset row {row['id']} must contain non-empty grounding")
        if not row["instruction"].strip() or not row["input"].strip() or not row["output"].strip():
            raise ValueError(f"Dataset row {row['id']} contains empty instruction/input/output")
        source_sets[row["split"]].add(row["source_url"])
        rows_by_split[row["split"]] += 1
        rows_by_task[row["task"]] += 1

    if rows_by_split["train"] < 1000:
        raise ValueError(f"Expected at least 1000 train rows, found {rows_by_split['train']}")
    if rows_by_split["validation"] + rows_by_split["test"] < 200:
        raise ValueError(
            "Expected at least 200 held-out rows across validation and test, "
            f"found {rows_by_split['validation'] + rows_by_split['test']}"
        )

    overlap = (
        source_sets["train"] & source_sets["validation"]
        | source_sets["train"] & source_sets["test"]
        | source_sets["validation"] & source_sets["test"]
    )
    if overlap:
        raise ValueError(f"Source overlap detected across splits: {sorted(overlap)[:5]}")

    return {
        "row_count": len(rows),
        "rows_by_split": dict(rows_by_split),
        "rows_by_task": dict(rows_by_task),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build or validate the Feishu Office dataset.")
    parser.add_argument("--mode", choices=("build", "validate"), default="build")
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    if args.mode == "build":
        result = materialize_dataset(manifest_path=args.manifest_path, output_dir=args.output_dir)
        print(
            json.dumps(
                {
                    "document_count": len(result["documents"]),
                    "row_count": len(result["rows"]),
                    "output_dir": str(args.output_dir),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    print(json.dumps(validate_materialized_dataset(args.output_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
