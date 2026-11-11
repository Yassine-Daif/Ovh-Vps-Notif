#!/usr/bin/env python3
"""Surveille la disponibilité des VPS OVHcloud (VPS-1 2027 / VPS-2 2027) et notifie via ntfy.sh.

Python 3.12+, bibliothèque standard uniquement. Voir README.md et NOTES.md.

Variables d'environnement :
  NTFY_TOPIC         (obligatoire) sujet ntfy.sh — secret, jamais affiché.
  INCLUDE_EUROPE     "true"/"false" (défaut "false") : surveille aussi l'Europe hors France.
  TEST_NOTIFICATION  "true"/"false" (défaut "false") : envoie une notif de test puis quitte.

Codes de sortie : 0 OK, 1 configuration/état, 2 API OVH, 3 ntfy.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# --- Constantes (cf. NOTES.md) -------------------------------------------------

PLANS: dict[str, str] = {
    "vps-2027-model1": "VPS-1 2027",
    "vps-2027-model2": "VPS-2 2027",
}
PRICES_FROM: dict[str, str] = {  # prix HT/mois avec engagement 12 mois
    "vps-2027-model1": "3,81 €",
    "vps-2027-model2": "7,21 €",
}

FRANCE = {"GRA", "SBG", "EU-WEST-RBX"}
EUROPE = {"DE", "UK", "WAW", "EU-SOUTH-MIL"}
OUTSIDE_EUROPE = {"BHS", "SGP", "SYD", "YNM"}

API_URL = (
    "https://eu.api.ovh.com/v1/vps/order/rule/datacenter/aggregated"
    "?ovhSubsidiary=FR&planCodes=" + ",".join(PLANS)
)
CONFIGURATOR_URL = "https://www.ovhcloud.com/fr/vps/configurator/"
NTFY_BASE_URL = "https://ntfy.sh/"

VALID_STATUSES = {"available", "out-of-stock", "out-of-stock-preorder-allowed"}
TIMEOUT = 20  # secondes
USER_AGENT = "ovh-vps-notif/1.0 (+python-urllib)"

STATE_FILE = Path(__file__).resolve().parent / "state.json"

EXIT_OK, EXIT_CONFIG, EXIT_OVH, EXIT_NTFY = 0, 1, 2, 3


class ConfigError(Exception):
    pass


class OvhApiError(Exception):
    pass


class NtfyError(Exception):
    pass


# --- Utilitaires ------------------------------------------------------------------


def log(msg: str) -> None:
    print(msg, flush=True)


def error(msg: str) -> None:
    print(f"ERREUR : {msg}", file=sys.stderr, flush=True)


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw == "":
        return default
    if raw in {"true", "1", "yes", "oui"}:
        return True
    if raw in {"false", "0", "no", "non"}:
        return False
    raise ConfigError(f"{name} doit valoir \"true\" ou \"false\" (valeur reçue invalide).")


def get_topic() -> str:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        raise ConfigError("la variable d'environnement NTFY_TOPIC est obligatoire.")
    # Règle des sujets ntfy ; la valeur n'est jamais affichée.
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", topic):
        raise ConfigError("NTFY_TOPIC contient des caractères invalides (autorisés : A-Z a-z 0-9 _ -, 64 max).")
    return topic


def region_of(dc: str) -> str | None:
    if dc in FRANCE:
        return "France"
    if dc in EUROPE:
        return "Europe"
    if dc in OUTSIDE_EUROPE:
        return "Hors Europe"
    return None


# --- API OVH ----------------------------------------------------------------------


def fetch_availability() -> object:
    req = urllib.request.Request(API_URL, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        raise OvhApiError(f"l'API OVH a répondu HTTP {e.code} {e.reason}.") from None
    except urllib.error.URLError as e:
        raise OvhApiError(f"impossible de joindre l'API OVH ({e.reason}).") from None
    except (TimeoutError, OSError) as e:
        raise OvhApiError(f"erreur réseau / timeout ({TIMEOUT} s) vers l'API OVH : {type(e).__name__}.") from None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise OvhApiError(f"réponse de l'API OVH non JSON ({e}).") from None


def parse(payload: object) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Valide la réponse et renvoie ({planCode: {datacenter: linuxStatus}}, avertissements).

    Lève OvhApiError si le format ne correspond pas à celui attendu.
    """

    def bad(why: str) -> OvhApiError:
        return OvhApiError(f"format de réponse inattendu : {why}")

    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise bad("clé 'models' (liste) absente.")

    result: dict[str, dict[str, str]] = {}
    warnings: list[str] = []
    for model in payload["models"]:
        if not isinstance(model, dict) or not isinstance(model.get("planCode"), str):
            raise bad("entrée de 'models' sans 'planCode'.")
        plan = model["planCode"]
        dcs = model.get("datacenters")
        if not isinstance(dcs, list):
            raise bad(f"'datacenters' absent pour {plan}.")
        if plan not in PLANS:
            continue
        statuses = result.setdefault(plan, {})
        for entry in dcs:
            if not isinstance(entry, dict):
                raise bad(f"entrée de datacenter invalide pour {plan}.")
            dc = entry.get("datacenter")
            status = entry.get("linuxStatus")
            if not isinstance(dc, str) or not dc:
                raise bad(f"champ 'datacenter' manquant pour {plan}.")
            if status not in VALID_STATUSES:
                raise bad(f"linuxStatus inconnu {status!r} pour {plan}/{dc}.")
            if entry.get("remoteStorage") is True:
                continue  # variante stockage distant, ignorée
            if region_of(dc) is None:
                warnings.append(f"datacenter inconnu {dc!r} pour {PLANS[plan]} (ignoré, à classer dans le script).")
                continue
            statuses[dc] = status

    missing = [p for p in PLANS if p not in result]
    if missing:
        raise bad(f"planCode absent de la réponse : {', '.join(missing)}.")
    return result, warnings


