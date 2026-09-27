"""Tests du générateur et vérificateur de manifeste de livraison."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".ephor" / "deploy"))

import manifest as delivery_manifest  # noqa: E402


def test_sha256_file(tmp_path: Path) -> None:
    f = tmp_path / "sample.txt"
    f.write_text("hello delivery", encoding="utf-8")
    h = delivery_manifest.sha256_file(f)
    assert len(h) == 64
    assert h == "fe0a8270114560f17e231d41c4388d955345502a10fe0e217553a2c84d1801a5"


def test_check_manifest_conforming(tmp_path: Path) -> None:
    policy = {
        "required_evidence": [
            "integrated_commit",
            "judge_report_sha256",
            "uv_lock_sha256",
            "judge_verdict_when_risk_eleve",
            "artifact_digest",
        ]
    }
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(json.dumps(policy), encoding="utf-8")

    manifest = {
        "integrated_commit": "abcdef123456",
        "judge_report_sha256": "1234567890abcdef" * 4,
        "judge_status": "verified_needs_review",
        "uv_lock_sha256": "abcdef1234567890" * 4,
        "artifact_digest": "sha256:" + "1" * 64,
        "risk": "modere",
        "judge_verdict_when_risk_eleve": True,
    }
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")

    errors = delivery_manifest.check_manifest(manifest_file, policy_file)
    assert not errors, f"Le manifeste devrait être conforme, erreurs: {errors}"


def test_check_manifest_missing_evidence(tmp_path: Path) -> None:
    policy = {
        "required_evidence": [
            "integrated_commit",
            "judge_report_sha256",
            "artifact_digest",
        ]
    }
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(json.dumps(policy), encoding="utf-8")

    manifest = {
        "integrated_commit": "abcdef123456",
        "judge_status": "verified_needs_review",
    }
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")

    errors = delivery_manifest.check_manifest(manifest_file, policy_file)
    assert len(errors) == 2
    assert any("judge_report_sha256" in err for err in errors)
    assert any("artifact_digest" in err for err in errors)


def test_check_manifest_failed_judge(tmp_path: Path) -> None:
    policy = {"required_evidence": ["integrated_commit"]}
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(json.dumps(policy), encoding="utf-8")

    manifest = {
        "integrated_commit": "abcdef123456",
        "judge_status": "verification_failed",
    }
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")

    errors = delivery_manifest.check_manifest(manifest_file, policy_file)
    assert len(errors) == 1
    assert "statut du juge" in errors[0]


def test_le_manifeste_refuse_une_empreinte_bouchee(tmp_path: Path) -> None:
    """Une suite de zéros satisfaisait le contrôle « champ non vide » : le manifeste avait
    l'air rassurant et n'attestait rien. L'empreinte de l'artefact livré est LA preuve
    qu'on livre bien ce qui a été jugé."""
    with pytest.raises(ValueError, match="empreinte de l'artefact"):
        delivery_manifest.generate_manifest(repo=tmp_path, artifact_digest="sha256:" + "0" * 64)
    with pytest.raises(ValueError, match="empreinte de l'artefact"):
        delivery_manifest.generate_manifest(repo=tmp_path, artifact_digest=None)


def test_le_modele_de_politique_ne_vaut_pas_politique(tmp_path: Path) -> None:
    """Le fichier livré avec Ephor décrit une chaîne de livraison qui n'existe pas
    encore. S'y conformer donnerait un « conforme » qui ne veut rien dire."""
    manifeste = tmp_path / "m.json"
    manifeste.write_text(json.dumps({"judge_status": "verified_needs_review"}), encoding="utf-8")
    modele = Path(delivery_manifest.__file__).parent / "release-policy.example.json"
    erreurs = delivery_manifest.check_manifest(manifeste, modele)
    assert erreurs and "modèle livré avec Ephor" in erreurs[0]
    assert "release-policy.json" in erreurs[0]


def test_seul_le_statut_que_le_juge_ecrit_est_accepte(tmp_path: Path) -> None:
    """« verified » et « ok » étaient acceptés : le juge ne les écrit jamais. Du mou dont
    seul un rapport falsifié pouvait profiter."""
    politique = tmp_path / "p.json"
    politique.write_text(json.dumps({"required_evidence": []}), encoding="utf-8")
    for statut, attendu in (("verified_needs_review", 0), ("verified", 1), ("ok", 1)):
        manifeste = tmp_path / "m.json"
        manifeste.write_text(json.dumps({"judge_status": statut}), encoding="utf-8")
        assert len(delivery_manifest.check_manifest(manifeste, politique)) == attendu, statut


