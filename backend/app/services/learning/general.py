"""Generate bounded, source-backed drafts and exercise batches for plan version 2."""

import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Literal

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import func, select

from app.models import Chunk, Document, KnowledgeBase, LearningCapability, LearningTask, new_id, utcnow
from app.schemas import StrictRequest
from app.services.access import allowed_kbs
from app.services.corpus import CorpusScope
from app.services.learning.model import ModelUnavailable, invoke_json
from app.services.learning.sources import check_sources, snapshot
from app.services.retrieval import retrieve

GENERAL_PLAN = 2
TASK_VERSION = "grounded-v2"
JOB_KIND = "practice"
MISSING_EVIDENCE = "在所选资料中暂未找到足够依据，请补充对应资料或缩小目标。"
Family = Literal["foundation", "application", "transfer"]


class Evidence(StrictRequest):
    source_id: str
    quote: str = Field(min_length=1, max_length=400)


class Coverage(StrictRequest):
    coverage_status: Literal["covered", "partial", "missing"]
    evidence: list[Evidence] = Field(max_length=4)
    gap: str = Field(max_length=400)


class Goal(Coverage):
    title: str = Field(min_length=1, max_length=200)
    observable_outcome: str = Field(min_length=1, max_length=1000)
    prerequisite_ordinals: list[int] = Field(max_length=4)


class Goals(StrictRequest):
    capabilities: list[Goal] = Field(min_length=1, max_length=5)


class Standard(StrictRequest):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,24}$")
    focus: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    description: str = Field(min_length=1, max_length=180)
    expected: str = Field(min_length=1, max_length=280)
    evidence: list[Evidence] = Field(min_length=1, max_length=3)


class Exercise(StrictRequest):
    prompt: str = Field(min_length=1, max_length=700)
    kind: Literal["explain", "calculate", "analyze", "apply", "followup"]
    rubric: list[Standard] = Field(min_length=1, max_length=5)
    evidence: list[Evidence] = Field(min_length=1, max_length=4)
    hints: list[str] = Field(min_length=3, max_length=3)
    solution: str = Field(min_length=1, max_length=750)


class Exercises(StrictRequest):
    tasks: list[Exercise] = Field(min_length=1, max_length=3)


class ExerciseVerdict(StrictRequest):
    index: int = Field(ge=0, le=2)
    supported: bool
    family: Family
    duplicate: bool
    reason: str = Field(min_length=1, max_length=400)


class CapabilityRejected(ValueError):
    def __init__(self, capability_id, reason):
        super().__init__(reason)
        self.capability_id = capability_id


def scoped_documents(db, user, workspace, *, require_same=False, lock=False):
    if not workspace.knowledge_base_id or not workspace.selected_document_ids:
        raise HTTPException(409, "所选资料已不可用，请重新选择资料")
    scope = CorpusScope(db, user, [workspace.knowledge_base_id], workspace.selected_document_ids)
    query = select(Document.id, Document.active_revision_id).where(
        Document.id.in_(scope.document_ids),
        Document.knowledge_base_id.in_(select(KnowledgeBase.id).where(allowed_kbs(user.id))),
        Document.knowledge_base_id == workspace.knowledge_base_id,
        Document.active_revision_id.is_not(None),
        Document.status != "deleting",
        (Document.expires_at.is_(None) | (Document.expires_at > utcnow())),
    ).order_by(Document.id)
    rows = db.execute(query.with_for_update() if lock else query).all()
    if len(rows) != len(scope.document_ids):
        raise HTTPException(409, "所选资料已删除、过期或不再可用，请重新选择资料")
    fingerprint = hashlib.sha256(json.dumps([tuple(row) for row in rows]).encode()).hexdigest()
    if require_same and fingerprint != workspace.source_fingerprint:
        raise HTTPException(409, "资料版本已变化，请重新选择资料生成草稿并核对")
    return scope, fingerprint


