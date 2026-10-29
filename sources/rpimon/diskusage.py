"""Occupation du disque : qui prend la place sur la partition suivie ?

scan()       mesure la place réellement occupée (blocs alloués, comme `du`) de chaque dossier,
             sans changer de système de fichiers ni suivre les liens symboliques ;
breakdown()  choisit quelques parts parlantes en descendant dans les dossiers dominants
             (/home -> /home/yunohost.app -> /home/yunohost.app/nextcloud...).

Le scan doit pouvoir tout lire : il tourne dans un service à part (rpimon-disk), avec la seule
capacité CAP_DAC_READ_SEARCH, ou via `sudo rpimon disk-scan`.
"""
from __future__ import annotations

import os
import shutil
import stat
import time

MAX_DEPTH = 5            # profondeur des dossiers dont on garde la taille
MAX_SLICES = 8           # parts du camembert (une couleur chacune), hors « Autres » et « Libre »
EXPAND_SHARE = 0.04     # on détaille tout conteneur d'au moins 4 % de l'utilisé...
MIN_SHARE = 0.02         # ...en parts d'au moins 2 % ; on garde les MAX_SLICES plus grosses
                         # feuilles, le reste va dans « Autres »

# Emplacements connus, en chemins relatifs à la partition : (motif, libellé, feuille).
# Une feuille est un emplacement parlant qu'on ne découpe pas davantage (les données d'une app,
# les sauvegardes...) ; les autres sont des conteneurs à détailler. {name} = dernier élément.
LABELS = [
    ("/home/yunohost.backup", "Sauvegardes YunoHost", True),
    ("/home/yunohost.app/*", "Données de l'app {name}", True),
    ("/home/yunohost.app", "Données des apps", False),
    ("/home/yunohost.multimedia", "Multimédia partagé", True),
    ("/var/www/*", "Code de l'app {name}", True),
    ("/var/www", "Code des apps", False),
    ("/opt/node_n", "Node.js (versions installées)", True),
    ("/opt/yunohost/*", "Environnement de l'app {name}", True),
    ("/opt", "Logiciels (/opt)", False),
    ("/usr", "Système (programmes)", True),
    ("/var/log/journal", "Journal systemd", True),
    ("/var/log", "Journaux", False),
    ("/var/cache/apt", "Cache apt (paquets téléchargés)", True),
    ("/var/cache", "Caches", False),
    ("/var/lib/mysql", "Bases MariaDB", True),
    ("/var/lib/postgresql", "Bases PostgreSQL", True),
    ("/var/lib/mongodb", "Bases MongoDB", True),
    ("/var/lib/redis", "Bases Redis", True),
    ("/var/lib/fail2ban", "Base fail2ban", True),
    ("/var/lib/docker", "Docker", True),
    ("/var/lib", "Données système", False),
    ("/var/mail", "Mails locaux", True),
    ("/var/swap", "Fichier d'échange (swap)", True),
    ("/swapfile", "Fichier d'échange (swap)", True),
    ("/home/*", "Dossier de {name}", True),
    ("/root", "Dossier de root", True),
]


def _blocks(st):
    b = getattr(st, "st_blocks", None)
    return b * 512 if b is not None else st.st_size


def scan(root, max_depth=MAX_DEPTH):
    """{chemin: octets} pour chaque dossier jusqu'à max_depth, + nb d'entrées illisibles."""
    root = os.path.abspath(root)
    dev = os.lstat(root).st_dev
    sizes, seen = {}, set()
    errors = 0

    def walk(path, depth):
        nonlocal errors
        try:
            total = _blocks(os.lstat(path))
            it = os.scandir(path)
        except OSError:
            errors += 1
            return 0
        with it:
            for e in it:
                try:
                    st = os.lstat(e.path)     # pas DirEntry.stat() : st_dev/st_ino à 0 sous Windows
                except OSError:
                    errors += 1
                    continue
                if st.st_dev != dev:                  # autre système de fichiers (/proc, /boot...)
                    continue
                if stat.S_ISDIR(st.st_mode):
                    total += walk(e.path, depth + 1)
                    continue
                if st.st_nlink > 1:                   # lien physique : compté une seule fois
                    if (st.st_ino, st.st_dev) in seen:
                        continue
                    seen.add((st.st_ino, st.st_dev))
                total += _blocks(st)
        if depth <= max_depth:
            sizes[path] = total
        return total

    walk(root, 0)
    return sizes, errors