def _depot_juge(tmp_path: Path) -> tuple[Path, Path]:
    """Un dépôt livré et le rapport du juge qui l'a vu, cohérents entre eux."""
    import subprocess

    depot = tmp_path / "depot"
    (depot / ".ephor").mkdir(parents=True)
    (depot / ".ephor" / "judge.py").write_text("# juge\n")
    (depot / "src").mkdir()
    (depot / "src" / "a.py").write_text("VALEUR = 1\n")

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(depot), *args], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    git("add", "-A")
    git("commit", "-qm", "livré")
    rapport = tmp_path / "report.json"
    rapport.write_text(
        json.dumps(
            {
                "status": "verified_needs_review",
                "risk": "faible",
                "files_sha256": {
                    "src/a.py": delivery_manifest.sha256_file(depot / "src" / "a.py"),
                    "src/efface.py": None,
                },
                "engine_sha256": delivery_manifest.engine_hash(depot),
            }
        ),
        encoding="utf-8",
    )
    return depot, rapport


DIGEST_OK = "sha256:" + "1" * 64


def test_le_manifeste_atteste_le_code_juge(tmp_path: Path) -> None:
    depot, rapport = _depot_juge(tmp_path)
    manifeste = delivery_manifest.generate_manifest(depot, "T-1", DIGEST_OK, report_file=rapport)
    assert manifeste["judge_status"] == "verified_needs_review"
    assert "src/a.py" in manifeste["judged_files_sha256"]


def test_le_manifeste_refuse_un_code_retouche_apres_le_verdict(tmp_path: Path) -> None:
    """Une livraison exige une preuve COURANTE. Un commit arrivé après le
    verdict et qui retouche un fichier jugé, c'est du code que personne n'a jugé."""
    import subprocess

    depot, rapport = _depot_juge(tmp_path)
    (depot / "src" / "a.py").write_text("VALEUR = 2\n")
    subprocess.run(["git", "-C", str(depot), "commit", "-qam", "après"], check=True)
    with pytest.raises(ValueError, match=r"src/a\.py livré n.est pas celui qui a été jugé"):
        delivery_manifest.generate_manifest(depot, "T-1", DIGEST_OK, report_file=rapport)


@pytest.mark.parametrize(
    ("changement", "motif"),
    [
        ({"status": "verification_failed"}, "pas favorable"),
        ({"files_sha256": None}, "antérieur à 0.8"),
        ({"engine_sha256": "0" * 64}, "le juge a changé"),
    ],
)
def test_le_manifeste_refuse_une_preuve_qui_n_atteste_rien(
    tmp_path: Path, changement: dict[str, object], motif: str
) -> None:
    depot, rapport = _depot_juge(tmp_path)
    donnees = json.loads(rapport.read_text(encoding="utf-8"))
    donnees.update(changement)
    rapport.write_text(json.dumps(donnees), encoding="utf-8")
    with pytest.raises(ValueError, match=motif):
        delivery_manifest.generate_manifest(depot, "T-1", DIGEST_OK, report_file=rapport)


def test_un_rapport_lu_dans_verify_est_dit_pour_ce_qu_il_est(tmp_path: Path) -> None:
    """`.verify/` est à portée de l'agent : un manifeste fait sur ce rapport est un essai."""
    import subprocess

    depot, rapport = _depot_juge(tmp_path)
    local = depot / ".verify" / "judge" / "T-1" / "latest"
    local.mkdir(parents=True)
    (local / "report.json").write_text(rapport.read_text(encoding="utf-8"), encoding="utf-8")
    script = ROOT / ".ephor" / "deploy" / "manifest.py"
    commande = [sys.executable, str(script), "generate", "--repo", str(depot), "--task-id", "T-1"]
    commande += ["--artifact-digest", DIGEST_OK, "--output", str(tmp_path / "m.json")]
    sortie = subprocess.run(commande, capture_output=True, text=True, check=False)
    assert sortie.returncode == 0, sortie.stderr
    assert "que l'agent peut écrire" in sortie.stderr
    commande += ["--report", str(rapport)]
    sortie = subprocess.run(commande, capture_output=True, text=True, check=False)
    assert sortie.returncode == 0 and "ATTENTION" not in sortie.stderr


def test_le_modele_recopie_sans_etre_rempli_dit_quoi_faire(tmp_path: Path) -> None:
    """Le modèle copié à sa place mais pas rempli : le message disait « sa place :
    .ephor/projet/release-policy.json » à qui venait de l'y mettre."""
    manifeste = tmp_path / "m.json"
    manifeste.write_text(json.dumps({"judge_status": "verified_needs_review"}), encoding="utf-8")
    modele = Path(delivery_manifest.__file__).parent / "release-policy.example.json"
    politique = tmp_path / ".ephor" / "projet" / "release-policy.json"
    politique.parent.mkdir(parents=True)
    politique.write_text(modele.read_text(encoding="utf-8"), encoding="utf-8")
    erreurs = delivery_manifest.check_manifest(manifeste, politique)
    assert erreurs and "encore le modèle" in erreurs[0]
    assert "Sa place" not in erreurs[0]
