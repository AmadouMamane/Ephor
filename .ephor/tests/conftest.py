"""Fixtures des tests d'Ephor : dépôts Git jetables, sans réseau ni dépendance."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

EPHOR = Path(__file__).resolve().parents[1]
ROOT = EPHOR.parent
sys.path.insert(0, str(EPHOR))

import judge  # noqa: E402

DEFECT = "def accuracy(batches):\n    return sum(c / t for c, t in batches) / len(batches)\n"
FIX = (
    "def accuracy(batches):\n"
    "    correct = sum(c for c, _ in batches)\n"
    "    total = sum(t for _, t in batches)\n"
    "    return correct / total if total else None\n"
)
WRONG = "def accuracy(batches):\n    return 0.5\n"
CHECK_C1 = (
    "import sys\n"
    "sys.path.insert(0, '.')\n"
    "from src.metrics import accuracy\n"
    "assert abs(accuracy([(9, 10), (1, 100)]) - 10 / 110) < 1e-12\n"
)
CHECK_C2 = (
    "import sys\n"
    "sys.path.insert(0, '.')\n"
    "from src.metrics import accuracy\n"
    "assert accuracy([]) is None\n"
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def contract(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "T-001",
        "objective": "Calculer une micro-accuracy sur des lots de tailles différentes.",
        "risk": "faible",
        "allowed_paths": ["src/", "tests/"],
        "criteria": [
            {
                "id": "C1",
                "description": "lots de tailles différentes",
                "check": ["{python}", "tasks/T-001/check_c1.py"],
            },
            {
                "id": "C2",
                "description": "liste vide donne None",
                "check": ["{python}", "tasks/T-001/check_c2.py"],
            },
        ],
        "verify": ["{python}", "-m", "unittest", "discover", "-s", "tests", "-q"],
        "timeout_seconds": 60,
    }
    data.update(changes)
    return data


def build_repo(path: Path, contract_data: dict[str, Any]) -> Path:
    (path / "src").mkdir(parents=True)
    (path / "src" / "__init__.py").write_text("")
    (path / "src" / "metrics.py").write_text(DEFECT)
    (path / "tests").mkdir()
    (path / "tests" / "__init__.py").write_text("")
    (path / "tests" / "test_smoke.py").write_text(
        "import unittest\n\n\nclass Smoke(unittest.TestCase):\n"
        "    def test_ok(self):\n        self.assertTrue(True)\n"
    )
    task = path / "tasks" / "T-001"
    task.mkdir(parents=True)
    (task / "contract.json").write_text(json.dumps(contract_data, indent=2) + "\n")
    (task / "check_c1.py").write_text(CHECK_C1)
    (task / "check_c2.py").write_text(CHECK_C2)
    (path / ".gitignore").write_text(".verify/\n__pycache__/\n")
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "user.name", "Ephor test")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "base approuvée : contrat et tests d'acceptation")
    trace_rouge(path)
    git(path, "switch", "-qc", "task/T-001")
    return path


def trace_rouge(repo: Path, task_id: str = "T-001") -> None:
    """Le contrôle rouge, écrit comme `make task-check` l'écrirait, puis commité sur la branche
    courante. Les critères ne sont pas relancés : le code de départ (DEFECT) les fait tous
    échouer, et les tests de `make task-check` le vérifient pour de vrai. Un contrat
    invalide n'en reçoit pas : le juge le refuse avant de chercher la trace."""
    try:
        contrat = judge.load_contract(repo / "tasks" / task_id / "contract.json", task_id)
    except judge.JudgeError:
        return
    trace = judge.trace_rouge(
        task_id,
        git(repo, "rev-parse", "HEAD").strip(),
        judge.preuves_rouges(repo, "HEAD", contrat, task_id),
        [{"id": c.id, "resultat": "rouge", "raison": "code de départ"} for c in contrat.criteria],
    )
    (repo / "tasks" / task_id / judge.ROUGE).write_text(json.dumps(trace, indent=2) + "\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "contrôle rouge")


@pytest.fixture
def make_repo(tmp_path: Path) -> Callable[..., Path]:
    def factory(**changes: Any) -> Path:
        return build_repo(tmp_path / "repo", contract(**changes))

    return factory


@pytest.fixture
def repo(make_repo: Callable[..., Path]) -> Path:
    return make_repo()


@pytest.fixture
def run_judge(tmp_path: Path) -> Callable[..., tuple[int, dict[str, Any]]]:
    def runner(repo: Path, *extra: str) -> tuple[int, dict[str, Any]]:
        code = judge.main(
            [
                "check",
                "--task-id",
                "T-001",
                "--repo",
                str(repo),
                "--workdir",
                str(tmp_path / "work"),
                *extra,
            ]
        )
        latest = repo / ".verify" / "judge" / "T-001" / "latest"
        return code, json.loads((latest / "report.json").read_text())

    return runner


def depot_node(cible: Path) -> None:
    """Un dépôt vivant : son Makefile, ses réglages Claude, sa pile qui n'est pas Python."""
    (cible / "src").mkdir(parents=True)
    (cible / ".claude").mkdir()
    (cible / "package.json").write_text('{"name":"app","scripts":{"test":"jest"}}\n')
    (cible / "Makefile").write_text("test:\n\tnpm test\n\nlint:\n\tnpm run lint\n")
    (cible / ".claude" / "settings.json").write_text('{"permissions":{"allow":[]}}\n')
    (cible / ".gitignore").write_text("node_modules/\n")
    (cible / "src" / "index.js").write_text("console.log('salut')\n")
    git(cible, "init", "-q", "-b", "main")
    git(cible, "config", "user.email", "test@example.invalid")
    git(cible, "config", "user.name", "Ephor test")
    git(cible, "add", "-A")
    git(cible, "commit", "-qm", "base du projet")


def bootstrap(target: Path, *options: str) -> str:
    """Installe Ephor sur `target` avec le script d'installation, et rend sa sortie."""
    script = ROOT / ".ephor" / "scripts" / "bootstrap_existing_repo.sh"
    return subprocess.run(
        ["bash", str(script), *options, str(target)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
