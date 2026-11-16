import os
import requests
import sys
import subprocess

# Configuración
REPO = "NITREWRITTEN"
DESCRIPTION = "Professional Mod Manager for Neverwinter Nights (Enhanced Edition & Diamond)"

def get_user(token):
    url = "https://api.github.com/user"
    headers = {"Authorization": f"token {token}"}
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        return r.json()["login"]
    return None

def create_repo(token, user):
    url = "https://api.github.com/user/repos"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    data = {
        "name": REPO,
        "public": True,
        "description": DESCRIPTION,
        "auto_init": False
    }
    
    print(f"Intentando crear el repositorio '{REPO}' en la cuenta de '{user}'...")
    r = requests.post(url, json=data, headers=headers)
    
    if r.status_code == 201:
        repo_data = r.json()
        clone_url = repo_data["clone_url"]
        print(f"Repositorio creado: {clone_url}")
        return clone_url
    elif r.status_code == 422:
        print(f"El repositorio '{REPO}' ya existe.")
        return f"https://github.com/{user}/{REPO}.git"
    else:
        print(f"Error al crear el repositorio (Codigo {r.status_code}): {r.text}")
        return None

def setup_git_remote(clone_url):
    try:
        # Usar el token en la URL de clonado para autenticacion automatica
        # Formato: https://<token>@github.com/user/repo.git
        auth_url = clone_url.replace("https://", f"https://{os.environ.get('GITHUB_TOKEN')}@")

        # Intentar añadir el remoto, si ya existe lo actualizamos
        subprocess.run(["git", "remote", "add", "origin", auth_url], check=False, capture_output=True)
        subprocess.run(["git", "remote", "set-url", "origin", auth_url], check=True)
        print("Remoto 'origin' configurado.")
        
        # Renombrar rama a main si es master
        subprocess.run(["git", "branch", "-M", "main"], check=True)
        
        print("Subiendo codigo a GitHub (rama main)...")
        result = subprocess.run(["git", "push", "-u", "origin", "main", "--force"], capture_output=True, text=True)
        if result.returncode == 0:
            print("Codigo subido con exito.")
        else:
            print(f"Error al subir el codigo: {result.stderr}")

        print("Creando y subiendo etiqueta de version v1.0.0 para disparar el Release...")
        subprocess.run(["git", "tag", "v1.0.0", "--force"], check=False)
        result = subprocess.run(["git", "push", "origin", "v1.0.0", "--force"], capture_output=True, text=True)
        if result.returncode == 0:
            print("Etiqueta v1.0.0 subida. El Release se generara automaticamente en GitHub Actions.")
        else:
            print(f"Error al subir la etiqueta: {result.stderr}")

    except Exception as e:
        print(f"Error en la configuracion de Git: {e}")

if __name__ == "__main__":
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("\n" + "="*50)
        print("ERROR: No se encontró la variable de entorno GITHUB_TOKEN.")
        print("Por favor, ejecuta lo siguiente antes de este script:")
        print("  $env:GITHUB_TOKEN = 'tu_token_personal_de_github'")
        print("="*50 + "\n")
        sys.exit(1)
    
    user = get_user(token)
    if not user:
        print("Error: No se pudo obtener el usuario del token.")
        sys.exit(1)
        
    url = create_repo(token, user)
    if url:
        setup_git_remote(url)
