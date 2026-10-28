"""Bản in-memory của Repo / Retriever + LLM mock cho unit test (không gọi mạng, không DB)."""
from __future__ import annotations

import dataclasses
import json
from typing import Any, Callable

from app.models.document import ChildChunk, Document, ParentChunk
from app.providers.llm import MockChatModel
from app.providers.reranker import MockReranker
from app.repositories.postgres import Repo, _std_norm, section_matches
from app.repositories.qdrant import HIT_FIELDS, Retriever, discipline_filter
from app.services.query.deps import Deps
from app.utils.keys import child_seq, make_child_id, make_parent_id, normalize_alias
from app.utils.sparse import tokenize
from tests.fixtures.query_seed.seed import SeedData, load_seed

S5 = "26DA06"
S6 = "TEST_S6"
S7 = "TEST_S7"
PQ = "TEST_PQ"   # dự án giả có chung alias "Phú Quốc" với 26DA06

PROJECTS: dict[str, dict[str, Any]] = {
    S5: {"name": "Quảng Trường Biển 88ha – S5", "aliases": ["S5", "Bãi Đất Đỏ", "BDD", "Quảng Trường Biển 88ha", "Phú Quốc"],
         "source_priority": ["CONTRACT", "DESIGN_CONSULTANT", "OPERATOR_STANDARD", "LEGAL_STANDARD"],
         "governing_language": "vi"},
    S6: {"name": "Dự án thử S6", "aliases": ["S6", "Tòa S6"], "source_priority": ["DESIGN_CONSULTANT"],
         "governing_language": "vi"},
    S7: {"name": "Dự án thử S7", "aliases": ["S7", "Tòa S7"], "source_priority": ["DESIGN_CONSULTANT"],
         "governing_language": "vi"},
    PQ: {"name": "Dự án thử Phú Quốc Marina", "aliases": ["Phú Quốc Marina", "Phú Quốc"], "source_priority": [],
         "governing_language": "vi"},
}
USERS = {"test_engineer": [S5], "multi": [S5, S6, S7, PQ], "s6_only_plus": [S5, S6]}


