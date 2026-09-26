"""Le juge : ce qu'il accepte, ce qu'il refuse, et ce qu'il ne laisse pas fuir."""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from conftest import EPHOR, FIX, ROOT, WRONG, contract, git, judge, trace_rouge

Runner = Callable[..., tuple[int, dict[str, Any]]]


def test_correct_fix_is_verified_without_touching_the_repo(repo: Path, run_judge: Runner) -> None:
    (repo / "src" / "metrics.py").write_text(FIX)
    status_before = git(repo, "status", "--porcelain")
    index_before = (repo / ".git" / "index").read_bytes()
    code, report = run_judge(repo)
    assert code == 0
    assert report["status"] == "verified_needs_review"
    assert [item["passed"] for item in report["criteria"]] == [True, True]
    assert report["changed_paths"] == ["src/metrics.py"]
    assert report["review_required"] is True and report["merged"] is False
    assert git(repo, "status", "--porcelain") == status_before
    assert (repo / ".git" / "index").read_bytes() == index_before
    assert len(git(repo, "worktree", "list").splitlines()) == 1
    latest = repo / ".verify" / "judge" / "T-001" / "latest"
    assert (latest / "junit.xml").exists() and (latest / "summary.txt").exists()


def test_progress_md_is_never_out_of_scope(repo: Path, run_judge: Runner) -> None:
    """La boucle de travail demande de tenir PROGRESS.md : le juge ne peut pas le punir."""
    (repo / "src" / "metrics.py").write_text(FIX)
    (repo / "PROGRESS.md").write_text("## Fait\n- C1 — commit local\n")
    code, report = run_judge(repo)
    assert code == 0 and report["status"] == "verified_needs_review"
    assert "PROGRESS.md" in report["changed_paths"]


def test_the_report_carries_what_the_task_has_cost(repo: Path, run_judge: Runner) -> None:
    """`usage` vaut null tant que rien n'est mesuré ; jamais zéro, qui voudrait dire gratuit."""
    (repo / "src" / "metrics.py").write_text(FIX)
    code, report = run_judge(repo)
    assert code == 0 and report["usage"] is None
    mesure = repo / ".verify" / "usage" / "T-001.json"
    mesure.parent.mkdir(parents=True, exist_ok=True)
    mesure.write_text(json.dumps({"facturables": 1110, "requetes": 3, "source": "claude-code"}))
    code, report = run_judge(repo)
    assert code == 0 and report["usage"]["facturables"] == 1110
    resume = (repo / ".verify" / "judge" / "T-001" / "latest" / "summary.txt").read_text()
    assert "1 110 facturables" in resume


def test_a_copilot_minimum_is_said_and_never_logged_as_a_cost(
    repo: Path, run_judge: Runner
) -> None:
    """Session Copilot encore ouverte : ses tokens manquent. Le résumé dit « au moins » et ce
    que Copilot a facturé ; le journal des jugements n'en fait pas un coût, qui ferait
    paraître la tâche bon marché au tableau de bord."""
    (repo / "src" / "metrics.py").write_text(FIX)
    mesure = repo / ".verify" / "usage" / "T-001.json"
    mesure.parent.mkdir(parents=True, exist_ok=True)
    mesure.write_text(
        json.dumps(
            {
                "facturables": 900,
                "requetes": 4,
                "source": "copilot",
                "nano_aiu": 1_590_000_000,
                "requetes_premium": 2,
                "detail_complet": False,
            }
        )
    )
    code, report = run_judge(repo)
    assert code == 0 and report["usage"]["detail_complet"] is False
    resume = (repo / ".verify" / "judge" / "T-001" / "latest" / "summary.txt").read_text()
    assert "au moins 900 facturables" in resume and "1,59 AIU, 2 premium" in resume
    lignes = (repo / ".verify" / "journal.jsonl").read_text().splitlines()
    assert json.loads(lignes[-1])["billable_tokens"] is None


def test_wrong_fix_fails_on_the_criteria(repo: Path, run_judge: Runner) -> None:
    (repo / "src" / "metrics.py").write_text(WRONG)
    code, report = run_judge(repo)
    assert code == 1
    assert report["status"] == "verification_failed"
    assert [item["passed"] for item in report["criteria"]] == [False, False]


def test_new_file_in_scope_is_part_of_the_patch(repo: Path, run_judge: Runner) -> None:
    (repo / "src" / "metrics.py").write_text(FIX)
    (repo / "src" / "helper.py").write_text("VALUE = 1\n")
    code, report = run_judge(repo)
    assert code == 0
    assert "src/helper.py" in report["changed_paths"]


def test_committed_branch_work_is_judged_too(repo: Path, run_judge: Runner) -> None:
    (repo / "src" / "metrics.py").write_text(FIX)
    git(repo, "commit", "-qam", "fix")
    code, report = run_judge(repo)
    assert code == 0
    assert report["changed_paths"] == ["src/metrics.py"]


def test_change_outside_the_contract_is_rejected(repo: Path, run_judge: Runner) -> None:
    (repo / "src" / "metrics.py").write_text(FIX)
    (repo / "README.md").write_text("hors périmètre\n")
    code, report = run_judge(repo)
    assert code == 2
    assert report["status"] == "rejected"
    assert any("hors du périmètre" in reason for reason in report["reasons"])
    assert report["steps"] == []


