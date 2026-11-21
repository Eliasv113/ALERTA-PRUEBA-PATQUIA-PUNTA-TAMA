import os
import json
import html
import hashlib
import base64
import mimetypes
import requests
import xml.etree.ElementTree as ET

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urljoin, urlparse


# =========================================================
# CONFIGURACIÓN
# =========================================================

RSS_URL = "https://ssl.smn.gob.ar/CAP/AR.php"
HISTORIAL_ARCHIVO = "historial_alertas.json"

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHANNEL_ID")

ZONA_HORARIA = ZoneInfo("America/Argentina/Buenos_Aires")

# Lista permitida de localidades monitoreadas.
# Si se cambia esta lista, también hay que actualizar LOCALIDADES_PERMITIDAS.
LOCALIDADES_PERMITIDAS = {
    "Patquía",
    "Punta de los Llanos",
    "Tama",
}

LOCALIDADES = {
    "Patquía": (-30.0468, -66.8823),
    "Punta de los Llanos": (-30.1455, -66.5262),
    "Tama": (-30.5058, -66.2952),
}

# Protección: evita ejecutar el sistema si se agregó accidentalmente
# una localidad distinta de las tres autorizadas.
if set(LOCALIDADES) != LOCALIDADES_PERMITIDAS:
    raise RuntimeError(
        "La configuración de LOCALIDADES no coincide con la lista permitida. "
        f"Configuradas: {sorted(LOCALIDADES)}; "
        f"permitidas: {sorted(LOCALIDADES_PERMITIDAS)}"
    )
# Límite conservador para las imágenes enviadas como fotos a Telegram.
# Si la imagen supera este tamaño o no puede descargarse, se intenta
# publicar el enlace directo en lugar de adjuntar el archivo.
MAX_BYTES_IMAGEN = 9_500_000
TIMEOUT_DESCARGA_IMAGEN = (10, 25)
EXTENSIONES_IMAGEN = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
MIMES_IMAGEN_TELEGRAM = {"image/jpeg", "image/png"}

CABECERAS_HTTP = {
    "User-Agent": "SMN-AlertasTelegram/1.0",
    "Accept": "image/jpeg,image/png,image/webp,image/*,*/*;q=0.8",
}

CABECERAS_SMN = {
    "User-Agent": "SMN-AlertasTelegram/1.1 (+monitoreo informativo)",
    "Accept": "application/xml, text/xml, application/rss+xml, */*;q=0.8",
    "Cache-Control": "no-cache, no-store, max-age=0",
    "Pragma": "no-cache",
}


def ahora_utc():
    return datetime.now(timezone.utc)


def registrar_demora_publicacion(fuente, fecha_publicacion):
    """Registra la diferencia entre la fecha declarada por la fuente y la detección."""
    if not fecha_publicacion or fecha_publicacion == "No especificada":
        print(f"[{fuente}] La fuente no informó una fecha de publicación interpretable.")
        return
    try:
        valor = fecha_publicacion.strip().replace("Z", "+00:00")
        publicada = datetime.fromisoformat(valor)
        if publicada.tzinfo is None:
            publicada = publicada.replace(tzinfo=timezone.utc)
        demora = (ahora_utc() - publicada.astimezone(timezone.utc)).total_seconds()
        print(
            f"[{fuente}] Publicación declarada: {publicada.isoformat()} | "
            f"detectada UTC: {ahora_utc().isoformat()} | "
            f"antigüedad al detectar: {demora:.1f} segundos"
        )
    except (ValueError, TypeError, OverflowError):
        print(f"[{fuente}] Fecha de publicación sin formato ISO reconocible: {fecha_publicacion}")


def consultar_url_smn(url, timeout, nombre_fuente):
    """Consulta una fuente del SMN y registra tiempos y cabeceras de respuesta."""
    inicio = ahora_utc()
    print(f"[{nombre_fuente}] Inicio de consulta UTC: {inicio.isoformat()} | URL: {url}")
    respuesta = requests.get(url, headers=CABECERAS_SMN, timeout=timeout)
    fin = ahora_utc()
    print(
        f"[{nombre_fuente}] Respuesta HTTP {respuesta.status_code} en "
        f"{(fin - inicio).total_seconds():.2f} s | "
        f"Date={respuesta.headers.get('Date', 'no informado')} | "
        f"Last-Modified={respuesta.headers.get('Last-Modified', 'no informado')} | "
        f"Age={respuesta.headers.get('Age', 'no informado')} | "
        f"ETag={respuesta.headers.get('ETag', 'no informado')} | "
        f"bytes={len(respuesta.content)}"
    )
    respuesta.raise_for_status()
    return respuesta

MESES = [
    "enero", "febrero", "marzo", "abril",
    "mayo", "junio", "julio", "agosto",
    "septiembre", "octubre", "noviembre", "diciembre",
]

TRADUCCIONES = {
    "Extreme": "Extrema",
    "Severe": "Severa",
    "Moderate": "Moderada",
    "Minor": "Leve",
    "Unknown": "Desconocida",
    "Observed": "Observada",
    "Likely": "Probable",
    "Possible": "Posible",
    "Unlikely": "Improbable",
    "Immediate": "Inmediata",
    "Expected": "Esperada",
    "Future": "Futura",
    "Past": "Pasada",
}

ICONOS_SEVERIDAD = {
    "Extreme": "🚨",
    "Severe": "🔴",
    "Moderate": "🟠",
    "Minor": "🟡",
    "Unknown": "⚪",
}


