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
| 0.1 | Livraison de `holidays` | **Résolu** : apt inutilisable ; option A, copie vendorisée (voir résultats) |
| 0.2 | Répertoire JSON inscriptible par l'utilisateur de confd | **Résolu** : `www-data` |
| 0.3 | Tenant + contrôle d'accès au schedule | **Résolu** |
| 0.4 | Fuseau par défaut | **Résolu** : `xivo_dao.resources.infos.dao.get().timezone` |
| 0.5 | Tâche quotidienne | **Résolu** : `cron.d` + CLI sur le modèle de `wazo-confd-purge-meetings` |
| 0.6 | Destination `user` copiée | **Résolu par lecture** (réserve : test réel en phase 7) |
| 0.7 | Unicité des clés `en_US` | **Résolu** : 0 doublon pour NL et FR |

### Résultats de la phase 0 (code lu : confd, xivo-dao, wazo-agid, wazo-plugind, tag wazo-26.10 / HEAD 26.11)

- **0.1 — dépendance.**
  - `wazo-plugind` gère `debian_depends` (paquets apt) et `depends` (autres plugins Wazo). Aucune gestion de dépendances pip trouvée.
  - Wazo est basé sur Debian 12 (bookworm) depuis la 25.15 ; je n'ai rien trouvé indiquant un changement pour 26.10 (à confirmer sur une 26.10 réelle).
  - Debian : `python3-holidays` = **0.10.1** (oldstable/bookworm), 0.71 (stable/trixie), 0.97 (testing). Les libellés de suites viennent de tracker.debian.org, non nommés par codename : à recouper.
  - **La 0.10.1 est incompatible avec le plan** (testée) : pas de `country_holidays`, pas de `language`, pas de sous-division ISO `6AE`/`57` ; la France est `holidays.France(prov='Alsace-Moselle')` avec noms français.
  - `holidays` récent (0.109) exige Python >= 3.11 = `X-Python3-Version` de confd. 14 Mo installé.
  - **Décision (validée) : option A, copie vendorisée dans le dépôt.** Pas de pip ni de réseau au build.
    - `wazo/rules package` copie `vendor/holidays/` dans `${pkgdir}/usr/lib/wazo-schedule-holidays/vendor/` (tout ce qui est dans `${pkgdir}` entre dans le `.deb` construit par plugind avec `dpkg-deb`).
    - Le plugin et le CLI ajoutent ce répertoire en tête de `sys.path`.
    - Le paquet généré s'appelle `wazo-plugind-<nom>-<namespace>` : pas de conflit avec un `python3-holidays` d'apt.
    - Taille mesurée hors `__pycache__` : ~7,4 Mo (4 Mo de `.py`, 3,5 Mo de `.mo`), mesure `du` en blocs donc majorée.
    - Version figée (`requirements-vendor.txt`, ex. `holidays==0.109`) ; `make vendor` la (re)télécharge, retire `__pycache__` et copie la licence MIT (`dist-info/licenses/LICENSE`). Mise à jour de la lib = nouvelle version du plugin.
    - `holidays` importe `dateutil.parser` à l'import, qui dépend de `six` ; `python-dateutil` n'est pas dans les dépendances de confd (non vérifié sur machine) : **on vendorise aussi `python-dateutil` et `six`** (~0,9 Mo).
    - `sys.path` : le répertoire `vendor` est ajouté en tête **uniquement pendant l'import**, puis retiré, pour ne pas modifier durablement le chemin du processus confd. Si un `holidays` incompatible est déjà chargé (pas de `country_holidays`), erreur explicite.
    - Non lu : le gabarit `control` et le script d'installation root de plugind (mise à jour d'un plugin déjà installé).
- **0.2 — utilisateur.** confd fait `change_user('www-data')` au démarrage (`config['user']`). Le répertoire JSON doit appartenir à `www-data` ; `rules install` le crée.
- **0.3 — tenant.**
  - Les ressources héritent de `ConfdResource` (`auth_verifier.verify_tenant` + `verify_token`).
  - L'accès à un schedule : `service.get(id, tenant_uuids=self._build_tenant_list({'recurse': True}))`, 404 sinon.
  - Le service d'édition ne valide que `closed_destination` : il n'y a pas de validateur par période.
