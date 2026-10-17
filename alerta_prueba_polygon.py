import os
import json
import feedparser
import requests
import re
from datetime import datetime

# Diccionario con las localidades y sus coordenadas
LOCALIDADES = {
    "Patquía": (-30.0468, -66.8823),
    "Punta de los Llanos": (-30.1455, -66.5262),
    "Tama": (-30.5058, -66.2952),
    "San Carlos de Bariloche": (-40.7500, -71.5000) # Coordenada centrada en el polígono del sur
}

RSS_URL = 'https://ssl.smn.gob.ar/CAP/AR.php'
TELEGRAM_BOT_TOKEN = '8882322196:AAGYo3rXhptjmA4vzkt-hvkKZ6QT8b3T7ZM'
TELEGRAM_CHANNEL_ID = '-1004459131844'
HISTORIAL_FILE = 'historial_alerta_prueba_polygon.json'

def cargar_historial():
    if os.path.exists(HISTORIAL_FILE):
        with open(HISTORIAL_FILE, 'r', encoding='utf-8') as f:
            try: return json.load(f)
            except: return []
    return []

def guardar_historial(historial):
    with open(HISTORIAL_FILE, 'w', encoding='utf-8') as f:
        json.dump(historial, f, ensure_ascii=False, indent=4)

def enviar_telegram_texto(mensaje):
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
    requests.post(url, json={'chat_id': TELEGRAM_CHANNEL_ID, 'text': mensaje})

def punto_en_poligono(latitud, longitud, poligono):
    x, y = longitud, latitud
    adentro = False
    n = len(poligono)
    if n == 0: return False
    p1_y, p1_x = poligono[0]
    for i in range(1, n + 1):
        p2_y, p2_x = poligono[i % n]
        if min(p1_y, p2_y) < y <= max(p1_y, p2_y):
            if x <= max(p1_x, p2_x):
                if p1_y != p2_y:
                    xinters = (y - p1_y) * (p2_x - p1_x) / (p2_y - p1_y) + p1_x
                if p1_x == p2_x or x <= xinters:
                    adentro = not adentro
        p1_x, p1_y = p2_x, p2_y
    return adentro

def formatear_fecha(fecha_str):
    try:
        dt = datetime.fromisoformat(fecha_str)
        return dt.strftime('%d/%m/%Y %H:%M')
    except:
        return fecha_str

def procesar_alertas():
    historial = cargar_historial()
    feed = feedparser.parse(RSS_URL)
    
    print(f"Buscando alertas en el feed... Se encontraron {len(feed.entries)} alertas activas.")

    for entry in feed.entries:
        alerta_id = entry.get('id', entry.get('title', ''))
        titulo = entry.get('title', 'Alerta')
        print(f"\n--- Analizando: {titulo} ---")
        
        if alerta_id in historial:
            print("Resultado: IGNORADA (Ya está en el historial).")
            continue
            
        # EXTRAE TODO EL TEXTO CRUDO DE LA ALERTA PARA BUSCAR A LA FUERZA
        texto_completo = str(entry)
        
        # Busca cualquier patrón que sea una cadena larga de coordenadas negativas (Ej: -40.6,-71.04)
        patron_coordenadas = r'(-\d{1,2}(?:\.\d+)?,-\d{1,3}(?:\.\d+)?(?:\s+-\d{1,2}(?:\.\d+)?,-\d{1,3}(?:\.\d+)?){3,})'
        match = re.search(patron_coordenadas, texto_completo)
        
        poligono_str = ""
        if match:
            poligono_str = match.group(1)
            print("Polígono ENCONTRADO por fuerza bruta.")
        else:
            print("Polígonos encontrados: 0")
            print("Resultado: NO afecta a las localidades configuradas.")
            continue
            
        puntos = []
        for par in poligono_str.split():
            partes = par.split(',')
            if len(partes) == 2:
                try:
                    puntos.append((float(partes[0]), float(partes[1])))
                except ValueError:
                    pass
                    
        localidades_afectadas = set()
        if puntos:
            for nombre_pueblo, (lat, lon) in LOCALIDADES.items():
                if punto_en_poligono(lat, lon, puntos):
                    localidades_afectadas.add(nombre_pueblo)

        if localidades_afectadas:
            print(f"Resultado: ¡ALERTA POSITIVA para {localidades_afectadas}! Enviando a Telegram...")
            
            certeza = entry.get('cap_certainty', 'No especificada')
            
            inicio_str = entry.get('cap_effective', '')
            inicio = formatear_fecha(inicio_str) if inicio_str else 'No especificado'
            
            fin_str = entry.get('cap_expires', '')
            fin = formatear_fecha(fin_str) if fin_str else 'No especificado'
            
            descripcion_cruda = entry.get('summary', entry.get('description', ''))
            descripcion = re.sub(r'<[^>]+>', '\n', descripcion_cruda)
            descripcion = re.sub(r'\n+', '\n', descripcion).strip()
            
            pueblos_str = ", ".join(sorted(localidades_afectadas))
            
            mensaje = (
                f"🚨 {titulo}\n"
                f"📍 Zonas afectadas: {pueblos_str}\n\n"
                f"Certeza: {certeza}\n\n"
                f"Inicio: {inicio}\n"
                f"Fin: {fin}\n\n"
                f"{descripcion}\n\n"
                f"Fuente: https://www.smn.gob.ar/alertas"
            )
            
            enviar_telegram_texto(mensaje)
            historial.append(alerta_id)
            guardar_historial(historial)
        else:
            print("Resultado: Polígono detectado, pero NO afecta a las localidades configuradas.")

if __name__ == '__main__':
    procesar_alertas()
