
import os
import json
import requests
import xml.etree.ElementTree as ET

from datetime import datetime, timezone
from pathlib import Path

# ==========================================
# CONFIGURACION
# ==========================================

LOCALIDADES = {
    "Patquía": (-30.0468, -66.8823),
    "Punta de los Llanos": (-30.1455, -66.5262),
    "Tama": (-30.5058, -66.2952),
    "San Carlos de Bariloche": (-40.7500, -71.5000),
}

RSS_URL = (
    "https://ssl.smn.gob.ar/feeds/CAP/"
    "rss_alertaCAP_nuevo.xml"
)

# Configurar estas variables en GitHub Secrets
TELEGRAM_BOT_TOKEN = os.getenv("8882322196:AAGYo3rXhptjmA4vzkt-hvkKZ6QT8b3T7ZM")
TELEGRAM_CHANNEL_ID = os.getenv("-1004459131844")

HISTORIAL_FILE = Path("historial_alertas.json")
TIMEOUT = 25

HEADERS = {
    "User-Agent": "SMN-Alertas-Telegram/1.0"
}


# ==========================================
# UTILIDADES XML
# ==========================================

def hijos(elemento, nombre):
    """Devuelve hijos por nombre, independientemente del namespace."""
    return [
        hijo for hijo in list(elemento)
        if hijo.tag.split("}")[-1] == nombre
    ]


def hijo(elemento, nombre, default=""):
    encontrados = hijos(elemento, nombre)
    if not encontrados:
        return default
    return (encontrados[0].text or "").strip()


def cargar_xml(url):
    respuesta = requests.get(
        url, headers=HEADERS, timeout=TIMEOUT
    )
    respuesta.raise_for_status()
    return ET.fromstring(respuesta.content)


# ==========================================
# HISTORIAL DE NOTIFICACIONES
# ==========================================

def cargar_historial():
    if not HISTORIAL_FILE.exists():
        return []

    try:
        with HISTORIAL_FILE.open(
            "r", encoding="utf-8"
        ) as archivo:
            datos = json.load(archivo)
            return datos if isinstance(datos, list) else []
    except (json.JSONDecodeError, OSError):
        print("Advertencia: no se pudo leer el historial.")
        return []


def guardar_historial(historial):
    with HISTORIAL_FILE.open(
        "w", encoding="utf-8"
    ) as archivo:
        json.dump(
            historial[-5000:],
            archivo,
            ensure_ascii=False,
            indent=2
        )


# ==========================================
# GEOMETRIA
# ==========================================

def punto_en_poligono(lat, lon, poligono):
    """Ray casting. Cada punto del polígono es (latitud, longitud)."""
    if len(poligono) < 3:
        return False

    dentro = False
    x, y = lon, lat

    j = len(poligono) - 1

    for i in range(len(poligono)):
        lat_i, lon_i = poligono[i]
        lat_j, lon_j = poligono[j]

        if (lat_i > y) != (lat_j > y):
            cruce = (
                (lon_j - lon_i) * (y - lat_i)
                / (lat_j - lat_i)
                + lon_i
            )

            if x < cruce:
                dentro = not dentro

        j = i

    return dentro


def extraer_poligonos(alerta):
    """Extrae todos los polígonos de las áreas CAP."""
    resultado = []

    for info in hijos(alerta, "info"):
        for area in hijos(info, "area"):
            for elemento in hijos(area, "polygon"):
                texto = (elemento.text or "").strip()
                puntos = []

                for par in texto.split():
                    partes = par.split(",")

                    if len(partes) != 2:
                        continue

                    try:
                        lat = float(partes[0])
                        lon = float(partes[1])

                        if -90 <= lat <= 90 and -180 <= lon <= 180:
                            puntos.append((lat, lon))
                    except ValueError:
                        continue

                if len(puntos) >= 3:
                    resultado.append(puntos)

    return resultado


def buscar_localidades(alerta):
    poligonos = extraer_poligonos(alerta)
    afectadas = set()

    for nombre, (lat, lon) in LOCALIDADES.items():
        if any(
            punto_en_poligono(lat, lon, poligono)
            for poligono in poligonos
        ):
            afectadas.add(nombre)

    return sorted(afectadas)


# ==========================================
# FECHAS Y TELEGRAM
# ==========================================

