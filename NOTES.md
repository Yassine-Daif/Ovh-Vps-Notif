# NOTES — Exploration API OVHcloud (disponibilité VPS)

Exploration faite le 2026-11-11 avec des appels `curl` réels à l'API publique OVH (sans authentification).

## 1. planCodes

Source : `GET https://eu.api.ovh.com/v1/order/catalog/public/vps?ovhSubsidiary=FR`

| Offre | planCode | Specs (`products[].blobs.technical`) | Prix HT / mois |
|---|---|---|---|
| VPS-1 2027 | `vps-2027-model1` | 2 vCore, 4 Go RAM, 40 Go NVMe, 500 Mbps | **3,81 €** (engagement 12 mois, mode `upfront12`) · 4,26 € (6 mois, `upfront6`) · 4,49 € (sans engagement, `default`) |
| VPS-2 2027 | `vps-2027-model2` | 4 vCore, 8 Go RAM, 75 Go NVMe, 1000 Mbps | **7,21 €** (12 mois) · 8,06 € (6 mois) · 8,49 € (sans engagement) |

- **Unité du prix** : le champ `price` du catalogue est exprimé en **1e-8 €** (ex. `381000000` → 3,81 €), HT. La TVA est dans un champ `tax` séparé (taux de 20 %, voir `locale.taxRate`).
- Le prix « à partir de » affiché sur le site correspond au tarif avec engagement de 12 mois.
- Pièges à éviter :
  - `vps-2027-model1.LZ` / `vps-2027-model2.LZ` (« VPS-1 LZ 2027 », « VPS-2 LZ 2027 ») sont des offres **Local Zone**, avec d'autres prix et d'autres datacenters (`EU-WEST-LZ-MRS`, `EU-WEST-LZ-AMS`…). Elles sont **hors périmètre**.
  - `vps-2025-model*` correspond à l'ancienne gamme (« VPS-x 2026 »).

## 2. Route de disponibilité (fiable, testée)

Routes publiques (`noAuthentication: true`, `apiStatus: PRODUCTION` dans `https://eu.api.ovh.com/v1/vps.json`) :

- **Route agrégée, recommandée** (un seul appel pour les deux plans) :
  `GET https://eu.api.ovh.com/v1/vps/order/rule/datacenter/aggregated?ovhSubsidiary=FR&planCodes=vps-2027-model1,vps-2027-model2`
- Route par plan :
  `GET https://eu.api.ovh.com/v1/vps/order/rule/datacenter?ovhSubsidiary=FR&planCode=vps-2027-model1`
  (paramètre optionnel `os`, par ex. `os=Debian%2012`)

Les deux routes ont renvoyé HTTP 200 pour les deux planCodes. `order.json` ne contient aucune route de disponibilité VPS utile.

### Exemple de réponse (route agrégée, tronquée)

```json
{"models":[
  {"planCode":"vps-2027-model2","datacenters":[
    {"datacenter":"SBG","code":"eu-west-sbg","remoteStorage":false,"status":"available","daysBeforeDelivery":0,"linuxStatus":"out-of-stock","windowsStatus":"available"},
    {"datacenter":"EU-WEST-RBX","code":"eu-west-rbx","remoteStorage":false,"status":"out-of-stock","daysBeforeDelivery":0,"linuxStatus":"out-of-stock","windowsStatus":"out-of-stock"},
    {"datacenter":"SBG","code":"eu-west-sbg","remoteStorage":true,"status":"out-of-stock","daysBeforeDelivery":0,"linuxStatus":"out-of-stock","windowsStatus":"out-of-stock"}
  ]}
]}
```

### Valeurs possibles du statut

Les champs `status`, `linuxStatus` et `windowsStatus` prennent une valeur de l'énumération `vps.order.rule.DatacenterStatusEnum` :
- `available`
- `out-of-stock`
- `out-of-stock-preorder-allowed`

### Points d'attention (observés)

- **`status` global trompeur** : sans paramètre `os`, `status` vaut `available` dès que Linux **ou** Windows est disponible. Par exemple, pour VPS-2 à SBG : `status=available`, mais `linuxStatus=out-of-stock`. Avec `os=Debian 12`, `status` suit la disponibilité Linux. → Pour un VPS Linux, il faut surveiller **`linuxStatus`**.
- **Doublons `remoteStorage`** : la route agrégée renvoie aussi des entrées `remoteStorage: true` (variante avec stockage distant) → filtrer sur `remoteStorage == false`.
- `daysBeforeDelivery` valait toujours 0.
- État constaté le 2026-11-11 : VPS-1 était disponible sous Linux seulement à SGP. VPS-2 était en rupture Linux partout (Windows disponible à SBG, GRA, DE, MIL, UK et WAW).
- La route `GET /vps/datacenter?country=FR` renvoie une erreur 500, elle est donc inutilisable.

## 3. Datacenters par groupe

Source : `configurations[name=vps_datacenter]` de chaque plan dans le catalogue. Les 11 valeurs sont identiques pour les deux plans et correspondent au champ `datacenter` de la route de dispo.

| Groupe | `datacenter` | `code` | Lieu |
|---|---|---|---|
| **France** (prioritaire) | `GRA` | eu-west-gra | Gravelines |
| | `SBG` | eu-west-sbg | Strasbourg |
| | `EU-WEST-RBX` | eu-west-rbx | Roubaix |
| **Europe hors France** (activable / désactivable) | `DE` | eu-west-lim | Limburg (Allemagne) |
| | `UK` | eu-west-eri | Erith (Royaume-Uni) |
| | `WAW` | eu-central-waw | Varsovie (Pologne) |
| | `EU-SOUTH-MIL` | eu-south-mil | Milan (Italie) |
| **Hors Europe** (exclus) | `BHS` | ca-east-bhs | Beauharnois (Canada) |
| | `SGP` | ap-southeast-sgp | Singapour |
| | `SYD` | ap-southeast-syd | Sydney |
| | `YNM` | ap-south-mum | Mumbai (Inde) |

Pour la suite : classer les datacenters avec ces listes explicites et signaler tout datacenter inconnu qui apparaîtrait. Le préfixe `eu-` du `code` ne suffit pas à repérer la France.