# =========================================================
# FUNCIONES XML
# =========================================================

def nombre_etiqueta(elemento):
    """Devuelve el nombre de una etiqueta XML sin su espacio de nombres."""
    return elemento.tag.split("}")[-1]


def hijo(elemento, nombre):
    """Busca el primer hijo directo con el nombre indicado."""
    if elemento is None:
        return None

    for item in elemento:
        if nombre_etiqueta(item) == nombre:
            return item

    return None


def texto(elemento, nombre, predeterminado="No especificada"):
    """Obtiene el texto de un hijo XML."""
    elemento_hijo = hijo(elemento, nombre)

    if elemento_hijo is None or not elemento_hijo.text:
        return predeterminado

    return elemento_hijo.text.strip()


def todos_hijos(elemento, nombre):
    """Obtiene todos los hijos directos que coinciden con el nombre."""
    if elemento is None:
        return []

    return [
        item for item in elemento
        if nombre_etiqueta(item) == nombre
    ]


# =========================================================
# FECHAS Y TRADUCCIONES
# =========================================================

def traducir(valor):
    return TRADUCCIONES.get(valor, valor or "No especificada")


def fecha_argentina(valor):
    """Convierte una fecha CAP a hora argentina y la muestra en español."""
    if not valor or valor == "No especificada":
        return "Sin especificar"

    try:
        valor = valor.strip()

        if valor.endswith("Z"):
            valor = valor[:-1] + "+00:00"

        fecha = datetime.fromisoformat(valor)

        # Si la fecha no incluye zona horaria, CAP normalmente debe
        # interpretarse en UTC para evitar mostrar una hora incorrecta.
        if fecha.tzinfo is None:
            fecha = fecha.replace(tzinfo=timezone.utc)

        fecha = fecha.astimezone(ZONA_HORARIA)
        mes = MESES[fecha.month - 1]

        return f"{fecha.day} de {mes} - {fecha:%H:%M}"

    except (ValueError, TypeError):
        return valor


# =========================================================
# POLÍGONOS Y LOCALIDADES
# =========================================================

def punto_en_poligono(latitud, longitud, poligono):
    """Comprueba si un punto está dentro de un polígono CAP."""
    if len(poligono) < 3:
        return False

    dentro = False
    j = len(poligono) - 1

    for i in range(len(poligono)):
        lat_i, lon_i = poligono[i]
        lat_j, lon_j = poligono[j]

        cruz = (lon_i > longitud) != (lon_j > longitud)

        if cruz:
            lat_interseccion = (
                (lat_j - lat_i) * (longitud - lon_i)
                / (lon_j - lon_i)
                + lat_i
            )

            if latitud < lat_interseccion:
                dentro = not dentro

        j = i

    return dentro


def leer_poligono(valor):
    """Convierte 'lat,lon lat,lon ...' en una lista de coordenadas."""
    puntos = []

    if not valor:
        return puntos

    for par in valor.split():
        try:
            partes = par.split(",")

            if len(partes) != 2:
                continue

            latitud = float(partes[0])
            longitud = float(partes[1])
            puntos.append((latitud, longitud))

        except (ValueError, TypeError):
            continue

    return puntos


def localidades_afectadas(area):
    """Devuelve las localidades que están dentro de algún polígono del área."""
    afectadas = set()

    for elemento in todos_hijos(area, "polygon"):
        poligono = leer_poligono(elemento.text)

        for nombre, coordenadas in LOCALIDADES.items():
            if punto_en_poligono(
                coordenadas[0],
                coordenadas[1],
                poligono,
            ):
                afectadas.add(nombre)

    return afectadas


# =========================================================
# DETECCIÓN Y DESCARGA DE RECURSOS GRÁFICOS CAP
# =========================================================

def url_http_valida(url):
    """Acepta únicamente URL HTTP/HTTPS con un nombre de host."""
    if not url:
        return False

    try:
        partes = urlparse(url.strip())
        return partes.scheme.lower() in {"http", "https"} and bool(partes.netloc)
    except ValueError:
        return False


def extension_url(url):
    try:
        return os.path.splitext(urlparse(url).path)[1].lower()
    except (TypeError, ValueError):
        return ""


def parece_imagen(mime_type, url, descripcion):
    """Determina si el recurso CAP parece una foto o un gráfico."""
    mime_type = (mime_type or "").split(";")[0].strip().lower()
    descripcion = (descripcion or "").lower()

    if mime_type.startswith("image/"):
        return True

    if extension_url(url) in EXTENSIONES_IMAGEN:
        return True

    # Algunos emisores CAP no informan bien el MIME o la extensión.
    palabras_graficas = (
        "foto", "fotografía", "fotografia", "imagen", "image",
        "mapa", "map", "radar", "satélite", "satelite", "gráfico",
        "grafico", "captura",
    )
    return bool(url) and any(palabra in descripcion for palabra in palabras_graficas)


def mime_por_firma(contenido):
    """Reconoce formatos de imagen frecuentes a partir de sus bytes."""
    if contenido.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"

    if contenido.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"

    if (
        len(contenido) >= 12
        and contenido[0:4] == b"RIFF"
        and contenido[8:12] == b"WEBP"
    ):
        return "image/webp"

    if contenido.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"

    return ""


