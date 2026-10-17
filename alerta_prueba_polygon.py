import os
import json
import feedparser
import requests
import re
from datetime import datetime

LOCALIDADES = {
    "Patquía": (-30.0468, -66.8823),
    "Punta de los Llanos": (-30.1455, -66.5262),
    "Tama": (-30.5058, -66.2952),
    "San Carlos de Bariloche": (-41.0500, -71.3102)
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

def procesar_alertas():
    historial = cargar_historial()
    feed = feedparser.parse(RSS_URL)
    
    print(f"Buscando alertas en el feed... Se encontraron {len(feed.entries)} alertas activas.")

    for entry in feed.entries:
        alerta_id = getattr(entry, 'id', getattr(entry, 'title', ''))
        titulo = getattr(entry, 'title', 'Alerta')
        print(f"\n--- Analizando: {titulo} ---")
        
        if alerta_id in historial:
            print("Resultado: IGNORADA (Ya está en el historial).")
            continue
            
        descripcion_cruda = getattr(entry, 'description', '')
        limpio = re.sub(r'<[^>]+>', '\n', descripcion_cruda)
        limpio = re.sub(r'\n+', '\n', limpio)

        polygons_matches = re.findall(r'Polygon:\s*([\d\.\-,\s]+)', limpio, re.IGNORECASE)
        print(f"Polígonos encontrados en el texto: {len(polygons_matches)}")
        
        localidades_afectadas = set()
        
        for p_str in polygons_matches:
            puntos = []
            for par in p_str.split():
                partes = par.split(',')
                if len(partes) == 2:
                    try: puntos.append((float(partes[0]), float(partes[1])))
                    except ValueError: pass
            
            if puntos:
                for nombre_pueblo, (lat, lon) in LOCALIDADES.items():
                    if punto_en_poligono(lat, lon, puntos):
                        localidades_afectadas.add(nombre_pueblo)

        if localidades_afectadas:
            print(f"Resultado: ¡ALERTA POSITIVA para {localidades_afectadas}! Enviando a Telegram...")
            pueblos_str = ", ".join(sorted(localidades_afectadas))
            mensaje = f"🚨 {titulo}\n📍 Zonas afectadas: {pueblos_str}\n\nFuente: https://www.smn.gob.ar/alertas"
            enviar_telegram_texto(mensaje)
            historial.append(alerta_id)
            guardar_historial(historial)
        else:
            print("Resultado: NO afecta a las localidades configuradas.")

if __name__ == '__main__':
    procesar_alertas()
