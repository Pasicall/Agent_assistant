from app.schemas_learning import AssessmentResult
from app.services.learning.model import invoke_json


def validate_assessment(result, task, attempt):
    result = AssessmentResult.model_validate(result).model_dump()
    expected = {c["id"] for c in task.rubric}
    actual = [c["criterion_id"] for c in result["criteria"]]
    if len(actual) != len(expected) or set(actual) != expected:
        raise ValueError("评价标准缺失、重复或越界")
    by_id = {c["criterion_id"]: c for c in result["criteria"]}
    for criterion in result["criteria"]:
        if not set(criterion["source_ids"]) <= set(task.source_ids):
            raise ValueError("评价引用不属于本题来源")
        quotes = criterion["student_quotes"]
        if any(not q.strip() or q not in attempt.answer_text for q in quotes):
            raise ValueError("评价引用了不存在的学生原句")
        if criterion["verdict"] != "insufficient_evidence" and not quotes:
            raise ValueError("评价缺少学生原句；无法判断时应标记证据不足")
        criterion["student_evidence"] = [
            {"quote": q, "start": attempt.answer_text.index(q), "end": attempt.answer_text.index(q) + len(q)}
            for q in quotes
        ]
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
    payload = {
        "task": task.prompt,
        "rubric": task.rubric,
        "answer": attempt.answer_text,
        "sources": [{"id": s.id, "text": s.excerpt[:1800]} for s in sources],
    }
    result, metadata = invoke_json(
        database,
        settings,
        user_id,
        "你是谨慎的学习辅导评价器。只按题目rubric逐项评价；遗漏、歧义或表达不足用insufficient_evidence。"
        "student_quotes必须逐字引用answer。每项说明来源id；不得把遗漏直接当知识错误。"
        "障碍仅为候选，给出另一种解释。只给一个聚焦追问或局部指导，避免直接揭示完整解法；"
        "如果反馈给出完整解法，reveals_solution必须为true。不能宣称掌握程度或自动改状态。",
        payload,
        AssessmentResult,
    )
    result = validate_assessment(result, task, attempt)
    return {**result, "metadata": metadata}
