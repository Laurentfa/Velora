"""Test lecture seule de l'accès EWS (Exchange hébergé Opale Center).

Usage : .venv/bin/python scripts/test_ews.py [identifiant]
Le mot de passe est demandé en saisie masquée, jamais stocké.
"""
import sys
from getpass import getpass

from exchangelib import Account, Configuration, Credentials, DELEGATE

def main() -> None:
    email = input("Adresse e-mail Exchange : ").strip()
    login = sys.argv[1] if len(sys.argv) > 1 else (input("Identifiant (vide = l'adresse) : ").strip() or email)
    server = input("Serveur EWS (vide = détection automatique) : ").strip()
    creds = Credentials(login, getpass(f"Mot de passe pour {login} : "))
    if server:
        account = Account(email, config=Configuration(server=server, credentials=creds), autodiscover=False, access_type=DELEGATE)
    else:
        account = Account(email, credentials=creds, autodiscover=True, access_type=DELEGATE)
    inbox = account.inbox
    print(f"OK - Boîte de réception : {inbox.total_count} mails, {inbox.unread_count} non lus")
    for m in inbox.all().order_by("-datetime_received")[:3]:
        print(f"  {m.datetime_received:%d/%m %H:%M}  {m.sender.email_address if m.sender else '?'}  {m.subject}")


if __name__ == "__main__":
    main()
