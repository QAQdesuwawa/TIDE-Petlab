"""已批准 Proposal → 不可变实验计划。

这里只生成实验元数据与 control/candidate 运行配置，不 import Runner、不写 profile，亦不
执行模型调用。实际执行器必须显式读取该计划并产出新的 run manifest。
"""
import copy
import hashlib
import json


EXPERIMENT_VERSION = 1


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _config_sha(config):
    return hashlib.sha256(_canonical(config).encode("utf-8")).hexdigest()[:16]


def select_proposal(proposal_manifest, index):
    """仅允许从 candidate-proposals 中按稳定序号选择一个建议。"""
    proposal = (proposal_manifest or {}).get("proposal") or proposal_manifest or {}
    if proposal.get("verdict") != "candidate-proposals":
        raise ValueError("Proposal 不是 candidate-proposals，不能批准")
    proposals = proposal.get("proposals") or []
    try:
        index = int(index)
        if index < 0:
            raise IndexError
        item = proposals[index]
    except (ValueError, IndexError, TypeError):
        raise ValueError("proposal_index 无效")
    if not isinstance(item, dict):
        raise ValueError("proposal 条目非法")
    return copy.deepcopy(item), copy.deepcopy(proposal)


def build_experiment_plan(review_manifest, proposal_manifest, proposal_index, approval):
    """人工确认后生成实验计划；当前只支持 recall_strategy feature flag 对照。"""
    if not isinstance(approval, dict) or not str(approval.get("approved_by", "")).strip():
        raise ValueError("approval 必须包含 approved_by")
    note = str(approval.get("note", "")).strip()
    if not note:
        raise ValueError("approval 必须包含非空 note")
    item, proposal = select_proposal(proposal_manifest, proposal_index)
    if item.get("proposal_kind") != "recall_strategy_experiment":
        raise ValueError("当前仅支持 recall_strategy_experiment")
    control, candidate = item.get("control"), item.get("candidate")
    if not control or not candidate:
        raise ValueError("Proposal 缺少 control/candidate")
    review = copy.deepcopy(review_manifest or {})
    batch = review.get("batch") or {}
    if batch.get("assessment_id") != proposal.get("assessment_id"):
        raise ValueError("review 与 proposal 的 assessment_id 不一致")

    base_params = sorted({str(group.get("condition", {}).get("params_sha"))
                          for group in (batch.get("groups") or [])
                          if group.get("condition", {}).get("params_sha") is not None})
    if not base_params:
        raise ValueError("review 缺少 base params_sha，不能创建实验")
    runtime_variants = {
        "control": {"recall_strategy": control},
        "candidate": {"recall_strategy": candidate},
    }
    config = {
        "experiment_version": EXPERIMENT_VERSION,
        "assessment_id": batch.get("assessment_id"),
        "proposal_kind": item.get("proposal_kind"),
        "condition": copy.deepcopy(item.get("condition") or {}),
        "runtime_variants": runtime_variants,
        "base_params_sha": base_params,
    }
    return {
        "experiment_version": EXPERIMENT_VERSION,
        "parent_review_id": review.get("review_id"),
        "parent_assessment_id": batch.get("assessment_id"),
        "proposal_index": int(proposal_index),
        "approved_proposal": item,
        "approval": {"approved_by": str(approval["approved_by"]).strip(), "note": note},
        "experiment_config": config,
        "experiment_config_sha": _config_sha(config),
        "status": "planned",
        "execution": {
            "required": "以同一 AssessmentSpec、同一条件单元运行 control/candidate，再建立新 AssessmentBatch",
            "executed_runs": [],
        },
        "guardrails": [
            "不修改 profile 或已有 params 文件",
            "不覆盖任何既有会话或评审产物",
            "feature flag 对照保持同一 base_params_sha；配置差异由 experiment_config_sha 标识",
            "执行需另行显式发起，不由批准动作自动触发",
        ],
    }
