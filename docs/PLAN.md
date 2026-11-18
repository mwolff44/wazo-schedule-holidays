# Plan de développement — wazo-schedule-holidays

Version Wazo minimale : **26.10**. Plugin confd (entry point `wazo_confd.plugins`).

## Constat vérifié (wazo-26.10)

- Les plages horaires sont évaluées par **wazo-agid** (`check_schedule`), pas par Asterisk.
- Critères d'une période : heures, jours de la semaine, jours du mois, mois. **Aucune année**
  (ni en base `schedule_time`, ni dans l'API confd, ni dans agid).
- Les périodes exceptionnelles (`closed`) sont évaluées **avant** les périodes ouvertes.
- Une période exceptionnelle exige une destination ; on reprend la `closed_destination` du schedule.
- `PUT /schedules/{id}` remplace la liste des `exceptional_periods` ; les périodes n'ont pas d'id
  exposé et sont recréées à chaque écriture.
- Bibliothèque `holidays` (PyPI, MIT, v0.109) : NL (nl/en_US/fy), FR avec sous-divisions
  `57` (Moselle) et `6AE` (Alsace) ; `67`/`68` n'existent pas. `Zone A/B/C` = zones scolaires (à masquer).

## Décisions de conception

1. Calendrier : lib `holidays`. Pays = code ISO + sous-division optionnelle.
2. Clé stable d'un férié : son nom `en_US`. Le nom affiché dépend du paramètre de requête
   `language` (défaut `en_US`, non stocké dans la config).
3. Sélection : tout actif par défaut ; on stocke les `excluded`.
4. Période générée : `closed`, `00:00-23:59`, `month_days=[j]`, `months=[m]`, tous `week_days`,
   destination = copie de la `closed_destination`.
5. Fenêtre : `[aujourd'hui, aujourd'hui+365 j[` (années N et N+1 filtrées), « aujourd'hui »
   dans le fuseau du schedule (repli : fuseau système).
6. Stockage : `<dir>/<schedule_id>.json`
   `{"version":1,"tenant_uuid":"...","country":"NL","subdivision":null,"excluded":[],"managed":[{"date":"2027-03-29","key":"Easter Monday"}]}`
   `managed` = périodes posées par le plugin (reconnues par signature).
7. Synchro idempotente : retire les périodes `managed` précédentes, ajoute les nouvelles,
   conserve les périodes manuelles ; écriture via le service schedule de confd ; JSON mis à jour
   seulement après succès.

## Phase 0 — vérifications bloquantes

| # | À vérifier | Comment |
|---|---|---|
| 0.1 | Livraison de `holidays` (apt `python3-holidays`, pip dans le venv confd, ou vendorisé) | wazo-plugind (`rules`, `debian_depends`), version du paquet Debian |
| 0.2 | Répertoire JSON inscriptible par l'utilisateur de confd | service systemd / utilisateur wazo-confd |
| 0.3 | Tenant + contrôle d'accès au schedule dans un plugin | `helpers/restful.py` (`_add_tenant_uuid`), `helpers/resource.py` |
| 0.4 | Fuseau par défaut (`infos.timezone`) | xivo-dao `infos` |
| 0.5 | Tâche quotidienne : timer systemd + CLI + `wazo-auth-keys` | plugin tiers existant (`wazo-calld-application-continue`) |
| 0.6 | Destination `user` copiée acceptée par la validation | test en phase 3 |
| 0.7 | Unicité des clés `en_US` par pays/année | script FR, NL, autres, 2026-2035 |

## Phase 1 — squelette
`wazo/plugin.yml` (namespace `mwolff44`, `min_wazo_version: 26.10`), `wazo/rules`
(build/package/install/uninstall + restart wazo-confd), `setup.py`, `etc/wazo-confd/conf.d/holidays.yml`.
Critère : confd démarre avec le plugin activé.

## Phase 2 — calendrier (sans confd)
`calendar.py` : `list_countries()`, `list_subdivisions(code)` (sans Zone A/B/C),
`holidays_for(code, subdiv, year, language)` → `[{key, name, date}]`.
Tests : Pâques 2026 = 5 avril ; Lundi de Pâques NL 2027 = 29 mars ; `6AE` contient le Vendredi saint,
`FR` seul non ; pays inconnu → erreur claire.

## Phase 3 — cœur de la synchro (pur)
`compute_desired(config, today, tz)` ; `diff(periods, old_managed, desired, destination)`.
Tests : fenêtre (veille / jour même / J+365), exclusions, période manuelle identique hors `managed`
non touchée, idempotence, changement de destination propagé, changement de pays.

## Phase 4 — stockage
Lecture/écriture atomique, validation `version` et `tenant_uuid`, suppression, nettoyage des orphelins.
Tests : répertoire temporaire, fichier corrompu/absent.

## Phase 5 — API
| Route | ACL |
|---|---|
| `GET /schedule-holidays/countries` | `confd.schedule-holidays.read` |
| `GET /schedule-holidays/countries/{code}?subdivision=&year=&language=` | idem |
| `GET/PUT/DELETE /schedules/{id}/holidays` | `confd.schedules.{id}.holidays.read/update/delete` |
| `POST /schedules/{id}/holidays/sync` (`?dry_run=true`) | `confd.schedules.{id}.holidays.update` |
| `POST /schedule-holidays/sync` (tous les schedules configurés) | `confd.schedule-holidays.sync.create` |

`PUT` valide pays, sous-division, clés exclues. `DELETE` retire aussi les périodes gérées.
Erreurs au format confd ; `api.yml` documenté.

## Phase 6 — planification et packaging
Timer systemd quotidien + CLI `wazo-schedule-holidays-sync` ; `wazo-auth-keys` ; test d'installation
via `wazo-plugind-cli` sur 26.10.

## Phase 7 — bout en bout et docs
VM 26.10 : schedule, `PUT` NL, sync, `GET /schedules/{id}`, appel simulé un jour férié. README.

## Limites à documenter
- Config JSON non répliquée en HA ni sauvegardée avec la DB.
- Une période manuelle identique à une période gérée serait supprimée à la désélection.
- Si la synchro échoue plus d'un an, un férié périmé pourrait se redéclencher.
- Les noms de pays ne viennent pas de la lib (codes seulement) : à localiser côté UI.
