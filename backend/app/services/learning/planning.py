import json
from pathlib import Path

from pydantic import Field

from app.models import LearningCapability, new_id
from app.schemas import StrictRequest
from app.services.answering import llm_ready
from app.services.corpus import CorpusScope
from app.services.learning.model import invoke_json
from app.services.learning.sources import snapshot
from app.services.retrieval import retrieve


def templates():
    return json.loads(Path(__file__).with_name("templates.json").read_text())


class ProposedCapability(StrictRequest):
    ordinal: int = Field(ge=0, le=2)
    title: str = Field(min_length=1, max_length=200)
    observable_outcome: str = Field(min_length=1, max_length=2000)
    source_ids: list[str] = Field(max_length=8)
    coverage_status: str = Field(pattern="^(covered|partial|missing)$")


class ProposedPlan(StrictRequest):
    capabilities: list[ProposedCapability] = Field(min_length=3, max_length=3)


def prepare(db, user, workspace):
    scope = CorpusScope(db, user, [workspace.knowledge_base_id], workspace.selected_document_ids)
    fingerprint = scope.fingerprint()
    proposals, candidates = [], {}
    for ordinal, cap in enumerate(templates()["capabilities"]):
        hits = retrieve(cap["query"], scope, top_k=3)
        expanded = scope.expand_neighbors(hits)
        ids = []
        for hit in expanded:
            cid = hit.get("id") or hit.get("chunk_id")
            hit["id"] = cid
            candidates[cid] = hit
            ids.append(cid)
        proposals.append(
            {
                "ordinal": ordinal,
                "title": cap["title"],
                "observable_outcome": cap["observable_outcome"],
                "source_ids": ids,
                "coverage_status": "partial" if ids else "missing",
            }
        )
    return fingerprint, proposals, candidates


def generate(database, settings, user_id, goal, proposals, candidates):
    if not llm_ready():
        return proposals  # Explicit template draft; the user must check coverage before confirmation.
    result, _ = invoke_json(
        database,
        settings,
        user_id,
        "首版仅支持数据库事务专题。结合goal细化三个候选能力的描述，保持每个ordinal的原始能力范围。"
        "只使用候选source_ids；没有足够依据时必须标记partial或missing，不假定资料覆盖。",
        {
            "goal": goal,
            "candidates": proposals,
            "sources": [{"id": key, "text": c["text"][:1200]} for key, c in candidates.items()],
        },
        ProposedPlan,
    )
    proposed = result["capabilities"]
    if sorted(c["ordinal"] for c in proposed) != [0, 1, 2]:
        raise ValueError("能力序号不完整")
    for cap in proposed:
        allowed = proposals[cap["ordinal"]]["source_ids"]
        if not set(cap["source_ids"]) <= set(allowed) or (
            cap["coverage_status"] == "covered" and not cap["source_ids"]
        ):
            raise ValueError("能力引用越界或缺失")
    return sorted(proposed, key=lambda c: c["ordinal"])


def persist(db, workspace, proposals, candidates):
    mapping = {cid: snapshot(db, workspace, candidate).id for cid, candidate in candidates.items()}
    cap_ids = [new_id() for _ in range(3)]
    for proposal in proposals:
        i = proposal["ordinal"]
        db.add(
            LearningCapability(
                id=cap_ids[i],
                workspace_id=workspace.id,
                title=proposal["title"],
                observable_outcome=proposal["observable_outcome"],
                ordinal=i,
                source_ids=[mapping[c] for c in proposal["source_ids"]],
                coverage_status=proposal["coverage_status"],
                prerequisite_ids=[cap_ids[j] for j in templates()["capabilities"][i]["prerequisites"]],
            )
        )