def draft_candidates(scope, goal):
    # Keep a bounded set across locations and files even when the exact wording misses retrieval.
    phrases = sorted(set(re.findall(r"[\w\u4e00-\u9fff]{4,}", goal)), key=lambda word: (-len(word), word))
    queries = [goal, *[phrase[:100] for phrase in phrases[:2] if phrase != goal]]
    ranked = {}
    for query in queries:
        for hit in retrieve(query, scope, top_k=7, rerank_results=False):
            ranked.setdefault(hit["id"], hit)

    samples = []
    for document_id in scope.document_ids:
        conditions = (*scope.conditions, Document.id == document_id)
        low, high = scope.db.execute(
            select(func.min(Chunk.ordinal), func.max(Chunk.ordinal)).join(Document).where(*conditions)
        ).one()
        if low is None:
            continue
        locations = [low, (low + high) // 2, high]
        query = select(Chunk.id).join(Document).where(*conditions).order_by(Chunk.ordinal, Chunk.id).limit(1)
        samples.append([scope.db.scalar(query.where(Chunk.ordinal >= position)) for position in dict.fromkeys(locations)])

    candidates = {}
    sample_ids = list(dict.fromkeys(cid for group in samples for cid in group if cid))
    sampled = scope.hydrate(sample_ids)
    order = [*ranked.keys()][:7] + [group[0] for group in samples] + sample_ids + list(ranked)
    locations = set()
    for cid in dict.fromkeys(order):
        hit = ranked.get(cid) or sampled.get(cid)
        if not hit or not hit["text"].strip():
            continue
        location = (hit["document_id"], hit.get("page"), hit["ordinal"])
        if location not in locations:
            locations.add(location)
            candidates[cid] = {**hit, "text": hit["text"][:850]}
        if len(candidates) == 18:
            break
    return candidates


def source_input(source):
    return {
        "id": source.id,
        "document_name": source.document_name,
        "page": source.page,
        "ordinal": source.ordinal,
        "revision_id": source.revision_id,
        "text": source.excerpt,
    }


def evidence_sources(evidence, sources, metadata):
    # Validate against the bytes the model actually received after context budgeting.
    exposed = {item["id"]: item["bytes"] for item in metadata.get("source_inputs", [])}
    texts = {
        item["id"]: item["text"].encode()[:exposed.get(item["id"])].decode("utf-8", errors="ignore")
        for item in sources
    }
    ids = []
    for item in evidence:
        source_id, quote = item["source_id"], item["quote"]
        if source_id not in texts or not quote.strip() or quote not in texts[source_id]:
            raise ValueError("依据原句与所选资料不一致")
        if source_id not in ids:
            ids.append(source_id)
    return ids


def validate_coverage(claim, sources, metadata):
    ids = evidence_sources(claim["evidence"], sources, metadata)
    status = claim["coverage_status"]
    if (status == "missing") != (not ids) or (status != "covered" and not claim["gap"].strip()):
        raise ValueError("覆盖状态与原文依据或缺口不一致")
    return ids


def verify_coverage(database, settings, user_id, outcome, sources):
    if not sources:
        return {"coverage_status": "missing", "evidence": [], "gap": MISSING_EVIDENCE}
    result, metadata = invoke_json(
        database, settings, user_id,
        "独立核验学习结果。仅按所给片段判断资料是否足以支持每个关键概念、步骤及条件。"
        "公式、图示细节、历史事实缺失时说明具体缺口，不补猜。covered需充分依据，partial需部分依据，"
        "missing表示当前无可引用内容。evidence只逐字摘录所给片段，gap说明实际缺口。",
        {"outcome": outcome, "sources": sources}, Coverage,
    )
    validate_coverage(result, sources, metadata)
    return result


def validate_prerequisites(edges):
    def walk(node, ancestors):
        if node not in edges or node in ancestors:
            raise ValueError("先修关系包含循环或不存在的能力")
        if len(edges[node]) != len(set(edges[node])):
            raise ValueError("先修关系包含重复能力")
        for parent in edges[node]:
            walk(parent, ancestors | {node})

    for node in edges:
        walk(node, set())


def generate_goals(database, settings, user_id, goal, candidates):
    if not candidates:
        return [{
            "title": "当前学习目标", "observable_outcome": goal, "prerequisite_ordinals": [],
            "coverage_status": "missing", "evidence": [], "gap": MISSING_EVIDENCE,
        }]
    sources = [
        {key: hit[key] for key in ("id", "document_name", "page", "ordinal", "revision_id", "text")}
        for hit in candidates.values()
    ]
    result, metadata = invoke_json(
        database, settings, user_id,
        "从用户目标与候选片段提出1到5项可观察学习结果。先修序号从0开始，只添加真实依赖。"
        "不要扩展到其他学科或目标。逐字摘录候选原文，标记充分、部分或无依据及具体缺口。"
        "若材料无关，给目标标记missing，不生成与目标无关的能力。",
        {"goal": goal, "sources": sources}, Goals,
    )
    proposals = result["capabilities"]
    validate_prerequisites({index: item["prerequisite_ordinals"] for index, item in enumerate(proposals)})
    for proposal in proposals:
        if not proposal["title"].strip() or not proposal["observable_outcome"].strip():
            raise ValueError("能力名称和学习结果不能为空")
        ids = validate_coverage(proposal, sources, metadata)
        if ids:
            checked = verify_coverage(
                database, settings, user_id, proposal["observable_outcome"],
                [source for source in sources if source["id"] in ids],
            )
            proposal.update(checked)
    return proposals


def persist_goals(db, workspace, proposals, candidates):
    mapping = {cid: snapshot(db, workspace, candidate).id for cid, candidate in candidates.items()}
    cap_ids = [new_id() for _ in proposals]
    for index, proposal in enumerate(proposals):
        evidence = [{**item, "source_id": mapping[item["source_id"]]} for item in proposal["evidence"]]
        db.add(LearningCapability(
            id=cap_ids[index], workspace_id=workspace.id, ordinal=index,
            title=proposal["title"], observable_outcome=proposal["observable_outcome"],
            prerequisite_ids=[cap_ids[parent] for parent in proposal["prerequisite_ordinals"]],
            source_ids=list(dict.fromkeys(item["source_id"] for item in evidence)),
            coverage_status=proposal["coverage_status"],
            coverage_details={"gap": proposal["gap"], "evidence": evidence},
        ))


def checked_capabilities(db, workspace, capability_ids):
    caps = db.scalars(
        select(LearningCapability).where(LearningCapability.workspace_id == workspace.id)
        .order_by(LearningCapability.ordinal)
    ).all()
    chosen = set(capability_ids)
    if not chosen or not chosen <= {cap.id for cap in caps}:
        raise HTTPException(422, "请选择本专题已核对的能力")
    validate_prerequisites({cap.id: cap.prerequisite_ids for cap in caps})
    selected = []
    for cap in caps:
        if cap.id not in chosen:
            continue
        if cap.coverage_status not in {"covered", "pending_review"} or not cap.source_ids:
            raise HTTPException(422, "所选能力需要绑定依据并完成核对")
        if not set(cap.prerequisite_ids) <= chosen:
            raise HTTPException(422, "所选能力的先修目标也需要确认")
        sources = check_sources(db, workspace, cap.source_ids, require_current=True)
        selected.append((cap, [source_input(source) for source in sources]))
    return selected


def normalized_prompt(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[\W\d_]+", "", text)


def checked_exercises(database, settings, user_id, outcome, sources):
    result, metadata = invoke_json(
        database, settings, user_id,
        "为一个已核验目标拟1到3道可作答题，先基础题，再有依据的应用或不同情境变式。"
        "合成数值和场景须明确假设、单位与约束。不要依赖未提供的图、公式或事实。"
        "逐题附逐字依据、分项标准与各自逐字依据。同一知识点用相同focus键，题内标准ID唯一。"
        "description供学生查看，只说明要完成什么。标准的参考答案写入expected，不向学生展示。"
        "hints依次为条件提醒、原文引导、局部示例。完整解析只写入solution。",
        {"outcome": outcome, "sources": sources}, Exercises,
    )
    prepared = []
    for exercise in result["tasks"]:
        try:
            ids = evidence_sources(exercise["evidence"], sources, metadata)
            rubric = exercise["rubric"]
            if not exercise["prompt"].strip() or not exercise["solution"].strip():
                continue
            if len({item["id"] for item in rubric}) != len(rubric):
                continue
            if any(not item["description"].strip() or not item["expected"].strip() for item in rubric):
                continue
            if any(not hint.strip() or len(hint) > 240 for hint in exercise["hints"]):
                continue
            for item in rubric:
                item["source_ids"] = evidence_sources(item["evidence"], sources, metadata)
                ids.extend(item["source_ids"])
            exercise["source_ids"] = list(dict.fromkeys(ids))
            normalized = normalized_prompt(exercise["prompt"])
            if any(SequenceMatcher(None, normalized, prompt).ratio() >= 0.88 for prompt, _ in prepared):
                continue
            prepared.append((normalized, exercise))
        except ValueError:
            continue
    if not prepared:
        raise ValueError("题面、标准或原文依据未通过校验")

    # Verify each question independently of the generator, with bounded summaries of its peers.
    approved = []
    for index, (_, exercise) in enumerate(prepared):
        relevant = [source for source in sources if source["id"] in exercise["source_ids"]]
        review, metadata = invoke_json(
            database, settings, user_id,
            "独立审题。检查题目是否检验当前学习目标，题面是否自足，解析及每条标准是否由原文与题设推出。"
            "缺单位、条件、原文不支持或公开标准和前三级提示泄露完整答案时判为不支持。"
            "比较本批较早题面，仅改数字或措辞视为重复。family按实际所需推理分类，"
            "foundation是解释或基础推导，application是在已知情境应用，transfer需实质不同情境或推理。",
            {
                "outcome": outcome, "index": index,
                "task": {
                    **{key: exercise[key] for key in ("prompt", "solution", "hints")},
                    "rubric": [{key: item[key] for key in ("description", "expected", "source_ids")}
                               for item in exercise["rubric"]],
                },
                "earlier_prompts": [item["prompt"][:320] for _, item in prepared[:index]],
                "sources": relevant,
            },
            ExerciseVerdict,
        )
        if review["index"] != index or not review["supported"] or review["duplicate"]:
            continue
        try:
            evidence_sources(exercise["evidence"], relevant, metadata)
            for item in exercise["rubric"]:
                evidence_sources(item["evidence"], relevant, metadata)
        except ValueError:
            continue
        approved.append((exercise, review))
    if not approved:
        raise ValueError("未找到可由原文与题设推出的练习，请补充资料或调整目标")
    return sorted(approved, key=lambda item: item[1]["family"] != "foundation")


def generate_practice(database, settings, user_id, selected):
    verified_capabilities = []
    for cap, sources in selected:
        try:
            verified = verify_coverage(database, settings, user_id, cap.observable_outcome, sources)
            if verified["coverage_status"] != "covered":
                raise ValueError("已绑定原文未能支持完整学习目标，请调整目标或补充依据")
            verified_capabilities.append((cap, sources, verified))
        except ModelUnavailable:
            raise
        except ValueError as exc:
            raise CapabilityRejected(cap.id, str(exc)) from exc
    results = []
    for cap, sources, verified in verified_capabilities:
        try:
            exercises = checked_exercises(database, settings, user_id, cap.observable_outcome, sources)
        except ModelUnavailable:
            raise
        except ValueError as exc:
            raise CapabilityRejected(cap.id, str(exc)) from exc
        results.append((cap.id, verified, exercises))
    return results


def publish_practice(db, workspace, results):
    ordinal = 0
    for capability_id, verified, exercises in results:
        cap = db.get(LearningCapability, capability_id)
        cap.coverage_status = "covered"
        cap.coverage_details = {"gap": "", "evidence": verified["evidence"]}
        for exercise, review in exercises:
            db.add(LearningTask(
                workspace_id=workspace.id, capability_id=cap.id, ordinal=ordinal,
                template_id=f"G{ordinal + 1}", template_version=TASK_VERSION,
                kind=exercise["kind"], task_family=review["family"],
                prompt=exercise["prompt"], rubric=exercise["rubric"], source_ids=exercise["source_ids"],
                hints=[*exercise["hints"], exercise["solution"]],
                generation_details={"evidence": exercise["evidence"], "verification": review},
            ))
            ordinal += 1
    db.flush()
