"""Le contrôle des réglages GitLab, face à un faux GitLab local.

Aucune instance GitLab n'est joignable d'ici : ces tests vérifient la lecture et le
jugement des réponses telles que la documentation de l'API les décrit. Le premier essai
contre une instance réelle reste à faire."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

import gitlab_check

JETON = "glpat-jeton-de-test-ne-pas-afficher"
PROJET = "groupe/projet"
BON = {
    "/api/v4/projects/groupe%2Fprojet": {
        "default_branch": "main",
        "ci_config_path": ".gitlab-ci.yml@groupe/controles",
        "only_allow_merge_if_pipeline_succeeds": True,
        "allow_merge_on_skipped_pipeline": False,
    },
    "/api/v4/projects/groupe%2Fprojet/protected_branches/main": {
        "push_access_levels": [{"access_level": 0}],
        "merge_access_levels": [{"access_level": 40}],
        "allow_force_push": False,
        "code_owner_approval_required": True,
    },
    "/api/v4/projects/groupe%2Fprojet/repository/files/.gitlab%2FCODEOWNERS?ref=main": {
        "file_path": ".gitlab/CODEOWNERS"
    },
    "/api/v4/projects/groupe%2Fprojet/approvals": {
        "merge_requests_author_approval": False,
        "merge_requests_disable_committers_approval": True,
        "disable_overriding_approvers_per_merge_request": True,
        "reset_approvals_on_push": True,
    },
}


@pytest.fixture
def gitlab() -> Iterator[tuple[str, dict[str, Any]]]:
    routes: dict[str, Any] = {}

    class Faux(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.headers.get("PRIVATE-TOKEN") != JETON:
                self.send_response(401)
                self.end_headers()
                return
            corps = routes.get(self.path)
            self.send_response(200 if corps is not None else 404)
            self.end_headers()
            if corps is not None:
                self.wfile.write(json.dumps(corps).encode("utf-8"))

        def log_message(self, *args: object) -> None:
            return

    serveur = ThreadingHTTPServer(("127.0.0.1", 0), Faux)
    fil = threading.Thread(target=serveur.serve_forever, daemon=True)
    fil.start()
    yield f"http://127.0.0.1:{serveur.server_address[1]}", routes
    serveur.shutdown()


def lancer(
    url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str]:
    monkeypatch.setenv("GITLAB_TOKEN", JETON)
    code = gitlab_check.main(["--url", url, "--projet", PROJET])
    return code, capsys.readouterr().out


def test_tout_en_place(
    gitlab: tuple[str, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    url, routes = gitlab
    routes.update(BON)
    code, sortie = lancer(url, monkeypatch, capsys)
    assert code == 0, sortie
    assert sortie.count("[  OK   ]") == 5
    assert JETON not in sortie, "le jeton ne s'affiche jamais"


def test_un_projet_neuf_dit_tout_ce_qui_manque(
    gitlab: tuple[str, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Réglages par défaut de GitLab : rien de ce qui rend la porte de la CI fiable."""
    url, routes = gitlab
    routes["/api/v4/projects/groupe%2Fprojet"] = {"default_branch": "main"}
    code, sortie = lancer(url, monkeypatch, capsys)
    assert code == 1
    assert sortie.count("[À FAIRE]") == 4
    assert "[INCONNU]" in sortie, "une offre sans approbations se dit, elle ne passe pas pour OK"
    assert ".gitlab-ci.yml@<groupe>/controles" in sortie
    assert "Le pipeline doit réussir" in sortie


@pytest.mark.parametrize(
    ("chemin", "changement", "attendu"),
    [
        ("/api/v4/projects/groupe%2Fprojet", {"ci_config_path": ".gitlab-ci.yml"}, "controles"),
        (
            "/api/v4/projects/groupe%2Fprojet",
            {"allow_merge_on_skipped_pipeline": True},
            "pipeline sauté",
        ),
        (
            "/api/v4/projects/groupe%2Fprojet/protected_branches/main",
            {"push_access_levels": [{"access_level": 30}]},
            "des développeurs peuvent y pousser",
        ),
        (
            "/api/v4/projects/groupe%2Fprojet/protected_branches/main",
            {"code_owner_approval_required": False},
            "pas obligatoire",
        ),
        (
            "/api/v4/projects/groupe%2Fprojet/approvals",
            {"merge_requests_author_approval": True},
            "l'auteur peut approuver",
        ),
    ],
)
def test_un_seul_reglage_manquant_suffit(
    gitlab: tuple[str, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    chemin: str,
    changement: dict[str, Any],
    attendu: str,
) -> None:
    url, routes = gitlab
    routes.update(json.loads(json.dumps(BON)))
    routes[chemin] = {**routes[chemin], **changement}
    code, sortie = lancer(url, monkeypatch, capsys)
    assert code == 1 and attendu in sortie, sortie


def test_le_jeton_ne_part_pas_en_clair_sur_le_reseau(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITLAB_TOKEN", JETON)
    with pytest.raises(SystemExit, match="https"):
        gitlab_check.main(["--url", "http://gitlab.interne", "--projet", PROJET])


def test_sans_jeton_il_le_dit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    monkeypatch.delenv("GL_TOKEN", raising=False)
    assert gitlab_check.main(["--url", "https://gitlab.interne", "--projet", PROJET]) == 2
    assert "GITLAB_TOKEN" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("remote", "attendu"),
    [
        (
            "git@gitlab.interne:groupe/sous/projet.git",
            ("https://gitlab.interne", "groupe/sous/projet"),
        ),
        (
            "ssh://git@gitlab.interne:2222/groupe/projet.git",
            ("https://gitlab.interne", "groupe/projet"),
        ),
        ("https://gitlab.interne/groupe/projet.git", ("https://gitlab.interne", "groupe/projet")),
        ("https://gitlab.interne/groupe/projet", ("https://gitlab.interne", "groupe/projet")),
        # Un jeton glissé dans l'adresse ne sort pas : il était affiché en tête du rapport.
        (
            "https://oauth2:JETON-FACTICE@gitlab.interne/groupe/projet.git",
            ("https://gitlab.interne", "groupe/projet"),
        ),
    ],
)
def test_le_projet_se_deduit_du_remote(remote: str, attendu: tuple[str, str]) -> None:
    assert gitlab_check.lire_remote(remote) == attendu
