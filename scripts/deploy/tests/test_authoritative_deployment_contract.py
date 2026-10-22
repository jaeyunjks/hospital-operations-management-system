"""Regression checks for the authoritative shell/OIDC deployment contract."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = (ROOT / ".github/workflows/cloud-deployment.yml").read_text(
    encoding="utf-8"
)
DEPLOY = (ROOT / "scripts/deploy/deploy-student.sh").read_text(encoding="utf-8")
LEGACY = (ROOT / "scripts/deploy/azure_deploy.py").read_text(encoding="utf-8")


def test_workflow_uses_only_oidc_shell_deployment():
    assert "AZURE_CREDENTIALS" not in WORKFLOW
    assert "azure_deploy.py" not in WORKFLOW
    assert "uses: azure/login@v2" in WORKFLOW
    assert "id-token: write" in WORKFLOW
    assert "./scripts/deploy/deploy-student.sh" in WORKFLOW
    assert "./scripts/deploy/deployment-testing.sh" in WORKFLOW


def test_workflow_requires_one_student_and_has_no_deploy_all_default():
    assert "student:" in WORKFLOW
    assert "required: true" in WORKFLOW
    assert 'group: homs-r2-cloud-student-${{ inputs.student }}' in WORKFLOW
    assert "Empty = everything" not in WORKFLOW
    assert "--only" not in WORKFLOW


def test_student5_cloud_flags_and_persistence_are_explicit():
    for value in (
        '"AI_ENABLED=false"',
        '"MCP_ENABLED=false"',
        '"RAG_ENABLED=false"',
        '"MULTI_AGENT_ENABLED=false"',
        '"STUDENT5_DATABASE_PATH=/data/staff_shift.db"',
        "--mount-path /data",
        "--min-replicas 1",
        "--max-replicas 1",
    ):
        assert value in DEPLOY


def test_legacy_python_deployer_is_clearly_deprecated():
    assert "DEPRECATED legacy Azure deployment prototype" in LEGACY
    assert "not used by the active GitHub workflow" in LEGACY
