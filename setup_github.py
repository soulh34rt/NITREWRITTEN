import os
import requests
import sys

# Nota: Este script es una guia para el usuario, ya que yo no tengo acceso a tokens de GitHub
# El usuario debe configurar su GITHUB_TOKEN en el entorno.

TOKEN = os.environ.get("GITHUB_TOKEN")
REPO = "NITREWRITTEN"
USER = "jgbed" # Asumiendo el usuario por el path

def create_repo():
    url = "https://api.github.com/user/repos"
    headers = {"Authorization": f"token {TOKEN}", "Accept": "application/vnd.github.v3+json"}
    data = {"name": REPO, "public": True, "description": "Professional Mod Manager for Neverwinter Nights"}
    
    r = requests.post(url, json=data, headers=headers)
    if r.status_code == 201:
        print(f"Repositorio {REPO} creado exitosamente.")
        return True
    else:
        print(f"Error al crear el repositorio: {r.text}")
        return False

if __name__ == "__main__":
    if not TOKEN:
        print("GITHUB_TOKEN no encontrado en el entorno.")
        sys.exit(1)
    create_repo()