def nombre_archivo_imagen(url, mime_type):
    """Crea un nombre de archivo sencillo para la carga multipart de Telegram."""
    nombre = os.path.basename(urlparse(url or "").path)
    nombre = nombre.split("?")[0].strip()

    if not nombre or "." not in nombre:
        extension = mimetypes.guess_extension(mime_type or "") or ".jpg"
        nombre = f"recurso_smn{extension}"

    # Telegram no necesita nombres largos ni caracteres especiales.
    nombre = "".join(
        caracter if caracter.isalnum() or caracter in "._-" else "_"
        for caracter in nombre
    )
    return nombre[:100] or "recurso_smn.jpg"


def decodificar_deref_uri(valor):
    """Decodifica el contenido base64 opcional de un <derefUri> CAP."""
    if not valor:
        return None

    valor = valor.strip()
    if "," in valor and valor.lower().startswith("data:"):
        valor = valor.split(",", 1)[1]

    valor = "".join(valor.split())

    # Evita intentar decodificar un recurso enorme recibido dentro del XML.
    if len(valor) > (MAX_BYTES_IMAGEN * 4 // 3 + 16):
        print("El recurso incrustado supera el tamaño permitido; se omite la descarga.")
        return None

    try:
        contenido = base64.b64decode(valor, validate=False)
    except (ValueError, base64.binascii.Error):
        return None

    if not contenido or len(contenido) > MAX_BYTES_IMAGEN:
        return None

    return contenido


def extraer_recursos(info, url_base):
    """Extrae recursos gráficos de los bloques <resource> de una sección <info>."""
    recursos = []

    for elemento in todos_hijos(info, "resource"):
        descripcion = texto(elemento, "resourceDesc", "Recurso gráfico del SMN")
        mime_type = texto(elemento, "mimeType", "").lower()
        uri_original = texto(elemento, "uri", "")
        datos_incrustados = texto(elemento, "derefUri", "")

        url = urljoin(url_base, uri_original) if uri_original else ""
        datos = decodificar_deref_uri(datos_incrustados) if datos_incrustados else None

        # Si el CAP trae datos incrustados sin MIME, se intenta detectar el formato.
        mime_detectado = mime_por_firma(datos) if datos else ""
        mime_efectivo = mime_type or mime_detectado

        if not parece_imagen(mime_efectivo, url, descripcion) and not datos:
            continue

        if url and not url_http_valida(url):
            url = ""

        recursos.append({
            "descripcion": descripcion,
            "mime_type": mime_efectivo,
            "url": url,
            "datos": datos,
        })

    # También contempla un elemento <web> solo si apunta directamente a un
    # archivo con extensión gráfica; no trata páginas web genéricas como fotos.
    enlace_web = texto(info, "web", "")
    if enlace_web:
        enlace_web = urljoin(url_base, enlace_web)
        if url_http_valida(enlace_web) and extension_url(enlace_web) in EXTENSIONES_IMAGEN:
            recursos.append({
                "descripcion": "Imagen enlazada en la alerta del SMN",
                "mime_type": mimetypes.guess_type(enlace_web)[0] or "",
                "url": enlace_web,
                "datos": None,
            })

    # Elimina recursos repetidos por URL o, si no tienen URL, por descripción.
    unicos = []
    vistos = set()
    for recurso in recursos:
        clave = recurso["url"] or (recurso["descripcion"], len(recurso["datos"] or b""))
        if clave in vistos:
            continue
        vistos.add(clave)
        unicos.append(recurso)

    return unicos


def preparar_recurso_grafico(recurso):
    """Prepara la imagen para subirla a Telegram o devuelve su enlace alternativo."""
    url = recurso.get("url", "")
    datos = recurso.get("datos")
    mime_declarado = (recurso.get("mime_type") or "").split(";")[0].lower()

    if datos:
        mime_real = mime_por_firma(datos) or mime_declarado
        if mime_real in MIMES_IMAGEN_TELEGRAM and len(datos) <= MAX_BYTES_IMAGEN:
            return {
                "descripcion": recurso["descripcion"],
                "url": url,
                "datos": datos,
                "mime_type": mime_real,
                "nombre": nombre_archivo_imagen(url, mime_real),
            }

    if not url:
        return {**recurso, "datos": None}

    try:
        with requests.get(
            url,
            headers=CABECERAS_HTTP,
            timeout=TIMEOUT_DESCARGA_IMAGEN,
            stream=True,
            allow_redirects=True,
        ) as respuesta:
            respuesta.raise_for_status()

            largo = respuesta.headers.get("Content-Length")
            if largo:
                try:
                    if int(largo) > MAX_BYTES_IMAGEN:
                        print(f"La imagen supera el límite permitido: {url}")
                        return {**recurso, "datos": None}
                except ValueError:
                    pass

            partes = []
            total = 0
            for bloque in respuesta.iter_content(chunk_size=64 * 1024):
                if not bloque:
                    continue

                total += len(bloque)
                if total > MAX_BYTES_IMAGEN:
                    print(f"La imagen supera el límite permitido durante la descarga: {url}")
                    return {**recurso, "datos": None}

                partes.append(bloque)

            contenido = b"".join(partes)
            mime_real = mime_por_firma(contenido)
            mime_respuesta = respuesta.headers.get("Content-Type", "").split(";")[0].strip().lower()

            # Se prioriza la firma real del archivo. Esto evita enviar como foto
            # una página HTML de error que el servidor haya devuelto con HTTP 200.
            mime_final = mime_real or mime_respuesta or mime_declarado
            if mime_final not in MIMES_IMAGEN_TELEGRAM:
                print(f"El recurso no es JPEG/PNG compatible para enviarse como foto: {url}")
                return {**recurso, "datos": None}

            return {
                "descripcion": recurso["descripcion"],
                "url": url,
                "datos": contenido,
                "mime_type": mime_final,
                "nombre": nombre_archivo_imagen(url, mime_final),
            }

    except (requests.RequestException, OSError, ValueError) as error:
        print(f"No se pudo descargar la imagen ({url}): {error}")
        return {**recurso, "datos": None}


# =========================================================
# HISTORIAL DE ALERTAS
# =========================================================

def cargar_historial():
    if not os.path.exists(HISTORIAL_ARCHIVO):
        return set()

    try:
        with open(HISTORIAL_ARCHIVO, "r", encoding="utf-8") as archivo:
            datos = json.load(archivo)

        if isinstance(datos, list):
            return set(str(item) for item in datos)

        if isinstance(datos, dict):
            return set(str(item) for item in datos.get("alertas", []))

        return set()

    except (OSError, json.JSONDecodeError):
        print("No se pudo leer el historial. Se iniciará vacío.")
        return set()


def guardar_historial(historial):
    with open(HISTORIAL_ARCHIVO, "w", encoding="utf-8") as archivo:
        json.dump(
            sorted(historial),
            archivo,
            ensure_ascii=False,
            indent=2,
        )


def clave_alerta(identificador, enviado):
    """Distingue una alerta de sus actualizaciones posteriores."""
    valor = f"{identificador}|{enviado}"
    return hashlib.sha256(valor.encode("utf-8")).hexdigest()


# =========================================================
# TELEGRAM
# =========================================================

def validar_configuracion_telegram():
    if not TOKEN or not CHAT_ID:
        raise RuntimeError(
            "Faltan los secretos TELEGRAM_BOT_TOKEN o TELEGRAM_CHANNEL_ID. "
            "Configurálos en los secretos del repositorio de GitHub."
        )


def respuesta_telegram_correcta(respuesta):
    respuesta.raise_for_status()
    resultado = respuesta.json()

    if not resultado.get("ok"):
        raise RuntimeError(f"Telegram rechazó la solicitud: {resultado}")

    return resultado


def enviar_telegram(mensaje):
    """Envía el texto de la alerta a Telegram."""
    validar_configuracion_telegram()
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    inicio_envio = ahora_utc()
    respuesta = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": mensaje,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )

    resultado = respuesta_telegram_correcta(respuesta)
    fin_envio = ahora_utc()
    print(
        "Mensaje de alerta enviado correctamente a Telegram | "
        f"confirmación UTC: {fin_envio.isoformat()} | "
        f"latencia de envío: {(fin_envio - inicio_envio).total_seconds():.2f} s"
    )
    return resultado.get("result", {})


def editar_mensaje_telegram(message_id, mensaje):
    """Actualiza el mensaje inicial ACP cuando termina el OCR, sin retrasar el aviso."""
    if not message_id:
        return
    respuesta = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/editMessageText",
        data={
            "chat_id": CHAT_ID,
            "message_id": message_id,
            "text": mensaje,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    respuesta_telegram_correcta(respuesta)
    print("Mensaje ACP actualizado con la validez reconocida por OCR.")


def enviar_foto_telegram(recurso):
    """Sube una imagen al canal usando el método sendPhoto de Telegram."""
    validar_configuracion_telegram()
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    descripcion = html.escape(recurso.get("descripcion") or "Recurso gráfico del SMN")
    caption = f"🖼️ <b>Recurso del SMN:</b> {descripcion}"

    # El texto de la leyenda de una foto en Telegram tiene un límite de 1024
    # caracteres después de interpretar las entidades HTML.
    if len(caption) > 950:
        caption = caption[:947] + "..."

    mime_type = recurso.get("mime_type", "image/jpeg")
    nombre = recurso.get("nombre", "recurso_smn.jpg")

    respuesta = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "caption": caption,
            "parse_mode": "HTML",
        },
        files={"photo": (nombre, recurso["datos"], mime_type)},
        timeout=40,
    )

    respuesta_telegram_correcta(respuesta)
    print(f"Imagen enviada correctamente a Telegram: {nombre}")


