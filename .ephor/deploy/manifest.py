#!/usr/bin/env python3
"""Générateur et vérificateur de manifeste de livraison scellé.

Conforme aux principes de .ephor/deploy/README.md et vérifié contre la politique de release.
Bibliothèque standard uniquement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

# `datetime.UTC` demande Python 3.11. Le Mac d'entreprise décrit par ce dépôt a un
# 3.9 : ce fichier doit tourner dessus comme le reste d'Ephor.
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def sha256_file(path: Path) -> str:
    """Calcule l'empreinte SHA-256 d'un fichier."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def git_commit(repo: Path) -> str:
    """Commit courant du dépôt."""
    res = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


def resolve_latest_task_report(repo: Path, task_id: str | None = None) -> tuple[str, Path]:
    """Retrouve le dernier rapport du juge."""
    judge_dir = repo / ".verify" / "judge"
    if not judge_dir.exists():
        raise FileNotFoundError("Aucun rapport du juge trouvé dans .verify/judge")

    if task_id:
        report_file = judge_dir / task_id / "latest" / "report.json"
        if not report_file.exists():
            raise FileNotFoundError(f"Rapport introuvable pour la tâche {task_id} : {report_file}")
        return task_id, report_file

    # Recherche de la tâche la plus récente
    candidates = [p for p in judge_dir.iterdir() if p.is_dir()]
    if not candidates:
        raise FileNotFoundError("Aucune tâche trouvée sous .verify/judge")
    latest = max(
        candidates,
        key=lambda p: (
            (p / "latest" / "report.json").stat().st_mtime
            if (p / "latest" / "report.json").exists()
            else 0
        ),
    )
    report_file = latest / "latest" / "report.json"
    if not report_file.exists():
        raise FileNotFoundError(f"Rapport manquant dans {latest}")
    return latest.name, report_file


def engine_hash(repo: Path) -> str:
    """Même calcul que `.ephor/judge.py` : l'empreinte du juge tel qu'il est ICI."""
    parts: list[bytes] = []
    moteur = repo / ".ephor"
    for path in sorted([*moteur.glob("*.py"), *moteur.glob("zones.txt")]):
        parts.append(path.name.encode("utf-8") + b":" + path.read_bytes())
    return hashlib.sha256(b"".join(parts)).hexdigest()


def contenu_livre(repo: Path, chemin: str) -> str | None:
    """Empreinte du fichier dans le commit livré (HEAD), ou None s'il n'y existe pas."""
    res = subprocess.run(
        ["git", "cat-file", "blob", f"HEAD:{chemin}"],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    return hashlib.sha256(res.stdout).hexdigest() if res.returncode == 0 else None


def ecarts_de_preuve(repo: Path, report: dict[str, Any]) -> list[str]:
    """Ce qui empêche ce verdict d'attester le code livré. Vide : il l'atteste.

    Une preuve est liée au contenu qu'elle a vérifié, et une livraison exige une preuve
    COURANTE. Le juge relève l'empreinte de chaque fichier jugé ; ici on vérifie que le
    commit livré porte exactement ces contenus. Cela tient après une fusion comme après un
    « squash » ; un commit qui retouche l'un de ces fichiers ensuite rend la preuve périmée,
    et c'est voulu : ce qu'on livrerait n'aurait pas été jugé.
    """
    ecarts: list[str] = []
    if report.get("status") != "verified_needs_review":
        ecarts.append(f"le verdict du juge n'est pas favorable : {report.get('status')}")
    fichiers = report.get("files_sha256")
    if not isinstance(fichiers, dict) or not fichiers:
        ecarts.append("rapport sans empreinte des fichiers jugés (juge antérieur à 0.8) : rejugez")
    else:
        for chemin, attendu in sorted(fichiers.items()):
            if contenu_livre(repo, chemin) != attendu:
                ecarts.append(f"{chemin} livré n'est pas celui qui a été jugé")
    if report.get("engine_sha256") != engine_hash(repo):
        ecarts.append("le juge a changé depuis ce verdict : rejugez avec le juge actuel")
    return ecarts


def generate_manifest(
    repo: Path,
    task_id: str | None = None,
    artifact_digest: str | None = None,
    output: Path | None = None,
    report_file: Path | None = None,
) -> dict[str, Any]:
    """Génère le dictionnaire de manifeste de livraison."""
    # Pas de bouchon : l'empreinte de l'artefact livré est LA preuve qu'on livre bien ce
    # qui a été jugé. Une suite de zéros satisfaisait le contrôle « champ non vide » et
    # rendait le manifeste rassurant sans rien attester. Mieux vaut refuser de le produire.
    # La suite de zéros était la valeur par défaut : elle a la bonne FORME, elle
    # satisfaisait le contrôle « champ non vide », et elle n'atteste rien. Elle est
    # refusée nommément, sinon le bouchon revient par la porte de service.
    digest = artifact_digest or os.environ.get("ARTIFACT_DIGEST")
    if not digest or not DIGEST.match(digest) or set(digest.split(":", 1)[1]) == {"0"}:
        raise ValueError(
            "empreinte de l'artefact absente, mal formée ou nulle. Passe "
            "--artifact-digest sha256:<64 caractères> ou ARTIFACT_DIGEST : sans elle, "
            "le manifeste n'atteste rien."
        )
    commit = git_commit(repo)
    if report_file is not None:
        task_name, report_path = task_id or "?", report_file
    else:
        task_name, report_path = resolve_latest_task_report(repo, task_id)
    report_data = json.loads(report_path.read_text(encoding="utf-8"))
    ecarts = ecarts_de_preuve(repo, report_data)
    if ecarts:
        raise ValueError(
            "le verdict du juge n'atteste pas ce qui serait livré : " + " ; ".join(ecarts)
        )

    uv_lock_path = repo / "uv.lock"
    uv_lock_hash = sha256_file(uv_lock_path) if uv_lock_path.exists() else None

    risk = report_data.get("risk", "faible")
    # Ce champ ne dit PAS qu'une revue de sécurité a eu lieu : il dit que le juge a rendu
    # un verdict favorable sur une tâche à risque élevé. L'ancien nom
    # (`security_review_if_risk_eleve`) faisait croire à une revue qui n'existe pas.
    juge_favorable = True
    if risk == "eleve":
        juge_favorable = report_data.get("status") == "verified_needs_review"

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "task_id": task_name,
        "integrated_commit": commit,
        "judge_report_sha256": sha256_file(report_path),
        "judge_status": report_data.get("status"),
        "engine_sha256": report_data.get("engine_sha256"),
        "judged_files_sha256": report_data.get("files_sha256"),
        "uv_lock_sha256": uv_lock_hash,
        "artifact_digest": digest,
        "risk": risk,
        "judge_verdict_when_risk_eleve": juge_favorable,
    }

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    return manifest


