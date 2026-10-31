from sqlalchemy import select

from app.models import LearningAttempt, LearningCapability, LearningDecision, LearningObstacle, LearningTask, utcnow


def passed(attempt):
    rows = attempt.assessment.get("criteria", [])
    return bool(rows) and all(c["verdict"] == "met" for c in rows)


def recompute(db, workspace):
    caps = db.scalars(select(LearningCapability).where(LearningCapability.workspace_id == workspace.id)).all()
    tasks = {t.id: t for t in db.scalars(select(LearningTask).where(LearningTask.workspace_id == workspace.id))}
    attempts = db.scalars(
        select(LearningAttempt)
        .where(LearningAttempt.workspace_id == workspace.id, LearningAttempt.assessment_status == "completed")
        .order_by(LearningAttempt.submitted_at, LearningAttempt.id)
    ).all()
    for cap in caps:
        valid = [a for a in attempts if tasks[a.task_id].capability_id == cap.id and not a.invalidated]
        independent = [a for a in valid if passed(a) and a.hint_level_at_submit == 0]
        families = {tasks[a.task_id].task_family for a in independent}
        cap.state = "unverified"
        if valid:
            cap.state = "needs_evidence"
        if any(passed(a) for a in valid):
            cap.state = "assisted"
        if independent:
            cap.state = "independent"
        if len(families) >= 2:
            cap.state = "transfer_verified"
        cap.state_evidence_ids = [a.id for a in (independent or valid)[-8:]]
        codes = {
            o["code"]
            for a in attempts
            if tasks[a.task_id].capability_id == cap.id
            for o in a.assessment.get("obstacle_candidates", [])
        }
        for code in codes:
            bad = [a for a in valid if any(o["code"] == code for o in a.assessment.get("obstacle_candidates", []))]
            obstacle = db.scalar(
                select(LearningObstacle).where(
                    LearningObstacle.capability_id == cap.id, LearningObstacle.obstacle_code == code
                )
            )
            if obstacle is None:
                obstacle = LearningObstacle(workspace_id=workspace.id, capability_id=cap.id, obstacle_code=code)
                db.add(obstacle)
            proof_families = {tasks[a.task_id].task_family for a in bad if a.hint_level_at_submit == 0}
            counter = [
                a
                for a in independent
                if bad
                and a.submitted_at > bad[-1].submitted_at
                and tasks[a.task_id].task_family != tasks[bad[-1].task_id].task_family
            ]
            obstacle.status = (
                "resolved"
                if counter
                else "disputed"
                if not bad
                else "confirmed"
                if len(proof_families) >= 2 and code != "expression"
                else "suspected"
            )
            obstacle.evidence_refs = [a.id for a in bad]
            obstacle.counter_evidence_refs = [a.id for a in counter]
            obstacle.updated_at = utcnow()
            if bad and not counter:
                cap.state = "needs_evidence"
    db.flush()