# --- État -------------------------------------------------------------------------


def load_state() -> dict[str, bool]:
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ConfigError(f"state.json illisible ({e}). Corrigez-le ou supprimez-le.") from None
    avail = data.get("availability") if isinstance(data, dict) else None
    if not isinstance(avail, dict) or not all(isinstance(v, bool) for v in avail.values()):
        raise ConfigError("state.json a un format inattendu. Corrigez-le ou supprimez-le.")
    return avail


def save_state(availability: dict[str, bool]) -> None:
    data = {
        "changed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "availability": dict(sorted(availability.items())),
    }
    fd, tmp = tempfile.mkstemp(dir=STATE_FILE.parent, prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, STATE_FILE)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# --- ntfy -------------------------------------------------------------------------


def send_ntfy(topic: str, title: str, message: str, priority: str, tags: list[str], click: str) -> None:
    headers = {
        "Title": title,  # ASCII uniquement (en-tête HTTP)
        "Priority": priority,
        "Tags": ",".join(tags),
        "Click": click,
        "User-Agent": USER_AGENT,
        "Content-Type": "text/plain; charset=utf-8",
    }
    req = urllib.request.Request(
        NTFY_BASE_URL + topic, data=message.encode("utf-8"), headers=headers, method="POST"
    )
    # Les messages d'erreur ne contiennent jamais l'URL (qui inclut le sujet secret).
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        raise NtfyError(f"ntfy.sh a répondu HTTP {e.code}.") from None
    except urllib.error.URLError as e:
        reason = type(e.reason).__name__ if not isinstance(e.reason, str) else "erreur réseau"
        raise NtfyError(f"impossible de joindre ntfy.sh ({reason}).") from None
    except (TimeoutError, OSError) as e:
        raise NtfyError(f"erreur réseau / timeout vers ntfy.sh ({type(e).__name__}).") from None


