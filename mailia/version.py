APP_VERSION = "1.0.0"  # remplacé automatiquement par le numéro du tag lors d'une publication (voir .github/workflows/release.yml)
UPDATE_REPO = "Laurentfa/Velora"  # dépôt GitHub des versions publiées ; vide = mise à jour automatique désactivée
def release_message(version: str, name: str, digest_hex: str) -> bytes:
    """Ce que la clé de publication signe pour chaque fichier d'une version : la VERSION, le NOM du fichier et son empreinte. Une signature ne vaut donc que pour cette
    version : on ne peut pas faire passer une ancienne version signée (peut-être vulnérable) pour une nouvelle, ni un fichier pour un autre."""
    return f"velora-release/1\nversion={version}\nname={name}\nsha256={digest_hex}".encode("utf-8")


UPDATE_PUBKEY = "rJnzMUYA3VwmW+ZssbBI/EM08VZdwjVEmtRL/Eioi1U="  # clé publique de vérification des mises à jour (la clé privée reste chez vous)