def enviar_enlace_recurso(recurso):
    """Envía el enlace a una imagen cuando no se pudo adjuntar como foto."""
    url = recurso.get("url", "")
    if not url or not url_http_valida(url):
        return

    descripcion = html.escape(recurso.get("descripcion") or "Ver imagen del SMN")
    mensaje = (
        "🖼️ <b>Imagen de la alerta del SMN</b>\n"
        f"<a href=\"{html.escape(url, quote=True)}\">{descripcion}</a>"
    )

    enviar_telegram(mensaje)


def enlaces_de_respaldo(recursos_preparados):
    """Crea las líneas con enlaces de los gráficos que no se pudieron adjuntar."""
    lineas = []

    for recurso in recursos_preparados:
        if recurso.get("datos"):
            continue

        url = recurso.get("url", "")
        if not url or not url_http_valida(url):
            continue

        descripcion = html.escape(recurso.get("descripcion") or "Ver imagen del SMN")
        lineas.append(
            f"• <a href=\"{html.escape(url, quote=True)}\">{descripcion}</a>"
        )

    return lineas


# =========================================================
# CONSTRUCCIÓN DEL MENSAJE
# =========================================================

def construir_mensaje(alerta, info, localidades, recursos_sin_adjunto=None):
    evento = texto(info, "event", "Alerta meteorológica")
    severidad = texto(info, "severity", "Unknown")
    certeza = texto(info, "certainty", "Unknown")
    urgencia = texto(info, "urgency", "Unknown")

    inicio = texto(info, "onset", "")
    if not inicio or inicio == "No especificada":
        inicio = texto(info, "effective", "")

    fin = texto(info, "expires", "")
    descripcion = texto(info, "description", "")
    instrucciones = texto(info, "instruction", "")
    icono = ICONOS_SEVERIDAD.get(severidad, "⚠️")

    lineas = [
        f"{icono} <b>ALERTA METEOROLÓGICA DEL SMN</b>",
        "",
        f"🌦️ <b>Fenómeno:</b> {html.escape(evento)}",
        f"📍 <b>Localidades:</b> {html.escape(', '.join(sorted(localidades)))}",
        f"{icono} <b>Severidad:</b> {html.escape(traducir(severidad))}",
        f"🎯 <b>Certeza:</b> {html.escape(traducir(certeza))}",
        f"⏳ <b>Urgencia:</b> {html.escape(traducir(urgencia))}",
        "",
        f"🟢 <b>Inicio:</b> {html.escape(fecha_argentina(inicio))}",
        f"🔚 <b>Fin:</b> {html.escape(fecha_argentina(fin))}",
    ]

    if descripcion and descripcion != "No especificada":
        lineas.extend([
            "",
            "📝 <b>Descripción:</b>",
            html.escape(descripcion),
        ])

    if instrucciones and instrucciones != "No especificada":
        lineas.extend([
            "",
            "🛡️ <b>Recomendaciones:</b>",
            html.escape(instrucciones),
        ])

    enlaces = enlaces_de_respaldo(recursos_sin_adjunto or [])
    if enlaces:
        lineas.extend([
            "",
            "🖼️ <b>Imágenes o recursos gráficos:</b>",
            *enlaces,
        ])

    return "\n".join(lineas)