def choose_next(db, workspace, trigger=None, skip=False, request_key=None):
    before = workspace.current_task_id
    tasks = db.scalars(
        select(LearningTask).where(LearningTask.workspace_id == workspace.id).order_by(LearningTask.ordinal)
    ).all()
    caps = {
        c.id: c for c in db.scalars(select(LearningCapability).where(LearningCapability.workspace_id == workspace.id))
    }
    if skip and before:
        next(t for t in tasks if t.id == before).status = "skipped"
    pending = [
        t
        for t in tasks
        if t.status == "pending"
        and caps[t.capability_id].coverage_status == "covered"
        and caps[t.capability_id].state != "transfer_verified"
        and all(caps[p].state in {"independent", "transfer_verified"} for p in caps[t.capability_id].prerequisite_ids)
    ]
    selected, reason = None, "目前没有可用的后续练习；请检查资料缺口、先修能力或被跳过的任务。"
    if trigger and not trigger.invalidated:
        current = next(t for t in tasks if t.id == trigger.task_id)
        related = [t for t in pending if t.capability_id == current.capability_id]
        weak = not passed(trigger)
        if weak:
            wanted = ["T3", "T5"] if current.template_id == "T2" else ["T5", "T7", "T4", "T6", "T8"]
            obstacles = db.scalars(
                select(LearningObstacle).where(
                    LearningObstacle.capability_id == current.capability_id,
                    LearningObstacle.status.in_(["suspected", "confirmed"]),
                )
            ).all()
            codes = {o.obstacle_code for o in obstacles}
            if "interleaving" in codes:
                wanted = ["T5", "T3", "T7"]
            elif "prevention" in codes:
                wanted = ["T4", "T6", "T8"]
            elif "stock_value_vs_order_count" in codes or "expression" in codes:
                wanted = ["T3", "T5", "T7"]
            selected = next((t for tid in wanted for t in related if t.template_id == tid), None)
            criteria = [c["criterion_id"] for c in trigger.assessment.get("criteria", []) if c["verdict"] != "met"]
            reason = f"提交未充分满足标准 {', '.join(criteria)}；先完成聚焦追问或新的场景分析，原判断仍可纠正。"
            names = {
                "interleaving": "交错执行理解",
                "stock_value_vs_order_count": "库存值与订单数区分",
                "prevention": "防护方法",
                "expression": "表达证据",
            }
            if obstacles:
                reason += (
                    " 当前线索："
                    + "；".join(
                        f"{names[o.obstacle_code]}（{'有重复证据' if o.status == 'confirmed' else '疑似'}，{len(o.evidence_refs)}条作答）"
                        for o in obstacles
                    )
                    + "。"
                )
        elif trigger.hint_level_at_submit:
            selected = next((t for t in related if t.kind != "followup"), None)
            reason = f"本次满足标准，但已使用第 {trigger.hint_level_at_submit} 级帮助；下一步用新变式补充独立完成证据。"
        else:
            reason = "本次未使用系统帮助并满足标准；减少相同定义重复，继续可用能力或不同场景验证。"
    if selected is None and pending:
        # Delay optional follow-up and remediation until indicated by evidence.
        selected = next((t for t in pending if t.template_id not in {"T3", "T4", "T7"}), pending[0])
    if selected and trigger is None:
        reason = (
            "已跳过上一项，跳过不计为通过；按先修关系选择下一份可用练习。"
            if skip
            else "目标和资料已确认，从满足先修条件的初始任务开始，先用作答确定学习起点。"
        )
    elif selected is None:
        reason += " 当前题库中没有满足条件的下一题；未完成验证的能力仍保留未验证状态。"
    workspace.current_task_id = selected.id if selected else None
    workspace.updated_at = utcnow()
    workspace.state_version += 1
    workspace.status = (
        "active"
        if selected
        else "completed"
        if caps and all(c.state == "transfer_verified" for c in caps.values())
        else "needs_practice"
    )
    if workspace.status == "completed":
        reason = "三个能力均已在至少两类任务中独立满足标准；本专题验证完成。历史作答和调整依据仍可回看与纠正。"
    evidence_refs = [trigger.id] if trigger else []
    if trigger and not trigger.invalidated:
        obstacles = db.scalars(
            select(LearningObstacle).where(
                LearningObstacle.capability_id == current.capability_id,
                LearningObstacle.status.in_(["suspected", "confirmed"]),
            )
        ).all()
        evidence_refs.extend(aid for obstacle in obstacles for aid in obstacle.evidence_refs)
    if workspace.status == "completed":
        evidence_refs.extend(aid for cap in caps.values() for aid in cap.state_evidence_ids)
    db.add(
        LearningDecision(
            workspace_id=workspace.id,
            request_key=request_key or f"auto:{trigger.id if trigger else 'confirm'}",
            trigger_attempt_id=trigger.id if trigger else None,
            kind="skip" if skip else "next",
            evidence_refs=list(dict.fromkeys(evidence_refs)),
            before_state={"task_id": before},
            after_state={"task_id": workspace.current_task_id, "status": workspace.status},
            reason=reason,
        )
    )
    db.flush()
    return workspace.current_task_id
