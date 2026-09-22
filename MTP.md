# Walkman MTP — pourquoi ça coince et quoi faire

## Ce qui a mal marché

1. **Session MTP qui se corrompt** — Après beaucoup de copies, `libmtp` renvoie *Could not send object property list* / I/O error. Les commandes suivantes échouent toutes (d’où ~10 morceaux au lieu de 99).
2. **Mauvais outils pour ce montage** — `shutil.copy` / `rsync` sur `gvfs/mtp:` ne marchent pas (mkstemp, Operation not supported). Il faut **`gio copy`** et **`gio remove`**.
3. **Noms de fichiers** — `&`, apostrophes, crochets `[]` et chemins très longs font échouer certaines copies.
4. **Trop de `mkdir` lents** — Créer l’arborescence album par album via MTP sans remontage régulier fatigue la session.

## Bonnes pratiques (appareil)

- Écran **déverrouillé**, mode USB **Transfert de fichiers** (MTP).
- Si tu peux : mode **Stockage de masse (MSC)** sur le Walkman → copie classique, bien plus fiable sous Linux.
- Avant une grosse synchro : débrancher 5 s, rebrancher.

## Nouveau flux recommandé

```bash
cd /path/to/sc-likes-to-walkman
python3 mtp_sync.py --remount
```

- Reconstruit l’export MTP-safe dans `/tmp/walkman-mtp-export/MUSIC`
- **Remonte** le volume MTP au début
- **Remonte tous les 12 fichiers** (variable `MTP_REMOUNT_EVERY`)
- **Vérifie la taille** de chaque fichier après copie
- **Reprend** où ça s’est arrêté (`mtp_sync_state.json`)

Relancer après échec :

```bash
python3 mtp_sync.py --remount
```

## Ancienne commande

`WALKMAN_SYNC_ONLY=1 python3 transfer_walkman.py` reste utilisable mais **`mtp_sync.py`** gère mieux la connexion.