# =========================================================
# PROCESAMIENTO DE ALERTAS CAP
# =========================================================

def procesar_cap(contenido, historial, claves_procesadas, url_cap=RSS_URL):
    """Procesa una alerta CAP y entrega primero el texto, sin esperar imágenes."""
    raiz = ET.fromstring(contenido)

    if nombre_etiqueta(raiz) != "alert":
        print("El documento recibido no es un XML CAP.")
        return

    identificador = texto(raiz, "identifier", "")
    enviado = texto(raiz, "sent", "")
    registrar_demora_publicacion("CAP", enviado)
    if not identificador:
        print("Se encontró una alerta sin identificador; se omite.")
        return

    clave = clave_alerta(identificador, enviado)
    if clave in historial or clave in claves_procesadas:
        print(f"Alerta ya notificada: {identificador}")
        return

    localidades = set()
    info_elegida = None
    recursos_encontrados = []

    for info in todos_hijos(raiz, "info"):
        localidades_info = set()
        for area in todos_hijos(info, "area"):
            localidades_info.update(localidades_afectadas(area))

        if localidades_info:
            localidades.update(localidades_info)
            if info_elegida is None:
                info_elegida = info
            recursos_encontrados.extend(extraer_recursos(info, url_cap))

    localidades.intersection_update(LOCALIDADES_PERMITIDAS)
    if not localidades or info_elegida is None:
        print(f"Sin coincidencias geográficas con las localidades permitidas: {identificador}")
        return

    print(f"Localidades autorizadas detectadas para {identificador}: {', '.join(sorted(localidades))}")

    # PRIMERO: entregar la alerta textual. No descargar gráficos antes de avisar.
    mensaje = construir_mensaje(raiz, info_elegida, localidades)
    enviar_telegram(mensaje)

    # El historial se guarda inmediatamente tras confirmar el envío del texto.
    historial.add(clave)
    claves_procesadas.add(clave)
    guardar_historial(historial)

    # DESPUÉS: adjuntar recursos. Los fallos gráficos no retrasan ni anulan el aviso.
    recursos_unicos = []
    vistos = set()
    for recurso in recursos_encontrados:
        clave_recurso = recurso.get("url") or (
            recurso.get("descripcion"), len(recurso.get("datos") or b"")
        )
        if clave_recurso not in vistos:
            vistos.add(clave_recurso)
            recursos_unicos.append(recurso)

    for recurso in recursos_unicos:
        preparado = preparar_recurso_grafico(recurso)
        if preparado.get("datos"):
            try:
                enviar_foto_telegram(preparado)
                continue
            except (requests.RequestException, RuntimeError, ValueError) as error:
                print(f"No se pudo adjuntar una imagen del SMN: {error}")

        try:
            enviar_enlace_recurso(preparado)
        except (requests.RequestException, RuntimeError, ValueError) as error:
            print(f"No se pudo enviar el enlace del recurso: {error}")


# =========================================================
# LECTURA DEL FEED DEL SMN
# =========================================================

def enlaces_cap(contenido):
    """Extrae los enlaces XML CAP de un RSS o Atom."""
    raiz = ET.fromstring(contenido)

    if nombre_etiqueta(raiz) == "alert":
        return [None]

    enlaces = []

    for elemento in raiz.iter():
        nombre = nombre_etiqueta(elemento)

        if nombre == "item":
            for item_hijo in elemento:
                if nombre_etiqueta(item_hijo) == "link" and item_hijo.text:
                    enlaces.append(item_hijo.text.strip())

        elif nombre == "entry":
            for item_hijo in elemento:
                if nombre_etiqueta(item_hijo) == "link":
                    href = item_hijo.attrib.get("href")
                    rel = item_hijo.attrib.get("rel", "alternate")

                    if href and rel in ("alternate", "enclosure"):
                        enlaces.append(href.strip())

    # Conserva el orden y elimina enlaces duplicados.
    return list(dict.fromkeys(enlaces))