@pytest.mark.parametrize(
    "path",
    [
        "tasks/T-001/check_c1.py",
        ".ephor/judge.py",
        ".ephor/projet/verify.sh",
        ".gitlab-ci.yml",
        ".github/workflows/ci.yml",
        "Makefile",
        "AGENTS.md",
        ".claude/settings.json",
        ".ephor/deploy/values.yaml",
    ],
)
def test_control_files_cannot_be_changed(repo: Path, run_judge: Runner, path: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("pass\n")
    code, report = run_judge(repo)
    assert code == 2
    assert any("fichier de contrôle" in reason for reason in report["reasons"])


def test_acceptance_test_outside_tasks_is_guarded(
    make_repo: Callable[..., Path], run_judge: Runner
) -> None:
    repo = make_repo(
        criteria=[
            {
                "id": "C1",
                "description": "test d'acceptation placé dans tests/",
                "check": ["{python}", "tests/acceptance_check.py"],
            }
        ]
    )
    git(repo, "switch", "-q", "main")
    (repo / "tests" / "acceptance_check.py").write_text("raise SystemExit(1)\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "test d'acceptation")
    trace_rouge(repo)  # l'opérateur refait le contrôle rouge
    git(repo, "switch", "-q", "task/T-001")
    git(repo, "rebase", "-q", "main")
    (repo / "tests" / "acceptance_check.py").write_text("pass\n")
    code, report = run_judge(repo)
    assert code == 2
    assert any("test d'acceptation modifié" in reason for reason in report["reasons"])


def test_a_proof_the_agent_may_write_itself_is_refused(
    make_repo: Callable[..., Path], run_judge: Runner
) -> None:
    """Le critère s'appuie sur un fichier absent de la base et que le contrat laisse écrire :
    l'agent noterait sa propre copie. Le juge refuse avant d'exécuter quoi que ce soit."""
    repo = make_repo(
        criteria=[
            {
                "id": "C1",
                "description": "preuve que l'agent écrirait lui-même",
                "check": ["{python}", "tests/preuve.py"],
            }
        ]
    )
    (repo / "src" / "metrics.py").write_text(WRONG)
    (repo / "tests" / "preuve.py").write_text("pass\n")
    code, report = run_judge(repo)
    assert code == 2
    assert any("preuve dans le périmètre" in reason for reason in report["reasons"])


def test_nothing_runs_before_the_patch_is_accepted(
    repo: Path, run_judge: Runner, tmp_path: Path
) -> None:
    """« REFUSÉ, rien n'a été exécuté » doit être vrai à la lettre.

    La liste des dépôts de référence nomme des dossiers, et le juge y lance git pour noter
    leur commit. Tant que le patch n'est pas accepté, ce fichier vient de l'arbre de travail
    de l'agent : lancer quoi que ce soit d'après lui, ce serait lui obéir.
    """
    piege = tmp_path / "piege"
    piege.mkdir()
    declaration = repo / ".ephor" / "projet" / "reference-repos.json"
    declaration.parent.mkdir(parents=True)
    declaration.write_text(
        json.dumps({"depots": [{"nom": "piege", "chemin": str(piege)}]}), encoding="utf-8"
    )
    code, report = run_judge(repo)
    assert code == 2
    assert report["reference_repos"] == {}


def test_contract_must_be_approved_on_the_base(repo: Path, run_judge: Runner) -> None:
    contract_file = repo / "tasks" / "T-001" / "contract.json"
    data = json.loads(contract_file.read_text())
    data["criteria"] = data["criteria"][:1]
    contract_file.write_text(json.dumps(data, indent=2) + "\n")
    code, report = run_judge(repo)
    assert code == 2
    assert any("approuvée" in reason for reason in report["reasons"])


def test_contract_created_only_on_the_branch_is_rejected(repo: Path, tmp_path: Path) -> None:
    folder = repo / "tasks" / "T-002"
    folder.mkdir()
    data = json.loads((repo / "tasks" / "T-001" / "contract.json").read_text())
    data["id"] = "T-002"
    (folder / "contract.json").write_text(json.dumps(data))
    code = judge.main(
        ["check", "--task-id", "T-002", "--repo", str(repo), "--workdir", str(tmp_path / "w")]
    )
    assert code == 2
    report = json.loads((repo / ".verify/judge/T-002/latest/report.json").read_text())
    assert any("absent de la base" in reason for reason in report["reasons"])


def test_symlink_is_rejected(repo: Path, run_judge: Runner) -> None:
    (repo / "src" / "link").symlink_to("/etc/passwd")
    code, report = run_judge(repo)
    assert code == 2
    assert any("lien symbolique" in reason for reason in report["reasons"])


def test_no_change_is_not_a_success(repo: Path, run_judge: Runner) -> None:
    code, report = run_judge(repo)
    assert code == 1
    assert report["status"] == "no_change"


def test_api_keys_never_reach_the_tests(
    make_repo: Callable[..., Path], run_judge: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets = {
        "LITELLM_API_KEY": "sk-litellm",
        "ANTHROPIC_AUTH_TOKEN": "tok",
        "OPENAI_API_KEY": "sk-openai",
        "GITLAB_TOKEN": "glpat",
        "UV_INDEX_JFROG_PASSWORD": "s3cret",
    }
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)
    probe = (
        f"import os, sys; leaked = [k for k in {list(secrets)!r} if k in os.environ]; "
        "sys.exit(f'fuite : {leaked}' if leaked else 0)"
    )
    setup_probe = (
        "import os, sys; "
        "ok = os.environ.get('UV_INDEX_JFROG_PASSWORD') == 's3cret'; "
        "sys.exit(0 if ok else 'identifiant JFrog absent')"
    )
    repo = make_repo(
        setup=["{python}", "-c", setup_probe],
        verify=[
            "{python}",
            "-c",
            probe + "; assert os.environ['VERIFY_STRICT'] == '1'",
        ],
    )
    (repo / "src" / "metrics.py").write_text(FIX)
    code, report = run_judge(repo)
    assert code == 0, report


def test_git_hook_trick_is_neutralized(repo: Path, run_judge: Runner, tmp_path: Path) -> None:
    """Un agent tricheur plante un hook Git qui neutralise les tests dans la copie vérifiée."""
    hooks = tmp_path / "evil-hooks"
    hooks.mkdir()
    marker = tmp_path / "hook-ran"
    hook = hooks / "post-checkout"
    hook.write_text(
        "#!/bin/sh\n"
        f"touch {marker}\n"
        'for f in tasks/T-001/check_c1.py tasks/T-001/check_c2.py; do echo pass > "$f"; done\n'
    )
    hook.chmod(0o755)
    git(repo, "config", "core.hooksPath", str(hooks))
    (repo / "src" / "metrics.py").write_text(WRONG)
    code, report = run_judge(repo)
    assert code == 1
    assert report["status"] == "verification_failed"
    assert not marker.exists()


def test_change_during_verification_makes_the_result_stale(
    make_repo: Callable[..., Path], run_judge: Runner, tmp_path: Path
) -> None:
    target = tmp_path / "repo" / "src" / "metrics.py"
    repo = make_repo(
        verify=[
            "{python}",
            "-c",
            f"import pathlib; pathlib.Path({str(target)!r}).write_text('x = 1\\n')",
        ]
    )
    (repo / "src" / "metrics.py").write_text(FIX)
    code, report = run_judge(repo)
    assert code == 1
    assert report["status"] == "stale"


def test_time_budget_is_enforced(make_repo: Callable[..., Path], run_judge: Runner) -> None:
    repo = make_repo(timeout_seconds=1, verify=["{python}", "-c", "import time; time.sleep(10)"])
    (repo / "src" / "metrics.py").write_text(FIX)
    code, report = run_judge(repo)
    assert code == 1
    assert report["status"] == "budget_exhausted"


def test_a_criterion_can_impose_its_own_maximum_duration(
    make_repo: Callable[..., Path], run_judge: Runner
) -> None:
    repo = make_repo(
        criteria=[
            {
                "id": "C1",
                "description": "lots de tailles différentes",
                "check": ["{python}", "tasks/T-001/check_c1.py"],
                "max_duration_seconds": 60,
            },
            {
                "id": "C2",
                "description": "doit répondre vite",
                "check": ["{python}", "-c", "import time; time.sleep(10)"],
                "max_duration_seconds": 1,
            },
        ]
    )
    (repo / "src" / "metrics.py").write_text(FIX)
    code, report = run_judge(repo)
    assert code == 1
    # Un critère trop lent est un échec de vérification, pas un budget de temps épuisé.
    assert report["status"] == "verification_failed"
    assert [item["passed"] for item in report["criteria"]] == [True, False]
    assert report["criteria"][1]["too_slow"] is True
    assert report["criteria"][0]["seconds"] <= 60
    summary = (repo / ".verify" / "judge" / "T-001" / "latest" / "summary.txt").read_text()
    assert "trop lent" in summary


def test_every_judgement_leaves_a_line_in_the_journal(repo: Path, run_judge: Runner) -> None:
    """Le rapport d'une tâche est écrasé à chaque exécution : seule cette trace permet
    de suivre une tendance, donc de décider si un composant d'Ephor paie."""
    (repo / "src" / "metrics.py").write_text(FIX)
    run_judge(repo)
    (repo / "src" / "metrics.py").write_text(WRONG)
    run_judge(repo)
    lines = (repo / ".verify" / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["status"] for line in lines] == [
        "verified_needs_review",
        "verification_failed",
    ]
    first = json.loads(lines[0])
    assert first["task_id"] == "T-001"
    assert first["criteria_passed"] == first["criteria_total"] == 2
    assert first["seconds"] > 0


def test_task_id_cannot_escape_tasks(repo: Path, tmp_path: Path) -> None:
    code = judge.main(
        ["check", "--task-id", "../T-001", "--repo", str(repo), "--workdir", str(tmp_path)]
    )
    assert code == 2


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"extra": 1}, "clés inconnues"),
        ({"risk": "moyen"}, "risk"),
        ({"allowed_paths": ["tasks/"]}, "zone de contrôle"),
        ({"allowed_paths": ["../src/"]}, "invalide"),
        ({"allowed_paths": ["src/*"]}, "invalide"),
        ({"allowed_paths": [".ephor/judge.py"]}, "zone de contrôle"),
        ({"timeout_seconds": True}, "timeout_seconds"),
        ({"verify": []}, "verify"),
        ({"id": "t-1"}, "id invalide"),
        (
            {
                "criteria": [
                    {
                        "id": "C1",
                        "description": "durée absurde",
                        "check": ["{python}", "-c", "pass"],
                        "max_duration_seconds": 0,
                    }
                ]
            },
            "max_duration_seconds",
        ),
        (
            {
                "criteria": [
                    {
                        "id": "C1",
                        "description": "clé inventée",
                        "check": ["{python}", "-c", "pass"],
                        "delai": 5,
                    }
                ]
            },
            "clés attendues",
        ),
    ],
)
def test_malformed_contracts_are_refused(change: dict[str, Any], message: str) -> None:

    with pytest.raises(judge.JudgeError) as caught:
        judge.parse_contract(contract(**change))
    assert any(message in item for item in caught.value.messages)


def test_duplicate_criterion_is_refused() -> None:

    data = contract()
    data["criteria"].append(dict(data["criteria"][0]))
    with pytest.raises(judge.JudgeError, match="double"):
        judge.parse_contract(data)


def test_new_creates_a_valid_skeleton_once(repo: Path) -> None:
    assert judge.main(["new", "--task-id", "FEAT-002", "--repo", str(repo)]) == 0
    assert (repo / "tasks" / "FEAT-002" / "test_feat_002.py").exists()
    assert judge.main(["validate", "--task-id", "FEAT-002", "--repo", str(repo)]) == 0
    assert judge.main(["new", "--task-id", "FEAT-002", "--repo", str(repo)]) == 2


def test_a_reviewed_contract_can_come_from_a_file(repo: Path, tmp_path: Path) -> None:
    """Le plan relu par un humain entre tel quel : plus rien à recoller à la main."""

    plan = contract(
        id="FEAT-003",
        criteria=[
            {
                "id": "C1",
                "description": "le cas normal",
                "check": ["uv", "run", "pytest", "tasks/FEAT-003/test_feat_003.py::test_normal"],
                "max_duration_seconds": 30,
            },
            {
                "id": "C2",
                "description": "la liste vide",
                "check": ["uv", "run", "pytest", "tasks/FEAT-003/test_feat_003.py::test_vide"],
            },
        ],
    )
    source = tmp_path / "plan.json"
    source.write_text(json.dumps(plan), encoding="utf-8")

    code = judge.main(["new", "--task-id", "FEAT-003", "--repo", str(repo), "--from", str(source)])
    assert code == 0
    written = json.loads((repo / "tasks" / "FEAT-003" / "contract.json").read_text())
    assert written["criteria"][0]["description"] == "le cas normal"
    assert written["criteria"][0]["max_duration_seconds"] == 30
    # Les fichiers de test cités par les critères sont créés, un test vide par critère.
    skeleton = (repo / "tasks" / "FEAT-003" / "test_feat_003.py").read_text()
    assert "def test_normal()" in skeleton
    assert "def test_vide()" in skeleton
    assert judge.main(["validate", "--task-id", "FEAT-003", "--repo", str(repo)]) == 0


