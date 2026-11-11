# OVH VPS Notif

Surveille la disponibilité des offres **VPS-1 2027** et **VPS-2 2027** d'OVHcloud (configurateur France)
et envoie une notification sur téléphone via [ntfy.sh](https://ntfy.sh) quand elles reviennent en stock.

## Fonctionnement

- `check_vps.py` (Python 3.12+, sans dépendance) interroge la route publique OVH
  `/vps/order/rule/datacenter/aggregated` (sans authentification) et lit la disponibilité Linux par datacenter.
- Datacenters surveillés : France (GRA, SBG, RBX) et, en option, Europe hors France (DE, UK, WAW, MIL).
  Les datacenters hors Europe sont ignorés.
- L'état est mémorisé dans `state.json`. Une notification est envoyée **uniquement** au passage
  indisponible → disponible (une seule notification groupée par exécution).
- En cas d'erreur de l'API OVH, le script s'arrête avec un code d'erreur sans modifier `state.json`.

Détails de l'API : voir [NOTES.md](NOTES.md).

## Configuration

| Variable | Obligatoire | Défaut | Rôle |
|---|---|---|---|
| `NTFY_TOPIC` | oui | — | Sujet ntfy.sh qui reçoit les notifications. **Secret.** |
| `INCLUDE_EUROPE` | non | `false` | `true` pour surveiller aussi les datacenters européens hors France. |
| `TEST_NOTIFICATION` | non | `false` | `true` pour envoyer une notification de test puis quitter. |

### Le secret `NTFY_TOPIC`

Sur ntfy.sh, toute personne qui connaît le nom du sujet peut le lire et y publier. Choisissez un nom long et
difficile à deviner, abonnez-vous-y dans l'application ntfy, et **ne l'écrivez jamais dans le dépôt** (qui est public).
Dans GitHub Actions, enregistrez-le en secret de dépôt (*Settings → Secrets and variables → Actions*).
Le script ne l'affiche jamais dans les logs.

## Lancement local

```bash
export NTFY_TOPIC="votre-sujet"        # ne pas commiter
python check_vps.py                     # vérification normale
TEST_NOTIFICATION=true python check_vps.py   # notification de test
INCLUDE_EUROPE=true python check_vps.py      # France + Europe
```

Codes de sortie : `0` OK, `1` configuration/état invalide, `2` erreur API OVH, `3` erreur ntfy.