# =========================================================
# AVISOS METEOROLÓGICOS A MUY CORTO PLAZO (GEORSS)
# =========================================================

GEORSS_URL = "https://ssl.smn.gob.ar/feeds/avisocorto_GeoRSS.xml"
MAX_BYTES_GIF = 9_500_000


class ExtractorImagenesHTML(__import__("html.parser", fromlist=["HTMLParser"]).HTMLParser):
    """Extrae los src de las imágenes publicadas en la descripción GeoRSS."""

    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "img":
            return
        atributos = dict(attrs)
        src = atributos.get("src", "").strip()
        if src and url_http_valida(src):
            self.urls.append(src)


def normalizar_texto(valor):
    """Normaliza texto para comparar departamentos sin diferencias de acentos."""
    import unicodedata
    valor = html.unescape(valor or "")
    valor = unicodedata.normalize("NFD", valor)
    valor = "".join(c for c in valor if unicodedata.category(c) != "Mn")
    return " ".join(valor.lower().replace(".", " ").replace(",", " ").split())


def localidades_georss(descripcion):
    """Relaciona exclusivamente los dos departamentos autorizados con las localidades."""
    limpio = normalizar_texto(__import__("re").sub(r"<[^>]+>", " ", descripcion))
    afectadas = set()

    # Patquía pertenece al departamento Independencia.
    if __import__("re").search(r"\bindependencia\b", limpio):
        afectadas.add("Patquía")

    # Punta de los Llanos y Tama pertenecen al departamento General Ángel
    # Vicente Peñaloza. Se aceptan abreviaturas habituales del SMN.
    patrones_penaloza = (
        r"\bgeneral\s+angel\s+vicente\s+penaloza\b",
        r"\bgral\s+angel\s+vicente\s+penaloza\b",
        r"\bgral\s+angel\s+v\s+penaloza\b",
        r"\bgeneral\s+a\s+v\s+penaloza\b",
        r"\bgral\s+a\s+v\s+penaloza\b",
    )
    if any(__import__("re").search(patron, limpio) for patron in patrones_penaloza):
        afectadas.update({"Punta de los Llanos", "Tama"})

    return afectadas.intersection(LOCALIDADES_PERMITIDAS)


def extraer_urls_imagenes_georss(descripcion):
    extractor = ExtractorImagenesHTML()
    try:
        extractor.feed(descripcion or "")
    except Exception as error:
        print(f"No se pudieron interpretar las imágenes del GeoRSS: {error}")
    # Conserva el orden y evita duplicados.
    return list(dict.fromkeys(extractor.urls))


def descargar_archivo(url):
    """Descarga un recurso SMN con un límite de tamaño estricto."""
    if not url_http_valida(url):
        return None
    try:
        with requests.get(
            url, headers=CABECERAS_HTTP, timeout=TIMEOUT_DESCARGA_IMAGEN,
            stream=True, allow_redirects=True
        ) as respuesta:
            respuesta.raise_for_status()
            partes = []
            total = 0
            for bloque in respuesta.iter_content(chunk_size=64 * 1024):
                if not bloque:
                    continue
                total += len(bloque)
                if total > MAX_BYTES_GIF:
                    print(f"Recurso SMN demasiado grande: {url}")
                    return None
                partes.append(bloque)
            return b"".join(partes)
    except (requests.RequestException, OSError, ValueError) as error:
        print(f"No se pudo descargar el recurso SMN {url}: {error}")
        return None


def leer_validez_grafico(datos, fecha_emision):
    """
    Lee la validez del aviso oficial con Tesseract.
    Si no puede identificarla, no inventa una duración ni una hora de cierre.
    """
    import re
    import subprocess
    import tempfile

    if not datos:
        return "No se pudo leer la validez en el gráfico oficial.", None

    try:
        with tempfile.NamedTemporaryFile(suffix=".gif") as temporal:
            temporal.write(datos)
            temporal.flush()
            resultado = subprocess.run(
                ["tesseract", temporal.name, "stdout", "-l", "spa+eng"],
                capture_output=True, text=True, timeout=25, check=False
            )
        texto_ocr = normalizar_texto(resultado.stdout)
    except (OSError, subprocess.SubprocessError) as error:
        print(f"No se pudo ejecutar OCR sobre el aviso: {error}")
        return "No se pudo leer la validez en el gráfico oficial.", None

    # Duraciones escritas en letras o números: una/dos/tres horas desde emisión.
    duraciones = [
        (r"(?:una|1)\s*(?:\(\s*1\s*\))?\s*hora(?:s)?\s+desde\s+la\s+emision", 1),
        (r"(?:dos|2)\s*(?:\(\s*2\s*\))?\s*hora(?:s)?\s+desde\s+la\s+emision", 2),
        (r"(?:tres|3)\s*(?:\(\s*3\s*\))?\s*hora(?:s)?\s+desde\s+la\s+emision", 3),
        (r"(?:cuatro|4)\s*(?:\(\s*4\s*\))?\s*hora(?:s)?\s+desde\s+la\s+emision", 4),
        (r"(?:seis|6)\s*(?:\(\s*6\s*\))?\s*hora(?:s)?\s+desde\s+la\s+emision", 6),
    ]

    for patron, horas in duraciones:
        if re.search(patron, texto_ocr):
            if fecha_emision is None:
                return f"Validez: {horas} hora(s) desde la emisión.", None
            fin = fecha_emision + __import__("datetime").timedelta(hours=horas)
            return (
                f"Validez: {horas} hora(s) desde la emisión. "
                f"Válido hasta las {fin:%H:%M} (hora argentina).",
                fin
            )

    # Respaldo para una hora exacta impresa en el gráfico.
    coincidencia = re.search(
        r"(?:valido\s+hasta|validez\s+hasta|hasta)\s*(?:las)?\s*(\d{1,2})[:.](\d{2})",
        texto_ocr
    )
    if coincidencia:
        hora, minuto = map(int, coincidencia.groups())
        if 0 <= hora <= 23 and 0 <= minuto <= 59 and fecha_emision:
            fin = fecha_emision.replace(hour=hora, minute=minuto, second=0, microsecond=0)
            if fin < fecha_emision:
                fin += __import__("datetime").timedelta(days=1)
            return f"Válido hasta las {hora:02d}:{minuto:02d} (hora argentina).", fin

    print("OCR del gráfico SMN (sin validez reconocible):", resultado.stdout[:500])
    return "Validez: no se pudo identificar en el gráfico oficial.", None


