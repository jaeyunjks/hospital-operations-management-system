"""Regression checks for the authoritative shell/OIDC deployment contract."""

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = (ROOT / ".github/workflows/cloud-deployment.yml").read_text(
    encoding="utf-8"
)
DEPLOY = (ROOT / "scripts/deploy/deploy-student.sh").read_text(encoding="utf-8")
FOUNDATION = (ROOT / "scripts/deploy/azure-foundation.sh").read_text(
    encoding="utf-8"
)
LEGACY = (ROOT / "scripts/deploy/azure_deploy.py").read_text(encoding="utf-8")


def shell_output(script: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed Bash executable and test script
        ["/bin/bash", "-c", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


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


def test_student5_uses_existing_short_app_names_without_changing_students_1_to_4():
    output = shell_output(
        "source scripts/deploy/azure-common.sh; "
        "for student in 1 2 3 4 5; do "
        "for service in database backend frontend; do "
        "container_app_name \"$student\" \"$service\"; printf '\\n'; "
        "done; done"
    ).splitlines()
    assert output == [
        "homs-r2-student1-database",
        "homs-r2-student1-backend",
        "homs-r2-student1-frontend",
        "homs-r2-student2-database",
        "homs-r2-student2-backend",
        "homs-r2-student2-frontend",
        "homs-r2-student3-database",
        "homs-r2-student3-backend",
        "homs-r2-student3-frontend",
        "homs-r2-student4-database",
        "homs-r2-student4-backend",
        "homs-r2-student4-frontend",
        "s5-db",
        "s5-api",
        "s5-web",
    ]


def test_foundation_skips_provider_registration_and_can_scope_student5_storage():
    assert "az provider register" not in FOUNDATION
    assert "--student)" in FOUNDATION
    assert 'FOUNDATION_STUDENTS=("$2")' in FOUNDATION

    output = shell_output(
        "AZURE_SUBSCRIPTION_ID=00000000-0000-0000-0000-000000000000 "
        "HOMS_AZURE_ACR_NAME=homsr2dryrunregistry "
        "HOMS_AZURE_STORAGE_ACCOUNT=homsr2dryrunstorage "
        "./scripts/deploy/azure-foundation.sh --student 5 --dry-run"
    )
    assert "Student 5 storage: student5-db -> student5-db-storage" in output
    for student in range(1, 5):
        assert f"Student {student} storage:" not in output


def test_student5_dry_runs_use_final_names_and_only_its_storage_link():
    environment = (
        "AZURE_SUBSCRIPTION_ID=00000000-0000-0000-0000-000000000000 "
        "HOMS_AZURE_ACR_NAME=homsr2dryrunregistry "
        "HOMS_AZURE_STORAGE_ACCOUNT=homsr2dryrunstorage "
    )
    deploy_output = shell_output(
        environment
        + "./scripts/deploy/deploy-student.sh 5 --tag 0123456789abcdef --dry-run"
    )
    test_output = shell_output(
        "./scripts/deploy/deployment-testing.sh 5 --dry-run"
    )
    for name in ("s5-db", "s5-api", "s5-web"):
        assert name in deploy_output
        assert name in test_output
    assert "student5-db-storage mounted at /data" in deploy_output
    assert "homs-r2-student5" not in deploy_output + test_output


def test_retained_status_smoke_and_cloud_review_catalogue_uses_student5_names():
    for name in ("s5-db", "s5-api", "s5-web"):
        assert f'"app": "{name}"' in LEGACY
    assert "homs-r2-student5" not in LEGACY


def test_legacy_python_deployer_is_clearly_deprecated():
    assert "DEPRECATED legacy Azure deployment prototype" in LEGACY
    assert "not used by the active GitHub workflow" in LEGACY