@pytest.mark.parametrize(
    "plan",
    ['{"id": "FEAT-004"', '{"id": "AUTRE-001"}', "", '{"id": "FEAT-004", "allowed_paths": 3}'],
)
def test_a_bad_plan_creates_nothing(repo: Path, tmp_path: Path, plan: str) -> None:
    source = tmp_path / "plan.json"
    source.write_text(plan, encoding="utf-8")
    code = judge.main(["new", "--task-id", "FEAT-004", "--repo", str(repo), "--from", str(source)])
    assert code == 2
    assert not (repo / "tasks" / "FEAT-004").exists()


def test_a_plan_that_grabs_a_control_zone_is_refused(repo: Path, tmp_path: Path) -> None:

    source = tmp_path / "plan.json"
    source.write_text(json.dumps(contract(id="FEAT-005", allowed_paths=[".ephor/"])))
    code = judge.main(["new", "--task-id", "FEAT-005", "--repo", str(repo), "--from", str(source)])
    assert code == 2
    assert not (repo / "tasks" / "FEAT-005").exists()


def test_a_missing_plan_is_said_plainly(repo: Path, tmp_path: Path) -> None:
    code = judge.main(
        ["new", "--task-id", "FEAT-006", "--repo", str(repo), "--from", str(tmp_path / "absent")]
    )
    assert code == 2


def test_example_contract_of_the_template_is_valid() -> None:
    path = Path(__file__).resolve().parents[2] / "tasks" / "EXEMPLE-001" / "contract.json"
    contract = judge.load_contract(path, "EXEMPLE-001")
    assert contract.risk in judge.RISKS


def engine() -> str | None:
    for name in ("podman", "docker"):
        if judge.engine_ready(name):
            return name
    return None