def fecha_emision_georss(valor):
    if not valor:
        return None
    try:
        fecha = datetime.fromisoformat(valor.strip().replace("Z", "+00:00"))
        if fecha.tzinfo is None:
            fecha = fecha.replace(tzinfo=timezone.utc)
        return fecha.astimezone(ZONA_HORARIA)
    except (ValueError, TypeError):
        return None


def fenomeno_georss(descripcion):
    """Extrae el fenómeno del fragmento HTML de descripción."""
    import re
    texto_plano = html.unescape(re.sub(r"<[^>]+>", " ", descripcion or ""))
    texto_plano = " ".join(texto_plano.split())
    coincidencia = re.search(
        r"(?:por ocurrencia de|por)\s+(.+?)(?:\.|$)",
        texto_plano, flags=re.IGNORECASE
    )
    return coincidencia.group(1).strip().rstrip(".") if coincidencia else "Fenómeno indicado por el SMN"


def enviar_documento_georss(url, datos, numero, indice):
    """Envía cada GIF original como documento para conservar su animación."""
    if not datos:
        return False
    nombre = os.path.basename(urlparse(url).path) or f"aviso_smn_{indice}.gif"
    nombre = nombre.split("?")[0][:100]
    respuesta = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendDocument",
        data={
            "chat_id": CHAT_ID,
            "caption": f"🖼️ Gráfico oficial del SMN — Aviso N° {html.escape(numero)} ({indice}/2)",
        },
        files={"document": (nombre, datos, "image/gif")},
        timeout=45
    )
    respuesta_telegram_correcta(respuesta)
    print(f"Gráfico oficial adjuntado: {nombre}")
    return True


def procesar_georss(contenido, historial, claves_procesadas):
    """Procesa ACP del GeoRSS y envía el texto antes de descargar/OCR de imágenes."""
    raiz = ET.fromstring(contenido)
    total_items = 0
    nuevos = 0

    for item in raiz.iter():
        if nombre_etiqueta(item) != "item":
            continue
        total_items += 1

        titulo = texto(item, "title", "")
        descripcion = texto(item, "description", "")
        fecha_xml = texto(item, "date", "")
        if not fecha_xml or fecha_xml == "No especificada":
            fecha_xml = texto(item, "date", "")

        registrar_demora_publicacion("GeoRSS-ACP", fecha_xml)
        afectadas = localidades_georss(descripcion)
        if not afectadas:
            print(f"ACP sin coincidencia con las localidades autorizadas: {titulo}")
            continue

        import re
        numero_match = re.search(r"N[°º]\s*(\d+)", titulo, flags=re.IGNORECASE)
        numero = numero_match.group(1) if numero_match else titulo.strip()
        fecha = fecha_emision_georss(fecha_xml)

        # El número y la fecha distinguen avisos sucesivos del mismo fenómeno.
        clave = hashlib.sha256(
            f"GEORSS|{numero}|{fecha_xml}".encode("utf-8")
        ).hexdigest()
        if clave in historial or clave in claves_procesadas:
            print(f"ACP ya notificado: N° {numero}")
            continue

        fecha_texto = (
            fecha.strftime("%d de ") + MESES[fecha.month - 1] + fecha.strftime(" de %Y - %H:%M")
            if fecha else (fecha_xml or "No informada")
        )
        fenomeno = html.escape(fenomeno_georss(descripcion))

        # PRIMERO: enviar la notificación mínima inmediatamente. No esperar imágenes ni OCR.
        validez_inicial = "Validez: consultar los gráficos oficiales adjuntos."
        lineas = [
            "🚨 <b>URGENTE</b>",
            "<b>AVISO METEOROLÓGICO A CORTO PLAZO</b>",
            "",
            f"📍 <b>Localidades:</b> {html.escape(', '.join(sorted(afectadas)))}",
            f"🌩️ <b>Fenómeno:</b> {fenomeno}",
            f"🕒 <b>Emitido:</b> {html.escape(fecha_texto)}",
            f"⏳ <b>{html.escape(validez_inicial)}</b>",
            "",
            "<i>Fuente: Servicio Meteorológico Nacional (SMN).</i>",
        ]
        mensaje = "\n".join(lineas)
        resultado_telegram = enviar_telegram(mensaje)
        message_id = resultado_telegram.get("message_id") if isinstance(resultado_telegram, dict) else None

        # Guardar el aviso apenas Telegram confirma el mensaje principal: un fallo de imágenes
        # no debe provocar que la alerta se repita en la siguiente ejecución.
        historial.add(clave)
        claves_procesadas.add(clave)
        guardar_historial(historial)
        nuevos += 1
        print(f"ACP N° {numero}: texto enviado para {', '.join(sorted(afectadas))}.")

        # DESPUÉS: descargar los dos GIF oficiales, conservar su animación y ejecutar OCR.
        urls = extraer_urls_imagenes_georss(descripcion)[:2]
        if len(urls) < 2:
            print(f"Advertencia: el ACP N° {numero} no contiene dos imágenes.")

        graficos = []
        for url in urls:
            datos = descargar_archivo(url)
            graficos.append({"url": url, "datos": datos})

        datos_ocr = next(
            (g["datos"] for g in graficos if g["url"].lower().endswith("aviso.gif") and g["datos"]),
            None,
        )
        if not datos_ocr:
            datos_ocr = next((g["datos"] for g in graficos if g["datos"]), None)

        validez, _ = leer_validez_grafico(datos_ocr, fecha)
        if message_id and validez not in {
            "Validez: no se pudo identificar en el gráfico oficial.",
            "No se pudo leer la validez en el gráfico oficial.",
        }:
            lineas[6] = f"⏳ <b>{html.escape(validez)}</b>"
            try:
                editar_mensaje_telegram(message_id, "\n".join(lineas))
            except (requests.RequestException, RuntimeError, ValueError) as error:
                print(f"No se pudo actualizar la validez del ACP N° {numero}: {error}")

        for indice, grafico in enumerate(graficos, start=1):
            if not grafico["datos"]:
                print(f"No se pudo adjuntar el gráfico {indice} del ACP N° {numero}.")
                continue
            try:
                enviar_documento_georss(grafico["url"], grafico["datos"], numero, indice)
            except (requests.RequestException, RuntimeError, ValueError) as error:
                print(f"Falló el envío del gráfico {indice} del ACP N° {numero}: {error}")

    print(f"GeoRSS: {total_items} elemento(s) revisado(s), {nuevos} ACP nuevo(s) enviado(s).")


