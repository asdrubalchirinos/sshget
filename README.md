# sshget / sshput

Descarga o sube archivos y carpetas completas por SFTP, con un navegador remoto interactivo. Sustituye el flujo de entrar con `ssh`, copiar a `~/copyfolder`, salir y usar `scp`.

macOS es la plataforma principal; también funciona en Linux. Solo autenticación con llaves SSH (incluye `ssh-agent` y `-i`).

Un `pipx install` instala **los dos** comandos: `sshget` (bajar) y `sshput` (subir).

## Instalación

Con [pipx](https://pipx.pypa.io/) (recomendado):

```bash
pipx install git+https://github.com/asdrubalchirinos/sshget.git
```

Desde un clon del repositorio:

```bash
pipx install .
```

O en un entorno virtual:

```bash
pip install .
```

Requisitos: Python 3.10+, un cliente OpenSSH usable (`~/.ssh/config`, `ssh-agent`) y llaves ya configuradas para el servidor.

## sshget — descargar

### Navegador interactivo

```bash
sshget myserver
sshget user@host
sshget user@host:2222
sshget -i ~/keys/server.pem myserver
```

`myserver` puede ser un alias de `~/.ssh/config`. El navegador lista el directorio remoto inicial (normalmente el home), muestra tipo (`dir` / `file`) y tamaño, y descarga lo marcado al directorio de trabajo local.

### Descarga directa (sin navegador)

```bash
sshget myserver:/var/log/syslog
sshget myserver:/var/www ./backup
sshget user@host:2222:/tmp/data ./data
```

Si la ruta remota es una carpeta, se copia de forma recursiva.

## sshput — subir

### Navegador interactivo (elige la carpeta remota)

```bash
sshput myserver ./notes.txt ./photos
sshput -i ~/keys/server.pem user@host ./site
```

Abre el mismo navegador remoto. Entras en la carpeta de destino y pulsas `u` para subir ahí los archivos locales (las carpetas van recursivas).

### Subida directa (sin navegador)

```bash
sshput ./notes.txt myserver:/var/tmp/
sshput ./site user@host:/var/www/
sshput ./a.txt ./b.txt myserver:/tmp/inbox/
```

Si una ruta local es una carpeta, se copia de forma recursiva.

## Archivos que ya existen

Por defecto pregunta qué hacer. En `sshget` el conflicto es local; en `sshput`, remoto.

```bash
sshget -y myserver:/etc/hosts
sshput --skip-existing ./notes.txt myserver:/tmp/
sshput --existing overwrite ./site myserver:/var/www/
```

## Teclas del navegador

| Tecla | sshget | sshput |
| --- | --- | --- |
| `↑` / `↓` | Mover el cursor | igual |
| `Enter` | Entrar en carpeta; en un archivo, descargarlo | Entrar en carpeta |
| `→` | Entrar en la carpeta | igual |
| `⌫` / `←` | Subir al padre (o borrar el filtro) | igual |
| letras | Filtrar por nombre | igual |
| `Esc` | Limpiar filtro; si está vacío, salir | igual |
| `Espacio` | Marcar / desmarcar | — |
| `d` o `Ctrl+G` | Descargar lo marcado | — |
| `u` o `Ctrl+G` | — | Subir a **esta** carpeta |
| `q` / `Ctrl+C` | Salir | igual |

`d`, `u` y `q` son atajos solo con el filtro vacío; si estás filtrando, esas letras forman parte de la búsqueda. `Ctrl+G` aplica siempre (bajar o subir según el comando).

## Equivalente con `scp`

Bajar:

```bash
sshget user@host:/ruta/remota ./
# ≈
scp -r user@host:/ruta/remota ./
```

Subir:

```bash
sshput ./local user@host:/ruta/
# ≈
scp -r ./local user@host:/ruta/
```

La diferencia es que estos comandos usan SFTP, muestran progreso, preguntan antes de sobrescribir, y pueden abrir un navegador cuando no pasas la ruta remota.

## Autenticación y `~/.ssh/config`

Se respetan `HostName`, `User`, `Port`, `IdentityFile`, `IdentitiesOnly` y `ProxyJump` (también encadenado). Las llaves se toman del agente y de los `IdentityFile` habituales.

`-i` / `--identity PATH` añade una llave concreta (por ejemplo un `.pem`), como `ssh -i`.

Las host keys se comprueban contra `known_hosts`. Un host desconocido **no** se acepta en silencio: en un TTY se pregunta `yes/no` como `ssh`; sin TTY la conexión falla. Si la clave cambió, se aborta con el aviso de identificación remota.

No hay login por contraseña.

## Desarrollo

```bash
pip install -e ".[dev]"
pytest
```