@pytest.mark.container
@pytest.mark.skipif(engine() is None, reason="ni podman ni docker prêts")
def test_container_runtime_runs_the_checks_offline(
    make_repo: Callable[..., Path], run_judge: Runner
) -> None:
    offline = (
        "import socket, sys\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
        "except OSError:\n"
        "    sys.exit(0)\n"
        "sys.exit('le réseau est ouvert pendant la vérification')\n"
    )
    repo = make_repo(
        criteria=[
            {"id": "C1", "description": "lots", "check": ["{python}", "tasks/T-001/check_c1.py"]},
            {
                "id": "C3",
                "description": "sans réseau",
                "check": ["{python}", "tasks/T-001/offline.py"],
            },
        ]
    )
    git(repo, "switch", "-q", "main")
    (repo / "tasks" / "T-001" / "offline.py").write_text(offline)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "critère hors ligne")
    trace_rouge(repo)  # l'opérateur refait le contrôle rouge
    git(repo, "switch", "-q", "task/T-001")
    git(repo, "rebase", "-q", "main")
    (repo / "src" / "metrics.py").write_text(FIX)
    image = os.environ.get("EPHOR_TEST_IMAGE", "python:3.12-slim")
    # Sous le HOME : podman machine ne partage que lui avec sa machine virtuelle.
    workdir = Path.home() / ".cache" / "ephor" / "tests" / uuid.uuid4().hex[:8]
    try:
        code, report = run_judge(
            repo, "--runtime", str(engine()), "--image", image, "--workdir", str(workdir)
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    assert code == 0, report
    assert report["runtime"]["kind"] in ("podman", "docker")


def test_ephor_keeps_python_3_9_syntax() -> None:
    """Le Python livré par Apple est un 3.9 : tout Ephor doit rester lisible par lui.

    Ces fichiers sont lancés par `python3`, pas par l'environnement du projet : le juge
    en CI, le compteur depuis un hook, la carte et l'aspirateur de docs depuis un script.
    """
    modules = (
        "judge.py",
        "adapters.py",
        "usage.py",
        "repo_map.py",
        "fetch_docs.py",
        "score.py",
        "personal_data.py",
    )
    dossier_ephor = Path(judge.__file__).parent
    fichiers = [dossier_ephor / nom for nom in modules]
    # `.ephor/deploy/manifest.py` est lancé par `python3` lui aussi (make delivery-manifest)
    # et le bootstrap le copie dans les dépôts cibles. Il était hors de cette liste, et il
    # importait `datetime.UTC` : il plantait sur le Python d'Apple.
    fichiers.append(dossier_ephor / "deploy" / "manifest.py")
    for source in fichiers:
        ast.parse(source.read_text(), feature_version=(3, 9))

    # La grammaire ne dit rien de la bibliothèque : `datetime.UTC` passerait. On importe
    # donc pour de vrai, si un 3.9 traîne sur la machine (celui d'Apple, en général).
    for interprete in ("/usr/bin/python3", shutil.which("python3.9")):
        if not interprete or not Path(interprete).exists():
            continue
        version = subprocess.run(
            [interprete, "-c", "import sys; print(sys.version_info[:2])"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if version != "(3, 9)":
            continue
        dossiers = [str(dossier_ephor), str(dossier_ephor / "deploy")]
        importe = ", ".join([*(nom[:-3] for nom in modules), "manifest"])
        code = f"import sys; sys.path[:0] = {dossiers!r}; import {importe}"
        resultat = subprocess.run([interprete, "-c", code], capture_output=True, text=True)
        assert resultat.returncode == 0, resultat.stderr[-400:]


def test_les_zones_protegees_suivent_les_reglages() -> None:
    """Le juge a sa propre liste de zones de contrôle. Elle avait pris du retard : elle
    protégeait encore `.gitleaksignore`, supprimé, et pas `.gitleaks.toml` qui le remplace
    — donc un patch de tâche pouvait s'ajouter une exemption de secret et passer."""
    assert judge.is_protected(".gitleaks.toml")
    assert judge.is_protected(".ephor/greffon-copilot/hooks/relais.sh")
    assert not judge.is_protected(".gitleaksignore")
    reglages = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    refuses = [
        regle[5:-1]
        for regle in reglages["permissions"]["deny"]
        if regle.startswith("Edit(") and "*" not in regle[5:-1].rstrip("*/")
    ]
    for chemin in refuses:
        cible = chemin.rstrip("/*").rstrip("/")
        assert judge.is_protected(cible) or judge.is_protected(cible + "/x"), (
            f"{chemin} est refusé aux agents par les réglages, mais le juge le laisse passer"
        )


def test_engine_hash_attestation() -> None:
    """L'empreinte du juge est un SHA-256 valide et déterministe."""
    digest = judge.engine_hash()
    assert len(digest) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert judge.engine_hash() == digest


def test_le_conteneur_garde_un_work_inscriptible(tmp_path: Path) -> None:
    """/work ne peut PAS être monté en lecture seule, et ce n'est pas de la frilosité.

    Le cache d'uv (`/work/.ephor-cache/uv`), l'environnement virtuel et les caches de
    pytest vivent DEDANS. Monté en lecture seule, une vérification ordinaire meurt sur
    « Read-only file system » — mesuré avec docker, juge complet, code juste : le contrat
    passait avant, il échouait après. Le bac à sable, l'un des arguments d'Ephor,
    devenait inutilisable.

    L'idée reste bonne. Elle demande d'abord de sortir les caches de /work vers un montage
    temporaire inscriptible. Ce test est là pour qu'on ne la remette pas sans ça.
    """
    runtime = judge.Runtime(kind="podman", image="fake:latest")
    for phase in ("setup", "verify"):
        commande, _ = judge.container_command(
            runtime=runtime,
            argv=["pytest"],
            copy=tmp_path,
            phase=phase,
            has_setup=True,
            name=f"essai-{phase}",
        )
        montages = [arg for arg in commande if str(tmp_path) in arg]
        assert montages, phase
        assert not any(":ro" in montage for montage in montages), (
            f"/work en lecture seule en phase {phase} : la vérification ne peut plus écrire"
        )
        assert any(arg.startswith("UV_CACHE_DIR=/work") for arg in commande), (
            "le cache d'uv est dans /work : c'est CE couplage qui interdit le :ro"
        )


def test_le_juge_n_a_pas_de_porte_hors_contrat(repo: Path) -> None:
    """Une clé `depends_on` a existé : elle lisait un rapport dans `.verify/`, dossier
    ignoré par git où l'agent écrit librement. Deux lignes de JSON inventées ouvraient la
    porte, et en CI — où `.verify/` n'existe jamais — la tâche était rejetée à tous les
    coups. Le principe du dépôt est que ce qui décide vient de la base approuvée : ce test
    est là pour qu'aucune porte ne se rouvre ailleurs."""
    assert "depends_on" not in Path(judge.__file__).read_text(encoding="utf-8")
    schema = json.loads((Path(judge.__file__).parent / "contract.schema.json").read_text())
    assert "depends_on" not in schema["properties"]
    data = {
        "id": "T-001",
        "objective": "Une clé inconnue doit être refusée, pas ignorée",
        "risk": "faible",
        "depends_on": ["T-000"],
        "allowed_paths": ["src/"],
        "criteria": [{"id": "C1", "description": "test", "check": ["true"]}],
    }
    with pytest.raises(judge.JudgeError):
        judge.parse_contract(data, "T-001")


def test_une_preuve_coupee_avant_ses_assertions_ne_vaut_rien(repo: Path, run_judge: Runner) -> None:
    """Un critère était « satisfait » dès que le processus sortait en 0 — et le code jugé,
    importé par la preuve, peut provoquer cette sortie avant la moindre assertion. Trois
    lignes suffisaient à faire déclarer vainqueur un code faux. Reproduit le 23/09/2026."""
    (repo / "src" / "metrics.py").write_text(
        "import sys\n\nsys.exit(0)\n\n\ndef accuracy(batches):\n    return 0.5\n"
    )
    code, report = run_judge(repo)
    assert code == 1
    assert [item["passed"] for item in report["criteria"]] == [False, False]


def test_une_preuve_qui_finit_par_sortir_en_zero_reste_valable(
    make_repo: Callable[..., Path], run_judge: Runner
) -> None:
    """La preuve a le droit de finir par `sys.exit(0)` : c'est SA fin. Ce qui est refusé,
    c'est une sortie venue d'ailleurs — sinon le garde-fou casserait du travail honnête."""
    repo = make_repo(
        criteria=[
            {
                "id": "C1",
                "description": "preuve qui se termine par une sortie explicite",
                "check": ["{python}", "tasks/T-001/check_c1.py"],
            }
        ]
    )
    (repo / "tasks" / "T-001" / "check_c1.py").write_text(
        "import sys\n"
        "sys.path.insert(0, '.')\n"
        "from src.metrics import accuracy\n"
        "assert abs(accuracy([(9, 10), (1, 100)]) - 10 / 110) < 1e-12\n"
        "sys.exit(0)\n"
    )
    git(repo, "switch", "-q", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "preuve")
    trace_rouge(repo)  # l'opérateur refait le contrôle rouge
    git(repo, "switch", "-q", "task/T-001")
    git(repo, "rebase", "-q", "main")
    (repo / "src" / "metrics.py").write_text(FIX)
    code, report = run_judge(repo)
    assert code == 0, report
    assert [item["passed"] for item in report["criteria"]] == [True]


def test_une_preuve_posee_a_la_racine_est_protegee(
    make_repo: Callable[..., Path], run_judge: Runner
) -> None:
    """La reconnaissance d'une preuve exigeait un « / » dans son chemin : `check_c1.py`
    à la racine n'était donc gardé par personne, et l'agent pouvait le vider."""
    repo = make_repo(
        allowed_paths=["src/", "tests/", "check_c1.py"],
        criteria=[
            {
                "id": "C1",
                "description": "preuve à la racine du dépôt",
                "check": ["{python}", "check_c1.py"],
            }
        ],
    )
    git(repo, "switch", "-q", "main")
    (repo / "check_c1.py").write_text(
        "import sys\n"
        "sys.path.insert(0, '.')\n"
        "from src.metrics import accuracy\n"
        "assert abs(accuracy([(9, 10), (1, 100)]) - 10 / 110) < 1e-12\n"
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "preuve à la racine")
    trace_rouge(repo)  # l'opérateur refait le contrôle rouge
    git(repo, "switch", "-q", "task/T-001")
    git(repo, "rebase", "-q", "main")
    (repo / "check_c1.py").write_text("# vidée par l'agent\n")
    (repo / "src" / "metrics.py").write_text(WRONG)
    code, report = run_judge(repo)
    assert code == 2
    assert any("test d'acceptation modifié" in reason for reason in report["reasons"])


def test_une_preuve_abimee_puis_remise_en_etat_est_vue(repo: Path, run_judge: Runner) -> None:
    """Le contrôle d'intégrité comparait les preuves une seule fois, à la fin. Un agent
    qui abîme une preuve, la fait passer, puis la remet d'aplomb ressortait « VÉRIFIÉ ».
    Les empreintes sont maintenant relevées avant la première étape et recomparées après
    chacune : le dégât est constaté au moment où il a lieu, la remise en état n'efface
    plus rien. Reproduit le 23 septembre 2026."""
    preuve = "tasks/T-001/check_c2.py"
    (repo / "src" / "__init__.py").write_text(
        "import atexit\n"
        "from pathlib import Path\n"
        "\n"
        f"_preuve = Path({preuve!r})\n"
        "_origine = _preuve.read_text()\n"
        "_preuve.write_text('pass\\n')\n"
        "atexit.register(lambda: _preuve.write_text(_origine))\n"
    )
    (repo / "src" / "metrics.py").write_text(FIX)
    code, report = run_judge(repo)
    assert code == 1, report["status"]
    assert report.get("tampered_proofs") == [preuve]
    # La preuve est bien revenue à son état d'origine : c'est tout l'intérêt du cas.
    assert "accuracy" in (repo / preuve).read_text()


def test_une_image_de_registre_prive_dit_quoi_faire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """« introuvable » était le seul mot rendu quand le registre refusait l'accès. En
    entreprise, l'image est privée : la cause la plus fréquente n'est pas une faute de frappe,
    c'est l'absence de connexion au registre."""
    faux = tmp_path / "bin"
    faux.mkdir()
    podman = faux / "podman"
    podman.write_text("#!/usr/bin/env bash\necho 'unauthorized: access denied' >&2\nexit 125\n")
    podman.chmod(0o755)
    monkeypatch.setenv("PATH", f"{faux}:{os.environ['PATH']}")
    logs = tmp_path / "logs"
    logs.mkdir()
    runtime = judge.Runtime("podman", "artifactory.example.com/docker/uv:python3.12")
    with pytest.raises(judge.JudgeError) as erreur:
        judge.ensure_image(runtime, logs / "pull.log")
    message = str(erreur.value)
    assert "podman login artifactory.example.com" in message, "la piste la plus probable"
    assert "EPHOR_IMAGE" in message, "et comment en désigner une autre"
    # Une image sans registre (Docker Hub) ne reçoit pas de conseil de connexion.
    with pytest.raises(judge.JudgeError) as simple:
        judge.ensure_image(judge.Runtime("podman", "python:3.12-slim"), logs / "pull2.log")
    assert "login" not in str(simple.value)


def bases_miroir() -> list[str]:
    """Les bases de l'image, prises sur un miroir quand le poste le dit : au bureau, ni
    Docker Hub ni ghcr.io ne répondent. Mêmes variables que `make ci-image`."""
    args: list[str] = []
    for variable, argument in (("EPHOR_CI_BASE", "BASE"), ("EPHOR_CI_GITLEAKS", "GITLEAKS")):
        if os.environ.get(variable):
            args += ["--build-arg", f"{argument}={os.environ[variable]}"]
    return args


@pytest.mark.container
@pytest.mark.skipif(engine() is None, reason="ni podman ni docker prêts")
def test_l_image_de_ci_porte_les_outils_qu_elle_promet() -> None:
    """La CI lance `make sec`, et `make sec` refuse d'être vert sans gitleaks. Or l'image
    publique n'a NI make NI gitleaks : cette ligne de CI n'avait jamais pu s'exécuter —
    « command not found », pas « scan absent ». Une image qui prétend porter l'outil sans
    le porter serait pire que pas d'image du tout, donc on construit et on demande."""
    moteur = engine()
    assert moteur is not None
    recette = ROOT / ".ephor" / "deploy" / "Containerfile.ci"
    epingle = re.search(r"GITLEAKS=\S+:v([0-9][0-9.]*)", recette.read_text())
    assert epingle, "la version de gitleaks doit être épinglée dans la recette"
    tag = "ephor-ci:test"
    build = subprocess.run(
        [moteur, "build", "-f", str(recette), *bases_miroir(), "-t", tag, str(recette.parent)],
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    assert build.returncode == 0, (build.stdout + build.stderr)[-2000:]
    sonde = (
        "for t in make git gitleaks python3 uv; do "
        'command -v "$t" >/dev/null || { echo "ABSENT: $t"; exit 1; }; done; gitleaks version'
    )
    dedans = subprocess.run(
        [moteur, "run", "--rm", "--entrypoint", "sh", tag, "-c", sonde],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert dedans.returncode == 0, dedans.stdout + dedans.stderr
    assert epingle.group(1) in dedans.stdout, "la version dans l'image doit être l'épinglée"


@pytest.mark.container
@pytest.mark.skipif(engine() is None, reason="ni podman ni docker prêts")
@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl absent")
def test_l_image_de_ci_reconnait_l_autorite_de_l_entreprise(tmp_path: Path) -> None:
    """Derrière un proxy qui déchiffre, ou face à un JFrog signé en interne, uv refusait le
    dépôt de paquets (« invalid peer certificate: UnknownIssuer », mesuré le 26/09/2026).
    L'autorité déposée dans autorites/ doit être reconnue par uv, Python et git, et un
    fichier qui n'est pas du PEM doit faire échouer la construction, pas passer en silence."""
    moteur = engine()
    assert moteur is not None
    contexte = tmp_path / "contexte"
    shutil.copytree(ROOT / ".ephor" / "deploy", contexte)
    ca = tmp_path / "ca"
    (ca / "simple" / "idna").mkdir(parents=True)
    (ca / "simple" / "idna" / "index.html").write_text("<html><body></body></html>")

    def openssl(*args: str) -> None:
        subprocess.run(["openssl", *args], cwd=ca, capture_output=True, check=True)

    openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out",
            "ca.crt", "-days", "1", "-subj", "/CN=Autorite de test",
            "-addext", "basicConstraints=critical,CA:TRUE",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign")  # fmt: skip
    openssl("req", "-newkey", "rsa:2048", "-nodes", "-keyout", "srv.key", "-out", "srv.csr",
            "-subj", "/CN=localhost")  # fmt: skip
    (ca / "ext.cnf").write_text(
        "subjectAltName=DNS:localhost\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n"
    )
    openssl("x509", "-req", "-in", "srv.csr", "-CA", "ca.crt", "-CAkey", "ca.key",
            "-CAcreateserial", "-out", "srv.crt", "-days", "1", "-extfile", "ext.cnf")  # fmt: skip

    def construit(tag: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                moteur,
                "build",
                "-f",
                str(contexte / "Containerfile.ci"),
                *bases_miroir(),
                "-t",
                tag,
                str(contexte),
            ],
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )

    shutil.copy(ca / "ca.crt", contexte / "autorites" / "entreprise.crt")
    build = construit("ephor-ci:autorite-test")
    assert build.returncode == 0, (build.stdout + build.stderr)[-2000:]
    # Un faux dépôt HTTPS signé par l'autorité, dans le conteneur lui-même, sans réseau.
    sonde = (
        "cd /ca && python3 -c 'import http.server, ssl; "
        's = http.server.ThreadingHTTPServer(("127.0.0.1", 8443), '
        "http.server.SimpleHTTPRequestHandler); "
        "c = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); "
        'c.load_cert_chain("srv.crt", "srv.key"); '
        "s.socket = c.wrap_socket(s.socket, server_side=True); s.serve_forever()' "
        ">/dev/null 2>&1 & "
        "for i in $(seq 50); do python3 -c 'import socket; "
        'socket.create_connection(("127.0.0.1", 8443), 0.2)\' 2>/dev/null && break; '
        "sleep 0.2; done; "
        "echo idna > /tmp/r.in; "
        "UV_HTTP_TIMEOUT=10 uv pip compile --no-cache --default-index "
        "https://localhost:8443/simple /tmp/r.in -o /tmp/r.txt 2>&1; "
        "python3 -c 'import urllib.request; "
        'print("python", urllib.request.urlopen("https://localhost:8443/simple/idna/").status)\' '
        "2>&1; git ls-remote https://localhost:8443/depot.git 2>&1; true"
    )
    dedans = subprocess.run(
        [moteur, "run", "--rm", "--network", "none", "-v", f"{ca}:/ca:ro",
         "--entrypoint", "sh", "ephor-ci:autorite-test", "-c", sonde],
        capture_output=True, text=True, timeout=300, check=False,
    )  # fmt: skip
    sortie = dedans.stdout + dedans.stderr
    assert "no versions of idna" in sortie, sortie  # uv a joint le dépôt : TLS accepté
    assert "python 200" in sortie, sortie
    assert "repository 'https://localhost:8443/depot.git/' not found" in sortie, sortie

    # Un certificat au format DER : le système l'écarterait sans bruit.
    der = subprocess.run(
        ["openssl", "x509", "-in", str(ca / "ca.crt"), "-outform", "DER"],
        capture_output=True,
        check=True,
    ).stdout
    (contexte / "autorites" / "entreprise.crt").write_bytes(der)
    refus = construit("ephor-ci:autorite-der")
    assert refus.returncode != 0
    assert "autorité écartée" in refus.stdout + refus.stderr
    # Un « ca.pem » tel que l'IT le livre : update-ca-certificates ne lit que les .crt.
    (contexte / "autorites" / "entreprise.crt").unlink()
    shutil.copy(ca / "ca.crt", contexte / "autorites" / "ca.pem")
    ignore = construit("ephor-ci:autorite-pem")
    assert ignore.returncode != 0
    assert "extension .crt attendue" in ignore.stdout + ignore.stderr


# ------------------------------------------------ des preuves qui vont au bout (ADR 0005)


def ajoute_sur_la_base(repo: Path, fichiers: dict[str, str]) -> None:
    """Pose des preuves sur la branche principale, puis ramène la tâche dessus."""
    git(repo, "switch", "-q", "main")
    for nom, contenu in fichiers.items():
        cible = repo / nom
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_text(contenu)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "preuves approuvées")
    trace_rouge(repo)  # l'opérateur refait le contrôle rouge
    git(repo, "switch", "-q", "task/T-001")
    git(repo, "rebase", "-q", "main")


PYTEST_C1 = [
    "{python}",
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:cacheprovider",
    "-p",
    "no:randomly",
    "tasks/T-001/test_t.py::test_c1",
]
TEST_T = (
    "import sys\n"
    "sys.path.insert(0, '.')\n"
    "from src.metrics import accuracy\n"
    "\n\n"
    "def test_c1():\n"
    "    assert abs(accuracy([(9, 10), (1, 100)]) - 10 / 110) < 1e-12\n"
    "\n\n"
    "def test_trivial():\n"
    "    assert True\n"
)


def pytest_repo(
    make_repo: Callable[..., Path], check: list[str] | None = None, **extra: Any
) -> Path:
    repo = make_repo(
        criteria=[{"id": "C1", "description": "preuve pytest", "check": check or PYTEST_C1}],
        **extra,
    )
    ajoute_sur_la_base(repo, {"tasks/T-001/test_t.py": TEST_T})
    return repo


def test_une_preuve_pytest_honnete_est_verifiee(
    make_repo: Callable[..., Path], run_judge: Runner
) -> None:
    repo = pytest_repo(make_repo)
    (repo / "src" / "metrics.py").write_text(FIX)
    code, report = run_judge(repo)
    assert code == 0, report
    assert report["criteria"][0]["proof"] == "rapport de tests"
    latest = repo / ".verify" / "judge" / "T-001" / "latest" / "logs"
    assert (latest / "C1.junit.xml").is_file(), "le rapport de tests est gardé avec le verdict"


def test_une_sortie_anticipee_ne_trompe_plus_une_preuve_pytest(
    make_repo: Callable[..., Path], run_judge: Runner
) -> None:
    """LE défaut reproduit le 24/09/2026 : sous pytest, une ligne `os._exit(0)`
    ajoutée au code faisait dire « VÉRIFIÉ » à un code jamais corrigé — le juge ne croyait
    que le code de sortie. Il exige maintenant un rapport de tests, et il n'y en a pas."""
    repo = pytest_repo(make_repo)
    (repo / "src" / "metrics.py").write_text(WRONG + '\n\n__import__("os")._exit(0)\n')
    code, report = run_judge(repo)
    assert code == 1, report["status"]
    assert report["criteria"][0]["passed"] is False
    assert "aucun rapport" in report["criteria"][0]["reason"]


def test_un_test_saute_par_le_code_juge_n_est_pas_une_reussite(
    make_repo: Callable[..., Path], run_judge: Runner
) -> None:
    """Le code jugé fait sauter le test qui le gêne ; l'autre passe, pytest sort en 0."""
    repo = pytest_repo(make_repo, check=[*PYTEST_C1[:-1], "tasks/T-001/test_t.py"])
    (repo / "src" / "metrics.py").write_text(
        "def accuracy(batches):\n    import pytest\n\n    pytest.skip('plus tard')\n"
    )
    code, report = run_judge(repo)
    assert code == 1
    assert "sauté" in report["criteria"][0]["reason"]


def test_le_rapport_est_demande_par_le_juge_pas_par_le_contrat() -> None:

    data = contract(
        criteria=[
            {
                "id": "C1",
                "description": "rapport imposé",
                "check": [*PYTEST_C1, "--junitxml=/tmp/r.xml"],
            }
        ]
    )
    with pytest.raises(judge.JudgeError, match="rapport de tests"):
        judge.parse_contract(data)


RAPPORT_OK = (
    '<testsuites><testsuite tests="3" failures="0" errors="0" skipped="0">'
    '<testcase classname="tasks.T-001.test_t" name="test_a[]"/>'
    '<testcase classname="tasks.T-001.test_t" name="test_a[x]"/>'
    '<testcase classname="tasks.T-001.test_t.TestB" name="test_c"/>'
    "</testsuite></testsuites>"
)


@pytest.mark.parametrize(
    ("contenu", "attendus", "raison"),
    [
        (RAPPORT_OK, ["tasks/T-001/test_t.py::test_a"], None),
        (RAPPORT_OK, ["tasks/T-001/test_t.py::TestB::test_c"], None),
        (RAPPORT_OK, ["tasks/T-001/test_t.py"], None),
        (RAPPORT_OK, ["tasks/T-001/test_t.py::test_absent"], "absent"),
        (RAPPORT_OK, ["tasks/T-001/autre.py"], "absent"),
        (RAPPORT_OK.replace('tests="3"', 'tests="4"'), [], "compteurs"),
        (RAPPORT_OK[:-30], [], "tronqué"),
        ('<!DOCTYPE x [<!ENTITY a "b">]>' + RAPPORT_OK, [], "DOCTYPE"),
        ('<testsuite tests="0"></testsuite>', [], "aucun test"),
        (
            '<testsuite tests="1" skipped="1"><testcase classname="t" name="x">'
            "<skipped/></testcase></testsuite>",
            [],
            "sauté",
        ),
        # pytest écrit depuis SON dossier racine : un nom plus court désigne le même test.
        (RAPPORT_OK.replace("tasks.T-001.", ""), ["tasks/T-001/test_t.py::test_a"], None),
    ],
)
def test_le_rapport_de_tests_est_lu_en_entier(
    tmp_path: Path, contenu: str, attendus: list[str], raison: str | None
) -> None:
    rapport = tmp_path / "r.xml"
    rapport.write_text(contenu)
    trouve = judge.verifier_rapport_pytest(rapport, judge.tests_attendus(attendus))
    if raison is None:
        assert trouve is None
    else:
        assert trouve is not None and raison in trouve
    assert judge.verifier_rapport_pytest(tmp_path / "absent.xml", []) is not None


@pytest.mark.parametrize(
    ("check", "forme"),
    [
        (["{python}", "tasks/T-001/c.py"], "jeton"),
        (["uv", "run", "python", "tasks/T-001/c.py"], "jeton"),
        (["uv", "run", "pytest", "-q", "tasks/T-001/t.py::test_x"], "rapport"),
        (["{python}", "-m", "pytest", "tasks/T-001/t.py"], "rapport"),
        (["pytest"], "rapport"),
        (["bash", ".ephor/scripts/lance.sh", "pytest"], "code"),
        (["npm", "test"], "code"),
    ],
)
def test_chaque_critere_a_une_forme_de_preuve(check: list[str], forme: str) -> None:
    assert judge.forme_de_preuve(judge.Criterion("C1", "x", tuple(check))) == forme


def test_une_sortie_attendue_est_comparee_de_l_exterieur(
    make_repo: Callable[..., Path], run_judge: Runner
) -> None:
    """Le critère « processus » : le juge donne l'entrée et compare la sortie
    exacte. Le code jugé ne peut ni sortir en avance, ni se déclarer satisfait."""
    lire = (
        "import json, sys; sys.path.insert(0, '.'); from src.metrics import accuracy; "
        "print(accuracy(json.load(sys.stdin)))"
    )
    repo = make_repo(
        criteria=[
            {
                "id": "C1",
                "description": "lots de tailles différentes",
                "check": ["{python}", "-c", lire],
                "stdin": "[[9, 10], [1, 100]]",
                "expected_output": "0.09090909090909091\n",
            }
        ]
    )
    (repo / "src" / "metrics.py").write_text(FIX)
    code, report = run_judge(repo)
    assert code == 0, report
    assert report["criteria"][0]["proof"] == "sortie exacte"
    (repo / "src" / "metrics.py").write_text(WRONG)
    code, report = run_judge(repo)
    assert code == 1
    assert "sortie différente" in report["criteria"][0]["reason"]
    (repo / "src" / "metrics.py").write_text(WRONG + '\n\n__import__("os")._exit(0)\n')
    code, report = run_judge(repo)
    assert code == 1


@pytest.mark.parametrize(
    ("critere", "refuse"),
    [
        ({"check": ["true"]}, True),
        ({"check": ["bash", ".ephor/scripts/verif.sh"]}, True),
        ({"check": ["{python}", "tasks/T-001/check_c1.py"]}, False),
        ({"check": PYTEST_C1}, False),
        ({"check": ["./outil"], "expected_output": "ok\n"}, False),
    ],
)
def test_un_risque_eleve_exige_une_preuve_qui_va_au_bout(
    critere: dict[str, Any], refuse: bool
) -> None:

    data = contract(risk="eleve", criteria=[{"id": "C1", "description": "critère", **critere}])
    if refuse:
        with pytest.raises(judge.JudgeError, match="risque élevé"):
            judge.parse_contract(data)
    else:
        judge.parse_contract(data)


@pytest.mark.parametrize(
    ("champs", "message"),
    [
        ({"returncode": 1}, "returncode ne sert"),
        ({"expected_output": 3}, "expected_output doit"),
        ({"stdin": ["x"]}, "stdin doit"),
        ({"expected_output": "", "returncode": 300}, "entre 0 et 255"),
    ],
)
def test_les_champs_de_sortie_attendue_sont_controles(champs: dict[str, Any], message: str) -> None:

    data = contract(
        criteria=[{"id": "C1", "description": "critère", "check": ["./outil"], **champs}]
    )
    with pytest.raises(judge.JudgeError) as refus:
        judge.parse_contract(data)
    assert any(message in item for item in refus.value.messages)


def test_un_contrat_a_cle_double_est_refuse(tmp_path: Path) -> None:
    """`json` garde la dernière clé en silence : le relecteur approuve ce qu'il lit en
    premier, le juge appliquerait autre chose."""
    fichier = tmp_path / "contract.json"
    fichier.write_text('{"id": "T-001", "risk": "eleve", "risk": "faible"}')
    with pytest.raises(judge.JudgeError, match="clé en double"):
        judge.load_contract(fichier)


def test_le_juge_n_ecrit_rien_dans_ton_depot_git(
    make_repo: Callable[..., Path], run_judge: Runner
) -> None:
    """La copie était un `git worktree` : elle partageait la configuration du dépôt, et un
    test qui lançait `git config` écrivait dans TON dépôt. C'est maintenant un clone à part."""
    repo = make_repo(
        verify=[
            "{python}",
            "-c",
            "import subprocess; "
            "subprocess.run(['git', 'config', 'user.name', 'pirate'], check=True)",
        ]
    )
    (repo / "src" / "metrics.py").write_text(FIX)
    code, report = run_judge(repo)
    assert code == 0, report
    assert git(repo, "config", "user.name").strip() == "Ephor test"
    assert not (repo / ".git" / "worktrees").exists()


def test_un_processus_laisse_en_arriere_plan_est_arrete(
    make_repo: Callable[..., Path], run_judge: Runner, tmp_path: Path
) -> None:
    """Un enfant lancé en arrière-plan survivait à son étape : il pouvait écrire dans la
    copie APRÈS le relevé des preuves. Le juge arrête maintenant tout le groupe."""
    temoin = tmp_path / "encore-vivant"
    enfant = f"import time, pathlib; time.sleep(1.5); pathlib.Path({str(temoin)!r}).write_text('x')"
    repo = make_repo(
        verify=[
            "{python}",
            "-c",
            f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', {enfant!r}])",
        ]
    )
    (repo / "src" / "metrics.py").write_text(FIX)
    code, _ = run_judge(repo)
    assert code == 0
    time.sleep(2.5)
    assert not temoin.exists()


def test_une_sortie_sans_fin_est_coupee(
    make_repo: Callable[..., Path], run_judge: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(judge, "MAX_LOG_BYTES", 50_000)
    repo = make_repo(verify=["{python}", "-c", "print('x' * 2_000_000)"])
    (repo / "src" / "metrics.py").write_text(FIX)
    code, report = run_judge(repo)
    assert code == 1 and report["status"] == "verification_failed"
    etape = next(step for step in report["steps"] if step["name"] == "verify")
    assert etape["output_limit"] is True
    journal = repo / ".verify" / "judge" / "T-001" / "latest" / "logs" / "verify.log"
    assert journal.stat().st_size <= 50_000


def test_un_silencieux_ajoute_est_nomme_au_verdict(
    make_repo: Callable[..., Path], run_judge: Runner
) -> None:
    repo = make_repo()
    (repo / "src" / "metrics.py").write_text(FIX.replace("None\n", "None  # noqa: E501\n"))
    code, report = run_judge(repo)
    assert code == 0
    assert report["silencers_added"] == ["src/metrics.py:4 noqa"]
    resume = (repo / ".verify" / "judge" / "T-001" / "latest" / "summary.txt").read_text()
    assert "silencieux" in resume


def test_un_silencieux_sur_risque_eleve_est_refuse(tmp_path: Path, run_judge: Runner) -> None:
    from conftest import build_repo

    repo = build_repo(tmp_path / "eleve", contract(risk="eleve"))
    (repo / "src" / "metrics.py").write_text(FIX.replace("None\n", "None  # type: ignore\n"))
    code, report = run_judge(repo)
    assert code == 2
    assert any("silencieux" in raison for raison in report["reasons"])
    assert report["steps"] == []


def test_les_silencieux_sont_reperes_dans_le_patch() -> None:
    patch = (
        b"diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n"
        b"@@ -1,2 +1,3 @@\n x = 1\n+y = f()  # type: ignore[call]\n z = 3\n"
        b"diff --git a/tests/t.py b/tests/t.py\n--- /dev/null\n+++ b/tests/t.py\n"
        b"@@ -0,0 +1,2 @@\n+@pytest.mark.skip\n+def test_x(): pass\n"
        b"diff --git a/PROGRESS.md b/PROGRESS.md\n--- a/PROGRESS.md\n+++ b/PROGRESS.md\n"
        b"@@ -1 +1,2 @@\n # PROGRESS\n+- retire le # noqa\n"
    )
    assert judge.silencieux_ajoutes(patch) == ["src/a.py:2 type: ignore", "tests/t.py:1 skip"]


def test_le_rapport_lie_chaque_fichier_juge_a_son_empreinte(repo: Path, run_judge: Runner) -> None:
    (repo / "src" / "metrics.py").write_text(FIX)
    code, report = run_judge(repo)
    assert code == 0
    assert report["files_sha256"] == {"src/metrics.py": judge.sha256(FIX.encode())}


def test_le_conteneur_est_durci_par_defaut(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Aucun privilège, image en lecture seule, aucune
    image téléchargée en cours de route. Débrayable pour une image de la maison."""
    runtime = judge.Runtime(kind="docker", image="img@sha256:" + "a" * 64)
    commande, _ = judge.container_command(
        runtime, ["pytest"], tmp_path, "verify", True, "n", preuves=tmp_path / "p", entree=True
    )
    for option in ("--cap-drop=ALL", "--read-only", "--pull=never", "-i"):
        assert option in commande, option
    assert f"{tmp_path / 'p'}:{judge.PREUVES_CONTENEUR}" in commande
    monkeypatch.setenv("EPHOR_CONTAINER_HARDENING", "0")
    commande, _ = judge.container_command(runtime, ["pytest"], tmp_path, "verify", True, "n")
    assert "--cap-drop=ALL" not in commande and "-i" not in commande


def test_sous_podman_linux_le_conteneur_prend_l_identite_du_poste(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mesuré dans une Fedora 44, SELinux actif, Podman sans droits root : une image qui
    déclare son propre utilisateur ne pouvait pas écrire dans la copie jugée, et l'agent
    honnête tombait en ÉCHEC dès l'installation. Sur Mac, rien de tout ça ne se voyait."""
    monkeypatch.setattr(judge.sys, "platform", "linux")
    monkeypatch.setattr(judge.os, "getuid", lambda: 1234)
    monkeypatch.setattr(judge.os, "getgid", lambda: 5678)
    monkeypatch.delenv("EPHOR_CONTAINER_ARGS", raising=False)
    monkeypatch.delenv("EPHOR_VOLUME_SUFFIX", raising=False)

    def commande(kind: str) -> list[str]:
        runtime = judge.Runtime(kind=kind, image="img@sha256:" + "a" * 64)
        return judge.container_command(runtime, ["pytest"], tmp_path, "verify", True, "n")[0]

    podman = commande("podman")
    assert "--userns=keep-id" in podman and "--user=1234:5678" in podman
    assert f"{tmp_path}:/work:Z" in podman, "SELinux : sans l'étiquette, Permission denied"
    assert "--userns=keep-id" not in commande("docker"), "Docker sous Linux : pas mesuré"

    # L'opérateur qui règle déjà l'identité garde la main ; et Podman refuse keep-id à
    # côté d'un --uidmap ou d'un --pod.
    for option in ("--userns=host", "--user=0", "--uidmap=0:1:1000", "--pod=new:p1"):
        monkeypatch.setenv("EPHOR_CONTAINER_ARGS", option)
        reglee = commande("podman")
        assert "--userns=keep-id" not in reglee and option in reglee, option

    monkeypatch.delenv("EPHOR_CONTAINER_ARGS")
    monkeypatch.setattr(judge.os, "getuid", lambda: 0)
    assert "--userns=keep-id" not in commande("podman"), "Podman lancé par root : rien à traduire"
    monkeypatch.setattr(judge.sys, "platform", "darwin")
    monkeypatch.setattr(judge.os, "getuid", lambda: 1234)
    assert "--userns=keep-id" not in commande("podman"), "sur Mac, la machine virtuelle s'en charge"


def test_une_seule_liste_de_zones_dans_le_moteur() -> None:
    """Cinq listes écrites à la main avaient divergé (25/09/2026) : les deux CI oubliaient
    `.pre-commit-config.yaml`, et aucune ne connaissait `.git/` ni `GNUmakefile`. La liste
    vit dans `.ephor/zones.txt` ; le juge la lit, les CI appellent le moteur."""
    assert judge.lire_zones() == judge.PROTECTED
    for zone in (".git/", "GNUmakefile", "makefile", ".mcp.json", ".pre-commit-config.yaml"):
        assert zone in judge.PROTECTED, zone
    assert not any(zone.startswith(("~", "!")) for zone in judge.PROTECTED)
    for nom in (".gitlab-ci.yml", ".github/workflows/pre-queue.yml"):
        texte = (ROOT / nom).read_text(encoding="utf-8")
        assert re.search(r'garde-de-base/\.ephor/judge\.py"? garde', texte), nom
        assert "Makefile|AGENTS" not in texte, f"{nom} recopie encore une liste de zones"


def test_sans_liste_de_zones_le_juge_ne_juge_pas(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une liste vide ne protégerait rien : le juge refuse plutôt que de tout laisser passer."""
    monkeypatch.setattr(judge, "PROTECTED", ())
    with pytest.raises(judge.JudgeError, match="zones de contrôle introuvable"):
        judge.is_protected("Makefile")
    assert judge.lire_zones(Path("/nulle/part/zones.txt")) == ()


@pytest.mark.parametrize("chemin", ["GNUmakefile", "makefile", ".mcp.json", "CLAUDE.local.md"])
def test_un_contrat_ne_peut_pas_ouvrir_une_zone_du_moteur(chemin: str) -> None:
    """GNU make lit GNUmakefile avant Makefile : un agent qui l'écrivait faisait afficher un
    faux « VÉRIFIÉ » à `make judge` (mesuré le 25/09/2026)."""
    with pytest.raises(judge.JudgeError, match="zone de contrôle"):
        judge.parse_contract(contract(allowed_paths=[chemin]))


def test_la_garde_de_la_ci_nomme_zones_et_silencieux(repo: Path, tmp_path: Path) -> None:
    (repo / "src" / "metrics.py").write_text(FIX)
    git(repo, "commit", "-qam", "correction")
    assert judge.main(["garde", "--repo", str(repo), "--base", "main"]) == 0
    (repo / "GNUmakefile").write_text("judge:\n\t@echo VÉRIFIÉ\n")
    (repo / "src" / "metrics.py").write_text(FIX + "x = 1  # noqa\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "détour")
    sortie = subprocess.run(
        [sys.executable, str(EPHOR / "judge.py"), "garde", "--repo", str(repo), "--base", "main"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert sortie.returncode == 3
    assert "zone de contrôle modifiée : GNUmakefile" in sortie.stdout
    assert "silencieux ajouté : src/metrics.py" in sortie.stdout


def test_l_empreinte_du_juge_couvre_ses_zones(tmp_path: Path) -> None:
    """Retirer une zone change le juge : un verdict rendu avant ne vaut plus pour la livraison."""
    moteur = tmp_path / ".ephor"
    moteur.mkdir()
    shutil.copy(EPHOR / "judge.py", moteur)
    (moteur / "zones.txt").write_text("Makefile\n.ephor/\n")
    code = "import sys; sys.path.insert(0, sys.argv[1]); import judge; print(judge.engine_hash())"
    empreinte = [sys.executable, "-c", code, str(moteur)]
    avant = subprocess.run(empreinte, capture_output=True, text=True, check=True)
    (moteur / "zones.txt").write_text(".ephor/\n")
    apres = subprocess.run(empreinte, capture_output=True, text=True, check=True)
    assert avant.stdout.strip() and avant.stdout != apres.stdout


def test_le_juge_nomme_ce_qui_s_execute_sans_etre_appele(
    repo: Path, run_judge: Callable[..., tuple[int, dict[str, Any]]]
) -> None:
    """`src/` est sur le chemin de Python : un `src/sitecustomize.py` tourne avant chaque
    preuve (mesuré le 25/09/2026). Le juge ne sait pas ce qu'il fait ; il le nomme."""
    (repo / "src" / "metrics.py").write_text(FIX)
    (repo / "src" / "sitecustomize.py").write_text("")
    code, report = run_judge(repo)
    assert code == 0
    assert report["autorun_files"] == ["src/sitecustomize.py"]
    resume = (repo / ".verify" / "judge" / "T-001" / "latest" / "summary.txt").read_text()
    assert "sans être appelé" in resume and "src/sitecustomize.py" in resume


def test_un_risque_eleve_refuse_ce_qui_s_execute_au_demarrage(
    make_repo: Callable[..., Path], run_judge: Callable[..., tuple[int, dict[str, Any]]]
) -> None:
    repo = make_repo(risk="eleve")
    (repo / "src" / "metrics.py").write_text(FIX)
    (repo / "src" / "piege.pth").write_text("import os\n")
    code, report = run_judge(repo)
    assert code == 2 and report["status"] == "rejected"
    assert any("démarrage de Python" in raison for raison in report["reasons"])


@pytest.mark.parametrize(
    ("allowed", "check"),
    [
        (["src/", "tests/"], ["{python}", "-m", "pytest", "-q", "tests/"]),
        (["src/", "tests/unit/"], ["{python}", "-m", "pytest", "-q", "tests"]),
        (["src/"], ["{python}", "-m", "pytest", "-q", "."]),
    ],
)
def test_un_dossier_de_preuve_ouvert_a_l_agent_est_refuse(
    make_repo: Callable[..., Path], run_judge: Runner, allowed: list[str], check: list[str]
) -> None:
    """`pytest tests/` avec `tests/` dans le périmètre : l'agent réécrivait le test
    d'acceptation rangé là, et un code jamais corrigé était VÉRIFIÉ (reproduit le 25/09/2026)."""
    repo = make_repo(
        allowed_paths=allowed,
        criteria=[{"id": "C1", "description": "la suite passe", "check": check}],
    )
    (repo / "tests" / "test_smoke.py").write_text("def test_ok():\n    assert True\n")
    code, report = run_judge(repo)
    assert code == 2 and report["steps"] == []
    assert any("preuve dans le périmètre de l'agent" in r and "C1" in r for r in report["reasons"])


def test_un_dossier_de_preuve_hors_du_perimetre_reste_permis(repo: Path) -> None:
    fermes = judge.parse_contract(
        contract(
            allowed_paths=["src/"],
            criteria=[
                {"id": "C1", "description": "la suite", "check": ["uv", "run", "pytest", "tests/"]}
            ],
        )
    )
    assert judge.cited_dirs(repo, "main", fermes) == [("C1", "tests")]
    assert judge.open_dirs(repo, "main", fermes) == []


def test_pytest_sans_fichier_designe_est_nomme(
    make_repo: Callable[..., Path], run_judge: Runner
) -> None:
    repo = make_repo(
        allowed_paths=["src/"],
        criteria=[
            {
                "id": "C1",
                "description": "ce que pytest collecte",
                "check": ["{python}", "-m", "pytest", "-q", "-p", "no:randomly"],
            }
        ],
    )
    (repo / "src" / "metrics.py").write_text(FIX)
    run_judge(repo)
    resume = (repo / ".verify" / "judge" / "T-001" / "latest" / "summary.txt").read_text()
    assert "pytest choisit lui-même ce qu'il lance : C1" in resume


def test_un_silencieux_dans_un_nom_accentue_ou_espace_est_nomme(
    repo: Path, run_judge: Runner
) -> None:
    """git écrit `"b/src/mod\\303\\250le.py"` : lu tel quel, ce fichier n'était à personne."""
    (repo / "src" / "metrics.py").write_text(FIX)
    (repo / "src" / "modèle.py").write_text("x = 1  # noqa\n")
    (repo / "src" / "a b.py").write_text("y = 2  # type: ignore\n")
    code, report = run_judge(repo)
    assert code == 0
    assert sorted(report["silencers_added"]) == [
        "src/a b.py:1 type: ignore",
        "src/modèle.py:1 noqa",
    ]


def test_un_nom_de_git_est_rendu_tel_qu_il_est_sur_le_disque() -> None:
    assert judge.nom_git('"b/src/mod\\303\\250le.py"') == "b/src/modèle.py"
    assert judge.nom_git('"b/q\\"t\\\\u.py"') == 'b/q"t\\u.py'
    assert judge.nom_git("b/a b.py\t") == "b/a b.py"
    assert judge.nom_git("b/simple.py") == "b/simple.py"


def test_un_reglage_des_controles_est_nomme_puis_refuse_sur_risque_eleve(
    make_repo: Callable[..., Path], run_judge: Runner, tmp_path: Path
) -> None:
    """Un `ruff.toml` posé dans un sous-dossier éteint le lint de ce dossier sans un seul
    `noqa` (mesuré le 25/09/2026)."""
    repo = make_repo()
    (repo / "src" / "metrics.py").write_text(FIX)
    (repo / "src" / "ruff.toml").write_text("[lint]\nselect = []\n")
    code, report = run_judge(repo)
    assert code == 0 and report["control_config_files"] == ["src/ruff.toml"]
    resume = (repo / ".verify" / "judge" / "T-001" / "latest" / "summary.txt").read_text()
    assert "réglage(s) des contrôles modifié(s)" in resume

    from conftest import build_repo

    eleve = build_repo(tmp_path / "eleve", contract(risk="eleve"))
    (eleve / "src" / "metrics.py").write_text(FIX)
    (eleve / "src" / "ruff.toml").write_text("[lint]\nselect = []\n")
    code, report = run_judge(eleve)
    assert code == 2 and report["steps"] == []
    assert any("réglage des contrôles" in raison for raison in report["reasons"])


def test_les_porteurs_de_dependances_sont_nommes_jamais_refuses() -> None:
    reglages, porteurs = judge.reglages_des_controles(
        ["pyproject.toml", "web/package.json", "web/.eslintrc.json", "src/app/x.py"]
    )
    assert reglages == ["web/.eslintrc.json"]
    assert porteurs == ["pyproject.toml", "web/package.json"]


def test_la_garde_de_la_ci_nomme_les_reglages_des_controles(repo: Path) -> None:
    (repo / "src" / "metrics.py").write_text(FIX)
    (repo / "src" / ".ruff.toml").write_text("[lint]\nselect = []\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "lint éteint")
    sortie = subprocess.run(
        [sys.executable, str(EPHOR / "judge.py"), "garde", "--repo", str(repo), "--base", "main"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert sortie.returncode == 3
    assert "réglage des contrôles modifié : src/.ruff.toml" in sortie.stdout


def test_un_contrat_hors_de_tasks_n_est_pas_juge(repo: Path, tmp_path: Path) -> None:
    ailleurs = repo / "docs" / "T-001"
    ailleurs.mkdir(parents=True)
    shutil.copy(repo / "tasks" / "T-001" / "contract.json", ailleurs / "contract.json")
    with pytest.raises(judge.JudgeError, match="sous tasks/"):
        judge.resolve_task(repo, None, str(ailleurs / "contract.json"))
    chemin, ident = judge.resolve_task(repo, None, str(repo / "tasks" / "T-001" / "contract.json"))
    assert ident == "T-001" and chemin.name == "contract.json"


def _acceptation_en_dossier(make_repo: Callable[..., Path]) -> Path:
    """Un critère qui cite le dossier de la tâche, et le test d'acceptation qui y vit."""
    repo = make_repo(
        criteria=[
            {
                "id": "C1",
                "description": "les tests d'acceptation du dossier",
                "check": ["{python}", "-m", "pytest", "-q", "-p", "no:randomly", "tasks/T-001/"],
            }
        ]
    )
    git(repo, "switch", "-q", "main")
    (repo / "tasks" / "T-001" / "test_acc.py").write_text(
        "import sys\nsys.path.insert(0, '.')\nfrom src.metrics import accuracy\n\n\n"
        "def test_acc():\n    assert abs(accuracy([(9, 10), (1, 100)]) - 10 / 110) < 1e-12\n"
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "acceptation")
    trace_rouge(repo)  # l'opérateur refait le contrôle rouge
    git(repo, "switch", "-q", "task/T-001")
    git(repo, "rebase", "-q", "main")
    return repo


def test_le_dossier_de_preuve_reecrit_pendant_la_verification_est_vu(
    make_repo: Callable[..., Path], run_judge: Runner
) -> None:
    """Un test de l'agent, lancé par la vérification, réécrivait le test d'acceptation rangé
    dans le dossier cité : le code faux ressortait VÉRIFIÉ (reproduit le 25/09/2026)."""
    repo = _acceptation_en_dossier(make_repo)
    (repo / "src" / "metrics.py").write_text(WRONG)
    (repo / "tests" / "test_sabotage.py").write_text(
        "from pathlib import Path\n"
        "Path('tasks/T-001/test_acc.py').write_text('def test_acc():\\n    assert True\\n')\n"
    )
    code, report = run_judge(repo)
    assert code == 1 and report["status"] == "verification_failed"
    assert "tasks/T-001/test_acc.py" in report["tampered_proofs"]


def test_un_conftest_apparu_pendant_la_verification_est_vu(
    make_repo: Callable[..., Path], run_judge: Runner
) -> None:
    repo = _acceptation_en_dossier(make_repo)
    (repo / "src" / "metrics.py").write_text(WRONG)
    (repo / "tests" / "test_sabotage.py").write_text(
        "from pathlib import Path\n"
        "Path('conftest.py').write_text('collect_ignore_glob = [\"tasks/*\"]\\n')\n"
    )
    code, report = run_judge(repo)
    assert code == 1
    assert "conftest.py" in report["tampered_proofs"]


def test_le_code_juge_est_celui_qui_sera_livre(repo: Path, run_judge: Runner) -> None:
    """Le correctif livre un code faux, et un test réécrit le vrai à sa place le temps du
    jugement : le manifeste aurait attesté un code que personne n'a vu passer les critères."""
    (repo / "src" / "metrics.py").write_text(WRONG)
    (repo / "tests" / "test_substitution.py").write_text(
        f"from pathlib import Path\nPath('src/metrics.py').write_text({FIX!r})\n"
    )
    code, report = run_judge(repo)
    assert code == 1
    assert "src/metrics.py" in report["tampered_proofs"]


def test_ce_que_l_installation_change_fait_partie_du_juge(
    make_repo: Callable[..., Path], run_judge: Runner
) -> None:
    """Un verrou de dépendances réécrit par `uv sync`, c'est l'installation du contrat."""
    repo = make_repo(
        setup=["{python}", "-c", "open('verrou.lock', 'a').write('x\\n')"],
    )
    git(repo, "switch", "-q", "main")
    (repo / "verrou.lock").write_text("v1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "verrou")
    trace_rouge(repo)  # l'opérateur refait le contrôle rouge
    git(repo, "switch", "-q", "task/T-001")
    git(repo, "rebase", "-q", "main")
    (repo / "src" / "metrics.py").write_text(FIX)
    code, report = run_judge(repo)
    assert code == 0, report.get("tampered_proofs")