def _children(sizes, path):
    prefix = path.rstrip(os.sep) + os.sep
    return [p for p in sizes if p != path and p.startswith(prefix) and os.sep not in p[len(prefix):]]


def _logical(path, root):
    """Chemin vu depuis la racine de la partition, toujours avec des « / »."""
    rel = os.path.relpath(path, root)
    return "/" if rel == "." else "/" + rel.replace(os.sep, "/")


def describe(logical):
    """(libellé, feuille ?) d'un chemin relatif à la partition."""
    name = logical.rstrip("/").rsplit("/", 1)[-1] or "/"
    for pattern, text, leaf in LABELS:
        if pattern.endswith("/*"):
            if logical.rsplit("/", 1)[0] == pattern[:-2]:
                return text.format(name=name), leaf
        elif logical == pattern:
            return text.format(name=name), leaf
    return logical, False


def breakdown(sizes, root, used, max_slices=MAX_SLICES):
    """Parts parlantes de l'espace utilisé : [{path, label, size}], la dernière = « Autres ».

    On part des dossiers de la racine et on détaille chaque conteneur qui pèse au moins
    EXPAND_SHARE de l'utilisé (/home -> /home/yunohost.app -> une part par app), jusqu'aux
    emplacements parlants ; on garde ensuite les max_slices plus grosses parts.
    """
    root = os.path.abspath(root)
    scanned = sizes.get(root, 0)
    base = max(used, scanned, 1)

    def node(p):                       # [chemin, taille, détaillable, reste d'un dossier détaillé]
        return [p, sizes[p], not describe(_logical(p, root))[1], False]

    items = [node(p) for p in _children(sizes, root)]
    rest = scanned - sum(it[1] for it in items)            # fichiers posés à la racine

    while True:
        items.sort(key=lambda it: -it[1])
        cand = next((it for it in items if it[2] and it[1] >= EXPAND_SHARE * base), None)
        if cand is None:
            break
        cand[2] = False
        big = [node(p) for p in _children(sizes, cand[0]) if sizes[p] >= MIN_SHARE * base]
        remainder = cand[1] - sum(k[1] for k in big)
        if not big:
            continue                                       # rien de parlant dessous
        items.remove(cand)
        items += big
        if remainder >= MIN_SHARE * base:
            items.append([cand[0], remainder, False, True])
        else:
            rest += remainder

    items.sort(key=lambda it: -it[1])
    out, others = [], rest + max(0, used - scanned)        # écart df/scan : métadonnées, fichiers ouverts
    for path, size, _, is_rest in items:
        if size >= MIN_SHARE * base and len(out) < max_slices:
            text = describe(_logical(path, root))[0]
            out.append({"path": path, "size": size, "label": "%s (reste)" % text if is_rest else text})
        else:
            others += size
    if others > 0:
        out.append({"path": None, "size": others, "label": "Autres"})
    return out


def snapshot(mount):
    """Scan complet de la partition qui porte `mount`, prêt à stocker en JSON."""
    t0 = time.time()
    total, used, free = shutil.disk_usage(mount)       # comme df : free = disponible hors réserve root
    sizes, errors = scan(mount)
    return {
        "ts": int(t0), "mount": mount, "total": total, "used": used, "free": free,
        "reserved": total - used - free, "scanned": sizes.get(os.path.abspath(mount), 0),
        "errors": errors, "duration": round(time.time() - t0, 1),
        "items": breakdown(sizes, mount, used),
    }