def check_manifest(manifest_path: Path, policy_path: Path) -> list[str]:
    """Vérifie la conformité d'un manifeste vis-à-vis d'une politique de release."""
    errors: list[str] = []
    if not manifest_path.exists():
        return [f"Manifeste introuvable : {manifest_path}"]
    if not policy_path.exists():
        return [f"Politique introuvable : {policy_path}"]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    policy = json.loads(policy_path.read_text(encoding="utf-8"))

    # Le modèle livré avec Ephor est un MODÈLE : il décrit une chaîne de livraison
    # qui n'existe pas encore (canary, seuils, retour arrière). Le valider contre lui-même
    # donnerait un « conforme » qui ne veut rien dire.
    if policy.get("example_only"):
        return [
            f"{policy_path} est le modèle livré avec Ephor, pas une politique. Sa place : "
            ".ephor/projet/release-policy.json, remplie avec les règles de l'entreprise, "
            "sans la ligne « example_only »."
        ]

    required = policy.get("required_evidence", [])
    for field in required:
        val = manifest.get(field)
        if val is None or val == "":
            errors.append(f"Preuve manquante : {field}")
        elif (
            field == "judge_verdict_when_risk_eleve" and manifest.get("risk") == "eleve" and not val
        ):
            errors.append("Verdict du juge défavorable sur une tâche à risque élevé")

    # Le juge n'écrit QUE ces statuts-là. Accepter « verified » ou « ok », qu'il n'écrit
    # jamais, c'était élargir gratuitement ce qu'un rapport falsifié pouvait prétendre.
    if manifest.get("judge_status") != "verified_needs_review":
        errors.append(f"Le statut du juge n'est pas favorable : {manifest.get('judge_status')}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Gestion du manifeste de livraison")
    sub = parser.add_subparsers(dest="cmd", required=True)

    gen = sub.add_parser("generate", help="Génère le manifeste")
    gen.add_argument("--repo", type=Path, default=Path.cwd())
    gen.add_argument("--task-id", type=str, default=None)
    gen.add_argument("--artifact-digest", type=str, default=None)
    gen.add_argument(
        "--report", type=Path, default=None, help="rapport du juge (défaut : le dernier local)"
    )
    gen.add_argument("--output", type=Path, default=Path(".verify/delivery-manifest.json"))

    chk = sub.add_parser("check", help="Vérifie la conformité d'un manifeste")
    chk.add_argument("--manifest", type=Path, default=Path(".verify/delivery-manifest.json"))
    chk.add_argument("--policy", type=Path, default=Path(".ephor/projet/release-policy.json"))

    args = parser.parse_args()

    if args.cmd == "generate":
        try:
            manifest = generate_manifest(
                args.repo, args.task_id, args.artifact_digest, args.output, args.report
            )
            print(
                f"Manifeste généré : {args.output} (commit={manifest['integrated_commit'][:7]})",
                flush=True,
            )
            if args.report is None:
                # Le rapport par défaut vit dans .verify/, que l'agent peut écrire : le même
                # défaut avait fait retirer `depends_on`. Le dire, là où on le lit.
                print(
                    "ATTENTION : rapport lu dans .verify/, que l'agent peut écrire. Ce manifeste"
                    " vaut pour un essai sur le poste ; en CI, --report désigne le rapport du"
                    " job `juge`.",
                    file=sys.stderr,
                )
            return 0
        except Exception as e:
            print(f"Erreur de génération : {e}", file=sys.stderr)
            return 1
    elif args.cmd == "check":
        errors = check_manifest(args.manifest, args.policy)
        if errors:
            print("ERREUR de conformité du manifeste de livraison :", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
            return 1
        print("Manifeste conforme aux exigences de la politique de livraison.")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