def build_notification(new: list[tuple[str, str]]) -> tuple[str, str, str, list[str], str]:
    """new = [(planCode, datacenter)] → (titre, message, priorité, tags, url Click)."""
    groups: dict[tuple[str, str], list[str]] = {}
    for plan, dc in new:
        groups.setdefault((plan, region_of(dc) or "?"), []).append(dc)
    keys = sorted(groups, key=lambda k: (list(PLANS).index(k[0]), k[1] != "France"))

    if len(keys) == 1:
        plan, region = keys[0]
        title = f"{PLANS[plan]} dispo en {region} ({', '.join(sorted(groups[keys[0]]))})"
    else:
        parts = [f"{PLANS[p]} {r} ({', '.join(sorted(groups[(p, r)]))})" for p, r in keys]
        title = "VPS dispo : " + " + ".join(parts)

    lines = []
    for plan, region in keys:
        for dc in sorted(groups[(plan, region)]):
            lines.append(f"• {PLANS[plan]} — {dc} ({region}) — dès {PRICES_FROM[plan]} HT/mois")
    lines.append("")
    lines.append("Touchez la notification pour ouvrir le configurateur OVHcloud.")

    has_france = any(region == "France" for _, region in keys)
    priority = "high" if has_france else "default"
    tags = ["flag-fr"] if has_france else ["computer"]

    plans_involved = {p for p, _ in keys}
    click = CONFIGURATOR_URL
    if len(plans_involved) == 1:
        click += "?planCode=" + next(iter(plans_involved))
    return title, "\n".join(lines), priority, tags, click


# --- Programme principal ------------------------------------------------------------


def run() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    topic = get_topic()
    include_europe = env_bool("INCLUDE_EUROPE")
    test_notification = env_bool("TEST_NOTIFICATION")

    if test_notification:
        log("Mode TEST_NOTIFICATION : envoi d'une notification de test…")
        send_ntfy(
            topic,
            title="Test OVH VPS Notif",
            message="Notification de test : la surveillance des VPS OVHcloud est bien configurée.",
            priority="default",
            tags=["white_check_mark"],
            click=CONFIGURATOR_URL,
        )
        log("Notification de test envoyée.")
        return EXIT_OK

    watched_regions = {"France"} | ({"Europe"} if include_europe else set())
    log(f"Régions surveillées : {', '.join(sorted(watched_regions))} (INCLUDE_EUROPE={str(include_europe).lower()})")

    payload = fetch_availability()
    statuses, warnings = parse(payload)
    previous = load_state()

    current: dict[str, bool] = {}
    newly_available: list[tuple[str, str]] = []
    skipped: set[str] = set()

    for plan, name in PLANS.items():
        plan_statuses = statuses[plan]
        watched_dcs = sorted(
            (FRANCE | (EUROPE if include_europe else set())),
            key=lambda d: (region_of(d) != "France", d),
        )
        for dc in watched_dcs:
            status = plan_statuses.get(dc)
            if status is None:
                warnings.append(f"{dc} absent de la réponse pour {name} (considéré indisponible).")
                status = "absent"
            available = status == "available"
            key = f"{plan}|{dc}"
            current[key] = available
            marker = ""
            if available and previous.get(key) is not True:
                newly_available.append((plan, dc))
                marker = "  <-- NOUVEAU"
            log(f"{name:<11} | {dc:<12} | {region_of(dc):<6} | {status}{marker}")
        skipped |= set(plan_statuses) - set(watched_dcs)

    if skipped:
        log(f"Non surveillés : {', '.join(sorted(skipped))}")
    for w in warnings:
        log(f"AVERTISSEMENT : {w}")

    if newly_available:
        title, message, priority, tags, click = build_notification(newly_available)
        log(f"Envoi de la notification : {title}")
        send_ntfy(topic, title, message, priority, tags, click)
        log("Notification envoyée.")
    else:
        log("Aucune nouvelle disponibilité : pas de notification.")

    # On ne réécrit state.json que si la disponibilité a changé : évite un commit
    # à chaque exécution du workflow (l'horodatage seul ne compte pas).
    if current != previous or not STATE_FILE.exists():
        save_state(current)
        log("state.json mis à jour.")
    else:
        log("state.json inchangé.")
    return EXIT_OK


def main() -> int:
    try:
        return run()
    except ConfigError as e:
        error(str(e))
        return EXIT_CONFIG
    except OvhApiError as e:
        error(f"API OVH : {e} state.json n'a pas été modifié, aucune notification envoyée.")
        return EXIT_OVH
    except NtfyError as e:
        error(f"ntfy : {e} state.json n'a pas été modifié (la notification sera retentée).")
        return EXIT_NTFY


if __name__ == "__main__":
    sys.exit(main())