def fake_project_doc(project_id: str, building: str, *, lang: str = "vi", stream: str = "DESIGN_CONSULTANT",
                     section_key: str = "sec_1", text: str = "Nội dung thử", doc_no: str = "1") -> SeedData:
    """1 tài liệu giả 1 parent / 1 child (VD dự án S6 chỉ có VN)."""
    doc_id = f"doc_fake{project_id.lower()}{doc_no}"[:16]
    doc = Document(doc_id=doc_id, doc_code=f"{project_id}_{building}_{stream}_HVAC_SPEC", project_id=project_id,
                   building=building, stream=stream, disciplines=["HVAC"], title=f"Tài liệu thử {building}",
                   revision="A", languages=[lang], source_path=f"fake/{doc_id}.pdf", file_hash=doc_id * 4)
    pid = make_parent_id(doc_id, lang, section_key)
    parent = ParentChunk(parent_id=pid, doc_id=doc_id, section_key=section_key, lang=lang, type="SECTION",
                         number="1", title="Mục thử", content_md=text, page_start=1, page_end=1, token_count=len(text) // 3)
    child = ChildChunk(child_id=make_child_id(pid, 1), parent_id=pid, section_key=section_key, lang=lang, type="TEXT",
                       text=f"[{project_id} {building} | {stream} | HVAC | 1 Mục thử | thử]\n{text}", raw_text=text,
                       page_start=1, page_end=1, project_id=project_id, building=building, stream=stream,
                       disciplines=["HVAC"], doc_id=doc_id, revision="A")
    return SeedData([doc], [parent], [child])


class InMemoryRepo(Repo):
    def __init__(self, data: SeedData, projects: dict[str, dict] | None = None, users: dict[str, list[str]] | None = None):
        self.data = data
        self.projects = projects or PROJECTS
        self.users = users or USERS
        self.conversations: dict[str, dict] = {}
        self.calls: list[str] = []

    def user_projects(self, user_id):
        return list(self.users.get(user_id, []))

    def aliases(self, project_ids):
        rows = []
        for pid in project_ids:
            for a in [pid, *self.projects.get(pid, {}).get("aliases", [])]:
                rows.append({"alias_norm": normalize_alias(a), "alias": a, "project_id": pid, "building": "",
                             "kind": "project"})
        return rows

    def project_info(self, project_ids):
        return {p: {**self.projects[p], "aliases": self.projects[p]["aliases"][:3]} for p in project_ids if p in self.projects}

    def documents(self, project_ids):
        self.calls.append("documents")
        return [d.model_dump() for d in self.data.documents if d.project_id in project_ids and d.is_latest]

    def parents_by_ids(self, parent_ids):
        self.calls.append("parents_by_ids")
        return [p.model_dump() for p in self.data.parents if p.parent_id in set(parent_ids)]

    def parents_by_sections(self, pairs):
        self.calls.append("parents_by_sections")
        s = set(pairs)
        return [p.model_dump() for p in self.data.parents if (p.doc_id, p.section_key) in s]

    def find_sections(self, doc_ids, refs):
        self.calls.append("find_sections")
        return [p.model_dump() for p in self.data.parents
                if p.doc_id in set(doc_ids) and any(section_matches(p.section_key, r) for r in refs)]

    def standards_in_system(self, codes):
        out = {}
        for c in codes:
            hit = [d.doc_id for d in self.data.documents
                   if _std_norm(c) in normalize_alias(f"{d.title or ''} {d.doc_code or ''}")]
            if hit:
                out[c] = hit
        return out

    def upsert_conversation(self, conversation_id, user_id, title):
        self.conversations.setdefault(conversation_id, {"conversation_id": conversation_id, "user_id": user_id,
                                                        "title": title})

    def list_conversations(self, user_id):
        return [c for c in self.conversations.values() if c["user_id"] == user_id]

    def get_conversation(self, conversation_id):
        return self.conversations.get(conversation_id)


class FakeRetriever(Retriever):
    """Lọc payload như Qdrant; điểm = tỷ lệ token query có trong child.text."""

    def __init__(self, data: SeedData, top_k: int = 10, fail_branches: set[tuple[str, str, str]] | None = None):
        self.data, self.top_k = data, top_k
        self.fail = fail_branches or set()
        self.calls: list[dict] = []

    def search(self, *, project_id, stream, lang, queries, keywords, disciplines, doc_ids=None):
        self.calls.append({"project_id": project_id, "stream": stream, "lang": lang, "queries": queries,
                           "doc_ids": doc_ids})
        if (project_id, stream, lang) in self.fail:
            raise RuntimeError("nhánh lỗi giả lập")
        disc = discipline_filter(disciplines)
        q = set(tokenize(" ".join([*queries, *keywords])))
        by_doc: dict[str, list[dict]] = {}
        for c in self.data.children:
            if (c.project_id, c.stream, c.lang) != (project_id, stream, lang) or not c.is_latest:
                continue
            if disc and not set(c.disciplines) & set(disc):
                continue
            if doc_ids and c.doc_id not in doc_ids:
                continue
            score = len(q & set(tokenize(c.text))) / (len(q) or 1)
            if score > 0:
                hit = {f: getattr(c, f) for f in HIT_FIELDS}
                by_doc.setdefault(c.doc_id, []).append({**hit, "fusion_score": score})
        out = []
        for hits in by_doc.values():
            out += sorted(hits, key=lambda h: -h["fusion_score"])[: self.top_k]
        return out

    def children_of(self, parent_id, lang):
        kids = [c for c in self.data.children if c.parent_id == parent_id]
        return sorted(({f: getattr(c, f) for f in HIT_FIELDS} for c in kids), key=lambda c: child_seq(c["child_id"]))


# ------------------------------------------------------------------ LLM mock

def router_json(**kw) -> str:
    base = {"rewritten_question": kw.pop("q", "câu hỏi"), "project_action": "keep", "project_mentions": [],
            "disciplines": [], "intent_type": "TECHNICAL_SPEC_LOOKUP", "streams": [], "queries_vi": [],
            "queries_en": [], "exact_keywords": []}
    base.update(kw)
    return json.dumps(base, ensure_ascii=False)


def scripted_llm(router: Callable[[str], str] | list[str] | str, grader: str = '{"verdict": "ENOUGH"}',
                 answer: str = "Trả lời theo tài liệu [1].") -> MockChatModel:
    """1 mock dùng chung cho fast + main: phân loại theo system prompt."""
    routers = [router] if isinstance(router, str) else router
    state = {"i": 0}

    def handler(messages):
        sys = str(messages[0].content)
        human = str(messages[-1].content)
        if sys.startswith("Bạn là bộ định tuyến"):
            if callable(routers):
                return routers(human)
            r = routers[min(state["i"], len(routers) - 1)]
            state["i"] += 1
            return r
        if sys.startswith("Bạn kiểm tra"):
            return grader
        return answer

    return MockChatModel(handler=handler)


def approx_tokens(text: str) -> int:
    return max(1, len(text) // 3) if text else 0


def make_deps(settings, data: SeedData | None = None, llm: MockChatModel | None = None, *,
              pipeline_overrides: dict | None = None, retriever: Retriever | None = None,
              repo: Repo | None = None) -> Deps:
    data = data or load_seed()
    s = settings
    if pipeline_overrides:
        pipe = {k: dict(v) if isinstance(v, dict) else v for k, v in s.pipeline.items()}
        for sec, vals in pipeline_overrides.items():
            pipe.setdefault(sec, {}).update(vals)
        s = dataclasses.replace(s, pipeline=pipe)
    llm = llm or scripted_llm(router_json())
    return Deps(settings=s, repo=repo or InMemoryRepo(data), retriever=retriever or FakeRetriever(data),
                reranker=MockReranker(), llm_fast=llm, llm_main=llm, count_tokens=approx_tokens)
