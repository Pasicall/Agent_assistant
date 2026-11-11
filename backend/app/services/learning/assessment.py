from app.schemas_learning import AssessmentResult, GeneralAssessmentResult
from app.services.learning.general import TASK_VERSION
from app.services.learning.model import invoke_json


def validate_assessment(result, task, attempt):
    general = getattr(task, "template_version", None) == TASK_VERSION
    schema = GeneralAssessmentResult if general else AssessmentResult
    result = schema.model_validate(result).model_dump()
    expected = {c["id"] for c in task.rubric}
    actual = [c["criterion_id"] for c in result["criteria"]]
    if len(actual) != len(expected) or set(actual) != expected:
        raise ValueError("评价标准缺失、重复或越界")
    by_id = {c["criterion_id"]: c for c in result["criteria"]}
    rubric = {c["id"]: c for c in task.rubric}
    for criterion in result["criteria"]:
        if not set(criterion["source_ids"]) <= set(task.source_ids):
            raise ValueError("评价引用不属于本题来源")
        if general and not set(criterion["source_ids"]) <= set(rubric[criterion["criterion_id"]]["source_ids"]):
            raise ValueError("评价引用不属于该评分标准的依据")
        quotes = criterion["student_quotes"]
        if any(not q.strip() or q not in attempt.answer_text for q in quotes):
            raise ValueError("评价引用了不存在的学生原句")
        if criterion["verdict"] != "insufficient_evidence" and not quotes:
            raise ValueError("评价缺少学生原句；无法判断时应标记证据不足")
        criterion["student_evidence"] = [
            {"quote": q, "start": attempt.answer_text.index(q), "end": attempt.answer_text.index(q) + len(q)}
            for q in quotes
        ]
    if general:
        return result
    codes = [o["code"] for o in result["obstacle_candidates"]]
    if len(codes) != len(set(codes)):
        raise ValueError("障碍码重复")
    for obstacle in result["obstacle_candidates"]:
        if not set(obstacle["criterion_ids"]) <= expected:
            raise ValueError("障碍关联了不存在的标准")
        if all(by_id[c]["verdict"] == "met" for c in obstacle["criterion_ids"]):
            raise ValueError("已满足的标准不能同时作为学习障碍证据")
        if obstacle["code"] != "expression" and not any(
            by_id[c]["verdict"] in {"not_met", "partial"} and by_id[c]["student_quotes"]
            for c in obstacle["criterion_ids"]
        ):
            raise ValueError("知识障碍缺少明确错误证据")
    return result


def assess(database, settings, user_id, task, attempt, sources):
    general = task.template_version == TASK_VERSION
    rubric = task.rubric
    if general:
        rubric = [{key: item[key] for key in ("id", "description", "expected", "source_ids")} for item in rubric]
    payload = {
        "task": task.prompt,
        "rubric": rubric,
        "answer": attempt.answer_text,
        "sources": [{"id": s.id, "text": s.excerpt[:1800]} for s in sources],
    }
    if general:
        payload["reference_solution"] = task.hints[3]
    guidance = (
        "同义表达按含义评分，每项只引用该标准source_ids绑定的资料；证据不足时给一个聚焦追问。"
        if general else "障碍仅为候选，给出另一种解释。只给一个聚焦追问或局部指导，"
    )
    result, metadata = invoke_json(
        database,
        settings,
        user_id,
        "你是谨慎的学习辅导评价器。只按题目rubric逐项评价；遗漏、歧义或表达不足用insufficient_evidence。"
        "student_quotes必须逐字引用answer。每项说明来源id；不得把遗漏直接当知识错误。"
        + guidance + "避免直接揭示完整解法。"
        "如果反馈给出完整解法，reveals_solution必须为true。不能宣称掌握程度或自动改状态。",
        payload,
        GeneralAssessmentResult if general else AssessmentResult,
    )
    result = validate_assessment(result, task, attempt)
    if general and task.hints[3] in result["feedback"]:
        result["reveals_solution"] = True
    return {**result, "metadata": metadata}