- **0.4 — fuseau.** `Schedule.timezone`, sinon `Infos.timezone` via `xivo_dao.resources.infos.dao.get()`.
- **0.5 — tâche quotidienne.**
  - Précédent : `debian/cron.d` de confd (`24 3 * * * root /usr/bin/wazo-confd-purge-meetings --quiet`). Le CLI `purge_meetings.py` n'appelle pas l'API HTTP : il charge la config et la clé de service, initialise la DB, construit `BusPublisher` et `SysconfdPublisher`, puis réutilise les services confd.
  - **Conséquence** : le CLI de synchro suit ce modèle. Plus de route globale `POST /schedule-holidays/sync` ni d'ACL `wazo-auth-keys`.
  - Le notifier schedule utilise le `bus` global de confd : le CLI doit en construire un avec son `BusPublisher`.
  - Point ouvert : cron en root crée des JSON appartenant à root ; il faut `chown www-data` après écriture (ou cron en `www-data` si la clé de service est lisible, non vérifié).
- **0.6 — destination copiée.** agid construit `ScheduleAction(action, actionid, actionargs)` de la même façon pour le repli et pour une période (`objects.py`) : les champs sont équivalents. Écrit via le service (modèle), la période ne passe par aucun validateur de destination.
- **0.7 — clés.** Sur ~18 000 combinaisons pays/sous-division/année (2026-2035), 2 922 ont des noms `en_US` en double (ex. `AD`/`06`). **0** pour NL et FR, toutes sous-divisions. Règle : si un nom est en double dans une année, suffixer par rang de date (`Name #1`, `Name #2`).
- **Noms de pays** : `python3-pycountry` est déjà une dépendance de confd (`plugins/localization/schema.py`). On l'utilise pour localiser les noms de pays (limite levée).

## Phase 1 — squelette
`wazo/plugin.yml` (namespace `mwolff44`, `min_wazo_version: 26.10`), `wazo/rules`
(build/package/install/uninstall + restart wazo-confd), `vendor/` + `make vendor`, `setup.py`, `etc/wazo-confd/conf.d/holidays.yml`.
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
Lecture/écriture atomique, **verrou de fichier** (`flock`) contre deux synchros simultanées (CLI + bouton),
validation `version` et `tenant_uuid`, suppression, nettoyage des orphelins.
Tests : répertoire temporaire, fichier corrompu/absent.

## Phase 5 — API
| Route | ACL |
|---|---|
| `GET /schedule-holidays/countries` | `confd.schedule_holidays.read` |
| `GET /schedule-holidays/countries/{code}?subdivision=&year=&language=` | `confd.schedule_holidays.read` |
| `GET/PUT/DELETE /schedules/{id}/holidays` | `confd.schedules.{id}.holidays.read/update/delete` |
| `POST /schedules/{id}/holidays/sync` (`?dry_run=true`) | `confd.schedules.{id}.holidays.sync.create` |

`PUT` valide pays, sous-division, clés exclues. `DELETE` retire aussi les périodes gérées.
Erreurs au format confd ; `api.yml` documenté.

