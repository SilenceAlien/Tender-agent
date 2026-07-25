"""Reference retriever — fetches relevant reference essays for RAG injection.

P1-2: SectionGenerator calls this to inject same-type reference essays
into the generation prompt, improving output quality by grounding the
LLM on real winning bids.

Two modes:
    1. Semantic (FAISS + embedder) — when an index is available, use
       vector similarity search for high-quality retrieval.
    2. Keyword (file-based) — fallback when no index/embedder/API key is
       configured.  Scans the knowledge_base directory for .txt files
       matching the bid_type + section_key, returning the most relevant
       snippets by keyword overlap.

The keyword fallback ensures RAG works out-of-the-box without requiring
a pre-built FAISS index or embedding API access.
"""

import logging
import re
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


def _safe_path_component(name: str) -> str:
    """Sanitize a single KB path component to prevent directory traversal.

    KB directory names are like '劳务外包类', '_shared', '食堂餐饮'. Keep only
    word chars, CJK ideographs and hyphens; strip path separators and '..'
    segments. Returns '' for empty/unsafe input so the caller's .exists()
    guard skips it instead of reading outside kb_root.
    """
    if not name:
        return ""
    cleaned = name.replace("\\", "/").split("/")[-1].replace("..", "")
    cleaned = re.sub(r"[^\w\u4e00-\u9fff\-]", "", cleaned)
    return cleaned

# Default knowledge base root (relative to project root)
_DEFAULT_KB_ROOT = Path(__file__).parent.parent.parent.parent / "knowledge_base"

# Max characters of reference text to inject per section (keeps prompt manageable)
MAX_REF_CHARS_PER_SECTION = 1500