def formatear_fecha(valor):
    if not valor:
        return "No especificada"

    try:
        fecha = datetime.fromisoformat(
            valor.strip().replace("Z", "+00:00")
        )
        return fecha.strftime("%d/%m/%Y %H:%M UTC")
    except ValueError:
        return valor


def enviar_telegram(mensaje):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        raise RuntimeError(
            "Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHANNEL_ID."
        )

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    respuesta = requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHANNEL_ID,
            "text": mensaje,
            "disable_web_page_preview": True,
        },
        timeout=TIMEOUT,
    )

    respuesta.raise_for_status()
    datos = respuesta.json()

    if not datos.get("ok"):
        raise RuntimeError(
            f"Telegram rechazó el mensaje: {datos}"
        )


# ==========================================
# PROCESAMIENTO CAP
# ==========================================

def procesar_alerta(url, historial):
    alerta = cargar_xml(url)

    identifier = hijo(alerta, "identifier")
    sent = hijo(alerta, "sent")
    status = hijo(alerta, "status")
    message_type = hijo(alerta, "msgType", "Alert")

    # Ignorar mensajes de prueba o borradores.
    if status and status.lower() != "actual":
        return

    if message_type.lower() not in ("alert", "update"):
        return

    if not identifier:
        print(f"CAP sin identifier: {url}")
        return

    # Identifica cada versión de una alerta.
    clave = f"{identifier}|{sent}"

    if clave in historial:
        print(f"Ya notificada: {identifier}")
        return

    localidades = buscar_localidades(alerta)

    if not localidades:
        print(f"Sin coincidencias geográficas: {identifier}")
        return

    infos = hijos(alerta, "info")

    if not infos:
        print(f"CAP sin información meteorológica: {identifier}")
        return

    # Puede haber más de un bloque info.
    for info in infos:
        fenomeno = hijo(info, "event", "Alerta meteorológica")
        titulo = hijo(info, "headline", fenomeno)
        descripcion = hijo(
            info, "description", "Sin descripción disponible"
        )
        severidad = hijo(info, "severity", "No especificada")
        certeza = hijo(info, "certainty", "No especificada")
        urgencia = hijo(info, "urgency", "No especificada")
        inicio = hijo(info, "effective") or hijo(info, "onset")
        fin = hijo(info, "expires")

        mensaje = (
            f"🚨 ALERTA DEL SMN\n\n"
            f"🌦️ {titulo}\n"
            f"📍 Localidades afectadas: {', '.join(localidades)}\n\n"
            f"🔴 Severidad: {severidad}\n"
            f"🎯 Certeza: {certeza}\n"
            f"⏱️ Urgencia: {urgencia}\n"
            f"🕒 Inicio: {formatear_fecha(inicio)}\n"
            f"🕒 Fin: {formatear_fecha(fin)}\n\n"
            f"{descripcion}\n\n"
            f"Fuente oficial: https://www.smn.gob.ar/alertas"
        )

        enviar_telegram(mensaje)

    historial.append(clave)
    guardar_historial(historial)

    print(
        f"NOTIFICADA: {identifier} -> {', '.join(localidades)}"
    )


def procesar_alertas():
    historial = cargar_historial()

    print("Consultando el RSS CAP del SMN...")

    rss = cargar_xml(RSS_URL)

    items = [
        elemento for elemento in rss.iter()
        if elemento.tag.split("}")[-1] == "item"
    ]

    # Compatibilidad básica con feeds Atom.
    if not items:
        items = [
            elemento for elemento in rss.iter()
            if elemento.tag.split("}")[-1] == "entry"
        ]

    print(f"Entradas encontradas en el feed: {len(items)}")

    urls = []

    for item in items:
        link = ""

        for elemento in list(item):
            nombre = elemento.tag.split("}")[-1]

            if nombre == "link":
                link = (
                    elemento.attrib.get("href", "")
                    or (elemento.text or "").strip()
                )
                if link:
                    break

        if link.startswith("https://"):
            urls.append(link)

    errores = 0

    for url in dict.fromkeys(urls):
        try:
            procesar_alerta(url, historial)
        except Exception as error:
            errores += 1
            print(f"Error procesando {url}: {error}")

    print(
        f"Proceso terminado. "
        f"CAP revisados: {len(set(urls))}. "
        f"Errores: {errores}."
    )


if __name__ == "__main__":
    procesar_alertas()