def consultar_georss(historial, claves_procesadas):
    print("Consultando avisos a corto plazo GeoRSS del SMN...")
    respuesta = consultar_url_smn(GEORSS_URL, timeout=(5, 20), nombre_fuente="GeoRSS")
    procesar_georss(respuesta.content, historial, claves_procesadas)


def ejecutar():
    validar_configuracion_telegram()
    inicio_ejecucion = ahora_utc()

    print("=" * 68)
    print("SISTEMA DE ALERTAS SMN — CAP + ACP GEORSS")
    print(f"Inicio UTC: {inicio_ejecucion.isoformat()}")
    print(f"Inicio Argentina: {inicio_ejecucion.astimezone(ZONA_HORARIA).isoformat()}")
    print(f"Localidades: {', '.join(sorted(LOCALIDADES_PERMITIDAS))}")
    print("Fuentes habilitadas: CAP y GeoRSS ACP")
    print("=" * 68)

    historial = cargar_historial()
    claves_procesadas = set()

    # GeoRSS ACP se procesa primero porque contiene avisos de muy corto plazo.
    # CAP se consulta después. Una caída de una fuente no impide consultar la otra.
    try:
        consultar_georss(historial, claves_procesadas)
    except (requests.RequestException, ET.ParseError, RuntimeError, ValueError) as error:
        print(f"ERROR consultando GeoRSS ACP del SMN: {error}")

    try:
        respuesta = consultar_url_smn(RSS_URL, timeout=(5, 15), nombre_fuente="CAP-RSS")
        enlaces = enlaces_cap(respuesta.content)
        raiz = ET.fromstring(respuesta.content)

        if nombre_etiqueta(raiz) == "alert":
            procesar_cap(
                respuesta.content,
                historial,
                claves_procesadas,
                url_cap=respuesta.url or RSS_URL,
            )
        else:
            print(f"Feed CAP recibido: {len(enlaces)} enlaces. Procesando documentos...")
            for enlace in enlaces:
                try:
                    url_cap = urljoin(respuesta.url or RSS_URL, enlace)
                    respuesta_cap = consultar_url_smn(
                        url_cap,
                        timeout=(5, 12),
                        nombre_fuente="Documento-CAP",
                    )
                    procesar_cap(
                        respuesta_cap.content,
                        historial,
                        claves_procesadas,
                        url_cap=respuesta_cap.url or url_cap,
                    )
                except (requests.RequestException, ET.ParseError, RuntimeError, ValueError) as error:
                    print(f"Error al procesar documento CAP {enlace}: {error}")
    except (requests.RequestException, ET.ParseError, RuntimeError, ValueError) as error:
        print(f"ERROR consultando el canal CAP del SMN: {error}")

    fin_ejecucion = ahora_utc()
    print(f"Fin UTC: {fin_ejecucion.isoformat()}")
    print(f"Fin Argentina: {fin_ejecucion.astimezone(ZONA_HORARIA).isoformat()}")
    print(f"Duración total: {(fin_ejecucion - inicio_ejecucion).total_seconds():.1f} segundos.")
    print("=" * 68)


if __name__ == "__main__":
    ejecutar()