class ReferenceRetriever:
    """Retrieve reference essay snippets for RAG-augmented generation.

    Usage:
        retriever = ReferenceRetriever()
        snippet = retriever.retrieve_for_section("sec7_service_plan", "劳务管理服务类")
        if snippet:
            prompt += f"\\n\\n【参考范文片段】\\n{snippet}"
    """

    def __init__(
        self,
        kb_root: str | Path | None = None,
        embedder=None,
        faiss_index=None,
    ):
        """Initialize the retriever.

        Args:
            kb_root: Path to the knowledge base root directory.
            embedder: Optional embedding provider for semantic search.
            faiss_index: Optional FAISS index for semantic search.
        """
        self.kb_root = Path(kb_root) if kb_root else _DEFAULT_KB_ROOT
        # embedder 可为 OpenAIEmbedder（生产语义检索）或 MockEmbedder。
        # MockEmbedder 仅用于离线/测试专用：缺 OPENAI_API_KEY 时仍能运行，
        # 不会强制要求 key，仅语义路径退化为随机向量，关键词回退路径不受影响。
        self.embedder = embedder
        self.faiss_index = faiss_index
        self._cache: dict[str, str] = {}  # query → result cache

    def _find_reference_files(
        self, bid_type: str, section_key: str, bid_subtype: str = ""
    ) -> list[Path]:
        """Find reference .txt files matching the bid type and section key.

        95+优化 补强二: 当 bid_subtype 存在时, 优先搜索子类型分区的 chunks 目录,
        其次搜索 _shared/ 分区, 最后回退到现有的 范文/ 目录.

        搜索优先级:
            1. knowledge_base/{kb_type}/{bid_subtype}/chunks/  (子类型分区)
            2. knowledge_base/{kb_type}/_shared/chapters/      (通用共享)
            3. knowledge_base/{kb_type}/范文/                   (兼容现有)
        """
        if not self.kb_root.exists():
            return []

        # 归一化 bid_type → KB 目录名: 劳务管理服务类/劳务外包类 都映射到 劳务外包类
        kb_type = "劳务外包类" if ("劳务" in bid_type) else bid_type
        kb_type = _safe_path_component(kb_type)

        candidates: list[Path] = []

        # 95+优化: 优先搜索子类型分区 chunks
        if bid_subtype:
            subtype_dir = self.kb_root / kb_type / _safe_path_component(bid_subtype) / "chunks"
            if subtype_dir.exists():
                for f in subtype_dir.glob("*.txt"):
                    if section_key in f.name:
                        candidates.append(f)

        # 95+优化: 搜索 _shared 分区 (仅格式固定型章节)
        if not candidates and bid_subtype:
            try:
                from core.retrieval.subtype_router import is_chapter_allowed_in_shared
                if is_chapter_allowed_in_shared(section_key):
                    shared_dir = self.kb_root / kb_type / "_shared" / "chapters"
                    if shared_dir.exists():
                        for f in shared_dir.glob("*.txt"):
                            if section_key in f.name:
                                candidates.append(f)
            except Exception:
                pass

        # 95+优化 补强四: 冷启动/样本不足时, 用最相似子类型的 chunks 补充检索
        if bid_subtype:
            try:
                from core.retrieval.subtype_router import (
                    get_retrieval_weights,
                    find_nearest_subtypes,
                )
                # 消费权重表: 记录三档动态权重, 供后续融合/调试
                weights = get_retrieval_weights(bid_subtype, kb_root=self.kb_root)
                logger.debug(
                    f"SubtypeRouter weights for '{bid_subtype}': {weights}"
                )
                # 冷启动(neighbor>0) 或 当前子类型 + _shared 均无匹配 → 补充最近邻子类型
                if weights.get("neighbor", 0.0) > 0.0 or not candidates:
                    for neighbor, sim in find_nearest_subtypes(bid_subtype):
                        neighbor_dir = self.kb_root / kb_type / _safe_path_component(neighbor) / "chunks"
                        if not neighbor_dir.exists():
                            continue
                        for f in neighbor_dir.glob("*.txt"):
                            if section_key in f.name:
                                candidates.append(f)
            except Exception as e:
                logger.warning(f"Nearest-subtype fallback failed: {e}")

        # Try exact bid_type directory first (existing behavior)
        if not candidates:
            type_dir = self.kb_root / kb_type / "范文"
            if type_dir.exists():
                # Files named like sec7_service_plan_七、服务方案.txt
                for f in type_dir.glob("*.txt"):
                    if section_key in f.name:
                        candidates.append(f)

        # Fallback: search all subdirectories if exact match found nothing
        if not candidates:
            for f in self.kb_root.rglob("*.txt"):
                if section_key in f.name and "范文" in str(f):
                    candidates.append(f)

        return candidates[:3]  # cap at 3 files

    def _load_doc_by_id(self, doc_id: str, query_hint: str = "") -> str:
        """RAG doc_store wire: 基于 doc_id 从 knowledge_base 装载 doc 片段。

        与 keyword 的 extract_best_snippet 一致，确保 snippet 格式相容。
        query_hint 用于按真实查询抽取最相关片段；为空时回退默认 query。
        """
        if not self.kb_root.exists():
            return ""

        # 优先使用真实 query（语义检索调用处传入），否则回退默认
        snippet_query = query_hint or "招标要求"

        # 尝试直接作为相对路径装载
        candidate = self.kb_root / doc_id
        if candidate.exists() and candidate.is_file():
            return self._extract_best_snippet(
                candidate,
                query=snippet_query,
                max_chars=MAX_REF_CHARS_PER_SECTION
            )

        # 回退：逐级 search rglob（兼容旧的仅文件名的 doc_id 格式）
        matches = list(self.kb_root.rglob(doc_id))
        if matches:
            f = matches[0]
            return self._extract_best_snippet(
                f,
                query=snippet_query,
                max_chars=MAX_REF_CHARS_PER_SECTION
            )
        
        return ""

    def _keyword_score(self, query: str, text: str) -> float:
        """Score a text snippet by keyword overlap with the query.

        Simple but effective for Chinese text: count how many query
        tokens (2+ char segments) appear in the text, normalized by
        text length to avoid bias toward long documents.
        """
        if not query or not text:
            return 0.0
        # Tokenize: extract 2-4 char Chinese segments + ASCII words
        tokens = re.findall(r"[\u4e00-\u9fff]{2,4}|[a-zA-Z]{3,}", query)
        if not tokens:
            return 0.0
        hits = sum(1 for t in tokens if t in text)
        return hits / len(tokens)

    def _extract_best_snippet(
        self, file_path: Path, query: str, max_chars: int = MAX_REF_CHARS_PER_SECTION
    ) -> str:
        """Read a reference file and extract the most relevant snippet.

        Strategy:
        1. Read the full file
        2. Split into paragraphs
        3. Score each paragraph by keyword overlap with the query
        4. Concatenate top paragraphs up to max_chars
        """
        try:
            text = file_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Cannot read reference file {file_path}: {e}")
            return ""

        if not text.strip():
            return ""

        # Split into paragraphs (double newline or single newline for Chinese text)
        paragraphs = re.split(r"\n{1,}", text)
        paragraphs = [p.strip() for p in paragraphs if p.strip() and len(p.strip()) > 20]

        if not paragraphs:
            return text[:max_chars]

        # Score and rank paragraphs
        scored = [(self._keyword_score(query, p), i, p) for i, p in enumerate(paragraphs)]
        scored.sort(key=lambda x: (-x[0], x[1]))  # highest score first, preserve order on ties

        # Concatenate top paragraphs up to max_chars
        result_parts: list[str] = []
        total = 0
        for score, _, para in scored:
            if score == 0 and result_parts:
                break  # no more relevant paragraphs
            if total + len(para) > max_chars:
                # Truncate the last paragraph to fit
                remaining = max_chars - total
                if remaining > 50:
                    result_parts.append(para[:remaining] + "...")
                break
            result_parts.append(para)
            total += len(para)

        return "\n\n".join(result_parts) if result_parts else text[:max_chars]

    def retrieve_for_section(
        self, section_key: str, bid_type: str = "", query_hint: str = "",
        bid_subtype: str = "",
    ) -> str:
        """Retrieve a reference snippet for a specific section.

        Args:
            section_key: e.g. "sec7_service_plan" or "ch3_service"
            bid_type: e.g. "劳务管理服务类" — narrows the search directory
            query_hint: Additional keywords to guide snippet selection
            bid_subtype: 95+优化 补强二 — e.g. "HRO" narrows to subtype partition

        Returns:
            Reference text snippet (may be empty if no references found).
        """
        cache_key = f"{section_key}|{bid_type}|{bid_subtype}|{query_hint}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Try semantic search first (if configured)
        if self.embedder and self.faiss_index:
            try:
                query = f"{section_key} {query_hint}".strip()
                query_vec = self.embedder.embed_query(query).reshape(1, -1)
                results = self.faiss_index.search(query_vec, k=1)
                if results and results[0]:
                    doc_id = results[0][0][0]
                    # Look up the document text from a local doc store (file-based)
                    # Pass the real semantic query so snippet extraction is relevant
                    snippet = self._load_doc_by_id(doc_id, query_hint=query)
                    if snippet:
                        logger.debug(f"Semantic retrieval returned doc with {len(snippet)} chars")
                        self._cache[cache_key] = snippet
                        return snippet
                    else:
                        logger.warning(f"Semantic doc_id {doc_id} not found in doc store")
            except Exception as e:
                logger.warning(f"Semantic retrieval failed: {e} — falling back to keyword")

        # Keyword-based retrieval (fallback or primary)
        files = self._find_reference_files(bid_type, section_key, bid_subtype=bid_subtype)
        if not files:
            self._cache[cache_key] = ""
            return ""

        query = f"{section_key} {query_hint}".strip() or section_key
        snippets: list[str] = []
        for f in files:
            snippet = self._extract_best_snippet(f, query)
            if snippet:
                source_name = f.stem  # filename without extension
                snippets.append(f"【参考：{source_name}】\n{snippet}")

        result = "\n\n---\n\n".join(snippets) if snippets else ""
        self._cache[cache_key] = result
        return result


# ── Module-level singleton for convenient access ──────────────────────

_default_retriever: Optional[ReferenceRetriever] = None


def get_reference_retriever() -> ReferenceRetriever:
    """Get or create the default ReferenceRetriever singleton."""
    global _default_retriever
    if _default_retriever is None:
        _default_retriever = ReferenceRetriever()
    return _default_retriever


def set_reference_retriever(retriever: ReferenceRetriever | None) -> None:
    """Override the default retriever (useful for testing or custom config)."""
    global _default_retriever
    _default_retriever = retriever
