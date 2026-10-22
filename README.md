# sshget

Descarga archivos o carpetas completas desde cualquier servidor SSH al directorio actual, con un navegador interactivo. Sustituye el flujo de entrar con `ssh`, copiar a `~/copyfolder`, salir y usar `scp`.

macOS es la plataforma principal; también funciona en Linux. Solo autenticación con llaves SSH (incluye `ssh-agent`).

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

## Uso

### Navegador interactivo

```bash
sshget myserver
sshget user@host
sshget user@host:2222
```

`myserver` puede ser un alias de `~/.ssh/config`. El navegador lista el directorio remoto inicial (normalmente el home), muestra tipo (`dir` / `file`) y tamaño, y descarga lo marcado al directorio de trabajo local.

### Descarga directa (sin navegador)

```bash
sshget myserver:/var/log/syslog
sshget myserver:/var/www ./backup
sshget user@host:2222:/tmp/data ./data
```

Si la ruta remota es una carpeta, se copia de forma recursiva (equivalente a `scp -r`).

### Archivos locales que ya existen

Por defecto pregunta qué hacer. Puedes fijar la política:

```bash
sshget -y myserver:/etc/hosts          # sobrescribir
sshget --skip-existing myserver:notes.txt
sshget --existing overwrite myserver:notes.txt
```

## Teclas del navegador

| Tecla | Acción |
| --- | --- |
| `↑` / `↓` | Mover el cursor |
| `Enter` | Entrar en una carpeta; en un archivo, descargarlo |
| `→` | Entrar en la carpeta seleccionada |
| `⌫` / `←` | Subir al padre (o borrar el filtro si hay texto) |
| letras | Filtrar por nombre (búsqueda incremental) |
| `Esc` | Limpiar el filtro; si ya está vacío, salir |
| `Espacio` | Marcar / desmarcar archivos o carpetas |
| `d` o `Ctrl+G` | Descargar lo marcado (o la fila actual si no hay marcas) |
| `q` / `Ctrl+C` | Salir |

`d` y `q` son atajos solo con el filtro vacío; si estás filtrando, esas letras forman parte de la búsqueda. `Ctrl+G` descarga siempre.

## Equivalente con `scp`

Esto:

```bash
sshget user@host:/ruta/remota ./
```

hace lo mismo, a grandes rasgos, que:

```bash
scp -r user@host:/ruta/remota ./
```

La diferencia es que `sshget` usa SFTP, muestra progreso, pregunta antes de sobrescribir, y puede abrir un navegador cuando no pasas la ruta.

## Autenticación y `~/.ssh/config`

Se respetan `HostName`, `User`, `Port`, `IdentityFile`, `IdentitiesOnly` y `ProxyJump` (también encadenado). Las llaves se toman del agente y de los `IdentityFile` habituales.

Las host keys se comprueban contra `known_hosts`. Un host desconocido **no** se acepta en silencio: en un TTY se pregunta `yes/no` como `ssh`; sin TTY la conexión falla. Si la clave cambió, se aborta con el aviso de identificación remota.

No hay login por contraseña.

## Desarrollo

```bash
pip install -e ".[dev]"
pytest
```
