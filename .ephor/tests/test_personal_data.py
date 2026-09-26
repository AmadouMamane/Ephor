"""Le juge des données personnelles : ce qu'il attrape, et ce qu'il laisse passer."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from conftest import EPHOR

sys.path.insert(0, str(EPHOR))

import personal_data

FUITES = """
import logging

log = logging.getLogger(__name__)


def virer(client, montant):
    log.info(f"virement de {montant} vers {client.iban}")
    if montant > 1000:
        raise ValueError(f"plafond dépassé pour {client.numeroCarte}")
    print(client["email"])
    log.warning("client %s hors plafond", client.id)
    return envoyer_courriel(client.email)
"""

PROPRE = """
import logging

log = logging.getLogger(__name__)
IBAN_MOTIF = r"^FR\\d{25}$"


def virer(client, montant):
    log.info("virement %s accepté pour le client %s", montant, client.id)
    if not verifier(client.iban, IBAN_MOTIF):
        raise ValueError("IBAN invalide")
    return enregistrer(client.iban, montant)
"""


def test_it_sees_through_f_strings_and_raised_errors() -> None:
    findings = dict(personal_data.scan_source(FUITES))
    assert "iban" in findings[8]
    assert "numero carte" in findings[10]  # camelCase découpé
    assert "email" in findings[11]  # accès par clé de dictionnaire
    assert len(findings) == 3


def test_what_is_not_a_log_is_not_its_business() -> None:
    """Envoyer un courriel à un client suppose de connaître son adresse : ce n'est pas une fuite."""
    lines = [line for line, _ in personal_data.scan_source(FUITES)]
    assert 13 not in lines  # envoyer_courriel(client.email)
    assert 12 not in lines  # log.warning avec un identifiant technique


DETOURS = """
import logging

log = logging.getLogger(__name__)


def virer(client, compte):
    message = f"vers {client.iban}"
    log.info(message)
    log.info("{iban}".format(iban=compte))
    log.info("ok", extra={"email": client.contact})
"""

INNOCENT = """
import logging

log = logging.getLogger(__name__)
EMAIL_MOTIF = r".+@.+"


class EmailInvalide(Exception):
    pass


def virer(client, stats):
    if not client.contact:
        raise EmailInvalide("format attendu")
    log.error("motif %s", EMAIL_MOTIF)
    log.info("%d adresses traitées", stats.email_count)
"""


def test_a_go_between_variable_does_not_get_it_through() -> None:
    """La forme la plus courante dans du vrai code : on passe par une variable."""
    trouve = dict(personal_data.scan_source(DETOURS))
    assert "iban" in trouve[9]  # la variable message, qui porte client.iban
    assert "iban" in trouve[10]  # le nom du paramètre de format
    assert "email" in trouve[11]  # la clé du dictionnaire extra


def test_a_name_is_not_a_datum() -> None:
    """Sonner sur un nom de classe ou une constante rendrait l'outil insupportable."""
    assert personal_data.scan_source(INNOCENT) == []


def test_correct_code_raises_nothing() -> None:
    assert personal_data.scan_source(PROPRE) == []


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("iban", "iban"),
        ("numeroCarteBancaire", "numero carte"),
        ("date_de_naissance", None),  # « de » coupe la paire : on n'invente pas
        ("date_naissance", "date naissance"),
        ("nom_fichier", None),
        ("nombre", None),
        ("card_number", "card number"),
    ],
)
def test_only_unambiguous_fields_raise_an_alert(name: str, expected: str | None) -> None:
    assert personal_data.sensitive(name) == expected


def test_the_repository_is_scanned_except_ephor(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "paiement.py").write_text(FUITES, encoding="utf-8")
    (tmp_path / ".ephor").mkdir()
    (tmp_path / ".ephor" / "outil.py").write_text(FUITES, encoding="utf-8")
    findings = personal_data.scan_repo(tmp_path)
    assert all(item.startswith("src/paiement.py:") for item in findings)
    assert len(findings) == 3


def test_un_dossier_du_projet_nomme_ephor_n_est_plus_un_angle_mort(tmp_path: Path) -> None:
    """Avant la 0.9, le dossier visible du même nom était sauté : c'était l'outil. Un
    paquet du projet qui porterait ce nom échappait donc au contrôle. Seul `.ephor/` l'est."""
    (tmp_path / "ephor").mkdir()
    (tmp_path / "ephor" / "paiement.py").write_text(FUITES, encoding="utf-8")
    assert len(personal_data.scan_repo(tmp_path)) == 3


def test_an_exception_is_granted_where_the_agent_cannot_reach(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "paiement.py").write_text(FUITES, encoding="utf-8")
    exceptions = tmp_path / ".ephor" / "projet" / "fuites-autorisees.txt"
    exceptions.parent.mkdir(parents=True)
    exceptions.write_text(
        "# validé par la conformité le 23/09/2026\nsrc/paiement.py:8\n", encoding="utf-8"
    )
    assert len(personal_data.scan_repo(tmp_path)) == 2
    exceptions.write_text("src/paiement.py\n", encoding="utf-8")
    assert personal_data.scan_repo(tmp_path) == []


def test_the_command_says_no_and_explains(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "paiement.py").write_text(FUITES, encoding="utf-8")
    assert personal_data.main(["--repo", str(tmp_path)]) == 1
    sortie = capsys.readouterr().out
    assert "src/paiement.py:8" in sortie
    assert "identifiant technique" in sortie


def test_les_caches_ne_sont_pas_du_code_de_projet(tmp_path: Path) -> None:
    """En conteneur, le juge range le cache d'uv DANS le dossier monté : le même commit
    était VÉRIFIÉ en local et en ÉCHEC en conteneur, sur des fuites trouvées dans pip."""
    fuite = 'def enregistre(logger, client):\n    logger.info("client %s", client.email_address)\n'
    for cache in (".ephor-cache/uv/pip", ".pytest_cache", "site-packages/paquet"):
        chemin = tmp_path / cache
        chemin.mkdir(parents=True)
        (chemin / "module.py").write_text(fuite, encoding="utf-8")
    assert personal_data.scan_repo(tmp_path) == []
    # Le même code dans le projet, lui, reste une fuite.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(fuite, encoding="utf-8")
    assert len(personal_data.scan_repo(tmp_path)) == 1


def test_un_vieux_fichier_en_latin_1_est_lu_et_un_octet_nul_ne_fait_rien_tomber(
    tmp_path: Path,
) -> None:
    """Lu en UTF-8, un fichier Latin-1 faisait tomber tout le contrôle sur une trace Python."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "vieux.py").write_bytes(
        b"# -*- coding: latin-1 -*-\nlibelle = 'caf\xe9'\nlogger.info(client.iban)\n"
    )
    (tmp_path / "src" / "nul.py").write_bytes(b"x = 1\x00\n")
    assert personal_data.scan_repo(tmp_path) == ["src/vieux.py:3: info() expose .iban (iban)"]


def test_un_depot_range_sous_un_dossier_saute_reste_controle(tmp_path: Path) -> None:
    """Jugé sur le chemin entier, un dépôt rangé sous `venv/` était sauté tout entier."""
    depot = tmp_path / "venv" / "projet"
    (depot / "src").mkdir(parents=True)
    (depot / "src" / "paiement.py").write_text(FUITES, encoding="utf-8")
    assert len(personal_data.scan_repo(depot)) == 3