## Phase 6 — planification et packaging
`etc/cron.d/` quotidien + CLI `wazo-schedule-holidays-sync` (modèle `wazo-confd-purge-meetings` :
config + clé de service, DB, `BusPublisher`/`SysconfdPublisher`, services confd, pas d'appel HTTP) ;
`chown www-data` des JSON ; `rules package` copie `vendor/` dans `${pkgdir}` ;
test d'installation via `wazo-plugind-cli` sur 26.10.

## Phase 7 — bout en bout et docs
VM 26.10 : schedule, `PUT` NL, sync, `GET /schedules/{id}`, appel simulé un jour férié. README.

## Limites à documenter
- Config JSON non répliquée en HA ni sauvegardée avec la DB.
- Une période manuelle identique à une période gérée serait supprimée à la désélection.
- Si la synchro échoue plus d'un an, un férié périmé pourrait se redéclencher.
- Les noms de pays viennent de `pycountry` (dépendance déjà présente dans confd).
- La lib `holidays` est vendorisée (non fournie par apt sur bookworm) : mise à jour = nouvelle version du plugin.

## Conventions et outillage (validé, modèle : `wazo-calld-queue`)
- Licence **GPL-3.0+** (cohérence avec confd et l'écosystème Wazo) ; en-tête `Copyright 2026 The Wazo Authors  (see the AUTHORS file)` + `SPDX-License-Identifier: GPL-3.0+` dans chaque fichier Python.
- `pyproject.toml` (black, isort, flake8, mypy), `.pre-commit-config.yaml` (dont `wazo-copyright-check`), `tox.ini`, `requirements-test.txt`, CI GitHub (linters + pytest, Python 3.11). `vendor/` exclu de tous les outils.
- Commits conventionnels (`feat:`, `fix:`, `docs:`, `chore:`, `test:`).
- `AGENTS.md` : structure et invariants (signature des périodes, fenêtre de 365 jours, JSON atomique + verrou).
- Le cœur (calendrier, synchro, stockage) n'importe rien de Wazo, donc testable sans stubs ; seuls `plugin.py`, `resources.py` et le CLI importent `wazo_confd`/`xivo_dao`.

## Avancement
- [x] Phase 0 : vérifications
- [~] Phase 1 : squelette plugin + `wazo/rules` écrits ; `build`+`package` rejoués ici et paquet vérifié autonome ; chargement par confd à tester sur une stack 26.10
- [x] Phase 2 : calendrier (`holiday_calendar.py`, `_vendor.py`)
- [x] Phase 3 : cœur de synchro (`sync.py`)
- [x] Phase 4 : stockage JSON, écriture atomique, `flock` (`storage.py`)
- [x] Phase 5 : API confd (resources, schémas, service, `api.yml`), testée par des tests de contrat contre confd/xivo-dao 26.10 (sans DB ni auth réelles)
- [x] Phase 6 : CLI + `cron.d` (`runner.py`, `wiring.py`, `cli.py`), testés unitairement et par contrat contre confd/xivo-dao 26.10
- [~] Phase 7 : README et checklist de test écrits ; bout en bout à faire sur une stack 26.10

## Décisions de la phase 5
- Le service est injecté (service schedule de confd, `ScheduleTime`, fuseau par défaut, `commit`) : il reste testable sans Wazo.
- **`commit` explicite** entre la modification du schedule et l'écriture finale du JSON : confd ne commit qu'après la requête (`after_request`, puis flush sysconfd et bus). Sans cela, un commit en échec laisserait des périodes supprimées en base mais plus « gérées ».
- Écriture du JSON en deux temps (union ancien/nouveau avant la modification, état final après le commit).
- Route globale `POST /schedule-holidays/sync` supprimée (le cron passera par le CLI, phase 6). ACL de la synchro : `...holidays.sync.create`.
- Noms de pays : noms ISO anglais (`pycountry`), non localisés ; noms de sous-divisions : alias le plus long de la lib, sinon le code (heuristique).
- `api.yml` référence `#/parameters/scheduleid`, défini dans l'`api.yml` du plugin `schedule` natif de confd.

## Décisions de la phase 6
- Le CLI ne contacte jamais wazo-auth : la clé de service (`_load_key_file`) ne fournit que `auth.username/password`. Elle n'est donc pas lue, ce qui lève le point ouvert « clé lisible par l'utilisateur du cron ».
- Le CLI construit lui-même `CRUDService(schedule_dao, build_validator(), ScheduleNotifier(bus))` : le `build_service()` du plugin schedule utilise le `bus` global de confd, qui n'existe que dans une requête Flask.
- Une transaction par schedule (`session_scope`) ; événements du bus publiés (`flush`) seulement après le commit, annulés (`rollback`) en cas d'échec. Un échec n'empêche pas les autres schedules ; code de sortie 1 s'il y en a eu.
- Un schedule introuvable (supprimé) → son JSON est supprimé (« orphelin ») ; jamais en `--dry-run`.
- `cron.d` : `7 4 * * * root /usr/bin/python3 -m wazo_schedule_holidays --quiet` (une fois par jour ; la fenêtre de 365 jours laisse une marge d'un an à une exécution manquée).
- Propriétaire : le CLI (root) donne les JSON et les verrous à `www-data` (`config['user']`) pour que confd puisse les relire/écrire.

## À vérifier sur une stack Wazo 26.10 (non vérifiable ici)
- `/usr/bin/python3 -m wazo_schedule_holidays` trouve le module : dépend de l'endroit où `python3 setup.py bdist` + plugind l'installent (`/usr/lib/python3/dist-packages` ou `/usr/local/lib/...`, tous deux dans le `sys.path` de Debian) ; sinon adapter le `cron.d`.
- `rules package` : présence de `vendor/` dans `/usr/lib/wazo-schedule-holidays/vendor` et import réel de `holidays` sous confd.
- Chargement du plugin par confd (`enabled_plugins`, entry point) et apparition des routes dans `/api/api.yml`.
- Écritures réelles : base PostgreSQL, `after_request`, événements `schedule_edited` reçus par le bus, évaluation par agid d'un appel un jour férié.
- Droits : `/var/lib/wazo-schedule-holidays` en `www-data`, fichiers écrits par confd puis par le CLI root.
- Mise à jour d'un plugin déjà installé (gabarit `control` / scripts de plugind non lus).

## Packaging : vérifié ici (build + package rejoués sur une copie propre du dépôt)
- Défaut trouvé et corrigé : les setuptools récents nomment le tar `wazo_schedule_holidays-*` (tiret bas) ; le glob `wazo-schedule-holidays-*` ne trouvait rien et, sans `set -e`, `package` réussissait avec un paquet **sans code Python**. `rules` utilise maintenant `set -e` et `dist/*.tar.gz` ; un tar manquant fait échouer `package`.
- Contenu du paquet : code Python, `api.yml`, entry points (`wazo_confd.plugins`, console script), `etc/cron.d`, `etc/wazo-confd/conf.d`, `vendor/` sous `/usr/lib/wazo-schedule-holidays/vendor`.
- Le paquet construit, importé seul, charge `holidays` depuis son `vendor/` (pas celui du dépôt) et `sys.path` reste propre.
- Réserve : le préfixe d'installation du code Python dépend du `python3` qui exécute `bdist` (ici un venv 3.11) ; sur Debian il sera celui du système. Non vérifiable ici.

## Constats sur une stack Wazo 26.10 (test de l'auteur, 2026-11-18)
- Installation par `wazo-plugind-cli install git ... --ref main` OK (l'option `-c` est dépréciée).
- Le plugin est chargé : les 6 routes apparaissent dans `/api/confd/1.1/api/api.yml` à côté des routes natives `/schedules`.
- Le code Python est installé dans `/usr/local/lib/python3.11/dist-packages/wazo_schedule_holidays/` : `/usr/bin/python3 -m wazo_schedule_holidays` le trouve, le `cron.d` est donc correct sur ce point.
- `/var/lib/wazo-schedule-holidays` : `drwxr-x--- www-data www-data`, comme prévu.
- Défaut trouvé : `python3 -m wazo_schedule_holidays` plantait (`KeyError: 'user'`), car `load_config` perdait `DEFAULT_CONFIG` (`read_config_file_hierarchy` ne retourne que le contenu des fichiers). Corrigé comme dans `purge_meetings.py`. Les tests de contrat remplaçaient `load_config` par un dictionnaire complet : deux tests utilisent maintenant le vrai.
